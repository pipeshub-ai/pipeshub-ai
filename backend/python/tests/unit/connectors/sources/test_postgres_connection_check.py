"""PostgreSQLConnector.check_connection: trying setup-form credentials before they are saved."""

import logging
import socket
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

import app.sources.client.postgres.postgres as pg_client
from app.connectors.core.base.connector.connector_service import BaseConnector, ConnectorInitError
from app.connectors.sources.postgres.connector import (
    CONNECTION_CHECK_TIMEOUT_S,
    PostgreSQLConnector,
    connection_kwargs_from_auth,
    describe_connection_error,
)
from app.sources.client.postgres.postgres import PostgreSQLResponse
from app.utils.url_fetcher import HostCheckError

_MODULE = "app.connectors.sources.postgres.connector"
_LOGGER = logging.getLogger("test.postgres.check")

BASIC = {"host": "db.example.com", "port": "5433", "database": "shop", "username": "reader", "password": "pw"}


class TestConnectionKwargsFromAuth:
    def test_basic_fields(self):
        assert connection_kwargs_from_auth(BASIC) == {
            "host": "db.example.com",
            "port": 5433,
            "database": "shop",
            "user": "reader",
            "password": "pw",
            "sslmode": "prefer",
        }

    def test_connection_string_with_sslmode_and_encoded_parts(self):
        kwargs = connection_kwargs_from_auth(
            {"connectionString": "postgres://re%40der:p%2Fw@db.example.com/my%20db?sslmode=require"}
        )
        assert kwargs == {
            "host": "db.example.com",
            "port": 5432,
            "database": "my db",
            "user": "re@der",
            "password": "p/w",
            "sslmode": "require",
        }

    def test_sslmode_is_normalised(self):
        assert connection_kwargs_from_auth({**BASIC, "sslmode": " Require "})["sslmode"] == "require"

    @pytest.mark.parametrize(
        ("auth", "message"),
        [
            ({"connectionString": "mysql://u:p@h/db"}, "must start with postgresql://"),
            ({"connectionString": "postgresql://u:p@h:99999/db"}, "port in the connection string"),
            ({"connectionString": "postgresql://h/db"}, "needs a user, host and database"),
            ({**BASIC, "port": "abc"}, "Port must be a number"),
            ({**BASIC, "port": "0"}, "from 1 to 65535"),
            ({**BASIC, "database": ""}, "Host, database and username are required"),
            ({"connectionString": "postgresql://u:p@h/db?sslmode=requir"}, "sslmode must be one of"),
        ],
    )
    def test_invalid_settings_raise_a_message_for_the_user(self, auth, message):
        with pytest.raises(ValueError, match=message):
            connection_kwargs_from_auth(auth)


class TestDescribeConnectionError:
    def _describe(self, error, sslmode="prefer"):
        return describe_connection_error(error, "db.example.com", 5432, "shop", "reader", sslmode)

    def test_wrong_password_or_unknown_user(self):
        message = self._describe(asyncpg.InvalidPasswordError('password authentication failed for user "reader"'))
        assert message == 'PostgreSQL rejected the login for user "reader". Check the username and password.'

    def test_pg_hba_refusal_keeps_the_server_text_naming_our_address(self):
        error = asyncpg.InvalidAuthorizationSpecificationError(
            'no pg_hba.conf entry for host "203.0.113.10", user "reader", database "shop", no encryption'
        )
        assert "203.0.113.10" in self._describe(error)

    def test_unknown_database(self):
        message = self._describe(asyncpg.InvalidCatalogNameError('database "shop" does not exist'))
        assert message == 'Database "shop" does not exist on db.example.com.'

    def test_unknown_host(self):
        assert "Check the host name" in self._describe(socket.gaierror(11001, "getaddrinfo failed"))

    def test_timeout_points_at_the_firewall(self):
        message = self._describe(TimeoutError())
        assert message.startswith("Timed out connecting to db.example.com:5432.")
        assert "firewall" in message

    @pytest.mark.parametrize(
        "error",
        [
            ConnectionRefusedError(111, "Connect call failed ('10.0.0.5', 5432)"),
            # asyncio's single error for a host whose every address refused.
            OSError("Multiple exceptions: [Errno 10061] Connect call failed ('::1', 5432, 0, 0), "
                    "[Errno 10061] Connect call failed ('127.0.0.1', 5432)"),
        ],
    )
    def test_refused(self, error):
        assert self._describe(error).startswith("db.example.com:5432 refused the connection.")

    @pytest.mark.parametrize(
        "error",
        [
            OSError(113, "Connect call failed ('10.0.0.5', 5432)"),
            OSError("Multiple exceptions: [Errno 113] Connect call failed ('10.0.0.5', 5432), "
                    "[Errno 101] Connect call failed ('2001:db8::5', 5432, 0, 0)"),
        ],
    )
    def test_unreachable_is_not_called_refused_and_does_not_list_addresses(self, error):
        message = self._describe(error)
        assert message.startswith("Could not reach db.example.com:5432. Check the host and port")
        assert "10.0.0.5" not in message

    def test_certificate_failure(self):
        assert "SSL handshake" in self._describe(ssl.SSLError("CERTIFICATE_VERIFY_FAILED"))

    def test_ssl_required_by_a_server_without_ssl(self):
        error = ConnectionError('PostgreSQL server at "db:5432" rejected SSL upgrade')
        assert self._describe(error, sslmode="require") == (
            "db.example.com:5432 does not accept SSL connections, but sslmode=require requires SSL."
        )

    def test_refused_upgrade_without_ssl_required_means_not_postgres(self):
        error = ConnectionError('PostgreSQL server at "db:5432" rejected SSL upgrade')
        assert self._describe(error) == "db.example.com:5432 did not answer like a PostgreSQL server. Check the port."


