"""Personal MariaDB connectors reach public addresses only, like the other database connectors.

A personal connector can be created by any user, so neither its connection check, nor a saved
instance, nor the in-product query tool may dial a private-network, loopback, link-local or
metadata address, or a Unix socket on this server. A team connector is created by an admin and
keeps the deployment rule (PIPESHUB_BLOCK_PRIVATE_ADDRESSES).
"""

from __future__ import annotations

import logging
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.sources.client.mariadb.mariadb as mariadb_client
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.mariadb.connection_check import check_connection
from app.connectors.sources.mariadb.connector import MariaDBConnector
from app.utils.execute_query import _execute_mariadb_query, _execute_query_impl
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV, public_address_refusal

_LOGGER = logging.getLogger("test.mariadb.address.policy")
_AUTH = {"port": "3306", "database": "shop", "username": "reader", "password": "pw"}

# A database on the LAN, the cloud metadata address, this machine, a public-looking name that
# resolves inward, and a socket on this server.
_INWARD_HOSTS = [
    ("10.0.0.7", None),
    ("169.254.169.254", None),
    ("127.0.0.1", None),
    ("db.internal.example", "10.0.0.7"),
    ("/run/mysqld/mysqld.sock", None),
]


_PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"

@pytest.fixture(autouse=True)
def _deployment_allows_private(monkeypatch) -> None:
    monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)


def _resolve(monkeypatch, answers: dict[str, str]) -> None:
    def getaddrinfo(host, *_args, **_kwargs) -> list:
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        ip = answers[host]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", MagicMock(side_effect=getaddrinfo))


@pytest.fixture
def driver(monkeypatch) -> MagicMock:
    """The binary driver: its connect is what dials; it refuses, as a server that is not there does."""
    fake = MagicMock()
    fake.connect = MagicMock(side_effect=ConnectionRefusedError())
    fake.create_async_pool = AsyncMock()
    monkeypatch.setattr(mariadb_client, "mariadb", fake)
    return fake


def _client(host: str, *, public_only: bool) -> mariadb_client.MariaDBClient:
    return mariadb_client.MariaDBClient(
        host=host, user="reader", password="pw", database="shop", public_only=public_only
    )


class TestMariaDBClient:
    @pytest.mark.parametrize(("host", "resolves_to"), _INWARD_HOSTS)
    async def test_a_public_only_client_never_reaches_the_driver(self, monkeypatch, driver, host, resolves_to) -> None:
        _resolve(monkeypatch, {host: resolves_to} if resolves_to else {})
        with pytest.raises(ConnectionError) as refused:
            await _client(host, public_only=True).connect()
        assert str(refused.value) == public_address_refusal(host)
        driver.connect.assert_not_called()

    async def test_a_team_client_dials_a_private_address(self, driver) -> None:
        with pytest.raises(ConnectionError):
            await _client("10.0.0.7", public_only=False).connect()
        driver.connect.assert_called_once()
        assert driver.connect.call_args.kwargs["host"] == "10.0.0.7"

    async def test_a_public_only_client_with_a_ca_never_dials_a_name(self, monkeypatch, driver) -> None:
        """The driver checks the certificate against the host it dials and looks a name up
        again on every connection, so a vetted name could reach a private address later."""
        _resolve(monkeypatch, {"db.example.com": "93.184.216.34"})
        client = mariadb_client.MariaDBClient(
            host="db.example.com", user="reader", password="pw", database="shop",
            public_only=True, ssl_ca_pem=_PEM,
        )
        with pytest.raises(ConnectionError) as refused:
            await client.connect()
        assert str(refused.value) == mariadb_client.PERSONAL_TLS_NEEDS_ADDRESS
        driver.connect.assert_not_called()

    async def test_a_public_only_client_with_a_ca_dials_a_public_address_it_was_given(self, driver) -> None:
        client = mariadb_client.MariaDBClient(
            host="93.184.216.34", user="reader", password="pw", database="shop",
            public_only=True, ssl_ca_pem=_PEM,
        )
        with pytest.raises(ConnectionError):
            await client.connect()
        assert driver.connect.call_args.kwargs["host"] == "93.184.216.34"
        assert driver.connect.call_args.kwargs["ssl_verify_cert"] is True

    async def test_the_builder_passes_the_rule_on(self, driver) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"auth": {**_AUTH, "host": "db.example.com"}})
        builder = await mariadb_client.MariaDBClientBuilder.build_from_services(
            _LOGGER, config_service, "maria-1", public_only=True
        )
        assert builder.get_client().public_only is True


