"""
Extended tests for app.sources.client.redshift.redshift covering missing lines:
- RedshiftClient.execute_query with params
- RedshiftClient.execute_query_raw with params
- RedshiftClient.execute_query_raw with no description
- RedshiftClientBuilder.build_from_services with ValidationError
- RedshiftClient.build_from_toolset missing database
- RedshiftResponse model
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# RedshiftClient query methods
# ============================================================================


class TestRedshiftClientQueries:
    def _make_client(self):
        with patch("app.sources.client.redshift.redshift.redshift_connector"):
            from app.sources.client.redshift.redshift import RedshiftClient
            client = RedshiftClient(
                host="cluster.us-east-1.redshift.amazonaws.com",
                database="testdb",
                user="testuser",
                password="testpass",
            )
        return client

    def test_execute_query_with_params(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("col1",), ("col2",)]
        mock_cursor.fetchall.return_value = [("val1", "val2")]
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn

        result = client.execute_query("SELECT * FROM t WHERE id = %s", {"id": 1})
        mock_cursor.execute.assert_called_once_with("SELECT * FROM t WHERE id = %s", {"id": 1})
        assert len(result) == 1
        assert result[0]["col1"] == "val1"

    def test_execute_query_no_description(self):
        """INSERT/UPDATE/DELETE returns affected_rows."""
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_cursor.rowcount = 5
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn

        result = client.execute_query("INSERT INTO t VALUES (1)")
        assert result[0]["affected_rows"] == 5

    def test_execute_query_raw_with_params(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall.return_value = [("val1",)]
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn

        columns, rows = client.execute_query_raw("SELECT * FROM t WHERE id = %s", [1])
        mock_cursor.execute.assert_called_once_with("SELECT * FROM t WHERE id = %s", [1])
        assert columns == ["col1"]
        assert rows == [("val1",)]

    def test_execute_query_raw_no_description(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = None
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn

        columns, rows = client.execute_query_raw("DELETE FROM t")
        assert columns == []
        assert rows == []

    def test_execute_query_raw_failure(self):
        client = self._make_client()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("query error")
        mock_conn.cursor.return_value = mock_cursor
        client._connection = mock_conn

        with pytest.raises(RuntimeError, match="Query execution failed"):
            client.execute_query_raw("BAD QUERY")
        mock_conn.rollback.assert_called_once()


# ============================================================================
# RedshiftClientBuilder.build_from_services
# ============================================================================


class TestRedshiftClientBuilderBuildFromServices:
    @pytest.mark.asyncio
    async def test_validation_error(self):
        from app.sources.client.redshift.redshift import RedshiftClientBuilder
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "cluster",
                "port": "invalid_port",  # should cause validation
                "database": "db",
                "user": "user",
            }
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError):
            await RedshiftClientBuilder.build_from_services(logger, config_service, "inst1")

    @pytest.mark.asyncio
    async def test_config_not_dict(self):
        from app.sources.client.redshift.redshift import RedshiftClientBuilder
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not a dict")
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Failed to get Redshift connector configuration"):
            await RedshiftClientBuilder.build_from_services(logger, config_service, "inst1")


# ============================================================================
# RedshiftClient.build_from_toolset
# ============================================================================


class TestBuildFromToolsetExtended:
    @pytest.mark.asyncio
    async def test_missing_database(self):
        from app.sources.client.redshift.redshift import RedshiftClient

        toolset_config = {
            "instanceId": "inst-123",
            "auth": {"username": "user", "password": "pass"},
        }
        config_service = AsyncMock()
        logger = logging.getLogger("test")

        with patch(
            "app.sources.client.redshift.redshift.get_toolset_by_id",
            new_callable=AsyncMock,
            return_value={
                "auth": {"host": "cluster", "port": 5439},
            },
        ):
            with pytest.raises(ValueError, match="missing required field: database"):
                await RedshiftClient.build_from_toolset(toolset_config, logger, config_service)


# ============================================================================
# RedshiftResponse
# ============================================================================


class TestRedshiftResponse:
    def test_to_dict(self):
        from app.sources.client.redshift.redshift import RedshiftResponse
        resp = RedshiftResponse(success=True, data={"key": "value"})
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"key": "value"}
        assert "error" not in d

    def test_to_json(self):
        from app.sources.client.redshift.redshift import RedshiftResponse
        resp = RedshiftResponse(success=False, error="bad request")
        j = resp.to_json()
        assert '"success":false' in j or '"success": false' in j
