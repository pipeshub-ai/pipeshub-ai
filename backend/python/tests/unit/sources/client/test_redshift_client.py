"""Tests for app.sources.client.redshift.redshift — Redshift client models and builders."""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# RedshiftConfig
# ---------------------------------------------------------------------------

class TestRedshiftConfig:
    def test_valid(self):
        from app.sources.client.redshift.redshift import RedshiftConfig

        cfg = RedshiftConfig(host="host.redshift.com", database="mydb", user="admin")
        assert cfg.host == "host.redshift.com"
        assert cfg.port == 5439
        assert cfg.database == "mydb"
        assert cfg.user == "admin"
        assert cfg.password == ""
        assert cfg.ssl is True
        assert cfg.timeout == 180

    def test_custom_values(self):
        from app.sources.client.redshift.redshift import RedshiftConfig

        cfg = RedshiftConfig(
            host="host.com", database="db", user="u", password="p",
            port=5440, timeout=60, ssl=False
        )
        assert cfg.port == 5440
        assert cfg.password == "p"
        assert cfg.ssl is False
        assert cfg.timeout == 60

    def test_missing_host_fails(self):
        from app.sources.client.redshift.redshift import RedshiftConfig

        with pytest.raises(ValidationError):
            RedshiftConfig(database="db", user="u")

    def test_missing_database_fails(self):
        from app.sources.client.redshift.redshift import RedshiftConfig

        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", user="u")

    def test_missing_user_fails(self):
        from app.sources.client.redshift.redshift import RedshiftConfig

        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", database="db")

    def test_invalid_port_fails(self):
        from app.sources.client.redshift.redshift import RedshiftConfig

        with pytest.raises(ValidationError):
            RedshiftConfig(host="h", database="db", user="u", port=0)

    def test_create_client(self):
        from app.sources.client.redshift.redshift import RedshiftConfig

        cfg = RedshiftConfig(host="h", database="db", user="u", password="p")
        with patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            client = cfg.create_client()
            assert client.host == "h"
            assert client.database == "db"


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------

class TestAuthConfig:
    def test_valid(self):
        from app.sources.client.redshift.redshift import AuthConfig

        cfg = AuthConfig(host="h", database="db", user="u")
        assert cfg.host == "h"
        assert cfg.port == 5439
        assert cfg.ssl is True

    def test_missing_fields(self):
        from app.sources.client.redshift.redshift import AuthConfig

        with pytest.raises(ValidationError):
            AuthConfig(host="h")


# ---------------------------------------------------------------------------
# RedshiftConnectorConfig
# ---------------------------------------------------------------------------

class TestRedshiftConnectorConfig:
    def test_valid(self):
        from app.sources.client.redshift.redshift import AuthConfig, RedshiftConnectorConfig

        auth = AuthConfig(host="h", database="db", user="u", password="p")
        cfg = RedshiftConnectorConfig(auth=auth)
        assert cfg.auth.host == "h"
        assert cfg.timeout == 180


# ---------------------------------------------------------------------------
# RedshiftClient
# ---------------------------------------------------------------------------

