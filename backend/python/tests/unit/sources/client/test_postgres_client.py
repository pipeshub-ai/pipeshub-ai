"""Unit tests for PostgreSQL client module."""

import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

# Install a mock 'psycopg2' module into sys.modules BEFORE importing the
# source module, because the source does ``import psycopg2`` at module level
# and sets it to None if the import fails.
_mock_psycopg2_module = MagicMock()
_mock_psycopg2_extras = MagicMock()
_mock_psycopg2_module.extras = _mock_psycopg2_extras
sys.modules["psycopg2"] = _mock_psycopg2_module
sys.modules["psycopg2.extras"] = _mock_psycopg2_extras

from app.sources.client.postgres.postgres import (  # noqa: E402
    AuthConfig,
    PostgreSQLClient,
    PostgreSQLClientBuilder,
    PostgreSQLConfig,
    PostgreSQLConnectorConfig,
    PostgreSQLResponse,
)

import app.sources.client.postgres.postgres as _pg_mod  # noqa: E402

_pg_mod.psycopg2 = _mock_psycopg2_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def log():
    lg = logging.getLogger("test_postgres_client")
    lg.setLevel(logging.CRITICAL)
    return lg


@pytest.fixture
def cs():
    return AsyncMock()


@pytest.fixture
def pg_module():
    """Return the mock psycopg2 module and reset state between tests."""
    _mock_psycopg2_module.reset_mock()
    _mock_psycopg2_module.extras = _mock_psycopg2_extras
    return _mock_psycopg2_module


# ---------------------------------------------------------------------------
# PostgreSQLResponse
# ---------------------------------------------------------------------------


class TestPostgreSQLResponse:
    def test_success(self):
        resp = PostgreSQLResponse(success=True, data={"key": "val"})
        assert resp.success is True
        assert resp.data == {"key": "val"}

    def test_defaults(self):
        resp = PostgreSQLResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None

    def test_to_dict_excludes_none(self):
        resp = PostgreSQLResponse(success=True, data={"key": "val"})
        d = resp.to_dict()
        assert d["success"] is True
        assert "data" in d
        assert "error" not in d
        assert "message" not in d

    def test_to_json(self):
        resp = PostgreSQLResponse(success=False, error="oops")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is False
        assert parsed["error"] == "oops"

    def test_to_json_excludes_none(self):
        resp = PostgreSQLResponse(success=True, message="OK")
        j = resp.to_json()
        assert '"success":true' in j
        assert '"message":"OK"' in j
        assert "data" not in j

    def test_extra_allowed(self):
        resp = PostgreSQLResponse(success=True, extra_field="x")
        d = resp.to_dict()
        assert d.get("extra_field") == "x"


# ---------------------------------------------------------------------------
# PostgreSQLClient — init / error paths
# ---------------------------------------------------------------------------


class TestPostgreSQLClientInit:
    def test_init_defaults(self):
        client = PostgreSQLClient(
            host="localhost", database="mydb", user="root", password="pass"
        )
        assert client.host == "localhost"
        assert client.database == "mydb"
        assert client.user == "root"
        assert client.password == "pass"
        assert client.port == 5432
        assert client.timeout == 30
        assert client.sslmode == "prefer"
        assert client._connection is None

    def test_init_custom_values(self):
        client = PostgreSQLClient(
            host="db.example.com",
            database="mydb",
            user="u",
            password="p",
            port=5433,
            timeout=60,
            sslmode="require",
        )
        assert client.port == 5433
        assert client.timeout == 60
        assert client.sslmode == "require"

    def test_init_missing_psycopg2_raises(self):
        with patch("app.sources.client.postgres.postgres.psycopg2", None):
            with pytest.raises(ImportError, match="psycopg2 is required"):
                PostgreSQLClient(
                    host="localhost", database="db", user="u", password="p"
                )


# ---------------------------------------------------------------------------
# PostgreSQLClient — connect / close / is_connected
# ---------------------------------------------------------------------------


