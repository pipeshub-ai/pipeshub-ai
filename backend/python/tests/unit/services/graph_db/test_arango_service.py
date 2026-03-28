"""
Unit tests for ArangoService (app/services/graph_db/arango/arango.py).

Tests cover:
- ArangoService.__init__: attribute initialization
- ArangoService.create: factory method delegates to __create_arango_client
- connect: DB creation if not exists, missing config, client not initialized
- disconnect: cleanup, error handling
- get_service_name / get_service_client
- create_collection: already exists, new, db not connected, exception
- create_graph: success, db not connected
- upsert_document: success, no _key, db not connected, execute_query returns None
- upsert_document_with_merge: merge/replace/keep strategies, invalid strategy, empty result
- batch_upsert_documents: success, empty list, missing _key, invalid strategy
- get_document: success, not found, db not connected
- delete_document: success, not found, db not connected
- execute_query: success, with bind vars, db not connected, exception
- create_index: persistent/hash/skiplist/ttl/unsupported, index exists, db not connected
- Stub methods: create_node, create_edge, delete_graph, delete_node, delete_edge,
  get_node, get_edge, get_nodes, get_edges
"""

import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from app.services.graph_db.arango.arango import ArangoService
from app.services.graph_db.arango.config import ArangoConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def arango_config():
    return ArangoConfig(
        url="http://localhost:8529",
        db="test_db",
        username="root",
        password="secret",
    )


@pytest.fixture
def service(mock_logger, arango_config):
    """Create an ArangoService instance without calling factory method."""
    svc = ArangoService(mock_logger, arango_config)
    return svc


@pytest.fixture
def connected_service(service):
    """Service with a mock client and db already set."""
    service.client = MagicMock()
    service.db = MagicMock()
    return service


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestArangoServiceInit:
    def test_attributes_initialized(self, mock_logger, arango_config):
        svc = ArangoService(mock_logger, arango_config)
        assert svc.logger is mock_logger
        assert svc.config_service is arango_config
        assert svc.client is None
        assert svc.db is None


# ---------------------------------------------------------------------------
# create (factory)
# ---------------------------------------------------------------------------


class TestArangoServiceCreate:
    @pytest.mark.asyncio
    @patch("app.services.graph_db.arango.arango.ArangoClient")
    async def test_create_initializes_client(self, mock_arango_client_cls, mock_logger, arango_config):
        mock_client_instance = MagicMock()
        mock_arango_client_cls.return_value = mock_client_instance

        svc = await ArangoService.create(mock_logger, arango_config)

        assert isinstance(svc, ArangoService)
        assert svc.client is mock_client_instance
        mock_arango_client_cls.assert_called_once_with(hosts="http://localhost:8529")

    @pytest.mark.asyncio
    @patch("app.services.graph_db.arango.arango.ArangoClient")
    async def test_create_with_config_service(self, mock_arango_client_cls, mock_logger):
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value={"url": "http://remote:8529"})

        mock_client_instance = MagicMock()
        mock_arango_client_cls.return_value = mock_client_instance

        svc = await ArangoService.create(mock_logger, mock_config_service)
        assert svc.client is mock_client_instance


# ---------------------------------------------------------------------------
# get_service_name / get_service_client
# ---------------------------------------------------------------------------