def _fake_client(connect_error: BaseException | None = None):
    client = MagicMock()
    client.connect = AsyncMock(side_effect=connect_error)
    client.close = AsyncMock()
    return client


class TestCheckConnection:
    async def test_a_refused_host_reports_the_refusal_and_is_not_retried(self):
        refused = HostCheckError('"10.0.0.5" is a private or internal address.')
        attempts = AsyncMock(return_value=(None, refused))
        with patch.object(PostgreSQLConnector, "_try_connection", attempts):
            result = await PostgreSQLConnector.check_connection(BASIC, _LOGGER)
        assert result.success is False
        assert result.message == '"10.0.0.5" is a private or internal address.'
        attempts.assert_awaited_once()

    async def test_invalid_settings_fail_without_connecting(self):
        with patch(f"{_MODULE}.PostgreSQLConfig") as config_cls:
            result = await PostgreSQLConnector.check_connection({**BASIC, "port": "x"}, _LOGGER)
        assert result.success is False
        assert "Port must be a number" in result.message
        config_cls.assert_not_called()

    async def test_success_runs_a_query_with_a_short_timeout_and_closes(self):
        client = _fake_client()
        response = PostgreSQLResponse(success=True, data={}, message="ok")
        with (
            patch(f"{_MODULE}.PostgreSQLConfig") as config_cls,
            patch(f"{_MODULE}.PostgreSQLDataSource") as data_source_cls,
        ):
            config_cls.return_value.create_client.return_value = client
            data_source_cls.return_value.test_connection = AsyncMock(return_value=response)
            result = await PostgreSQLConnector.check_connection(BASIC, _LOGGER)

        assert result.success is True
        assert result.message == 'Connected to database "shop" as "reader".'
        kwargs = config_cls.call_args.kwargs
        assert kwargs["timeout"] == CONNECTION_CHECK_TIMEOUT_S
        assert kwargs["max_pool_size"] == 1
        assert (kwargs["host"], kwargs["port"], kwargs["password"]) == ("db.example.com", 5433, "pw")
        client.close.assert_awaited_once()

    async def test_connect_failure_is_described_from_the_driver_error(self):
        driver_error = asyncpg.InvalidPasswordError("password authentication failed")
        connect_error = ConnectionError(f"Failed to connect to PostgreSQL: {driver_error}")
        connect_error.__cause__ = driver_error
        client = _fake_client(connect_error)
        with patch(f"{_MODULE}.PostgreSQLConfig") as config_cls:
            config_cls.return_value.create_client.return_value = client
            result = await PostgreSQLConnector.check_connection(BASIC, _LOGGER)

        assert result.success is False
        assert result.message.startswith('PostgreSQL rejected the login for user "reader".')
        client.close.assert_awaited_once()

    async def test_ssl_only_server_refusal_is_retried_with_ssl_to_get_the_real_reason(self):
        no_encryption = asyncpg.InvalidAuthorizationSpecificationError(
            'no pg_hba.conf entry for host "203.0.113.10", user "reader", database "shop", no encryption'
        )
        wrong_password = asyncpg.InvalidPasswordError('password authentication failed for user "reader"')
        attempts = AsyncMock(side_effect=[(None, no_encryption), (None, wrong_password)])
        with patch.object(PostgreSQLConnector, "_try_connection", attempts):
            result = await PostgreSQLConnector.check_connection(BASIC, _LOGGER)

        assert result.message.startswith('PostgreSQL rejected the login for user "reader".')
        assert [call.args[0]["sslmode"] for call in attempts.await_args_list] == ["prefer", "require"]

    async def test_a_server_without_ssl_keeps_its_own_refusal(self):
        no_encryption = asyncpg.InvalidAuthorizationSpecificationError(
            'pg_hba.conf rejects connection for host "172.17.0.1", user "blocked", database "shop", no encryption'
        )
        no_ssl = ConnectionError('PostgreSQL server at "127.0.0.1:5432" rejected SSL upgrade')
        attempts = AsyncMock(side_effect=[(None, no_encryption), (None, no_ssl)])
        with patch.object(PostgreSQLConnector, "_try_connection", attempts):
            result = await PostgreSQLConnector.check_connection(BASIC, _LOGGER)
        assert result.message == f"PostgreSQL refused the login: {no_encryption}"

    @pytest.mark.parametrize("sslmode", ["require", "disable"])
    async def test_no_retry_when_the_user_chose_the_ssl_mode(self, sslmode):
        no_encryption = asyncpg.InvalidAuthorizationSpecificationError("no pg_hba.conf entry ... no encryption")
        attempts = AsyncMock(return_value=(None, no_encryption))
        with patch.object(PostgreSQLConnector, "_try_connection", attempts):
            result = await PostgreSQLConnector.check_connection({**BASIC, "sslmode": sslmode}, _LOGGER)
        assert result.message.startswith("PostgreSQL refused the login")
        attempts.assert_awaited_once()

    async def test_failed_test_query_is_reported(self):
        client = _fake_client()
        response = PostgreSQLResponse(success=False, error="permission denied", message="failed")
        with (
            patch(f"{_MODULE}.PostgreSQLConfig") as config_cls,
            patch(f"{_MODULE}.PostgreSQLDataSource") as data_source_cls,
        ):
            config_cls.return_value.create_client.return_value = client
            data_source_cls.return_value.test_connection = AsyncMock(return_value=response)
            result = await PostgreSQLConnector.check_connection(BASIC, _LOGGER)

        assert result.success is False
        assert "permission denied" in result.message


