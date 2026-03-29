"""Comprehensive unit tests for app.sources.client.mariadb.mariadb."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

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


@pytest.fixture
def mock_mariadb_module():
    """Create a mock mariadb module for testing."""
    mock_mod = MagicMock()
    mock_mod.Error = Exception
    return mock_mod


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

    def test_custom(self):
        config = MariaDBConfig(
            host="db.example.com", port=3307, database="mydb",
            user="admin", password="secret", timeout=60,
            ssl_ca="/path/to/ca.pem", charset="utf8"
        )
        assert config.host == "db.example.com"
        assert config.port == 3307
        assert config.database == "mydb"

    def test_create_client(self):
        config = MariaDBConfig(host="localhost", user="root")
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.__bool__ = MagicMock(return_value=True)
            # mariadb is not None check
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
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            client = MariaDBClient(host="localhost", user="root", password="pass", database="mydb")
            assert client.host == "localhost"
            assert client.database == "mydb"
            assert client._connection is None

    def test_init_no_database(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            assert client.database is None

    def test_connect_already_connected(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            client._connection = MagicMock()  # Simulate existing connection
            result = client.connect()
            assert result is client  # Returns self

    def test_connect_success(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_conn = MagicMock()
            mock_mod.connect.return_value = mock_conn
            client = MariaDBClient(host="localhost", user="root", password="pass")
            result = client.connect()
            assert result is client
            assert client._connection is mock_conn

    def test_connect_with_database_and_ssl(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_conn = MagicMock()
            mock_mod.connect.return_value = mock_conn
            client = MariaDBClient(
                host="localhost", user="root", password="pass",
                database="mydb", ssl_ca="/path/ca.pem"
            )
            client.connect()
            call_kwargs = mock_mod.connect.call_args.kwargs
            assert call_kwargs["database"] == "mydb"
            assert call_kwargs["ssl_ca"] == "/path/ca.pem"

    def test_connect_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            mock_mod.connect.side_effect = Exception("Connection refused")
            client = MariaDBClient(host="localhost", user="root", password="")
            with pytest.raises(ConnectionError, match="Failed to connect"):
                client.connect()

    def test_close_with_connection(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            client._connection = mock_conn
            client.close()
            mock_conn.close.assert_called_once()
            assert client._connection is None

    def test_close_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_conn.close.side_effect = Exception("Close failed")
            client._connection = mock_conn
            client.close()  # Should not raise
            assert client._connection is None

    def test_close_no_connection(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            client.close()  # Should not raise

    def test_is_connected_true(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            client._connection = mock_conn
            assert client.is_connected() is True

    def test_is_connected_false_no_connection(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb"):
            client = MariaDBClient(host="localhost", user="root", password="")
            assert client.is_connected() is False

    def test_is_connected_false_ping_fails(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_conn.ping.side_effect = Exception("Ping failed")
            client._connection = mock_conn
            assert client.is_connected() is False

    def test_execute_query_with_results(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.description = [("col1",), ("col2",)]
            mock_cursor.fetchall.return_value = [{"col1": "v1", "col2": "v2"}]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ping.return_value = None
            client._connection = mock_conn

            results = client.execute_query("SELECT * FROM t")
            assert len(results) == 1
            mock_conn.commit.assert_called()

    def test_execute_query_no_description(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.description = None
            mock_cursor.rowcount = 5
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ping.return_value = None
            client._connection = mock_conn

            results = client.execute_query("INSERT INTO t VALUES (1)")
            assert results == [{"affected_rows": 5}]

    def test_execute_query_reconnects(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            mock_conn = MagicMock()
            mock_mod.connect.return_value = mock_conn
            mock_cursor = MagicMock()
            mock_cursor.description = [("col1",)]
            mock_cursor.fetchall.return_value = [{"col1": "val"}]
            mock_conn.cursor.return_value = mock_cursor
            # First call: not connected
            mock_conn.ping.side_effect = [Exception("stale"), None]

            client = MariaDBClient(host="localhost", user="root", password="")
            client._connection = mock_conn
            # is_connected returns False, then connect is called
            results = client.execute_query("SELECT 1")

    def test_execute_query_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.execute.side_effect = Exception("Query error")
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ping.return_value = None
            client._connection = mock_conn

            with pytest.raises(RuntimeError, match="Query execution failed"):
                client.execute_query("BAD QUERY")
            mock_conn.rollback.assert_called()

    def test_execute_query_raw_with_results(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.description = [("col1",), ("col2",)]
            mock_cursor.fetchall.return_value = [("v1", "v2")]
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ping.return_value = None
            client._connection = mock_conn

            columns, rows = client.execute_query_raw("SELECT * FROM t")
            assert columns == ["col1", "col2"]
            assert len(rows) == 1

    def test_execute_query_raw_no_description(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.description = None
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ping.return_value = None
            client._connection = mock_conn

            columns, rows = client.execute_query_raw("INSERT INTO t VALUES (1)")
            assert columns == []
            assert rows == []

    def test_execute_query_raw_error(self):
        with patch("app.sources.client.mariadb.mariadb.mariadb") as mock_mod:
            mock_mod.Error = Exception
            client = MariaDBClient(host="localhost", user="root", password="")
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.execute.side_effect = Exception("error")
            mock_conn.cursor.return_value = mock_cursor
            mock_conn.ping.return_value = None
            client._connection = mock_conn

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
            mock_conn = MagicMock()
            mock_mod.connect.return_value = mock_conn
            client = MariaDBClient(host="localhost", user="root", password="")

            with client as c:
                assert c is client
            mock_conn.close.assert_called_once()


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
