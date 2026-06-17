"""Unit tests for MariaDB client module."""

import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We must install a mock 'mariadb' module into sys.modules BEFORE importing
# the source module, because the source does `import mariadb` at module level
# and sets it to None if the import fails.
_mock_mariadb_module = MagicMock()
_mock_mariadb_module.Error = type("MariaDBError", (Exception,), {})
sys.modules["mariadb"] = _mock_mariadb_module

from app.sources.client.mariadb.mariadb import (  # noqa: E402
    AuthConfig,
    MariaDBClient,
    MariaDBClientBuilder,
    MariaDBConfig,
    MariaDBConnectorConfig,
    MariaDBResponse,
)

import app.sources.client.mariadb.mariadb as _mariadb_mod  # noqa: E402

_mariadb_mod.mariadb = _mock_mariadb_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool_mock():
    """Build a mock mariadb.ConnectionPool."""
    return MagicMock()


@pytest.fixture
def logger():
    return logging.getLogger("test_mariadb_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def mariadb_module():
    """Return the mock mariadb module and reset state between tests."""
    _mock_mariadb_module.reset_mock()
    _mock_mariadb_module.Error = type("MariaDBError", (Exception,), {})
    _mock_mariadb_module.ConnectionPool.side_effect = None
    _mock_mariadb_module.ConnectionPool.return_value = _make_pool_mock()
    return _mock_mariadb_module


# ---------------------------------------------------------------------------
# MariaDBResponse
# ---------------------------------------------------------------------------


class TestMariaDBResponse:
    def test_success(self):
        resp = MariaDBResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_to_dict(self):
        resp = MariaDBResponse(success=True, data={"key": "val"})
        d = resp.to_dict()
        assert d["success"] is True
        assert "data" in d

    def test_to_json(self):
        resp = MariaDBResponse(success=False, error="oops")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is False

    def test_defaults(self):
        resp = MariaDBResponse(success=True)
        assert resp.data is None
        assert resp.error is None


# ---------------------------------------------------------------------------
# MariaDBClient
# ---------------------------------------------------------------------------


class TestMariaDBClient:
    def test_init(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        assert client.host == "localhost"
        assert client.user == "root"
        assert client.port == 3306
        assert client.database is None
        assert client.charset == "utf8mb4"
        assert client._pool is None
        assert client._max_pool_size == 10

    def test_init_with_all_options(self):
        client = MariaDBClient(
            host="db.local", user="u", password="p",
            database="mydb", port=3307, timeout=60,
            ssl_ca="/ca.pem", charset="utf8",
            pool_acquire_timeout=12.0,
        )
        assert client.database == "mydb"
        assert client.port == 3307
        assert client.ssl_ca == "/ca.pem"
        assert client.pool_acquire_timeout == 12.0

    def test_init_invalid_acquire_timeout(self):
        with pytest.raises(ValueError, match="pool_acquire_timeout"):
            MariaDBClient(
                host="h", user="u", password="p", pool_acquire_timeout=0
            )

    def test_connect_already_connected(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        client._pool = _make_pool_mock()
        result = client.connect()
        assert result is client

    def test_connect_success(self, mariadb_module):
        mock_pool = _make_pool_mock()
        mariadb_module.ConnectionPool.return_value = mock_pool
        client = MariaDBClient(host="localhost", user="root", password="pass")
        result = client.connect()
        assert result is client
        assert client._pool is mock_pool

    def test_connect_with_database(self, mariadb_module):
        mock_pool = _make_pool_mock()
        mariadb_module.ConnectionPool.return_value = mock_pool
        client = MariaDBClient(host="localhost", user="root", password="pass", database="mydb")
        client.connect()
        call_kwargs = mariadb_module.ConnectionPool.call_args[1]
        assert call_kwargs["database"] == "mydb"

    def test_connect_with_ssl_ca(self, mariadb_module):
        mock_pool = _make_pool_mock()
        mariadb_module.ConnectionPool.return_value = mock_pool
        client = MariaDBClient(host="localhost", user="root", password="pass", ssl_ca="/ca.pem")
        client.connect()
        call_kwargs = mariadb_module.ConnectionPool.call_args[1]
        assert call_kwargs["ssl_ca"] == "/ca.pem"

    def test_connect_failure(self, mariadb_module):
        mariadb_module.ConnectionPool.side_effect = mariadb_module.Error(
            "connection refused"
        )
        client = MariaDBClient(host="localhost", user="root", password="pass")
        with pytest.raises(ConnectionError, match="Failed to connect"):
            client.connect()

    def test_close(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_pool = _make_pool_mock()
        client._pool = mock_pool
        client.close()
        mock_pool.close.assert_called_once()
        assert client._pool is None

    def test_close_no_pool(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        client.close()  # Should not raise

    def test_close_error_handled(self, mariadb_module):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_pool = _make_pool_mock()
        mock_pool.close.side_effect = mariadb_module.Error("close fail")
        client._pool = mock_pool
        client.close()
        assert client._pool is None

    def test_is_connected_true(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        client._pool = _make_pool_mock()
        assert client.is_connected() is True

    def test_is_connected_false_no_pool(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        assert client.is_connected() is False

    def test_resize_pool_updates_size(self):
        client = MariaDBClient(host="h", user="u", password="p")
        result = client.resize_pool(max_size=1)
        assert result is client
        assert client._max_pool_size == 1

    def test_resize_pool_after_connect_raises(self):
        client = MariaDBClient(host="h", user="u", password="p")
        client._pool = _make_pool_mock()
        with pytest.raises(RuntimeError, match="Cannot resize"):
            client.resize_pool(max_size=1)

    def test_resize_pool_invalid_size(self):
        client = MariaDBClient(host="h", user="u", password="p")
        with pytest.raises(ValueError, match="max_size"):
            client.resize_pool(max_size=0)

    def _make_pooled_client(self, cursor):
        """Build a client whose pool checks out a connection wired to ``cursor``."""
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_pool = _make_pool_mock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = cursor
        mock_pool.get_connection.return_value = mock_conn
        client._pool = mock_pool
        return client, mock_pool, mock_conn

    def test_execute_query_with_results(self):
        cursor = MagicMock()
        cursor.description = [("id",), ("name",)]
        cursor.fetchall.return_value = [{"id": 1, "name": "test"}]
        client, _, conn = self._make_pooled_client(cursor)

        results = client.execute_query("SELECT * FROM t")
        assert results == [{"id": 1, "name": "test"}]
        conn.commit.assert_called_once()

    def test_execute_query_no_results(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.rowcount = 5
        client, _, _ = self._make_pooled_client(cursor)

        results = client.execute_query("DELETE FROM t")
        assert results == [{"affected_rows": 5}]

    def test_execute_query_error(self, mariadb_module):
        cursor = MagicMock()
        cursor.execute.side_effect = mariadb_module.Error("query fail")
        client, _, conn = self._make_pooled_client(cursor)

        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query("BAD SQL")
        conn.rollback.assert_called_once()

    def test_execute_query_auto_connect(self, mariadb_module):
        """Should auto-connect if pool not initialized."""
        mock_pool = _make_pool_mock()
        cursor = MagicMock()
        cursor.description = None
        cursor.rowcount = 0
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = cursor
        mock_pool.get_connection.return_value = mock_conn
        mariadb_module.ConnectionPool.return_value = mock_pool

        client = MariaDBClient(host="localhost", user="root", password="pass")
        assert client._pool is None

        results = client.execute_query("SELECT 1")
        assert results == [{"affected_rows": 0}]
        mariadb_module.ConnectionPool.assert_called_once()

    def test_execute_query_raw_with_results(self):
        cursor = MagicMock()
        cursor.description = [("id",), ("name",)]
        cursor.fetchall.return_value = [(1, "test")]
        client, _, _ = self._make_pooled_client(cursor)

        columns, rows = client.execute_query_raw("SELECT * FROM t")
        assert columns == ["id", "name"]
        assert rows == [(1, "test")]

    def test_execute_query_raw_no_results(self):
        cursor = MagicMock()
        cursor.description = None
        client, _, _ = self._make_pooled_client(cursor)

        columns, rows = client.execute_query_raw("INSERT INTO t VALUES (1)")
        assert columns == []
        assert rows == []

    def test_execute_query_raw_error(self, mariadb_module):
        cursor = MagicMock()
        cursor.execute.side_effect = mariadb_module.Error("fail")
        client, _, _ = self._make_pooled_client(cursor)

        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query_raw("BAD SQL")

    def test_get_connection_info(self):
        client = MariaDBClient(host="localhost", user="root", password="pass", database="mydb")
        info = client.get_connection_info()
        assert info["host"] == "localhost"
        assert info["database"] == "mydb"
        assert info["user"] == "root"
        assert "password" not in info

    def test_context_manager(self, mariadb_module):
        mock_pool = _make_pool_mock()
        mariadb_module.ConnectionPool.side_effect = None
        mariadb_module.ConnectionPool.return_value = mock_pool

        client = MariaDBClient(host="localhost", user="root", password="pass")

        with client as c:
            assert c is client
        # close() was called on the pool
        mock_pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# MariaDBConfig
# ---------------------------------------------------------------------------


class TestMariaDBConfig:
    def test_create_client(self):
        cfg = MariaDBConfig(host="localhost", user="root", password="pass")
        client = cfg.create_client()
        assert isinstance(client, MariaDBClient)
        assert client.host == "localhost"

    def test_defaults(self):
        cfg = MariaDBConfig(host="localhost", user="root")
        assert cfg.port == 3306
        assert cfg.password == ""
        assert cfg.timeout == 30
        assert cfg.charset == "utf8mb4"

    def test_all_options(self):
        cfg = MariaDBConfig(
            host="db.local", user="u", password="p",
            port=3307, database="mydb", timeout=60,
            ssl_ca="/ca.pem", charset="utf8",
            pool_acquire_timeout=20.0,
        )
        client = cfg.create_client()
        assert client.port == 3307
        assert client.database == "mydb"
        assert client.pool_acquire_timeout == 20.0


# ---------------------------------------------------------------------------
# MariaDBConnectorConfig / AuthConfig
# ---------------------------------------------------------------------------


class TestMariaDBConnectorConfig:
    def test_from_dict(self):
        cfg = MariaDBConnectorConfig.model_validate({
            "auth": {"host": "localhost", "port": 3306, "user": "root", "password": "pass"},
            "timeout": 30,
        })
        assert cfg.auth.host == "localhost"
        assert cfg.timeout == 30

    def test_defaults(self):
        cfg = MariaDBConnectorConfig.model_validate({
            "auth": {"host": "localhost", "user": "root"},
        })
        assert cfg.auth.port == 3306
        assert cfg.timeout == 30


# ---------------------------------------------------------------------------
# MariaDBClientBuilder
# ---------------------------------------------------------------------------


class TestMariaDBClientBuilder:
    def test_init(self):
        mock_client = MagicMock()
        builder = MariaDBClientBuilder(mock_client)
        assert builder.get_client() is mock_client

    def test_get_connection_info(self):
        mock_client = MagicMock()
        mock_client.get_connection_info.return_value = {"host": "localhost"}
        builder = MariaDBClientBuilder(mock_client)
        assert builder.get_connection_info() == {"host": "localhost"}

    def test_build_with_config(self):
        cfg = MariaDBConfig(host="localhost", user="root", password="pass")
        builder = MariaDBClientBuilder.build_with_config(cfg)
        assert isinstance(builder, MariaDBClientBuilder)

    @pytest.mark.asyncio
    async def test_build_from_services_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"host": "localhost", "port": 3306, "user": "root", "password": "pass"},
                "timeout": 30,
            }
        )
        builder = await MariaDBClientBuilder.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(builder, MariaDBClientBuilder)

    @pytest.mark.asyncio
    async def test_build_from_services_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value="not a dict")
        with pytest.raises(ValueError):
            await MariaDBClientBuilder.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_validation_error(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"port": "not_a_number"}}  # Missing required fields
        )
        with pytest.raises(ValueError):
            await MariaDBClientBuilder.build_from_services(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await MariaDBClientBuilder._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_not_dict_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value="string config")
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_instance_id(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await MariaDBClientBuilder._get_connector_config(logger, mock_config_service, None)
        # Should not include "for instance" when no ID
        assert "Failed to get MariaDB" in str(exc_info.value)


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_missing_instance_id_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="instanceId"):
            await MariaDBClient.build_from_toolset({}, logger, mock_config_service)

    @pytest.mark.asyncio
    async def test_success(self, logger, mock_config_service):
        with patch(
            "app.sources.client.mariadb.mariadb.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"host": "localhost", "port": 3306}},
        ):
            result = await MariaDBClient.build_from_toolset(
                {"instanceId": "inst-1", "auth": {"username": "root", "password": "pass"}},
                logger,
                mock_config_service,
            )
            assert isinstance(result, MariaDBClient)

    @pytest.mark.asyncio
    async def test_missing_host_raises(self, logger, mock_config_service):
        with patch(
            "app.sources.client.mariadb.mariadb.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {}},
        ):
            with pytest.raises(ValueError, match="host"):
                await MariaDBClient.build_from_toolset(
                    {"instanceId": "inst-1", "auth": {"username": "root"}},
                    logger,
                    mock_config_service,
                )

    @pytest.mark.asyncio
    async def test_missing_username_raises(self, logger, mock_config_service):
        with patch(
            "app.sources.client.mariadb.mariadb.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"host": "localhost"}},
        ):
            with pytest.raises(ValueError, match="username"):
                await MariaDBClient.build_from_toolset(
                    {"instanceId": "inst-1", "auth": {}},
                    logger,
                    mock_config_service,
                )

    @pytest.mark.asyncio
    async def test_password_none_defaults_empty(self, logger, mock_config_service):
        with patch(
            "app.sources.client.mariadb.mariadb.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"host": "localhost"}},
        ):
            result = await MariaDBClient.build_from_toolset(
                {"instanceId": "inst-1", "auth": {"username": "root"}},
                logger,
                mock_config_service,
            )
            assert result.password == ""