class TestSupportsConnectionCheck:
    def test_postgres_overrides_the_check(self):
        assert PostgreSQLConnector.supports_connection_check() is True

    def test_a_connector_without_the_override_does_not(self):
        class NoCheck(BaseConnector):
            pass

        assert NoCheck.supports_connection_check() is False


class TestEveryConnectionGoesThroughTheAddressPolicy:
    """The setup check and the sync both open connections through the client's checked dial."""

    @pytest.fixture
    def driver_connect(self, monkeypatch):
        connect = AsyncMock(side_effect=AssertionError("asyncpg.connect must not be called"))
        monkeypatch.setattr(pg_client, "asyncpg", asyncpg)
        monkeypatch.setattr(asyncpg, "connect", connect)
        return connect

    @staticmethod
    def _connector(host: str) -> PostgreSQLConnector:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"auth": {**BASIC, "host": host}})
        return PostgreSQLConnector(
            logger=_LOGGER,
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=config_service,
            connector_id="conn-pg-policy",
        )

    async def test_sync_refuses_a_private_host_while_the_switch_is_on(self, driver_connect, monkeypatch):
        monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
        connector = self._connector("10.0.0.1")
        with pytest.raises(ConnectorInitError, match="private or internal address"):
            await connector.init()
        driver_connect.assert_not_called()
        assert connector.data_source is None

    async def test_sync_refuses_a_metadata_host_in_every_mode(self, driver_connect, monkeypatch):
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        with pytest.raises(ConnectorInitError, match="cloud metadata"):
            await self._connector("169.254.169.254").init()
        driver_connect.assert_not_called()

    async def test_the_setup_check_refuses_a_private_host_while_the_switch_is_on(self, driver_connect, monkeypatch):
        monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
        result = await PostgreSQLConnector.check_connection({**BASIC, "host": "10.0.0.1"}, _LOGGER)
        assert result.success is False
        assert "private or internal address" in result.message
        driver_connect.assert_not_called()

    async def test_a_name_that_does_not_resolve_is_reported_without_a_second_lookup(self, driver_connect, monkeypatch):
        def no_such_host(*args, **kwargs):
            raise socket.gaierror(11001, "getaddrinfo failed")

        lookups = MagicMock(side_effect=no_such_host)
        monkeypatch.setattr(socket, "getaddrinfo", lookups)
        result = await PostgreSQLConnector.check_connection({**BASIC, "host": "db.example.invalid"}, _LOGGER)
        assert result.message == 'Could not find a server named "db.example.invalid". Check the host name.'
        lookups.assert_called_once()
        driver_connect.assert_not_called()
