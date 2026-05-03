"""Unit tests for Snowflake client module."""

import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

# Install a mock 'snowflake.connector' module BEFORE importing the source
# module, because it does ``import snowflake.connector`` at module level and
# sets the reference to None on ImportError.
_mock_snowflake_pkg = MagicMock()
_mock_snowflake_connector = MagicMock()
_mock_snowflake_pkg.connector = _mock_snowflake_connector
sys.modules.setdefault("snowflake", _mock_snowflake_pkg)
sys.modules["snowflake.connector"] = _mock_snowflake_connector

from app.sources.client.snowflake.snowflake import (  # noqa: E402
    AuthConfig,
    AuthType,
    CredentialsConfig,
    SnowflakeClient,
    SnowflakeConnectorConfig,
    SnowflakeOAuthConfig,
    SnowflakePATConfig,
    SnowflakeResponse,
    SnowflakeRESTClientViaOAuth,
    SnowflakeRESTClientViaPAT,
    SnowflakeSDKClient,
)

import app.sources.client.snowflake.snowflake as _sf_mod  # noqa: E402

_sf_mod.snowflake_connector = _mock_snowflake_connector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def log():
    lg = logging.getLogger("test_snowflake_client")
    lg.setLevel(logging.CRITICAL)
    return lg


@pytest.fixture
def cs():
    return AsyncMock()


@pytest.fixture
def sf_connector():
    """Reset the snowflake_connector mock between tests."""
    _mock_snowflake_connector.reset_mock()
    _mock_snowflake_connector.connect.side_effect = None
    return _mock_snowflake_connector


# ---------------------------------------------------------------------------
# AuthType
# ---------------------------------------------------------------------------


class TestAuthType:
    def test_values(self):
        assert AuthType.OAUTH == "OAUTH"
        assert AuthType.PAT == "PAT"

    def test_is_str_enum(self):
        assert isinstance(AuthType.OAUTH, str)


# ---------------------------------------------------------------------------
# SnowflakeResponse
# ---------------------------------------------------------------------------


class TestSnowflakeResponse:
    def test_success(self):
        resp = SnowflakeResponse(success=True, data={"k": "v"})
        assert resp.success is True

    def test_defaults(self):
        resp = SnowflakeResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None
        assert resp.statement_handle is None

    def test_to_dict_excludes_none(self):
        resp = SnowflakeResponse(success=True, data={"k": "v"})
        d = resp.to_dict()
        assert d["success"] is True
        assert "error" not in d

    def test_to_json(self):
        resp = SnowflakeResponse(success=False, error="fail", message="reason")
        parsed = json.loads(resp.to_json())
        assert parsed["success"] is False
        assert parsed["error"] == "fail"
        assert parsed["message"] == "reason"

    def test_extra_allowed(self):
        resp = SnowflakeResponse(success=True, custom="x")
        d = resp.to_dict()
        assert d.get("custom") == "x"

    def test_statement_handle(self):
        resp = SnowflakeResponse(success=True, statement_handle="abc-123")
        assert resp.statement_handle == "abc-123"


# ---------------------------------------------------------------------------
# SnowflakeRESTClientViaOAuth
# ---------------------------------------------------------------------------


class TestSnowflakeRESTClientViaOAuth:
    def test_init_sets_headers(self):
        c = SnowflakeRESTClientViaOAuth(
            account_identifier="myacct", oauth_token="tok"
        )
        assert c.oauth_token == "tok"
        assert c.account_identifier == "myacct"
        assert c.headers["Authorization"] == "Bearer tok"

    def test_build_base_url_from_plain_name(self):
        c = SnowflakeRESTClientViaOAuth(
            account_identifier="myacct", oauth_token="tok"
        )
        assert c.get_base_url() == "https://myacct.snowflakecomputing.com/api/v2"

    def test_build_base_url_from_fqdn(self):
        c = SnowflakeRESTClientViaOAuth(
            account_identifier="myacct.snowflakecomputing.com",
            oauth_token="tok",
        )
        assert c.get_base_url() == "https://myacct.snowflakecomputing.com/api/v2"

    def test_get_account_identifier(self):
        c = SnowflakeRESTClientViaOAuth(
            account_identifier="myacct", oauth_token="tok"
        )
        assert c.get_account_identifier() == "myacct"

    def test_custom_timeout(self):
        c = SnowflakeRESTClientViaOAuth(
            account_identifier="myacct", oauth_token="tok", timeout=60.0
        )
        assert c.timeout == 60.0


