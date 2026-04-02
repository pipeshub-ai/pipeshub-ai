"""Full-coverage unit tests for Redshift client module.

Imports all classes at module-level (ensuring coverage tracks them)
and covers every statement & branch including:
  - RedshiftClient init, connect, close, is_connected, execute_query,
    execute_query_raw, get_connection_info, context manager
  - RedshiftConfig, AuthConfig, RedshiftConnectorConfig validation
  - RedshiftClientBuilder.build_with_config, build_from_services, _get_connector_config
  - RedshiftClient.build_from_toolset (pick_value helper, all missing-field errors)
  - RedshiftResponse to_dict, to_json
  - All branch conditions: params vs no-params, cursor.description vs None,
    connected vs not connected, empty rows, etc.

NOTE: We mock app.api.routes.toolsets via sys.modules before importing the
redshift module to avoid the deep import chain that fails under --cov.
"""

import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-mock the problematic dependency to allow import under --cov
_toolsets_mock = MagicMock()
if "app.api.routes.toolsets" not in sys.modules:
    sys.modules["app.api.routes.toolsets"] = _toolsets_mock

from app.sources.client.redshift.redshift import (  # noqa: E402
    AuthConfig,
    RedshiftClient,
    RedshiftClientBuilder,
    RedshiftConfig,
    RedshiftConnectorConfig,
    RedshiftResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**overrides):
    defaults = dict(host="h", database="db", user="u", password="p")
    defaults.update(overrides)
    with patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
        return RedshiftClient(**defaults)


# ---------------------------------------------------------------------------
# RedshiftConfig
# ---------------------------------------------------------------------------


