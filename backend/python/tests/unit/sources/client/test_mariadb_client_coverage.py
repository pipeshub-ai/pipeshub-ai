"""Comprehensive unit tests for app.sources.client.mariadb.mariadb."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.mariadb.mariadb import (
    AuthConfig,
    MariaDBClient,
    MariaDBClientBuilder,
    MariaDBConfig,
    MariaDBConnectorConfig,
    MariaDBResponse,
)


@pytest.fixture
def log():
    lg = logging.getLogger("test_mariadb")
    lg.setLevel(logging.CRITICAL)
    return lg


def _pool_mock():
    return MagicMock()


# ============================================================================
# MariaDBConfig
# ============================================================================
class TestMariaDBConfig:
    def test_defaults(self):
        config = MariaDBConfig(host="localhost", user="root")
        assert config.port == 3306
        assert config.database is None
        assert config.password == ""
        assert config.timeout == 30
        assert config.charset == "utf8mb4"
        assert config.pool_size == 5

    def test_custom(self):
        config = MariaDBConfig(
            host="db.example.com", port=3307, database="mydb",
            user="admin", password="secret", timeout=60,
            ssl_ca="/path/to/ca.pem", charset="utf8",
            pool_size=8, pool_acquire_timeout=12.0,
        )
        assert config.host == "db.example.com"
        assert config.port == 3307
        assert config.database == "mydb"
        assert config.pool_size == 8
        assert config.pool_acquire_timeout == 12.0

    def test_create_client(self):
        config = MariaDBConfig(host="localhost", user="root")
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.__bool__ = MagicMock(return_value=True)
            client = config.create_client()
            assert isinstance(client, MariaDBClient)

    def test_port_validation(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MariaDBConfig(host="localhost", user="root", port=0)
        with pytest.raises(ValidationError):
            MariaDBConfig(host="localhost", user="root", port=70000)


# ============================================================================
# AuthConfig
# ============================================================================
class TestAuthConfig:
    def test_defaults(self):
        config = AuthConfig(host="localhost", user="root")
        assert config.port == 3306
        assert config.database is None
        assert config.password == ""

    def test_full(self):
        config = AuthConfig(
            host="db.com", port=3307, database="db", user="u", password="p",
            ssl_ca="/ca.pem"
        )
        assert config.ssl_ca == "/ca.pem"


# ============================================================================
# MariaDBConnectorConfig
# ============================================================================
class TestMariaDBConnectorConfig:
    def test_defaults(self):
        auth = AuthConfig(host="localhost", user="root")
        config = MariaDBConnectorConfig(auth=auth)
        assert config.timeout == 30


# ============================================================================
# MariaDBResponse
# ============================================================================
class TestMariaDBResponse:
    def test_success(self):
        resp = MariaDBResponse(success=True, data={"key": "val"})
        assert resp.success is True
        d = resp.to_dict()
        assert d["success"] is True
        assert "error" not in d  # excluded when None

    def test_error(self):
        resp = MariaDBResponse(success=False, error="Something failed")
        assert resp.error == "Something failed"

    def test_to_json(self):
        resp = MariaDBResponse(success=True, message="OK")
        j = resp.to_json()
        assert '"success":true' in j
        assert '"message":"OK"' in j


# ============================================================================
# MariaDBClient
# ============================================================================
class TestMariaDBClient:
    def test_init_no_mariadb(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb", None):
            with pytest.raises(ImportError, match="mariadb is required"):
                MariaDBClient(host="localhost", user="root", password="")

    def test_init_success(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="pass", database="mydb")
            assert client.host == "localhost"
            assert client.database == "mydb"
            assert client._pool is None

    def test_init_no_database(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            assert client.database is None

    def test_connect_already_connected(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            client._pool = _pool_mock()
            result = client.connect()
            assert result is client

    def test_connect_success(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_pool = _pool_mock()
            mock_mod.ConnectionPool.return_value = mock_pool
            client = MariaDBClient(host="localhost", user="root", password="pass")
            result = client.connect()
            assert result is client
            assert client._pool is mock_pool

    def test_connect_with_database_and_ssl(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_pool = _pool_mock()
            mock_mod.ConnectionPool.return_value = mock_pool
            client = MariaDBClient(
                host="localhost", user="root", password="pass",
                database="mydb", ssl_ca="/path/ca.pem"
            )
            client.connect()
            call_kwargs = mock_mod.ConnectionPool.call_args.kwargs
            assert call_kwargs["database"] == "mydb"
            assert call_kwargs["ssl_ca"] == "/path/ca.pem"

    def test_connect_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            mock_mod.ConnectionPool.side_effect = Exception("Connection refused")
            client = MariaDBClient(host="localhost", user="root", password="")
            with pytest.raises(ConnectionError, match="Failed to connect"):
                client.connect()

    def test_close_with_pool(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_pool = _pool_mock()
            client._pool = mock_pool
            client.close()
            mock_pool.close.assert_called_once()
            assert client._pool is None

    def test_close_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_pool = _pool_mock()
            mock_pool.close.side_effect = Exception("Close failed")
            client._pool = mock_pool
            client.close()  # Should not raise
            assert client._pool is None

    def test_close_no_pool(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            client.close()  # Should not raise

    def test_is_connected_true(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            client._pool = _pool_mock()
            assert client.is_connected() is True

    def test_is_connected_false_no_pool(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            assert client.is_connected() is False

    def _make_pooled_client(self, mock_mod, cursor):
        mock_mod.Error = Exception
        client = MariaDBClient(host="localhost", user="root", password="")
        mock_pool = _pool_mock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = cursor
        mock_pool.get_connection.return_value = mock_conn
        client._pool = mock_pool
        return client, mock_pool, mock_conn

    def test_execute_query_with_results(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            cursor = MagicMock()
            cursor.description = [("col1",), ("col2",)]
            cursor.fetchall.return_value = [{"col1": "v1", "col2": "v2"}]
            client, _, conn = self._make_pooled_client(mock_mod, cursor)

            results = client.execute_query("SELECT * FROM t")
            assert len(results) == 1
            conn.commit.assert_called()

    def test_execute_query_no_description(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            cursor = MagicMock()
            cursor.description = None
            cursor.rowcount = 5
            client, _, _ = self._make_pooled_client(mock_mod, cursor)

            results = client.execute_query("INSERT INTO t VALUES (1)")
            assert results == [{"affected_rows": 5}]

    def test_execute_query_auto_connects(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            mock_pool = _pool_mock()
            cursor = MagicMock()
            cursor.description = [("col1",)]
            cursor.fetchall.return_value = [{"col1": "val"}]
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = cursor
            mock_pool.get_connection.return_value = mock_conn
            mock_mod.ConnectionPool.return_value = mock_pool

            client = MariaDBClient(host="localhost", user="root", password="")
            assert client._pool is None
            client.execute_query("SELECT 1")
            mock_mod.ConnectionPool.assert_called_once()

    def test_execute_query_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            cursor = MagicMock()
            cursor.execute.side_effect = Exception("Query error")
            client, _, conn = self._make_pooled_client(mock_mod, cursor)

            with pytest.raises(RuntimeError, match="Query execution failed"):
                client.execute_query("BAD QUERY")
            conn.rollback.assert_called()

    def test_execute_query_raw_with_results(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            cursor = MagicMock()
            cursor.description = [("col1",), ("col2",)]
            cursor.fetchall.return_value = [("v1", "v2")]
            client, _, _ = self._make_pooled_client(mock_mod, cursor)

            columns, rows = client.execute_query_raw("SELECT * FROM t")
            assert columns == ["col1", "col2"]
            assert len(rows) == 1

    def test_execute_query_raw_no_description(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            cursor = MagicMock()
            cursor.description = None
            client, _, _ = self._make_pooled_client(mock_mod, cursor)

            columns, rows = client.execute_query_raw("INSERT INTO t VALUES (1)")
            assert columns == []
            assert rows == []

    def test_execute_query_raw_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            cursor = MagicMock()
            cursor.execute.side_effect = Exception("error")
            client, _, _ = self._make_pooled_client(mock_mod, cursor)

            with pytest.raises(RuntimeError, match="Query execution failed"):
                client.execute_query_raw("BAD QUERY")

    def test_get_connection_info(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(
                host="db.com", port=3307, database="mydb",
                user="admin", password="pass"
            )
            info = client.get_connection_info()
            assert info["host"] == "db.com"
            assert info["port"] == 3307
            assert info["database"] == "mydb"
            assert info["user"] == "admin"

    def test_context_manager(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            mock_pool = _pool_mock()
            mock_mod.ConnectionPool.return_value = mock_pool
            client = MariaDBClient(host="localhost", user="root", password="")

            with client as c:
                assert c is client
            mock_pool.close.assert_called_once()


# ============================================================================
# MariaDBClientBuilder
# ============================================================================
class TestMariaDBClientBuilder:
    def test_init_and_get_client(self):
        mock_client = MagicMock(spec=MariaDBClient)
        builder = MariaDBClientBuilder(mock_client)
        assert builder.get_client() is mock_client

    def test_get_connection_info(self):
        mock_client = MagicMock(spec=MariaDBClient)
        mock_client.get_connection_info.return_value = {"host": "localhost"}
        builder = MariaDBClientBuilder(mock_client)
        assert builder.get_connection_info() == {"host": "localhost"}

    def test_build_with_config(self):
        config = MariaDBConfig(host="localhost", user="root")
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            builder = MariaDBClientBuilder.build_with_config(config)
            assert isinstance(builder, MariaDBClientBuilder)

    @pytest.mark.asyncio
    async def test_build_from_services(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "auth": {"host": "localhost", "port": 3306, "user": "root", "password": "pass"},
            "timeout": 30,
        })
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            builder = await MariaDBClientBuilder.build_from_services(log, cs, "inst1")
            assert isinstance(builder, MariaDBClientBuilder)

    @pytest.mark.asyncio
    async def test_build_from_services_validation_error(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"auth": {"host": ""}})
        with pytest.raises(ValueError, match="Invalid MariaDB"):
            await MariaDBClientBuilder.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder.build_from_services(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_build_from_services_not_dict(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="not a dict")
        with pytest.raises(ValueError, match="MariaDB"):
            await MariaDBClientBuilder.build_from_services(log, cs, "inst1")


class TestMariaDBGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_success(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"auth": {}})
        result = await MariaDBClientBuilder._get_connector_config(log, cs, "inst1")
        assert "auth" in result

    @pytest.mark.asyncio
    async def test_none(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder._get_connector_config(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_not_dict(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="string_value")
        # The "Invalid" ValueError is caught by the outer except and re-raised as "Failed to get"
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder._get_connector_config(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_exception(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("etcd down"))
        with pytest.raises(ValueError, match="Failed to get MariaDB"):
            await MariaDBClientBuilder._get_connector_config(log, cs, "inst1")

    @pytest.mark.asyncio
    async def test_no_instance_id(self, log):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await MariaDBClientBuilder._get_connector_config(log, cs, None)


# ============================================================================
# MariaDBClient.build_from_toolset
# ============================================================================
class TestMariaDBBuildFromToolset:
    @pytest.mark.asyncio
    async def test_success(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.mariadb.mariadb.get_toolset_by_id", return_value={
            "auth": {"host": "db.com", "port": 3306, "database": "mydb"},
        }):
            config = {
                "instanceId": "inst1",
                "auth": {"username": "user", "password": "pass"},
            }
            with patch("app.sources.client.mariadb.mariadb.mariadb"):
                client = await MariaDBClient.build_from_toolset(config, log, cs)
                assert isinstance(client, MariaDBClient)

    @pytest.mark.asyncio
    async def test_no_instance_id(self, log):
        cs = AsyncMock()
        with pytest.raises(ValueError, match="instanceId"):
            await MariaDBClient.build_from_toolset({}, log, cs)

    @pytest.mark.asyncio
    async def test_no_host(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.mariadb.mariadb.get_toolset_by_id", return_value={
            "auth": {"port": 3306},
        }):
            config = {"instanceId": "inst1", "auth": {"username": "user"}}
            with pytest.raises(ValueError, match="host"):
                await MariaDBClient.build_from_toolset(config, log, cs)

    @pytest.mark.asyncio
    async def test_no_username(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.mariadb.mariadb.get_toolset_by_id", return_value={
            "auth": {"host": "db.com"},
        }):
            config = {"instanceId": "inst1", "auth": {}}
            with pytest.raises(ValueError, match="username"):
                await MariaDBClient.build_from_toolset(config, log, cs)

    @pytest.mark.asyncio
    async def test_empty_toolset_config(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.mariadb.mariadb.get_toolset_by_id", return_value={
            "auth": {"host": "db.com"},
        }):
            # toolset_config is falsy after get_toolset_by_id succeeds but
            # the empty-check happens after pick_value
            config = {"instanceId": "inst1"}
            with pytest.raises(ValueError, match="username"):
                await MariaDBClient.build_from_toolset(config, log, cs)

    @pytest.mark.asyncio
    async def test_password_none_defaults_empty(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.mariadb.mariadb.get_toolset_by_id", return_value={
            "auth": {"host": "db.com", "port": 3306},
        }):
            config = {
                "instanceId": "inst1",
                "auth": {"username": "user"},
                # no password key
            }
            with patch("app.sources.client.mariadb.mariadb.mariadb"):
                client = await MariaDBClient.build_from_toolset(config, log, cs)
                assert client.password == ""

    @pytest.mark.asyncio
    async def test_pick_value_from_top_level(self, log):
        cs = AsyncMock()
        with patch("app.sources.client.mariadb.mariadb.get_toolset_by_id", return_value={
            "host": "db.com",  # top-level, not in auth
            "port": 3307,
        }):
            config = {
                "instanceId": "inst1",
                "username": "user",  # top-level
                "password": "pass",
            }
            with patch("app.sources.client.mariadb.mariadb.mariadb"):
                client = await MariaDBClient.build_from_toolset(config, log, cs)
                assert client.host == "db.com"
                assert client.user == "user"