class TestServiceMetadata:
    @pytest.mark.asyncio
    async def test_get_service_name(self, service):
        assert await service.get_service_name() == "arango"

    @pytest.mark.asyncio
    async def test_get_service_client_none(self, service):
        assert await service.get_service_client() is None

    @pytest.mark.asyncio
    async def test_get_service_client_with_client(self, connected_service):
        result = await connected_service.get_service_client()
        assert result is connected_service.client


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_db_if_not_exists(self, service, arango_config):
        mock_client = MagicMock()
        service.client = mock_client

        mock_sys_db = MagicMock()
        mock_sys_db.has_database.return_value = False
        mock_target_db = MagicMock()

        mock_client.db.side_effect = [mock_sys_db, mock_target_db]

        result = await service.connect()

        assert result is True
        assert service.db is mock_target_db
        mock_sys_db.create_database.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_db_already_exists(self, service, arango_config):
        mock_client = MagicMock()
        service.client = mock_client

        mock_sys_db = MagicMock()
        mock_sys_db.has_database.return_value = True
        mock_target_db = MagicMock()

        mock_client.db.side_effect = [mock_sys_db, mock_target_db]

        result = await service.connect()

        assert result is True
        assert service.db is mock_target_db
        mock_sys_db.create_database.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_no_client(self, service):
        service.client = None
        result = await service.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_invalid_config(self, mock_logger):
        """Test connect with config_service returning None."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value=None)

        svc = ArangoService(mock_logger, mock_config_service)
        svc.client = MagicMock()

        result = await svc.connect()
        assert result is False
        assert svc.client is None
        assert svc.db is None

    @pytest.mark.asyncio
    async def test_connect_missing_required_values(self, mock_logger):
        """Test connect when config is missing required fields."""
        config = ArangoConfig(url="http://localhost:8529", db="", username="root", password="pass")
        svc = ArangoService(mock_logger, config)
        svc.client = MagicMock()

        result = await svc.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_exception_during_db_creation(self, service):
        mock_client = MagicMock()
        service.client = mock_client
        mock_client.db.side_effect = Exception("Connection refused")

        result = await service.connect()

        assert result is False
        assert service.client is None
        assert service.db is None

    @pytest.mark.asyncio
    async def test_connect_with_config_service_instance(self, mock_logger):
        """Test connect path using ConfigurationService (not ArangoConfig)."""
        mock_config_service = AsyncMock()
        mock_config_service.get_config = AsyncMock(return_value={
            "url": "http://localhost:8529",
            "username": "root",
            "password": "secret",
            "db": "test_db",
        })

        svc = ArangoService(mock_logger, mock_config_service)
        mock_client = MagicMock()
        svc.client = mock_client

        mock_sys_db = MagicMock()
        mock_sys_db.has_database.return_value = True
        mock_target_db = MagicMock()
        mock_client.db.side_effect = [mock_sys_db, mock_target_db]

        result = await svc.connect()
        assert result is True
        mock_config_service.get_config.assert_awaited_once()


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_with_client(self, connected_service):
        result = await connected_service.disconnect()
        assert result is True
        assert connected_service.client is None
        assert connected_service.db is None

    @pytest.mark.asyncio
    async def test_disconnect_without_client(self, service):
        result = await service.disconnect()
        assert result is True
        assert service.client is None

    @pytest.mark.asyncio
    async def test_disconnect_close_raises(self, connected_service):
        connected_service.client.close.side_effect = Exception("close error")
        result = await connected_service.disconnect()
        assert result is False


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------


class TestCreateCollection:
    @pytest.mark.asyncio
    async def test_create_collection_already_exists(self, connected_service):
        connected_service.db.has_collection.return_value = True
        result = await connected_service.create_collection("test_col")
        assert result is True
        connected_service.db.create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_collection_new(self, connected_service):
        connected_service.db.has_collection.return_value = False
        result = await connected_service.create_collection("test_col")
        assert result is True
        connected_service.db.create_collection.assert_called_once_with("test_col")

    @pytest.mark.asyncio
    async def test_create_collection_db_not_connected(self, service):
        result = await service.create_collection("test_col")
        assert result is False

    @pytest.mark.asyncio
    async def test_create_collection_exception(self, connected_service):
        connected_service.db.has_collection.side_effect = Exception("db error")
        result = await connected_service.create_collection("test_col")
        assert result is False


# ---------------------------------------------------------------------------
# create_graph
# ---------------------------------------------------------------------------


class TestCreateGraph:
    @pytest.mark.asyncio
    async def test_create_graph_success(self, connected_service):
        result = await connected_service.create_graph("knowledge_graph")
        assert result is True
        connected_service.db.create_graph.assert_called_once_with("knowledge_graph")

    @pytest.mark.asyncio
    async def test_create_graph_db_not_connected(self, service):
        result = await service.create_graph("knowledge_graph")
        assert result is False


# ---------------------------------------------------------------------------
# upsert_document
# ---------------------------------------------------------------------------


class TestUpsertDocument:
    @pytest.mark.asyncio
    async def test_upsert_document_success(self, connected_service):
        # Mock execute_query to return an empty list (successful UPSERT without RETURN)
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = []
            doc = {"_key": "doc1", "name": "test"}
            result = await connected_service.upsert_document("col", doc)
            assert result is True
            mock_eq.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_document_no_key(self, connected_service):
        result = await connected_service.upsert_document("col", {"name": "test"})
        assert result is False

    @pytest.mark.asyncio
    async def test_upsert_document_db_not_connected(self, service):
        result = await service.upsert_document("col", {"_key": "doc1"})
        assert result is False

    @pytest.mark.asyncio
    async def test_upsert_document_execute_query_returns_none(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = None
            result = await connected_service.upsert_document("col", {"_key": "doc1"})
            assert result is False

    @pytest.mark.asyncio
    async def test_upsert_document_exception(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.side_effect = Exception("query error")
            result = await connected_service.upsert_document("col", {"_key": "doc1"})
            assert result is False


# ---------------------------------------------------------------------------
# upsert_document_with_merge
# ---------------------------------------------------------------------------


class TestUpsertDocumentWithMerge:
    @pytest.mark.asyncio
    async def test_merge_strategy(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = [{"_key": "doc1", "name": "merged"}]
            result = await connected_service.upsert_document_with_merge(
                "col", {"_key": "doc1", "name": "test"}, merge_strategy="merge"
            )
            assert result == {"_key": "doc1", "name": "merged"}
            # Verify MERGE(OLD, @document) is in the query
            call_args = mock_eq.call_args
            assert "MERGE(OLD, @document)" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_replace_strategy(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = [{"_key": "doc1"}]
            result = await connected_service.upsert_document_with_merge(
                "col", {"_key": "doc1"}, merge_strategy="replace"
            )
            assert result is not None
            call_args = mock_eq.call_args
            assert "UPDATE @document" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_keep_strategy(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = [{"_key": "doc1"}]
            result = await connected_service.upsert_document_with_merge(
                "col", {"_key": "doc1"}, merge_strategy="keep"
            )
            assert result is not None
            call_args = mock_eq.call_args
            assert "UPDATE OLD" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_invalid_strategy(self, connected_service):
        result = await connected_service.upsert_document_with_merge(
            "col", {"_key": "doc1"}, merge_strategy="invalid"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_key(self, connected_service):
        result = await connected_service.upsert_document_with_merge("col", {"name": "test"})
        assert result is None

    @pytest.mark.asyncio
    async def test_db_not_connected(self, service):
        result = await service.upsert_document_with_merge("col", {"_key": "doc1"})
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = []
            result = await connected_service.upsert_document_with_merge(
                "col", {"_key": "doc1"}
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_none_result(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = None
            result = await connected_service.upsert_document_with_merge(
                "col", {"_key": "doc1"}
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.side_effect = Exception("error")
            result = await connected_service.upsert_document_with_merge(
                "col", {"_key": "doc1"}
            )
            assert result is None


# ---------------------------------------------------------------------------
# batch_upsert_documents
# ---------------------------------------------------------------------------


class TestBatchUpsertDocuments:
    @pytest.mark.asyncio
    async def test_batch_upsert_success_merge(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = []
            docs = [{"_key": "d1"}, {"_key": "d2"}]
            result = await connected_service.batch_upsert_documents("col", docs, "merge")
            assert result is True
            call_args = mock_eq.call_args
            assert "MERGE(OLD, doc)" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_batch_upsert_replace(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = []
            result = await connected_service.batch_upsert_documents(
                "col", [{"_key": "d1"}], "replace"
            )
            assert result is True
            call_args = mock_eq.call_args
            assert "UPDATE doc" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_batch_upsert_keep(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = []
            result = await connected_service.batch_upsert_documents(
                "col", [{"_key": "d1"}], "keep"
            )
            assert result is True
            call_args = mock_eq.call_args
            assert "UPDATE OLD" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_batch_upsert_empty_list(self, connected_service):
        result = await connected_service.batch_upsert_documents("col", [])
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_upsert_missing_key(self, connected_service):
        result = await connected_service.batch_upsert_documents("col", [{"name": "no_key"}])
        assert result is False

    @pytest.mark.asyncio
    async def test_batch_upsert_invalid_strategy(self, connected_service):
        result = await connected_service.batch_upsert_documents(
            "col", [{"_key": "d1"}], "wrong"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_batch_upsert_db_not_connected(self, service):
        result = await service.batch_upsert_documents("col", [{"_key": "d1"}])
        assert result is False

    @pytest.mark.asyncio
    async def test_batch_upsert_returns_none(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.return_value = None
            result = await connected_service.batch_upsert_documents(
                "col", [{"_key": "d1"}]
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self, connected_service):
        with patch.object(connected_service, "execute_query", new_callable=AsyncMock) as mock_eq:
            mock_eq.side_effect = Exception("fail")
            result = await connected_service.batch_upsert_documents(
                "col", [{"_key": "d1"}]
            )
            assert result is False


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


class TestGetDocument:
    @pytest.mark.asyncio
    async def test_get_document_success(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"_key": "doc1", "name": "test"}
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.get_document("col", "doc1")
        assert result == {"_key": "doc1", "name": "test"}

    @pytest.mark.asyncio
    async def test_get_document_not_found(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.get.side_effect = Exception("Document not found")
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.get_document("col", "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_db_not_connected(self, service):
        result = await service.get_document("col", "doc1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_collection_error(self, connected_service):
        connected_service.db.collection.side_effect = Exception("collection error")
        result = await connected_service.get_document("col", "doc1")
        assert result is None


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    @pytest.mark.asyncio
    async def test_delete_document_success(self, connected_service):
        mock_collection = MagicMock()
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.delete_document("col", "doc1")
        assert result is True
        mock_collection.delete.assert_called_once_with("doc1")

    @pytest.mark.asyncio
    async def test_delete_document_not_found(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.delete.side_effect = Exception("not found")
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.delete_document("col", "doc1")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_document_db_not_connected(self, service):
        result = await service.delete_document("col", "doc1")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_document_collection_error(self, connected_service):
        connected_service.db.collection.side_effect = Exception("error")
        result = await connected_service.delete_document("col", "doc1")
        assert result is False


# ---------------------------------------------------------------------------
# execute_query
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    @pytest.mark.asyncio
    async def test_execute_query_success(self, connected_service):
        mock_cursor = iter([{"name": "a"}, {"name": "b"}])
        connected_service.db.aql.execute.return_value = mock_cursor

        result = await connected_service.execute_query("FOR d IN col RETURN d")
        assert result == [{"name": "a"}, {"name": "b"}]

    @pytest.mark.asyncio
    async def test_execute_query_with_bind_vars(self, connected_service):
        mock_cursor = iter([{"name": "match"}])
        connected_service.db.aql.execute.return_value = mock_cursor

        result = await connected_service.execute_query(
            "FOR d IN col FILTER d.name == @name RETURN d",
            {"name": "match"}
        )
        assert result == [{"name": "match"}]
        connected_service.db.aql.execute.assert_called_once_with(
            "FOR d IN col FILTER d.name == @name RETURN d",
            bind_vars={"name": "match"}
        )

    @pytest.mark.asyncio
    async def test_execute_query_no_bind_vars_defaults_empty(self, connected_service):
        connected_service.db.aql.execute.return_value = iter([])
        await connected_service.execute_query("RETURN 1")
        connected_service.db.aql.execute.assert_called_once_with(
            "RETURN 1", bind_vars={}
        )

    @pytest.mark.asyncio
    async def test_execute_query_empty_result(self, connected_service):
        connected_service.db.aql.execute.return_value = iter([])
        result = await connected_service.execute_query("RETURN 1")
        assert result == []

    @pytest.mark.asyncio
    async def test_execute_query_db_not_connected(self, service):
        result = await service.execute_query("RETURN 1")
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_query_exception(self, connected_service):
        connected_service.db.aql.execute.side_effect = Exception("query error")
        result = await connected_service.execute_query("BAD QUERY")
        assert result is None


# ---------------------------------------------------------------------------
# create_index
# ---------------------------------------------------------------------------


class TestCreateIndex:
    @pytest.mark.asyncio
    async def test_create_persistent_index(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.indexes.return_value = []
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.create_index("col", ["field1"], "persistent")
        assert result is True
        mock_collection.ensure_persistent_index.assert_called_once_with(["field1"])

    @pytest.mark.asyncio
    async def test_create_hash_index(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.indexes.return_value = []
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.create_index("col", ["field1"], "hash")
        assert result is True
        mock_collection.ensure_hash_index.assert_called_once_with(["field1"])

    @pytest.mark.asyncio
    async def test_create_skiplist_index(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.indexes.return_value = []
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.create_index("col", ["field1"], "skiplist")
        assert result is True
        mock_collection.ensure_skiplist_index.assert_called_once_with(["field1"])

    @pytest.mark.asyncio
    async def test_create_ttl_index(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.indexes.return_value = []
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.create_index("col", ["field1"], "ttl")
        assert result is True
        mock_collection.ensure_ttl_index.assert_called_once_with(["field1"])

    @pytest.mark.asyncio
    async def test_create_unsupported_index(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.indexes.return_value = []
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.create_index("col", ["field1"], "fulltext")
        assert result is False

    @pytest.mark.asyncio
    async def test_index_already_exists(self, connected_service):
        mock_collection = MagicMock()
        mock_collection.indexes.return_value = [
            {"fields": ["field1"], "type": "persistent"}
        ]
        connected_service.db.collection.return_value = mock_collection

        result = await connected_service.create_index("col", ["field1"], "persistent")
        assert result is True
        mock_collection.ensure_persistent_index.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_index_db_not_connected(self, service):
        result = await service.create_index("col", ["field1"])
        assert result is False

    @pytest.mark.asyncio
    async def test_create_index_exception(self, connected_service):
        connected_service.db.collection.side_effect = Exception("error")
        result = await connected_service.create_index("col", ["field1"])
        assert result is False


# ---------------------------------------------------------------------------
# Stub methods (always return fixed values)
# ---------------------------------------------------------------------------


class TestStubMethods:
    @pytest.mark.asyncio
    async def test_create_node_returns_false(self, service):
        assert await service.create_node("type", "id") is False

    @pytest.mark.asyncio
    async def test_create_edge_returns_false(self, service):
        assert await service.create_edge("type", "from", "to") is False

    @pytest.mark.asyncio
    async def test_delete_graph_returns_false(self, service):
        assert await service.delete_graph() is False

    @pytest.mark.asyncio
    async def test_delete_node_returns_false(self, service):
        assert await service.delete_node("type", "id") is False

    @pytest.mark.asyncio
    async def test_delete_edge_returns_false(self, service):
        assert await service.delete_edge("type", "from", "to") is False

    @pytest.mark.asyncio
    async def test_get_node_returns_none(self, service):
        assert await service.get_node("type", "id") is None

    @pytest.mark.asyncio
    async def test_get_edge_returns_none(self, service):
        assert await service.get_edge("type", "from", "to") is None

    @pytest.mark.asyncio
    async def test_get_nodes_returns_empty(self, service):
        assert await service.get_nodes("type") == []

    @pytest.mark.asyncio
    async def test_get_edges_returns_empty(self, service):
        assert await service.get_edges("type") == []