class TestRedshiftClient:
    def _make_client(self):
        with patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            from app.sources.client.redshift.redshift import RedshiftClient
            return RedshiftClient(host="h", database="db", user="u", password="p")

    def test_init(self):
        client = self._make_client()
        assert client.host == "h"
        assert client.port == 5439
        assert client.database == "db"
        assert client._connection is None

    def test_get_connection_info(self):
        client = self._make_client()
        info = client.get_connection_info()
        assert info["host"] == "h"
        assert info["port"] == 5439
        assert info["database"] == "db"
        assert info["ssl"] is True

    def test_import_error_when_no_connector(self):
        with patch("app.sources.client.redshift.redshift.redshift_connector", None):
            from app.sources.client.redshift.redshift import RedshiftClient
            with pytest.raises(ImportError, match="redshift_connector"):
                RedshiftClient(host="h", database="db", user="u", password="p")

    def test_connect_success(self):
        mock_connector = MagicMock()
        mock_conn = MagicMock()
        mock_connector.connect.return_value = mock_conn
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_connector):
            from app.sources.client.redshift.redshift import RedshiftClient
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            result = client.connect()
            assert result is client
            assert client._connection is mock_conn

    def test_connect_already_connected(self):
        mock_connector = MagicMock()
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_connector):
            from app.sources.client.redshift.redshift import RedshiftClient
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            mock_conn = MagicMock()
            client._connection = mock_conn
            result = client.connect()
            assert result is client
            mock_connector.connect.assert_not_called()

    def test_connect_failure(self):
        mock_connector = MagicMock()
        mock_connector.connect.side_effect = Exception("conn refused")
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_connector):
            from app.sources.client.redshift.redshift import RedshiftClient
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            with pytest.raises(ConnectionError, match="Failed to connect"):
                client.connect()

    def test_close(self):
        client = self._make_client()
        mock_conn = MagicMock()
        client._connection = mock_conn
        client.close()
        mock_conn.close.assert_called_once()
        assert client._connection is None

    def test_close_no_connection(self):
        client = self._make_client()
        client.close()  # Should not raise

    def test_close_error_handled(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_conn.close.side_effect = Exception("close error")
        client._connection = mock_conn
        client.close()  # Should not raise
        assert client._connection is None

    def test_is_connected_no_connection(self):
        client = self._make_client()
        assert client.is_connected() is False

    def test_is_connected_active(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        assert client.is_connected() is True

    def test_is_connected_broken(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("broken")
        client._connection = mock_conn
        assert client.is_connected() is False
        assert client._connection is None

    def test_execute_query_select(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("col1",), ("col2",)]
        mock_cursor.fetchall.return_value = [("a", 1), ("b", 2)]
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        result = client.execute_query("SELECT * FROM test")
        assert len(result) == 2
        assert result[0] == {"col1": "a", "col2": 1}

    def test_execute_query_insert(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_cursor.rowcount = 5
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        result = client.execute_query("INSERT INTO test VALUES (1)")
        assert result == [{"affected_rows": 5}]

    def test_execute_query_with_params(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",)]
        mock_cursor.fetchall.return_value = [(1,)]
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        result = client.execute_query("SELECT * FROM test WHERE id = %s", [1])
        mock_cursor.execute.assert_called_with("SELECT * FROM test WHERE id = %s", [1])

    def test_execute_query_failure(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("query error")
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query("BAD SQL")
        mock_conn.rollback.assert_called_once()

    def test_execute_query_raw_select(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall.return_value = [(1,), (2,)]
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        cols, rows = client.execute_query_raw("SELECT 1")
        assert cols == ["col1"]
        assert len(rows) == 2

    def test_execute_query_raw_no_description(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        cols, rows = client.execute_query_raw("DO SOMETHING")
        assert cols == []
        assert rows == []

    def test_execute_query_raw_failure(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("raw error")
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn
        with pytest.raises(RuntimeError):
            client.execute_query_raw("BAD SQL")

    def test_context_manager(self):
        mock_connector = MagicMock()
        mock_conn = MagicMock()
        mock_connector.connect.return_value = mock_conn
        with patch("app.sources.client.redshift.redshift.redshift_connector", mock_connector):
            from app.sources.client.redshift.redshift import RedshiftClient
            client = RedshiftClient(host="h", database="db", user="u", password="p")
            with client as c:
                assert c is client
            mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# RedshiftClientBuilder
# ---------------------------------------------------------------------------

class TestRedshiftClientBuilder:
    def test_get_client(self):
        from app.sources.client.redshift.redshift import RedshiftClientBuilder
        mock_client = MagicMock()
        builder = RedshiftClientBuilder(mock_client)
        assert builder.get_client() is mock_client

    def test_get_connection_info(self):
        from app.sources.client.redshift.redshift import RedshiftClientBuilder
        mock_client = MagicMock()
        mock_client.get_connection_info.return_value = {"host": "h"}
        builder = RedshiftClientBuilder(mock_client)
        assert builder.get_connection_info() == {"host": "h"}

    def test_build_with_config(self):
        from app.sources.client.redshift.redshift import RedshiftClientBuilder, RedshiftConfig
        with patch("app.sources.client.redshift.redshift.redshift_connector", MagicMock()):
            cfg = RedshiftConfig(host="h", database="db", user="u", password="p")
            builder = RedshiftClientBuilder.build_with_config(cfg)
            assert builder.get_client() is not None


# ---------------------------------------------------------------------------
# RedshiftClient.build_from_toolset
# ---------------------------------------------------------------------------

class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_missing_instance_id(self):
        from app.sources.client.redshift.redshift import RedshiftClient
        with pytest.raises(ValueError, match="Instance ID is required"):
            await RedshiftClient.build_from_toolset(
                {}, MagicMock(), MagicMock()
            )

    @pytest.mark.asyncio
    async def test_missing_host(self):
        from app.sources.client.redshift.redshift import RedshiftClient
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
        from app.sources.client.redshift.redshift import RedshiftClient
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
    async def test_successful_build(self):
        from app.sources.client.redshift.redshift import RedshiftClient
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