class TestRedshiftConfigFC:
    def test_valid_defaults(self):
        cfg = RedshiftConfig(host="h", database="db", user="u")
        assert cfg.port == 5439
        assert cfg.password == ""
        assert cfg.ssl is True
        assert cfg.timeout == 180

    def test_custom_values(self):
        cfg = RedshiftConfig(
            host="h", database="db", user="u", password="p",
            port=5440, timeout=60, ssl=False,
        )
        assert cfg.port == 5440
        assert cfg.ssl is False

    def test_missing_host(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RedshiftConfig(database="db", user="u")

    def test_missing_database(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", user="u")

    def test_missing_user(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", database="db")

    def test_invalid_port_zero(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", database="db", user="u", port=0)

    def test_invalid_port_too_high(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", database="db", user="u", port=70000)

    def test_invalid_timeout(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", database="db", user="u", timeout=0)

    def test_create_client(self):
        cfg = RedshiftConfig(host="h", database="db", user="u", password="p")
        with patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = cfg.create_client()
        assert isinstance(client, RedshiftClient)
        assert client.host == "h"


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------


class TestAuthConfigFC:
    def test_valid(self):
        cfg = AuthConfig(host="h", database="db", user="u")
        assert cfg.port == 5439
        assert cfg.ssl is True
        assert cfg.password == ""

    def test_missing_fields(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AuthConfig(host="h")

    def test_custom_port_and_ssl(self):
        cfg = AuthConfig(host="h", database="db", user="u", port=9999, ssl=False)
        assert cfg.port == 9999
        assert cfg.ssl is False


# ---------------------------------------------------------------------------
# RedshiftConnectorConfig
# ---------------------------------------------------------------------------


class TestRedshiftConnectorConfigFC:
    def test_valid(self):
        auth = AuthConfig(host="h", database="db", user="u", password="p")
        cfg = RedshiftConnectorConfig(auth=auth)
        assert cfg.timeout == 180

    def test_custom_timeout(self):
        auth = AuthConfig(host="h", database="db", user="u")
        cfg = RedshiftConnectorConfig(auth=auth, timeout=60)
        assert cfg.timeout == 60

    def test_invalid_timeout(self):
        from pydantic import ValidationError
        auth = AuthConfig(host="h", database="db", user="u")
        with pytest.raises(ValidationError):
            RedshiftConnectorConfig(auth=auth, timeout=0)


# ---------------------------------------------------------------------------
# RedshiftClient
# ---------------------------------------------------------------------------


class TestRedshiftClientFC:
    def test_init(self):
        client = _make_client()
        assert client.host == "h"
        assert client.port == 5439
        assert client._connection is None

    def test_init_custom_params(self):
        client = _make_client(port=5440, timeout=60, ssl=False)
        assert client.port == 5440
        assert client.timeout == 60
        assert client.ssl is False

    def test_import_error_when_no_connector(self):
        with patch("app.sources.client.redshift.redshift.redshift_connector", None):
            with pytest.raises(ImportError, match="redshift_connector"):
                RedshiftClient(host="h", database="db", user="u", password="p")

    def test_get_connection_info(self):
        client = _make_client()
        info = client.get_connection_info()
        assert info == {
            "host": "h", "port": 5439, "database": "db", "user": "u", "ssl": True,
        }

    # --- connect ---

    def test_connect_success(self):
        mock_rc = MagicMock()
        mock_conn = MagicMock()
        mock_rc.connect.return_value = mock_conn
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_rc):
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            result = client.connect()
        assert result is client
        assert client._connection is mock_conn

    def test_connect_already_connected(self):
        mock_rc = MagicMock()
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_rc):
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = MagicMock()
            client._connection = mock_conn
            result = client.connect()
        assert result is client
        mock_rc.connect.assert_not_called()

    def test_connect_failure(self):
        mock_rc = MagicMock()
        mock_rc.connect.side_effect = Exception("conn refused")
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_rc):
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            with pytest.raises(ConnectionError, match="Failed to connect"):
                client.connect()

    # --- close ---

    def test_close(self):
        client = _make_client()
        mock_conn = MagicMock()
        client._connection = mock_conn
        client.close()
        mock_conn.close.assert_called_once()
        assert client._connection is None

    def test_close_no_connection(self):
        client = _make_client()
        client.close()  # Should not raise

    def test_close_error_handled(self):
        client = _make_client()
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("close err")
        client._connection = mock_conn
        client.close()  # Should not raise
        assert client._connection is None

    # --- is_connected ---

    def test_is_connected_none(self):
        client = _make_client()
        assert client.is_connected() is False

    def test_is_connected_true(self):
        client = _make_client()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        client._connection = mock_conn
        assert client.is_connected() is True

    def test_is_connected_broken(self):
        client = _make_client()
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("broken")
        client._connection = mock_conn
        assert client.is_connected() is False
        assert client._connection is None

    # --- execute_query ---

    def test_execute_query_select(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.description = [("col1",), ("col2",)]
        cur.fetchall.return_value = [("a", 1), ("b", 2)]
        mc.cursor.return_value = cur
        client._connection = mc
        result = client.execute_query("SELECT *")
        assert len(result) == 2
        assert result[0] == {"col1": "a", "col2": 1}

    def test_execute_query_with_params(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.description = [("id",)]
        cur.fetchall.return_value = [(1,)]
        mc.cursor.return_value = cur
        client._connection = mc
        result = client.execute_query("SELECT * WHERE id=%s", [1])
        cur.execute.assert_called_with("SELECT * WHERE id=%s", [1])
        assert result == [{"id": 1}]

    def test_execute_query_no_description(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.description = None
        cur.rowcount = 5
        mc.cursor.return_value = cur
        client._connection = mc
        result = client.execute_query("INSERT INTO t VALUES (1)")
        assert result == [{"affected_rows": 5}]

    def test_execute_query_failure(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.execute.side_effect = Exception("query err")
        mc.cursor.return_value = cur
        client._connection = mc
        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query("BAD SQL")
        mc.rollback.assert_called_once()

    def test_execute_query_auto_connect(self):
        """When not connected, execute_query should call connect()."""
        mock_rc = MagicMock()
        mock_conn = MagicMock()
        cur = MagicMock()
        cur.description = [("x",)]
        cur.fetchall.return_value = [(1,)]
        mock_conn.cursor.return_value = cur
        mock_rc.connect.return_value = mock_conn

        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_rc):
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            result = client.execute_query("SELECT 1")
        assert result == [{"x": 1}]

    # --- execute_query_raw ---

    def test_execute_query_raw_select(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.description = [("col1",)]
        cur.fetchall.return_value = [(1,), (2,)]
        mc.cursor.return_value = cur
        client._connection = mc
        cols, rows = client.execute_query_raw("SELECT 1")
        assert cols == ["col1"]
        assert len(rows) == 2

    def test_execute_query_raw_with_params(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.description = [("col1",)]
        cur.fetchall.return_value = [("val",)]
        mc.cursor.return_value = cur
        client._connection = mc
        cols, rows = client.execute_query_raw("SELECT * WHERE id=%s", [1])
        cur.execute.assert_called_with("SELECT * WHERE id=%s", [1])

    def test_execute_query_raw_no_description(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.description = None
        mc.cursor.return_value = cur
        client._connection = mc
        cols, rows = client.execute_query_raw("DELETE FROM t")
        assert cols == []
        assert rows == []

    def test_execute_query_raw_empty_rows(self):
        """description exists but fetchall returns empty list."""
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.description = [("col1",)]
        cur.fetchall.return_value = []
        mc.cursor.return_value = cur
        client._connection = mc
        cols, rows = client.execute_query_raw("SELECT * FROM empty_table")
        assert cols == ["col1"]
        assert rows == []

    def test_execute_query_raw_failure(self):
        client = _make_client()
        mc = MagicMock()
        cur = MagicMock()
        cur.execute.side_effect = Exception("raw err")
        mc.cursor.return_value = cur
        client._connection = mc
        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query_raw("BAD SQL")
        mc.rollback.assert_called_once()

    def test_execute_query_raw_auto_connect(self):
        """When not connected, execute_query_raw should call connect()."""
        mock_rc = MagicMock()
        mock_conn = MagicMock()
        cur = MagicMock()
        cur.description = [("x",)]
        cur.fetchall.return_value = [(1,)]
        mock_conn.cursor.return_value = cur
        mock_rc.connect.return_value = mock_conn

        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_rc):
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            cols, rows = client.execute_query_raw("SELECT 1")
        assert cols == ["x"]

    # --- context manager ---

    def test_context_manager(self):
        mock_rc = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        mock_rc.connect.return_value = mock_conn
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_rc):
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            with client as c:
                assert c is client
            mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# RedshiftClientBuilder
# ---------------------------------------------------------------------------


class TestRedshiftClientBuilderFC:
    def test_get_client(self):
        mock_client = MagicMock()
        builder = RedshiftClientBuilder(mock_client)
        assert builder.get_client() is mock_client

    def test_get_connection_info(self):
        mock_client = MagicMock()
        mock_client.get_connection_info.return_value = {"host": "h"}
        builder = RedshiftClientBuilder(mock_client)
        assert builder.get_connection_info() == {"host": "h"}

    def test_build_with_config(self):
        cfg = RedshiftConfig(host="h", database="db", user="u", password="p")
        with patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            builder = RedshiftClientBuilder.build_with_config(cfg)
        assert builder.get_client() is not None

    # --- build_from_services ---

    @pytest.mark.asyncio
    async def test_build_from_services_success(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "cluster.redshift.com",
                "port": 5439,
                "database": "mydb",
                "user": "admin",
                "password": "pass",
                "ssl": True,
            },
            "timeout": 120,
        })
        lgr = logging.getLogger("test")
        with patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            builder = await RedshiftClientBuilder.build_from_services(
                lgr, config_service, "inst-1"
            )
        assert builder.get_client().host == "cluster.redshift.com"

    @pytest.mark.asyncio
    async def test_build_from_services_validation_error(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "h",
                "port": "invalid_port",
                "database": "db",
                "user": "u",
            }
        })
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Invalid Redshift connector configuration"):
            await RedshiftClientBuilder.build_from_services(lgr, config_service, "i1")

    @pytest.mark.asyncio
    async def test_build_from_services_empty_config(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift"):
            await RedshiftClientBuilder.build_from_services(lgr, config_service, "i1")

    @pytest.mark.asyncio
    async def test_build_from_services_config_not_dict(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="string_config")
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift"):
            await RedshiftClientBuilder.build_from_services(lgr, config_service, "i1")

    @pytest.mark.asyncio
    async def test_build_from_services_no_instance_id(self):
        """connector_instance_id=None path."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError):
            await RedshiftClientBuilder.build_from_services(lgr, config_service, None)

    @pytest.mark.asyncio
    async def test_build_from_services_generic_exception(self):
        """Non-ValidationError exception in build_from_services."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "h",
                "port": 5439,
                "database": "db",
                "user": "u",
                "password": "p",
            }
        })
        lgr = logging.getLogger("test")
        with patch("app.sources.client.redshift.redshift.redshift_connector", None):
            with pytest.raises(ImportError):
                await RedshiftClientBuilder.build_from_services(lgr, config_service, "i1")

    # --- _get_connector_config ---

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {"host": "h"}})
        lgr = logging.getLogger("test")
        result = await RedshiftClientBuilder._get_connector_config(lgr, config_service, "i1")
        assert result["auth"]["host"] == "h"

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift"):
            await RedshiftClientBuilder._get_connector_config(lgr, config_service, "i1")

    @pytest.mark.asyncio
    async def test_get_connector_config_not_dict(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="bad")
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift"):
            await RedshiftClientBuilder._get_connector_config(lgr, config_service, "i1")

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift"):
            await RedshiftClientBuilder._get_connector_config(lgr, config_service, "i1")

    @pytest.mark.asyncio
    async def test_get_connector_config_no_instance_id(self):
        """connector_instance_id=None: 'if connector_instance_id' is False."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift"):
            await RedshiftClientBuilder._get_connector_config(lgr, config_service, None)

    @pytest.mark.asyncio
    async def test_get_connector_config_not_dict_no_instance_id(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=42)
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift"):
            await RedshiftClientBuilder._get_connector_config(lgr, config_service, None)

    @pytest.mark.asyncio
    async def test_get_connector_config_not_dict_with_instance_id(self):
        """config is not dict, connector_instance_id is truthy."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[1, 2])
        lgr = logging.getLogger("test")
        with pytest.raises(ValueError, match="for instance i1"):
            await RedshiftClientBuilder._get_connector_config(lgr, config_service, "i1")