# ---------------------------------------------------------------------------
# SnowflakeRESTClientViaPAT
# ---------------------------------------------------------------------------


class TestSnowflakeRESTClientViaPAT:
    def test_init_sets_headers(self):
        c = SnowflakeRESTClientViaPAT(
            account_identifier="myacct", pat_token="patxyz"
        )
        assert c.pat_token == "patxyz"
        assert c.headers["Authorization"] == "Bearer patxyz"
        assert (
            c.headers["X-Snowflake-Authorization-Token-Type"]
            == "PROGRAMMATIC_ACCESS_TOKEN"
        )

    def test_build_base_url_plain(self):
        c = SnowflakeRESTClientViaPAT(
            account_identifier="myacct", pat_token="tok"
        )
        assert c.get_base_url() == "https://myacct.snowflakecomputing.com/api/v2"

    def test_build_base_url_fqdn(self):
        c = SnowflakeRESTClientViaPAT(
            account_identifier="myacct.snowflakecomputing.com",
            pat_token="tok",
        )
        assert c.get_base_url() == "https://myacct.snowflakecomputing.com/api/v2"

    def test_get_account_identifier(self):
        c = SnowflakeRESTClientViaPAT(
            account_identifier="myacct", pat_token="tok"
        )
        assert c.get_account_identifier() == "myacct"


# ---------------------------------------------------------------------------
# SnowflakeOAuthConfig / SnowflakePATConfig
# ---------------------------------------------------------------------------


class TestSnowflakeOAuthConfig:
    def test_valid(self):
        cfg = SnowflakeOAuthConfig(
            account_identifier="myacct", oauth_token="tok"
        )
        assert cfg.account_identifier == "myacct"
        assert cfg.timeout == 30.0

    def test_missing_fields(self):
        with pytest.raises(ValidationError):
            SnowflakeOAuthConfig(account_identifier="myacct")
        with pytest.raises(ValidationError):
            SnowflakeOAuthConfig(oauth_token="tok")

    def test_invalid_timeout(self):
        with pytest.raises(ValidationError):
            SnowflakeOAuthConfig(
                account_identifier="a", oauth_token="t", timeout=0
            )

    def test_create_client(self):
        cfg = SnowflakeOAuthConfig(
            account_identifier="myacct", oauth_token="tok", timeout=45.0
        )
        client = cfg.create_client()
        assert isinstance(client, SnowflakeRESTClientViaOAuth)
        assert client.timeout == 45.0


class TestSnowflakePATConfig:
    def test_valid(self):
        cfg = SnowflakePATConfig(
            account_identifier="myacct", pat_token="tok"
        )
        assert cfg.account_identifier == "myacct"
        assert cfg.timeout == 30.0

    def test_missing_fields(self):
        with pytest.raises(ValidationError):
            SnowflakePATConfig(account_identifier="myacct")
        with pytest.raises(ValidationError):
            SnowflakePATConfig(pat_token="tok")

    def test_invalid_timeout(self):
        with pytest.raises(ValidationError):
            SnowflakePATConfig(
                account_identifier="a", pat_token="t", timeout=0
            )

    def test_create_client(self):
        cfg = SnowflakePATConfig(
            account_identifier="myacct", pat_token="tok", timeout=45.0
        )
        client = cfg.create_client()
        assert isinstance(client, SnowflakeRESTClientViaPAT)
        assert client.timeout == 45.0


# ---------------------------------------------------------------------------
# AuthConfig / CredentialsConfig / SnowflakeConnectorConfig
# ---------------------------------------------------------------------------


class TestAuthConfig:
    def test_defaults(self):
        cfg = AuthConfig()
        assert cfg.authType == AuthType.PAT
        assert cfg.patToken is None

    def test_oauth_type(self):
        cfg = AuthConfig(authType=AuthType.OAUTH)
        assert cfg.authType == AuthType.OAUTH

    def test_pat_token(self):
        cfg = AuthConfig(authType=AuthType.PAT, patToken="pat-xyz")
        assert cfg.patToken == "pat-xyz"