class TestPostgreSQLClientConnection:
    def test_connect_success(self, pg_module):
        mock_conn = MagicMock()
        mock_conn.closed = False
        pg_module.connect.return_value = mock_conn

        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        result = client.connect()

        assert result is client
        assert client._connection is mock_conn
        call_kwargs = pg_module.connect.call_args.kwargs
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["database"] == "db"
        assert call_kwargs["port"] == 5432
        assert call_kwargs["sslmode"] == "prefer"
        assert call_kwargs["connect_timeout"] == 30

    def test_connect_with_custom_sslmode(self, pg_module):
        mock_conn = MagicMock()
        mock_conn.closed = False
        pg_module.connect.return_value = mock_conn

        client = PostgreSQLClient(
            host="h", database="d", user="u", password="p", sslmode="require"
        )
        client.connect()

        call_kwargs = pg_module.connect.call_args.kwargs
        assert call_kwargs["sslmode"] == "require"

    def test_connect_already_connected_returns_self(self, pg_module):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        existing_conn = MagicMock()
        existing_conn.closed = False
        client._connection = existing_conn

        result = client.connect()

        assert result is client
        # psycopg2.connect should NOT be called again
        pg_module.connect.assert_not_called()

    def test_connect_reconnects_when_closed(self, pg_module):
        mock_new_conn = MagicMock()
        mock_new_conn.closed = False
        pg_module.connect.return_value = mock_new_conn

        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        closed_conn = MagicMock()
        closed_conn.closed = True
        client._connection = closed_conn

        client.connect()
        assert client._connection is mock_new_conn
        pg_module.connect.assert_called_once()

    def test_connect_failure_raises_connection_error(self, pg_module):
        pg_module.connect.side_effect = Exception("connection refused")

        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        with pytest.raises(ConnectionError, match="Failed to connect to PostgreSQL"):
            client.connect()

    def test_close(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_conn = MagicMock()
        client._connection = mock_conn

        client.close()

        mock_conn.close.assert_called_once()
        assert client._connection is None

    def test_close_no_connection_is_noop(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        client.close()  # Should not raise
        assert client._connection is None

    def test_close_error_is_swallowed(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("close failed")
        client._connection = mock_conn

        client.close()  # Should not raise
        assert client._connection is None

    def test_is_connected_true(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_conn = MagicMock()
        mock_conn.closed = False
        client._connection = mock_conn
        assert client.is_connected() is True

    def test_is_connected_false_when_no_connection(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        assert client.is_connected() is False

    def test_is_connected_false_when_connection_closed(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_conn = MagicMock()
        mock_conn.closed = True
        client._connection = mock_conn
        assert client.is_connected() is False

    def test_context_manager_connects_and_closes(self, pg_module):
        mock_conn = MagicMock()
        mock_conn.closed = False
        pg_module.connect.side_effect = None
        pg_module.connect.return_value = mock_conn

        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        with client as c:
            assert c is client
            assert client._connection is mock_conn

        mock_conn.close.assert_called_once()
        assert client._connection is None


# ---------------------------------------------------------------------------
# PostgreSQLClient — execute_query
# ---------------------------------------------------------------------------


class TestPostgreSQLClientExecuteQuery:
    def _make_connected_client(self, cursor):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_conn.cursor.return_value = cursor
        client._connection = mock_conn
        return client, mock_conn

    def test_execute_query_with_results(self):
        cursor = MagicMock()
        cursor.description = [("id",), ("name",)]
        cursor.fetchall.return_value = [{"id": 1, "name": "test"}]
        client, conn = self._make_connected_client(cursor)

        results = client.execute_query("SELECT * FROM t")

        assert results == [{"id": 1, "name": "test"}]
        conn.commit.assert_called_once()
        cursor.close.assert_called_once()

    def test_execute_query_with_params(self):
        cursor = MagicMock()
        cursor.description = [("id",)]
        cursor.fetchall.return_value = [{"id": 1}]
        client, _ = self._make_connected_client(cursor)

        client.execute_query("SELECT * FROM t WHERE id = %s", (1,))

        cursor.execute.assert_called_once_with("SELECT * FROM t WHERE id = %s", (1,))

    def test_execute_query_without_params(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.rowcount = 0
        client, _ = self._make_connected_client(cursor)

        client.execute_query("SELECT 1")

        cursor.execute.assert_called_once_with("SELECT 1")

    def test_execute_query_no_description_returns_affected_rows(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.rowcount = 3
        client, conn = self._make_connected_client(cursor)

        results = client.execute_query("DELETE FROM t WHERE id < 10")

        assert results == [{"affected_rows": 3}]
        conn.commit.assert_called_once()

    def test_execute_query_auto_connects(self, pg_module):
        """When not connected, execute_query should call connect() first."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        cursor = MagicMock()
        cursor.description = None
        cursor.rowcount = 0
        mock_conn.cursor.return_value = cursor
        pg_module.connect.return_value = mock_conn

        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        # Start with no connection — should trigger connect()
        assert client._connection is None
        client.execute_query("SELECT 1")

        pg_module.connect.assert_called_once()
        assert client._connection is mock_conn

    def test_execute_query_failure_rolls_back_and_raises(self):
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("syntax error")
        client, conn = self._make_connected_client(cursor)

        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query("BAD SQL")

        conn.rollback.assert_called_once()

    def test_execute_query_uses_realdict_cursor(self, pg_module):
        cursor = MagicMock()
        cursor.description = None
        cursor.rowcount = 0
        client, conn = self._make_connected_client(cursor)
        # Ensure RealDictCursor is referenced on the extras mock
        pg_module.extras.RealDictCursor = object

        client.execute_query("SELECT 1")
        # cursor() should be called with cursor_factory kwarg
        call_kwargs = conn.cursor.call_args.kwargs
        assert "cursor_factory" in call_kwargs


# ---------------------------------------------------------------------------
# PostgreSQLClient — execute_query_raw
# ---------------------------------------------------------------------------


class TestPostgreSQLClientExecuteQueryRaw:
    def _make_connected_client(self, cursor):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_conn.cursor.return_value = cursor
        client._connection = mock_conn
        return client, mock_conn

    def test_with_results(self):
        cursor = MagicMock()
        cursor.description = [("id",), ("name",)]
        cursor.fetchall.return_value = [(1, "a"), (2, "b")]
        client, conn = self._make_connected_client(cursor)

        columns, rows = client.execute_query_raw("SELECT * FROM t")

        assert columns == ["id", "name"]
        assert rows == [(1, "a"), (2, "b")]
        conn.commit.assert_called_once()
        cursor.close.assert_called_once()

    def test_no_description_returns_empty(self):
        cursor = MagicMock()
        cursor.description = None
        client, _ = self._make_connected_client(cursor)

        columns, rows = client.execute_query_raw("INSERT INTO t VALUES (1)")

        assert columns == []
        assert rows == []

    def test_with_params(self):
        cursor = MagicMock()
        cursor.description = [("id",)]
        cursor.fetchall.return_value = [(1,)]
        client, _ = self._make_connected_client(cursor)

        client.execute_query_raw("SELECT * FROM t WHERE id = %s", (1,))
        cursor.execute.assert_called_once_with(
            "SELECT * FROM t WHERE id = %s", (1,)
        )

    def test_no_params(self):
        cursor = MagicMock()
        cursor.description = None
        client, _ = self._make_connected_client(cursor)

        client.execute_query_raw("SELECT 1")
        cursor.execute.assert_called_once_with("SELECT 1")

    def test_auto_connects(self, pg_module):
        mock_conn = MagicMock()
        mock_conn.closed = False
        cursor = MagicMock()
        cursor.description = None
        mock_conn.cursor.return_value = cursor
        pg_module.connect.return_value = mock_conn

        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        client.execute_query_raw("SELECT 1")
        pg_module.connect.assert_called_once()

    def test_failure_rolls_back_and_raises(self):
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("bad query")
        client, conn = self._make_connected_client(cursor)

        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query_raw("BAD SQL")
        conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# PostgreSQLClient — get_connection_info
# ---------------------------------------------------------------------------


class TestPostgreSQLClientConnectionInfo:
    def test_get_connection_info_excludes_password(self):
        client = PostgreSQLClient(
            host="h", database="d", user="u", password="secret", port=5433,
            sslmode="require"
        )
        info = client.get_connection_info()
        assert info == {
            "host": "h",
            "port": 5433,
            "database": "d",
            "user": "u",
            "sslmode": "require",
        }
        assert "password" not in info


# ---------------------------------------------------------------------------
# PostgreSQLConfig
# ---------------------------------------------------------------------------


class TestPostgreSQLConfig:
    def test_defaults(self):
        cfg = PostgreSQLConfig(host="h", database="d", user="u")
        assert cfg.port == 5432
        assert cfg.password == ""
        assert cfg.timeout == 30
        assert cfg.sslmode == "prefer"

    def test_all_values(self):
        cfg = PostgreSQLConfig(
            host="h", database="d", user="u", password="p",
            port=5433, timeout=60, sslmode="require",
        )
        assert cfg.port == 5433
        assert cfg.timeout == 60
        assert cfg.sslmode == "require"

    def test_missing_host_raises(self):
        with pytest.raises(ValidationError):
            PostgreSQLConfig(database="d", user="u")

    def test_missing_database_raises(self):
        with pytest.raises(ValidationError):
            PostgreSQLConfig(host="h", user="u")

    def test_missing_user_raises(self):
        with pytest.raises(ValidationError):
            PostgreSQLConfig(host="h", database="d")

    def test_accepts_username_alias(self):
        cfg = PostgreSQLConfig.model_validate(
            {"host": "h", "database": "d", "username": "admin"}
        )
        assert cfg.user == "admin"

    def test_invalid_port_low(self):
        with pytest.raises(ValidationError):
            PostgreSQLConfig(host="h", database="d", user="u", port=0)

    def test_invalid_port_high(self):
        with pytest.raises(ValidationError):
            PostgreSQLConfig(host="h", database="d", user="u", port=70000)

    def test_invalid_timeout_zero(self):
        with pytest.raises(ValidationError):
            PostgreSQLConfig(host="h", database="d", user="u", timeout=0)

    def test_create_client(self):
        cfg = PostgreSQLConfig(host="h", database="d", user="u", password="p")
        client = cfg.create_client()
        assert isinstance(client, PostgreSQLClient)
        assert client.host == "h"
        assert client.database == "d"
        assert client.user == "u"
        assert client.password == "p"


# ---------------------------------------------------------------------------
# AuthConfig / PostgreSQLConnectorConfig
# ---------------------------------------------------------------------------


class TestAuthConfig:
    def test_defaults(self):
        cfg = AuthConfig(host="h", database="d", user="u")
        assert cfg.port == 5432
        assert cfg.password == ""
        assert cfg.sslmode == "prefer"

    def test_username_alias(self):
        cfg = AuthConfig.model_validate(
            {"host": "h", "database": "d", "username": "root"}
        )
        assert cfg.user == "root"

    def test_full(self):
        cfg = AuthConfig(
            host="h", port=5433, database="d", user="u", password="p",
            sslmode="require",
        )
        assert cfg.port == 5433
        assert cfg.sslmode == "require"

    def test_populates_from_connection_string(self):
        cfg = AuthConfig.model_validate({
            "connection_string": "postgresql://alice:secret@db.example.com:6432/mydb",
        })
        assert cfg.host == "db.example.com"
        assert cfg.port == 6432
        assert cfg.database == "mydb"
        assert cfg.user == "alice"
        assert cfg.password == "secret"

    def test_connection_string_alias(self):
        cfg = AuthConfig.model_validate({
            "connectionString": "postgresql://u:p@h:5432/db",
        })
        assert cfg.host == "h"
        assert cfg.database == "db"
        assert cfg.user == "u"

    def test_url_encoded_credentials_are_decoded(self):
        # Password contains characters that must be percent-encoded in a URI.
        cfg = AuthConfig.model_validate({
            "connection_string": "postgresql://alice%40corp:p%40ss%2Fword@h/db",
        })
        assert cfg.user == "alice@corp"
        assert cfg.password == "p@ss/word"

    def test_explicit_fields_override_connection_string(self):
        # If caller provides both, explicit fields win over the parsed DSN.
        cfg = AuthConfig.model_validate({
            "connection_string": "postgresql://a:b@h1:5432/db1",
            "host": "h2",
            "database": "db2",
        })
        assert cfg.host == "h2"
        assert cfg.database == "db2"
        # Fields not explicitly set still come from the connection string.
        assert cfg.user == "a"

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            AuthConfig.model_validate({"host": "h", "database": "d"})

    def test_connection_string_missing_database_raises(self):
        with pytest.raises(ValidationError):
            AuthConfig.model_validate({
                "connection_string": "postgresql://user@host:5432/",
            })


class TestPostgreSQLConnectorConfig:
    def test_from_dict(self):
        cfg = PostgreSQLConnectorConfig.model_validate({
            "auth": {
                "host": "localhost", "port": 5432,
                "database": "mydb", "user": "root", "password": "pass",
            },
            "timeout": 30,
        })
        assert cfg.auth.host == "localhost"
        assert cfg.auth.database == "mydb"
        assert cfg.timeout == 30

    def test_defaults_timeout(self):
        cfg = PostgreSQLConnectorConfig.model_validate({
            "auth": {"host": "h", "database": "d", "user": "u"},
        })
        assert cfg.timeout == 30

    def test_invalid_timeout(self):
        with pytest.raises(ValidationError):
            PostgreSQLConnectorConfig.model_validate({
                "auth": {"host": "h", "database": "d", "user": "u"},
                "timeout": 0,
            })


# ---------------------------------------------------------------------------
# PostgreSQLClientBuilder
# ---------------------------------------------------------------------------


class TestPostgreSQLClientBuilder:
    def test_init_and_get_client(self):
        mock_client = MagicMock(spec=PostgreSQLClient)
        builder = PostgreSQLClientBuilder(mock_client)
        assert builder.get_client() is mock_client

    def test_get_connection_info(self):
        mock_client = MagicMock(spec=PostgreSQLClient)
        mock_client.get_connection_info.return_value = {"host": "localhost"}
        builder = PostgreSQLClientBuilder(mock_client)
        assert builder.get_connection_info() == {"host": "localhost"}

    def test_build_with_config(self):
        cfg = PostgreSQLConfig(host="h", database="d", user="u", password="p")
        builder = PostgreSQLClientBuilder.build_with_config(cfg)
        assert isinstance(builder, PostgreSQLClientBuilder)
        assert isinstance(builder.get_client(), PostgreSQLClient)

    @pytest.mark.asyncio
    async def test_build_from_services_success(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "auth": {
                "host": "localhost", "port": 5432,
                "database": "mydb", "user": "root", "password": "pass",
            },
            "timeout": 30,
        })
        builder = await PostgreSQLClientBuilder.build_from_services(
            log, cs, "inst-1"
        )
        assert isinstance(builder, PostgreSQLClientBuilder)
        client = builder.get_client()
        assert client.host == "localhost"
        assert client.database == "mydb"

    @pytest.mark.asyncio
    async def test_build_from_services_accepts_username_alias(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "auth": {
                "host": "h", "database": "d", "username": "admin",
                "password": "p",
            },
        })
        builder = await PostgreSQLClientBuilder.build_from_services(
            log, cs, "inst-1"
        )
        assert builder.get_client().user == "admin"

    @pytest.mark.asyncio
    async def test_build_from_services_no_config_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get PostgreSQL"):
            await PostgreSQLClientBuilder.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_not_dict_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value="not a dict")
        with pytest.raises(ValueError):
            await PostgreSQLClientBuilder.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_validation_error(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "auth": {"port": "not_a_number"},  # missing required fields
        })
        with pytest.raises(ValueError, match="Invalid PostgreSQL"):
            await PostgreSQLClientBuilder.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_generic_exception_reraises(self, log, cs):
        # If get_config raises a non-ValueError, should be wrapped by the
        # inner _get_connector_config's try/except as ValueError.
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(ValueError, match="Failed to get PostgreSQL"):
            await PostgreSQLClientBuilder.build_from_services(log, cs, "inst-1")


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_dict(self, log, cs):
        cs.get_config = AsyncMock(return_value={"auth": {}})
        result = await PostgreSQLClientBuilder._get_connector_config(
            log, cs, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get PostgreSQL"):
            await PostgreSQLClientBuilder._get_connector_config(
                log, cs, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_not_dict_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value="string config")
        with pytest.raises(ValueError, match="Failed to get PostgreSQL"):
            await PostgreSQLClientBuilder._get_connector_config(
                log, cs, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_exception_raises(self, log, cs):
        cs.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get PostgreSQL"):
            await PostgreSQLClientBuilder._get_connector_config(
                log, cs, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_no_instance_id_in_message(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await PostgreSQLClientBuilder._get_connector_config(log, cs, None)
        assert "for instance" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_instance_id_in_message(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await PostgreSQLClientBuilder._get_connector_config(
                log, cs, "inst-abc"
            )
        assert "inst-abc" in str(exc_info.value)
