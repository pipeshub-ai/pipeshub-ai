"""
Unit tests for ArangoHTTPClient (app/services/graph_db/arango/arango_http_client.py).

Tests cover:
- __init__: attribute initialization, URL normalization
- _get_session: event loop detection, session recreation on loop change, reuse
- connect: success, failure
- disconnect: with session, without session
- _check_response_for_errors: dict error, list with errors, no errors
- database_exists: found, not found
- create_database: success, conflict, failure
- get_document: found, not found, error
- create_document: success, conflict, error
- update_document: success, error
- delete_document: success, error
- execute_aql: success, cursor pagination, query error
- batch_insert_documents: success with overwrite, without overwrite, error
- batch_delete_documents: success, partial not-found, real errors, empty
- create_edge / delete_edge
- collection_exists / create_collection
- begin_transaction / commit_transaction / abort_transaction
- ensure_persistent_index
- update_collection_schema: success, already configured, failure
- has_collection / has_graph
- _handle_response helper
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arango.arango_http_client import (
    ARANGO_ERROR_DOCUMENT_NOT_FOUND,
    ARANGO_ERROR_SCHEMA_DUPLICATE,
    ArangoHTTPClient,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def client(mock_logger):
    return ArangoHTTPClient(
        base_url="http://localhost:8529",
        username="root",
        password="secret",
        database="test_db",
        logger=mock_logger,
    )


class MockResponse:
    """Async context manager mock for aiohttp response."""

    def __init__(self, status, json_data=None, text_data=""):
        self.status = status
        self._json = json_data
        self._text = text_data

    async def json(self):
        return self._json

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_attributes(self, client):
        assert client.base_url == "http://localhost:8529"
        assert client.database == "test_db"
        assert client.username == "root"
        assert client.password == "secret"
        assert client._session is None
        assert client._session_loop is None

    def test_url_trailing_slash_stripped(self, mock_logger):
        c = ArangoHTTPClient(
            base_url="http://localhost:8529/",
            username="root",
            password="pass",
            database="db",
            logger=mock_logger,
        )
        assert c.base_url == "http://localhost:8529"


# ---------------------------------------------------------------------------
# _get_session
# ---------------------------------------------------------------------------


class TestGetSession:
    @pytest.mark.asyncio
    async def test_creates_new_session(self, client):
        with patch("app.services.graph_db.arango.arango_http_client.aiohttp.ClientSession") as mock_cls:
            mock_session = MagicMock()
            mock_cls.return_value = mock_session
            session = await client._get_session()
            assert session is mock_session
            assert client._session is mock_session

    @pytest.mark.asyncio
    async def test_reuses_existing_session(self, client):
        with patch("app.services.graph_db.arango.arango_http_client.aiohttp.ClientSession") as mock_cls:
            mock_session = MagicMock()
            mock_cls.return_value = mock_session
            s1 = await client._get_session()
            s2 = await client._get_session()
            assert s1 is s2
            assert mock_cls.call_count == 1

    @pytest.mark.asyncio
    async def test_recreates_session_on_loop_change(self, client):
        """When event loop changes, old session is closed and new one created."""
        mock_old_session = AsyncMock()
        client._session = mock_old_session
        client._session_loop = "different_loop"  # Simulate different loop

        with patch("app.services.graph_db.arango.arango_http_client.aiohttp.ClientSession") as mock_cls:
            mock_new_session = MagicMock()
            mock_cls.return_value = mock_new_session
            session = await client._get_session()
            assert session is mock_new_session
            mock_old_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_old_session_error_ignored(self, client):
        """Errors closing old session should be silently ignored."""
        mock_old_session = AsyncMock()
        mock_old_session.close.side_effect = Exception("close error")
        client._session = mock_old_session
        client._session_loop = "different_loop"

        with patch("app.services.graph_db.arango.arango_http_client.aiohttp.ClientSession") as mock_cls:
            mock_new_session = MagicMock()
            mock_cls.return_value = mock_new_session
            session = await client._get_session()
            assert session is mock_new_session


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, client):
        mock_resp = MockResponse(200, json_data={"version": "3.11.0"})
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.connect()
            assert result is True

    @pytest.mark.asyncio
    async def test_connect_failure(self, client):
        mock_resp = MockResponse(401)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.connect()
            assert result is False

    @pytest.mark.asyncio
    async def test_connect_exception(self, client):
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("network error")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.connect()
            assert result is False


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_with_session(self, client):
        mock_session = AsyncMock()
        client._session = mock_session
        client._session_loop = "some_loop"

        await client.disconnect()

        assert client._session is None
        assert client._session_loop is None
        mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_without_session(self, client):
        await client.disconnect()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_disconnect_close_error_ignored(self, client):
        mock_session = AsyncMock()
        mock_session.close.side_effect = Exception("error")
        client._session = mock_session

        await client.disconnect()
        assert client._session is None


# ---------------------------------------------------------------------------
# _check_response_for_errors
# ---------------------------------------------------------------------------


class TestCheckResponseForErrors:
    def test_dict_with_no_error(self, client):
        # Should not raise
        client._check_response_for_errors({"result": "ok"}, "test op")

    def test_dict_with_error(self, client):
        data = {"error": True, "errorMessage": "bad request", "errorNum": 1234}
        with pytest.raises(Exception, match="test op failed"):
            client._check_response_for_errors(data, "test op")

    def test_list_with_no_errors(self, client):
        data = [{"_key": "a"}, {"_key": "b"}]
        client._check_response_for_errors(data, "test op")

    def test_list_with_errors(self, client):
        data = [
            {"_key": "a"},
            {"error": True, "errorMessage": "conflict", "errorNum": 1210},
        ]
        with pytest.raises(Exception, match="1 error"):
            client._check_response_for_errors(data, "test op")

    def test_list_multiple_errors(self, client):
        data = [
            {"error": True, "errorMessage": "e1", "errorNum": 1},
            {"error": True, "errorMessage": "e2", "errorNum": 2},
        ]
        with pytest.raises(Exception, match="2 error"):
            client._check_response_for_errors(data, "test op")

    def test_non_dict_non_list_is_noop(self, client):
        # Should not raise for string/int/None etc.
        client._check_response_for_errors("plain text", "op")
        client._check_response_for_errors(42, "op")


# ---------------------------------------------------------------------------
# database_exists
# ---------------------------------------------------------------------------


class TestDatabaseExists:
    @pytest.mark.asyncio
    async def test_database_exists_true(self, client):
        mock_resp = MockResponse(200, json_data={"result": ["_system", "test_db"]})
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.database_exists("test_db") is True

    @pytest.mark.asyncio
    async def test_database_exists_false(self, client):
        mock_resp = MockResponse(200, json_data={"result": ["_system"]})
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.database_exists("nonexistent") is False

    @pytest.mark.asyncio
    async def test_database_exists_error_status(self, client):
        mock_resp = MockResponse(500)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.database_exists("test_db") is False


# ---------------------------------------------------------------------------
# create_database
# ---------------------------------------------------------------------------


class TestCreateDatabase:
    @pytest.mark.asyncio
    async def test_create_database_success(self, client):
        mock_resp = MockResponse(201)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.create_database("new_db") is True

    @pytest.mark.asyncio
    async def test_create_database_already_exists(self, client):
        mock_resp = MockResponse(409)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.create_database("existing_db") is True

    @pytest.mark.asyncio
    async def test_create_database_failure(self, client):
        mock_resp = MockResponse(500, text_data="server error")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.create_database("fail_db") is False


# ---------------------------------------------------------------------------
# Document operations
# ---------------------------------------------------------------------------


class TestGetDocument:
    @pytest.mark.asyncio
    async def test_get_document_found(self, client):
        doc = {"_key": "doc1", "name": "test"}
        mock_resp = MockResponse(200, json_data=doc)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_document("col", "doc1")
            assert result == doc

    @pytest.mark.asyncio
    async def test_get_document_not_found(self, client):
        mock_resp = MockResponse(404)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_document("col", "missing")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_document_with_txn(self, client):
        doc = {"_key": "doc1"}
        mock_resp = MockResponse(200, json_data=doc)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_document("col", "doc1", txn_id="txn123")
            assert result == doc

    @pytest.mark.asyncio
    async def test_get_document_error_status(self, client):
        mock_resp = MockResponse(500, text_data="error")
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_document("col", "doc1")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_document_exception(self, client):
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("network")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_document("col", "doc1")
            assert result is None


class TestCreateDocument:
    @pytest.mark.asyncio
    async def test_create_document_success(self, client):
        result_data = {"_key": "doc1", "_id": "col/doc1", "_rev": "_abc123"}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_document("col", {"_key": "doc1", "name": "test"})
            assert result == result_data

    @pytest.mark.asyncio
    async def test_create_document_conflict(self, client):
        mock_resp = MockResponse(409)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_document("col", {"_key": "doc1"})
            assert result == {"_key": "doc1"}

    @pytest.mark.asyncio
    async def test_create_document_error(self, client):
        mock_resp = MockResponse(500, text_data="server error")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Failed to create document"):
                await client.create_document("col", {"_key": "doc1"})

    @pytest.mark.asyncio
    async def test_create_document_with_response_error(self, client):
        """Test that _check_response_for_errors is called on successful status."""
        error_data = {"error": True, "errorMessage": "bad", "errorNum": 123}
        mock_resp = MockResponse(201, json_data=error_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception):
                await client.create_document("col", {"_key": "doc1"})


class TestUpdateDocument:
    @pytest.mark.asyncio
    async def test_update_document_success(self, client):
        result_data = {"_key": "doc1", "_rev": "_new_rev"}
        mock_resp = MockResponse(200, json_data=result_data)
        mock_session = MagicMock()
        mock_session.patch.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_document("col", "doc1", {"name": "updated"})
            assert result == result_data

    @pytest.mark.asyncio
    async def test_update_document_error(self, client):
        mock_resp = MockResponse(404, text_data="not found")
        mock_session = MagicMock()
        mock_session.patch.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Failed to update document"):
                await client.update_document("col", "doc1", {"name": "x"})


class TestDeleteDocumentHTTP:
    @pytest.mark.asyncio
    async def test_delete_document_success(self, client):
        mock_resp = MockResponse(200, json_data={"_key": "doc1"})
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.delete_document("col", "doc1")
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_document_no_content(self, client):
        """204 No Content response (empty body) should succeed."""
        mock_resp = MockResponse(204)
        # Override json to raise ValueError like real empty response
        async def raise_content_type():
            raise ValueError("No content")
        mock_resp.json = raise_content_type
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.delete_document("col", "doc1")
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_document_error_status(self, client):
        mock_resp = MockResponse(404, text_data="not found")
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Failed to delete document"):
                await client.delete_document("col", "doc1")


# ---------------------------------------------------------------------------
# execute_aql
# ---------------------------------------------------------------------------


class TestExecuteAql:
    @pytest.mark.asyncio
    async def test_simple_query(self, client):
        result_data = {"result": [{"name": "a"}, {"name": "b"}], "hasMore": False}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            results = await client.execute_aql("FOR d IN col RETURN d")
            assert results == [{"name": "a"}, {"name": "b"}]

    @pytest.mark.asyncio
    async def test_cursor_pagination(self, client):
        """Test that cursor follows hasMore pages."""
        page1 = {"result": [{"n": 1}], "hasMore": True, "id": "cursor123"}
        page2 = {"result": [{"n": 2}], "hasMore": False}

        mock_post_resp = MockResponse(201, json_data=page1)
        mock_cursor_resp = MockResponse(200, json_data=page2)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_resp
        mock_session.put.return_value = mock_cursor_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            results = await client.execute_aql("FOR d IN col RETURN d")
            assert results == [{"n": 1}, {"n": 2}]

    @pytest.mark.asyncio
    async def test_query_error(self, client):
        mock_resp = MockResponse(400, text_data="syntax error")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Query failed"):
                await client.execute_aql("BAD QUERY")

    @pytest.mark.asyncio
    async def test_query_with_bind_vars_and_txn(self, client):
        result_data = {"result": [{"_key": "x"}], "hasMore": False}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            results = await client.execute_aql(
                "RETURN @val",
                bind_vars={"val": 42},
                txn_id="txn999",
            )
            assert results == [{"_key": "x"}]

    @pytest.mark.asyncio
    async def test_query_response_with_error(self, client):
        """Test error detected in response body despite 201 status."""
        error_data = {
            "error": True,
            "errorMessage": "collection not found",
            "errorNum": 1203,
            "result": [],
            "hasMore": False,
        }
        mock_resp = MockResponse(201, json_data=error_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception):
                await client.execute_aql("FOR d IN missing RETURN d")


# ---------------------------------------------------------------------------
# batch_insert_documents
# ---------------------------------------------------------------------------


class TestBatchInsertDocuments:
    @pytest.mark.asyncio
    async def test_empty_documents(self, client):
        result = await client.batch_insert_documents("col", [])
        assert result == {"created": 0, "updated": 0, "errors": 0}

    @pytest.mark.asyncio
    async def test_batch_insert_new_docs(self, client):
        result_data = [{"_key": "a", "_rev": "_r1"}, {"_key": "b", "_rev": "_r2"}]
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_insert_documents(
                "col", [{"_key": "a"}, {"_key": "b"}]
            )
            assert result["created"] == 2
            assert result["updated"] == 0
            assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_batch_insert_updated_docs(self, client):
        result_data = [{"_key": "a", "_oldRev": "_old", "_rev": "_new"}]
        mock_resp = MockResponse(202, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_insert_documents("col", [{"_key": "a"}])
            assert result["updated"] == 1
            assert result["created"] == 0

    @pytest.mark.asyncio
    async def test_batch_insert_without_overwrite(self, client):
        result_data = [{"_key": "a", "_rev": "_r1"}]
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_insert_documents(
                "col", [{"_key": "a"}], overwrite=False
            )
            assert result["created"] == 1

    @pytest.mark.asyncio
    async def test_batch_insert_error_status(self, client):
        mock_resp = MockResponse(500, text_data="error")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Batch insert failed"):
                await client.batch_insert_documents("col", [{"_key": "a"}])

    @pytest.mark.asyncio
    async def test_batch_insert_single_doc_response(self, client):
        """When response is a single dict instead of list."""
        result_data = {"_key": "a", "_rev": "_r1"}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_insert_documents("col", [{"_key": "a"}])
            assert result["created"] == 1


# ---------------------------------------------------------------------------
# batch_delete_documents
# ---------------------------------------------------------------------------


class TestBatchDeleteDocuments:
    @pytest.mark.asyncio
    async def test_empty_keys(self, client):
        result = await client.batch_delete_documents("col", [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_batch_delete_success(self, client):
        result_data = [{"_key": "a"}, {"_key": "b"}]
        mock_resp = MockResponse(200, json_data=result_data)
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_delete_documents("col", ["a", "b"])
            assert result == 2

    @pytest.mark.asyncio
    async def test_batch_delete_with_not_found(self, client):
        """Documents not found (1202) should count as deleted."""
        result_data = [
            {"_key": "a"},
            {"error": True, "errorNum": ARANGO_ERROR_DOCUMENT_NOT_FOUND, "errorMessage": "not found"},
        ]
        mock_resp = MockResponse(200, json_data=result_data)
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_delete_documents("col", ["a", "b"])
            assert result == 2

    @pytest.mark.asyncio
    async def test_batch_delete_with_real_errors(self, client):
        result_data = [
            {"error": True, "errorNum": 999, "errorMessage": "real error"},
        ]
        mock_resp = MockResponse(200, json_data=result_data)
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="1 error"):
                await client.batch_delete_documents("col", ["a"])

    @pytest.mark.asyncio
    async def test_batch_delete_error_status(self, client):
        mock_resp = MockResponse(500, text_data="server error")
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Batch delete failed"):
                await client.batch_delete_documents("col", ["a"])


# ---------------------------------------------------------------------------
# Edge operations
# ---------------------------------------------------------------------------


class TestEdgeOperations:
    @pytest.mark.asyncio
    async def test_create_edge_success(self, client):
        result_data = {"_key": "edge1", "_id": "edges/edge1", "_rev": "_r1"}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_edge("edges", "users/u1", "records/r1")
            assert result == result_data

    @pytest.mark.asyncio
    async def test_create_edge_with_data(self, client):
        result_data = {"_key": "e1"}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_edge(
                "edges", "users/u1", "records/r1",
                edge_data={"role": "READER"},
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_create_edge_conflict(self, client):
        mock_resp = MockResponse(409)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_edge(
                "edges", "u/1", "r/2", edge_data={"_key": "e1"}
            )
            assert result == {"_key": "e1"}

    @pytest.mark.asyncio
    async def test_delete_edge_found(self, client):
        # First query returns edge keys
        with patch.object(client, "execute_aql", new_callable=AsyncMock, return_value=["edge_key_1"]):
            mock_resp = MockResponse(200)
            mock_session = MagicMock()
            mock_session.delete.return_value = mock_resp

            with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
                result = await client.delete_edge("edges", "u/1", "r/2")
                assert result is True

    @pytest.mark.asyncio
    async def test_delete_edge_not_found(self, client):
        with patch.object(client, "execute_aql", new_callable=AsyncMock, return_value=[]):
            result = await client.delete_edge("edges", "u/1", "r/2")
            assert result is False


# ---------------------------------------------------------------------------
# Collection operations
# ---------------------------------------------------------------------------


class TestCollectionOperations:
    @pytest.mark.asyncio
    async def test_collection_exists_true(self, client):
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.collection_exists("users") is True

    @pytest.mark.asyncio
    async def test_collection_exists_false(self, client):
        mock_resp = MockResponse(404)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.collection_exists("missing") is False

    @pytest.mark.asyncio
    async def test_collection_exists_exception(self, client):
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("error")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.collection_exists("col") is False

    @pytest.mark.asyncio
    async def test_create_collection_success(self, client):
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_collection("users")
            assert result is True

    @pytest.mark.asyncio
    async def test_create_collection_edge(self, client):
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_collection("edges", edge=True)
            assert result is True

    @pytest.mark.asyncio
    async def test_create_collection_conflict(self, client):
        mock_resp = MockResponse(409)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_collection("existing")
            assert result is True

    @pytest.mark.asyncio
    async def test_create_collection_failure(self, client):
        mock_resp = MockResponse(500, text_data="error")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_collection("fail")
            assert result is False

    @pytest.mark.asyncio
    async def test_has_collection_delegates(self, client):
        with patch.object(client, "collection_exists", new_callable=AsyncMock, return_value=True):
            result = await client.has_collection("users")
            assert result is True


# ---------------------------------------------------------------------------
# Transaction operations
# ---------------------------------------------------------------------------


class TestTransactionOperations:
    @pytest.mark.asyncio
    async def test_begin_transaction(self, client):
        result_data = {"result": {"id": "txn123"}}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            txn_id = await client.begin_transaction(["col1"], ["col2"])
            assert txn_id == "txn123"

    @pytest.mark.asyncio
    async def test_begin_transaction_failure(self, client):
        mock_resp = MockResponse(400, text_data="bad request")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Failed to begin transaction"):
                await client.begin_transaction(["col1"], ["col2"])

    @pytest.mark.asyncio
    async def test_commit_transaction(self, client):
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.put.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            await client.commit_transaction("txn123")

    @pytest.mark.asyncio
    async def test_commit_transaction_failure(self, client):
        mock_resp = MockResponse(400, text_data="bad")
        mock_session = MagicMock()
        mock_session.put.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Failed to commit transaction"):
                await client.commit_transaction("txn123")

    @pytest.mark.asyncio
    async def test_abort_transaction(self, client):
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            await client.abort_transaction("txn123")

    @pytest.mark.asyncio
    async def test_abort_transaction_failure(self, client):
        mock_resp = MockResponse(400, text_data="bad")
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Failed to abort transaction"):
                await client.abort_transaction("txn123")


# ---------------------------------------------------------------------------
# ensure_persistent_index
# ---------------------------------------------------------------------------


class TestEnsurePersistentIndex:
    @pytest.mark.asyncio
    async def test_ensure_index_success(self, client):
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.ensure_persistent_index("col", ["field1"])
            assert result is True

    @pytest.mark.asyncio
    async def test_ensure_index_failure(self, client):
        mock_resp = MockResponse(500, text_data="error")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.ensure_persistent_index("col", ["field1"])
            assert result is False

    @pytest.mark.asyncio
    async def test_ensure_index_exception(self, client):
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("error")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.ensure_persistent_index("col", ["field1"])
            assert result is False


# ---------------------------------------------------------------------------
# update_collection_schema
# ---------------------------------------------------------------------------


class TestUpdateCollectionSchema:
    @pytest.mark.asyncio
    async def test_no_schema_returns_true(self, client):
        result = await client.update_collection_schema("col", schema=None)
        assert result is True

    @pytest.mark.asyncio
    async def test_update_schema_success(self, client):
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.put.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_collection_schema("col", schema={"type": "object"})
            assert result is True

    @pytest.mark.asyncio
    async def test_update_schema_duplicate(self, client):
        error_data = {"errorMessage": "duplicate", "errorNum": ARANGO_ERROR_SCHEMA_DUPLICATE}
        mock_resp = MockResponse(400, json_data=error_data)
        mock_session = MagicMock()
        mock_session.put.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_collection_schema("col", schema={"type": "object"})
            assert result is True

    @pytest.mark.asyncio
    async def test_update_schema_failure(self, client):
        error_data = {"errorMessage": "other error", "errorNum": 9999}
        mock_resp = MockResponse(400, json_data=error_data)
        mock_session = MagicMock()
        mock_session.put.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_collection_schema("col", schema={"type": "object"})
            assert result is False

    @pytest.mark.asyncio
    async def test_update_schema_exception_with_duplicate(self, client):
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception(f"error {ARANGO_ERROR_SCHEMA_DUPLICATE}")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_collection_schema("col", schema={"type": "object"})
            assert result is True


# ---------------------------------------------------------------------------
# Graph operations
# ---------------------------------------------------------------------------


class TestGraphOperations:
    @pytest.mark.asyncio
    async def test_get_graph_found(self, client):
        graph_data = {"graph": {"name": "kg"}}
        mock_resp = MockResponse(200, json_data=graph_data)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_graph("kg")
            assert result == graph_data

    @pytest.mark.asyncio
    async def test_get_graph_not_found(self, client):
        mock_resp = MockResponse(404)
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_graph("missing")
            assert result is None

    @pytest.mark.asyncio
    async def test_has_graph_true(self, client):
        with patch.object(client, "get_graph", new_callable=AsyncMock, return_value={"name": "kg"}):
            assert await client.has_graph("kg") is True

    @pytest.mark.asyncio
    async def test_has_graph_false(self, client):
        with patch.object(client, "get_graph", new_callable=AsyncMock, return_value=None):
            assert await client.has_graph("missing") is False


# ---------------------------------------------------------------------------
# _handle_response helper
# ---------------------------------------------------------------------------


class TestHandleResponse:
    @pytest.mark.asyncio
    async def test_success_response(self, client):
        resp = MockResponse(200, json_data={"data": "ok"})
        async with resp as r:
            result = await client._handle_response(r, "test")
            assert result == {"data": "ok"}

    @pytest.mark.asyncio
    async def test_not_found_response(self, client):
        resp = MockResponse(404)
        async with resp as r:
            result = await client._handle_response(r, "test")
            assert result is None

    @pytest.mark.asyncio
    async def test_error_response(self, client):
        resp = MockResponse(500, text_data="server error")
        async with resp as r:
            result = await client._handle_response(r, "test")
            assert result is None


# ---------------------------------------------------------------------------
# Additional coverage: _get_session — RuntimeError (no running event loop)
# ---------------------------------------------------------------------------


class TestGetSessionNoRunningLoop:
    @pytest.mark.asyncio
    async def test_get_session_runtime_error_branch(self, client):
        """When asyncio.get_running_loop raises RuntimeError, current_loop is None."""
        with patch("app.services.graph_db.arango.arango_http_client.asyncio.get_running_loop", side_effect=RuntimeError):
            with patch("app.services.graph_db.arango.arango_http_client.aiohttp.ClientSession") as mock_cls:
                mock_session = MagicMock()
                mock_cls.return_value = mock_session
                session = await client._get_session()
                assert session is mock_session
                assert client._session_loop is None


# ---------------------------------------------------------------------------
# Additional coverage: _check_response_for_errors — edge cases
# ---------------------------------------------------------------------------


class TestCheckResponseForErrorsAdditional:
    def test_dict_with_error_false_no_raise(self, client):
        """Dict with error=False should not raise."""
        client._check_response_for_errors({"error": False, "result": "ok"}, "op")

    def test_list_item_non_dict_ignored(self, client):
        """Non-dict items in a list are ignored (no error check)."""
        data = ["plain_string", 42, None, {"_key": "a"}]
        client._check_response_for_errors(data, "test op")

    def test_dict_error_defaults_for_missing_fields(self, client):
        """When errorMessage and errorNum are missing, defaults are used."""
        data = {"error": True}
        with pytest.raises(Exception, match="Unknown error"):
            client._check_response_for_errors(data, "test op")

    def test_list_error_defaults_for_missing_fields(self, client):
        """List item with error=True but no errorMessage/errorNum uses defaults."""
        data = [{"error": True}]
        with pytest.raises(Exception, match="Unknown"):
            client._check_response_for_errors(data, "test op")

    def test_none_input_is_noop(self, client):
        """None input should not raise."""
        client._check_response_for_errors(None, "op")


# ---------------------------------------------------------------------------
# Additional coverage: create_document — accepted (202) status
# ---------------------------------------------------------------------------


class TestCreateDocumentAccepted:
    @pytest.mark.asyncio
    async def test_create_document_accepted_status(self, client):
        """Status 202 (ACCEPTED) is a success path for document creation."""
        result_data = {"_key": "doc1", "_id": "col/doc1"}
        mock_resp = MockResponse(202, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_document("col", {"_key": "doc1"})
            assert result == result_data


# ---------------------------------------------------------------------------
# Additional coverage: update_document — 201 and 202 statuses, txn_id
# ---------------------------------------------------------------------------


class TestUpdateDocumentAdditional:
    @pytest.mark.asyncio
    async def test_update_document_created_status(self, client):
        """Status 201 is also accepted for update."""
        result_data = {"_key": "doc1", "_rev": "_new"}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.patch.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_document("col", "doc1", {"field": "val"})
            assert result == result_data

    @pytest.mark.asyncio
    async def test_update_document_accepted_status(self, client):
        """Status 202 is also accepted for update."""
        result_data = {"_key": "doc1"}
        mock_resp = MockResponse(202, json_data=result_data)
        mock_session = MagicMock()
        mock_session.patch.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_document("col", "doc1", {"f": "v"})
            assert result == result_data

    @pytest.mark.asyncio
    async def test_update_document_with_txn(self, client):
        """Transaction ID is passed as header."""
        result_data = {"_key": "doc1"}
        mock_resp = MockResponse(200, json_data=result_data)
        mock_session = MagicMock()
        mock_session.patch.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_document("col", "doc1", {"f": "v"}, txn_id="txn99")
            assert result == result_data

    @pytest.mark.asyncio
    async def test_update_document_with_response_error(self, client):
        """Error in response body is caught even with 200 status."""
        error_data = {"error": True, "errorMessage": "bad", "errorNum": 999}
        mock_resp = MockResponse(200, json_data=error_data)
        mock_session = MagicMock()
        mock_session.patch.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception):
                await client.update_document("col", "doc1", {"f": "v"})

    @pytest.mark.asyncio
    async def test_update_document_exception(self, client):
        """Network exception propagates."""
        mock_session = MagicMock()
        mock_session.patch.side_effect = Exception("network error")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="network error"):
                await client.update_document("col", "doc1", {"f": "v"})


# ---------------------------------------------------------------------------
# Additional coverage: delete_document — accepted status, txn_id, response error
# ---------------------------------------------------------------------------


class TestDeleteDocumentAdditional:
    @pytest.mark.asyncio
    async def test_delete_document_accepted_status(self, client):
        """Status 202 (ACCEPTED) is a success for deletion."""
        mock_resp = MockResponse(202, json_data={"_key": "doc1"})
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.delete_document("col", "doc1")
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_document_with_txn(self, client):
        """Transaction ID is passed as header for deletion."""
        mock_resp = MockResponse(200, json_data={"_key": "doc1"})
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.delete_document("col", "doc1", txn_id="txn55")
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_document_response_error_in_body(self, client):
        """Error in delete response body is detected and raises."""
        error_data = {"error": True, "errorMessage": "fail", "errorNum": 1}
        mock_resp = MockResponse(200, json_data=error_data)
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception):
                await client.delete_document("col", "doc1")

    @pytest.mark.asyncio
    async def test_delete_document_content_type_error(self, client):
        """ContentTypeError when parsing JSON body is silently handled."""
        import aiohttp
        mock_resp = MockResponse(200)
        async def raise_content_type_error():
            raise aiohttp.ContentTypeError(MagicMock(), MagicMock())
        mock_resp.json = raise_content_type_error
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.delete_document("col", "doc1")
            assert result is True


# ---------------------------------------------------------------------------
# Additional coverage: execute_aql — cursor fetch error
# ---------------------------------------------------------------------------


class TestExecuteAqlAdditional:
    @pytest.mark.asyncio
    async def test_cursor_fetch_failure(self, client):
        """Error during cursor pagination raises."""
        page1 = {"result": [{"n": 1}], "hasMore": True, "id": "cursor123"}
        mock_post_resp = MockResponse(201, json_data=page1)
        mock_cursor_resp = MockResponse(500, text_data="cursor error")

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_resp
        mock_session.put.return_value = mock_cursor_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Cursor fetch failed"):
                await client.execute_aql("FOR d IN col RETURN d")

    @pytest.mark.asyncio
    async def test_cursor_response_body_error(self, client):
        """Error detected in cursor response body raises."""
        page1 = {"result": [{"n": 1}], "hasMore": True, "id": "cursor123"}
        page2_error = {"error": True, "errorMessage": "cursor expired", "errorNum": 1600, "result": [], "hasMore": False}
        mock_post_resp = MockResponse(201, json_data=page1)
        mock_cursor_resp = MockResponse(200, json_data=page2_error)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_resp
        mock_session.put.return_value = mock_cursor_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception):
                await client.execute_aql("FOR d IN col RETURN d")

    @pytest.mark.asyncio
    async def test_execute_aql_network_exception(self, client):
        """Network exception during query propagates."""
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("network down")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="network down"):
                await client.execute_aql("RETURN 1")


# ---------------------------------------------------------------------------
# Additional coverage: batch_insert — error items in response, overwrite_mode
# ---------------------------------------------------------------------------


class TestBatchInsertAdditional:
    @pytest.mark.asyncio
    async def test_batch_insert_with_error_items(self, client):
        """Items with error=True in batch response are counted as errors."""
        result_data = [
            {"_key": "a", "_rev": "_r1"},
            {"error": True, "errorMessage": "e", "errorNum": 1},
        ]
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            # _check_response_for_errors will raise because there's an error
            with pytest.raises(Exception, match="1 error"):
                await client.batch_insert_documents("col", [{"_key": "a"}, {"_key": "b"}])

    @pytest.mark.asyncio
    async def test_batch_insert_with_txn(self, client):
        """Transaction ID is passed for batch inserts."""
        result_data = [{"_key": "a", "_rev": "_r1"}]
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_insert_documents("col", [{"_key": "a"}], txn_id="txn99")
            assert result["created"] == 1

    @pytest.mark.asyncio
    async def test_batch_insert_network_exception(self, client):
        """Network exception during batch insert propagates."""
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("connection reset")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="connection reset"):
                await client.batch_insert_documents("col", [{"_key": "a"}])


# ---------------------------------------------------------------------------
# Additional coverage: batch_delete — txn_id, network exception
# ---------------------------------------------------------------------------


class TestBatchDeleteAdditional:
    @pytest.mark.asyncio
    async def test_batch_delete_with_txn(self, client):
        """Transaction ID is passed for batch deletes."""
        result_data = [{"_key": "a"}]
        mock_resp = MockResponse(200, json_data=result_data)
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.batch_delete_documents("col", ["a"], txn_id="txn42")
            assert result == 1

    @pytest.mark.asyncio
    async def test_batch_delete_network_exception(self, client):
        """Network exception during batch delete propagates."""
        mock_session = MagicMock()
        mock_session.delete.side_effect = Exception("timeout")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="timeout"):
                await client.batch_delete_documents("col", ["a"])


# ---------------------------------------------------------------------------
# Additional coverage: create_edge — error status, network exception
# ---------------------------------------------------------------------------


class TestCreateEdgeAdditional:
    @pytest.mark.asyncio
    async def test_create_edge_error_status(self, client):
        """Non-success status for edge creation raises."""
        mock_resp = MockResponse(500, text_data="server error")
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="Failed to create edge"):
                await client.create_edge("edges", "u/1", "r/2")

    @pytest.mark.asyncio
    async def test_create_edge_network_exception(self, client):
        """Network exception during edge creation propagates."""
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("network")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="network"):
                await client.create_edge("edges", "u/1", "r/2")

    @pytest.mark.asyncio
    async def test_create_edge_with_txn(self, client):
        """Transaction ID is passed for edge creation."""
        result_data = {"_key": "e1"}
        mock_resp = MockResponse(201, json_data=result_data)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_edge("edges", "u/1", "r/2", txn_id="txn7")
            assert result == result_data


# ---------------------------------------------------------------------------
# Additional coverage: delete_edge — exception branch
# ---------------------------------------------------------------------------


class TestDeleteEdgeAdditional:
    @pytest.mark.asyncio
    async def test_delete_edge_exception(self, client):
        """Exception during edge deletion returns False."""
        with patch.object(client, "execute_aql", new_callable=AsyncMock, side_effect=Exception("query failed")):
            result = await client.delete_edge("edges", "u/1", "r/2")
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_edge_with_txn(self, client):
        """Transaction ID is passed through edge deletion."""
        with patch.object(client, "execute_aql", new_callable=AsyncMock, return_value=["edge_key_1"]):
            mock_resp = MockResponse(200)
            mock_session = MagicMock()
            mock_session.delete.return_value = mock_resp

            with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
                result = await client.delete_edge("edges", "u/1", "r/2", txn_id="txn5")
                assert result is True

    @pytest.mark.asyncio
    async def test_delete_edge_non_success_status(self, client):
        """Non-success HTTP status returns False for edge deletion."""
        with patch.object(client, "execute_aql", new_callable=AsyncMock, return_value=["edge_key_1"]):
            mock_resp = MockResponse(500)
            mock_session = MagicMock()
            mock_session.delete.return_value = mock_resp

            with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
                result = await client.delete_edge("edges", "u/1", "r/2")
                assert result is False


# ---------------------------------------------------------------------------
# Additional coverage: create_collection — exception branch
# ---------------------------------------------------------------------------


class TestCreateCollectionAdditional:
    @pytest.mark.asyncio
    async def test_create_collection_exception(self, client):
        """Network exception during collection creation returns False."""
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("error")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.create_collection("fail")
            assert result is False


# ---------------------------------------------------------------------------
# Additional coverage: get_graph — error status, exception
# ---------------------------------------------------------------------------


class TestGetGraphAdditional:
    @pytest.mark.asyncio
    async def test_get_graph_error_status(self, client):
        """Non-200 non-404 status returns None."""
        mock_resp = MockResponse(500, text_data="server error")
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_graph("graph1")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_graph_exception(self, client):
        """Network exception returns None."""
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("network")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.get_graph("graph1")
            assert result is None


# ---------------------------------------------------------------------------
# Additional coverage: transaction — exception branches, 204 status
# ---------------------------------------------------------------------------


class TestTransactionAdditional:
    @pytest.mark.asyncio
    async def test_begin_transaction_network_exception(self, client):
        """Network exception during begin_transaction propagates."""
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("network")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="network"):
                await client.begin_transaction(["col1"], ["col2"])

    @pytest.mark.asyncio
    async def test_commit_transaction_204_success(self, client):
        """204 No Content is a valid success status for commit."""
        mock_resp = MockResponse(204)
        mock_session = MagicMock()
        mock_session.put.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            await client.commit_transaction("txn123")

    @pytest.mark.asyncio
    async def test_commit_transaction_network_exception(self, client):
        """Network exception during commit propagates."""
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("network")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="network"):
                await client.commit_transaction("txn123")

    @pytest.mark.asyncio
    async def test_abort_transaction_204_success(self, client):
        """204 No Content is a valid success status for abort."""
        mock_resp = MockResponse(204)
        mock_session = MagicMock()
        mock_session.delete.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            await client.abort_transaction("txn123")

    @pytest.mark.asyncio
    async def test_abort_transaction_network_exception(self, client):
        """Network exception during abort propagates."""
        mock_session = MagicMock()
        mock_session.delete.side_effect = Exception("network")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            with pytest.raises(Exception, match="network"):
                await client.abort_transaction("txn123")


# ---------------------------------------------------------------------------
# Additional coverage: update_collection_schema — exception with "duplicate" text
# ---------------------------------------------------------------------------


class TestUpdateCollectionSchemaAdditional:
    @pytest.mark.asyncio
    async def test_update_schema_exception_with_duplicate_text(self, client):
        """Exception message containing 'duplicate' returns True."""
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("schema duplicate detected")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_collection_schema("col", schema={"type": "object"})
            assert result is True

    @pytest.mark.asyncio
    async def test_update_schema_generic_exception(self, client):
        """Generic exception returns False."""
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("something else entirely")

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            result = await client.update_collection_schema("col", schema={"type": "object"})
            assert result is False

    @pytest.mark.asyncio
    async def test_update_schema_empty_dict(self, client):
        """Empty dict schema (falsy) returns True without making a request."""
        result = await client.update_collection_schema("col", schema={})
        assert result is True


# ---------------------------------------------------------------------------
# Additional coverage: _handle_response — 201 and 202 statuses
# ---------------------------------------------------------------------------


class TestHandleResponseAdditional:
    @pytest.mark.asyncio
    async def test_created_response(self, client):
        resp = MockResponse(201, json_data={"created": True})
        async with resp as r:
            result = await client._handle_response(r, "test")
            assert result == {"created": True}

    @pytest.mark.asyncio
    async def test_accepted_response(self, client):
        resp = MockResponse(202, json_data={"accepted": True})
        async with resp as r:
            result = await client._handle_response(r, "test")
            assert result == {"accepted": True}


# ---------------------------------------------------------------------------
# Additional coverage: create_database — 200 OK
# ---------------------------------------------------------------------------


class TestCreateDatabaseAdditional:
    @pytest.mark.asyncio
    async def test_create_database_200_ok(self, client):
        """200 OK is also a success for database creation."""
        mock_resp = MockResponse(200)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=mock_session):
            assert await client.create_database("new_db") is True