class TestCredentialsConfig:
    def test_defaults(self):
        cfg = CredentialsConfig()
        assert cfg.access_token is None

    def test_access_token(self):
        cfg = CredentialsConfig(access_token="tok")
        assert cfg.access_token == "tok"


class TestSnowflakeConnectorConfig:
    def test_from_dict(self):
        cfg = SnowflakeConnectorConfig.model_validate({
            "accountIdentifier": "acct",
            "auth": {"authType": "PAT", "patToken": "tok"},
            "timeout": 60.0,
        })
        assert cfg.accountIdentifier == "acct"
        assert cfg.auth.authType == AuthType.PAT
        assert cfg.auth.patToken == "tok"
        assert cfg.timeout == 60.0

    def test_defaults(self):
        cfg = SnowflakeConnectorConfig.model_validate({
            "accountIdentifier": "acct",
        })
        assert cfg.auth.authType == AuthType.PAT
        assert cfg.auth.patToken is None
        assert cfg.credentials is None
        assert cfg.timeout == 30.0

    def test_oauth_with_credentials(self):
        cfg = SnowflakeConnectorConfig.model_validate({
            "accountIdentifier": "acct",
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "tok"},
        })
        assert cfg.auth.authType == AuthType.OAUTH
        assert cfg.credentials.access_token == "tok"

    def test_missing_account_identifier(self):
        with pytest.raises(ValidationError):
            SnowflakeConnectorConfig.model_validate({})

    def test_invalid_timeout(self):
        with pytest.raises(ValidationError):
            SnowflakeConnectorConfig.model_validate({
                "accountIdentifier": "acct", "timeout": 0,
            })


# ---------------------------------------------------------------------------
# SnowflakeClient (builder)
# ---------------------------------------------------------------------------