class TestMariaDBConnectionCheck:
    @pytest.mark.parametrize(("host", "resolves_to"), _INWARD_HOSTS)
    async def test_a_members_check_is_refused_before_anything_is_dialled(
        self, monkeypatch, driver, host, resolves_to
    ) -> None:
        _resolve(monkeypatch, {host: resolves_to} if resolves_to else {})
        result = await MariaDBConnector.check_connection({**_AUTH, "host": host}, _LOGGER, public_only=True)
        assert result.success is False
        assert result.message == public_address_refusal(host)
        driver.connect.assert_not_called()

    async def test_the_check_is_public_only_when_the_caller_says_nothing(self, driver) -> None:
        result = await check_connection({**_AUTH, "host": "10.0.0.7"}, _LOGGER)
        assert result.message == public_address_refusal("10.0.0.7")
        driver.connect.assert_not_called()

    async def test_an_admins_check_dials_a_private_address(self, driver) -> None:
        result = await MariaDBConnector.check_connection({**_AUTH, "host": "10.0.0.7"}, _LOGGER, public_only=False)
        assert result.success is False
        driver.connect.assert_called_once()


class TestSavedMariaDBConnector:
    @staticmethod
    def _connector(host: str, *, scope: str, saved_scope: str | None = None) -> MariaDBConnector:
        saved = {"auth": {**_AUTH, "host": host}}
        if saved_scope:
            saved["scope"] = saved_scope
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=saved)
        return MariaDBConnector(
            logger=_LOGGER,
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=config_service,
            connector_id="maria-1",
            scope=scope,
        )

    @pytest.mark.parametrize(("host", "resolves_to"), _INWARD_HOSTS)
    async def test_a_personal_connector_to_an_inward_address_fails_at_init(
        self, monkeypatch, driver, host, resolves_to
    ) -> None:
        _resolve(monkeypatch, {host: resolves_to} if resolves_to else {})
        connector = self._connector(host, scope="personal")
        with pytest.raises(ConnectorInitError) as refused:
            await connector.init()
        assert str(refused.value) == public_address_refusal(host)
        driver.connect.assert_not_called()

    async def test_a_team_connector_to_a_private_address_connects(self, driver) -> None:
        assert await self._connector("10.0.0.7", scope="team").init() is False  # the driver refuses
        driver.connect.assert_called_once()

    @pytest.mark.parametrize(("scope", "saved_scope"), [("team", "personal"), ("personal", "team"), ("personal", None)])
    async def test_either_record_naming_it_personal_is_enough(self, driver, scope, saved_scope) -> None:
        with pytest.raises(ConnectorInitError, match="public"):
            await self._connector("10.0.0.7", scope=scope, saved_scope=saved_scope).init()
        driver.connect.assert_not_called()


class TestMariaDBQueryTool:
    async def test_a_public_only_query_never_reaches_the_driver(self, driver) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"auth": {**_AUTH, "host": "10.0.0.7"}})
        result = await _execute_mariadb_query("SELECT 1", config_service, "maria-1", public_only=True)
        assert result["ok"] is False
        driver.connect.assert_not_called()

    async def test_the_query_tool_passes_the_connectors_rule_on(self) -> None:
        with patch(
            "app.utils.execute_query._execute_mariadb_query",
            AsyncMock(return_value={"ok": False, "error": "x"}),
        ) as run:
            await _execute_query_impl(
                "SELECT 1", "mariadb", MagicMock(), "maria-1", public_only=True,
            )
        assert run.await_args.kwargs["public_only"] is True