# ---------------------------------------------------------------------------
# RedshiftClient.build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolsetFC:
    @pytest.mark.asyncio
    async def test_missing_instance_id(self):
        with pytest.raises(ValueError, match="Instance ID is required"):
            await RedshiftClient.build_from_toolset({}, MagicMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_missing_host(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {}, "credentials": {}},
        ):
            with pytest.raises(ValueError, match="host"):
                await RedshiftClient.build_from_toolset(
                    {"instanceId": "i1", "username": "u", "password": "p"},
                    MagicMock(), MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_missing_user(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"host": "h", "database": "db"}, "credentials": {}},
        ):
            with pytest.raises(ValueError, match="user"):
                await RedshiftClient.build_from_toolset(
                    {"instanceId": "i1"},
                    MagicMock(), MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_missing_database(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"host": "h"}, "credentials": {}},
        ):
            with pytest.raises(ValueError, match="database"):
                await RedshiftClient.build_from_toolset(
                    {"instanceId": "i1", "username": "u", "password": "p"},
                    MagicMock(), MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_successful_build(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"host": "h", "database": "db"}, "credentials": {}},
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.host == "h"
            assert client.user == "u"

    @pytest.mark.asyncio
    async def test_successful_build_with_port(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"host": "h", "database": "db", "port": 5440},
                "credentials": {},
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.port == 5440

    @pytest.mark.asyncio
    async def test_password_none_defaults_to_empty(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={"auth": {"host": "h", "database": "db"}, "credentials": {}},
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u"},
                MagicMock(), MagicMock(),
            )
            assert client.password == ""

    @pytest.mark.asyncio
    async def test_pick_value_from_credentials(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "credentials": {"host": "h", "database": "db"},
                "auth": {},
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "credentials": {"username": "u", "password": "p"}},
                MagicMock(), MagicMock(),
            )
            assert client.user == "u"

    @pytest.mark.asyncio
    async def test_pick_value_hostname_key(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"hostname": "h2", "database": "db"},
                "credentials": {},
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.host == "h2"

    @pytest.mark.asyncio
    async def test_pick_value_endpoint_key(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"endpoint": "e1", "database": "db"},
                "credentials": {},
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.host == "e1"

    @pytest.mark.asyncio
    async def test_pick_value_db_key(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"host": "h", "db": "mydb"},
                "credentials": {},
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.database == "mydb"

    @pytest.mark.asyncio
    async def test_pick_value_databaseName_key(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"host": "h", "databaseName": "named_db"},
                "credentials": {},
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.database == "named_db"

    @pytest.mark.asyncio
    async def test_pick_value_non_dict_container_skipped(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": "not_a_dict",
                "credentials": "also_not_dict",
                "host": "h",
                "database": "db",
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.host == "h"

    @pytest.mark.asyncio
    async def test_pick_value_empty_string_skipped(self):
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"host": "", "hostname": "h3", "database": "db"},
                "credentials": {},
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.host == "h3"

    @pytest.mark.asyncio
    async def test_pick_value_none_auth_and_credentials(self):
        """When auth and credentials are None, pick_value should still find
        values from the top-level config dict."""
        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": None,
                "credentials": None,
                "host": "h",
                "database": "db",
            },
        ), patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = await RedshiftClient.build_from_toolset(
                {"instanceId": "i1", "username": "u", "password": "p"},
                MagicMock(), MagicMock(),
            )
            assert client.host == "h"


# ---------------------------------------------------------------------------
# RedshiftResponse
# ---------------------------------------------------------------------------


class TestRedshiftResponseFC:
    def test_to_dict(self):
        resp = RedshiftResponse(success=True, data={"k": "v"}, message="ok")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"k": "v"}
        assert d["message"] == "ok"
        assert "error" not in d

    def test_to_dict_with_error(self):
        resp = RedshiftResponse(success=False, error="bad")
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"] == "bad"

    def test_to_json(self):
        resp = RedshiftResponse(success=False, error="bad request")
        j = resp.to_json()
        assert "false" in j
        assert "bad request" in j

    def test_to_json_success(self):
        resp = RedshiftResponse(success=True, data=[1, 2, 3])
        j = resp.to_json()
        assert "true" in j

    def test_defaults(self):
        resp = RedshiftResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None

    def test_list_data(self):
        resp = RedshiftResponse(success=True, data=[1, 2, 3])
        d = resp.to_dict()
        assert d["data"] == [1, 2, 3]