class TestSnowflakeClient:
    def test_init_and_get_client(self):
        inner = SnowflakeRESTClientViaPAT(
            account_identifier="acct", pat_token="tok"
        )
        client = SnowflakeClient(inner)
        assert client.get_client() is inner

    def test_get_base_url(self):
        inner = SnowflakeRESTClientViaPAT(
            account_identifier="acct", pat_token="tok"
        )
        client = SnowflakeClient(inner)
        assert client.get_base_url() == "https://acct.snowflakecomputing.com/api/v2"

    def test_get_account_identifier(self):
        inner = SnowflakeRESTClientViaOAuth(
            account_identifier="acct", oauth_token="tok"
        )
        client = SnowflakeClient(inner)
        assert client.get_account_identifier() == "acct"

    def test_build_with_pat_config(self):
        cfg = SnowflakePATConfig(account_identifier="acct", pat_token="tok")
        client = SnowflakeClient.build_with_config(cfg)
        assert isinstance(client, SnowflakeClient)
        assert isinstance(client.get_client(), SnowflakeRESTClientViaPAT)

    def test_build_with_oauth_config(self):
        cfg = SnowflakeOAuthConfig(
            account_identifier="acct", oauth_token="tok"
        )
        client = SnowflakeClient.build_with_config(cfg)
        assert isinstance(client, SnowflakeClient)
        assert isinstance(client.get_client(), SnowflakeRESTClientViaOAuth)

    @pytest.mark.asyncio
    async def test_build_from_services_pat(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "accountIdentifier": "acct",
            "auth": {"authType": "PAT", "patToken": "pat-tok"},
            "timeout": 30.0,
        })
        client = await SnowflakeClient.build_from_services(log, cs, "inst-1")
        assert isinstance(client, SnowflakeClient)
        inner = client.get_client()
        assert isinstance(inner, SnowflakeRESTClientViaPAT)
        assert inner.pat_token == "pat-tok"

    @pytest.mark.asyncio
    async def test_build_from_services_oauth(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "accountIdentifier": "acct",
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "oauth-tok"},
        })
        client = await SnowflakeClient.build_from_services(log, cs, "inst-1")
        inner = client.get_client()
        assert isinstance(inner, SnowflakeRESTClientViaOAuth)
        assert inner.oauth_token == "oauth-tok"

    @pytest.mark.asyncio
    async def test_build_from_services_pat_missing_token(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "accountIdentifier": "acct",
            "auth": {"authType": "PAT"},
        })
        with pytest.raises(ValueError, match="PAT token required"):
            await SnowflakeClient.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_missing_credentials(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "accountIdentifier": "acct",
            "auth": {"authType": "OAUTH"},
        })
        with pytest.raises(ValueError, match="OAuth access token required"):
            await SnowflakeClient.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_empty_access_token(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "accountIdentifier": "acct",
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": None},
        })
        with pytest.raises(ValueError, match="OAuth access token required"):
            await SnowflakeClient.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_empty_config_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Snowflake"):
            await SnowflakeClient.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_not_dict_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value="not a dict")
        with pytest.raises(ValueError):
            await SnowflakeClient.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_validation_error(self, log, cs):
        # Truthy dict but missing required accountIdentifier → Pydantic fails
        cs.get_config = AsyncMock(return_value={"auth": {"authType": "PAT"}})
        with pytest.raises(ValueError, match="Invalid Snowflake"):
            await SnowflakeClient.build_from_services(log, cs, "inst-1")


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestSnowflakeGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, log, cs):
        cs.get_config = AsyncMock(return_value={"accountIdentifier": "acct"})
        result = await SnowflakeClient._get_connector_config(log, cs, "inst-1")
        assert result == {"accountIdentifier": "acct"}

    @pytest.mark.asyncio
    async def test_none_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Snowflake"):
            await SnowflakeClient._get_connector_config(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_not_dict_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value="string value")
        with pytest.raises(ValueError, match="Failed to get Snowflake"):
            await SnowflakeClient._get_connector_config(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, log, cs):
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(ValueError, match="Failed to get Snowflake"):
            await SnowflakeClient._get_connector_config(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_no_instance_id_in_message(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await SnowflakeClient._get_connector_config(log, cs, None)
        assert "for instance" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_instance_id_in_message(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await SnowflakeClient._get_connector_config(log, cs, "inst-xyz")
        assert "inst-xyz" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SnowflakeSDKClient
# ---------------------------------------------------------------------------


class TestSnowflakeSDKClientInit:
    def test_missing_connector_raises(self):
        with patch(
            "app.sources.client.snowflake.snowflake.snowflake_connector", None
        ):
            with pytest.raises(
                ImportError, match="snowflake-connector-python is required"
            ):
                SnowflakeSDKClient(account_identifier="acct")

    def test_clean_plain_identifier(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        assert c.account_identifier == "acct"

    def test_clean_with_https_prefix(self):
        c = SnowflakeSDKClient(account_identifier="https://acct")
        assert c.account_identifier == "acct"

    def test_clean_with_http_prefix(self):
        c = SnowflakeSDKClient(account_identifier="http://acct")
        assert c.account_identifier == "acct"

    def test_clean_with_snowflakecomputing_suffix(self):
        c = SnowflakeSDKClient(
            account_identifier="acct.snowflakecomputing.com"
        )
        assert c.account_identifier == "acct"

    def test_clean_with_full_url(self):
        c = SnowflakeSDKClient(
            account_identifier="https://acct.snowflakecomputing.com/some/path"
        )
        assert c.account_identifier == "acct"

    def test_init_with_all_options(self):
        c = SnowflakeSDKClient(
            account_identifier="acct",
            warehouse="wh",
            database="db",
            schema="sch",
            role="r",
            oauth_token="tok",
            timeout=120,
        )
        assert c.warehouse == "wh"
        assert c.database == "db"
        assert c.schema == "sch"
        assert c.role == "r"
        assert c._oauth_token == "tok"
        assert c._authenticator == "oauth"
        assert c.timeout == 120

    def test_init_with_user_password(self):
        c = SnowflakeSDKClient(
            account_identifier="acct", user="u", password="p"
        )
        assert c._user == "u"
        assert c._password == "p"
        assert c._authenticator is None

    def test_init_with_external_browser(self):
        c = SnowflakeSDKClient(
            account_identifier="acct", authenticator="externalbrowser", user="u"
        )
        assert c._authenticator == "externalbrowser"

    def test_get_account_identifier(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        assert c.get_account_identifier() == "acct"


class TestSnowflakeSDKClientConnection:
    def test_connect_already_connected(self, sf_connector):
        c = SnowflakeSDKClient(account_identifier="acct")
        c._connection = MagicMock()
        result = c.connect()
        assert result is c
        sf_connector.connect.assert_not_called()

    def test_connect_with_oauth(self, sf_connector):
        mock_conn = MagicMock()
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct", oauth_token="tok"
        )
        result = c.connect()

        assert result is c
        assert c._connection is mock_conn
        kwargs = sf_connector.connect.call_args.kwargs
        assert kwargs["account"] == "acct"
        assert kwargs["token"] == "tok"
        assert kwargs["authenticator"] == "oauth"

    def test_connect_with_user_password(self, sf_connector):
        mock_conn = MagicMock()
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct", user="u", password="p"
        )
        c.connect()
        kwargs = sf_connector.connect.call_args.kwargs
        assert kwargs["user"] == "u"
        assert kwargs["password"] == "p"
        assert "token" not in kwargs

    def test_connect_with_external_browser(self, sf_connector):
        mock_conn = MagicMock()
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct",
            authenticator="externalbrowser",
            user="u",
        )
        c.connect()
        kwargs = sf_connector.connect.call_args.kwargs
        assert kwargs["authenticator"] == "externalbrowser"
        assert kwargs["user"] == "u"

    def test_connect_with_all_optional_params(self, sf_connector):
        mock_conn = MagicMock()
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct",
            warehouse="wh",
            database="db",
            schema="sch",
            role="r",
            oauth_token="tok",
        )
        c.connect()
        kwargs = sf_connector.connect.call_args.kwargs
        assert kwargs["warehouse"] == "wh"
        assert kwargs["database"] == "db"
        assert kwargs["schema"] == "sch"
        assert kwargs["role"] == "r"

    def test_connect_failure_raises(self, sf_connector):
        sf_connector.connect.side_effect = Exception("denied")
        c = SnowflakeSDKClient(
            account_identifier="acct", oauth_token="tok"
        )
        with pytest.raises(ConnectionError, match="Failed to connect to Snowflake"):
            c.connect()

    def test_close(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        mock_conn = MagicMock()
        c._connection = mock_conn
        c.close()
        mock_conn.close.assert_called_once()
        assert c._connection is None

    def test_close_no_connection(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        c.close()  # should not raise

    def test_close_error_swallowed(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("close failed")
        c._connection = mock_conn
        c.close()
        assert c._connection is None

    def test_is_connected_true(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        c._connection = mock_conn
        assert c.is_connected() is True

    def test_is_connected_false_no_connection(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        assert c.is_connected() is False

    def test_is_connected_false_closed(self):
        c = SnowflakeSDKClient(account_identifier="acct")
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = True
        c._connection = mock_conn
        assert c.is_connected() is False

    def test_context_manager(self, sf_connector):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct", oauth_token="tok"
        )
        with c as ctx:
            assert ctx is c
            assert c._connection is mock_conn
        mock_conn.close.assert_called_once()
        assert c._connection is None


class TestSnowflakeSDKClientExecuteQuery:
    def _make_connected(self, cursor):
        c = SnowflakeSDKClient(account_identifier="acct")
        conn = MagicMock()
        conn.is_closed.return_value = False
        conn.cursor.return_value = cursor
        c._connection = conn
        return c, conn

    def test_with_results(self):
        cursor = MagicMock()
        cursor.description = [("id",), ("name",)]
        cursor.fetchall.return_value = [(1, "a"), (2, "b")]
        c, _ = self._make_connected(cursor)

        results = c.execute_query("SELECT * FROM t")

        assert results == [
            {"id": 1, "name": "a"},
            {"id": 2, "name": "b"},
        ]
        cursor.close.assert_called_once()

    def test_no_description_returns_empty_list(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        c, _ = self._make_connected(cursor)

        results = c.execute_query("CREATE TABLE t (id INT)")
        assert results == []

    def test_with_params(self):
        cursor = MagicMock()
        cursor.description = [("id",)]
        cursor.fetchall.return_value = [(1,)]
        c, _ = self._make_connected(cursor)

        c.execute_query("SELECT * FROM t WHERE id = %s", (1,))
        cursor.execute.assert_called_once_with(
            "SELECT * FROM t WHERE id = %s", (1,)
        )

    def test_no_params(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        c, _ = self._make_connected(cursor)

        c.execute_query("SELECT 1")
        cursor.execute.assert_called_once_with("SELECT 1")

    def test_with_query_timeout(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        c, _ = self._make_connected(cursor)

        c.execute_query("SELECT 1", timeout=120)

        # First call is the ALTER SESSION for timeout, second is the query
        assert cursor.execute.call_count == 2
        first_call = cursor.execute.call_args_list[0]
        assert "STATEMENT_TIMEOUT_IN_SECONDS = 120" in first_call.args[0]

    def test_invalid_timeout_type_raises(self):
        cursor = MagicMock()
        c, _ = self._make_connected(cursor)
        with pytest.raises(RuntimeError, match="Query execution failed"):
            c.execute_query("SELECT 1", timeout="bad")  # type: ignore

    def test_negative_timeout_raises(self):
        cursor = MagicMock()
        c, _ = self._make_connected(cursor)
        with pytest.raises(RuntimeError, match="Query execution failed"):
            c.execute_query("SELECT 1", timeout=-1)

    def test_auto_connects(self, sf_connector):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct", oauth_token="tok"
        )
        c.execute_query("SELECT 1")
        sf_connector.connect.assert_called_once()

    def test_failure_raises(self):
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("bad query")
        c, _ = self._make_connected(cursor)
        with pytest.raises(RuntimeError, match="Query execution failed"):
            c.execute_query("BAD SQL")


class TestSnowflakeSDKClientExecuteQueryRaw:
    def _make_connected(self, cursor):
        c = SnowflakeSDKClient(account_identifier="acct")
        conn = MagicMock()
        conn.is_closed.return_value = False
        conn.cursor.return_value = cursor
        c._connection = conn
        return c

    def test_with_results(self):
        cursor = MagicMock()
        cursor.description = [("id",), ("name",)]
        cursor.fetchall.return_value = [(1, "a")]
        c = self._make_connected(cursor)

        columns, rows = c.execute_query_raw("SELECT * FROM t")
        assert columns == ["id", "name"]
        assert rows == [(1, "a")]
        cursor.close.assert_called_once()

    def test_no_description(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        c = self._make_connected(cursor)

        columns, rows = c.execute_query_raw("INSERT INTO t VALUES (1)")
        assert columns == []
        assert rows == []

    def test_with_params(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        c = self._make_connected(cursor)

        c.execute_query_raw("SELECT * FROM t WHERE x = %s", (1,))
        cursor.execute.assert_called_once_with(
            "SELECT * FROM t WHERE x = %s", (1,)
        )

    def test_no_params(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        c = self._make_connected(cursor)
        c.execute_query_raw("SELECT 1")
        cursor.execute.assert_called_once_with("SELECT 1")

    def test_auto_connects(self, sf_connector):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct", oauth_token="tok"
        )
        c.execute_query_raw("SELECT 1")
        sf_connector.connect.assert_called_once()

    def test_failure_raises(self):
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("bad query")
        c = self._make_connected(cursor)
        with pytest.raises(RuntimeError, match="Query execution failed"):
            c.execute_query_raw("BAD SQL")


class TestSnowflakeSDKClientExecuteMany:
    def _make_connected(self, cursor):
        c = SnowflakeSDKClient(account_identifier="acct")
        conn = MagicMock()
        conn.is_closed.return_value = False
        conn.cursor.return_value = cursor
        c._connection = conn
        return c

    def test_success(self):
        cursor = MagicMock()
        cursor.rowcount = 5
        c = self._make_connected(cursor)

        result = c.execute_many(
            "INSERT INTO t VALUES (%s)", [(1,), (2,), (3,)]
        )
        cursor.executemany.assert_called_once_with(
            "INSERT INTO t VALUES (%s)", [(1,), (2,), (3,)]
        )
        assert result == 5
        cursor.close.assert_called_once()

    def test_auto_connects(self, sf_connector):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        cursor = MagicMock()
        cursor.rowcount = 0
        mock_conn.cursor.return_value = cursor
        sf_connector.connect.return_value = mock_conn

        c = SnowflakeSDKClient(
            account_identifier="acct", oauth_token="tok"
        )
        c.execute_many("INSERT INTO t VALUES (%s)", [(1,)])
        sf_connector.connect.assert_called_once()

    def test_failure_raises(self):
        cursor = MagicMock()
        cursor.executemany.side_effect = Exception("bad batch")
        c = self._make_connected(cursor)
        with pytest.raises(RuntimeError, match="Query execution failed"):
            c.execute_many("BAD SQL", [(1,)])
