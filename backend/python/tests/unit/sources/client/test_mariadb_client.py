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

# Patch the module-level 'mariadb' reference in the source module
import app.sources.client.mariadb.mariadb as _mariadb_mod  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from app.sources.client.mariadb.mariadb import (
    AuthConfig,
    MariaDBClient,
    MariaDBClientBuilder,
    MariaDBConfig,
    MariaDBConnectorConfig,
    MariaDBResponse,
)

_mariadb_mod.mariadb = _mock_mariadb_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        assert client._connection is None

    def test_init_with_all_options(self):
        client = MariaDBClient(
            host="db.local", user="u", password="p",
            database="mydb", port=3307, timeout=60,
            ssl_ca="/ca.pem", charset="utf8"
        )
        assert client.database == "mydb"
        assert client.port == 3307
        assert client.ssl_ca == "/ca.pem"

    def test_connect_already_connected(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        client._connection = MagicMock()
        result = client.connect()
        assert result is client

    def test_connect_success(self, mariadb_module):
        mock_conn = MagicMock()
        mariadb_module.connect.return_value = mock_conn
        client = MariaDBClient(host="localhost", user="root", password="pass")
        result = client.connect()
        assert result is client
        assert client._connection is mock_conn

    def test_connect_with_database(self, mariadb_module):
        mock_conn = MagicMock()
        mariadb_module.connect.return_value = mock_conn
        client = MariaDBClient(host="localhost", user="root", password="pass", database="mydb")
        client.connect()
        call_kwargs = mariadb_module.connect.call_args[1]
        assert call_kwargs["database"] == "mydb"

    def test_connect_with_ssl_ca(self, mariadb_module):
        mock_conn = MagicMock()
        mariadb_module.connect.return_value = mock_conn
        client = MariaDBClient(host="localhost", user="root", password="pass", ssl_ca="/ca.pem")
        client.connect()
        call_kwargs = mariadb_module.connect.call_args[1]
        assert call_kwargs["ssl_ca"] == "/ca.pem"

    def test_connect_failure(self, mariadb_module):
        mariadb_module.connect.side_effect = mariadb_module.Error("connection refused")
        client = MariaDBClient(host="localhost", user="root", password="pass")
        with pytest.raises(ConnectionError, match="Failed to connect"):
            client.connect()

    def test_close(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        client._connection = mock_conn
        client.close()
        mock_conn.close.assert_called_once()
        assert client._connection is None

    def test_close_no_connection(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        client.close()  # Should not raise

    def test_close_error_handled(self, mariadb_module):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_conn.close.side_effect = mariadb_module.Error("close fail")
        client._connection = mock_conn
        client.close()
        assert client._connection is None

    def test_is_connected_true(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        client._connection = mock_conn
        assert client.is_connected() is True

    def test_is_connected_false_no_connection(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        assert client.is_connected() is False

    def test_is_connected_false_ping_fails(self, mariadb_module):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_conn.ping.side_effect = mariadb_module.Error("ping fail")
        client._connection = mock_conn
        assert client.is_connected() is False

    def test_execute_query_with_results(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [{"id": 1, "name": "test"}]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.ping.return_value = None
        client._connection = mock_conn

        results = client.execute_query("SELECT * FROM t")
        assert results == [{"id": 1, "name": "test"}]
        mock_conn.commit.assert_called_once()

    def test_execute_query_no_results(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_cursor.rowcount = 5
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.ping.return_value = None
        client._connection = mock_conn

        results = client.execute_query("DELETE FROM t")
        assert results == [{"affected_rows": 5}]

    def test_execute_query_error(self, mariadb_module):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = mariadb_module.Error("query fail")
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.ping.return_value = None
        client._connection = mock_conn

        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query("BAD SQL")
        mock_conn.rollback.assert_called_once()

    def test_execute_query_auto_connect(self, mariadb_module):
        """Should auto-connect if not connected."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value = mock_cursor
        # First ping raises (not connected), then connect succeeds
        mock_conn.ping.side_effect = [mariadb_module.Error("not connected"), None]
        mariadb_module.connect.return_value = mock_conn

        client = MariaDBClient(host="localhost", user="root", password="pass")
        client._connection = mock_conn  # Set initial "broken" connection

        results = client.execute_query("SELECT 1")
        assert results == [{"affected_rows": 0}]

    def test_execute_query_raw_with_results(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [(1, "test")]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.ping.return_value = None
        client._connection = mock_conn

        columns, rows = client.execute_query_raw("SELECT * FROM t")
        assert columns == ["id", "name"]
        assert rows == [(1, "test")]

    def test_execute_query_raw_no_results(self):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.ping.return_value = None
        client._connection = mock_conn

        columns, rows = client.execute_query_raw("INSERT INTO t VALUES (1)")
        assert columns == []
        assert rows == []

    def test_execute_query_raw_error(self, mariadb_module):
        client = MariaDBClient(host="localhost", user="root", password="pass")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = mariadb_module.Error("fail")
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.ping.return_value = None
        client._connection = mock_conn

        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query_raw("BAD SQL")

    def test_get_connection_info(self):
        client = MariaDBClient(host="localhost", user="root", password="pass", database="mydb")
        info = client.get_connection_info()
        assert info["host"] == "localhost"
        assert info["database"] == "mydb"
        assert info["user"] == "root"
        assert "password" not in info

    def test_context_manager(self):
        mock_conn = MagicMock()
        mock_conn.close.return_value = None
        # Reset connect mock fully (prior tests may have set side_effect)
        _mock_mariadb_module.connect.reset_mock()
        _mock_mariadb_module.connect.side_effect = None
        _mock_mariadb_module.connect.return_value = mock_conn
        client = MariaDBClient(host="localhost", user="root", password="pass")

        with client as c:
            assert c is client
        # close() was called on the connection
        assert mock_conn.close.called


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
            ssl_ca="/ca.pem", charset="utf8"
        )
        client = cfg.create_client()
        assert client.port == 3307
        assert client.database == "mydb"


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

# =============================================================================
# Merged from test_mariadb_client_coverage.py
# =============================================================================

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
class TestMariaDBConfigCoverage:
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
class TestMariaDBConnectorConfigCoverage:
    def test_defaults(self):
        auth = AuthConfig(host="localhost", user="root")
        config = MariaDBConnectorConfig(auth=auth)
        assert config.timeout == 30


# ============================================================================
# MariaDBResponse
# ============================================================================
class TestMariaDBResponseCoverage:
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
class TestMariaDBClientCoverage:
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
class TestMariaDBClientBuilderCoverage:
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
