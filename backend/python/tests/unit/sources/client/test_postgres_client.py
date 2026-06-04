"""Unit tests for PostgreSQL client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.sources.client.postgres.postgres import (  # noqa: E402
    AuthConfig,
    PostgreSQLClient,
    PostgreSQLClientBuilder,
    PostgreSQLConfig,
    PostgreSQLConnectorConfig,
    PostgreSQLResponse,
)

import app.sources.client.postgres.postgres as _pg_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool_mock():
    """Build an asyncpg-style pool mock with sensible defaults."""
    pool = MagicMock()
    pool.is_closing.return_value = False
    pool.close = AsyncMock()
    pool.terminate = MagicMock()
    return pool


def _make_async_connection_context(connection: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=connection)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_statement_mock(*, attributes, rows=None):
    statement = MagicMock()
    statement.get_attributes.return_value = attributes
    statement.fetch = AsyncMock(return_value=list(rows or []))
    return statement


def _make_attribute(name: str) -> MagicMock:
    attr = MagicMock()
    attr.name = name
    return attr


class _FakeRecord:
    """Minimal asyncpg.Record stand-in: iterable for tuple(row), keyed lookup for dict(row)."""

    def __init__(self, values: dict[str, object]) -> None:
        self._values = values
        self._items = tuple(values.values())

    def keys(self):
        return self._values.keys()

    def __getitem__(self, key: str | int) -> object:
        if isinstance(key, int):
            return self._items[key]
        return self._values[key]

    def __iter__(self):
        return iter(self._items)


class _FakeRecordDuplicateColumns:
    """Simulates asyncpg: name lookup returns first match; iteration is positional."""

    def __init__(self, items: tuple[object, ...]) -> None:
        self._items = items

    def __getitem__(self, key: str) -> object:
        return self._items[0]

    def __iter__(self):
        return iter(self._items)


@pytest.fixture
def log():
    lg = logging.getLogger("test_postgres_client")
    lg.setLevel(logging.CRITICAL)
    return lg


@pytest.fixture
def cs():
    return AsyncMock()


@pytest.fixture
def asyncpg_module():
    module = MagicMock()
    module.create_pool = AsyncMock(return_value=_make_pool_mock())
    return module


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
        assert client._pool is None
        assert client.min_pool_size == 1
        assert client.max_pool_size == 10

    def test_init_custom_values(self):
        client = PostgreSQLClient(
            host="db.example.com",
            database="mydb",
            user="u",
            password="p",
            port=5433,
            timeout=60,
            sslmode="require",
            min_pool_size=2,
            max_pool_size=8,
            pool_acquire_timeout=15.0,
        )
        assert client.port == 5433
        assert client.timeout == 60
        assert client.sslmode == "require"
        assert client.min_pool_size == 2
        assert client.max_pool_size == 8
        assert client.pool_acquire_timeout == 15.0

    def test_init_missing_asyncpg_raises(self):
        with patch("app.sources.client.postgres.postgres.asyncpg", None):
            with pytest.raises(ImportError, match="asyncpg is required"):
                PostgreSQLClient(
                    host="localhost", database="db", user="u", password="p"
                )

    def test_init_invalid_min_pool_size(self):
        with pytest.raises(ValueError, match="min_pool_size"):
            PostgreSQLClient(
                host="h", database="d", user="u", password="p", min_pool_size=0
            )

    def test_init_max_below_min(self):
        with pytest.raises(ValueError, match="max_pool_size"):
            PostgreSQLClient(
                host="h", database="d", user="u", password="p",
                min_pool_size=5, max_pool_size=2,
            )

    def test_init_invalid_acquire_timeout(self):
        with pytest.raises(ValueError, match="pool_acquire_timeout"):
            PostgreSQLClient(
                host="h", database="d", user="u", password="p",
                pool_acquire_timeout=0,
            )


# ---------------------------------------------------------------------------
# PostgreSQLClient — connect / close / is_connected
# ---------------------------------------------------------------------------


class TestPostgreSQLClientConnection:
    @pytest.mark.asyncio
    async def test_connect_success(self, asyncpg_module):
        mock_pool = _make_pool_mock()
        asyncpg_module.create_pool.return_value = mock_pool

        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="localhost", database="db", user="u", password="p"
            )
            result = await client.connect()

        assert result is client
        assert client._pool is mock_pool
        call_args = asyncpg_module.create_pool.call_args
        call_kwargs = call_args.kwargs
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["database"] == "db"
        assert call_kwargs["port"] == 5432
        assert call_kwargs["ssl"] == "prefer"
        assert call_kwargs["timeout"] == client.timeout
        assert call_kwargs["command_timeout"] == client.timeout
        assert call_kwargs["min_size"] == 1
        assert call_kwargs["max_size"] == 10

    @pytest.mark.asyncio
    async def test_connect_with_custom_sslmode(self, asyncpg_module):
        mock_pool = _make_pool_mock()
        asyncpg_module.create_pool.return_value = mock_pool

        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="h", database="d", user="u", password="p", sslmode="require"
            )
            await client.connect()

        call_kwargs = asyncpg_module.create_pool.call_args.kwargs
        assert call_kwargs["ssl"] == "require"

    @pytest.mark.asyncio
    async def test_connect_already_connected_returns_self(self, asyncpg_module):
        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="localhost", database="db", user="u", password="p"
            )
            existing_pool = _make_pool_mock()
            client._pool = existing_pool

            result = await client.connect()

        assert result is client
        asyncpg_module.create_pool.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_reconnects_when_pool_closed(self, asyncpg_module):
        mock_new_pool = _make_pool_mock()
        asyncpg_module.create_pool.return_value = mock_new_pool

        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="localhost", database="db", user="u", password="p"
            )
            closed_pool = _make_pool_mock()
            closed_pool.is_closing.return_value = True
            client._pool = closed_pool

            await client.connect()

        assert client._pool is mock_new_pool
        asyncpg_module.create_pool.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_failure_raises_connection_error(self, asyncpg_module):
        asyncpg_module.create_pool.side_effect = Exception("connection refused")

        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="localhost", database="db", user="u", password="p"
            )
            with pytest.raises(ConnectionError, match="Failed to connect to PostgreSQL"):
                await client.connect()

    @pytest.mark.asyncio
    async def test_close(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_pool = _make_pool_mock()
        client._pool = mock_pool

        await client.close()

        mock_pool.close.assert_awaited_once()
        assert client._pool is None

    @pytest.mark.asyncio
    async def test_close_no_pool_is_noop(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        await client.close()
        assert client._pool is None

    @pytest.mark.asyncio
    async def test_close_error_is_swallowed(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_pool = _make_pool_mock()
        mock_pool.close.side_effect = Exception("close failed")
        client._pool = mock_pool

        await client.close()
        mock_pool.terminate.assert_called_once()
        assert client._pool is None

    def test_is_connected_true(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        client._pool = _make_pool_mock()
        assert client.is_connected() is True

    def test_is_connected_false_when_no_pool(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        assert client.is_connected() is False

    def test_is_connected_false_when_pool_closed(self):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_pool = _make_pool_mock()
        mock_pool.is_closing.return_value = True
        client._pool = mock_pool
        assert client.is_connected() is False

    @pytest.mark.asyncio
    async def test_context_manager_connects_and_closes(self, asyncpg_module):
        mock_pool = _make_pool_mock()
        asyncpg_module.create_pool.return_value = mock_pool

        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="localhost", database="db", user="u", password="p"
            )
            async with client as c:
                assert c is client
                assert client._pool is mock_pool

        mock_pool.close.assert_awaited_once()
        assert client._pool is None


# ---------------------------------------------------------------------------
# PostgreSQLClient — resize_pool
# ---------------------------------------------------------------------------


class TestPostgreSQLClientResizePool:
    def test_resize_pool_updates_sizes(self):
        client = PostgreSQLClient(
            host="h", database="d", user="u", password="p"
        )
        result = client.resize_pool(min_pool_size=1, max_pool_size=1)
        assert result is client
        assert client.min_pool_size == 1
        assert client.max_pool_size == 1

    def test_resize_after_connect_raises(self):
        client = PostgreSQLClient(
            host="h", database="d", user="u", password="p"
        )
        client._pool = _make_pool_mock()
        with pytest.raises(RuntimeError, match="Cannot resize"):
            client.resize_pool(min_pool_size=1, max_pool_size=1)

    def test_resize_invalid_min(self):
        client = PostgreSQLClient(
            host="h", database="d", user="u", password="p"
        )
        with pytest.raises(ValueError, match="min_pool_size"):
            client.resize_pool(min_pool_size=0, max_pool_size=1)

    def test_resize_max_below_min(self):
        client = PostgreSQLClient(
            host="h", database="d", user="u", password="p"
        )
        with pytest.raises(ValueError, match="max_pool_size"):
            client.resize_pool(min_pool_size=4, max_pool_size=2)


# ---------------------------------------------------------------------------
# PostgreSQLClient — execute_query
# ---------------------------------------------------------------------------


class TestPostgreSQLClientExecuteQuery:
    def _make_pooled_client(self, *, statement=None, execute_result="DELETE 0", pool=None):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_pool = pool or _make_pool_mock()
        mock_conn = MagicMock()
        mock_conn.prepare = AsyncMock(return_value=statement)
        mock_conn.execute = AsyncMock(return_value=execute_result)
        mock_pool.acquire.return_value = _make_async_connection_context(mock_conn)
        client._pool = mock_pool
        return client, mock_pool, mock_conn

    @pytest.mark.asyncio
    async def test_execute_query_with_results(self):
        statement = _make_statement_mock(
            attributes=[_make_attribute("id"), _make_attribute("name")],
            rows=[_FakeRecord({"id": 1, "name": "test"})],
        )
        client, pool, conn = self._make_pooled_client(statement=statement)

        results = await client.execute_query("SELECT * FROM t")

        assert results == [{"id": 1, "name": "test"}]
        conn.prepare.assert_awaited_once_with("SELECT * FROM t")
        conn.execute.assert_not_called()
        pool.acquire.assert_called_once_with(timeout=client.pool_acquire_timeout)

    @pytest.mark.asyncio
    async def test_execute_query_with_params(self):
        statement = _make_statement_mock(
            attributes=[_make_attribute("id")],
            rows=[_FakeRecord({"id": 1})],
        )
        client, _, _ = self._make_pooled_client(statement=statement)

        await client.execute_query("SELECT * FROM t WHERE id = $1", (1,))
        statement.fetch.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_execute_query_without_params(self):
        statement = _make_statement_mock(attributes=[])
        client, _, conn = self._make_pooled_client(statement=statement, execute_result="SELECT 0")

        await client.execute_query("SELECT 1")
        conn.execute.assert_awaited_once_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_execute_query_no_description_returns_affected_rows(self):
        statement = _make_statement_mock(attributes=[])
        client, _, conn = self._make_pooled_client(statement=statement, execute_result="DELETE 3")

        results = await client.execute_query("DELETE FROM t WHERE id < 10")

        assert results == [{"affected_rows": 3}]
        conn.execute.assert_awaited_once_with("DELETE FROM t WHERE id < 10")

    @pytest.mark.asyncio
    async def test_execute_query_auto_connects(self, asyncpg_module):
        mock_pool = _make_pool_mock()
        statement = _make_statement_mock(attributes=[])
        mock_conn = MagicMock()
        mock_conn.prepare = AsyncMock(return_value=statement)
        mock_conn.execute = AsyncMock(return_value="SELECT 0")
        mock_pool.acquire.return_value = _make_async_connection_context(mock_conn)
        asyncpg_module.create_pool.return_value = mock_pool

        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="localhost", database="db", user="u", password="p"
            )
            assert client._pool is None
            await client.execute_query("SELECT 1")

        asyncpg_module.create_pool.assert_awaited_once()
        assert client._pool is mock_pool

    @pytest.mark.asyncio
    async def test_execute_query_failure_raises(self):
        statement = _make_statement_mock(attributes=[_make_attribute("id")])
        statement.fetch.side_effect = Exception("syntax error")
        client, _, _ = self._make_pooled_client(statement=statement)

        with pytest.raises(RuntimeError, match="Query execution failed"):
            await client.execute_query("BAD SQL")


# ---------------------------------------------------------------------------
# PostgreSQLClient — execute_query_raw
# ---------------------------------------------------------------------------


class TestPostgreSQLClientExecuteQueryRaw:
    def _make_pooled_client(self, *, statement=None, pool=None):
        client = PostgreSQLClient(
            host="localhost", database="db", user="u", password="p"
        )
        mock_pool = pool or _make_pool_mock()
        mock_conn = MagicMock()
        mock_conn.prepare = AsyncMock(return_value=statement)
        mock_conn.execute = AsyncMock(return_value="INSERT 0 1")
        mock_pool.acquire.return_value = _make_async_connection_context(mock_conn)
        client._pool = mock_pool
        return client, mock_pool, mock_conn

    @pytest.mark.asyncio
    async def test_with_results(self):
        statement = _make_statement_mock(
            attributes=[_make_attribute("id"), _make_attribute("name")],
            rows=[
                _FakeRecord({"id": 1, "name": "a"}),
                _FakeRecord({"id": 2, "name": "b"}),
            ],
        )
        client, pool, conn = self._make_pooled_client(statement=statement)

        columns, rows = await client.execute_query_raw("SELECT * FROM t")

        assert columns == ["id", "name"]
        assert rows == [(1, "a"), (2, "b")]
        conn.prepare.assert_awaited_once_with("SELECT * FROM t")
        pool.acquire.assert_called_once_with(timeout=client.pool_acquire_timeout)

    @pytest.mark.asyncio
    async def test_duplicate_column_names_preserve_positional_values(self):
        statement = _make_statement_mock(
            attributes=[_make_attribute("a"), _make_attribute("a")],
            rows=[_FakeRecordDuplicateColumns((1, 2))],
        )
        client, _, _ = self._make_pooled_client(statement=statement)

        columns, rows = await client.execute_query_raw("SELECT a, a FROM t")

        assert columns == ["a", "a"]
        assert rows == [(1, 2)]

    @pytest.mark.asyncio
    async def test_no_description_returns_empty(self):
        statement = _make_statement_mock(attributes=[])
        client, _, conn = self._make_pooled_client(statement=statement)

        columns, rows = await client.execute_query_raw("INSERT INTO t VALUES (1)")

        assert columns == []
        assert rows == []
        conn.execute.assert_awaited_once_with("INSERT INTO t VALUES (1)")

    @pytest.mark.asyncio
    async def test_with_params(self):
        statement = _make_statement_mock(
            attributes=[_make_attribute("id")],
            rows=[_FakeRecord({"id": 1})],
        )
        client, _, _ = self._make_pooled_client(statement=statement)

        await client.execute_query_raw("SELECT * FROM t WHERE id = $1", (1,))
        statement.fetch.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_no_params(self):
        statement = _make_statement_mock(attributes=[])
        client, _, conn = self._make_pooled_client(statement=statement)

        await client.execute_query_raw("SELECT 1")
        conn.execute.assert_awaited_once_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_auto_connects(self, asyncpg_module):
        mock_pool = _make_pool_mock()
        statement = _make_statement_mock(attributes=[])
        mock_conn = MagicMock()
        mock_conn.prepare = AsyncMock(return_value=statement)
        mock_conn.execute = AsyncMock(return_value="SELECT 0")
        mock_pool.acquire.return_value = _make_async_connection_context(mock_conn)
        asyncpg_module.create_pool.return_value = mock_pool

        with patch.object(_pg_mod, "asyncpg", asyncpg_module):
            client = PostgreSQLClient(
                host="localhost", database="db", user="u", password="p"
            )
            await client.execute_query_raw("SELECT 1")

        asyncpg_module.create_pool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_raises(self):
        statement = _make_statement_mock(attributes=[_make_attribute("id")])
        statement.fetch.side_effect = Exception("bad query")
        client, _, _ = self._make_pooled_client(statement=statement)

        with pytest.raises(RuntimeError, match="Query execution failed"):
            await client.execute_query_raw("BAD SQL")


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
        assert cfg.min_pool_size == 1
        assert cfg.max_pool_size == 10

    def test_all_values(self):
        cfg = PostgreSQLConfig(
            host="h", database="d", user="u", password="p",
            port=5433, timeout=60, sslmode="require",
            min_pool_size=2, max_pool_size=20, pool_acquire_timeout=15.0,
        )
        assert cfg.port == 5433
        assert cfg.timeout == 60
        assert cfg.sslmode == "require"
        assert cfg.min_pool_size == 2
        assert cfg.max_pool_size == 20
        assert cfg.pool_acquire_timeout == 15.0

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
        assert client.min_pool_size == 1
        assert client.max_pool_size == 10


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
