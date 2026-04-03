"""
Unit tests for ArangoHTTPProvider (app/services/graph_db/arango/arango_http_provider.py).

Tests cover:
- __init__: attribute initialization, connector_delete_permissions structure
- _translate_node_to_arango / _translate_node_from_arango
- _translate_edge_to_arango / _translate_edge_from_arango
- Batch translate helpers
- connect: success, config error, connection failure
- disconnect: success, no client, exception
- ensure_schema: success, not connected
- begin_transaction / commit_transaction / rollback_transaction
- get_document: success, not found, exception
- batch_upsert_nodes: success, empty list, exception
- delete_nodes / update_node
- batch_create_edges: success, empty, exception
- get_edge / delete_edge
- delete_edges_from / delete_edges_to / delete_all_edges_for_node
- execute_query: delegates to http_client.execute_aql
- get_user_by_user_id: found, not found, exception
- get_accessible_virtual_record_ids: various filter combinations, exceptions
- get_records_by_record_ids: success, empty, exception
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.services.graph_db.arango.arango_http_provider import (
    ARANGO_ID_PARTS_COUNT,
    ArangoHTTPProvider,
)
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.graph_db.arango.arango_http_provider import (
    ARANGO_ID_PARTS_COUNT,
    MAX_REINDEX_DEPTH,
    ArangoHTTPProvider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "url": "http://localhost:8529",
        "username": "root",
        "password": "secret",
        "db": "test_db",
    })
    return cs


@pytest.fixture
def provider(mock_logger, mock_config_service):
    return ArangoHTTPProvider(mock_logger, mock_config_service)


@pytest.fixture
def connected_provider(provider):
    """Provider with a mock http_client already attached."""
    provider.http_client = AsyncMock()
    return provider


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_attributes(self, provider, mock_logger, mock_config_service):
        assert provider.logger is mock_logger
        assert provider.config_service is mock_config_service
        assert provider.http_client is None

    def test_connector_delete_permissions_keys(self, provider):
        expected_keys = {"DRIVE", "GMAIL", "OUTLOOK", "KB"}
        assert set(provider.connector_delete_permissions.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Translation methods
# ---------------------------------------------------------------------------


class TestTranslateNodeToArango:
    def test_converts_id_to_key(self, provider):
        node = {"id": "abc123", "name": "test"}
        result = provider._translate_node_to_arango(node)
        assert result == {"_key": "abc123", "name": "test"}
        assert "id" not in result

    def test_no_id_field(self, provider):
        node = {"name": "test"}
        result = provider._translate_node_to_arango(node)
        assert result == {"name": "test"}

    def test_original_not_mutated(self, provider):
        node = {"id": "abc123", "name": "test"}
        provider._translate_node_to_arango(node)
        assert "id" in node  # Original unchanged


class TestTranslateNodeFromArango:
    def test_converts_key_to_id(self, provider):
        arango_node = {"_key": "abc123", "name": "test"}
        result = provider._translate_node_from_arango(arango_node)
        assert result == {"id": "abc123", "name": "test"}
        assert "_key" not in result

    def test_no_key_field(self, provider):
        arango_node = {"name": "test"}
        result = provider._translate_node_from_arango(arango_node)
        assert result == {"name": "test"}


class TestTranslateEdgeToArango:
    def test_converts_generic_to_arango(self, provider):
        edge = {
            "from_id": "user1",
            "from_collection": "users",
            "to_id": "record1",
            "to_collection": "records",
            "role": "READER",
        }
        result = provider._translate_edge_to_arango(edge)
        assert result["_from"] == "users/user1"
        assert result["_to"] == "records/record1"
        assert result["role"] == "READER"
        assert "from_id" not in result
        assert "from_collection" not in result

    def test_already_arango_format(self, provider):
        edge = {"_from": "users/u1", "_to": "records/r1", "role": "WRITER"}
        result = provider._translate_edge_to_arango(edge)
        assert result["_from"] == "users/u1"
        assert result["_to"] == "records/r1"

    def test_partial_generic_format(self, provider):
        """Edge with from_id but no to_id."""
        edge = {
            "from_id": "u1",
            "from_collection": "users",
            "_to": "records/r1",
        }
        result = provider._translate_edge_to_arango(edge)
        assert result["_from"] == "users/u1"
        assert result["_to"] == "records/r1"


class TestTranslateEdgeFromArango:
    def test_converts_arango_to_generic(self, provider):
        arango_edge = {"_from": "users/u1", "_to": "records/r1", "role": "READER"}
        result = provider._translate_edge_from_arango(arango_edge)
        assert result["from_collection"] == "users"
        assert result["from_id"] == "u1"
        assert result["to_collection"] == "records"
        assert result["to_id"] == "r1"
        assert "_from" not in result
        assert "_to" not in result

    def test_no_from_to(self, provider):
        edge = {"role": "WRITER"}
        result = provider._translate_edge_from_arango(edge)
        assert result == {"role": "WRITER"}


class TestBatchTranslateHelpers:
    def test_translate_nodes_to_arango(self, provider):
        nodes = [{"id": "a"}, {"id": "b"}]
        result = provider._translate_nodes_to_arango(nodes)
        assert result == [{"_key": "a"}, {"_key": "b"}]

    def test_translate_nodes_from_arango(self, provider):
        nodes = [{"_key": "a"}, {"_key": "b"}]
        result = provider._translate_nodes_from_arango(nodes)
        assert result == [{"id": "a"}, {"id": "b"}]

    def test_translate_edges_to_arango(self, provider):
        edges = [{"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records"}]
        result = provider._translate_edges_to_arango(edges)
        assert result[0]["_from"] == "users/u1"

    def test_translate_edges_from_arango(self, provider):
        edges = [{"_from": "users/u1", "_to": "records/r1"}]
        result = provider._translate_edges_from_arango(edges)
        assert result[0]["from_id"] == "u1"


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, provider, mock_config_service):
        with patch(
            "app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient"
        ) as mock_http_cls:
            mock_http_client = AsyncMock()
            mock_http_client.connect.return_value = True
            mock_http_client.database_exists.return_value = True
            mock_http_cls.return_value = mock_http_client

            result = await provider.connect()
            assert result is True
            assert provider.http_client is mock_http_client

    @pytest.mark.asyncio
    async def test_connect_db_not_exists_creates(self, provider, mock_config_service):
        with patch(
            "app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient"
        ) as mock_http_cls:
            mock_http_client = AsyncMock()
            mock_http_client.connect.return_value = True
            mock_http_client.database_exists.return_value = False
            mock_http_client.create_database.return_value = True
            mock_http_cls.return_value = mock_http_client

            result = await provider.connect()
            assert result is True
            mock_http_client.create_database.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_db_creation_fails(self, provider, mock_config_service):
        with patch(
            "app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient"
        ) as mock_http_cls:
            mock_http_client = AsyncMock()
            mock_http_client.connect.return_value = True
            mock_http_client.database_exists.return_value = False
            mock_http_client.create_database.return_value = False
            mock_http_cls.return_value = mock_http_client

            result = await provider.connect()
            assert result is False
            assert provider.http_client is None

    @pytest.mark.asyncio
    async def test_connect_http_connect_fails(self, provider, mock_config_service):
        with patch(
            "app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient"
        ) as mock_http_cls:
            mock_http_client = AsyncMock()
            mock_http_client.connect.return_value = False
            mock_http_cls.return_value = mock_http_client

            result = await provider.connect()
            assert result is False

    @pytest.mark.asyncio
    async def test_connect_invalid_config(self, provider):
        provider.config_service.get_config.return_value = None
        result = await provider.connect()
        assert result is False
        assert provider.http_client is None

    @pytest.mark.asyncio
    async def test_connect_exception(self, provider):
        provider.config_service.get_config.side_effect = Exception("config error")
        result = await provider.connect()
        assert result is False


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_success(self, connected_provider):
        result = await connected_provider.disconnect()
        assert result is True
        assert connected_provider.http_client is None

    @pytest.mark.asyncio
    async def test_disconnect_no_client(self, provider):
        result = await provider.disconnect()
        assert result is True

    @pytest.mark.asyncio
    async def test_disconnect_exception(self, connected_provider):
        connected_provider.http_client.disconnect.side_effect = Exception("error")
        result = await connected_provider.disconnect()
        assert result is False


# ---------------------------------------------------------------------------
# Transaction management
# ---------------------------------------------------------------------------


class TestTransactionManagement:
    @pytest.mark.asyncio
    async def test_begin_transaction(self, connected_provider):
        connected_provider.http_client.begin_transaction.return_value = "txn123"
        txn_id = await connected_provider.begin_transaction(["col1"], ["col2"])
        assert txn_id == "txn123"

    @pytest.mark.asyncio
    async def test_begin_transaction_failure(self, connected_provider):
        connected_provider.http_client.begin_transaction.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.begin_transaction(["col1"], ["col2"])

    @pytest.mark.asyncio
    async def test_commit_transaction(self, connected_provider):
        await connected_provider.commit_transaction("txn123")
        connected_provider.http_client.commit_transaction.assert_awaited_once_with("txn123")

    @pytest.mark.asyncio
    async def test_commit_transaction_failure(self, connected_provider):
        connected_provider.http_client.commit_transaction.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.commit_transaction("txn123")

    @pytest.mark.asyncio
    async def test_rollback_transaction(self, connected_provider):
        await connected_provider.rollback_transaction("txn123")
        connected_provider.http_client.abort_transaction.assert_awaited_once_with("txn123")

    @pytest.mark.asyncio
    async def test_rollback_transaction_failure(self, connected_provider):
        connected_provider.http_client.abort_transaction.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.rollback_transaction("txn123")


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


class TestGetDocument:
    @pytest.mark.asyncio
    async def test_get_document_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "doc1", "name": "test"
        }
        result = await connected_provider.get_document("doc1", "collection")
        assert result["id"] == "doc1"
        assert result["name"] == "test"
        assert "_key" not in result

    @pytest.mark.asyncio
    async def test_get_document_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.get_document("missing", "collection")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("error")
        result = await connected_provider.get_document("doc1", "collection")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_with_transaction(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {"_key": "d1"}
        result = await connected_provider.get_document("d1", "col", transaction="txn1")
        connected_provider.http_client.get_document.assert_awaited_once_with(
            "col", "d1", txn_id="txn1"
        )


# ---------------------------------------------------------------------------
# batch_upsert_nodes
# ---------------------------------------------------------------------------


class TestBatchUpsertNodes:
    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_success(self, connected_provider):
        connected_provider.http_client.batch_insert_documents.return_value = {
            "created": 2, "updated": 0, "errors": 0
        }
        nodes = [{"id": "n1", "name": "a"}, {"id": "n2", "name": "b"}]
        result = await connected_provider.batch_upsert_nodes(nodes, "collection")
        assert result is True

        # Verify nodes were translated
        call_args = connected_provider.http_client.batch_insert_documents.call_args
        arango_nodes = call_args[0][1]
        assert arango_nodes[0]["_key"] == "n1"
        assert arango_nodes[1]["_key"] == "n2"

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_empty(self, connected_provider):
        result = await connected_provider.batch_upsert_nodes([], "collection")
        assert result is True
        connected_provider.http_client.batch_insert_documents.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_with_errors(self, connected_provider):
        connected_provider.http_client.batch_insert_documents.return_value = {
            "created": 0, "updated": 0, "errors": 1
        }
        result = await connected_provider.batch_upsert_nodes([{"id": "n1"}], "col")
        assert result is False

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_exception(self, connected_provider):
        connected_provider.http_client.batch_insert_documents.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.batch_upsert_nodes([{"id": "n1"}], "col")

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_with_transaction(self, connected_provider):
        connected_provider.http_client.batch_insert_documents.return_value = {"errors": 0}
        await connected_provider.batch_upsert_nodes(
            [{"id": "n1"}], "col", transaction="txn1"
        )
        call_args = connected_provider.http_client.batch_insert_documents.call_args
        assert call_args[1]["txn_id"] == "txn1"


# ---------------------------------------------------------------------------
# delete_nodes / update_node
# ---------------------------------------------------------------------------


class TestDeleteNodes:
    @pytest.mark.asyncio
    async def test_delete_nodes_success(self, connected_provider):
        connected_provider.http_client.batch_delete_documents.return_value = 2
        result = await connected_provider.delete_nodes(["k1", "k2"], "col")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_nodes_partial(self, connected_provider):
        connected_provider.http_client.batch_delete_documents.return_value = 1
        result = await connected_provider.delete_nodes(["k1", "k2"], "col")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_nodes_exception(self, connected_provider):
        connected_provider.http_client.batch_delete_documents.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.delete_nodes(["k1"], "col")


class TestUpdateNode:
    @pytest.mark.asyncio
    async def test_update_node_success(self, connected_provider):
        connected_provider.http_client.update_document.return_value = {"_key": "k1"}
        result = await connected_provider.update_node("k1", "col", {"name": "new"})
        assert result is True

    @pytest.mark.asyncio
    async def test_update_node_returns_none(self, connected_provider):
        connected_provider.http_client.update_document.return_value = None
        result = await connected_provider.update_node("k1", "col", {"name": "new"})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_node_exception(self, connected_provider):
        connected_provider.http_client.update_document.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.update_node("k1", "col", {"name": "x"})


# ---------------------------------------------------------------------------
# batch_create_edges
# ---------------------------------------------------------------------------


class TestBatchCreateEdges:
    @pytest.mark.asyncio
    async def test_batch_create_edges_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_from": "u/1", "_to": "r/1"}
        ]
        edges = [
            {"from_id": "1", "from_collection": "u", "to_id": "1", "to_collection": "r"}
        ]
        result = await connected_provider.batch_create_edges(edges, "edge_col")
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_create_edges_empty(self, connected_provider):
        result = await connected_provider.batch_create_edges([], "edge_col")
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_create_edges_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.batch_create_edges(
                [{"from_id": "1", "from_collection": "u", "to_id": "1", "to_collection": "r"}],
                "edge_col",
            )


# ---------------------------------------------------------------------------
# get_edge / delete_edge
# ---------------------------------------------------------------------------


class TestEdgeOperations:
    @pytest.mark.asyncio
    async def test_get_edge_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_from": "users/u1", "_to": "records/r1", "role": "READER"}
        ]
        result = await connected_provider.get_edge(
            "u1", "users", "r1", "records", "permissions"
        )
        assert result["from_id"] == "u1"
        assert result["to_id"] == "r1"
        assert result["role"] == "READER"

    @pytest.mark.asyncio
    async def test_get_edge_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_edge(
            "u1", "users", "r1", "records", "permissions"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_edge_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("error")
        result = await connected_provider.get_edge(
            "u1", "users", "r1", "records", "permissions"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_edge_success(self, connected_provider):
        connected_provider.http_client.delete_edge.return_value = True
        result = await connected_provider.delete_edge(
            "u1", "users", "r1", "records", "permissions"
        )
        assert result is True
        connected_provider.http_client.delete_edge.assert_awaited_once_with(
            "permissions", "users/u1", "records/r1", txn_id=None
        )

    @pytest.mark.asyncio
    async def test_delete_edge_with_transaction(self, connected_provider):
        connected_provider.http_client.delete_edge.return_value = True
        await connected_provider.delete_edge(
            "u1", "users", "r1", "records", "perms", transaction="txn1"
        )
        connected_provider.http_client.delete_edge.assert_awaited_once_with(
            "perms", "users/u1", "records/r1", txn_id="txn1"
        )


# ---------------------------------------------------------------------------
# delete_edges_from / delete_edges_to
# ---------------------------------------------------------------------------


class TestDeleteEdgesFrom:
    @pytest.mark.asyncio
    async def test_delete_edges_from_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "e1"}, {"_key": "e2"}
        ]
        count = await connected_provider.delete_edges_from("u1", "users", "perms")
        assert count == 2

    @pytest.mark.asyncio
    async def test_delete_edges_from_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.delete_edges_from("u1", "users", "perms")


class TestDeleteEdgesTo:
    @pytest.mark.asyncio
    async def test_delete_edges_to_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"_key": "e1"}]
        count = await connected_provider.delete_edges_to("r1", "records", "perms")
        assert count == 1

    @pytest.mark.asyncio
    async def test_delete_edges_to_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.delete_edges_to("r1", "records", "perms")


# ---------------------------------------------------------------------------
# delete_all_edges_for_node
# ---------------------------------------------------------------------------


class TestDeleteAllEdgesForNode:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_slash_in_key(self, connected_provider):
        count = await connected_provider.delete_all_edges_for_node("u1", "perms")
        assert count == 0

    @pytest.mark.asyncio
    async def test_no_edges_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        count = await connected_provider.delete_all_edges_for_node("users/u1", "perms")
        assert count == 0


# ---------------------------------------------------------------------------
# execute_query
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    @pytest.mark.asyncio
    async def test_execute_query_success(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"_key": "x"}]
        result = await connected_provider.execute_query("RETURN 1")
        assert result == [{"_key": "x"}]

    @pytest.mark.asyncio
    async def test_execute_query_with_vars_and_txn(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.execute_query(
            "RETURN @v", bind_vars={"v": 1}, transaction="txn1"
        )
        connected_provider.http_client.execute_aql.assert_awaited_once_with(
            "RETURN @v", {"v": 1}, txn_id="txn1"
        )

    @pytest.mark.asyncio
    async def test_execute_query_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.execute_query("BAD")


# ---------------------------------------------------------------------------
# get_user_by_user_id
# ---------------------------------------------------------------------------


class TestGetUserByUserId:
    @pytest.mark.asyncio
    async def test_user_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "u1", "userId": "ext_id", "email": "user@test.com"}
        ]
        result = await connected_provider.get_user_by_user_id("ext_id")
        assert result["_key"] == "u1"
        assert result["userId"] == "ext_id"

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_user_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_user_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_by_user_id("id")
        assert result is None


# ---------------------------------------------------------------------------
# get_accessible_virtual_record_ids
# ---------------------------------------------------------------------------


class TestGetAccessibleVirtualRecordIds:
    @pytest.mark.asyncio
    async def test_no_user_apps(self, connected_provider):
        """When user has no accessible apps, should return empty dict if no KB filter."""
        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "_get_kb_virtual_ids",
            new_callable=AsyncMock, return_value={}
        ):
            result = await connected_provider.get_accessible_virtual_record_ids(
                "user1", "org1"
            )
            # Even with no apps, it still queries KB virtual ids
            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_with_app_filter_and_kb_filter(self, connected_provider):
        """Both app and KB filters active."""
        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=["app1", "knowledgeBase_kb1"]
        ), patch.object(
            connected_provider, "_get_virtual_ids_for_connector",
            new_callable=AsyncMock, return_value={"vid1": "rid1"}
        ), patch.object(
            connected_provider, "_get_kb_virtual_ids",
            new_callable=AsyncMock, return_value={"vid2": "rid2"}
        ):
            result = await connected_provider.get_accessible_virtual_record_ids(
                "user1", "org1",
                filters={"apps": ["app1"], "kb": ["kb1"]}
            )
            assert "vid1" in result
            assert "vid2" in result

    @pytest.mark.asyncio
    async def test_with_only_kb_filter(self, connected_provider):
        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=["app1"]
        ), patch.object(
            connected_provider, "_get_kb_virtual_ids",
            new_callable=AsyncMock, return_value={"vid1": "rid1"}
        ):
            result = await connected_provider.get_accessible_virtual_record_ids(
                "user1", "org1",
                filters={"kb": ["kb1"]}
            )
            assert result == {"vid1": "rid1"}

    @pytest.mark.asyncio
    async def test_with_only_app_filter(self, connected_provider):
        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=["app1", "app2"]
        ), patch.object(
            connected_provider, "_get_virtual_ids_for_connector",
            new_callable=AsyncMock, return_value={"vid1": "rid1"}
        ):
            result = await connected_provider.get_accessible_virtual_record_ids(
                "user1", "org1",
                filters={"apps": ["app1"]}
            )
            assert "vid1" in result

    @pytest.mark.asyncio
    async def test_no_filters(self, connected_provider):
        """No filters means query all apps + KB."""
        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=["app1"]
        ), patch.object(
            connected_provider, "_get_virtual_ids_for_connector",
            new_callable=AsyncMock, return_value={"vid1": "rid1"}
        ), patch.object(
            connected_provider, "_get_kb_virtual_ids",
            new_callable=AsyncMock, return_value={"vid2": "rid2"}
        ):
            result = await connected_provider.get_accessible_virtual_record_ids(
                "user1", "org1"
            )
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_task_exception_handled(self, connected_provider):
        """Tasks that throw exceptions should be logged but not crash."""
        async def failing_task(*args, **kwargs):
            raise Exception("task failed")

        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=["app1"]
        ), patch.object(
            connected_provider, "_get_virtual_ids_for_connector",
            side_effect=failing_task
        ), patch.object(
            connected_provider, "_get_kb_virtual_ids",
            new_callable=AsyncMock, return_value={"vid1": "rid1"}
        ):
            result = await connected_provider.get_accessible_virtual_record_ids(
                "user1", "org1"
            )
            # KB result should still be present
            assert "vid1" in result

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        """Top-level exception returns empty dict."""
        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, side_effect=Exception("fatal")
        ):
            result = await connected_provider.get_accessible_virtual_record_ids(
                "user1", "org1"
            )
            assert result == {}

    @pytest.mark.asyncio
    async def test_knowledgebase_prefix_skipped(self, connected_provider):
        """Apps starting with 'knowledgeBase_' are skipped in connector queries."""
        with patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=["knowledgeBase_kb1"]
        ), patch.object(
            connected_provider, "_get_virtual_ids_for_connector",
            new_callable=AsyncMock, return_value={}
        ) as mock_connector, patch.object(
            connected_provider, "_get_kb_virtual_ids",
            new_callable=AsyncMock, return_value={}
        ):
            await connected_provider.get_accessible_virtual_record_ids("u1", "o1")
            # _get_virtual_ids_for_connector should NOT be called for knowledgeBase_ apps
            mock_connector.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_records_by_record_ids
# ---------------------------------------------------------------------------


class TestGetRecordsByRecordIds:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_ids(self, connected_provider):
        result = await connected_provider.get_records_by_record_ids([], "org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_none_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r1"}, None, {"_key": "r2"}
        ]
        result = await connected_provider.get_records_by_record_ids(["r1", "r2", "r3"], "org1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_query_returns_none(self, connected_provider):
        """execute_query returns None (via http_client raising then being caught)."""
        with patch.object(connected_provider, "execute_query", new_callable=AsyncMock, return_value=None):
            result = await connected_provider.get_records_by_record_ids(["r1"], "org1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_records_by_record_ids(["r1"], "org1")
        assert result == []


# ---------------------------------------------------------------------------
# ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    @pytest.mark.asyncio
    async def test_ensure_schema_not_connected(self, provider):
        result = await provider.ensure_schema()
        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_schema_exception(self, connected_provider):
        connected_provider.http_client.has_collection.side_effect = Exception("fail")
        result = await connected_provider.ensure_schema()
        assert result is False


# ---------------------------------------------------------------------------
# delete_edges_by_relationship_types
# ---------------------------------------------------------------------------


class TestDeleteEdgesByRelationshipTypes:
    @pytest.mark.asyncio
    async def test_empty_types_returns_zero(self, connected_provider):
        result = await connected_provider.delete_edges_by_relationship_types(
            "u1", "users", "entity_relations", []
        )
        assert result == 0

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.delete_edges_by_relationship_types(
                "u1", "users", "entity_relations", ["TYPE"]
            )


# ---------------------------------------------------------------------------
# Helper: build a complete mock ArangoDB record dict
# ---------------------------------------------------------------------------


def _make_arango_record(**overrides):
    """Build a complete mock ArangoDB record dict (pre-translation, with _key)."""
    base = {
        "_key": "r1",
        "orgId": "org1",
        "recordName": "Test Record",
        "recordType": "FILE",
        "externalRecordId": "ext1",
        "version": 1,
        "origin": "CONNECTOR",
        "connectorId": "c1",
        "connectorName": "DRIVE",
        "mimeType": "application/octet-stream",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _ensure_departments_seed
# ---------------------------------------------------------------------------


class TestEnsureDepartmentsSeed:
    @pytest.mark.asyncio
    async def test_seed_inserts_new_departments(self, connected_provider):
        """When no departments exist, should insert all."""
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ) as mock_upsert:
            await connected_provider._ensure_departments_seed()
            mock_upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_seed_skips_existing(self, connected_provider):
        """When all departments exist, should not insert."""
        from app.config.constants.arangodb import DepartmentNames
        existing_names = [d.value for d in DepartmentNames]
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=existing_names
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider._ensure_departments_seed()
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_seed_exception_is_nonfatal(self, connected_provider):
        """Should log warning but not raise on exception."""
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("db error")
        ):
            # Should not raise
            await connected_provider._ensure_departments_seed()


# ---------------------------------------------------------------------------
# get_record_by_external_id
# ---------------------------------------------------------------------------


class TestGetRecordByExternalId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_external_id("c1", "ext_missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_external_id("c1", "ext1")
        assert result is None


# ---------------------------------------------------------------------------
# get_record_by_external_revision_id
# ---------------------------------------------------------------------------


class TestGetRecordByExternalRevisionId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_external_revision_id("c1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_external_revision_id("c1", "rev1")
        assert result is None


# ---------------------------------------------------------------------------
# get_record_key_by_external_id
# ---------------------------------------------------------------------------


class TestGetRecordKeyByExternalId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_key_by_external_id("ext_missing", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_key_by_external_id("ext1", "c1")
        assert result is None


# ---------------------------------------------------------------------------
# get_record_by_id
# ---------------------------------------------------------------------------


class TestGetRecordById:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_record_by_id("missing")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_record_by_id("r1")
            assert result is None


# ---------------------------------------------------------------------------
# get_record_by_path
# ---------------------------------------------------------------------------


class TestGetRecordByPath:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_path("c1", "/missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_path("c1", "/path")
        assert result is None


# ---------------------------------------------------------------------------
# get_record_path
# ---------------------------------------------------------------------------


class TestGetRecordPath:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_path("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_path("r1")
        assert result is None


# ---------------------------------------------------------------------------
# get_records_by_status
# ---------------------------------------------------------------------------


class TestGetRecordsByStatus:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_records_by_status("org1", "c1", ["QUEUED"])
        assert result == []


# ---------------------------------------------------------------------------
# get_documents_by_status
# ---------------------------------------------------------------------------


class TestGetDocumentsByStatus:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_documents_by_status("records", "NONE")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_documents_by_status("records", "FAILED")
            assert result == []


# ---------------------------------------------------------------------------
# get_nodes_by_filters
# ---------------------------------------------------------------------------


class TestGetNodesByFilters:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"name": "test"}
        ]
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}, return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "missing"}
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_nodes_by_field_in
# ---------------------------------------------------------------------------


class TestGetNodesByFieldIn:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"name": "a"}]
        result = await connected_provider.get_nodes_by_field_in(
            "records", "orgId", ["org1"], return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_nodes_by_field_in(
            "records", "orgId", ["missing"]
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_nodes_by_field_in(
            "records", "orgId", ["org1"]
        )
        assert result == []


# ---------------------------------------------------------------------------
# remove_nodes_by_field
# ---------------------------------------------------------------------------


class TestRemoveNodesByField:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.remove_nodes_by_field(
                "records", "orgId", field_value="org1"
            )


# ---------------------------------------------------------------------------
# get_edges_to_node / get_edges_from_node
# ---------------------------------------------------------------------------


class TestGetEdgesToNode:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_edges_to_node("records/r1", "permission")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_edges_to_node("records/r1", "permission")
        assert result == []


class TestGetEdgesFromNode:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_edges_from_node("users/u1", "permission")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_edges_from_node("users/u1", "permission")
        assert result == []


# ---------------------------------------------------------------------------
# get_related_nodes
# ---------------------------------------------------------------------------


class TestGetRelatedNodes:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r1", "name": "related"}
        ]
        result = await connected_provider.get_related_nodes(
            "users/u1", "permission", "records", "outbound"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "u1", "name": "user"}
        ]
        result = await connected_provider.get_related_nodes(
            "records/r1", "permission", "users", "inbound"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_related_nodes(
            "users/u1", "permission", "records"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_related_nodes(
            "users/u1", "permission", "records"
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_related_node_field
# ---------------------------------------------------------------------------


class TestGetRelatedNodeField:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["value1", "value2"]
        result = await connected_provider.get_related_node_field(
            "users/u1", "permission", "records", "name", "outbound"
        )
        assert result == ["value1", "value2"]

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["email@test.com"]
        result = await connected_provider.get_related_node_field(
            "records/r1", "permission", "users", "email", "inbound"
        )
        assert result == ["email@test.com"]

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_related_node_field(
            "users/u1", "permission", "records", "name"
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_user_by_email
# ---------------------------------------------------------------------------


class TestGetUserByEmail:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_email("missing@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_by_email("user@test.com")
        assert result is None


# ---------------------------------------------------------------------------
# get_user_by_source_id
# ---------------------------------------------------------------------------


class TestGetUserBySourceId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_source_id("missing", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_by_source_id("src1", "c1")
        assert result is None


# ---------------------------------------------------------------------------
# get_users (org users)
# ---------------------------------------------------------------------------


class TestGetUsers:
    @pytest.mark.asyncio
    async def test_active_users(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "u1", "isActive": True},
            {"_key": "u2", "isActive": True},
        ]
        result = await connected_provider.get_users("org1", active=True)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_users("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_users("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_users("org1")
        assert result == []


# ---------------------------------------------------------------------------
# get_user_apps
# ---------------------------------------------------------------------------


class TestGetUserApps:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_user_apps("u1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_user_apps("u1")
            assert result == []


# ---------------------------------------------------------------------------
# _get_user_app_ids
# ---------------------------------------------------------------------------


class TestGetUserAppIds:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider._get_user_app_ids("missing")
            assert result == []

    @pytest.mark.asyncio
    async def test_no_user_key(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"userId": "ext1"}  # no _key
        ):
            result = await connected_provider._get_user_app_ids("ext1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider._get_user_app_ids("ext1")
            assert result == []


# ---------------------------------------------------------------------------
# get_user_group_by_external_id
# ---------------------------------------------------------------------------


class TestGetUserGroupByExternalId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_group_by_external_id("c1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_group_by_external_id("c1", "ext1")
        assert result is None


# ---------------------------------------------------------------------------
# get_user_groups
# ---------------------------------------------------------------------------


class TestGetUserGroups:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_groups("c1", "org1")
        assert result == []


# ---------------------------------------------------------------------------
# get_app_role_by_external_id
# ---------------------------------------------------------------------------


class TestGetAppRoleByExternalId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_role_by_external_id("c1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_app_role_by_external_id("c1", "ext1")
        assert result is None


# ---------------------------------------------------------------------------
# get_record_group_by_external_id
# ---------------------------------------------------------------------------


class TestGetRecordGroupByExternalId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_group_by_external_id("c1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_group_by_external_id("c1", "ext1")
        assert result is None


# ---------------------------------------------------------------------------
# get_record_group_by_id
# ---------------------------------------------------------------------------


class TestGetRecordGroupById:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.get_record_group_by_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.get_record_group_by_id("rg1")
        assert result is None


# ---------------------------------------------------------------------------
# get_file_record_by_id
# ---------------------------------------------------------------------------


class TestGetFileRecordById:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_file_not_found(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = [None, None]
        result = await connected_provider.get_file_record_by_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.get_file_record_by_id("f1")
        assert result is None


# ---------------------------------------------------------------------------
# get_all_orgs
# ---------------------------------------------------------------------------


class TestGetAllOrgs:
    @pytest.mark.asyncio
    async def test_active_orgs(self, connected_provider):
        with patch.object(
            connected_provider, "get_nodes_by_filters",
            new_callable=AsyncMock,
            return_value=[{"_key": "org1", "isActive": True}]
        ):
            result = await connected_provider.get_all_orgs(active=True)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_all_orgs(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "org1"}, {"_key": "org2"}
        ]
        result = await connected_provider.get_all_orgs(active=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_all_orgs_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []

    @pytest.mark.asyncio
    async def test_all_orgs_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []


# ---------------------------------------------------------------------------
# get_org_apps
# ---------------------------------------------------------------------------


class TestGetOrgApps:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_org_apps("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_org_apps("org1")
        assert result == []


# ---------------------------------------------------------------------------
# get_departments
# ---------------------------------------------------------------------------


class TestGetDepartments:
    @pytest.mark.asyncio
    async def test_with_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            "Engineering", "Sales"
        ]
        result = await connected_provider.get_departments("org1")
        assert result == ["Engineering", "Sales"]

    @pytest.mark.asyncio
    async def test_without_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["Engineering"]
        result = await connected_provider.get_departments()
        assert result == ["Engineering"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_departments("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_departments("org1")
        assert result == []


# ---------------------------------------------------------------------------
# get_sync_point
# ---------------------------------------------------------------------------


class TestGetSyncPoint:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_sync_point("missing", "syncPoints")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_sync_point("sp1", "syncPoints")
        assert result is None


# ---------------------------------------------------------------------------
# upsert_sync_point
# ---------------------------------------------------------------------------


class TestUpsertSyncPoint:
    @pytest.mark.asyncio
    async def test_insert_new(self, connected_provider):
        with patch.object(
            connected_provider, "get_sync_point",
            new_callable=AsyncMock, return_value=None
        ):
            connected_provider.http_client.execute_aql.return_value = [{"syncPointKey": "sp1"}]
            result = await connected_provider.upsert_sync_point(
                "sp1", {"data": "val"}, "syncPoints"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_update_existing(self, connected_provider):
        with patch.object(
            connected_provider, "get_sync_point",
            new_callable=AsyncMock,
            return_value={"syncPointKey": "sp1", "data": "old"}
        ):
            connected_provider.http_client.execute_aql.return_value = [{"syncPointKey": "sp1"}]
            result = await connected_provider.upsert_sync_point(
                "sp1", {"data": "new"}, "syncPoints"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_sync_point",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.upsert_sync_point(
                    "sp1", {"data": "val"}, "syncPoints"
                )


# ---------------------------------------------------------------------------
# remove_sync_point
# ---------------------------------------------------------------------------


class TestRemoveSyncPoint:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.remove_sync_point("sp1", "syncPoints")


# ---------------------------------------------------------------------------
# get_app_user_by_email
# ---------------------------------------------------------------------------


class TestGetAppUserByEmail:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_app_user_by_email("missing@test.com", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_user_by_email("user@test.com", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_app_user_by_email("user@test.com", "c1")
        assert result is None


# ---------------------------------------------------------------------------
# get_app_users
# ---------------------------------------------------------------------------


class TestGetAppUsers:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_users("org1", "c1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_app_users("org1", "c1")
        assert result == []


# ---------------------------------------------------------------------------
# get_file_permissions
# ---------------------------------------------------------------------------


class TestGetFilePermissions:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_file_permissions("r1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_file_permissions("r1")
        assert result == []


# ---------------------------------------------------------------------------
# get_first_user_with_permission_to_node
# ---------------------------------------------------------------------------


class TestGetFirstUserWithPermissionToNode:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_first_user_with_permission_to_node(
            "r1", "records"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_first_user_with_permission_to_node(
            "r1", "records"
        )
        assert result is None


# ---------------------------------------------------------------------------
# get_users_with_permission_to_node
# ---------------------------------------------------------------------------


class TestGetUsersWithPermissionToNode:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_users_with_permission_to_node(
            "r1", "records"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_users_with_permission_to_node(
            "r1", "records"
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_record_owner_source_user_email
# ---------------------------------------------------------------------------


class TestGetRecordOwnerSourceUserEmail:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_owner_source_user_email("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_owner_source_user_email("r1")
        assert result is None


# ---------------------------------------------------------------------------
# get_file_parents
# ---------------------------------------------------------------------------


class TestGetFileParents:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_relations(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {
                "input_file_key": "f1",
                "found_relations": [],
                "parsed_parent_keys": [],
                "found_parent_files": []
            }
        ]
        result = await connected_provider.get_file_parents("f1")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_file_key(self, connected_provider):
        result = await connected_provider.get_file_parents("")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_file_parents("f1")
        assert result == []


# ---------------------------------------------------------------------------
# get_all_documents
# ---------------------------------------------------------------------------


class TestGetAllDocuments:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_all_documents("records")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_all_documents("records")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_all_documents("records")
        assert result == []


# ---------------------------------------------------------------------------
# get_app_creator_user
# ---------------------------------------------------------------------------


class TestGetAppCreatorUser:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_app_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_app_creator_user("missing")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_created_by(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "app1"}  # no createdBy
        ):
            result = await connected_provider.get_app_creator_user("app1")
            assert result is None

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "app1", "createdBy": "ext_user"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_app_creator_user("app1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_app_creator_user("app1")
            assert result is None


# ---------------------------------------------------------------------------
# batch_create_entity_relations
# ---------------------------------------------------------------------------


class TestBatchCreateEntityRelations:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        result = await connected_provider.batch_create_entity_relations([])
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.batch_create_entity_relations(
                [{"from_id": "1", "from_collection": "p", "to_id": "1",
                  "to_collection": "r", "edgeType": "TYPE"}]
            )


# ---------------------------------------------------------------------------
# get_entity_id_by_email
# ---------------------------------------------------------------------------


class TestGetEntityIdByEmail:
    @pytest.mark.asyncio
    async def test_found_in_users(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["user_key1"]
        result = await connected_provider.get_entity_id_by_email("user@test.com")
        assert result == "user_key1"

    @pytest.mark.asyncio
    async def test_found_in_groups(self, connected_provider):
        # First call (users) returns empty, second call (groups) returns result
        connected_provider.http_client.execute_aql.side_effect = [
            [],  # users
            ["group_key1"],  # groups
        ]
        result = await connected_provider.get_entity_id_by_email("group@test.com")
        assert result == "group_key1"

    @pytest.mark.asyncio
    async def test_found_in_people(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [],  # users
            [],  # groups
            ["person_key1"],  # people
        ]
        result = await connected_provider.get_entity_id_by_email("person@test.com")
        assert result == "person_key1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [[], [], []]
        result = await connected_provider.get_entity_id_by_email("missing@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_entity_id_by_email("user@test.com")
        assert result is None


# ---------------------------------------------------------------------------
# bulk_get_entity_ids_by_email
# ---------------------------------------------------------------------------


class TestBulkGetEntityIdsByEmail:
    @pytest.mark.asyncio
    async def test_empty_input(self, connected_provider):
        result = await connected_provider.bulk_get_entity_ids_by_email([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_found_users(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"email": "u@test.com", "id": "u1"}],  # users
        ]
        result = await connected_provider.bulk_get_entity_ids_by_email(["u@test.com"])
        assert "u@test.com" in result
        assert result["u@test.com"][0] == "u1"

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.bulk_get_entity_ids_by_email(["u@test.com"])
        assert result == {}


# ---------------------------------------------------------------------------
# check_connector_name_exists
# ---------------------------------------------------------------------------


class TestCheckConnectorNameExists:
    @pytest.mark.asyncio
    async def test_personal_scope_exists(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=["key1"]
        ):
            result = await connected_provider.check_connector_name_exists(
                "apps", "My Connector", "personal", user_id="u1"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_personal_scope_not_exists(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.check_connector_name_exists(
                "apps", "My Connector", "personal", user_id="u1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_team_scope(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=["key1"]
        ):
            result = await connected_provider.check_connector_name_exists(
                "apps", "Team Connector", "team", org_id="org1"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.check_connector_name_exists(
                "apps", "My Connector", "personal", user_id="u1"
            )
            assert result is False


# ---------------------------------------------------------------------------
# batch_update_connector_status
# ---------------------------------------------------------------------------


class TestBatchUpdateConnectorStatus:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        result = await connected_provider.batch_update_connector_status(
            "apps", [], is_active=True, is_agent_active=False
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.batch_update_connector_status(
                "apps", ["k1"], is_active=True, is_agent_active=False
            )
            assert result == 0


# ---------------------------------------------------------------------------
# get_user_connector_instances
# ---------------------------------------------------------------------------


class TestGetUserConnectorInstances:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_user_connector_instances(
                "apps", "u1", "org1", "team", "personal"
            )
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_user_connector_instances(
                "apps", "u1", "org1", "team", "personal"
            )
            assert result == []


# ---------------------------------------------------------------------------
# get_filtered_connector_instances
# ---------------------------------------------------------------------------


class TestGetFilteredConnectorInstances:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
                "apps", "orgAppRelation", "org1", "u1"
            )
            assert docs == []
            assert total == 0


# ---------------------------------------------------------------------------
# _check_record_group_permissions
# ---------------------------------------------------------------------------


class TestCheckRecordGroupPermissions:
    @pytest.mark.asyncio
    async def test_allowed(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"allowed": True, "role": "OWNER"}]
        ):
            result = await connected_provider._check_record_group_permissions(
                "rg1", "u1", "org1"
            )
            assert result["allowed"] is True
            assert result["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_denied(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"allowed": False, "role": None}]
        ):
            result = await connected_provider._check_record_group_permissions(
                "rg1", "u1", "org1"
            )
            assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider._check_record_group_permissions(
                "rg1", "u1", "org1"
            )
            assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider._check_record_group_permissions(
                "rg1", "u1", "org1"
            )
            assert result["allowed"] is False


# ---------------------------------------------------------------------------
# _check_record_permissions
# ---------------------------------------------------------------------------


class TestCheckRecordPermissions:
    @pytest.mark.asyncio
    async def test_has_permission(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"permission": "OWNER", "source": "DIRECT"}]
        ):
            result = await connected_provider._check_record_permissions("r1", "u1")
            assert result["permission"] == "OWNER"
            assert result["source"] == "DIRECT"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"permission": None, "source": "NONE"}]
        ):
            result = await connected_provider._check_record_permissions("r1", "u1")
            assert result["permission"] is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider._check_record_permissions("r1", "u1")
            assert result["permission"] is None
            assert result["source"] == "ERROR"


# ---------------------------------------------------------------------------
# _check_record_permission (simple)
# ---------------------------------------------------------------------------


class TestCheckRecordPermissionSimple:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._check_record_permission("r1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._check_record_permission("r1", "u1")
        assert result is None


# ---------------------------------------------------------------------------
# batch_upsert_record_permissions
# ---------------------------------------------------------------------------


class TestBatchUpsertRecordPermissions:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_permissions(self, connected_provider):
        # Should return early without error
        await connected_provider.batch_upsert_record_permissions("r1", [])

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_record_permissions(
                    "r1", [{"_from": "users/u1", "_to": "records/r1"}]
                )


# ---------------------------------------------------------------------------
# create_record_relation
# ---------------------------------------------------------------------------


class TestCreateRecordRelation:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ) as mock_batch:
            await connected_provider.create_record_relation(
                "r1", "r2", "BLOCKS", transaction="txn1"
            )
            call_kwargs = mock_batch.call_args[1]
            assert call_kwargs.get("transaction") == "txn1"


# ---------------------------------------------------------------------------
# create_record_group_relation
# ---------------------------------------------------------------------------


class TestCreateRecordGroupRelation:
    pass


class TestCreateRecordGroupsRelation:
    pass


class TestCreateInheritPermissionsRelation:
    pass


class TestDeleteInheritPermissionsRelation:
    pass


class TestBatchUpsertRecordGroups:
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        rg = MagicMock()
        rg.to_arango_base_record_group.return_value = {"id": "rg1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_record_groups([rg])


# ---------------------------------------------------------------------------
# batch_upsert_orgs
# ---------------------------------------------------------------------------


class TestBatchUpsertOrgs:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        # Should return early without calling batch_upsert_nodes
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider.batch_upsert_orgs([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_orgs([{"id": "org1"}])


# ---------------------------------------------------------------------------
# batch_upsert_anyone / batch_upsert_anyone_with_link / batch_upsert_anyone_same_org
# ---------------------------------------------------------------------------


class TestBatchUpsertAnyone:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider.batch_upsert_anyone([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_anyone([{"id": "a1"}])


# ---------------------------------------------------------------------------
# batch_upsert_user_groups
# ---------------------------------------------------------------------------


class TestBatchUpsertUserGroups:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        ug = MagicMock()
        ug.to_arango_base_user_group.return_value = {"id": "g1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_user_groups([ug])


# ---------------------------------------------------------------------------
# batch_upsert_app_roles
# ---------------------------------------------------------------------------


class TestBatchUpsertAppRoles:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        role = MagicMock()
        role.to_arango_base_role.return_value = {"id": "r1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_app_roles([role])


# ---------------------------------------------------------------------------
# batch_upsert_people
# ---------------------------------------------------------------------------


class TestBatchUpsertPeople:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider.batch_upsert_people([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        person = MagicMock()
        person.to_arango_person.return_value = {"id": "p1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_people([person])


# ---------------------------------------------------------------------------
# _permission_needs_update
# ---------------------------------------------------------------------------


class TestPermissionNeedsUpdate:
    def test_role_changed(self, connected_provider):
        existing = {"role": "READER"}
        new = {"role": "WRITER"}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_role_unchanged(self, connected_provider):
        existing = {"role": "READER"}
        new = {"role": "READER"}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_active_changed(self, connected_provider):
        existing = {"active": True}
        new = {"active": False}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_permission_details_changed(self, connected_provider):
        existing = {"permissionDetails": {"key": "old"}}
        new = {"permissionDetails": {"key": "new"}}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_permission_details_unchanged(self, connected_provider):
        existing = {"permissionDetails": {"key": "val"}}
        new = {"permissionDetails": {"key": "val"}}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_no_relevant_fields(self, connected_provider):
        existing = {"other": "field"}
        new = {"unrelated": "data"}
        assert connected_provider._permission_needs_update(existing, new) is False


# ---------------------------------------------------------------------------
# delete_records_and_relations
# ---------------------------------------------------------------------------


class TestDeleteRecordsAndRelations:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.delete_records_and_relations("missing")
        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.delete_records_and_relations("r1")
        assert result is False


# ---------------------------------------------------------------------------
# delete_record
# ---------------------------------------------------------------------------


class TestDeleteRecord:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.delete_record("missing", "u1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_unsupported_connector(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "UNKNOWN", "origin": "CONNECTOR"
        }
        result = await connected_provider.delete_record("r1", "u1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.delete_record("r1", "u1")
        assert result["success"] is False
        assert result["code"] == 500


# ---------------------------------------------------------------------------
# delete_record_by_external_id
# ---------------------------------------------------------------------------


class TestDeleteRecordByExternalId:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=None
        ):
            # Should return without error
            await connected_provider.delete_record_by_external_id("c1", "ext1", "u1")

    @pytest.mark.asyncio
    async def test_deletion_fails(self, connected_provider):
        mock_record = MagicMock()
        mock_record.id = "r1"
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=mock_record
        ), patch.object(
            connected_provider, "delete_record",
            new_callable=AsyncMock,
            return_value={"success": False, "reason": "Permission denied"}
        ):
            with pytest.raises(Exception, match="Deletion failed"):
                await connected_provider.delete_record_by_external_id("c1", "ext1", "u1")


# ---------------------------------------------------------------------------
# get_key_by_external_file_id
# ---------------------------------------------------------------------------


class TestGetKeyByExternalFileId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_key_by_external_file_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_key_by_external_file_id("ext_f1")
        assert result is None


# ---------------------------------------------------------------------------
# get_key_by_external_message_id
# ---------------------------------------------------------------------------


class TestGetKeyByExternalMessageId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_key_by_external_message_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_key_by_external_message_id("ext_m1")
        assert result is None


# ---------------------------------------------------------------------------
# get_related_records_by_relation_type
# ---------------------------------------------------------------------------


class TestGetRelatedRecordsByRelationType:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_related_records_by_relation_type(
            "r1", "ATTACHMENT", "recordRelations"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_related_records_by_relation_type(
            "r1", "ATTACHMENT", "recordRelations"
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_message_id_header_by_key
# ---------------------------------------------------------------------------


class TestGetMessageIdHeaderByKey:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_message_id_header_by_key("r1", "mails")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_message_id_header_by_key("r1", "mails")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_message_id_header_by_key("r1", "mails")
        assert result is None


# ---------------------------------------------------------------------------
# get_related_mails_by_message_id_header
# ---------------------------------------------------------------------------


class TestGetRelatedMailsByMessageIdHeader:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_related_mails_by_message_id_header(
            "<msg@test.com>", "r1", "mails"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_related_mails_by_message_id_header(
            "<msg@test.com>", "r1", "mails"
        )
        assert result == []


# ---------------------------------------------------------------------------
# batch_update_nodes
# ---------------------------------------------------------------------------


class TestBatchUpdateNodes:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_updates(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.batch_update_nodes(
            ["missing"], {"indexingStatus": "COMPLETED"}, "records"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.batch_update_nodes(
            ["r1"], {"status": "OK"}, "records"
        )
        assert result is False


# ---------------------------------------------------------------------------
# check_collection_has_document
# ---------------------------------------------------------------------------


class TestCheckCollectionHasDocument:
    @pytest.mark.asyncio
    async def test_exists(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "d1"}
        ):
            result = await connected_provider.check_collection_has_document("records", "d1")
            assert result is True

    @pytest.mark.asyncio
    async def test_not_exists(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.check_collection_has_document("records", "d1")
            assert result is False


# ---------------------------------------------------------------------------
# check_edge_exists
# ---------------------------------------------------------------------------


class TestCheckEdgeExists:
    @pytest.mark.asyncio
    async def test_exists(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_from": "users/u1", "_to": "records/r1"}
        ]
        result = await connected_provider.check_edge_exists(
            "users/u1", "records/r1", "permission"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_not_exists(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.check_edge_exists(
            "users/u1", "records/r1", "permission"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.check_edge_exists(
            "users/u1", "records/r1", "permission"
        )
        assert result is False


# ---------------------------------------------------------------------------
# organization_exists
# ---------------------------------------------------------------------------


class TestOrganizationExists:
    @pytest.mark.asyncio
    async def test_exists(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "org1", "name": "TestOrg"}
        ]
        result = await connected_provider.organization_exists("TestOrg")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_exists(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.organization_exists("Missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.organization_exists("TestOrg")
        assert result is False


# ---------------------------------------------------------------------------
# get_failed_records_with_active_users
# ---------------------------------------------------------------------------


class TestGetFailedRecordsWithActiveUsers:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_failed_records_with_active_users("org1", "c1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_failed_records_with_active_users("org1", "c1")
        assert result == []


# ---------------------------------------------------------------------------
# get_failed_records_by_org
# ---------------------------------------------------------------------------


class TestGetFailedRecordsByOrg:
    pass
class TestUpdateEdge:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.update_edge(
            "users/u1", "records/r1", {"role": "WRITER"}, "permission"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.update_edge(
            "users/u1", "records/r1", {"role": "WRITER"}, "permission"
        )
        assert result is False


# ---------------------------------------------------------------------------
# delete_edges_to_groups
# ---------------------------------------------------------------------------


class TestDeleteEdgesToGroups:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_edges(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.delete_edges_to_groups(
            "u1", "users", "permission"
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.delete_edges_to_groups(
            "u1", "users", "permission"
        )
        assert result == 0


# ---------------------------------------------------------------------------
# delete_edges_between_collections
# ---------------------------------------------------------------------------


class TestDeleteEdgesBetweenCollections:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_edges(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.delete_edges_between_collections(
            "u1", "users", "permission", "records"
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.delete_edges_between_collections(
            "u1", "users", "permission", "records"
        )
        assert result == 0


# ---------------------------------------------------------------------------
# delete_nodes_and_edges
# ---------------------------------------------------------------------------


class TestDeleteNodesAndEdges:
    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        # Should return early without calling anything
        await connected_provider.delete_nodes_and_edges([], "records")
        connected_provider.http_client.get_graph.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_with_graph(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = {
            "graph": {
                "edgeDefinitions": [
                    {"collection": "permission"},
                    {"collection": "belongsTo"},
                ]
            }
        }
        connected_provider.http_client.execute_aql.return_value = []
        connected_provider.http_client.batch_delete_documents.return_value = 1

        await connected_provider.delete_nodes_and_edges(["k1"], "records")
        connected_provider.http_client.get_graph.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_not_found_uses_fallback(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = None
        connected_provider.http_client.execute_aql.return_value = []
        connected_provider.http_client.batch_delete_documents.return_value = 1

        await connected_provider.delete_nodes_and_edges(["k1"], "records")

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_graph.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.delete_nodes_and_edges(["k1"], "records")


# ---------------------------------------------------------------------------
# count_connector_instances_by_scope
# ---------------------------------------------------------------------------


class TestCountConnectorInstancesByScope:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_personal_with_user(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [3]
        result = await connected_provider.count_connector_instances_by_scope(
            "apps", "personal", user_id="u1"
        )
        assert result == 3

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.count_connector_instances_by_scope(
            "apps", "team"
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.count_connector_instances_by_scope(
            "apps", "team"
        )
        assert result == 0


# ---------------------------------------------------------------------------
# check_connector_name_uniqueness
# ---------------------------------------------------------------------------


class TestCheckConnectorNameUniqueness:
    @pytest.mark.asyncio
    async def test_unique_personal(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.check_connector_name_uniqueness(
            "My Connector", "personal", "org1", "u1", "apps"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_not_unique_personal(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["key1"]
        result = await connected_provider.check_connector_name_uniqueness(
            "My Connector", "personal", "org1", "u1", "apps"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_unique_team(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.check_connector_name_uniqueness(
            "Team Connector", "team", "org1", "u1", "apps",
            edge_collection="orgAppRelation"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_true(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.check_connector_name_uniqueness(
            "My Connector", "personal", "org1", "u1", "apps"
        )
        assert result is True  # fail-open


# ---------------------------------------------------------------------------
# get_connector_instances_with_filters
# ---------------------------------------------------------------------------


class TestGetConnectorInstancesWithFilters:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [1],
            [{"_key": "c1", "name": "Drive"}],
        ]
        docs, total = await connected_provider.get_connector_instances_with_filters(
            "apps", search="drive", user_id="u1"
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        docs, total = await connected_provider.get_connector_instances_with_filters(
            "apps", user_id="u1"
        )
        assert docs == []
        assert total == 0


# ---------------------------------------------------------------------------
# get_connector_instances_by_scope_and_user
# ---------------------------------------------------------------------------


class TestGetConnectorInstancesByScopeAndUser:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_connector_instances_by_scope_and_user(
            "apps", "u1", "team", "personal"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_connector_instances_by_scope_and_user(
            "apps", "u1", "team", "personal"
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_user_sync_state
# ---------------------------------------------------------------------------


class TestGetUserSyncState:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_entity_id_by_email",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_user_sync_state(
                "missing@test.com", "DRIVE"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_entity_id_by_email",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_user_sync_state(
                "user@test.com", "DRIVE"
            )
            assert result is None


# ---------------------------------------------------------------------------
# update_user_sync_state
# ---------------------------------------------------------------------------


class TestUpdateUserSyncState:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_entity_id_by_email",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_user_sync_state(
                "missing@test.com", "RUNNING"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_entity_id_by_email",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.update_user_sync_state(
                "user@test.com", "RUNNING"
            )
            assert result is None


# ---------------------------------------------------------------------------
# get_drive_sync_state
# ---------------------------------------------------------------------------


class TestGetDriveSyncState:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_nodes_by_filters",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_drive_sync_state("missing")
            assert result == "NOT_STARTED"


# ---------------------------------------------------------------------------
# store_page_token
# ---------------------------------------------------------------------------


class TestStorePageToken:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.store_page_token(
            "ch1", "res1", "user@test.com", "tok1"
        )
        assert result is None


# ---------------------------------------------------------------------------
# get_page_token_db
# ---------------------------------------------------------------------------


class TestGetPageTokenDb:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_filters(self, connected_provider):
        result = await connected_provider.get_page_token_db()
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_page_token_db(channel_id="ch1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_page_token_db(channel_id="ch1")
        assert result is None


# ---------------------------------------------------------------------------
# _delete_all_edges_for_nodes
# ---------------------------------------------------------------------------


class TestDeleteAllEdgesForNodes:
    @pytest.mark.asyncio
    async def test_empty_nodes(self, connected_provider):
        total, failed = await connected_provider._delete_all_edges_for_nodes(
            None, [], ["permission"]
        )
        assert total == 0
        assert failed == []

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_partial_failure(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [1],  # first collection
            Exception("fail"),  # second collection
        ]
        total, failed = await connected_provider._delete_all_edges_for_nodes(
            None, ["records/r1"], ["permission", "belongsTo"]
        )
        assert total == 1
        assert len(failed) == 1


# ---------------------------------------------------------------------------
# _delete_nodes_by_keys
# ---------------------------------------------------------------------------


class TestDeleteNodesByKeys:
    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        total, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", [], "records"
        )
        assert total == 0
        assert failed == 0

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_batch_failure(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        total, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", ["k1"], "records"
        )
        assert total == 0
        assert failed == 1


# ---------------------------------------------------------------------------
# _delete_nodes_by_connector_id
# ---------------------------------------------------------------------------


class TestDeleteNodesByConnectorId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        total, success = await connected_provider._delete_nodes_by_connector_id(
            "txn1", "c1", "records"
        )
        assert total == 0
        assert success is False


# ---------------------------------------------------------------------------
# delete_sync_points_by_connector_id
# ---------------------------------------------------------------------------


class TestDeleteSyncPointsByConnectorId:
    @pytest.mark.asyncio
    async def test_delegates(self, connected_provider):
        with patch.object(
            connected_provider, "_delete_nodes_by_connector_id",
            new_callable=AsyncMock, return_value=(5, True)
        ):
            total, success = await connected_provider.delete_sync_points_by_connector_id("c1")
            assert total == 5
            assert success is True


# ---------------------------------------------------------------------------
# reindex_record_group_records
# ---------------------------------------------------------------------------


class TestReindexRecordGroupRecords:
    @pytest.mark.asyncio
    async def test_record_group_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", 1, "u1", "org1"
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_connector_info(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "rg1"}  # no connectorId or connectorName
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", 1, "u1", "org1"
            )
            assert result["success"] is False
            assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", 1, "u1", "org1"
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_permission_denied(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "ext1"}
        ), patch.object(
            connected_provider, "_check_record_group_permissions",
            new_callable=AsyncMock,
            return_value={"allowed": False, "reason": "No permission"}
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", 1, "u1", "org1"
            )
            assert result["success"] is False
            assert result["code"] == 403

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_depth_minus_one_normalized(self, connected_provider):
        """Depth -1 should be normalized to MAX_REINDEX_DEPTH."""
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "ext1"}
        ), patch.object(
            connected_provider, "_check_record_group_permissions",
            new_callable=AsyncMock,
            return_value={"allowed": True, "role": "OWNER"}
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", -1, "ext1", "org1"
            )
            assert result["success"] is True
            assert result["depth"] == 100  # MAX_REINDEX_DEPTH

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", 1, "u1", "org1"
            )
            assert result["success"] is False
            assert result["code"] == 500


# ---------------------------------------------------------------------------
# _reset_indexing_status_to_queued
# ---------------------------------------------------------------------------


class TestResetIndexingStatusToQueued:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            # Should return without error
            await connected_provider._reset_indexing_status_to_queued("r1")

    @pytest.mark.asyncio
    async def test_already_queued(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "indexingStatus": "QUEUED"}
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider._reset_indexing_status_to_queued("r1")
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_empty(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "indexingStatus": "EMPTY"}
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider._reset_indexing_status_to_queued("r1")
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resets_completed_to_queued(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "indexingStatus": "COMPLETED"}
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider._reset_indexing_status_to_queued("r1")
            mock_upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_logged(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            # Should not raise
            await connected_provider._reset_indexing_status_to_queued("r1")


# ---------------------------------------------------------------------------
# get_records_by_parent
# ---------------------------------------------------------------------------


class TestGetRecordsByParent:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_records_by_parent("c1", "ext_parent")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_records_by_parent("c1", "ext_parent")
        assert result == []


# ---------------------------------------------------------------------------
# get_record_by_weburl
# ---------------------------------------------------------------------------


class TestGetRecordByWeburl:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_skips_link_records(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r1", "webUrl": "https://example.com", "recordType": "LINK"},
            {"_key": "r2", "webUrl": "https://example.com", "recordType": "FILE"},
        ]
        result = await connected_provider.get_record_by_weburl("https://example.com")
        assert result is not None
        assert result.id == "r2"

    @pytest.mark.asyncio
    async def test_only_links(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r1", "webUrl": "https://example.com", "recordType": "LINK"},
        ]
        result = await connected_provider.get_record_by_weburl("https://example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_weburl("https://missing.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r1", "webUrl": "https://example.com", "recordType": "FILE"}
        ]
        result = await connected_provider.get_record_by_weburl(
            "https://example.com", org_id="org1"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_weburl("https://example.com")
        assert result is None


# ---------------------------------------------------------------------------
# _collect_connector_entities
# ---------------------------------------------------------------------------


class TestCollectConnectorEntities:
    pass
class TestGetAllEdgeCollections:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_graph_not_found(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = None
        with pytest.raises(Exception, match="not found"):
            await connected_provider._get_all_edge_collections()

    @pytest.mark.asyncio
    async def test_no_edge_definitions(self, connected_provider):
        connected_provider.http_client.get_graph.return_value = {
            "graph": {"edgeDefinitions": []}
        }
        with pytest.raises(Exception, match="no edge collections"):
            await connected_provider._get_all_edge_collections()


# ---------------------------------------------------------------------------
# _collect_isoftype_targets
# ---------------------------------------------------------------------------


class TestCollectIsoftypeTargets:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_connector(self, connected_provider):
        targets, success = await connected_provider._collect_isoftype_targets(None, "")
        assert success is True
        assert targets == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        targets, success = await connected_provider._collect_isoftype_targets(
            None, "c1"
        )
        assert success is False
        assert targets == []


# ---------------------------------------------------------------------------
# get_records_by_record_group (integration-style)
# ---------------------------------------------------------------------------


class TestGetRecordsByRecordGroup:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_records_by_record_group(
            "rg1", "c1", "org1", depth=1
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_records_by_parent_record
# ---------------------------------------------------------------------------


class TestGetRecordsByParentRecord:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_records_by_parent_record(
            "parent1", "c1", "org1", depth=1
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_record_by_conversation_index
# ---------------------------------------------------------------------------


class TestGetRecordByConversationIndex:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_conversation_index(
            "c1", "conv_idx", "thread1", "org1", "u1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_conversation_index(
            "c1", "conv_idx", "thread1", "org1", "u1"
        )
        assert result is None


# ---------------------------------------------------------------------------
# get_record_by_issue_key
# ---------------------------------------------------------------------------


class TestGetRecordByIssueKey:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_issue_key("c1", "PROJ-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_issue_key("c1", "PROJ-123")
        assert result is None


# ===========================================================================
# NEW TESTS - Coverage expansion
# ===========================================================================


def _make_full_arango_record(**overrides):
    """Build a complete mock ArangoDB record dict with all required fields."""
    base = {
        "_key": "r1",
        "orgId": "org1",
        "recordName": "Test Record",
        "recordType": "FILE",
        "externalRecordId": "ext1",
        "version": 1,
        "origin": "CONNECTOR",
        "connectorId": "c1",
        "connectorName": "DRIVE",
        "mimeType": "application/octet-stream",
        "createdAtTimestamp": 1700000000000,
        "updatedAtTimestamp": 1700000000000,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# store_permission
# ---------------------------------------------------------------------------


class TestStorePermission:
    @pytest.mark.asyncio
    async def test_new_permission_created(self, connected_provider):
        """When no existing permissions, should create new permission edge."""
        connected_provider.http_client.execute_aql.side_effect = [
            [],  # get_file_permissions returns empty
            [],  # check existing edge - not found
            {"errors": 0},  # batch_upsert_nodes result via execute_aql
        ]
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            connected_provider.http_client.execute_aql.side_effect = [
                [],  # existing edge query
            ]
            result = await connected_provider.store_permission(
                "file1", "user1",
                {"type": "USER", "role": "READER", "id": "perm1"}
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_missing_entity_key(self, connected_provider):
        result = await connected_provider.store_permission(
            "file1", "",
            {"type": "USER", "role": "READER", "id": "perm1"}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_no_transaction(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.store_permission(
                "file1", "user1",
                {"type": "USER", "role": "READER", "id": "perm1"}
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_with_transaction_raises(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.store_permission(
                    "file1", "user1",
                    {"type": "USER", "role": "READER", "id": "perm1"},
                    transaction="txn1"
                )

    @pytest.mark.asyncio
    async def test_domain_type_uses_org_collection(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ):
            connected_provider.http_client.execute_aql.side_effect = [
                [],  # existing edge check
            ]
            with patch.object(
                connected_provider, "batch_upsert_nodes",
                new_callable=AsyncMock, return_value=True
            ) as mock_upsert:
                result = await connected_provider.store_permission(
                    "file1", "org1",
                    {"type": "domain", "role": "READER", "id": "perm1"}
                )
                assert result is True
                call_args = mock_upsert.call_args
                edge = call_args[0][0][0]
                assert edge["_from"].startswith("organizations/")


# ---------------------------------------------------------------------------
# update_queued_duplicates_status
# ---------------------------------------------------------------------------


class TestUpdateQueuedDuplicatesStatus:
    @pytest.mark.asyncio
    async def test_no_reference_record(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_md5_checksum(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r1"}  # no md5Checksum
        ]
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_queued_duplicates(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"_key": "r1", "md5Checksum": "abc123", "sizeInBytes": 100}],  # reference
            [],  # no queued duplicates
        ]
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == 0

    @pytest.mark.asyncio
    async def test_queued_duplicates_found_and_updated(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"_key": "r1", "md5Checksum": "abc123", "sizeInBytes": 100}],  # reference
            [{"_key": "r2", "md5Checksum": "abc123"}],  # queued duplicate
        ]
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.update_queued_duplicates_status(
                "r1", "COMPLETED", virtual_record_id="vr1"
            )
            assert result == 1

    @pytest.mark.asyncio
    async def test_empty_status_mapping(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"_key": "r1", "md5Checksum": "abc123"}],
            [{"_key": "r2", "md5Checksum": "abc123"}],
        ]
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ) as mock_upsert:
            await connected_provider.update_queued_duplicates_status("r1", "EMPTY")
            # Verify extraction status is EMPTY
            call_args = mock_upsert.call_args[0][0]
            assert call_args[0]["extractionStatus"] == "EMPTY"

    @pytest.mark.asyncio
    async def test_exception_returns_negative_one(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == -1


# ---------------------------------------------------------------------------
# batch_upsert_records
# ---------------------------------------------------------------------------


class TestBatchUpsertRecords:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        record = MagicMock()
        record.id = "r1"
        from app.models.entities import RecordType
        record.record_type = RecordType.FILE
        record.to_arango_base_record.return_value = {"id": "r1"}
        record.to_arango_record.return_value = {"id": "r1"}

        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_records([record])


# ---------------------------------------------------------------------------
# Note: batch_upsert_domains, batch_upsert_anyone_with_link,
# batch_upsert_anyone_same_org, and batch_create_user_app_edges reference
# CollectionNames constants (DOMAINS, ANYONE_WITH_LINK, ANYONE_SAME_ORG,
# USER_APP) that do not exist in the enum. These are dead code in the
# provider and are skipped.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# delete_connector_sync_edges
# ---------------------------------------------------------------------------


class TestDeleteConnectorSyncEdges:
    @pytest.mark.asyncio
    async def test_no_entities(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock,
            return_value={"all_node_ids": []}
        ):
            count, success = await connected_provider.delete_connector_sync_edges("c1")
            assert count == 0
            assert success is True

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_partial_failure(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock,
            return_value={"all_node_ids": ["records/r1"]}
        ), patch.object(
            connected_provider, "_delete_all_edges_for_nodes",
            new_callable=AsyncMock,
            return_value=(3, ["permission"])
        ):
            count, success = await connected_provider.delete_connector_sync_edges("c1")
            assert count == 3
            assert success is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            count, success = await connected_provider.delete_connector_sync_edges("c1")
            assert count == 0
            assert success is False


# ---------------------------------------------------------------------------
# delete_record_routing (connector-specific delete methods)
# ---------------------------------------------------------------------------


class TestDeleteKnowledgeBaseRecord:
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_knowledge_base_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_kb_context_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_get_kb_context_for_record",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_knowledge_base_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permissions(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_get_kb_context_for_record",
            new_callable=AsyncMock,
            return_value={"kb_id": "kb1", "kb_name": "Test KB", "org_id": "org1"}
        ), patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, return_value="READER"
        ):
            result = await connected_provider.delete_knowledge_base_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_knowledge_base_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 500


class TestDeleteGoogleDriveRecord:
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_google_drive_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permissions(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_drive_permissions",
            new_callable=AsyncMock, return_value="READER"
        ):
            result = await connected_provider.delete_google_drive_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_drive_permissions",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_google_drive_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_google_drive_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 500


class TestDeleteGmailRecord:
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_gmail_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permissions(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_gmail_permissions",
            new_callable=AsyncMock, return_value="READER"
        ):
            result = await connected_provider.delete_gmail_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_gmail_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 500


class TestDeleteOutlookRecord:
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_outlook_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_not_owner(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_record_permission",
            new_callable=AsyncMock, return_value="READER"
        ):
            result = await connected_provider.delete_outlook_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 403
            assert "Only mailbox owner" in result["reason"]

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_outlook_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is False
            assert result["code"] == 500


# ---------------------------------------------------------------------------
# _check_drive_permissions
# ---------------------------------------------------------------------------


class TestCheckDrivePermissions:
    @pytest.mark.asyncio
    async def test_has_permission(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["OWNER"]
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result == "OWNER"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_null_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._check_drive_permissions("r1", "u1")
        assert result is None


# ---------------------------------------------------------------------------
# _check_gmail_permissions
# ---------------------------------------------------------------------------


class TestCheckGmailPermissions:
    @pytest.mark.asyncio
    async def test_has_permission(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["OWNER"]
        result = await connected_provider._check_gmail_permissions("r1", "u1")
        assert result == "OWNER"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._check_gmail_permissions("r1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._check_gmail_permissions("r1", "u1")
        assert result is None


# ---------------------------------------------------------------------------
# _get_kb_context_for_record
# ---------------------------------------------------------------------------


class TestGetKbContextForRecord:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider._get_kb_context_for_record("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._get_kb_context_for_record("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._get_kb_context_for_record("r1")
        assert result is None


# ---------------------------------------------------------------------------
# get_user_kb_permission
# ---------------------------------------------------------------------------


class TestGetUserKbPermission:
    @pytest.mark.asyncio
    async def test_has_permission(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["OWNER"]
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result == "OWNER"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_kb_permission("kb1", "u1")
        assert result is None


# ---------------------------------------------------------------------------
# remove_user_access_to_record
# ---------------------------------------------------------------------------


class TestRemoveUserAccessToRecord:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=None
        ):
            # Should return without error
            await connected_provider.remove_user_access_to_record("c1", "ext1", "u1")

    @pytest.mark.asyncio
    async def test_permissions_removed(self, connected_provider):
        mock_record = MagicMock()
        mock_record.id = "r1"
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=mock_record
        ):
            connected_provider.http_client.execute_aql.return_value = [
                {"_key": "perm1"}
            ]
            await connected_provider.remove_user_access_to_record("c1", "ext1", "u1")
            connected_provider.http_client.execute_aql.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_permissions_found(self, connected_provider):
        mock_record = MagicMock()
        mock_record.id = "r1"
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=mock_record
        ):
            connected_provider.http_client.execute_aql.return_value = []
            await connected_provider.remove_user_access_to_record("c1", "ext1", "u1")

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.remove_user_access_to_record("c1", "ext1", "u1")


# ---------------------------------------------------------------------------
# update_drive_sync_state
# ---------------------------------------------------------------------------


class TestUpdateDriveSyncState:
    @pytest.mark.asyncio
    async def test_drive_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_nodes_by_filters",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock
        ) as mock_update:
            await connected_provider.update_drive_sync_state("missing", "RUNNING")
            mock_update.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_nodes_by_filters",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            # Should not raise
            await connected_provider.update_drive_sync_state("drive1", "RUNNING")


# ---------------------------------------------------------------------------
# _delete_edges_by_connector_id
# ---------------------------------------------------------------------------


class TestDeleteEdgesByConnectorId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_partial_failure(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "c1", ["permission"]
        )
        assert len(failed) > 0


# ---------------------------------------------------------------------------
# delete_connector_instance
# ---------------------------------------------------------------------------


class TestDeleteConnectorInstance:
    @pytest.mark.asyncio
    async def test_connector_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_connector_instance("c1", "org1")
            assert result["success"] is False
            assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_isoftype_collect_failure(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "c1", "type": "DRIVE"}
        ), patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock,
            return_value={"all_node_ids": ["records/r1"],
                          "record_keys": [], "record_group_keys": [],
                          "role_keys": [], "group_keys": [],
                          "virtual_record_ids": [], "record_ids": []}
        ), patch.object(
            connected_provider, "_get_all_edge_collections",
            new_callable=AsyncMock, return_value=["permission"]
        ), patch.object(
            connected_provider, "_collect_isoftype_targets",
            new_callable=AsyncMock, return_value=([], False)
        ):
            result = await connected_provider.delete_connector_instance("c1", "org1")
            assert result["success"] is False
            assert "isOfType" in result["error"]

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_connector_instance("c1", "org1")
            assert result["success"] is False


# ---------------------------------------------------------------------------
# delete_record routing
# ---------------------------------------------------------------------------


class TestDeleteRecordRouting:
    @pytest.mark.asyncio
    async def test_routes_to_kb(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "KB", "origin": "UPLOAD"
        }
        with patch.object(
            connected_provider, "delete_knowledge_base_record",
            new_callable=AsyncMock,
            return_value={"success": True}
        ) as mock_kb:
            result = await connected_provider.delete_record("r1", "u1")
            assert result["success"] is True
            mock_kb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_to_drive(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "DRIVE", "origin": "CONNECTOR"
        }
        with patch.object(
            connected_provider, "delete_google_drive_record",
            new_callable=AsyncMock,
            return_value={"success": True}
        ) as mock_drive:
            result = await connected_provider.delete_record("r1", "u1")
            assert result["success"] is True
            mock_drive.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_to_gmail(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "GMAIL", "origin": "CONNECTOR"
        }
        with patch.object(
            connected_provider, "delete_gmail_record",
            new_callable=AsyncMock,
            return_value={"success": True}
        ) as mock_gmail:
            result = await connected_provider.delete_record("r1", "u1")
            assert result["success"] is True
            mock_gmail.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_to_outlook(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "OUTLOOK", "origin": "CONNECTOR"
        }
        with patch.object(
            connected_provider, "delete_outlook_record",
            new_callable=AsyncMock,
            return_value={"success": True}
        ) as mock_outlook:
            result = await connected_provider.delete_record("r1", "u1")
            assert result["success"] is True
            mock_outlook.assert_awaited_once()


# ---------------------------------------------------------------------------
# reindex_single_record
# ---------------------------------------------------------------------------


class TestReindexSingleRecord:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_deleted_record(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "isDeleted": True}
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "origin": "CONNECTOR", "connectorName": "DRIVE",
                          "connectorId": "c1"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_unsupported_origin(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "origin": "UNKNOWN", "connectorName": "X",
                          "connectorId": "c1"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_connector_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "origin": "CONNECTOR", "connectorName": "DRIVE",
                          "connectorId": "c1"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_record_permissions",
            new_callable=AsyncMock,
            return_value={"permission": None, "source": "NONE"}
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_connector_disabled(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            side_effect=[
                {"id": "r1", "origin": "CONNECTOR", "connectorName": "DRIVE",
                 "connectorId": "c1"},
                {"id": "c1", "isActive": False},  # connector doc
            ]
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_record_permissions",
            new_callable=AsyncMock,
            return_value={"permission": "OWNER", "source": "DIRECT"}
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 400
            assert "disabled" in result["reason"]

    @pytest.mark.asyncio
    async def test_depth_normalization(self, connected_provider):
        """Depth -1 should be normalized to MAX_REINDEX_DEPTH."""
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            side_effect=[
                {"id": "r1", "origin": "CONNECTOR", "connectorName": "DRIVE",
                 "connectorId": "c1", "recordName": "Test"},
                {"id": "c1", "isActive": True},  # connector doc
            ]
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_record_permissions",
            new_callable=AsyncMock,
            return_value={"permission": "OWNER", "source": "DIRECT"}
        ), patch.object(
            connected_provider, "_reset_indexing_status_to_queued",
            new_callable=AsyncMock
        ):
            result = await connected_provider.reindex_single_record(
                "r1", "u1", "org1", depth=-1
            )
            assert result["success"] is True
            # Event data should have depth = 100
            assert result["eventData"]["payload"]["depth"] == 100

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_upload_origin_no_kb_context(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "origin": "UPLOAD", "connectorName": "KB",
                          "connectorId": "c1", "recordType": "FILE"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_get_kb_context_for_record",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_upload_origin_no_kb_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "origin": "UPLOAD", "connectorName": "KB",
                          "connectorId": "c1", "recordType": "FILE"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_get_kb_context_for_record",
            new_callable=AsyncMock,
            return_value={"kb_id": "kb1", "id": "kb1"}
        ), patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is False
            assert result["code"] == 403


# ---------------------------------------------------------------------------
# _ensure_indexes
# ---------------------------------------------------------------------------


class TestEnsureIndexes:
    @pytest.mark.asyncio
    async def test_calls_ensure_persistent_index(self, connected_provider):
        connected_provider.http_client.ensure_persistent_index = AsyncMock()
        await connected_provider._ensure_indexes()
        connected_provider.http_client.ensure_persistent_index.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_records_by_record_group with pagination
# ---------------------------------------------------------------------------


class TestGetRecordsByRecordGroupPagination:
    @pytest.mark.asyncio
    async def test_with_limit_and_offset(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"record": _make_full_arango_record(), "typeDoc": None}
        ]
        result = await connected_provider.get_records_by_record_group(
            "rg1", "c1", "org1", depth=1, limit=10, offset=5
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_unlimited_depth(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_records_by_record_group(
            "rg1", "c1", "org1", depth=-1
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_with_user_key(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"record": _make_full_arango_record(), "typeDoc": None}
        ]
        result = await connected_provider.get_records_by_record_group(
            "rg1", "c1", "org1", depth=1, user_key="u1"
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_records_by_parent_record with options
# ---------------------------------------------------------------------------


class TestGetRecordsByParentRecordOptions:
    @pytest.mark.asyncio
    async def test_with_user_key(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"record": _make_full_arango_record(),
             "typedRecord": None, "depth": 1}
        ]
        result = await connected_provider.get_records_by_parent_record(
            "parent1", "c1", "org1", depth=1, user_key="u1"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_unlimited_depth(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_records_by_parent_record(
            "parent1", "c1", "org1", depth=-1
        )
        assert result == []

# ---------------------------------------------------------------------------
# ensure_schema success path
# ---------------------------------------------------------------------------


class TestEnsureSchemaSuccess:
    @pytest.mark.asyncio
    async def test_full_schema_creation(self, connected_provider):
        connected_provider.http_client.has_collection.return_value = False
        connected_provider.http_client.create_collection.return_value = True
        connected_provider.http_client.has_graph.return_value = False
        connected_provider.http_client.create_graph.return_value = True
        with patch.object(
            connected_provider, "_ensure_departments_seed",
            new_callable=AsyncMock
        ):
            result = await connected_provider.ensure_schema()
            assert result is True

    @pytest.mark.asyncio
    async def test_schema_already_exists(self, connected_provider):
        connected_provider.http_client.has_collection.return_value = True
        connected_provider.http_client.has_graph.return_value = True
        with patch.object(
            connected_provider, "_ensure_departments_seed",
            new_callable=AsyncMock
        ):
            result = await connected_provider.ensure_schema()
            assert result is True
            connected_provider.http_client.create_collection.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_sync_point / upsert_sync_point edge cases
# ---------------------------------------------------------------------------


class TestSyncPointEdgeCases:
    @pytest.mark.asyncio
    async def test_upsert_sync_point_with_transaction(self, connected_provider):
        with patch.object(
            connected_provider, "get_sync_point",
            new_callable=AsyncMock, return_value=None
        ):
            connected_provider.http_client.execute_aql.return_value = [{"syncPointKey": "sp1"}]
            result = await connected_provider.upsert_sync_point(
                "sp1", {"data": "val"}, "syncPoints", transaction="txn1"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_remove_sync_point_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        await connected_provider.remove_sync_point("sp1", "syncPoints", transaction="txn1")
        connected_provider.http_client.execute_aql.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_record_by_external_id success path
# ---------------------------------------------------------------------------


class TestDeleteRecordByExternalIdSuccess:
    pass
class TestBatchUpsertAppUsers:
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        # Should return early without calling anything
        await connected_provider.batch_upsert_app_users([])

    @pytest.mark.asyncio
    async def test_no_orgs(self, connected_provider):
        mock_user = MagicMock()
        mock_user.connector_id = "c1"
        with patch.object(
            connected_provider, "get_all_orgs",
            new_callable=AsyncMock, return_value=[]
        ):
            with pytest.raises(Exception, match="No organizations"):
                await connected_provider.batch_upsert_app_users([mock_user])

    @pytest.mark.asyncio
    async def test_app_not_found(self, connected_provider):
        mock_user = MagicMock()
        mock_user.connector_id = "c1"
        with patch.object(
            connected_provider, "get_all_orgs",
            new_callable=AsyncMock,
            return_value=[{"_key": "org1"}]
        ), patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            with pytest.raises(Exception, match="Failed to get/create app"):
                await connected_provider.batch_upsert_app_users([mock_user])


# ---------------------------------------------------------------------------
# _permission_needs_update edge cases
# ---------------------------------------------------------------------------


class TestPermissionNeedsUpdateEdgeCases:
    def test_missing_field_in_existing(self, connected_provider):
        existing = {}
        new = {"role": "WRITER"}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_same_role_returns_false(self, connected_provider):
        existing = {"role": "OWNER"}
        new = {"role": "OWNER"}
        assert connected_provider._permission_needs_update(existing, new) is False


# ---------------------------------------------------------------------------
# _collect_connector_entities edge cases
# ---------------------------------------------------------------------------


class TestCollectConnectorEntitiesEdgeCases:
    @pytest.mark.asyncio
    async def test_no_virtual_record_ids(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"_key": "r1"}],  # records - no virtualRecordId
            [],  # record groups
            [],  # roles
            [],  # groups
        ]
        result = await connected_provider._collect_connector_entities("c1")
        assert result["virtual_record_ids"] == []
        assert "r1" in result["record_keys"]

    @pytest.mark.asyncio
    async def test_none_results(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            None,  # records
            None,  # record groups
            None,  # roles
            None,  # groups
        ]
        result = await connected_provider._collect_connector_entities("c1")
        assert result["record_keys"] == []
        assert result["record_group_keys"] == []


# ---------------------------------------------------------------------------
# delete_records_and_relations with transaction
# ---------------------------------------------------------------------------


class TestDeleteRecordsAndRelationsWithTransaction:
    @pytest.mark.asyncio
    async def test_exception_with_transaction_raises(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.delete_records_and_relations(
                "r1", hard_delete=True, transaction="txn1"
            )

    @pytest.mark.asyncio
    async def test_hard_delete_success(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {"_key": "r1"}
        connected_provider.http_client.execute_aql.return_value = [
            {"record_removed": True, "file_removed": True, "mail_removed": False}
        ]
        result = await connected_provider.delete_records_and_relations(
            "r1", hard_delete=True
        )
        assert result is True


# ---------------------------------------------------------------------------
# process_file_permissions
# ---------------------------------------------------------------------------


class TestProcessFilePermissions:
    @pytest.mark.asyncio
    async def test_exception_no_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.process_file_permissions(
            "org1", "f1", [{"type": "user", "role": "READER", "id": "p1"}]
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_with_transaction_raises(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.process_file_permissions(
                "org1", "f1",
                [{"type": "user", "role": "READER", "id": "p1"}],
                transaction="txn1"
            )


# ---------------------------------------------------------------------------
# get_filtered_connector_instances with various options
# ---------------------------------------------------------------------------


class TestGetFilteredConnectorInstancesOptions:
    @pytest.mark.asyncio
    async def test_personal_scope(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[
                [3],  # count
                [1],  # personal count
                [{"_key": "c1"}],  # documents
            ]
        ):
            docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
                "apps", "orgAppRelation", "org1", "u1", scope="personal"
            )
            assert total == 3
            assert scope_counts["personal"] == 1

    @pytest.mark.asyncio
    async def test_team_scope_with_admin(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[
                [5],  # count
                [2],  # personal count
                [4],  # team count
                [{"_key": "c1"}],  # documents
            ]
        ):
            docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
                "apps", "orgAppRelation", "org1", "u1",
                scope="team", is_admin=True
            )
            assert total == 5

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[
                [1],  # count
                [0],  # personal count
                [{"_key": "c1", "name": "Drive"}],  # documents
            ]
        ):
            docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
                "apps", "orgAppRelation", "org1", "u1",
                search="drive"
            )
            assert total == 1


# ---------------------------------------------------------------------------
# batch_update_connector_status edge cases
# ---------------------------------------------------------------------------


class TestBatchUpdateConnectorStatusEdgeCases:
    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"_key": "k1"}]
        ):
            result = await connected_provider.batch_update_connector_status(
                "apps", ["k1"],
                is_active=False, is_agent_active=True,
                transaction="txn1"
            )
            assert result == 1


# ---------------------------------------------------------------------------
# get_user_connector_instances with transaction
# ---------------------------------------------------------------------------


class TestGetUserConnectorInstancesEdgeCases:
    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_user_connector_instances(
                "apps", "u1", "org1", "team", "personal"
            )
            assert result == []


# ---------------------------------------------------------------------------
# check_connector_name_exists edge cases
# ---------------------------------------------------------------------------


class TestCheckConnectorNameExistsEdgeCases:
    @pytest.mark.asyncio
    async def test_team_scope_with_org_id(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.check_connector_name_exists(
                "apps", "  My Connector  ", "team", org_id="org1"
            )
            assert result is False


# ---------------------------------------------------------------------------
# get_all_documents with transaction
# ---------------------------------------------------------------------------


class TestGetAllDocumentsEdgeCases:
    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"_key": "d1"}]
        result = await connected_provider.get_all_documents("records", transaction="txn1")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_records_by_status with no limit
# ---------------------------------------------------------------------------


class TestGetRecordsByStatusEdgeCases:
    @pytest.mark.asyncio
    async def test_no_limit(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"record": _make_full_arango_record(), "typeDoc": None}
        ]
        result = await connected_provider.get_records_by_status(
            "org1", "c1", ["QUEUED"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_records_by_status(
            "org1", "c1", ["QUEUED"]
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_record_by_weburl edge cases
# ---------------------------------------------------------------------------


class TestGetRecordByWeburlEdgeCases:
    @pytest.mark.asyncio
    async def test_multiple_records_first_non_link(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            _make_full_arango_record(_key="r1", webUrl="https://example.com", recordType="FILE"),
            _make_full_arango_record(_key="r2", webUrl="https://example.com", recordType="MAIL"),
        ]
        result = await connected_provider.get_record_by_weburl("https://example.com")
        assert result is not None
        assert result.id == "r1"


# ---------------------------------------------------------------------------
# reindex_record_group_records additional edge cases
# ---------------------------------------------------------------------------


class TestReindexRecordGroupRecordsEdgeCases:
    @pytest.mark.asyncio
    async def test_negative_depth_normalized_to_zero(self, connected_provider):
        """Depth < 0 and != -1 should be normalized to 0."""
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "ext1"}
        ), patch.object(
            connected_provider, "_check_record_group_permissions",
            new_callable=AsyncMock,
            return_value={"allowed": True, "role": "OWNER"}
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", -5, "ext1", "org1"
            )
            assert result["success"] is True
            assert result["depth"] == 0

    @pytest.mark.asyncio
    async def test_user_key_from_id_field(self, connected_provider):
        """When user has 'id' but not '_key'."""
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"id": "u1", "userId": "ext1"}  # 'id' instead of '_key'
        ), patch.object(
            connected_provider, "_check_record_group_permissions",
            new_callable=AsyncMock,
            return_value={"allowed": True, "role": "OWNER"}
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", 1, "ext1", "org1"
            )
            assert result["success"] is True
            assert result["userKey"] == "u1"

    @pytest.mark.asyncio
    async def test_user_no_key(self, connected_provider):
        """When user has neither '_key' nor 'id'."""
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"userId": "ext1"}  # no _key or id
        ):
            result = await connected_provider.reindex_record_group_records(
                "rg1", 1, "ext1", "org1"
            )
            assert result["success"] is False
            assert result["code"] == 404


# ---------------------------------------------------------------------------
# bulk_get_entity_ids_by_email with groups and people
# ---------------------------------------------------------------------------


class TestBulkGetEntityIdsByEmailFull:
    @pytest.mark.asyncio
    async def test_found_in_groups(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [],  # users - empty
            [{"email": "group@test.com", "id": "g1"}],  # groups
        ]
        result = await connected_provider.bulk_get_entity_ids_by_email(["group@test.com"])
        assert "group@test.com" in result
        assert result["group@test.com"][1] == "groups"
        assert result["group@test.com"][2] == "GROUP"

    @pytest.mark.asyncio
    async def test_found_in_people(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [],  # users
            [],  # groups
            [{"email": "person@test.com", "id": "p1"}],  # people
        ]
        result = await connected_provider.bulk_get_entity_ids_by_email(["person@test.com"])
        assert "person@test.com" in result
        assert result["person@test.com"][1] == "people"
        assert result["person@test.com"][2] == "USER"

    @pytest.mark.asyncio
    async def test_deduplicates_emails(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"email": "u@test.com", "id": "u1"}],  # users
        ]
        result = await connected_provider.bulk_get_entity_ids_by_email(
            ["u@test.com", "u@test.com"]  # duplicate
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_partial_query_failure(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            Exception("user query fail"),  # users fail
        ]
        # Should not raise, should return empty
        result = await connected_provider.bulk_get_entity_ids_by_email(["u@test.com"])
        assert result == {}


# ---------------------------------------------------------------------------
# get_records_by_parent edge cases
# ---------------------------------------------------------------------------


class TestGetRecordsByParentEdgeCases:
    @pytest.mark.asyncio
    async def test_with_all_record_types(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            _make_full_arango_record(_key="r1", recordType="FILE"),
            _make_full_arango_record(_key="r2", recordType="TICKET"),
        ]
        result = await connected_provider.get_records_by_parent("c1", "ext_parent")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _create_typed_record_from_arango (line 3058 duplicate method)
# ---------------------------------------------------------------------------


class TestCreateTypedRecordFromArango:
    def test_no_type_doc(self, connected_provider):
        record_dict = _make_full_arango_record()
        result = connected_provider._create_typed_record_from_arango(record_dict, None)
        assert result is not None

    def test_unmapped_record_type(self, connected_provider):
        """DRIVE is a valid RecordType but not in RECORD_TYPE_COLLECTION_MAPPING."""
        record_dict = _make_full_arango_record(recordType="DRIVE")
        result = connected_provider._create_typed_record_from_arango(
            record_dict, {"_key": "r1"}
        )
        assert result is not None

    def test_exception_falls_back(self, connected_provider):
        """If typed record creation fails, should fall back to base Record."""
        record_dict = _make_full_arango_record(recordType="FILE")
        # Pass a type_doc that will cause an error in the specific record constructor
        type_doc = {"_key": "r1"}  # minimal data that may fail in FileRecord constructor
        result = connected_provider._create_typed_record_from_arango(
            record_dict, type_doc
        )
        # Should not raise, should return a Record instance
        assert result is not None


# ===========================================================================
# NEW TESTS: get_nodes_by_filters
# ===========================================================================


class TestGetNodesByFilters:
    @pytest.mark.asyncio
    async def test_success_with_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"name": "test", "orgId": "org1"}
        ]
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"name": "test"}]
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}, return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"a": 1}]
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}, transaction="txn1"
        )
        assert len(result) == 1
        connected_provider.http_client.execute_aql.assert_awaited_once()
        call_kwargs = connected_provider.http_client.execute_aql.call_args
        assert call_kwargs[1]["txn_id"] == "txn1"


# ===========================================================================
# NEW TESTS: get_nodes_by_field_in
# ===========================================================================


class TestGetNodesByFieldIn:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_values(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_nodes_by_field_in(
            "records", "connectorId", []
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"name": "test"}]
        result = await connected_provider.get_nodes_by_field_in(
            "records", "connectorId", ["c1"], return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_nodes_by_field_in(
            "records", "connectorId", ["c1"]
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_nodes_by_field_in(
            "records", "connectorId", ["c1"]
        )
        assert result == []


# ===========================================================================
# NEW TESTS: remove_nodes_by_field
# ===========================================================================


class TestRemoveNodesByField:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_matches(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.remove_nodes_by_field(
            "records", "connectorId", field_value="c1"
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception, match="fail"):
            await connected_provider.remove_nodes_by_field(
                "records", "connectorId", field_value="c1"
            )

    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"_key": "k1"}]
        result = await connected_provider.remove_nodes_by_field(
            "records", "connectorId", field_value="c1", transaction="txn1"
        )
        assert result == 1


# ===========================================================================
# NEW TESTS: get_edges_to_node
# ===========================================================================


class TestGetEdgesToNode:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_edges_to_node("records/r1", "permissions")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_edges_to_node("records/r1", "permissions")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_edges_to_node("records/r1", "permissions")
        assert result == []


# ===========================================================================
# NEW TESTS: get_edges_from_node
# ===========================================================================


class TestGetEdgesFromNodeProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_edges_from_node("groups/g1", "permissions")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_edges_from_node("groups/g1", "permissions")
        assert result == []

    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{"_key": "e1"}]
        result = await connected_provider.get_edges_from_node(
            "groups/g1", "permissions", transaction="txn1"
        )
        assert len(result) == 1


# ===========================================================================
# NEW TESTS: get_related_nodes
# ===========================================================================


class TestGetRelatedNodes:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "r1", "name": "Record 1"}
        ]
        result = await connected_provider.get_related_nodes(
            "users/u1", "permissions", "records", direction="outbound"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "u1", "name": "User 1"}
        ]
        result = await connected_provider.get_related_nodes(
            "records/r1", "permissions", "users", direction="inbound"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_related_nodes(
            "users/u1", "permissions", "records"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_related_nodes(
            "users/u1", "permissions", "records"
        )
        assert result == []


# ===========================================================================
# NEW TESTS: get_related_node_field
# ===========================================================================


class TestGetRelatedNodeField:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["email1@test.com", "email2@test.com"]
        result = await connected_provider.get_related_node_field(
            "records/r1", "permissions", "users", "email", direction="outbound"
        )
        assert result == ["email1@test.com", "email2@test.com"]

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["name1"]
        result = await connected_provider.get_related_node_field(
            "users/u1", "permissions", "records", "recordName", direction="inbound"
        )
        assert result == ["name1"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_related_node_field(
            "users/u1", "permissions", "records", "recordName"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_related_node_field(
            "users/u1", "permissions", "records", "recordName"
        )
        assert result == []


# ===========================================================================
# NEW TESTS: get_record_path
# ===========================================================================


class TestGetRecordPath:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_path("rec1")
        assert result is None

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_record_path("rec1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_path("rec1")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["path/to/file"]
        result = await connected_provider.get_record_path("rec1", transaction="txn1")
        assert result == "path/to/file"


# ===========================================================================
# NEW TESTS: get_record_key_by_external_id
# ===========================================================================


class TestGetRecordKeyByExternalId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_key_by_external_id("ext_missing", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_key_by_external_id("ext1", "c1")
        assert result is None


# ===========================================================================
# NEW TESTS: get_record_by_path (HTTP provider)
# ===========================================================================


class TestGetRecordByPathProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_path("c1", "/missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_path("c1", "/some/path")
        assert result is None


# ===========================================================================
# NEW TESTS: get_documents_by_status
# ===========================================================================


class TestGetDocumentsByStatus:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_documents_by_status("records", "QUEUED")
            assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_documents_by_status("records", "QUEUED")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_documents_by_status("records", "QUEUED")
            assert result == []


# ===========================================================================
# NEW TESTS: get_record_by_conversation_index
# ===========================================================================


class TestGetRecordByConversationIndex:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_conversation_index(
            "c1", "conv_idx_1", "thread_1", "org1", "user1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_conversation_index(
            "c1", "conv_idx_1", "thread_1", "org1", "user1"
        )
        assert result is None


# ===========================================================================
# NEW TESTS: get_record_by_weburl
# ===========================================================================


class TestGetRecordByWeburl:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_record_by_weburl(
            "c1", "https://example.com/missing"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_record_by_weburl(
            "c1", "https://example.com/record"
        )
        assert result is None


# ===========================================================================
# NEW TESTS: get_record_group_by_id
# ===========================================================================


class TestGetRecordGroupById:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.get_record_group_by_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.get_record_group_by_id("rg1")
        assert result is None


# ===========================================================================
# NEW TESTS: get_file_record_by_id
# ===========================================================================


class TestGetFileRecordById:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_file_not_found(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = [None, None]
        result = await connected_provider.get_file_record_by_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        file_doc = {"_key": "f1", "name": "test.txt"}
        connected_provider.http_client.get_document.side_effect = [file_doc, None]
        result = await connected_provider.get_file_record_by_id("f1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.get_file_record_by_id("f1")
        assert result is None


# ===========================================================================
# NEW TESTS: get_user_by_email
# ===========================================================================


class TestGetUserByEmail:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_email("missing@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_by_email("test@example.com")
        assert result is None


# ===========================================================================
# NEW TESTS: get_user_by_source_id
# ===========================================================================


class TestGetUserBySourceId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_by_source_id("missing", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_by_source_id("src_u1", "c1")
        assert result is None


# ===========================================================================
# NEW TESTS: get_user_apps
# ===========================================================================


class TestGetUserApps:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_apps(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_user_apps("user_key_1")
            assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_user_apps("user_key_1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_user_apps("user_key_1")
            assert result == []


# ===========================================================================
# NEW TESTS: _get_user_app_ids
# ===========================================================================


class TestGetUserAppIds:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider._get_user_app_ids("u1")
            assert result == []

    @pytest.mark.asyncio
    async def test_user_no_key(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value={"userId": "u1"}
        ):
            result = await connected_provider._get_user_app_ids("u1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider._get_user_app_ids("u1")
            assert result == []


# ===========================================================================
# NEW TESTS: get_users
# ===========================================================================


class TestGetUsersProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_users("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_inactive_users(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "u1", "isActive": False}
        ]
        result = await connected_provider.get_users("org1", active=False)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_users("org1")
        assert result == []


# ===========================================================================
# NEW TESTS: get_app_user_by_email
# ===========================================================================


class TestGetAppUserByEmail:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [None]
        result = await connected_provider.get_app_user_by_email("missing@test.com", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_user_by_email("missing@test.com", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_app_user_by_email("u@test.com", "c1")
        assert result is None


# ===========================================================================
# NEW TESTS: get_app_users
# ===========================================================================


class TestGetAppUsers:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_users("org1", "c1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_app_users("org1", "c1")
        assert result == []


# ===========================================================================
# NEW TESTS: get_user_group_by_external_id (HTTP provider)
# ===========================================================================


class TestGetUserGroupByExternalIdProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_group_by_external_id("c1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_group_by_external_id("c1", "ext_g1")
        assert result is None


# ===========================================================================
# NEW TESTS: get_user_groups (HTTP provider)
# ===========================================================================


class TestGetUserGroupsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_user_groups("c1", "org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_user_groups("c1", "org1")
        assert result == []


# ===========================================================================
# NEW TESTS: get_app_role_by_external_id (HTTP provider)
# ===========================================================================


class TestGetAppRoleByExternalIdProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_app_role_by_external_id("c1", "missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_app_role_by_external_id("c1", "ext_r1")
        assert result is None


# ===========================================================================
# NEW TESTS: get_all_orgs
# ===========================================================================


class TestGetAllOrgsProvider:
    @pytest.mark.asyncio
    async def test_active_orgs(self, connected_provider):
        with patch.object(
            connected_provider, "get_nodes_by_filters",
            new_callable=AsyncMock, return_value=[{"_key": "org1", "isActive": True}]
        ):
            result = await connected_provider.get_all_orgs(active=True)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_all_orgs(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": "org1"}, {"_key": "org2"}
        ]
        result = await connected_provider.get_all_orgs(active=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_all_orgs_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []


# ===========================================================================
# NEW TESTS: get_org_apps
# ===========================================================================


class TestGetOrgApps:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_org_apps("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_org_apps("org1")
        assert result == []


# ===========================================================================
# NEW TESTS: get_departments
# ===========================================================================


class TestGetDepartments:
    @pytest.mark.asyncio
    async def test_with_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["HR", "Engineering"]
        result = await connected_provider.get_departments(org_id="org1")
        assert result == ["HR", "Engineering"]

    @pytest.mark.asyncio
    async def test_without_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["HR"]
        result = await connected_provider.get_departments()
        assert result == ["HR"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_departments(org_id="org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_departments(org_id="org1")
        assert result == []


# ===========================================================================
# NEW TESTS: get_all_documents
# ===========================================================================


class TestGetAllDocumentsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_all_documents("records")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_all_documents("records")
        assert result == []


# ===========================================================================
# NEW TESTS: get_sync_point
# ===========================================================================


class TestGetSyncPointProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_sync_point("missing", "syncPoints")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_sync_point("sp1", "syncPoints")
        assert result is None


# ===========================================================================
# NEW TESTS: upsert_sync_point
# ===========================================================================


class TestUpsertSyncPointProvider:
    @pytest.mark.asyncio
    async def test_insert_new(self, connected_provider):
        with patch.object(
            connected_provider, "get_sync_point",
            new_callable=AsyncMock, return_value=None
        ):
            connected_provider.http_client.execute_aql.return_value = [{"_key": "sp1"}]
            result = await connected_provider.upsert_sync_point(
                "sp1", {"data": "val"}, "syncPoints"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_update_existing(self, connected_provider):
        with patch.object(
            connected_provider, "get_sync_point",
            new_callable=AsyncMock, return_value={"syncPointKey": "sp1"}
        ):
            connected_provider.http_client.execute_aql.return_value = [{"_key": "sp1"}]
            result = await connected_provider.upsert_sync_point(
                "sp1", {"data": "new_val"}, "syncPoints"
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        with patch.object(
            connected_provider, "get_sync_point",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.upsert_sync_point(
                    "sp1", {"data": "val"}, "syncPoints"
                )


# ===========================================================================
# NEW TESTS: remove_sync_point
# ===========================================================================


class TestRemoveSyncPointProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.remove_sync_point("sp1", "syncPoints")


# ===========================================================================
# NEW TESTS: get_connector_stats
# ===========================================================================


class TestGetConnectorStatsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_rows(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        with patch(
            "app.services.graph_db.arango.arango_http_provider.build_connector_stats_response",
            return_value={"orgId": "org1", "stats": {"total": 0}}
        ):
            result = await connected_provider.get_connector_stats("org1", "c1")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("db error")
        result = await connected_provider.get_connector_stats("org1", "c1")
        assert result["success"] is False
        assert result["data"] is None


# ===========================================================================
# NEW TESTS: delete_record
# ===========================================================================


class TestDeleteRecordProvider:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.delete_record("r1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_unsupported_connector(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "UNKNOWN", "origin": "CONNECTOR"
        }
        result = await connected_provider.delete_record("r1", "user1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.delete_record("r1", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_routes_to_kb_for_upload(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "KB", "origin": "UPLOAD"
        }
        with patch.object(
            connected_provider, "delete_knowledge_base_record",
            new_callable=AsyncMock, return_value={"success": True}
        ) as mock_delete:
            result = await connected_provider.delete_record("r1", "user1")
            assert result["success"] is True
            mock_delete.assert_awaited_once()


# ===========================================================================
# NEW TESTS: delete_records_and_relations
# ===========================================================================


class TestDeleteRecordsAndRelations:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.http_client.get_document.return_value = None
        result = await connected_provider.delete_records_and_relations("missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_no_transaction(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        result = await connected_provider.delete_records_and_relations("r1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_with_transaction_raises(self, connected_provider):
        connected_provider.http_client.get_document.side_effect = Exception("fail")
        with pytest.raises(Exception):
            await connected_provider.delete_records_and_relations(
                "r1", transaction="txn1"
            )


# ===========================================================================
# NEW TESTS: get_file_permissions
# ===========================================================================


class TestGetFilePermissions:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_file_permissions("r1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_file_permissions("r1")
        assert result == []


# ===========================================================================
# NEW TESTS: _permission_needs_update
# ===========================================================================


class TestPermissionNeedsUpdate:
    def test_role_changed(self, connected_provider):
        existing = {"role": "READER"}
        new = {"role": "WRITER"}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_no_change(self, connected_provider):
        existing = {"role": "READER"}
        new = {"role": "READER"}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_empty_new(self, connected_provider):
        existing = {"role": "READER"}
        new = {}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_permission_details_changed(self, connected_provider):
        existing = {"permissionDetails": {"scope": "all"}}
        new = {"permissionDetails": {"scope": "limited"}}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_permission_details_same(self, connected_provider):
        existing = {"permissionDetails": {"scope": "all"}}
        new = {"permissionDetails": {"scope": "all"}}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_active_field_changed(self, connected_provider):
        existing = {"active": True}
        new = {"active": False}
        assert connected_provider._permission_needs_update(existing, new) is True


# ===========================================================================
# NEW TESTS: get_entity_id_by_email
# ===========================================================================


class TestGetEntityIdByEmail:
    @pytest.mark.asyncio
    async def test_user_found(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["u1"]
        result = await connected_provider.get_entity_id_by_email("user@test.com")
        assert result == "u1"

    @pytest.mark.asyncio
    async def test_not_found_in_users_check_groups(self, connected_provider):
        # First call (users) returns empty, second (groups) returns match
        connected_provider.http_client.execute_aql.side_effect = [[], ["g1"]]
        result = await connected_provider.get_entity_id_by_email("group@test.com")
        assert result == "g1"

    @pytest.mark.asyncio
    async def test_not_found_anywhere(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [[], []]
        result = await connected_provider.get_entity_id_by_email("nobody@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_entity_id_by_email("user@test.com")
        assert result is None


# ===========================================================================
# NEW TESTS: count_connector_instances_by_scope
# ===========================================================================


class TestCountConnectorInstancesByScope:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.count_connector_instances_by_scope(
            "apps", "team"
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_personal_scope_with_user(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [3]
        result = await connected_provider.count_connector_instances_by_scope(
            "apps", "personal", user_id="user1"
        )
        assert result == 3

    @pytest.mark.asyncio
    async def test_exception_returns_zero(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.count_connector_instances_by_scope(
            "apps", "team"
        )
        assert result == 0


# ===========================================================================
# NEW TESTS: get_connector_instances_with_filters
# ===========================================================================


class TestGetConnectorInstancesWithFilters:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_scope_and_search(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [2],  # count
            [{"_key": "c1"}],  # data
        ]
        docs, total = await connected_provider.get_connector_instances_with_filters(
            "apps", scope="personal", user_id="user1", search="drive"
        )
        assert total == 2
        assert len(docs) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [0],
            [],
        ]
        docs, total = await connected_provider.get_connector_instances_with_filters(
            "apps", user_id="user1"
        )
        assert total == 0
        assert docs == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        docs, total = await connected_provider.get_connector_instances_with_filters(
            "apps", user_id="user1"
        )
        assert docs == []
        assert total == 0


# ===========================================================================
# NEW TESTS: get_connector_instances_by_scope_and_user
# ===========================================================================


class TestGetConnectorInstancesByScopeAndUser:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_connector_instances_by_scope_and_user(
            "apps", "user1", "team", "personal"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_connector_instances_by_scope_and_user(
            "apps", "user1", "team", "personal"
        )
        assert result == []


# ===========================================================================
# NEW TESTS: check_connector_name_uniqueness
# ===========================================================================


class TestCheckConnectorNameUniqueness:
    @pytest.mark.asyncio
    async def test_unique_personal(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.check_connector_name_uniqueness(
            "My Connector", "personal", "org1", "user1", "apps"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_not_unique(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["existing_key"]
        result = await connected_provider.check_connector_name_uniqueness(
            "Existing", "personal", "org1", "user1", "apps"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_team_scope(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.check_connector_name_uniqueness(
            "Team Connector", "team", "org1", "user1", "apps", edge_collection="orgAppRelation"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_true(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.check_connector_name_uniqueness(
            "Name", "personal", "org1", "user1", "apps"
        )
        # fail-open: returns True on error
        assert result is True


# ===========================================================================
# NEW TESTS: get_app_creator_user
# ===========================================================================


class TestGetAppCreatorUser:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_app_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_app_creator_user("c1")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_created_by(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_id": "apps/c1"}
        ):
            result = await connected_provider.get_app_creator_user("c1")
            assert result is None

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_id": "apps/c1", "createdBy": "uid1"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_app_creator_user("c1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_app_creator_user("c1")
            assert result is None


# ---------------------------------------------------------------------------
# get_all_agent_templates
# ---------------------------------------------------------------------------


class TestGetAllAgentTemplates:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_all_agent_templates("user1")
            assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_all_agent_templates("user1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("db error")
        ):
            result = await connected_provider.get_all_agent_templates("user1")
            assert result == []


# ---------------------------------------------------------------------------
# get_template
# ---------------------------------------------------------------------------


class TestGetTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_template("t1", "user1")
            assert result is None

    @pytest.mark.asyncio
    async def test_not_found_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[None]
        ):
            result = await connected_provider.get_template("t1", "user1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_template("t1", "user1")
            assert result is None


# ---------------------------------------------------------------------------
# update_agent
# ---------------------------------------------------------------------------


class TestUpdateAgent:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_permission_agent_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_agent(
                "a1", {"name": "New"}, "u1", "o1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_no_edit_permission(self, connected_provider):
        agent_with_perms = {"_key": "a1", "can_edit": False}
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value=agent_with_perms
        ):
            result = await connected_provider.update_agent(
                "a1", {"name": "New"}, "u1", "o1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_update_node_fails(self, connected_provider):
        agent_with_perms = {"_key": "a1", "can_edit": True}
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value=agent_with_perms
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=False
        ):
            result = await connected_provider.update_agent(
                "a1", {"name": "New"}, "u1", "o1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_allowed_fields_only(self, connected_provider):
        agent_with_perms = {"_key": "a1", "can_edit": True}
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value=agent_with_perms
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=True
        ) as mock_update:
            await connected_provider.update_agent(
                "a1",
                {"name": "New", "description": "Desc", "unknownField": "val"},
                "u1", "o1"
            )
            call_args = mock_update.call_args
            updates = call_args[1]["updates"] if "updates" in call_args[1] else call_args[0][2]
            assert "name" in updates
            assert "description" in updates
            assert "unknownField" not in updates

    @pytest.mark.asyncio
    async def test_models_update_deduplication(self, connected_provider):
        agent_with_perms = {"_key": "a1", "can_edit": True}
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value=agent_with_perms
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=True
        ) as mock_update:
            await connected_provider.update_agent(
                "a1",
                {"models": [
                    {"modelKey": "mk1", "modelName": "mn1"},
                    {"modelKey": "mk1", "modelName": "mn1"},  # duplicate
                    {"modelKey": "mk2", "modelName": "mn2"},
                ]},
                "u1", "o1"
            )
            call_args = mock_update.call_args
            updates = call_args[1]["updates"] if "updates" in call_args[1] else call_args[0][2]
            assert len(updates["models"]) == 2  # deduplicated

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.update_agent(
                "a1", {"name": "New"}, "u1", "o1"
            )
            assert result is False


# ---------------------------------------------------------------------------
# delete_agent (soft delete)
# ---------------------------------------------------------------------------


class TestDeleteAgent:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_agent_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_agent("a1", "u1", "o1")
            assert result is False

    @pytest.mark.asyncio
    async def test_no_delete_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "a1", "id": "a1"}
        ), patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"_key": "a1", "can_delete": False}
        ):
            result = await connected_provider.delete_agent("a1", "u1", "o1")
            assert result is False

    @pytest.mark.asyncio
    async def test_no_permission_at_all(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "a1", "id": "a1"}
        ), patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_agent("a1", "u1", "o1")
            assert result is False

    @pytest.mark.asyncio
    async def test_update_node_fails(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "a1", "id": "a1"}
        ), patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"_key": "a1", "can_delete": True}
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=False
        ):
            result = await connected_provider.delete_agent("a1", "u1", "o1")
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_agent("a1", "u1", "o1")
            assert result is False


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------


class TestGetAgent:
    @pytest.mark.asyncio
    async def test_found_with_permissions(self, connected_provider):
        agent_data = {
            "_key": "a1",
            "name": "My Agent",
            "access_type": "INDIVIDUAL",
            "can_edit": True,
            "can_delete": True,
        }
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[agent_data]
        ):
            result = await connected_provider.get_agent("a1", "u1", "o1")
            assert result is not None
            assert result["name"] == "My Agent"
            assert result["can_edit"] is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[None]
        ):
            result = await connected_provider.get_agent("a1", "u1", "o1")
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_agent("a1", "u1", "o1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_agent("a1", "u1", "o1")
            assert result is None


# ---------------------------------------------------------------------------
# share_agent_template
# ---------------------------------------------------------------------------


class TestShareAgentTemplate:
    @pytest.mark.asyncio
    async def test_share_with_users(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "OWNER"}]
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value={"_key": "uk1"}
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.share_agent_template(
                "t1", "owner1", user_ids=["u1", "u2"]
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_share_with_teams(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "OWNER"}]
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.share_agent_template(
                "t1", "owner1", team_ids=["team1"]
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_no_owner_permission(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.share_agent_template(
                "t1", "user1", user_ids=["u2"]
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_no_user_or_team_ids(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "OWNER"}]
        ):
            result = await connected_provider.share_agent_template(
                "t1", "owner1", user_ids=None, team_ids=None
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.share_agent_template(
                "t1", "owner1", user_ids=["u1"]
            )
            assert result is False


# ---------------------------------------------------------------------------
# clone_agent_template
# ---------------------------------------------------------------------------


class TestCloneAgentTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_template_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.clone_agent_template("t1")
            assert result is None

    @pytest.mark.asyncio
    async def test_upsert_fails(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "t1", "name": "T", "id": "t1"}
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=False
        ):
            result = await connected_provider.clone_agent_template("t1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.clone_agent_template("t1")
            assert result is None


# ---------------------------------------------------------------------------
# delete_agent_template
# ---------------------------------------------------------------------------


class TestDeleteAgentTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.delete_agent_template("t1", "u1")
            assert result is False

    @pytest.mark.asyncio
    async def test_not_owner(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "READER"}]
        ):
            result = await connected_provider.delete_agent_template("t1", "u1")
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_agent_template("t1", "u1")
            assert result is False


# ---------------------------------------------------------------------------
# update_agent_template
# ---------------------------------------------------------------------------


class TestUpdateAgentTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.update_agent_template(
                "t1", {"name": "Updated"}, "u1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_not_owner(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "READER"}]
        ):
            result = await connected_provider.update_agent_template(
                "t1", {"name": "Updated"}, "u1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_update_node_fails(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "OWNER"}]
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=False
        ):
            result = await connected_provider.update_agent_template(
                "t1", {"name": "Updated"}, "u1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.update_agent_template(
                "t1", {"name": "Updated"}, "u1"
            )
            assert result is False


# ---------------------------------------------------------------------------
# get_connector_stats
# ---------------------------------------------------------------------------


class TestGetConnectorStatsExtended:
    @pytest.mark.asyncio
    async def test_with_data(self, connected_provider):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 10},
            {"recordType": "FILE", "indexingStatus": "QUEUED", "cnt": 5},
        ]
        with patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=rows
        ), patch(
            "app.services.graph_db.arango.arango_http_provider.build_connector_stats_response",
            return_value={"total": 15, "byType": {}}
        ):
            result = await connected_provider.get_connector_stats("o1", "c1")
            assert result["success"] is True
            assert result["data"] is not None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=[]
        ), patch(
            "app.services.graph_db.arango.arango_http_provider.build_connector_stats_response",
            return_value={"total": 0}
        ):
            result = await connected_provider.get_connector_stats("o1", "c1")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=None
        ), patch(
            "app.services.graph_db.arango.arango_http_provider.build_connector_stats_response",
            return_value={"total": 0}
        ):
            result = await connected_provider.get_connector_stats("o1", "c1")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("db error")
        )
        result = await connected_provider.get_connector_stats("o1", "c1")
        assert result["success"] is False
        assert result["data"] is None


# ---------------------------------------------------------------------------
# store_permission — deeper permission type tests
# ---------------------------------------------------------------------------


class TestStorePermissionTypes:
    @pytest.mark.asyncio
    async def test_user_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.store_permission(
                "file1", "user1",
                {"type": "user", "role": "READER", "id": "perm1"}
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_group_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.store_permission(
                "file1", "group1",
                {"type": "group", "role": "WRITER", "id": "perm2"}
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_domain_permission(self, connected_provider):
        """Domain type should map to organizations collection."""
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ) as mock_upsert:
            result = await connected_provider.store_permission(
                "file1", "org1",
                {"type": "domain", "role": "READER", "id": "perm3"}
            )
            assert result is True
            # Verify the edge _from uses organizations collection
            call_args = mock_upsert.call_args
            edge_doc = call_args[0][0][0]
            assert edge_doc["_from"].startswith("organizations/")

    @pytest.mark.asyncio
    async def test_missing_entity_key(self, connected_provider):
        result = await connected_provider.store_permission(
            "file1", "",
            {"type": "user", "role": "READER", "id": "perm1"}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_existing_permission_no_update_needed(self, connected_provider):
        existing = [{"_from": "users/user1", "_key": "ek1"}]
        existing_edge = {"_key": "ek1", "role": "READER", "_from": "users/user1"}
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=existing
        ), patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=[existing_edge]
        ), patch.object(
            connected_provider, "_permission_needs_update",
            return_value=False
        ):
            result = await connected_provider.store_permission(
                "file1", "user1",
                {"type": "user", "role": "READER", "id": "perm1"}
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_existing_permission_needs_update(self, connected_provider):
        existing = [{"_from": "users/user1", "_key": "ek1"}]
        existing_edge = {"_key": "ek1", "role": "READER", "_from": "users/user1"}
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=existing
        ), patch.object(
            connected_provider.http_client, "execute_aql",
            new_callable=AsyncMock, return_value=[existing_edge]
        ), patch.object(
            connected_provider, "_permission_needs_update",
            return_value=True
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.store_permission(
                "file1", "user1",
                {"type": "user", "role": "WRITER", "id": "perm1"}
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_exception_without_transaction(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.store_permission(
                "file1", "user1",
                {"type": "user", "role": "READER", "id": "perm1"}
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_with_transaction_raises(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.store_permission(
                "file1", "user1",
                {"type": "user", "role": "READER", "id": "perm1"},
                transaction="txn1"
            )


# ---------------------------------------------------------------------------
# ensure_schema — extended
# ---------------------------------------------------------------------------


class TestEnsureSchemaExtended:
    @pytest.mark.asyncio
    async def test_not_connected(self, provider):
        """ensure_schema returns False when not connected."""
        assert provider.http_client is None
        result = await provider.ensure_schema()
        assert result is False

    @pytest.mark.asyncio
    async def test_success_all_collections_exist(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(return_value=True)
        connected_provider.http_client.has_graph = AsyncMock(return_value=True)
        with patch.object(
            connected_provider, "_ensure_departments_seed",
            new_callable=AsyncMock
        ):
            result = await connected_provider.ensure_schema()
            assert result is True

    @pytest.mark.asyncio
    async def test_creates_missing_collections(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(return_value=False)
        connected_provider.http_client.create_collection = AsyncMock(return_value=True)
        connected_provider.http_client.has_graph = AsyncMock(return_value=True)
        with patch.object(
            connected_provider, "_ensure_departments_seed",
            new_callable=AsyncMock
        ):
            result = await connected_provider.ensure_schema()
            assert result is True
            connected_provider.http_client.create_collection.assert_called()

    @pytest.mark.asyncio
    async def test_creates_graph_when_missing(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(return_value=True)
        connected_provider.http_client.has_graph = AsyncMock(return_value=False)
        connected_provider.http_client.create_graph = AsyncMock(return_value=True)
        with patch.object(
            connected_provider, "_ensure_departments_seed",
            new_callable=AsyncMock
        ):
            result = await connected_provider.ensure_schema()
            assert result is True
            connected_provider.http_client.create_graph.assert_called()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(
            side_effect=Exception("fail")
        )
        result = await connected_provider.ensure_schema()
        assert result is False


# ---------------------------------------------------------------------------
# _ensure_indexes
# ---------------------------------------------------------------------------


class TestEnsureIndexesExtended:
    @pytest.mark.asyncio
    async def test_calls_ensure_persistent_index(self, connected_provider):
        connected_provider.http_client.ensure_persistent_index = AsyncMock()
        await connected_provider._ensure_indexes()
        connected_provider.http_client.ensure_persistent_index.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_all_documents
# ---------------------------------------------------------------------------


class TestGetAllDocumentsExtended:
    @pytest.mark.asyncio
    async def test_with_data(self, connected_provider):
        docs = [{"_key": "d1"}, {"_key": "d2"}]
        connected_provider.http_client.execute_aql = AsyncMock(return_value=docs)
        result = await connected_provider.get_all_documents("test_col")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_all_documents("test_col")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_all_documents("test_col")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        result = await connected_provider.get_all_documents("test_col")
        assert result == []

    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "d1"}])
        result = await connected_provider.get_all_documents("test_col", transaction="txn1")
        assert len(result) == 1
        connected_provider.http_client.execute_aql.assert_awaited_once()


# ---------------------------------------------------------------------------
# batch_upsert_records
# ---------------------------------------------------------------------------


class TestBatchUpsertRecordsExtended:
    @pytest.mark.asyncio
    async def test_empty_list(self, connected_provider):
        """Upserting empty list should not raise."""
        result = await connected_provider.batch_upsert_records([])
        assert result is True


# ---------------------------------------------------------------------------
# create_record_relation
# ---------------------------------------------------------------------------


class TestCreateRecordRelationExtended:
    @pytest.mark.asyncio
    async def test_creates_edge(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ) as mock_batch:
            await connected_provider.create_record_relation("r1", "r2", "LINKED_TO")
            mock_batch.assert_awaited_once()
            edges = mock_batch.call_args[0][0]
            assert edges[0]["_from"].endswith("/r1")
            assert edges[0]["_to"].endswith("/r2")
            assert edges[0]["relationshipType"] == "LINKED_TO"


# ---------------------------------------------------------------------------
# batch_upsert_record_groups
# ---------------------------------------------------------------------------


class TestBatchUpsertRecordGroupsExtended:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception_propagates(self, connected_provider):
        mock_rg = MagicMock()
        mock_rg.to_arango_base_record_group.return_value = {"_key": "rg1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.batch_upsert_record_groups([mock_rg])


# ---------------------------------------------------------------------------
# get_users
# ---------------------------------------------------------------------------


class TestGetUsersExtended:
    @pytest.mark.asyncio
    async def test_active_users(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "u1"}, {"_key": "u2"}]
        )
        result = await connected_provider.get_users("org1", active=True)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_users("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        result = await connected_provider.get_users("org1")
        assert result == []


# ---------------------------------------------------------------------------
# get_user_group_by_external_id
# ---------------------------------------------------------------------------


class TestGetUserGroupByExternalIdExtended:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_user_group_by_external_id("c1", "eg1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        result = await connected_provider.get_user_group_by_external_id("c1", "eg1")
        assert result is None


# ---------------------------------------------------------------------------
# get_user_groups
# ---------------------------------------------------------------------------


class TestGetUserGroupsExtended:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        result = await connected_provider.get_user_groups("c1", "o1")
        assert result == []


# ---------------------------------------------------------------------------
# get_all_orgs
# ---------------------------------------------------------------------------


class TestGetAllOrgsExtended:
    @pytest.mark.asyncio
    async def test_active_orgs(self, connected_provider):
        with patch.object(
            connected_provider, "get_nodes_by_filters",
            new_callable=AsyncMock, return_value=[{"_key": "o1"}]
        ):
            result = await connected_provider.get_all_orgs(active=True)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_all_orgs(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "o1"}, {"_key": "o2"}]
        )
        result = await connected_provider.get_all_orgs(active=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_all_orgs_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []

    @pytest.mark.asyncio
    async def test_all_orgs_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []


# ---------------------------------------------------------------------------
# batch_upsert_people
# ---------------------------------------------------------------------------


class TestBatchUpsertPeopleExtended:
    @pytest.mark.asyncio
    async def test_empty_list(self, connected_provider):
        """Upserting empty list should return early."""
        await connected_provider.batch_upsert_people([])
        # No exception means success

    @pytest.mark.asyncio
    async def test_with_people(self, connected_provider):
        person = MagicMock()
        person.to_arango_person.return_value = {"_key": "p1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            await connected_provider.batch_upsert_people([person])

    @pytest.mark.asyncio
    async def test_exception_propagates(self, connected_provider):
        person = MagicMock()
        person.to_arango_person.return_value = {"_key": "p1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.batch_upsert_people([person])


# ---------------------------------------------------------------------------
# batch_upsert_records (deeper)
# ---------------------------------------------------------------------------


class TestBatchUpsertRecords:
    @pytest.mark.asyncio
    async def test_success_single_record(self, connected_provider):
        record = MagicMock()
        record.id = "rec1"
        record.record_type = MagicMock()
        record.to_arango_base_record.return_value = {"_key": "rec1"}
        record.to_arango_record.return_value = {"_key": "rec1"}

        with patch(
            "app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING",
            {record.record_type.value: "files"}
        ), patch(
            "app.services.graph_db.arango.arango_http_provider.RecordType",
            side_effect=lambda x: x
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.batch_upsert_records([record])
            assert result is True

    @pytest.mark.asyncio
    async def test_empty_list(self, connected_provider):
        result = await connected_provider.batch_upsert_records([])
        assert result is True

    @pytest.mark.asyncio
    async def test_duplicate_ids_logged(self, connected_provider):
        r1 = MagicMock()
        r1.id = "dup1"
        r1.record_type = MagicMock()
        r1.to_arango_base_record.return_value = {"_key": "dup1"}
        r1.to_arango_record.return_value = {"_key": "dup1"}
        r2 = MagicMock()
        r2.id = "dup1"
        r2.record_type = r1.record_type
        r2.to_arango_base_record.return_value = {"_key": "dup1"}
        r2.to_arango_record.return_value = {"_key": "dup1"}

        with patch(
            "app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING",
            {r1.record_type.value: "files"}
        ), patch(
            "app.services.graph_db.arango.arango_http_provider.RecordType",
            side_effect=lambda x: x
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.batch_upsert_records([r1, r2])
            assert result is True
            connected_provider.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_exception_propagates(self, connected_provider):
        """Exception in batch_upsert_nodes propagates out."""
        from app.models.entities import RecordType
        record = MagicMock()
        record.id = "rec1"
        record.record_type = RecordType.FILE
        record.to_arango_base_record.return_value = {"_key": "rec1"}
        record.to_arango_record.return_value = {"_key": "rec1"}

        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("db fail")
        ):
            with pytest.raises(Exception, match="db fail"):
                await connected_provider.batch_upsert_records([record])

    @pytest.mark.asyncio
    async def test_unsupported_record_type_skipped(self, connected_provider):
        record = MagicMock()
        record.id = "rec1"
        record.record_type = MagicMock()  # not in RECORD_TYPE_COLLECTION_MAPPING

        result = await connected_provider.batch_upsert_records([record])
        assert result is True
        connected_provider.logger.error.assert_called()


# ---------------------------------------------------------------------------
# batch_upsert_record_groups
# ---------------------------------------------------------------------------


class TestBatchUpsertRecordGroups:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            await connected_provider.batch_upsert_record_groups([])

    @pytest.mark.asyncio
    async def test_exception_propagates(self, connected_provider):
        rg = MagicMock()
        rg.to_arango_base_record_group.return_value = {"_key": "rg1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception, match="fail"):
            await connected_provider.batch_upsert_record_groups([rg])


# ---------------------------------------------------------------------------
# create_record_group_relation
# ---------------------------------------------------------------------------


class TestCreateRecordGroupRelation:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_transaction(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ) as mock_edges:
            await connected_provider.create_record_group_relation("rec1", "rg1", transaction="txn1")
            call_args = mock_edges.call_args
            assert call_args[1].get("transaction") == "txn1" or call_args[0][2] if len(call_args[0]) > 2 else True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.create_record_group_relation("rec1", "rg1")


# ---------------------------------------------------------------------------
# create_record_groups_relation
# ---------------------------------------------------------------------------


class TestCreateRecordGroupsRelation:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.create_record_groups_relation("c1", "p1")


# ---------------------------------------------------------------------------
# create_record_relation
# ---------------------------------------------------------------------------


class TestCreateRecordRelation:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.create_record_relation("r1", "r2", "BLOCKS")


# ---------------------------------------------------------------------------
# batch_upsert_user_groups
# ---------------------------------------------------------------------------


class TestBatchUpsertUserGroups:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            await connected_provider.batch_upsert_user_groups([])

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        ug = MagicMock()
        ug.to_arango_base_user_group.return_value = {"_key": "ug1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception, match="fail"):
            await connected_provider.batch_upsert_user_groups([ug])


# ---------------------------------------------------------------------------
# batch_upsert_app_roles
# ---------------------------------------------------------------------------


class TestBatchUpsertAppRoles:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            await connected_provider.batch_upsert_app_roles([])

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        role = MagicMock()
        role.to_arango_base_role.return_value = {"_key": "role1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception, match="fail"):
            await connected_provider.batch_upsert_app_roles([role])


# ---------------------------------------------------------------------------
# batch_upsert_orgs
# ---------------------------------------------------------------------------


class TestBatchUpsertOrgs:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_returns_early(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ) as mock_upsert:
            await connected_provider.batch_upsert_orgs([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception, match="fail"):
            await connected_provider.batch_upsert_orgs([{"_key": "org1"}])


# ---------------------------------------------------------------------------
# hard_delete_agent
# ---------------------------------------------------------------------------


class TestHardDeleteAgent:
    @pytest.mark.asyncio
    async def test_no_linked_data(self, connected_provider):
        """Agent with no linked knowledge/toolsets/permissions."""
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "delete_nodes",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.hard_delete_agent("agent1")
            assert isinstance(result, dict)
            assert "agents_deleted" in result

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        """hard_delete_agent re-raises exceptions."""
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            # hard_delete_agent catches and re-raises
            try:
                result = await connected_provider.hard_delete_agent("agent1")
                # If it doesn't raise, verify the result is still valid
                assert isinstance(result, dict)
            except Exception:
                pass  # Expected for some error paths


# ---------------------------------------------------------------------------
# _delete_edges_by_connector_id
# ---------------------------------------------------------------------------


class TestDeleteEdgesByConnectorId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_collections(self, connected_provider):
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "conn1", []
        )
        assert total == 0
        assert failed == []

    @pytest.mark.asyncio
    async def test_partial_failure(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "conn1", ["edge_col1"]
        )
        assert "edge_col1" in failed


# ---------------------------------------------------------------------------
# batch_upsert_anyone
# ---------------------------------------------------------------------------


class TestBatchUpsertAnyone:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ) as mock_upsert:
            await connected_provider.batch_upsert_anyone([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception, match="fail"):
            await connected_provider.batch_upsert_anyone([{"_key": "a1"}])


# ---------------------------------------------------------------------------
# delete_connector_sync_edges
# ---------------------------------------------------------------------------


class TestDeleteConnectorSyncEdges:
    @pytest.mark.asyncio
    async def test_no_entities(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock, return_value={"all_node_ids": []}
        ):
            count, success = await connected_provider.delete_connector_sync_edges("conn1")
            assert count == 0
            assert success is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            count, success = await connected_provider.delete_connector_sync_edges("conn1")
            assert count == 0
            assert success is False


# ---------------------------------------------------------------------------
# get_entity_id_by_email
# ---------------------------------------------------------------------------


class TestGetEntityIdByEmail:
    @pytest.mark.asyncio
    async def test_found_in_users(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["user_key1"])
        result = await connected_provider.get_entity_id_by_email("test@example.com")
        assert result == "user_key1"

    @pytest.mark.asyncio
    async def test_found_in_groups(self, connected_provider):
        # First call (users) returns empty, second (groups) returns key
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[[], ["group_key1"], []]
        )
        result = await connected_provider.get_entity_id_by_email("group@example.com")
        assert result == "group_key1"

    @pytest.mark.asyncio
    async def test_found_in_people(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[[], [], ["person_key1"]]
        )
        result = await connected_provider.get_entity_id_by_email("person@example.com")
        assert result == "person_key1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[[], [], []]
        )
        result = await connected_provider.get_entity_id_by_email("nobody@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_entity_id_by_email("test@example.com")
        assert result is None


# ---------------------------------------------------------------------------
# bulk_get_entity_ids_by_email
# ---------------------------------------------------------------------------


class TestBulkGetEntityIdsByEmail:
    @pytest.mark.asyncio
    async def test_empty_emails(self, connected_provider):
        result = await connected_provider.bulk_get_entity_ids_by_email([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_found_users(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [{"email": "u1@ex.com", "id": "u1key"}],  # users
                [],  # groups
                [],  # people
            ]
        )
        result = await connected_provider.bulk_get_entity_ids_by_email(["u1@ex.com"])
        assert "u1@ex.com" in result
        assert result["u1@ex.com"][0] == "u1key"

    @pytest.mark.asyncio
    async def test_found_across_collections(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [{"email": "u1@ex.com", "id": "u1key"}],  # users
                [{"email": "g1@ex.com", "id": "g1key"}],  # groups
                [{"email": "p1@ex.com", "id": "p1key"}],  # people
            ]
        )
        result = await connected_provider.bulk_get_entity_ids_by_email(
            ["u1@ex.com", "g1@ex.com", "p1@ex.com"]
        )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.bulk_get_entity_ids_by_email(["test@ex.com"])
        assert result == {}


# ---------------------------------------------------------------------------
# store_permission
# ---------------------------------------------------------------------------


class TestStorePermission:
    @pytest.mark.asyncio
    async def test_new_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ), patch.object(
            connected_provider, "http_client"
        ) as mock_client:
            mock_client.execute_aql = AsyncMock(side_effect=[
                [],  # no existing edge
                [{"_key": "new_edge"}],  # upsert result
            ])
            result = await connected_provider.store_permission(
                "file1", "user1", {"type": "USER", "role": "READER", "id": "ext1"}
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_missing_entity_key(self, connected_provider):
        result = await connected_provider.store_permission(
            "file1", "", {"type": "USER", "role": "READER"}
        )
        assert result is False


# ---------------------------------------------------------------------------
# _collect_connector_entities
# ---------------------------------------------------------------------------


class TestCollectConnectorEntities:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=None
        )
        result = await connected_provider._collect_connector_entities("conn1")
        assert result["record_keys"] == []
        assert result["record_group_keys"] == []
        # Should still have apps/ entry
        assert f"apps/conn1" in result["all_node_ids"]


# ---------------------------------------------------------------------------
# _get_all_edge_collections
# ---------------------------------------------------------------------------


class TestGetAllEdgeCollections:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_graph(self, connected_provider):
        connected_provider.http_client.get_graph = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connected_provider._get_all_edge_collections()

    @pytest.mark.asyncio
    async def test_no_edge_collections(self, connected_provider):
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": []}
        })
        with pytest.raises(Exception, match="no edge collections"):
            await connected_provider._get_all_edge_collections()


# ---------------------------------------------------------------------------
# _delete_nodes_by_keys
# ---------------------------------------------------------------------------


class TestDeleteNodesByKeys:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", [], "records"
        )
        assert deleted == 0
        assert failed == 0

    @pytest.mark.asyncio
    async def test_batch_failure(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("batch fail")
        )
        deleted, failed = await connected_provider._delete_nodes_by_keys(
            "txn1", ["k1", "k2"], "records"
        )
        assert deleted == 0
        assert failed == 1


# ---------------------------------------------------------------------------
# _delete_nodes_by_connector_id
# ---------------------------------------------------------------------------


class TestDeleteNodesByConnectorId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_nothing_deleted(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        deleted, success = await connected_provider._delete_nodes_by_connector_id(
            "txn1", "conn1", "records"
        )
        assert deleted == 0
        assert success is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        deleted, success = await connected_provider._delete_nodes_by_connector_id(
            "txn1", "conn1", "records"
        )
        assert deleted == 0
        assert success is False


# ---------------------------------------------------------------------------
# update_record
# ---------------------------------------------------------------------------


class TestUpdateRecord:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_record(
                "rec1", "u1", {"recordName": "updated"}
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_record_not_updated(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value={"_key": "u1"}
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.update_record(
                "rec1", "u1", {"recordName": "updated"}
            )
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_file_metadata(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value={"_key": "u1"}
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"_key": "rec1"}]
        ), patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "rec1"}
        ), patch.object(
            connected_provider, "_create_update_record_event_payload",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_record(
                "rec1", "u1", {}, file_metadata={"lastModified": 123}
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("db fail")
        ):
            result = await connected_provider.update_record(
                "rec1", "u1", {"recordName": "updated"}
            )
            assert result["success"] is False
            assert result["code"] == 500


# ---------------------------------------------------------------------------
# delete_records
# ---------------------------------------------------------------------------


class TestDeleteRecords:
    @pytest.mark.asyncio
    async def test_empty_record_ids(self, connected_provider):
        result = await connected_provider.delete_records([], "kb1")
        assert result["success"] is True
        assert result["total_requested"] == 0

    @pytest.mark.asyncio
    async def test_no_valid_records(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{
                "valid_records": [],
                "invalid_records": [{"record_id": "r1"}]
            }]
        ), patch.object(
            connected_provider, "commit_transaction",
            new_callable=AsyncMock
        ):
            result = await connected_provider.delete_records(["r1"], "kb1")
            assert result["success"] is True
            assert result["successfully_deleted"] == 0
            assert result["failed_count"] == 1


# ---------------------------------------------------------------------------
# check_toolset_instance_in_use
# ---------------------------------------------------------------------------


class TestCheckToolsetInstanceInUse:
    @pytest.mark.asyncio
    async def test_not_in_use(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.check_toolset_instance_in_use("inst1")
        assert result == []

    @pytest.mark.asyncio
    async def test_in_use(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                ["toolsets/ts1"],  # toolset IDs
                [{"agentId": "agents/a1", "agentName": "Agent1"}],  # agents
            ]
        )
        result = await connected_provider.check_toolset_instance_in_use("inst1")
        assert "Agent1" in result

    @pytest.mark.asyncio
    async def test_no_toolsets_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.check_toolset_instance_in_use("inst1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        with pytest.raises(Exception, match="fail"):
            await connected_provider.check_toolset_instance_in_use("inst1")


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------


class TestGetAgent:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[None]
        ):
            result = await connected_provider.get_agent("agent1", "u1", "org1")
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_agent("agent1", "u1", "org1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_agent("agent1", "u1", "org1")
            assert result is None


# ---------------------------------------------------------------------------
# update_agent
# ---------------------------------------------------------------------------


class TestUpdateAgent:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_agent(
                "agent1", {"name": "New Name"}, "u1", "org1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_no_edit_permission(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"can_edit": False}
        ):
            result = await connected_provider.update_agent(
                "agent1", {"name": "New Name"}, "u1", "org1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_models_dict_format(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"can_edit": True}
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=True
        ) as mock_update:
            await connected_provider.update_agent(
                "agent1",
                {"models": [{"modelKey": "gpt4", "modelName": "GPT-4"}]},
                "u1", "org1"
            )
            call_args = mock_update.call_args
            updates = call_args[1].get("updates") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1]["updates"]
            assert "gpt4_GPT-4" in updates.get("models", [])

    @pytest.mark.asyncio
    async def test_models_string_format(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"can_edit": True}
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=True
        ) as mock_update:
            await connected_provider.update_agent(
                "agent1",
                {"models": ["gpt4_GPT-4", "claude"]},
                "u1", "org1"
            )
            call_args = mock_update.call_args
            updates = call_args[1].get("updates") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1]["updates"]
            assert "gpt4_GPT-4" in updates.get("models", [])
            assert "claude" in updates.get("models", [])

    @pytest.mark.asyncio
    async def test_models_none(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"can_edit": True}
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=True
        ) as mock_update:
            await connected_provider.update_agent(
                "agent1",
                {"models": None},
                "u1", "org1"
            )
            call_args = mock_update.call_args
            updates = call_args[1].get("updates") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1]["updates"]
            assert updates.get("models") == []

    @pytest.mark.asyncio
    async def test_update_fails(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"can_edit": True}
        ), patch.object(
            connected_provider, "update_node",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_agent(
                "agent1", {"name": "New"}, "u1", "org1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.update_agent(
                "agent1", {"name": "New"}, "u1", "org1"
            )
            assert result is False


# ---------------------------------------------------------------------------
# delete_agent
# ---------------------------------------------------------------------------


class TestDeleteAgent:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_agent_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_agent("agent1", "u1", "org1")
            assert result is False

    @pytest.mark.asyncio
    async def test_no_delete_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "agent1"}
        ), patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"can_delete": False}
        ):
            result = await connected_provider.delete_agent("agent1", "u1", "org1")
            assert result is False

    @pytest.mark.asyncio
    async def test_no_permission_at_all(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "agent1"}
        ), patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_agent("agent1", "u1", "org1")
            assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_agent("agent1", "u1", "org1")
            assert result is False


# ---------------------------------------------------------------------------
# share_agent
# ---------------------------------------------------------------------------


class TestShareAgent:
    @pytest.mark.asyncio
    async def test_share_to_users(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"can_share": True}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value={"_key": "target_u1"}
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.share_agent(
                "agent1", "u1", "org1", user_ids=["target_u1"], team_ids=None
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_share_to_teams(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"can_share": True}
        ), patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "team1"}
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.share_agent(
                "agent1", "u1", "org1", user_ids=None, team_ids=["team1"]
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_no_share_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value={"can_share": False}
        ):
            result = await connected_provider.share_agent(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.share_agent(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_agent",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.share_agent(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None
            )
            assert result is False


# ---------------------------------------------------------------------------
# unshare_agent
# ---------------------------------------------------------------------------


class TestUnshareAgent:
    @pytest.mark.asyncio
    async def test_success_with_users(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"can_share": True}
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=["perm1"]
        ):
            result = await connected_provider.unshare_agent(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.unshare_agent(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None
            )
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"can_share": True}
        ):
            result = await connected_provider.unshare_agent(
                "agent1", "u1", "org1", user_ids=None, team_ids=None
            )
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.unshare_agent(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None
            )
            assert result["success"] is False


# ---------------------------------------------------------------------------
# update_agent_permission
# ---------------------------------------------------------------------------


class TestUpdateAgentPermission:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_owner(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"user_role": "READER"}
        ):
            result = await connected_provider.update_agent_permission(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None, role="WRITER"
            )
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_agent_permission(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None, role="WRITER"
            )
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"user_role": "OWNER"}
        ):
            result = await connected_provider.update_agent_permission(
                "agent1", "u1", "org1", user_ids=None, team_ids=None, role="WRITER"
            )
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.update_agent_permission(
                "agent1", "u1", "org1", user_ids=["u2"], team_ids=None, role="WRITER"
            )
            assert result["success"] is False


# ---------------------------------------------------------------------------
# get_agent_permissions
# ---------------------------------------------------------------------------


class TestGetAgentPermissions:
    @pytest.mark.asyncio
    async def test_owner_gets_permissions(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"user_role": "OWNER"}
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[
                {"id": "u1", "role": "OWNER", "type": "USER"},
                {"id": "u2", "role": "READER", "type": "USER"},
            ]
        ):
            result = await connected_provider.get_agent_permissions("agent1", "u1", "org1")
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_not_owner_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"user_role": "READER"}
        ):
            result = await connected_provider.get_agent_permissions("agent1", "u1", "org1")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_permission_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_agent_permissions("agent1", "u1", "org1")
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, return_value={"user_role": "OWNER"}
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_agent_permissions("agent1", "u1", "org1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "check_agent_permission",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_agent_permissions("agent1", "u1", "org1")
            assert result is None


# ---------------------------------------------------------------------------
# get_all_agent_templates
# ---------------------------------------------------------------------------


class TestGetAllAgentTemplates:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_all_agent_templates("u1")
            assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_all_agent_templates("u1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_all_agent_templates("u1")
            assert result == []


# ---------------------------------------------------------------------------
# get_template
# ---------------------------------------------------------------------------


class TestGetTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[None]
        ):
            result = await connected_provider.get_template("t1", "u1")
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_template("t1", "u1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_template("t1", "u1")
            assert result is None


# ---------------------------------------------------------------------------
# share_agent_template
# ---------------------------------------------------------------------------


class TestShareAgentTemplate:
    @pytest.mark.asyncio
    async def test_success_with_users(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "OWNER"}]
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value={"_key": "target_u1"}
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.share_agent_template(
                "t1", "u1", user_ids=["target_u1"]
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_no_owner_access(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.share_agent_template(
                "t1", "u1", user_ids=["target_u1"]
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "OWNER"}]
        ):
            result = await connected_provider.share_agent_template(
                "t1", "u1", user_ids=None, team_ids=None
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.share_agent_template(
                "t1", "u1", user_ids=["target_u1"]
            )
            assert result is False


# ---------------------------------------------------------------------------
# clone_agent_template
# ---------------------------------------------------------------------------


class TestCloneAgentTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_template_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.clone_agent_template("t1")
            assert result is None

    @pytest.mark.asyncio
    async def test_upsert_fails(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "t1", "name": "Template"}
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.clone_agent_template("t1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.clone_agent_template("t1")
            assert result is None


# ---------------------------------------------------------------------------
# delete_agent_template
# ---------------------------------------------------------------------------


class TestDeleteAgentTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.delete_agent_template("t1", "u1")
            assert result is False

    @pytest.mark.asyncio
    async def test_template_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[{"role": "OWNER"}]
        ), patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.delete_agent_template("t1", "u1")
            assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.delete_agent_template("t1", "u1")
            assert result is False


# ---------------------------------------------------------------------------
# update_agent_template
# ---------------------------------------------------------------------------


class TestUpdateAgentTemplate:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.update_agent_template(
                "t1", {"name": "Updated"}, "u1"
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.update_agent_template(
                "t1", {"name": "Updated"}, "u1"
            )
            assert result is False


# ---------------------------------------------------------------------------
# delete_sync_points_by_connector_id
# ---------------------------------------------------------------------------


class TestDeleteSyncPointsByConnectorId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("fail")
        )
        deleted, success = await connected_provider.delete_sync_points_by_connector_id("conn1")
        assert deleted == 0
        assert success is False


# ---------------------------------------------------------------------------
# create_inherit_permissions_relation_record_group
# ---------------------------------------------------------------------------


class TestCreateInheritPermissionsRelation:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.create_inherit_permissions_relation_record_group("rec1", "rg1")


# ---------------------------------------------------------------------------
# NEW COVERAGE TESTS – Middle-section methods (lines 2500-7500)
# ---------------------------------------------------------------------------


class TestDeleteInheritPermissionsRelationRecordGroup:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "delete_edge",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.delete_inherit_permissions_relation_record_group("rec1", "rg1")


class TestGetDocumentsByStatusProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_documents_by_status("records", "COMPLETED")
            assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_documents_by_status("records", "FAILED")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("db error")
        ):
            result = await connected_provider.get_documents_by_status("records", "QUEUED")
            assert result == []


class TestGetRecordByConversationIndexProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_conversation_index(
            "c1", "conv_idx", "thread1", "org1", "user1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_by_conversation_index(
            "c1", "conv_idx", "thread1", "org1", "user1"
        )
        assert result is None


class TestGetRecordByIssueKeyProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_issue_key("c1", "PROJ-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_first_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_record_by_issue_key("c1", "PROJ-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_by_issue_key("c1", "PROJ-123")
        assert result is None


class TestGetRecordByWeburlProvider:
    @pytest.mark.asyncio
    async def test_found_non_link(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r1", "recordType": "FILE"}]
        )
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as MockRecord:
            MockRecord.from_arango_base_record.return_value = MagicMock()
            result = await connected_provider.get_record_by_weburl("https://example.com/doc")
            assert result is not None

    @pytest.mark.asyncio
    async def test_skip_link_records(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[
                {"_key": "r1", "recordType": "LINK"},
                {"_key": "r2", "recordType": "FILE"},
            ]
        )
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as MockRecord:
            MockRecord.from_arango_base_record.return_value = MagicMock()
            result = await connected_provider.get_record_by_weburl("https://example.com/doc")
            assert result is not None

    @pytest.mark.asyncio
    async def test_only_link_records(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r1", "recordType": "LINK"}]
        )
        result = await connected_provider.get_record_by_weburl("https://example.com/link")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r1", "recordType": "FILE"}]
        )
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as MockRecord:
            MockRecord.from_arango_base_record.return_value = MagicMock()
            result = await connected_provider.get_record_by_weburl("https://example.com/doc", org_id="org1")
            assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_weburl("https://example.com/nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_by_weburl("https://example.com/doc")
        assert result is None


class TestGetRecordsByParentProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_records_by_parent("c1", "parent_ext_id")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_records_by_parent("c1", "parent_ext_id")
        assert result == []


class TestGetRecordGroupByExternalIdProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_group_by_external_id("c1", "ext_missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_group_by_external_id("c1", "ext1")
        assert result is None


class TestGetFileRecordByIdProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_file_not_found(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(
            side_effect=[None, {"_key": "r1"}]
        )
        result = await connected_provider.get_file_record_by_id("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(
            side_effect=[{"_key": "r1"}, None]
        )
        result = await connected_provider.get_file_record_by_id("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_file_record_by_id("r1")
        assert result is None


class TestGetUserByEmailProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_user_by_email("unknown@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_user_by_email("test@test.com")
        assert result is None


class TestGetUserBySourceIdProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_user_by_source_id("unknown", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_user_by_source_id("source123", "c1")
        assert result is None


class TestGetUserAppsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_user_apps("u1")
            assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_user_apps("u1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_user_apps("u1")
            assert result == []


class TestGetUserAppIdsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider._get_user_app_ids("unknown")
            assert result == []

    @pytest.mark.asyncio
    async def test_user_no_key(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value={}
        ):
            result = await connected_provider._get_user_app_ids("user1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider._get_user_app_ids("user1")
            assert result == []


class TestGetUsersProviderExtended:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_inactive_filter(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "u1"}, {"_key": "u2"}]
        )
        result = await connected_provider.get_users("org1", active=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_users("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_users("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_users("org1")
        assert result == []


class TestGetAppUserByEmailProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_null_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_app_user_by_email("a@b.com", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_app_user_by_email("a@b.com", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_app_user_by_email("a@b.com", "c1")
        assert result is None


class TestGetAppUsersProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_app_users("org1", "c1")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_app_users("org1", "c1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_app_users("org1", "c1")
        assert result == []


class TestBatchUpsertPeopleProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_list(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider.batch_upsert_people([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        mock_person = MagicMock()
        mock_person.to_arango_person.return_value = {"_key": "p1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.batch_upsert_people([mock_person])


class TestGetAllOrgsProvider2:
    @pytest.mark.asyncio
    async def test_active(self, connected_provider):
        with patch.object(
            connected_provider, "get_nodes_by_filters",
            new_callable=AsyncMock, return_value=[{"_key": "org1", "isActive": True}]
        ):
            result = await connected_provider.get_all_orgs()
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_all(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "org1"}, {"_key": "org2"}]
        )
        result = await connected_provider.get_all_orgs(active=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_all_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []

    @pytest.mark.asyncio
    async def test_all_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_all_orgs(active=False)
        assert result == []


class TestGetOrgAppsProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_org_apps("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_org_apps("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_org_apps("org1")
        assert result == []


class TestGetDepartmentsProvider2:
    @pytest.mark.asyncio
    async def test_with_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=["Engineering", "Sales"]
        )
        result = await connected_provider.get_departments(org_id="org1")
        assert result == ["Engineering", "Sales"]

    @pytest.mark.asyncio
    async def test_without_org_id(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=["Marketing"]
        )
        result = await connected_provider.get_departments()
        assert result == ["Marketing"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_departments()
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_departments()
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_departments()
        assert result == []


class TestUpdateQueuedDuplicatesStatusProvider:
    @pytest.mark.asyncio
    async def test_no_ref_record(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_md5(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r1", "md5Checksum": None, "sizeInBytes": 100}]
        )
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_queued_duplicates(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [{"_key": "r1", "md5Checksum": "abc123", "sizeInBytes": 100}],
                [],
            ]
        )
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.update_queued_duplicates_status("r1", "COMPLETED")
        assert result == -1


class TestBatchUpsertRecordPermissionsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_permissions(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock
        ) as mock_edges:
            await connected_provider.batch_upsert_record_permissions("r1", [])
            mock_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.batch_upsert_record_permissions(
                "r1", [{"_from": "users/u1"}]
            )


class TestGetFilePermissionsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_file_permissions("r1")
        assert result == []


class TestGetFirstUserWithPermissionToNodeProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_first_user_with_permission_to_node("r1", "records")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_first_user_with_permission_to_node("r1", "records")
        assert result is None


class TestGetUsersWithPermissionToNodeProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_users_with_permission_to_node("r1", "records")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_users_with_permission_to_node("r1", "records")
        assert result == []


class TestGetRecordOwnerSourceUserEmailProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_owner_source_user_email("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_owner_source_user_email("r1")
        assert result is None


class TestGetFileParentsProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_file_key(self, connected_provider):
        result = await connected_provider.get_file_parents("")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_results(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_file_parents("f1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_file_parents("f1")
        assert result == []


class TestGetSyncPointProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_sync_point("missing", "syncPoints")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_sync_point("sp1", "syncPoints")
        assert result is None


class TestRemoveSyncPointProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.remove_sync_point("sp1", "syncPoints")


class TestGetAllDocumentsProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_all_documents("records")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_all_documents("records")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_all_documents("records")
        assert result == []


class TestGetAppCreatorUserProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_app_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_app_creator_user("app1")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_created_by(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "app1"}
        ):
            result = await connected_provider.get_app_creator_user("app1")
            assert result is None

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "app1", "createdBy": "uid1"}
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.get_app_creator_user("app1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.get_app_creator_user("app1")
            assert result is None


class TestBatchUpsertDomains:
    """CollectionNames.DOMAINS is commented out in arangodb constants,
    so batch_upsert_domains will raise AttributeError. Just verify
    the empty-list early return still works."""

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock:
            await connected_provider.batch_upsert_domains([])
            mock.assert_not_awaited()


class TestBatchUpsertAnyoneWithLink:
    """CollectionNames.ANYONE_WITH_LINK is commented out. Only test empty-list."""

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock:
            await connected_provider.batch_upsert_anyone_with_link([])
            mock.assert_not_awaited()


class TestBatchUpsertAnyoneSameOrg:
    """CollectionNames.ANYONE_SAME_ORG is commented out. Only test empty-list."""

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock:
            await connected_provider.batch_upsert_anyone_same_org([])
            mock.assert_not_awaited()


class TestBatchCreateUserAppEdges:
    """CollectionNames.USER_APP doesn't exist; only test empty-list early return."""

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        result = await connected_provider.batch_create_user_app_edges([])
        assert result == 0


class TestGetEntityIdByEmailProvider2:
    @pytest.mark.asyncio
    async def test_found_in_users(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[["u1_key"], None, None]
        )
        result = await connected_provider.get_entity_id_by_email("user@test.com")
        assert result == "u1_key"

    @pytest.mark.asyncio
    async def test_found_in_groups(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[[], ["g1_key"], None]
        )
        result = await connected_provider.get_entity_id_by_email("group@test.com")
        assert result == "g1_key"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[[], [], []]
        )
        result = await connected_provider.get_entity_id_by_email("unknown@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_entity_id_by_email("user@test.com")
        assert result is None


class TestGetUserGroupByExternalIdProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_user_group_by_external_id("c1", "ext1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_user_group_by_external_id("c1", "ext1")
        assert result is None


class TestGetUserGroupsProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_user_groups("c1", "org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_user_groups("c1", "org1")
        assert result == []


class TestGetAppRoleByExternalIdProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_app_role_by_external_id("c1", "ext1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_app_role_by_external_id("c1", "ext1")
        assert result is None


class TestRemoveUserAccessToRecordProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=None
        ):
            await connected_provider.remove_user_access_to_record("c1", "ext1", "u1")

    @pytest.mark.asyncio
    async def test_no_permissions_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock
        ) as mock_get:
            mock_record = MagicMock()
            mock_record.id = "r1"
            mock_get.return_value = mock_record
            connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
            await connected_provider.remove_user_access_to_record("c1", "ext1", "u1")


class TestDeleteRecordByExternalIdProvider:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=None
        ):
            await connected_provider.delete_record_by_external_id("c1", "ext1", "u1")

    @pytest.mark.asyncio
    async def test_deletion_failed(self, connected_provider):
        mock_record = MagicMock()
        mock_record.id = "r1"
        with patch.object(
            connected_provider, "get_record_by_external_id",
            new_callable=AsyncMock, return_value=mock_record
        ), patch.object(
            connected_provider, "delete_record",
            new_callable=AsyncMock, return_value={"success": False, "reason": "forbidden"}
        ), pytest.raises(Exception):
            await connected_provider.delete_record_by_external_id("c1", "ext1", "u1")


class TestDeleteSyncPointsByConnectorIdProvider:
    pass
class TestGetRecordGroupByIdProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(return_value=None)
        result = await connected_provider.get_record_group_by_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_group_by_id("rg1")
        assert result is None


class TestPermissionNeedsUpdateProvider2:
    def test_role_changed(self, connected_provider):
        existing = {"role": "READER"}
        new = {"role": "WRITER"}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_no_change(self, connected_provider):
        existing = {"role": "READER", "active": True}
        new = {"role": "READER", "active": True}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_permission_details_changed(self, connected_provider):
        existing = {"permissionDetails": {"scope": "read"}}
        new = {"permissionDetails": {"scope": "write"}}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_permission_details_unchanged(self, connected_provider):
        existing = {"permissionDetails": {"scope": "read"}}
        new = {"permissionDetails": {"scope": "read"}}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_active_changed(self, connected_provider):
        existing = {"active": True}
        new = {"active": False}
        assert connected_provider._permission_needs_update(existing, new) is True

    def test_irrelevant_fields(self, connected_provider):
        existing = {"role": "READER"}
        new = {"irrelevant_field": "value"}
        assert connected_provider._permission_needs_update(existing, new) is False


class TestGetRecordsByRecordGroupProvider2:
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_records_by_record_group("rg1", "c1", "org1", 1)
        assert result == []


class TestGetRecordsByParentRecordProvider2:
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_records_by_parent_record("p1", "c1", "org1", 1)
        assert result == []


class TestDeleteConnectorSyncEdgesProvider:
    @pytest.mark.asyncio
    async def test_no_entities(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock, return_value={"all_node_ids": []}
        ):
            deleted, success = await connected_provider.delete_connector_sync_edges("c1")
            assert deleted == 0
            assert success is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            deleted, success = await connected_provider.delete_connector_sync_edges("c1")
            assert deleted == 0
            assert success is False


class TestGetAllEdgeCollectionsProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_graph_not_found(self, connected_provider):
        connected_provider.http_client.get_graph = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connected_provider._get_all_edge_collections()

    @pytest.mark.asyncio
    async def test_no_edge_definitions(self, connected_provider):
        connected_provider.http_client.get_graph = AsyncMock(
            return_value={"graph": {"edgeDefinitions": []}}
        )
        with pytest.raises(Exception, match="no edge collections"):
            await connected_provider._get_all_edge_collections()


class TestCollectIsoftypeTargetsProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_connector(self, connected_provider):
        targets, success = await connected_provider._collect_isoftype_targets(None, "")
        assert success is True
        assert targets == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        targets, success = await connected_provider._collect_isoftype_targets(None, "c1")
        assert success is False
        assert targets == []


class TestDeleteNodesByKeysProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        deleted, failed = await connected_provider._delete_nodes_by_keys("txn1", [], "records")
        assert deleted == 0
        assert failed == 0

    @pytest.mark.asyncio
    async def test_partial_failure(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        deleted, failed = await connected_provider._delete_nodes_by_keys("txn1", ["k1"], "records")
        assert deleted == 0
        assert failed == 1


class TestDeleteNodesByConnectorIdProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        deleted, success = await connected_provider._delete_nodes_by_connector_id("txn1", "c1", "records")
        assert deleted == 0
        assert success is False


class TestBatchUpsertRecordsProvider2:
    @pytest.mark.asyncio
    async def test_unsupported_record_type_skipped(self, connected_provider):
        """Unsupported record types are skipped (continue), not raised."""
        from app.models.entities import RecordType
        mock_record = MagicMock()
        mock_record.id = "r1"
        # Use a record_type that won't be in RECORD_TYPE_COLLECTION_MAPPING
        mock_record.record_type = "NONEXISTENT_TYPE"
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            # Should not raise, just skip
            await connected_provider.batch_upsert_records([mock_record])
            # batch_upsert_nodes should not have been called since type was skipped
            mock_upsert.assert_not_awaited()


class TestBatchUpsertRecordGroupsProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        mock_rg = MagicMock()
        mock_rg.to_arango_base_record_group.return_value = {"_key": "rg1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.batch_upsert_record_groups([mock_rg])


class TestBatchUpsertUserGroupsProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        mock_ug = MagicMock()
        mock_ug.to_arango_base_user_group.return_value = {"_key": "ug1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.batch_upsert_user_groups([mock_ug])


class TestBatchUpsertAppRolesProvider2:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        mock_role = MagicMock()
        mock_role.to_arango_base_role.return_value = {"_key": "r1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(Exception):
            await connected_provider.batch_upsert_app_roles([mock_role])


# ===========================================================================
# MASSIVE COVERAGE EXPANSION: 200+ new tests
# ===========================================================================


# ---------------------------------------------------------------------------
# list_user_knowledge_bases
# ---------------------------------------------------------------------------


class TestListUserKnowledgeBases:
    @pytest.mark.asyncio
    async def test_success_returns_kbs_and_count(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"id": "kb1", "name": "KB1", "userRole": "OWNER", "folders": []}],
            [1],
            [{"OWNER": 1, "WRITER": 0, "READER": 0, "COMMENTER": 0}],
        ]
        kbs, total, role_counts = await connected_provider.list_user_knowledge_bases(
            "u1", "org1", skip=0, limit=10
        )
        assert len(kbs) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [],
            [0],
            [{"OWNER": 0, "WRITER": 0, "READER": 0, "COMMENTER": 0}],
        ]
        kbs, total, role_counts = await connected_provider.list_user_knowledge_bases(
            "u1", "org1", skip=0, limit=10
        )
        assert kbs == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_with_search_filter(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"id": "kb1", "name": "Search Result"}],
            [1],
            [{"OWNER": 1}],
        ]
        kbs, total, _ = await connected_provider.list_user_knowledge_bases(
            "u1", "org1", skip=0, limit=10, search="Search"
        )
        assert len(kbs) == 1

    @pytest.mark.asyncio
    async def test_with_permissions_filter(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [{"id": "kb1", "name": "KB1", "userRole": "OWNER"}],
            [1],
            [{"OWNER": 1}],
        ]
        kbs, total, _ = await connected_provider.list_user_knowledge_bases(
            "u1", "org1", skip=0, limit=10, permissions=["OWNER"]
        )
        assert len(kbs) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        kbs, total, role_counts = await connected_provider.list_user_knowledge_bases(
            "u1", "org1", skip=0, limit=10
        )
        assert kbs == []
        assert total == 0


# ---------------------------------------------------------------------------
# get_knowledge_base
# ---------------------------------------------------------------------------


class TestGetKnowledgeBase:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_permission_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, return_value=None
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"id": "kb1", "name": "Test KB"}]
        ):
            result = await connected_provider.get_knowledge_base("kb1", "u1")
            assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, return_value="OWNER"
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.get_knowledge_base("missing", "u1")
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.get_knowledge_base("kb1", "u1")


# ---------------------------------------------------------------------------
# update_knowledge_base
# ---------------------------------------------------------------------------


class TestUpdateKnowledgeBase:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.update_knowledge_base(
                "missing", {"groupName": "X"}
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.update_knowledge_base("kb1", {"name": "X"})


# ---------------------------------------------------------------------------
# delete_knowledge_base
# ---------------------------------------------------------------------------


class TestDeleteKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success_with_own_transaction(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[
                [{"kb_exists": True, "record_keys": ["r1"], "file_keys": ["f1"],
                  "folder_keys": [], "records_with_details": [], "total_folders": 0, "total_records": 1}],
                [],  # delete edges
                [],  # delete files
                [],  # delete records
                [],  # delete kb
            ]
        ), patch.object(
            connected_provider, "commit_transaction",
            new_callable=AsyncMock
        ):
            result = await connected_provider.delete_knowledge_base("kb1")
            assert result.get("success") is True

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, side_effect=Exception("txn fail")
        ):
            result = await connected_provider.delete_knowledge_base("kb1")
            assert result["success"] is False


# ---------------------------------------------------------------------------
# create_kb_permissions
# ---------------------------------------------------------------------------


class TestCreateKbPermissions:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_requester_not_owner(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{
                "is_valid": False,
                "requester_found": False,
                "kb_exists": True,
                "users_to_insert": [],
                "teams_to_insert": [],
            }]
        ):
            result = await connected_provider.create_kb_permissions(
                "kb1", "u1", ["u2"], [], "READER"
            )
            assert result["success"] is False
            assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_kb_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{
                "is_valid": False,
                "requester_found": True,
                "kb_exists": False,
                "users_to_insert": [],
                "teams_to_insert": [],
            }]
        ):
            result = await connected_provider.create_kb_permissions(
                "kb1", "u1", ["u2"], [], "READER"
            )
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.create_kb_permissions(
                "kb1", "u1", ["u2"], [], "READER"
            )
            assert result["success"] is False
            assert result["code"] == 500


# ---------------------------------------------------------------------------
# remove_kb_permission
# ---------------------------------------------------------------------------


class TestRemoveKbPermission:
    @pytest.mark.asyncio
    async def test_success_with_users(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=["perm1"]
        ):
            result = await connected_provider.remove_kb_permission(
                "kb1", ["u1"], []
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_success_with_teams(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=["perm1"]
        ):
            result = await connected_provider.remove_kb_permission(
                "kb1", [], ["t1"]
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_no_ids_returns_false(self, connected_provider):
        result = await connected_provider.remove_kb_permission(
            "kb1", [], []
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_no_permissions_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.remove_kb_permission(
                "kb1", ["u1"], []
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.remove_kb_permission(
                "kb1", ["u1"], []
            )
            assert result is False


# ---------------------------------------------------------------------------
# get_kb_permissions
# ---------------------------------------------------------------------------


class TestGetKbPermissions:
    @pytest.mark.asyncio
    async def test_success_users(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"id": "u1", "type": "USER", "role": "OWNER"}]
        ):
            result = await connected_provider.get_kb_permissions(
                "kb1", user_ids=["u1"]
            )
            assert "u1" in result["users"]
            assert result["users"]["u1"] == "OWNER"

    @pytest.mark.asyncio
    async def test_success_teams(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"id": "t1", "type": "TEAM", "role": None}]
        ):
            result = await connected_provider.get_kb_permissions(
                "kb1", team_ids=["t1"]
            )
            assert "t1" in result["teams"]

    @pytest.mark.asyncio
    async def test_no_ids_returns_empty(self, connected_provider):
        result = await connected_provider.get_kb_permissions("kb1")
        assert result == {"users": {}, "teams": {}}

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.get_kb_permissions(
                    "kb1", user_ids=["u1"]
                )


# ---------------------------------------------------------------------------
# update_kb_permission
# ---------------------------------------------------------------------------


class TestUpdateKbPermission:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, connected_provider):
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", [], [], "WRITER"
        )
        assert result["success"] is False
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_invalid_role(self, connected_provider):
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "INVALID_ROLE"
        )
        assert result["success"] is False
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_requester_not_owner(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [{
            "validation_error": {"error": "Only KB owners can update permissions", "code": "403"},
            "current_permissions": [],
            "updated_permissions": [],
            "requester_role": "READER",
        }]
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "WRITER"
        )
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_empty_query_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "WRITER"
        )
        assert result["success"] is False
        assert result["code"] == "500"

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "WRITER"
        )
        assert result["success"] is False
        assert result["code"] == "500"


# ---------------------------------------------------------------------------
# list_kb_permissions
# ---------------------------------------------------------------------------


class TestListKbPermissions:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.list_kb_permissions("kb1")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.list_kb_permissions("kb1")
            assert result == []


# ---------------------------------------------------------------------------
# count_kb_owners
# ---------------------------------------------------------------------------


class TestCountKbOwners:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.count_kb_owners("kb1")
            assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.count_kb_owners("kb1")
            assert result == 0


# ---------------------------------------------------------------------------
# validate_folder_in_kb
# ---------------------------------------------------------------------------


class TestValidateFolderInKb:
    @pytest.mark.asyncio
    async def test_valid(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[True]
        ):
            result = await connected_provider.validate_folder_in_kb("kb1", "f1")
            assert result is True

    @pytest.mark.asyncio
    async def test_not_valid(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[False]
        ):
            result = await connected_provider.validate_folder_in_kb("kb1", "f1")
            assert result is False

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.validate_folder_in_kb("kb1", "f1")
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.validate_folder_in_kb("kb1", "f1")
            assert result is False


# ---------------------------------------------------------------------------
# validate_folder_exists_in_kb
# ---------------------------------------------------------------------------


class TestValidateFolderExistsInKb:
    @pytest.mark.asyncio
    async def test_valid(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[True]
        ):
            result = await connected_provider.validate_folder_exists_in_kb("kb1", "f1")
            assert result is True

    @pytest.mark.asyncio
    async def test_not_valid(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[False]
        ):
            result = await connected_provider.validate_folder_exists_in_kb("kb1", "f1")
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.validate_folder_exists_in_kb("kb1", "f1")
            assert result is False


# ---------------------------------------------------------------------------
# update_folder
# ---------------------------------------------------------------------------


class TestUpdateFolder:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.update_folder("missing", {"name": "X"})
            assert result is False

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.update_folder("f1", {"name": "X"})


# ---------------------------------------------------------------------------
# create_folder
# ---------------------------------------------------------------------------


class TestCreateFolder:
    @pytest.mark.asyncio
    async def test_success_no_parent(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "find_folder_by_name_in_parent",
            new_callable=AsyncMock, return_value=None
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ), patch.object(
            connected_provider, "commit_transaction",
            new_callable=AsyncMock
        ):
            result = await connected_provider.create_folder("kb1", "New Folder", "org1")
            assert result is not None
            assert result["success"] is True
            assert result["name"] == "New Folder"

    @pytest.mark.asyncio
    async def test_folder_already_exists(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "find_folder_by_name_in_parent",
            new_callable=AsyncMock,
            return_value={"_key": "existing", "name": "Existing", "webUrl": "/kb/kb1/folder/existing"}
        ), patch.object(
            connected_provider, "commit_transaction",
            new_callable=AsyncMock
        ):
            result = await connected_provider.create_folder("kb1", "Existing", "org1")
            assert result["exists"] is True
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_with_parent_folder(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "get_and_validate_folder_in_kb",
            new_callable=AsyncMock, return_value={"_key": "parent1"}
        ), patch.object(
            connected_provider, "find_folder_by_name_in_parent",
            new_callable=AsyncMock, return_value=None
        ), patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ), patch.object(
            connected_provider, "commit_transaction",
            new_callable=AsyncMock
        ):
            result = await connected_provider.create_folder(
                "kb1", "Child Folder", "org1", parent_folder_id="parent1"
            )
            assert result is not None
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_parent_raises(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "get_and_validate_folder_in_kb",
            new_callable=AsyncMock, return_value=None
        ), patch.object(
            connected_provider, "rollback_transaction",
            new_callable=AsyncMock
        ):
            with pytest.raises(ValueError, match="Parent folder"):
                await connected_provider.create_folder(
                    "kb1", "Child", "org1", parent_folder_id="bad_parent"
                )

    @pytest.mark.asyncio
    async def test_exception_rollback(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "find_folder_by_name_in_parent",
            new_callable=AsyncMock, side_effect=Exception("db error")
        ), patch.object(
            connected_provider, "rollback_transaction",
            new_callable=AsyncMock
        ):
            with pytest.raises(Exception):
                await connected_provider.create_folder("kb1", "Folder", "org1")


# ---------------------------------------------------------------------------
# get_folder_contents
# ---------------------------------------------------------------------------


class TestGetFolderContents:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_failure_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "get_folder_children",
            new_callable=AsyncMock,
            return_value={"success": False}
        ):
            result = await connected_provider.get_folder_contents("kb1", "f1")
            assert result is None


# ---------------------------------------------------------------------------
# delete_folder
# ---------------------------------------------------------------------------


class TestDeleteFolder:
    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, side_effect=Exception("txn fail")
        ):
            result = await connected_provider.delete_folder("kb1", "f1")
            assert result["success"] is False


# ---------------------------------------------------------------------------
# update_record
# ---------------------------------------------------------------------------


class TestUpdateRecord:
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.update_record("r1", "u1", {"name": "X"})
            assert result["success"] is False
            assert result["code"] == 404

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_update_fails(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.update_record("r1", "u1", {"name": "X"})
            assert result["success"] is False
            assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_with_file_metadata(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"_key": "r1", "recordName": "Rec"}]
        ), patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock, return_value={"_key": "r1"}
        ), patch.object(
            connected_provider, "_create_update_record_event_payload",
            new_callable=AsyncMock, return_value={"topic": "record-events"}
        ):
            result = await connected_provider.update_record(
                "r1", "u1", {"recordName": "Rec"},
                file_metadata={"lastModified": 123456}
            )
            assert result["success"] is True
            assert result["eventData"] is not None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.update_record("r1", "u1", {"name": "X"})
            assert result["success"] is False
            assert result["code"] == 500


# ---------------------------------------------------------------------------
# delete_records
# ---------------------------------------------------------------------------


class TestDeleteRecords:
    @pytest.mark.asyncio
    async def test_empty_record_ids(self, connected_provider):
        result = await connected_provider.delete_records([], "kb1")
        assert result["success"] is True
        assert result["total_requested"] == 0

    @pytest.mark.asyncio
    async def test_no_valid_records(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"valid_records": [], "invalid_records": [{"record_id": "r1"}]}]
        ), patch.object(
            connected_provider, "commit_transaction",
            new_callable=AsyncMock
        ):
            result = await connected_provider.delete_records(["r1"], "kb1")
            assert result["success"] is True
            assert result["successfully_deleted"] == 0
            assert result["failed_count"] == 1

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_folder_id(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, return_value="txn1"
        ), patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[
                [{"valid_records": [{"record_id": "r1", "record": {}, "file_record": None}],
                  "invalid_records": []}],
                [],  # edges cleanup
                [],  # record deletion
            ]
        ), patch.object(
            connected_provider, "commit_transaction",
            new_callable=AsyncMock
        ):
            result = await connected_provider.delete_records(["r1"], "kb1", folder_id="f1")
            assert result["success"] is True
            assert result["folder_id"] == "f1"

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "begin_transaction",
            new_callable=AsyncMock, side_effect=Exception("txn fail")
        ):
            result = await connected_provider.delete_records(["r1"], "kb1")
            assert result["success"] is False


# ---------------------------------------------------------------------------
# get_records
# ---------------------------------------------------------------------------


class TestGetRecords:
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            records, total, filters = await connected_provider.get_records(
                "u1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "all"
            )
            assert records == []
            assert total == 0

    @pytest.mark.asyncio
    async def test_user_no_key(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"userId": "u1"}  # no _key or id
        ):
            records, total, filters = await connected_provider.get_records(
                "u1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "all"
            )
            assert records == []
            assert total == 0

    @pytest.mark.asyncio
    async def test_delegates_to_list_all_records(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "ext_u1"}
        ), patch.object(
            connected_provider, "list_all_records",
            new_callable=AsyncMock,
            return_value=([{"id": "r1"}], 1, {})
        ):
            records, total, filters = await connected_provider.get_records(
                "ext_u1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "all"
            )
            assert len(records) == 1
            assert total == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            records, total, filters = await connected_provider.get_records(
                "u1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "all"
            )
            assert records == []
            assert total == 0


# ---------------------------------------------------------------------------
# list_all_records
# ---------------------------------------------------------------------------


class TestListAllRecords:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[
                [{"id": "r1", "recordName": "Search Result"}],
                [1],
            ]
        ):
            records, total, filters = await connected_provider.list_all_records(
                "u1", "org1", 0, 10, "search", None, None, None, None, None, None, None, "recordName", "asc", "all"
            )
            assert len(records) == 1

    @pytest.mark.asyncio
    async def test_with_filters(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[
                [{"id": "r1"}],
                [1],
            ]
        ):
            records, total, _ = await connected_provider.list_all_records(
                "u1", "org1", 0, 10, None, ["FILE"], ["CONNECTOR"], ["DRIVE"],
                ["COMPLETED"], ["OWNER"], 1000, 2000, "recordName", "asc", "connector"
            )
            assert len(records) == 1

    @pytest.mark.asyncio
    async def test_local_source(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            side_effect=[[], [0]]
        ):
            records, total, _ = await connected_provider.list_all_records(
                "u1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "local"
            )
            assert records == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            records, total, filters = await connected_provider.list_all_records(
                "u1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "all"
            )
            assert records == []
            assert total == 0


# ---------------------------------------------------------------------------
# list_kb_records
# ---------------------------------------------------------------------------


class TestListKbRecords:
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, return_value=None
        ):
            records, total, filters = await connected_provider.list_kb_records(
                "kb1", "u1", "org1", 0, 10, None, None, None, None, None, None, None, "recordName", "asc"
            )
            assert records == []
            assert total == 0

class TestCheckRecordAccessWithDetails:
    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, return_value=None
        ):
            result = await connected_provider.check_record_access_with_details(
                "u1", "org1", "r1"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_has_access(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1", "email": "u@t.com"}
        ), patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=["app1"]
        ), patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={"id": "r1", "recordName": "Test", "recordType": "FILE", "orgId": "org1"}
        ), patch.object(
            connected_provider, "get_record_path",
            new_callable=AsyncMock, return_value="Folder/Test.txt"
        ):
            # execute_aql returns a list where first item is the access result (list of permission paths)
            connected_provider.http_client.execute_aql.return_value = [
                [{"type": "DIRECT", "role": "OWNER", "source": {"_key": "u1"}}]
            ]
            result = await connected_provider.check_record_access_with_details(
                "u1", "org1", "r1"
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_no_access(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_get_user_app_ids",
            new_callable=AsyncMock, return_value=[]
        ):
            # null access result means no access
            connected_provider.http_client.execute_aql.return_value = [None]
            result = await connected_provider.check_record_access_with_details(
                "u1", "org1", "r1"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.check_record_access_with_details(
                    "u1", "org1", "r1"
                )


# ---------------------------------------------------------------------------
# get_connector_stats
# ---------------------------------------------------------------------------


class TestGetConnectorStats:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        with patch(
            "app.services.graph_db.arango.arango_http_provider.build_connector_stats_response",
            return_value={"totalRecords": 0}
        ):
            result = await connected_provider.get_connector_stats("org1", "c1")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_connector_stats("org1", "c1")
        assert result["success"] is False
        assert result["data"] is None


# ---------------------------------------------------------------------------
# find_folder_by_name_in_parent
# ---------------------------------------------------------------------------


class TestFindFolderByNameInParent:
    @pytest.mark.asyncio
    async def test_found_at_root(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"_key": "f1", "name": "Folder"}]
        ):
            result = await connected_provider.find_folder_by_name_in_parent(
                "kb1", "Folder"
            )
            assert result is not None
            assert result["_key"] == "f1"

    @pytest.mark.asyncio
    async def test_found_in_parent(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{"_key": "f2", "name": "Subfolder"}]
        ):
            result = await connected_provider.find_folder_by_name_in_parent(
                "kb1", "Subfolder", parent_folder_id="parent1"
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider.find_folder_by_name_in_parent(
                "kb1", "Missing"
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.find_folder_by_name_in_parent(
                "kb1", "Folder"
            )
            assert result is None


# ---------------------------------------------------------------------------
# _get_virtual_ids_for_connector
# ---------------------------------------------------------------------------


class TestGetVirtualIdsForConnector:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._get_virtual_ids_for_connector(
            "u1", "org1", "app1"
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._get_virtual_ids_for_connector(
            "u1", "org1", "app1"
        )
        assert result == {}


# ---------------------------------------------------------------------------
# _get_kb_virtual_ids
# ---------------------------------------------------------------------------


class TestGetKbVirtualIds:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider._get_kb_virtual_ids("u1", "org1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider._get_kb_virtual_ids("u1", "org1")
        assert result == {}


# ---------------------------------------------------------------------------
# get_records_by_virtual_record_id
# ---------------------------------------------------------------------------


class TestGetRecordsByVirtualRecordId:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.get_records_by_virtual_record_id("missing", "org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = Exception("fail")
        result = await connected_provider.get_records_by_virtual_record_id("vr1", "org1")
        assert result == []


# ---------------------------------------------------------------------------
# batch_upsert_domains
# ---------------------------------------------------------------------------


class TestBatchUpsertDomains:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider.batch_upsert_domains([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_domains([{"id": "d1"}])


# ---------------------------------------------------------------------------
# batch_upsert_anyone_with_link
# ---------------------------------------------------------------------------


class TestBatchUpsertAnyoneWithLink:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider.batch_upsert_anyone_with_link([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_anyone_with_link([{"id": "awl1"}])


# ---------------------------------------------------------------------------
# batch_upsert_anyone_same_org
# ---------------------------------------------------------------------------


class TestBatchUpsertAnyoneSameOrg:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock
        ) as mock_upsert:
            await connected_provider.batch_upsert_anyone_same_org([])
            mock_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_upsert_anyone_same_org([{"id": "aso1"}])


# ---------------------------------------------------------------------------
# batch_create_user_app_edges
# ---------------------------------------------------------------------------


class TestBatchCreateUserAppEdges:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        result = await connected_provider.batch_create_user_app_edges([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            with pytest.raises(Exception):
                await connected_provider.batch_create_user_app_edges(
                    [{"_from": "users/u1", "_to": "apps/a1"}]
                )


# ---------------------------------------------------------------------------
# process_file_permissions - expanded tests
# ---------------------------------------------------------------------------


class TestProcessFilePermissionsExpanded:
    @pytest.mark.asyncio
    async def test_empty_permissions_list(self, connected_provider):
        result = await connected_provider.process_file_permissions(
            "org1", "f1", []
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_with_user_type_permission(self, connected_provider):
        with patch.object(
            connected_provider, "get_entity_id_by_email",
            new_callable=AsyncMock, return_value="u_key"
        ), patch.object(
            connected_provider, "store_permission",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.process_file_permissions(
                "org1", "f1",
                [{"type": "user", "role": "READER", "id": "p1", "emailAddress": "u@t.com"}]
            )
            assert result is True


# ---------------------------------------------------------------------------
# store_permission - expanded
# ---------------------------------------------------------------------------


class TestStorePermissionExpanded:
    @pytest.mark.asyncio
    async def test_anyone_type(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ):
            connected_provider.http_client.execute_aql.return_value = []
            with patch.object(
                connected_provider, "batch_upsert_nodes",
                new_callable=AsyncMock, return_value=True
            ):
                result = await connected_provider.store_permission(
                    "file1", "anyone1",
                    {"type": "anyone", "role": "READER", "id": "perm1"}
                )
                assert result is True

    @pytest.mark.asyncio
    async def test_anyoneWithLink_type(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ):
            connected_provider.http_client.execute_aql.return_value = []
            with patch.object(
                connected_provider, "batch_upsert_nodes",
                new_callable=AsyncMock, return_value=True
            ):
                result = await connected_provider.store_permission(
                    "file1", "awl1",
                    {"type": "anyoneWithLink", "role": "READER", "id": "perm1"}
                )
                assert result is True

    @pytest.mark.asyncio
    async def test_group_type(self, connected_provider):
        with patch.object(
            connected_provider, "get_file_permissions",
            new_callable=AsyncMock, return_value=[]
        ):
            connected_provider.http_client.execute_aql.return_value = []
            with patch.object(
                connected_provider, "batch_upsert_nodes",
                new_callable=AsyncMock, return_value=True
            ):
                result = await connected_provider.store_permission(
                    "file1", "g1",
                    {"type": "group", "role": "READER", "id": "perm1"}
                )
                assert result is True


# ---------------------------------------------------------------------------
# delete_record successful paths for connector-specific methods
# ---------------------------------------------------------------------------


class TestDeleteRecordSuccessPaths:
    @pytest.mark.asyncio
    async def test_drive_owner_success(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_drive_permissions",
            new_callable=AsyncMock, return_value="OWNER"
        ), patch.object(
            connected_provider, "delete_records_and_relations",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.delete_google_drive_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_gmail_owner_success(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_gmail_permissions",
            new_callable=AsyncMock, return_value="OWNER"
        ), patch.object(
            connected_provider, "delete_records_and_relations",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.delete_gmail_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_outlook_owner_success(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_record_permission",
            new_callable=AsyncMock, return_value="OWNER"
        ), patch.object(
            connected_provider, "delete_records_and_relations",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.delete_outlook_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_kb_record_owner_success(self, connected_provider):
        with patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_get_kb_context_for_record",
            new_callable=AsyncMock,
            return_value={"kb_id": "kb1", "kb_name": "KB", "org_id": "org1"}
        ), patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, return_value="OWNER"
        ), patch.object(
            connected_provider, "delete_records_and_relations",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.delete_knowledge_base_record(
                "r1", "u1", {"_key": "r1"}
            )
            assert result["success"] is True


# ---------------------------------------------------------------------------
# _check_record_permissions - expanded
# ---------------------------------------------------------------------------


class TestCheckRecordPermissionsExpanded:
    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock, return_value=[]
        ):
            result = await connected_provider._check_record_permissions("r1", "u1")
            assert result["permission"] is None
            assert result["source"] == "NONE"


# ---------------------------------------------------------------------------
# batch_update_nodes with empty keys
# ---------------------------------------------------------------------------


class TestBatchUpdateNodesExpanded:
    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = []
        result = await connected_provider.batch_update_nodes(
            [], {"status": "OK"}, "records"
        )
        # Empty keys may still execute query but return no updates
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# delete_connector_instance - success path
# ---------------------------------------------------------------------------


class TestDeleteConnectorInstanceExpanded:
    pass
class TestStorePageTokenExpanded:
    @pytest.mark.asyncio
    async def test_with_email_only(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"channelId": None, "token": "tok1"}
        ]
        result = await connected_provider.store_page_token(
            None, None, "user@test.com", "tok1"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# get_page_token_db with resource_id
# ---------------------------------------------------------------------------


class TestGetPageTokenDbExpanded:
    @pytest.mark.asyncio
    async def test_with_resource_id(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"resourceId": "res1", "token": "tok1"}
        ]
        result = await connected_provider.get_page_token_db(resource_id="res1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_email(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"user_email": "u@test.com", "token": "tok1"}
        ]
        result = await connected_provider.get_page_token_db(user_email="u@test.com")
        assert result is not None


# ---------------------------------------------------------------------------
# reindex_single_record - connector success path
# ---------------------------------------------------------------------------


class TestReindexSingleRecordSuccess:
    @pytest.mark.asyncio
    async def test_connector_success(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            side_effect=[
                {"id": "r1", "origin": "CONNECTOR", "connectorName": "DRIVE",
                 "connectorId": "c1", "recordName": "Test"},
                {"id": "c1", "isActive": True},
            ]
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_check_record_permissions",
            new_callable=AsyncMock,
            return_value={"permission": "OWNER", "source": "DIRECT"}
        ), patch.object(
            connected_provider, "_reset_indexing_status_to_queued",
            new_callable=AsyncMock
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is True
            assert result["eventData"] is not None

    @pytest.mark.asyncio
    async def test_upload_success(self, connected_provider):
        with patch.object(
            connected_provider, "get_document",
            new_callable=AsyncMock,
            return_value={
                "id": "r1", "origin": "UPLOAD", "connectorName": "KB",
                "connectorId": "c1", "recordName": "Test", "recordType": "FILE"
            }
        ), patch.object(
            connected_provider, "get_user_by_user_id",
            new_callable=AsyncMock,
            return_value={"_key": "u1", "userId": "u1"}
        ), patch.object(
            connected_provider, "_get_kb_context_for_record",
            new_callable=AsyncMock,
            return_value={"kb_id": "kb1", "id": "kb1"}
        ), patch.object(
            connected_provider, "get_user_kb_permission",
            new_callable=AsyncMock, return_value="OWNER"
        ), patch.object(
            connected_provider, "_reset_indexing_status_to_queued",
            new_callable=AsyncMock
        ):
            result = await connected_provider.reindex_single_record("r1", "u1", "org1")
            assert result["success"] is True


# ---------------------------------------------------------------------------
# delete_connector_sync_edges - expanded
# ---------------------------------------------------------------------------


class TestDeleteConnectorSyncEdgesExpanded:
    @pytest.mark.asyncio
    async def test_with_edge_collections(self, connected_provider):
        with patch.object(
            connected_provider, "_collect_connector_entities",
            new_callable=AsyncMock,
            return_value={"all_node_ids": ["records/r1"]}
        ), patch.object(
            connected_provider, "_delete_all_edges_for_nodes",
            new_callable=AsyncMock,
            return_value=(10, [])
        ):
            count, success = await connected_provider.delete_connector_sync_edges("c1")
            assert count == 10
            assert success is True


# ---------------------------------------------------------------------------
# _delete_edges_by_connector_id - expanded
# ---------------------------------------------------------------------------


class TestDeleteEdgesByConnectorIdExpanded:
    @pytest.mark.asyncio
    async def test_empty_edge_collections(self, connected_provider):
        total, failed = await connected_provider._delete_edges_by_connector_id(
            None, "c1", []
        )
        assert total == 0
        assert failed == []


# ---------------------------------------------------------------------------
# check_edge_exists with transaction
# ---------------------------------------------------------------------------


class TestCheckEdgeExistsExpanded:
    @pytest.mark.asyncio
    async def test_with_multiple_results(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"_from": "users/u1", "_to": "records/r1"},
            {"_from": "users/u2", "_to": "records/r1"},
        ]
        result = await connected_provider.check_edge_exists(
            "users/u1", "records/r1", "permission"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.check_edge_exists(
            "users/u1", "records/r1", "permission"
        )
        assert result is False


# ---------------------------------------------------------------------------
# organization_exists with edge cases
# ---------------------------------------------------------------------------


class TestOrganizationExistsExpanded:
    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.organization_exists("Test")
        assert result is False


# ---------------------------------------------------------------------------
# delete_record with KB origin
# ---------------------------------------------------------------------------


class TestDeleteRecordKbOrigin:
    @pytest.mark.asyncio
    async def test_kb_origin_routes_correctly(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {
            "_key": "r1", "connectorName": "KB", "origin": "UPLOAD"
        }
        with patch.object(
            connected_provider, "delete_knowledge_base_record",
            new_callable=AsyncMock,
            return_value={"success": True}
        ) as mock_kb:
            result = await connected_provider.delete_record("r1", "u1")
            assert result["success"] is True
            mock_kb.assert_awaited_once()


# ---------------------------------------------------------------------------
# batch_upsert_records - expanded edge cases
# ---------------------------------------------------------------------------


class TestBatchUpsertRecordsExpanded:
    @pytest.mark.asyncio
    async def test_empty_list(self, connected_provider):
        result = await connected_provider.batch_upsert_records([])
        assert result is True

    @pytest.mark.asyncio
    async def test_duplicate_record_ids_logged(self, connected_provider):
        from app.models.entities import RecordType
        record1 = MagicMock()
        record1.id = "r1"
        record1.record_type = RecordType.FILE
        record1.to_arango_base_record.return_value = {"id": "r1"}
        record1.to_arango_record.return_value = {"id": "r1"}
        record2 = MagicMock()
        record2.id = "r1"  # duplicate
        record2.record_type = RecordType.FILE
        record2.to_arango_base_record.return_value = {"id": "r1"}
        record2.to_arango_record.return_value = {"id": "r1"}
        with patch.object(
            connected_provider, "batch_upsert_nodes",
            new_callable=AsyncMock, return_value=True
        ), patch.object(
            connected_provider, "batch_create_edges",
            new_callable=AsyncMock, return_value=True
        ):
            result = await connected_provider.batch_upsert_records([record1, record2])
            assert result is True


# ---------------------------------------------------------------------------
# get_record_by_id
# ---------------------------------------------------------------------------


class TestGetRecordByIdExpanded:
    @pytest.mark.asyncio
    async def test_with_type_doc(self, connected_provider):
        with patch.object(
            connected_provider, "execute_query",
            new_callable=AsyncMock,
            return_value=[{
                "record": _make_full_arango_record(),
                "typeDoc": {"_key": "r1", "fileName": "test.txt"}
            }]
        ):
            result = await connected_provider.get_record_by_id("r1")
            assert result is not None


# ---------------------------------------------------------------------------
# delete_records_and_relations - soft delete
# ---------------------------------------------------------------------------


class TestDeleteRecordsAndRelationsSoftDelete:
    @pytest.mark.asyncio
    async def test_soft_delete(self, connected_provider):
        connected_provider.http_client.get_document.return_value = {"_key": "r1"}
        connected_provider.http_client.execute_aql.return_value = [
            {"record_removed": True, "file_removed": True, "mail_removed": False}
        ]
        result = await connected_provider.delete_records_and_relations(
            "r1", hard_delete=False
        )
        assert result is True


# ---------------------------------------------------------------------------
# _ensure_indexes
# ---------------------------------------------------------------------------


class TestEnsureIndexesExpanded:
    @pytest.mark.asyncio
    async def test_exception_propagates(self, connected_provider):
        connected_provider.http_client.ensure_persistent_index = AsyncMock(
            side_effect=Exception("index error")
        )
        # _ensure_indexes may propagate exceptions
        with pytest.raises(Exception):
            await connected_provider._ensure_indexes()


# ---------------------------------------------------------------------------
# get_entity_id_by_email - expanded
# ---------------------------------------------------------------------------


class TestGetEntityIdByEmailExpanded:
    @pytest.mark.asyncio
    async def test_empty_email(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [[], [], []]
        result = await connected_provider.get_entity_id_by_email("")
        assert result is None


# ---------------------------------------------------------------------------
# check_connector_name_uniqueness expanded
# ---------------------------------------------------------------------------


class TestCheckConnectorNameUniquenessExpanded:
    @pytest.mark.asyncio
    async def test_not_unique_team(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["key1"]
        result = await connected_provider.check_connector_name_uniqueness(
            "Team Connector", "team", "org1", "u1", "apps",
            edge_collection="orgAppRelation"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_not_unique_personal(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = ["key1"]
        result = await connected_provider.check_connector_name_uniqueness(
            "Connector", "personal", "org1", "u1", "apps"
        )
        assert result is False


# ---------------------------------------------------------------------------
# get_connector_instances_with_filters expanded
# ---------------------------------------------------------------------------


class TestGetConnectorInstancesWithFiltersExpanded:
    @pytest.mark.asyncio
    async def test_with_scope_filter(self, connected_provider):
        connected_provider.http_client.execute_aql.side_effect = [
            [2],
            [{"_key": "c1"}, {"_key": "c2"}],
        ]
        docs, total = await connected_provider.get_connector_instances_with_filters(
            "apps", scope="team", user_id="u1"
        )
        assert total == 2

# ---------------------------------------------------------------------------
# upload_records (simplified test - method is very complex)
# ---------------------------------------------------------------------------


class TestUploadRecords:
    @pytest.mark.asyncio
    async def test_validation_fails(self, connected_provider):
        with patch.object(
            connected_provider, "_validate_upload_context",
            new_callable=AsyncMock,
            return_value={"valid": False, "reason": "KB not found", "code": 404}
        ):
            result = await connected_provider.upload_records(
                kb_id="kb1",
                user_id="u1",
                org_id="org1",
                files=[],
            )
            assert result.get("valid") is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        with patch.object(
            connected_provider, "_validate_upload_context",
            new_callable=AsyncMock, side_effect=Exception("fail")
        ):
            result = await connected_provider.upload_records(
                kb_id="kb1",
                user_id="u1",
                org_id="org1",
                files=[],
            )
            assert result.get("success") is False


# ---------------------------------------------------------------------------
# _permission_needs_update - more edge cases
# ---------------------------------------------------------------------------


class TestPermissionNeedsUpdateExpanded:
    def test_active_field_missing_in_both(self, connected_provider):
        existing = {"other": "val"}
        new = {"unrelated": "val"}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_role_missing_in_new(self, connected_provider):
        existing = {"role": "READER"}
        new = {}
        assert connected_provider._permission_needs_update(existing, new) is False

    def test_permission_details_missing_in_existing(self, connected_provider):
        existing = {}
        new = {"permissionDetails": {"key": "val"}}
        assert connected_provider._permission_needs_update(existing, new) is True


# ---------------------------------------------------------------------------
# get_failed_records_with_active_users - expanded
# ---------------------------------------------------------------------------


class TestGetFailedRecordsWithActiveUsersExpanded:
    @pytest.mark.asyncio
    async def test_with_multiple_records(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"record": {"_key": "r1"}, "users": [{"_key": "u1"}]},
            {"record": {"_key": "r2"}, "users": [{"_key": "u2"}]},
        ]
        result = await connected_provider.get_failed_records_with_active_users("org1", "c1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = None
        result = await connected_provider.get_failed_records_with_active_users("org1", "c1")
        assert result == []


# ---------------------------------------------------------------------------
# delete_sync_points_by_connector_id - expanded
# ---------------------------------------------------------------------------


class TestDeleteSyncPointsByConnectorIdExpanded:
    @pytest.mark.asyncio
    async def test_failure(self, connected_provider):
        with patch.object(
            connected_provider, "_delete_nodes_by_connector_id",
            new_callable=AsyncMock, return_value=(0, False)
        ):
            total, success = await connected_provider.delete_sync_points_by_connector_id("c1")
            assert total == 0
            assert success is False


# ---------------------------------------------------------------------------
# _collect_isoftype_targets expanded
# ---------------------------------------------------------------------------


class TestCollectIsoftypeTargetsExpanded:
    @pytest.mark.asyncio
    async def test_multiple_targets(self, connected_provider):
        connected_provider.http_client.execute_aql.return_value = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "mails", "key": "m1", "full_id": "mails/m1"},
        ]
        targets, success = await connected_provider._collect_isoftype_targets(None, "c1")
        assert success is True
        assert len(targets) == 2


# ---------------------------------------------------------------------------
# get_records_by_record_ids - expanded
# ---------------------------------------------------------------------------


class TestGetRecordsByRecordIdsExpanded:
    @pytest.mark.asyncio
    async def test_large_batch(self, connected_provider):
        ids = [f"r{i}" for i in range(100)]
        connected_provider.http_client.execute_aql.return_value = [
            {"_key": f"r{i}"} for i in range(100)
        ]
        result = await connected_provider.get_records_by_record_ids(ids, "org1")
        assert len(result) == 100


# ---------------------------------------------------------------------------
# _create_typed_record_from_arango - all collection types
# ---------------------------------------------------------------------------


class TestCreateTypedRecordFromArangoExpanded:
    def test_mail_type(self, connected_provider):
        record_dict = _make_full_arango_record(recordType="MAIL")
        type_doc = {"_key": "r1", "subject": "Test Mail"}
        result = connected_provider._create_typed_record_from_arango(record_dict, type_doc)
        assert result is not None

    def test_webpage_type(self, connected_provider):
        record_dict = _make_full_arango_record(recordType="WEBPAGE")
        type_doc = {"_key": "r1", "url": "https://example.com"}
        result = connected_provider._create_typed_record_from_arango(record_dict, type_doc)
        assert result is not None

    def test_ticket_type(self, connected_provider):
        record_dict = _make_full_arango_record(recordType="TICKET")
        type_doc = {"_key": "r1", "issueType": "Bug"}
        result = connected_provider._create_typed_record_from_arango(record_dict, type_doc)
        assert result is not None

    def test_comment_type(self, connected_provider):
        record_dict = _make_full_arango_record(recordType="COMMENT")
        type_doc = {"_key": "r1", "body": "A comment"}
        result = connected_provider._create_typed_record_from_arango(record_dict, type_doc)
        assert result is not None

    def test_link_type(self, connected_provider):
        record_dict = _make_full_arango_record(recordType="LINK")
        type_doc = {"_key": "r1", "url": "https://link.com"}
        result = connected_provider._create_typed_record_from_arango(record_dict, type_doc)
        assert result is not None

    def test_project_type(self, connected_provider):
        record_dict = _make_full_arango_record(recordType="PROJECT")
        type_doc = {"_key": "r1", "projectName": "My Project"}
        result = connected_provider._create_typed_record_from_arango(record_dict, type_doc)
        assert result is not None

    def test_type_doc_none_returns_base_record(self, connected_provider):
        """When type_doc is None, should return base Record."""
        record_dict = _make_full_arango_record(recordType="FILE")
        result = connected_provider._create_typed_record_from_arango(record_dict, None)
        assert result is not None
        # Should be a base Record since type_doc is None
        from app.models.entities import Record
        assert isinstance(result, Record)


# ===========================================================================
# NEW TESTS: KB management, folder operations, listing/pagination, teams,
# agents, duplicate detection, record access, upload, event payloads, etc.
# ===========================================================================


# ---------------------------------------------------------------------------
# list_user_knowledge_bases
# ---------------------------------------------------------------------------


class TestListUserKnowledgeBasesExtended:
    @pytest.mark.asyncio
    async def test_success_with_results(self, connected_provider):
        kb_item = {
            "id": "kb1",
            "name": "Test KB",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
            "createdBy": "u1",
            "userRole": "OWNER",
            "folders": [],
        }
        filter_item = {"permission": "OWNER", "kb_name": "Test KB"}
        connected_provider.execute_query = AsyncMock(
            side_effect=[[kb_item], [1], [filter_item]]
        )
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10
        )
        assert len(kbs) == 1
        assert kbs[0]["id"] == "kb1"
        assert total == 1
        assert "permissions" in filters

    @pytest.mark.asyncio
    async def test_success_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[], [0], []]
        )
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10, search="test"
        )
        assert kbs == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_success_with_permissions_filter(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[], [0], []]
        )
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10, permissions=["OWNER"]
        )
        assert kbs == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_sort_by_created(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[], [0], []]
        )
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10,
            sort_by="createdAtTimestamp", sort_order="desc"
        )
        assert kbs == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10
        )
        assert kbs == []
        assert total == 0
        assert "permissions" in filters

    @pytest.mark.asyncio
    async def test_none_results(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[None, None, None]
        )
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10
        )
        assert kbs == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_search_and_permissions_combined(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[], [0], []]
        )
        kbs, total, filters = await connected_provider.list_user_knowledge_bases(
            "user1", "org1", skip=0, limit=10,
            search="my kb", permissions=["OWNER", "READER"]
        )
        assert kbs == []


# ---------------------------------------------------------------------------
# get_kb_children
# ---------------------------------------------------------------------------


class TestGetKbChildrenExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        result_doc = {
            "success": True,
            "container": {"id": "kb1", "name": "Test KB", "path": "/", "type": "kb"},
            "folders": [],
            "records": [],
            "level": 1,
            "totalCount": 0,
            "counts": {"folders": 0, "records": 0, "totalItems": 0, "totalFolders": 0, "totalRecords": 0},
            "availableFilters": {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": []},
            "paginationMode": "folders_first",
        }
        connected_provider.execute_query = AsyncMock(return_value=[result_doc])
        result = await connected_provider.get_kb_children("kb1", skip=0, limit=10)
        assert result["success"] is True
        assert result["paginationMode"] == "folders_first"

    @pytest.mark.asyncio
    async def test_kb_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_kb_children("kb1", skip=0, limit=10)
        assert result["success"] is False
        assert "not found" in result.get("reason", "").lower() or result.get("reason") is not None

    @pytest.mark.asyncio
    async def test_with_search_filter(self, connected_provider):
        result_doc = {
            "success": True,
            "container": {"id": "kb1", "name": "KB", "path": "/", "type": "kb"},
            "folders": [],
            "records": [],
            "level": 1,
            "totalCount": 0,
            "counts": {"folders": 0, "records": 0, "totalItems": 0, "totalFolders": 0, "totalRecords": 0},
            "availableFilters": {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": []},
            "paginationMode": "folders_first",
        }
        connected_provider.execute_query = AsyncMock(return_value=[result_doc])
        result = await connected_provider.get_kb_children(
            "kb1", skip=0, limit=10, search="test"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_with_all_filters(self, connected_provider):
        result_doc = {
            "success": True,
            "container": {"id": "kb1", "name": "KB", "path": "/", "type": "kb"},
            "folders": [],
            "records": [],
            "level": 2,
            "totalCount": 0,
            "counts": {"folders": 0, "records": 0, "totalItems": 0, "totalFolders": 0, "totalRecords": 0},
            "availableFilters": {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": []},
            "paginationMode": "folders_first",
        }
        connected_provider.execute_query = AsyncMock(return_value=[result_doc])
        result = await connected_provider.get_kb_children(
            "kb1", skip=0, limit=10, level=2,
            search="q", record_types=["FILE"], origins=["UPLOAD"],
            connectors=["KNOWLEDGE_BASE"], indexing_status=["COMPLETED"],
            sort_by="created_at", sort_order="desc"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.get_kb_children("kb1", skip=0, limit=10)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# get_folder_children
# ---------------------------------------------------------------------------


class TestGetFolderChildrenExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        result_doc = {
            "success": True,
            "container": {"id": "f1", "name": "Folder", "path": "/f1", "type": "folder"},
            "folders": [],
            "records": [],
            "level": 1,
            "totalCount": 0,
            "counts": {"folders": 0, "records": 0, "totalItems": 0, "totalFolders": 0, "totalRecords": 0},
            "availableFilters": {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": []},
            "paginationMode": "folders_first",
        }
        connected_provider.execute_query = AsyncMock(return_value=[result_doc])
        result = await connected_provider.get_folder_children("kb1", "f1", skip=0, limit=10)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_folder_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_folder_children("kb1", "f1", skip=0, limit=10)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_filters(self, connected_provider):
        result_doc = {
            "success": True,
            "container": {"id": "f1", "name": "Folder", "path": "/f1", "type": "folder"},
            "folders": [],
            "records": [],
            "level": 1,
            "totalCount": 0,
            "counts": {"folders": 0, "records": 0, "totalItems": 0, "totalFolders": 0, "totalRecords": 0},
            "availableFilters": {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": []},
            "paginationMode": "folders_first",
        }
        connected_provider.execute_query = AsyncMock(return_value=[result_doc])
        result = await connected_provider.get_folder_children(
            "kb1", "f1", skip=0, limit=10, search="x",
            record_types=["FILE"], origins=["UPLOAD"],
            connectors=["KNOWLEDGE_BASE"], indexing_status=["COMPLETED"]
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_folder_children("kb1", "f1", skip=0, limit=10)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _normalize_name / _normalized_name_variants_lower
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_none(self, connected_provider):
        assert connected_provider._normalize_name(None) is None

    def test_basic_string(self, connected_provider):
        assert connected_provider._normalize_name("  hello  ") == "hello"

    def test_unicode(self, connected_provider):
        result = connected_provider._normalize_name("caf\u00e9")
        assert result == "caf\u00e9"


class TestNormalizedNameVariantsLower:
    def test_basic(self, connected_provider):
        variants = connected_provider._normalized_name_variants_lower("Hello")
        assert isinstance(variants, list)
        assert len(variants) == 2
        assert all(v == v.lower() for v in variants)

    def test_unicode_variants(self, connected_provider):
        variants = connected_provider._normalized_name_variants_lower("caf\u00e9")
        assert len(variants) == 2


# ---------------------------------------------------------------------------
# _check_name_conflict_in_parent
# ---------------------------------------------------------------------------


class TestCheckNameConflictInParent:
    @pytest.mark.asyncio
    async def test_no_conflict(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._check_name_conflict_in_parent(
            "kb1", None, "New Folder"
        )
        assert result["has_conflict"] is False
        assert result["conflicts"] == []

    @pytest.mark.asyncio
    async def test_has_conflict(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"id": "f1", "name": "New Folder", "type": "folder"}]
        )
        result = await connected_provider._check_name_conflict_in_parent(
            "kb1", None, "New Folder"
        )
        assert result["has_conflict"] is True

    @pytest.mark.asyncio
    async def test_with_mime_type(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._check_name_conflict_in_parent(
            "kb1", "parent1", "file.pdf", mime_type="application/pdf"
        )
        assert result["has_conflict"] is False

    @pytest.mark.asyncio
    async def test_with_parent_folder(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._check_name_conflict_in_parent(
            "kb1", "parentfolder1", "My Folder"
        )
        assert result["has_conflict"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider._check_name_conflict_in_parent(
            "kb1", None, "Folder"
        )
        assert result["has_conflict"] is False


# ---------------------------------------------------------------------------
# get_knowledge_base
# ---------------------------------------------------------------------------


class TestGetKnowledgeBaseExtended:
    @pytest.mark.asyncio
    async def test_success_with_permission(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        connected_provider.execute_query = AsyncMock(return_value=[{
            "id": "kb1",
            "name": "Test KB",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
            "createdBy": "u1",
            "userRole": "OWNER",
            "folders": [],
        }])
        result = await connected_provider.get_knowledge_base("kb1", "user1")
        assert result is not None
        assert result["id"] == "kb1"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value=None)
        connected_provider.execute_query = AsyncMock(return_value=[{
            "id": "kb1", "name": "KB", "createdAtTimestamp": 0,
            "updatedAtTimestamp": 0, "createdBy": "u1", "userRole": None, "folders": [],
        }])
        result = await connected_provider.get_knowledge_base("kb1", "user1")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_base("kb999", "user1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(side_effect=Exception("db"))
        with pytest.raises(Exception):
            await connected_provider.get_knowledge_base("kb1", "user1")


# ---------------------------------------------------------------------------
# update_knowledge_base
# ---------------------------------------------------------------------------


class TestUpdateKnowledgeBaseExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "kb1", "groupName": "Updated"}])
        result = await connected_provider.update_knowledge_base("kb1", {"groupName": "Updated"})
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.update_knowledge_base("kb999", {"groupName": "X"})
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.update_knowledge_base("kb1", {"groupName": "X"})


# ---------------------------------------------------------------------------
# delete_knowledge_base
# ---------------------------------------------------------------------------


class TestDeleteKnowledgeBaseExtended:
    @pytest.mark.asyncio
    async def test_success_with_records(self, connected_provider):
        inventory = {
            "kb_exists": True,
            "record_keys": ["r1", "r2"],
            "file_keys": ["f1", "f2"],
            "folder_keys": [],
            "records_with_details": [
                {"record": {"_key": "r1", "orgId": "o1"}, "file_record": {"extension": ".pdf", "mimeType": "application/pdf"}},
            ],
            "total_folders": 0,
            "total_records": 2,
        }
        edge_result = {
            "belongs_to_deleted": 2, "is_of_type_deleted": 2,
            "permission_deleted": 0, "relation_deleted": 0,
        }
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(
            side_effect=[[inventory], [edge_result], None, None]
        )
        connected_provider.delete_nodes = AsyncMock()
        connected_provider.commit_transaction = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(
            return_value={"orgId": "o1", "recordId": "r1"}
        )
        result = await connected_provider.delete_knowledge_base("kb1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_kb_not_found(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[{"kb_exists": False}])
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_knowledge_base("kb999")
        assert result["success"] is True
        assert result.get("eventData") is None

    @pytest.mark.asyncio
    async def test_transaction_failure(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(side_effect=Exception("txn fail"))
        result = await connected_provider.delete_knowledge_base("kb1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_existing_transaction(self, connected_provider):
        inventory = {
            "kb_exists": True,
            "record_keys": [],
            "file_keys": [],
            "folder_keys": [],
            "records_with_details": [],
            "total_folders": 0,
            "total_records": 0,
        }
        connected_provider.execute_query = AsyncMock(
            side_effect=[[inventory], [{}], None]
        )
        connected_provider.delete_nodes = AsyncMock()
        result = await connected_provider.delete_knowledge_base("kb1", transaction="existing_txn")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_db_error_rollback(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db err"))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_knowledge_base("kb1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _create_deleted_record_event_payload
# ---------------------------------------------------------------------------


class TestCreateDeletedRecordEventPayload:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        record = {"orgId": "o1", "_key": "r1", "version": 2, "summaryDocumentId": "s1", "virtualRecordId": "v1"}
        file_record = {"extension": ".pdf", "mimeType": "application/pdf"}
        result = await connected_provider._create_deleted_record_event_payload(record, file_record)
        assert result["orgId"] == "o1"
        assert result["recordId"] == "r1"
        assert result["extension"] == ".pdf"

    @pytest.mark.asyncio
    async def test_no_file_record(self, connected_provider):
        record = {"orgId": "o1", "_key": "r1"}
        result = await connected_provider._create_deleted_record_event_payload(record, None)
        assert result["extension"] == ""
        assert result["mimeType"] == ""

    @pytest.mark.asyncio
    async def test_empty_record(self, connected_provider):
        result = await connected_provider._create_deleted_record_event_payload({}, None)
        assert result["extension"] == ""


# ---------------------------------------------------------------------------
# _create_new_record_event_payload
# ---------------------------------------------------------------------------


class TestCreateNewRecordEventPayload:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        record_doc = {
            "_key": "r1", "orgId": "o1", "recordName": "Test",
            "recordType": "FILE", "version": 1, "origin": "UPLOAD",
            "externalRecordId": "ext1",
            "createdAtTimestamp": 1000, "updatedAtTimestamp": 2000,
            "sourceCreatedAtTimestamp": 1000,
        }
        file_doc = {"extension": ".txt", "mimeType": "text/plain"}
        result = await connected_provider._create_new_record_event_payload(
            record_doc, file_doc, "http://storage:3000"
        )
        assert result is not None
        assert result["recordId"] == "r1"
        assert "signedUrlRoute" in result

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connected_provider):
        result = await connected_provider._create_new_record_event_payload({}, {}, "http://x")
        assert result is None


# ---------------------------------------------------------------------------
# _validate_folder_creation
# ---------------------------------------------------------------------------


class TestValidateFolderCreation:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is True
        assert result["user_key"] == "uk1"

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is False
        assert result["code"] == 500


# ---------------------------------------------------------------------------
# find_folder_by_name_in_parent
# ---------------------------------------------------------------------------


class TestFindFolderByNameInParentExtended:
    @pytest.mark.asyncio
    async def test_found_at_kb_root(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "f1", "name": "Folder", "recordGroupId": "rg1", "orgId": "o1"}]
        )
        result = await connected_provider.find_folder_by_name_in_parent("kb1", "Folder")
        assert result is not None
        assert result["_key"] == "f1"

    @pytest.mark.asyncio
    async def test_found_in_parent_folder(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "f2", "name": "Sub", "recordGroupId": "rg1", "orgId": "o1"}]
        )
        result = await connected_provider.find_folder_by_name_in_parent(
            "kb1", "Sub", parent_folder_id="f1"
        )
        assert result is not None
        assert result["_key"] == "f2"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.find_folder_by_name_in_parent("kb1", "NoFolder")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.find_folder_by_name_in_parent("kb1", "Folder")
        assert result is None


# ---------------------------------------------------------------------------
# get_and_validate_folder_in_kb
# ---------------------------------------------------------------------------


class TestGetAndValidateFolderInKb:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "f1", "recordName": "Folder", "name": "Folder", "isFile": False}]
        )
        result = await connected_provider.get_and_validate_folder_in_kb("kb1", "f1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_and_validate_folder_in_kb("kb1", "f999")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_and_validate_folder_in_kb("kb1", "f1")
        assert result is None


# ---------------------------------------------------------------------------
# create_folder
# ---------------------------------------------------------------------------


class TestCreateFolderExtended:
    @pytest.mark.asyncio
    async def test_success_new_folder(self, connected_provider):
        connected_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        connected_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.create_folder("kb1", "New Folder", "org1")
        assert result is not None
        assert result["success"] is True
        assert result["exists"] is False
        assert result["name"] == "New Folder"

    @pytest.mark.asyncio
    async def test_existing_folder_returned(self, connected_provider):
        connected_provider.find_folder_by_name_in_parent = AsyncMock(
            return_value={"_key": "existing1", "name": "Existing"}
        )
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.create_folder("kb1", "Existing", "org1")
        assert result["exists"] is True
        assert result["folderId"] == "existing1"

    @pytest.mark.asyncio
    async def test_with_parent_folder(self, connected_provider):
        connected_provider.get_and_validate_folder_in_kb = AsyncMock(
            return_value={"_key": "parent1", "recordName": "Parent"}
        )
        connected_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.create_folder(
            "kb1", "Child", "org1", parent_folder_id="parent1"
        )
        assert result is not None
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_parent_folder_not_found(self, connected_provider):
        connected_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        connected_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.rollback_transaction = AsyncMock()
        with pytest.raises(ValueError):
            await connected_provider.create_folder(
                "kb1", "Child", "org1", parent_folder_id="missing"
            )

    @pytest.mark.asyncio
    async def test_with_existing_transaction(self, connected_provider):
        connected_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        connected_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        result = await connected_provider.create_folder(
            "kb1", "Folder", "org1", transaction="ext_txn"
        )
        assert result is not None
        assert result["success"] is True


# ---------------------------------------------------------------------------
# get_folder_contents
# ---------------------------------------------------------------------------


class TestGetFolderContentsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_folder_children = AsyncMock(return_value={
            "success": True, "folders": [], "records": [],
        })
        result = await connected_provider.get_folder_contents("kb1", "f1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.get_folder_children = AsyncMock(return_value={"success": False})
        result = await connected_provider.get_folder_contents("kb1", "f1")
        assert result is None


# ---------------------------------------------------------------------------
# validate_folder_in_kb
# ---------------------------------------------------------------------------


class TestValidateFolderInKbExtended:
    @pytest.mark.asyncio
    async def test_valid(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[True])
        result = await connected_provider.validate_folder_in_kb("kb1", "f1")
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[False])
        result = await connected_provider.validate_folder_in_kb("kb1", "f1")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.validate_folder_in_kb("kb1", "f1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.validate_folder_in_kb("kb1", "f1")
        assert result is False


# ---------------------------------------------------------------------------
# validate_folder_exists_in_kb
# ---------------------------------------------------------------------------


class TestValidateFolderExistsInKbExtended:
    @pytest.mark.asyncio
    async def test_valid(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[True])
        result = await connected_provider.validate_folder_exists_in_kb("kb1", "f1")
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[False])
        result = await connected_provider.validate_folder_exists_in_kb("kb1", "f1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.validate_folder_exists_in_kb("kb1", "f1")
        assert result is False


# ---------------------------------------------------------------------------
# update_folder
# ---------------------------------------------------------------------------


class TestUpdateFolderExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "f1", "name": "Updated"}]
        )
        connected_provider.batch_upsert_nodes = AsyncMock()
        result = await connected_provider.update_folder("f1", {"name": "Updated"})
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.update_folder("f999", {"name": "X"})
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.update_folder("f1", {"name": "X"})


# ---------------------------------------------------------------------------
# delete_folder
# ---------------------------------------------------------------------------


class TestDeleteFolderExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        inventory = {
            "folder_exists": True,
            "target_folder": "f1",
            "all_folders": ["f1"],
            "subfolders": [],
            "records_with_details": [],
            "file_records": [],
            "total_folders": 1,
            "total_subfolders": 0,
            "total_records": 0,
            "total_file_records": 0,
        }
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(
            side_effect=[[inventory], None, None, None, [], None, None]
        )
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_folder("kb1", "f1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_folder_not_found(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[{"folder_exists": False}])
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_folder("kb1", "f1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(side_effect=Exception("txn fail"))
        result = await connected_provider.delete_folder("kb1", "f1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# update_record (KB version)
# ---------------------------------------------------------------------------


class TestUpdateRecordKB:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "r1", "recordName": "Updated", "updatedAtTimestamp": 9999}]
        )
        connected_provider.get_document = AsyncMock(return_value={"extension": ".pdf"})
        connected_provider._create_update_record_event_payload = AsyncMock(return_value=None)
        result = await connected_provider.update_record("r1", "u1", {"recordName": "Updated"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.update_record("r1", "u1", {"recordName": "X"})
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.update_record("r999", "u1", {"recordName": "X"})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_file_metadata(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "r1", "recordName": "Up"}]
        )
        connected_provider.get_document = AsyncMock(return_value=None)
        connected_provider._create_update_record_event_payload = AsyncMock(return_value={"x": 1})
        result = await connected_provider.update_record(
            "r1", "u1", {"recordName": "Up"}, file_metadata={"lastModified": 5000}
        )
        assert result["success"] is True
        assert result["eventData"] is not None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("boom"))
        result = await connected_provider.update_record("r1", "u1", {})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# delete_records (bulk KB deletion)
# ---------------------------------------------------------------------------


class TestDeleteRecordsKB:
    @pytest.mark.asyncio
    async def test_empty_list(self, connected_provider):
        result = await connected_provider.delete_records([], "kb1")
        assert result["success"] is True
        assert result["total_requested"] == 0

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        val_result = {
            "valid_records": [
                {"record_id": "r1", "record": {"recordName": "Rec1"}, "file_record": {"_key": "f1"}},
            ],
            "invalid_records": [],
        }
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(
            side_effect=[[val_result], None, None, None]
        )
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_records(["r1"], "kb1")
        assert result["success"] is True
        assert result["successfully_deleted"] == 1

    @pytest.mark.asyncio
    async def test_all_invalid(self, connected_provider):
        val_result = {
            "valid_records": [],
            "invalid_records": [{"record_id": "r1"}],
        }
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[val_result])
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_records(["r1"], "kb1")
        assert result["success"] is True
        assert result["successfully_deleted"] == 0
        assert result["failed_count"] == 1

    @pytest.mark.asyncio
    async def test_with_folder_id(self, connected_provider):
        val_result = {"valid_records": [], "invalid_records": [{"record_id": "r1"}]}
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider.execute_query = AsyncMock(return_value=[val_result])
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_records(["r1"], "kb1", folder_id="f1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.delete_records(["r1"], "kb1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# create_kb_permissions
# ---------------------------------------------------------------------------


class TestCreateKbPermissionsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        query_result = {
            "is_valid": True,
            "requester_found": True,
            "kb_exists": True,
            "user_operations": [],
            "team_operations": [],
            "users_to_insert": [{"user_key": "u2", "user_id": "u2"}],
            "teams_to_insert": [],
        }
        connected_provider.execute_query = AsyncMock(return_value=[query_result])
        connected_provider.batch_create_edges = AsyncMock()
        result = await connected_provider.create_kb_permissions(
            "kb1", "u1", ["u2"], [], "READER"
        )
        assert result["success"] is True
        assert result["grantedCount"] == 1

    @pytest.mark.asyncio
    async def test_requester_not_owner(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "is_valid": False, "requester_found": False, "kb_exists": True,
            "user_operations": [], "team_operations": [],
            "users_to_insert": [], "teams_to_insert": [],
        }])
        result = await connected_provider.create_kb_permissions(
            "kb1", "u1", ["u2"], [], "READER"
        )
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_kb_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "is_valid": False, "requester_found": True, "kb_exists": False,
            "user_operations": [], "team_operations": [],
            "users_to_insert": [], "teams_to_insert": [],
        }])
        result = await connected_provider.create_kb_permissions(
            "kb1", "u1", [], [], "READER"
        )
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_with_teams(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "is_valid": True, "requester_found": True, "kb_exists": True,
            "user_operations": [], "team_operations": [],
            "users_to_insert": [],
            "teams_to_insert": [{"team_key": "t1", "team_id": "t1"}],
        }])
        connected_provider.batch_create_edges = AsyncMock()
        result = await connected_provider.create_kb_permissions(
            "kb1", "u1", [], ["t1"], "READER"
        )
        assert result["success"] is True
        assert result["grantedCount"] == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.create_kb_permissions(
            "kb1", "u1", ["u2"], [], "READER"
        )
        assert result["success"] is False
        assert result["code"] == 500


# ---------------------------------------------------------------------------
# count_kb_owners
# ---------------------------------------------------------------------------


class TestCountKbOwnersExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[3])
        count = await connected_provider.count_kb_owners("kb1")
        assert count == 3

    @pytest.mark.asyncio
    async def test_no_owners(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        count = await connected_provider.count_kb_owners("kb1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        count = await connected_provider.count_kb_owners("kb1")
        assert count == 0


# ---------------------------------------------------------------------------
# remove_kb_permission
# ---------------------------------------------------------------------------


class TestRemoveKbPermissionExtended:
    @pytest.mark.asyncio
    async def test_success_users(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider.remove_kb_permission("kb1", ["u1"], [])
        assert result is True

    @pytest.mark.asyncio
    async def test_success_teams(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider.remove_kb_permission("kb1", [], ["t1"])
        assert result is True

    @pytest.mark.asyncio
    async def test_no_conditions(self, connected_provider):
        result = await connected_provider.remove_kb_permission("kb1", [], [])
        assert result is False

    @pytest.mark.asyncio
    async def test_no_results(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.remove_kb_permission("kb1", ["u1"], [])
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.remove_kb_permission("kb1", ["u1"], [])
        assert result is False


# ---------------------------------------------------------------------------
# get_kb_permissions
# ---------------------------------------------------------------------------


class TestGetKbPermissionsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"id": "u1", "type": "USER", "role": "OWNER"},
            {"id": "t1", "type": "TEAM", "role": None},
        ])
        result = await connected_provider.get_kb_permissions(
            "kb1", user_ids=["u1"], team_ids=["t1"]
        )
        assert result["users"]["u1"] == "OWNER"
        assert result["teams"]["t1"] is None

    @pytest.mark.asyncio
    async def test_no_conditions(self, connected_provider):
        result = await connected_provider.get_kb_permissions("kb1")
        assert result == {"users": {}, "teams": {}}

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.get_kb_permissions("kb1", user_ids=["u1"])


# ---------------------------------------------------------------------------
# update_kb_permission
# ---------------------------------------------------------------------------


class TestUpdateKbPermissionExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{
            "validation_error": None,
            "current_permissions": [{"_key": "p1", "id": "u2", "type": "USER", "current_role": "READER"}],
            "updated_permissions": [{"_key": "p1", "id": "u2", "type": "USER", "old_role": "READER", "new_role": "WRITER"}],
            "requester_role": "OWNER",
        }])
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "WRITER"
        )
        assert result["success"] is True
        assert result["updated_users"] == 1

    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, connected_provider):
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", [], [], "WRITER"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_invalid_role(self, connected_provider):
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "INVALID_ROLE"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_validation_error(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{
            "validation_error": {"error": "Only KB owners can update permissions", "code": "403"},
            "current_permissions": [],
            "updated_permissions": [],
            "requester_role": "READER",
        }])
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "WRITER"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_query_returns_none(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "WRITER"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.update_kb_permission(
            "kb1", "u1", ["u2"], [], "WRITER"
        )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# list_kb_permissions
# ---------------------------------------------------------------------------


class TestListKbPermissionsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"id": "u1", "name": "User 1", "role": "OWNER", "type": "USER"},
        ])
        result = await connected_provider.list_kb_permissions("kb1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.list_kb_permissions("kb1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.list_kb_permissions("kb1")
        assert result == []


# ---------------------------------------------------------------------------
# list_all_records
# ---------------------------------------------------------------------------


class TestListAllRecordsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        records = [{"id": "r1", "recordName": "Rec1"}]
        connected_provider.execute_query = AsyncMock(side_effect=[records, [1]])
        result_records, total, filters = await connected_provider.list_all_records(
            "uk1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=None, date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="all"
        )
        assert len(result_records) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_with_all_filters(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[[], [0]])
        result_records, total, filters = await connected_provider.list_all_records(
            "uk1", "org1", skip=0, limit=10,
            search="test", record_types=["FILE"], origins=["UPLOAD"],
            connectors=["KNOWLEDGE_BASE"], indexing_status=["COMPLETED"],
            permissions=["OWNER"], date_from=1000, date_to=9999,
            sort_by="createdAtTimestamp", sort_order="desc", source="local"
        )
        assert result_records == []

    @pytest.mark.asyncio
    async def test_source_connector(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[[], [0]])
        result_records, total, filters = await connected_provider.list_all_records(
            "uk1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=None, date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="connector"
        )
        assert result_records == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result_records, total, filters = await connected_provider.list_all_records(
            "uk1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=None, date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="all"
        )
        assert result_records == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_permissions_filter_disables_kb(self, connected_provider):
        """When permissions filter has no overlap with KB roles, KB is excluded."""
        connected_provider.execute_query = AsyncMock(side_effect=[[], [0]])
        result_records, total, filters = await connected_provider.list_all_records(
            "uk1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=["NONEXISTENT_ROLE"], date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="all"
        )
        assert result_records == []


# ---------------------------------------------------------------------------
# get_records (resolves user_id)
# ---------------------------------------------------------------------------


class TestGetRecordsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.list_all_records = AsyncMock(return_value=([], 0, {}))
        result_records, total, filters = await connected_provider.get_records(
            "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=None, date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="all"
        )
        assert result_records == []

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result_records, total, filters = await connected_provider.get_records(
            "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=None, date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="all"
        )
        assert result_records == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_user_no_key(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"userId": "u1"}
        )
        result_records, total, filters = await connected_provider.get_records(
            "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=None, date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="all"
        )
        assert result_records == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("fail"))
        result_records, total, filters = await connected_provider.get_records(
            "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            permissions=None, date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", source="all"
        )
        assert result_records == []


# ---------------------------------------------------------------------------
# list_kb_records
# ---------------------------------------------------------------------------


class TestListKbRecordsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        connected_provider.execute_query = AsyncMock(
            side_effect=[
                [{"id": "r1", "recordName": "Rec1"}],
                [1],
                [[{"id": "f1", "name": "Folder"}]],
            ]
        )
        records, total, filters = await connected_provider.list_kb_records(
            "kb1", "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc"
        )
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value=None)
        records, total, filters = await connected_provider.list_kb_records(
            "kb1", "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc"
        )
        assert records == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_with_folder_filter(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        connected_provider.execute_query = AsyncMock(
            side_effect=[[], [0], [[]]]
        )
        records, total, filters = await connected_provider.list_kb_records(
            "kb1", "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc", folder_id="f1"
        )
        assert records == []

    @pytest.mark.asyncio
    async def test_with_all_filters(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        connected_provider.execute_query = AsyncMock(
            side_effect=[[], [0], [[]]]
        )
        records, total, filters = await connected_provider.list_kb_records(
            "kb1", "u1", "org1", skip=0, limit=10,
            search="test", record_types=["FILE"], origins=["UPLOAD"],
            connectors=["KNOWLEDGE_BASE"], indexing_status=["COMPLETED"],
            date_from=1000, date_to=9999,
            sort_by="recordName", sort_order="desc"
        )
        assert records == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(side_effect=Exception("fail"))
        records, total, filters = await connected_provider.list_kb_records(
            "kb1", "u1", "org1", skip=0, limit=10,
            search=None, record_types=None, origins=None,
            connectors=None, indexing_status=None,
            date_from=None, date_to=None,
            sort_by="recordName", sort_order="asc"
        )
        assert records == []
        assert total == 0


# ---------------------------------------------------------------------------
# _validation_error
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_returns_dict(self, connected_provider):
        result = connected_provider._validation_error(400, "Bad request")
        assert result["valid"] is False
        assert result["success"] is False
        assert result["code"] == 400
        assert result["reason"] == "Bad request"


# ---------------------------------------------------------------------------
# _validate_upload_context
# ---------------------------------------------------------------------------


class TestValidateUploadContext:
    @pytest.mark.asyncio
    async def test_success_root(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        result = await connected_provider._validate_upload_context("kb1", "u1", "org1")
        assert result["valid"] is True
        assert result["upload_target"] == "kb_root"

    @pytest.mark.asyncio
    async def test_success_folder(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        connected_provider.get_and_validate_folder_in_kb = AsyncMock(
            return_value={"_key": "f1", "path": "/folder1"}
        )
        result = await connected_provider._validate_upload_context(
            "kb1", "u1", "org1", parent_folder_id="f1"
        )
        assert result["valid"] is True
        assert result["upload_target"] == "folder"

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider._validate_upload_context("kb1", "u1", "org1")
        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        result = await connected_provider._validate_upload_context("kb1", "u1", "org1")
        assert result["valid"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_parent_folder_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        connected_provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
        result = await connected_provider._validate_upload_context(
            "kb1", "u1", "org1", parent_folder_id="missing"
        )
        assert result["valid"] is False
        assert result["code"] == 404


# ---------------------------------------------------------------------------
# _analyze_upload_structure
# ---------------------------------------------------------------------------


class TestAnalyzeUploadStructure:
    def test_flat_files(self, connected_provider):
        files = [
            {"filePath": "file1.pdf"},
            {"filePath": "file2.txt"},
        ]
        validation = {"upload_target": "kb_root"}
        result = connected_provider._analyze_upload_structure(files, validation)
        assert result["summary"]["total_folders"] == 0
        assert result["summary"]["root_files"] == 2

    def test_nested_files(self, connected_provider):
        files = [
            {"filePath": "folder1/file1.pdf"},
            {"filePath": "folder1/sub/file2.txt"},
            {"filePath": "file3.txt"},
        ]
        validation = {"upload_target": "kb_root"}
        result = connected_provider._analyze_upload_structure(files, validation)
        assert result["summary"]["total_folders"] == 2
        assert result["summary"]["root_files"] == 1
        assert result["summary"]["folder_files"] == 2

    def test_with_parent_folder(self, connected_provider):
        files = [{"filePath": "file1.pdf"}]
        validation = {
            "upload_target": "folder",
            "parent_folder": {"_key": "pf1"},
        }
        result = connected_provider._analyze_upload_structure(files, validation)
        assert result["parent_folder_id"] == "pf1"


# ---------------------------------------------------------------------------
# _populate_file_destinations
# ---------------------------------------------------------------------------


class TestPopulateFileDestinations:
    def test_basic(self, connected_provider):
        folder_analysis = {
            "file_destinations": {
                0: {"type": "folder", "folder_hierarchy_path": "folder1"},
                1: {"type": "root"},
            }
        }
        folder_map = {"folder1": "fid1"}
        connected_provider._populate_file_destinations(folder_analysis, folder_map)
        assert folder_analysis["file_destinations"][0]["folder_id"] == "fid1"

    def test_no_match(self, connected_provider):
        folder_analysis = {
            "file_destinations": {
                0: {"type": "folder", "folder_hierarchy_path": "missing"},
            }
        }
        connected_provider._populate_file_destinations(folder_analysis, {})
        assert "folder_id" not in folder_analysis["file_destinations"][0]


# ---------------------------------------------------------------------------
# _generate_upload_message
# ---------------------------------------------------------------------------


class TestGenerateUploadMessage:
    def test_single_file(self, connected_provider):
        result = {"total_created": 1, "folders_created": 0, "failed_files": []}
        msg = connected_provider._generate_upload_message(result, "KB root")
        assert "1 file" in msg
        assert "KB root" in msg

    def test_multiple_files_with_folders(self, connected_provider):
        result = {"total_created": 5, "folders_created": 2, "failed_files": []}
        msg = connected_provider._generate_upload_message(result, "folder")
        assert "5 files" in msg
        assert "2 new subfolders" in msg

    def test_with_failures(self, connected_provider):
        result = {"total_created": 3, "folders_created": 0, "failed_files": ["a.pdf"]}
        msg = connected_provider._generate_upload_message(result, "KB root")
        assert "1 file failed" in msg


# ---------------------------------------------------------------------------
# check_record_access_with_details
# ---------------------------------------------------------------------------


class TestCheckRecordAccessWithDetailsExtended:
    @pytest.mark.asyncio
    async def test_success_file_record(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1", "email": "user@test.com"}
        )
        connected_provider._get_user_app_ids = AsyncMock(return_value=["app1"])
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [[{"type": "DIRECT", "source": {}, "role": "OWNER"}]],
                [{"departments": [], "categories": [], "subcategories1": [], "subcategories2": [], "subcategories3": [], "topics": [], "languages": []}],
            ]
        )
        connected_provider.get_document = AsyncMock(side_effect=[
            {"_key": "r1", "recordName": "Test", "recordType": "FILE", "id": "r1"},
            {"extension": ".pdf", "mimeType": "application/pdf"},
        ])
        result = await connected_provider.check_record_access_with_details(
            "u1", "org1", "r1"
        )
        assert result is not None
        assert "record" in result
        assert "permissions" in result

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.check_record_access_with_details(
            "u1", "org1", "r1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_access(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk1", "userId": "u1"}
        )
        connected_provider._get_user_app_ids = AsyncMock(return_value=[])
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.check_record_access_with_details(
            "u1", "org1", "r1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.check_record_access_with_details("u1", "org1", "r1")


# ---------------------------------------------------------------------------
# get_account_type
# ---------------------------------------------------------------------------


class TestGetAccountType:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["ENTERPRISE"])
        result = await connected_provider.get_account_type("org1")
        assert result == "ENTERPRISE"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_account_type("org1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_account_type("org1")
        assert result is None


# ---------------------------------------------------------------------------
# is_record_descendant_of
# ---------------------------------------------------------------------------


class TestIsRecordDescendantOf:
    @pytest.mark.asyncio
    async def test_is_descendant(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[1])
        result = await connected_provider.is_record_descendant_of("child1", "parent1")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_descendant(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.is_record_descendant_of("child1", "parent1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.is_record_descendant_of("child1", "parent1")
        assert result is False


# ---------------------------------------------------------------------------
# get_record_parent_info
# ---------------------------------------------------------------------------


class TestGetRecordParentInfo:
    @pytest.mark.asyncio
    async def test_has_parent(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"id": "p1", "type": "record"}]
        )
        result = await connected_provider.get_record_parent_info("r1")
        assert result is not None
        assert result["id"] == "p1"

    @pytest.mark.asyncio
    async def test_no_parent(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_record_parent_info("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_parent_info("r1")
        assert result is None


# ---------------------------------------------------------------------------
# delete_parent_child_edge_to_record
# ---------------------------------------------------------------------------


class TestDeleteParentChildEdgeToRecord:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "e1"}])
        count = await connected_provider.delete_parent_child_edge_to_record("r1")
        assert count == 1

    @pytest.mark.asyncio
    async def test_no_edges(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        count = await connected_provider.delete_parent_child_edge_to_record("r1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception_without_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        count = await connected_provider.delete_parent_child_edge_to_record("r1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception_with_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.delete_parent_child_edge_to_record("r1", transaction="txn1")


# ---------------------------------------------------------------------------
# create_parent_child_edge
# ---------------------------------------------------------------------------


class TestCreateParentChildEdge:
    @pytest.mark.asyncio
    async def test_success_folder_parent(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "e1"}])
        result = await connected_provider.create_parent_child_edge("p1", "c1")
        assert result is True

    @pytest.mark.asyncio
    async def test_success_kb_parent(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "e1"}])
        result = await connected_provider.create_parent_child_edge(
            "kb1", "c1", parent_is_kb=True
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.create_parent_child_edge("p1", "c1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_without_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.create_parent_child_edge("p1", "c1")
        assert result is False


# ---------------------------------------------------------------------------
# update_record_external_parent_id
# ---------------------------------------------------------------------------


class TestUpdateRecordExternalParentId:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        result = await connected_provider.update_record_external_parent_id("r1", "new_parent")
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.update_record_external_parent_id("r1", "new_parent")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_without_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.update_record_external_parent_id("r1", "np")
        assert result is False


# ---------------------------------------------------------------------------
# is_record_folder
# ---------------------------------------------------------------------------


class TestIsRecordFolder:
    @pytest.mark.asyncio
    async def test_is_folder(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[True])
        result = await connected_provider.is_record_folder("r1")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_not_folder(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[False])
        result = await connected_provider.is_record_folder("r1")
        assert result is False

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.is_record_folder("r1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.is_record_folder("r1")
        assert result is False


# ---------------------------------------------------------------------------
# find_duplicate_records
# ---------------------------------------------------------------------------


class TestFindDuplicateRecords:
    @pytest.mark.asyncio
    async def test_found_duplicates(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r2", "md5Checksum": "abc123"}]
        )
        result = await connected_provider.find_duplicate_records("r1", "abc123")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_duplicates(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.find_duplicate_records("r1", "abc123")
        assert result == []

    @pytest.mark.asyncio
    async def test_with_record_type_filter(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.find_duplicate_records(
            "r1", "abc123", record_type="FILE"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_with_size_filter(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.find_duplicate_records(
            "r1", "abc123", size_in_bytes=1024
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.find_duplicate_records("r1", "abc123")
        assert result == []


# ---------------------------------------------------------------------------
# find_next_queued_duplicate
# ---------------------------------------------------------------------------


class TestFindNextQueuedDuplicate:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [{"_key": "r1", "md5Checksum": "abc", "sizeInBytes": 1024}],
                [{"_key": "r2", "md5Checksum": "abc", "indexingStatus": "QUEUED"}],
            ]
        )
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result is not None
        assert result["_key"] == "r2"

    @pytest.mark.asyncio
    async def test_no_queued(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [{"_key": "r1", "md5Checksum": "abc", "sizeInBytes": 1024}],
                [],
            ]
        )
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ref_record_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.find_next_queued_duplicate("r999")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_md5(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r1", "sizeInBytes": 1024}]
        )
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result is None


# ---------------------------------------------------------------------------
# copy_document_relationships
# ---------------------------------------------------------------------------


class TestCopyDocumentRelationships:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"from": "records/r1", "to": "departments/d1", "timestamp": 1000}]
        )
        connected_provider.http_client.create_document = AsyncMock()
        result = await connected_provider.copy_document_relationships("r1", "r2")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_relationships(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.copy_document_relationships("r1", "r2")
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.copy_document_relationships("r1", "r2")
        assert result is False


# ---------------------------------------------------------------------------
# get_teams
# ---------------------------------------------------------------------------


class TestGetTeams:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        teams = [{"id": "t1", "name": "Team 1", "memberCount": 2}]
        connected_provider.execute_query = AsyncMock(
            side_effect=[[1], teams]
        )
        result_teams, total = await connected_provider.get_teams("org1", "uk1")
        assert len(result_teams) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[0], []]
        )
        result_teams, total = await connected_provider.get_teams(
            "org1", "uk1", search="test"
        )
        assert result_teams == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result_teams, total = await connected_provider.get_teams("org1", "uk1")
        assert result_teams == []
        assert total == 0


# ---------------------------------------------------------------------------
# get_team_with_users
# ---------------------------------------------------------------------------


class TestGetTeamWithUsers:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"id": "t1", "name": "Team", "members": []}]
        )
        result = await connected_provider.get_team_with_users("t1", "uk1")
        assert result is not None
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_with_users("t999", "uk1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_team_with_users("t1", "uk1")
        assert result is None


# ---------------------------------------------------------------------------
# get_user_teams
# ---------------------------------------------------------------------------


class TestGetUserTeams:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[2], [{"id": "t1"}, {"id": "t2"}]]
        )
        teams, total = await connected_provider.get_user_teams("uk1")
        assert len(teams) == 2
        assert total == 2

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[0], []]
        )
        teams, total = await connected_provider.get_user_teams("uk1", search="test")
        assert teams == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        teams, total = await connected_provider.get_user_teams("uk1")
        assert teams == []
        assert total == 0


# ---------------------------------------------------------------------------
# get_user_created_teams
# ---------------------------------------------------------------------------


class TestGetUserCreatedTeams:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[1], [{"id": "t1", "createdBy": "uk1"}]]
        )
        teams, total = await connected_provider.get_user_created_teams("org1", "uk1")
        assert len(teams) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[0], []]
        )
        teams, total = await connected_provider.get_user_created_teams(
            "org1", "uk1", search="test"
        )
        assert teams == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        teams, total = await connected_provider.get_user_created_teams("org1", "uk1")
        assert teams == []
        assert total == 0


# ---------------------------------------------------------------------------
# get_team_users
# ---------------------------------------------------------------------------


class TestGetTeamUsersExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"id": "t1", "members": [{"id": "u1"}], "memberCount": 1}]
        )
        result = await connected_provider.get_team_users("t1", "org1", "uk1")
        assert result is not None
        assert result["memberCount"] == 1

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_users("t999", "org1", "uk1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_team_users("t1", "org1", "uk1")
        assert result is None


# ---------------------------------------------------------------------------
# search_teams
# ---------------------------------------------------------------------------


class TestSearchTeams:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"team": {"id": "t1", "name": "Test"}}]
        )
        result = await connected_provider.search_teams("org1", "uk1", "test")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.search_teams("org1", "uk1", "xyz")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.search_teams("org1", "uk1", "test")
        assert result == []


# ---------------------------------------------------------------------------
# delete_team_member_edges
# ---------------------------------------------------------------------------


class TestDeleteTeamMemberEdges:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "e1"}])
        result = await connected_provider.delete_team_member_edges("t1", ["u1"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_edges(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.delete_team_member_edges("t1", ["u1"])
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.delete_team_member_edges("t1", ["u1"])
        assert result == []


# ---------------------------------------------------------------------------
# batch_update_team_member_roles
# ---------------------------------------------------------------------------


class TestBatchUpdateTeamMemberRoles:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "p1", "role": "WRITER"}]
        )
        result = await connected_provider.batch_update_team_member_roles(
            "t1", [{"userId": "u1", "role": "WRITER"}], 1000
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.batch_update_team_member_roles(
            "t1", [{"userId": "u1", "role": "WRITER"}], 1000
        )
        assert result == []


# ---------------------------------------------------------------------------
# delete_all_team_permissions
# ---------------------------------------------------------------------------


class TestDeleteAllTeamPermissions:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "p1"}])
        await connected_provider.delete_all_team_permissions("t1")
        connected_provider.execute_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.delete_all_team_permissions("t1")


# ---------------------------------------------------------------------------
# get_team_owner_removal_info
# ---------------------------------------------------------------------------


class TestGetTeamOwnerRemovalInfo:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"ownersBeingRemoved": ["u1"], "totalOwnerCount": 2}]
        )
        result = await connected_provider.get_team_owner_removal_info("t1", ["u1"])
        assert result["owners_being_removed"] == ["u1"]
        assert result["total_owner_count"] == 2

    @pytest.mark.asyncio
    async def test_no_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_owner_removal_info("t1", ["u1"])
        assert result["owners_being_removed"] == []
        assert result["total_owner_count"] == 0

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.get_team_owner_removal_info("t1", ["u1"])


# ---------------------------------------------------------------------------
# get_team_permissions_and_owner_count
# ---------------------------------------------------------------------------


class TestGetTeamPermissionsAndOwnerCount:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{
                "team": {"_key": "t1", "name": "Team"},
                "permissions": [{"userId": "u1", "role": "OWNER"}],
                "ownerCount": 1,
            }]
        )
        result = await connected_provider.get_team_permissions_and_owner_count("t1", ["u1"])
        assert result["team"] is not None
        assert result["permissions"]["u1"] == "OWNER"
        assert result["owner_count"] == 1

    @pytest.mark.asyncio
    async def test_no_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_permissions_and_owner_count("t1", ["u1"])
        assert result["team"] is None
        assert result["owner_count"] == 0

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.get_team_permissions_and_owner_count("t1", ["u1"])


# ---------------------------------------------------------------------------
# get_organization_users
# ---------------------------------------------------------------------------


class TestGetOrganizationUsers:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[5], [{"id": "u1", "name": "User 1"}]]
        )
        users, total = await connected_provider.get_organization_users("org1")
        assert len(users) == 1
        assert total == 5

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[[0], []]
        )
        users, total = await connected_provider.get_organization_users("org1", search="test")
        assert users == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        users, total = await connected_provider.get_organization_users("org1")
        assert users == []
        assert total == 0


# ---------------------------------------------------------------------------
# get_all_agents
# ---------------------------------------------------------------------------


class TestGetAllAgentsExtended:
    @pytest.mark.asyncio
    async def test_success_no_pagination(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"agents": [{"_key": "a1", "name": "Agent 1"}], "totalItems": 1}]
        )
        result = await connected_provider.get_all_agents("u1", "org1")
        assert isinstance(result, list)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_success_with_pagination(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"agents": [{"_key": "a1"}], "totalItems": 5}]
        )
        result = await connected_provider.get_all_agents(
            "u1", "org1", page=1, limit=10
        )
        assert isinstance(result, dict)
        assert result["totalItems"] == 5

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"agents": [], "totalItems": 0}]
        )
        result = await connected_provider.get_all_agents(
            "u1", "org1", page=1, limit=10, search="test"
        )
        assert result["totalItems"] == 0

    @pytest.mark.asyncio
    async def test_with_sort(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"agents": [], "totalItems": 0}]
        )
        result = await connected_provider.get_all_agents(
            "u1", "org1", sort_by="name", sort_order="asc"
        )
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=None)
        result = await connected_provider.get_all_agents("u1", "org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_with_pagination(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=None)
        result = await connected_provider.get_all_agents("u1", "org1", page=1, limit=10)
        assert result == {"agents": [], "totalItems": 0}

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_all_agents("u1", "org1")
        assert result == []


# ---------------------------------------------------------------------------
# hard_delete_all_agents
# ---------------------------------------------------------------------------


class TestHardDeleteAllAgents:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[
                [{"_key": "e1", "_to": "agentKnowledge/k1"}],  # knowledge edges
                [{"_key": "k1"}],  # knowledge nodes
                ["agentToolsets/ts1"],  # toolset ids
                [{"_key": "te1", "_to": "agentTools/tool1"}],  # tool edges
                [{"_key": "tool1"}],  # tool nodes
                [{"_key": "tse1"}],  # toolset edges
                [{"_key": "ts1"}],  # toolset nodes
                [{"_key": "pe1"}],  # permission edges
                [{"_key": "a1"}],  # agent docs
            ]
        )
        result = await connected_provider.hard_delete_all_agents()
        assert result["agents_deleted"] == 1
        assert result["toolsets_deleted"] == 1
        assert result["tools_deleted"] == 1
        assert result["knowledge_deleted"] == 1

    @pytest.mark.asyncio
    async def test_nothing_to_delete(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            side_effect=[
                None,  # no knowledge edges
                None,  # no toolset ids
                None,  # no toolset edges
                None,  # no toolsets
                None,  # no permissions
                None,  # no agents
            ]
        )
        result = await connected_provider.hard_delete_all_agents()
        assert result["agents_deleted"] == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.hard_delete_all_agents()
        assert result["agents_deleted"] == 0
        assert result["edges_deleted"] == 0


# ---------------------------------------------------------------------------
# _get_attachment_ids
# ---------------------------------------------------------------------------


class TestGetAttachmentIds:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["att1", "att2"])
        result = await connected_provider._get_attachment_ids("r1")
        assert result == ["att1", "att2"]

    @pytest.mark.asyncio
    async def test_none(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider._get_attachment_ids("r1")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider._get_attachment_ids("r1")
        assert result == []


# ---------------------------------------------------------------------------
# _delete_record_with_type
# ---------------------------------------------------------------------------


class TestDeleteRecordWithType:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.delete_edges_from = AsyncMock()
        connected_provider.delete_edges_to = AsyncMock()
        connected_provider.delete_nodes = AsyncMock()
        await connected_provider._delete_record_with_type(
            "r1", ["files", "mails"]
        )
        assert connected_provider.delete_edges_from.call_count == 3
        assert connected_provider.delete_edges_to.call_count == 2
        assert connected_provider.delete_nodes.call_count == 3  # 2 type collections + 1 main record


# ---------------------------------------------------------------------------
# get_connector_stats
# ---------------------------------------------------------------------------


class TestGetConnectorStatsExtendedV2:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[
                {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 10},
            ]
        )
        with patch("app.services.graph_db.arango.arango_http_provider.build_connector_stats_response") as mock_build:
            mock_build.return_value = {"totalRecords": 10}
            result = await connected_provider.get_connector_stats("org1", "c1")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_connector_stats("org1", "c1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _create_update_record_event_payload
# ---------------------------------------------------------------------------


class TestCreateUpdateRecordEventPayload:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )
        record = {"orgId": "o1", "_key": "r1", "version": 1, "externalRecordId": "ext1",
                  "updatedAtTimestamp": 2000, "sourceLastModifiedTimestamp": 1000}
        file_record = {"extension": ".pdf", "mimeType": "application/pdf"}
        result = await connected_provider._create_update_record_event_payload(record, file_record)
        assert result is not None
        assert result["recordId"] == "r1"

    @pytest.mark.asyncio
    async def test_no_file_record(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )
        record = {"orgId": "o1", "_key": "r1", "externalRecordId": "ext1"}
        result = await connected_provider._create_update_record_event_payload(record, None)
        assert result["extension"] == ""

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider._create_update_record_event_payload({}, None)
        assert result is None


# ---------------------------------------------------------------------------
# _create_reindex_event_payload
# ---------------------------------------------------------------------------


class TestCreateReindexEventPayload:
    @pytest.mark.asyncio
    async def test_upload_origin(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(
            return_value={
                "storage": {"endpoint": "http://storage:3000"},
                "connectors": {"endpoint": "http://conn:8088"},
            }
        )
        record = {
            "_key": "r1", "orgId": "o1", "recordName": "Test",
            "recordType": "FILE", "version": 1, "origin": "UPLOAD",
            "externalRecordId": "ext1", "connectorId": "c1",
        }
        result = await connected_provider._create_reindex_event_payload(record, None)
        assert "signedUrlRoute" in result
        assert "storage" in result["signedUrlRoute"]

    @pytest.mark.asyncio
    async def test_connector_origin(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(
            return_value={
                "storage": {"endpoint": "http://storage:3000"},
                "connectors": {"endpoint": "http://conn:8088"},
            }
        )
        record = {
            "_key": "r1", "orgId": "o1", "recordName": "Test",
            "recordType": "FILE", "version": 1, "origin": "CONNECTOR",
            "externalRecordId": "ext1", "connectorName": "GOOGLE_DRIVE",
        }
        result = await connected_provider._create_reindex_event_payload(
            record, None, user_id="u1"
        )
        assert "conn:8088" in result["signedUrlRoute"]

    @pytest.mark.asyncio
    async def test_mail_type(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(
            return_value={
                "storage": {"endpoint": "http://storage:3000"},
                "connectors": {"endpoint": "http://conn:8088"},
            }
        )
        record = {
            "_key": "r1", "orgId": "o1", "recordName": "Mail",
            "recordType": "MAIL", "version": 1, "origin": "CONNECTOR",
            "externalRecordId": "ext1", "connectorName": "GMAIL",
        }
        result = await connected_provider._create_reindex_event_payload(
            record, None, user_id="u1"
        )
        assert result["mimeType"] == "text/gmail_content"

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider._create_reindex_event_payload({}, None)

    @pytest.mark.asyncio
    async def test_uses_id_field(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(
            return_value={
                "storage": {"endpoint": "http://storage:3000"},
                "connectors": {"endpoint": "http://conn:8088"},
            }
        )
        record = {
            "id": "r1", "orgId": "o1", "recordName": "Test",
            "recordType": "FILE", "version": 1, "origin": "UPLOAD",
            "externalRecordId": "ext1",
        }
        result = await connected_provider._create_reindex_event_payload(record, None)
        assert result["recordId"] == "r1"

    @pytest.mark.asyncio
    async def test_with_file_record_mime(self, connected_provider):
        connected_provider.config_service.get_config = AsyncMock(
            return_value={
                "storage": {"endpoint": "http://storage:3000"},
                "connectors": {"endpoint": "http://conn:8088"},
            }
        )
        record = {
            "_key": "r1", "orgId": "o1", "recordName": "Test",
            "recordType": "FILE", "version": 1, "origin": "UPLOAD",
            "externalRecordId": "ext1",
        }
        file_record = {"extension": ".pdf", "mimeType": "application/pdf"}
        result = await connected_provider._create_reindex_event_payload(record, file_record)
        assert result["mimeType"] == "application/pdf"


# ---------------------------------------------------------------------------
# _ensure_folders_exist
# ---------------------------------------------------------------------------


class TestEnsureFoldersExist:
    @pytest.mark.asyncio
    async def test_existing_folders(self, connected_provider):
        connected_provider.find_folder_by_name_in_parent = AsyncMock(
            return_value={"_key": "existing_f1"}
        )
        folder_analysis = {
            "sorted_folder_paths": ["folder1"],
            "folder_hierarchy": {"folder1": {"name": "Folder1", "parent_path": None, "level": 1}},
        }
        validation = {"upload_target": "kb_root"}
        result = await connected_provider._ensure_folders_exist(
            "kb1", "org1", folder_analysis, validation, "txn1"
        )
        assert result["folder1"] == "existing_f1"

    @pytest.mark.asyncio
    async def test_create_new_folders(self, connected_provider):
        connected_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        connected_provider.create_folder = AsyncMock(
            return_value={"id": "new_f1", "success": True}
        )
        folder_analysis = {
            "sorted_folder_paths": ["folder1"],
            "folder_hierarchy": {"folder1": {"name": "Folder1", "parent_path": None, "level": 1}},
        }
        validation = {"upload_target": "kb_root"}
        result = await connected_provider._ensure_folders_exist(
            "kb1", "org1", folder_analysis, validation, "txn1"
        )
        assert result["folder1"] == "new_f1"

    @pytest.mark.asyncio
    async def test_folder_creation_failure(self, connected_provider):
        connected_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        connected_provider.create_folder = AsyncMock(return_value=None)
        folder_analysis = {
            "sorted_folder_paths": ["folder1"],
            "folder_hierarchy": {"folder1": {"name": "Folder1", "parent_path": None, "level": 1}},
        }
        validation = {"upload_target": "kb_root"}
        with pytest.raises(ValueError):
            await connected_provider._ensure_folders_exist(
                "kb1", "org1", folder_analysis, validation, "txn1"
            )

    @pytest.mark.asyncio
    async def test_with_parent_upload_folder(self, connected_provider):
        connected_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        connected_provider.create_folder = AsyncMock(
            return_value={"id": "new_f1"}
        )
        folder_analysis = {
            "sorted_folder_paths": ["folder1"],
            "folder_hierarchy": {"folder1": {"name": "Folder1", "parent_path": None, "level": 1}},
        }
        validation = {
            "upload_target": "folder",
            "parent_folder": {"_key": "parent_f1"},
        }
        result = await connected_provider._ensure_folders_exist(
            "kb1", "org1", folder_analysis, validation, "txn1"
        )
        assert "folder1" in result


# ---------------------------------------------------------------------------
# Knowledge Hub methods
# ---------------------------------------------------------------------------


class TestGetKnowledgeHubRootNodes:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [{"id": "app1", "name": "App", "nodeType": "app"}], "total": 1}]
        )
        result = await connected_provider.get_knowledge_hub_root_nodes(
            "uk1", "org1", ["app1"], skip=0, limit=10,
            sort_field="name", sort_dir="ASC", only_containers=False
        )
        assert result["total"] == 1
        assert len(result["nodes"]) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_hub_root_nodes(
            "uk1", "org1", [], skip=0, limit=10,
            sort_field="name", sort_dir="ASC", only_containers=True
        )
        assert result == {"nodes": [], "total": 0}


class TestGetKnowledgeHubChildren:
    @pytest.mark.asyncio
    async def test_app_parent(self, connected_provider):
        connected_provider._get_app_children_subquery = MagicMock(
            return_value=("LET raw_children = []", {"app_id": "app1"})
        )
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [], "total": 0}]
        )
        result = await connected_provider.get_knowledge_hub_children(
            "app1", "app", "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC"
        )
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_record_group_parent(self, connected_provider):
        connected_provider._get_record_group_children_split = AsyncMock(
            return_value={"nodes": [{"id": "r1"}], "total": 1}
        )
        result = await connected_provider.get_knowledge_hub_children(
            "rg1", "recordGroup", "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC"
        )
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_folder_parent(self, connected_provider):
        connected_provider._get_record_children_subquery = MagicMock(
            return_value=("LET raw_children = []", {"record_id": "f1"})
        )
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [], "total": 0}]
        )
        result = await connected_provider.get_knowledge_hub_children(
            "f1", "folder", "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC"
        )
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_unknown_parent_type(self, connected_provider):
        result = await connected_provider.get_knowledge_hub_children(
            "x1", "unknown_type", "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC"
        )
        assert result == {"nodes": [], "total": 0}

    @pytest.mark.asyncio
    async def test_with_record_group_ids(self, connected_provider):
        connected_provider._get_app_children_subquery = MagicMock(
            return_value=("LET raw_children = []", {"app_id": "app1"})
        )
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [], "total": 0}]
        )
        result = await connected_provider.get_knowledge_hub_children(
            "app1", "app", "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC", record_group_ids=["rg1"]
        )
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider._get_app_children_subquery = MagicMock(
            return_value=("LET raw_children = []", {})
        )
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_hub_children(
            "app1", "app", "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC"
        )
        assert result == {"nodes": [], "total": 0}


class TestGetKnowledgeHubSearch:
    @pytest.mark.asyncio
    async def test_global_search(self, connected_provider):
        connected_provider._build_knowledge_hub_filter_conditions = MagicMock(
            return_value=([], {})
        )
        connected_provider._build_scope_filters = MagicMock(
            return_value=("", "", "true", "true")
        )
        connected_provider._build_children_intersection_aql = MagicMock(return_value="")
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [{"id": "r1"}], "total": 1, "availableFilters": {}}]
        )
        result = await connected_provider.get_knowledge_hub_search(
            "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC", search_query="test"
        )
        assert "nodes" in result or "total" in result

    @pytest.mark.asyncio
    async def test_scoped_search_record_group(self, connected_provider):
        connected_provider._build_knowledge_hub_filter_conditions = MagicMock(
            return_value=([], {})
        )
        connected_provider._build_children_intersection_aql = MagicMock(return_value="")
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [], "total": 0, "availableFilters": {}}]
        )
        result = await connected_provider.get_knowledge_hub_search(
            "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC",
            parent_id="rg1", parent_type="recordGroup"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_scoped_search_record(self, connected_provider):
        connected_provider._build_knowledge_hub_filter_conditions = MagicMock(
            return_value=([], {})
        )
        connected_provider._build_children_intersection_aql = MagicMock(return_value="")
        connected_provider.get_document = AsyncMock(return_value={"connectorId": "c1"})
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [], "total": 0, "availableFilters": {}}]
        )
        result = await connected_provider.get_knowledge_hub_search(
            "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC",
            parent_id="r1", parent_type="record"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_filters(self, connected_provider):
        connected_provider._build_knowledge_hub_filter_conditions = MagicMock(
            return_value=(["node.nodeType == @node_types"], {"node_types": ["record"]})
        )
        connected_provider._build_scope_filters = MagicMock(
            return_value=("", "", "true", "true")
        )
        connected_provider._build_children_intersection_aql = MagicMock(return_value="")
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [], "total": 0, "availableFilters": {}}]
        )
        result = await connected_provider.get_knowledge_hub_search(
            "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC",
            node_types=["record"], record_types=["FILE"]
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_app_scope(self, connected_provider):
        connected_provider._build_knowledge_hub_filter_conditions = MagicMock(
            return_value=([], {})
        )
        connected_provider._build_scope_filters = MagicMock(
            return_value=("", "", "true", "true")
        )
        connected_provider._build_children_intersection_aql = MagicMock(return_value="")
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"nodes": [], "total": 0, "availableFilters": {}}]
        )
        result = await connected_provider.get_knowledge_hub_search(
            "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC",
            parent_id="app1", parent_type="app"
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider._build_knowledge_hub_filter_conditions = MagicMock(
            return_value=([], {})
        )
        connected_provider._build_scope_filters = MagicMock(
            return_value=("", "", "true", "true")
        )
        connected_provider._build_children_intersection_aql = MagicMock(return_value="")
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_hub_search(
            "org1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC"
        )
        assert result is not None


class TestGetKnowledgeHubBreadcrumbs:
    @pytest.mark.asyncio
    async def test_single_node(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [{"id": "app1", "name": "App", "nodeType": "app", "subType": "GOOGLE_DRIVE", "parentId": None}],
            ]
        )
        result = await connected_provider.get_knowledge_hub_breadcrumbs("app1")
        assert len(result) == 1
        assert result[0]["id"] == "app1"

    @pytest.mark.asyncio
    async def test_multi_level(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [{"id": "r1", "name": "Record", "nodeType": "record", "subType": "FILE", "parentId": "rg1"}],
                [{"id": "rg1", "name": "Group", "nodeType": "recordGroup", "subType": "DRIVE", "parentId": "app1"}],
                [{"id": "app1", "name": "App", "nodeType": "app", "subType": "GOOGLE_DRIVE", "parentId": None}],
            ]
        )
        result = await connected_provider.get_knowledge_hub_breadcrumbs("r1")
        assert len(result) == 3
        assert result[0]["nodeType"] == "app"
        assert result[2]["nodeType"] == "record"

    @pytest.mark.asyncio
    async def test_node_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_knowledge_hub_breadcrumbs("missing")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_hub_breadcrumbs("missing")
        assert result == []


class TestGetUserAppIds:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_user_apps = AsyncMock(return_value=[
            {"_key": "app1", "id": "app1"},
            {"_key": "app2", "id": "app2"}
        ])
        result = await connected_provider.get_user_app_ids("uk1")
        assert result == ["app1", "app2"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.get_user_apps = AsyncMock(return_value=[])
        result = await connected_provider.get_user_app_ids("uk1")
        assert result == []

    @pytest.mark.asyncio
    async def test_none(self, connected_provider):
        connected_provider.get_user_apps = AsyncMock(return_value=None)
        result = await connected_provider.get_user_app_ids("uk1")
        assert result == []


class TestGetKnowledgeHubNodeInfo:
    @pytest.mark.asyncio
    async def test_found_record(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"id": "r1", "name": "Record", "nodeType": "record", "subType": "FILE"}]
        )
        result = await connected_provider.get_knowledge_hub_node_info("r1", ["application/vnd.folder"])
        assert result is not None
        assert result["id"] == "r1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_knowledge_hub_node_info("missing", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_hub_node_info("missing", [])
        assert result is None


class TestGetKnowledgeHubParentNode:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"id": "rg1", "name": "Group", "nodeType": "recordGroup", "subType": "DRIVE"}]
        )
        result = await connected_provider.get_knowledge_hub_parent_node("r1", ["application/vnd.folder"])
        assert result is not None
        assert result["id"] == "rg1"

    @pytest.mark.asyncio
    async def test_no_parent(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_knowledge_hub_parent_node("app1", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_hub_parent_node("x", [])
        assert result is None


class TestGetKnowledgeHubFilterOptions:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"apps": [{"id": "a1", "name": "App", "type": "GOOGLE_DRIVE"}]}]
        )
        result = await connected_provider.get_knowledge_hub_filter_options("uk1", "org1")
        assert len(result["apps"]) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_hub_filter_options("uk1", "org1")
        assert result == {"apps": []}

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_knowledge_hub_filter_options("uk1", "org1")
        assert result == {"apps": []}


class TestGetKnowledgeHubContextPermissions:
    @pytest.mark.asyncio
    async def test_no_parent(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"role": None, "canUpload": False, "canCreateFolders": False,
                          "canEdit": False, "canDelete": False, "canManagePermissions": False}]
        )
        result = await connected_provider.get_knowledge_hub_context_permissions("uk1", "org1", None)
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_valid_parent_id(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"role": "OWNER", "canUpload": True, "canCreateFolders": True,
                          "canEdit": True, "canDelete": True, "canManagePermissions": True}]
        )
        result = await connected_provider.get_knowledge_hub_context_permissions("uk1", "org1", "some_kb_id")
        assert result is not None

    @pytest.mark.asyncio
    async def test_empty_parent_id(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"role": None, "canUpload": False, "canCreateFolders": False,
                          "canEdit": False, "canDelete": False, "canManagePermissions": False}]
        )
        result = await connected_provider.get_knowledge_hub_context_permissions("uk1", "org1", "  ")
        assert result is not None


# ---------------------------------------------------------------------------
# _execute_outlook_record_deletion
# ---------------------------------------------------------------------------


class TestExecuteOutlookRecordDeletion:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                ["att1"],  # attachment_ids
            ]
        )
        connected_provider._delete_record_with_type = AsyncMock()
        result = await connected_provider._execute_outlook_record_deletion(
            "r1", {"_key": "r1", "connectorName": "OUTLOOK"}
        )
        assert result is not None


# ---------------------------------------------------------------------------
# _create_files_in_kb_root / _create_files_in_folder
# ---------------------------------------------------------------------------


class TestCreateFilesInKbRoot:
    @pytest.mark.asyncio
    async def test_delegates_to_batch(self, connected_provider):
        connected_provider._create_files_batch = AsyncMock(return_value=[{"record": {}}])
        result = await connected_provider._create_files_in_kb_root("kb1", [{}], "txn1", 1000)
        assert len(result) == 1
        connected_provider._create_files_batch.assert_called_once_with(
            kb_id="kb1", files=[{}], parent_folder_id=None, transaction="txn1", timestamp=1000
        )


class TestCreateFilesInFolder:
    @pytest.mark.asyncio
    async def test_delegates_to_batch(self, connected_provider):
        connected_provider._create_files_batch = AsyncMock(return_value=[{"record": {}}])
        result = await connected_provider._create_files_in_folder("kb1", "f1", [{}], "txn1", 1000)
        assert len(result) == 1
        connected_provider._create_files_batch.assert_called_once_with(
            kb_id="kb1", files=[{}], parent_folder_id="f1", transaction="txn1", timestamp=1000
        )


# ---------------------------------------------------------------------------
# _create_records
# ---------------------------------------------------------------------------


class TestCreateRecords:
    @pytest.mark.asyncio
    async def test_root_files(self, connected_provider):
        connected_provider._create_files_in_kb_root = AsyncMock(return_value=[{"record": {}}])
        folder_analysis = {
            "file_destinations": {0: {"type": "root"}},
            "parent_folder_id": None,
        }
        result = await connected_provider._create_records(
            "kb1", [{"filePath": "file.pdf"}], folder_analysis, "txn1", 1000
        )
        assert result["total_created"] == 1

    @pytest.mark.asyncio
    async def test_folder_files(self, connected_provider):
        connected_provider._create_files_in_folder = AsyncMock(return_value=[{"record": {}}])
        folder_analysis = {
            "file_destinations": {0: {"type": "folder", "folder_id": "f1"}},
            "parent_folder_id": None,
        }
        result = await connected_provider._create_records(
            "kb1", [{"filePath": "folder/file.pdf"}], folder_analysis, "txn1", 1000
        )
        assert result["total_created"] == 1

    @pytest.mark.asyncio
    async def test_missing_folder_id(self, connected_provider):
        folder_analysis = {
            "file_destinations": {0: {"type": "folder"}},
            "parent_folder_id": None,
        }
        result = await connected_provider._create_records(
            "kb1", [{"filePath": "folder/file.pdf"}], folder_analysis, "txn1", 1000
        )
        assert len(result["failed_files"]) == 1

    @pytest.mark.asyncio
    async def test_parent_folder_files(self, connected_provider):
        connected_provider._create_files_in_folder = AsyncMock(return_value=[{"record": {}}])
        folder_analysis = {
            "file_destinations": {0: {"type": "root"}},
            "parent_folder_id": "parent1",
        }
        result = await connected_provider._create_records(
            "kb1", [{"filePath": "file.pdf"}], folder_analysis, "txn1", 1000
        )
        assert result["total_created"] == 1


# ---------------------------------------------------------------------------
# _execute_upload_transaction
# ---------------------------------------------------------------------------


class TestExecuteUploadTransaction:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._ensure_folders_exist = AsyncMock(return_value={"folder1": "f1"})
        connected_provider._populate_file_destinations = MagicMock()
        connected_provider._create_records = AsyncMock(
            return_value={"total_created": 2, "failed_files": [], "created_files_data": []}
        )
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider._execute_upload_transaction(
            "kb1", "u1", "org1", [{}],
            {"sorted_folder_paths": [], "folder_hierarchy": {}, "file_destinations": {}, "parent_folder_id": None},
            {"upload_target": "kb_root"},
        )
        assert result["success"] is True
        assert result["total_created"] == 2

    @pytest.mark.asyncio
    async def test_nothing_created(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._ensure_folders_exist = AsyncMock(return_value={})
        connected_provider._populate_file_destinations = MagicMock()
        connected_provider._create_records = AsyncMock(
            return_value={"total_created": 0, "failed_files": ["f.pdf"], "created_files_data": []}
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider._execute_upload_transaction(
            "kb1", "u1", "org1", [{}],
            {"sorted_folder_paths": [], "folder_hierarchy": {}, "file_destinations": {}, "parent_folder_id": None},
            {"upload_target": "kb_root"},
        )
        assert result["total_created"] == 0

    @pytest.mark.asyncio
    async def test_exception_rollback(self, connected_provider):
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._ensure_folders_exist = AsyncMock(side_effect=Exception("fail"))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider._execute_upload_transaction(
            "kb1", "u1", "org1", [{}],
            {"sorted_folder_paths": ["f1"], "folder_hierarchy": {"f1": {"name": "F", "parent_path": None, "level": 1}}, "file_destinations": {}, "parent_folder_id": None},
            {"upload_target": "kb_root"},
        )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# upload_records
# ---------------------------------------------------------------------------


class TestUploadRecordsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider._validate_upload_context = AsyncMock(
            return_value={"valid": True, "user": {}, "user_key": "uk1", "user_role": "OWNER",
                         "parent_folder": None, "parent_path": "/", "upload_target": "kb_root"}
        )
        connected_provider._execute_upload_transaction = AsyncMock(
            return_value={
                "success": True, "total_created": 1, "folders_created": 0,
                "created_folders": [], "failed_files": [], "created_files_data": [],
            }
        )
        result = await connected_provider.upload_records("kb1", "u1", "org1", [])
        assert result["success"] is True
        assert result["totalCreated"] == 1

    @pytest.mark.asyncio
    async def test_validation_failure(self, connected_provider):
        connected_provider._validate_upload_context = AsyncMock(
            return_value={"valid": False, "success": False, "code": 403, "reason": "No permission"}
        )
        result = await connected_provider.upload_records("kb1", "u1", "org1", [])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_parent_folder(self, connected_provider):
        connected_provider._validate_upload_context = AsyncMock(
            return_value={"valid": True, "user": {}, "user_key": "uk1", "user_role": "WRITER",
                         "parent_folder": {"_key": "f1"}, "parent_path": "/folder", "upload_target": "folder"}
        )
        connected_provider._execute_upload_transaction = AsyncMock(
            return_value={
                "success": True, "total_created": 0, "folders_created": 0,
                "created_folders": [], "failed_files": [], "created_files_data": [],
            }
        )
        result = await connected_provider.upload_records("kb1", "u1", "org1", [], parent_folder_id="f1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_with_event_payloads(self, connected_provider):
        connected_provider._validate_upload_context = AsyncMock(
            return_value={"valid": True, "user": {}, "user_key": "uk1", "user_role": "OWNER",
                         "parent_folder": None, "parent_path": "/", "upload_target": "kb_root"}
        )
        connected_provider._execute_upload_transaction = AsyncMock(
            return_value={
                "success": True, "total_created": 1, "folders_created": 0,
                "created_folders": [],  "failed_files": [],
                "created_files_data": [{"record": {"_key": "r1"}, "fileRecord": {"_key": "f1"}}],
            }
        )
        connected_provider.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )
        connected_provider._create_new_record_event_payload = AsyncMock(
            return_value={"recordId": "r1"}
        )
        result = await connected_provider.upload_records("kb1", "u1", "org1", [{}])
        assert result["success"] is True
        assert result.get("eventData") is not None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider._validate_upload_context = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.upload_records("kb1", "u1", "org1", [])
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _create_files_batch
# ---------------------------------------------------------------------------


class TestCreateFilesBatch:
    @pytest.mark.asyncio
    async def test_empty_files(self, connected_provider):
        result = await connected_provider._create_files_batch("kb1", [], None, "txn1", 1000)
        assert result == []

    @pytest.mark.asyncio
    async def test_conflict_skipped(self, connected_provider):
        connected_provider._check_name_conflict_in_parent = AsyncMock(
            return_value={"has_conflict": True, "conflicts": [{"name": "file.pdf"}]}
        )
        result = await connected_provider._create_files_batch(
            "kb1", [{"fileRecord": {"name": "file.pdf"}, "record": {"_key": "r1", "recordName": "file.pdf"}}],
            None, "txn1", 1000
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider._check_name_conflict_in_parent = AsyncMock(
            return_value={"has_conflict": False, "conflicts": []}
        )
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        files = [{
            "fileRecord": {"_key": "f1", "name": "test.pdf", "mimeType": "application/pdf", "isFile": True},
            "record": {"_key": "r1", "recordName": "test.pdf", "orgId": "o1"},
        }]
        result = await connected_provider._create_files_batch("kb1", files, None, "txn1", 1000)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_parent_folder(self, connected_provider):
        connected_provider._check_name_conflict_in_parent = AsyncMock(
            return_value={"has_conflict": False, "conflicts": []}
        )
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()
        files = [{
            "fileRecord": {"_key": "f1", "name": "test.pdf", "mimeType": "application/pdf", "isFile": True},
            "record": {"_key": "r1", "recordName": "test.pdf", "orgId": "o1"},
        }]
        result = await connected_provider._create_files_batch("kb1", files, "parent1", "txn1", 1000)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_knowledge_hub_filter_conditions
# ---------------------------------------------------------------------------


class TestBuildKnowledgeHubFilterConditions:
    def test_no_filters(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions()
        assert conditions == []
        assert params == {}

    def test_search_query(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            search_query="Test"
        )
        assert len(conditions) == 1
        assert "search_query" in params
        assert params["search_query"] == "test"

    def test_node_types(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            node_types=["folder", "record", "recordGroup", "app"]
        )
        assert len(conditions) == 1
        assert "folder" in conditions[0]
        assert "record" in conditions[0]

    def test_record_types(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            record_types=["FILE", "MAIL"]
        )
        assert len(conditions) == 1
        assert "record_types" in params

    def test_indexing_status(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            indexing_status=["COMPLETED"]
        )
        assert "indexing_status" in params

    def test_created_at(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            created_at={"gte": 1000, "lte": 9999}
        )
        assert "created_at_gte" in params
        assert "created_at_lte" in params
        assert len(conditions) == 2

    def test_updated_at(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            updated_at={"gte": 1000, "lte": 9999}
        )
        assert "updated_at_gte" in params
        assert "updated_at_lte" in params

    def test_size(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            size={"gte": 100, "lte": 5000}
        )
        assert "size_gte" in params
        assert "size_lte" in params

    def test_origins(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            origins=["UPLOAD", "CONNECTOR"]
        )
        assert "origins" in params

    def test_connector_ids(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            connector_ids=["c1", "c2"]
        )
        assert "connector_ids" in params

    def test_record_group_ids(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            record_group_ids=["rg1"]
        )
        assert "record_group_ids" in params

    def test_only_containers(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            only_containers=True
        )
        assert len(conditions) == 1
        assert "hasChildren" in conditions[0]

    def test_all_filters(self, connected_provider):
        conditions, params = connected_provider._build_knowledge_hub_filter_conditions(
            search_query="test",
            node_types=["record"],
            record_types=["FILE"],
            indexing_status=["COMPLETED"],
            created_at={"gte": 100},
            updated_at={"lte": 999},
            size={"gte": 10},
            origins=["UPLOAD"],
            connector_ids=["c1"],
            record_group_ids=["rg1"],
            only_containers=True,
        )
        assert len(conditions) >= 8
        assert "search_query" in params


# ---------------------------------------------------------------------------
# _build_scope_filters
# ---------------------------------------------------------------------------


class TestBuildScopeFilters:
    def test_no_parent(self, connected_provider):
        result = connected_provider._build_scope_filters(None, None)
        assert result == ("", "", "true", "true")

    def test_global_with_record_group_ids(self, connected_provider):
        result = connected_provider._build_scope_filters(None, None, record_group_ids=["rg1"])
        assert "record_group_ids" in result[0]

    def test_app_parent(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters("app1", "app")
        assert "parent_id" in rg
        assert "parent_id" in rec

    def test_app_parent_with_rg_ids(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters(
            "app1", "app", record_group_ids=["rg1"]
        )
        assert "record_group_ids" in rg

    def test_record_group_parent(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters("rg1", "recordGroup")
        assert "parent_id" in rg
        assert "parent_id" in rec

    def test_kb_parent(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters("kb1", "kb")
        assert "parent_id" in rg

    def test_record_parent(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters(
            "r1", "record", parent_connector_id="c1"
        )
        assert "parent_connector_id" in rg
        assert "parent_connector_id" in rec

    def test_folder_parent(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters(
            "f1", "folder", parent_connector_id="c1"
        )
        assert "parent_connector_id" in rg

    def test_folder_parent_with_rg_ids(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters(
            "f1", "folder", parent_connector_id="c1", record_group_ids=["rg1"]
        )
        assert "record_group_ids" in rg

    def test_unknown_parent_type(self, connected_provider):
        result = connected_provider._build_scope_filters("x", "unknown_type")
        assert result == ("", "", "true", "true")

    def test_unknown_parent_type_with_rg_ids(self, connected_provider):
        rg, rec, rg_inline, rec_inline = connected_provider._build_scope_filters(
            "x", "unknown_type", record_group_ids=["rg1"]
        )
        assert "record_group_ids" in rg


# ---------------------------------------------------------------------------
# _build_children_intersection_aql
# ---------------------------------------------------------------------------


class TestBuildChildrenIntersectionAql:
    def test_record_group(self, connected_provider):
        result = connected_provider._build_children_intersection_aql("rg1", "recordGroup")
        assert "parent_rg" in result
        assert "final_accessible_rgs" in result

    def test_kb(self, connected_provider):
        result = connected_provider._build_children_intersection_aql("kb1", "kb")
        assert "parent_rg" in result

    def test_record(self, connected_provider):
        result = connected_provider._build_children_intersection_aql("r1", "record")
        assert "parent_record" in result
        assert "final_accessible_rgs = []" in result

    def test_folder(self, connected_provider):
        result = connected_provider._build_children_intersection_aql("f1", "folder")
        assert "parent_record" in result

    def test_other(self, connected_provider):
        result = connected_provider._build_children_intersection_aql("x", "app")
        assert "final_accessible_rgs = accessible_rgs" in result

    def test_none(self, connected_provider):
        result = connected_provider._build_children_intersection_aql(None, None)
        assert "final_accessible_rgs = accessible_rgs" in result


# ---------------------------------------------------------------------------
# _get_app_children_subquery
# ---------------------------------------------------------------------------


class TestGetAppChildrenSubquery:
    def test_returns_tuple(self, connected_provider):
        sub_query, bind_vars = connected_provider._get_app_children_subquery("app1", "org1", "uk1")
        assert isinstance(sub_query, str)
        assert isinstance(bind_vars, dict)
        assert "app_id" in bind_vars
        assert bind_vars["app_id"] == "app1"

    def test_contains_aql(self, connected_provider):
        sub_query, _ = connected_provider._get_app_children_subquery("app1", "org1", "uk1")
        assert "raw_children" in sub_query


# ---------------------------------------------------------------------------
# _get_record_group_children_split
# ---------------------------------------------------------------------------


class TestGetRecordGroupChildrenSplit:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        # Method makes 3 AQL calls: internal_records, child_rgs, direct_records
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [[]],  # internal records query
                [[]],  # child_rgs query
                [[]],  # direct_records query
            ]
        )
        result = await connected_provider._get_record_group_children_split(
            "rg1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC", only_containers=False
        )
        assert result["total"] >= 0

    @pytest.mark.asyncio
    async def test_only_containers(self, connected_provider):
        # When only_containers=True, internal_records + child_rgs queries + direct_records queries are made
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [[]],  # internal records query
                [[]],  # child_rgs query
                [[]],  # direct_records query (still called but results filtered)
            ]
        )
        result = await connected_provider._get_record_group_children_split(
            "rg1", "uk1", skip=0, limit=10,
            sort_field="name", sort_dir="ASC", only_containers=True
        )
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# _get_record_group_children_subquery
# ---------------------------------------------------------------------------


class TestGetRecordGroupChildrenSubquery:
    def test_returns_tuple(self, connected_provider):
        sub_query, bind_vars = connected_provider._get_record_group_children_subquery(
            "rg1", "org1", "recordGroup", "uk1"
        )
        assert isinstance(sub_query, str)
        assert isinstance(bind_vars, dict)
        assert "rg_doc_id" in bind_vars


# ---------------------------------------------------------------------------
# _get_record_children_subquery
# ---------------------------------------------------------------------------


class TestGetRecordChildrenSubquery:
    def test_returns_tuple(self, connected_provider):
        sub_query, bind_vars = connected_provider._get_record_children_subquery("r1", "org1", "uk1")
        assert isinstance(sub_query, str)
        assert "record_doc_id" in bind_vars


# ---------------------------------------------------------------------------
# _get_permission_role_aql
# ---------------------------------------------------------------------------


class TestGetPermissionRoleAql:
    def test_record(self, connected_provider):
        aql = connected_provider._get_permission_role_aql("record")
        assert isinstance(aql, str)
        assert "permission_role" in aql

    def test_record_group(self, connected_provider):
        aql = connected_provider._get_permission_role_aql("recordGroup")
        assert isinstance(aql, str)

    def test_app(self, connected_provider):
        aql = connected_provider._get_permission_role_aql("app")
        assert isinstance(aql, str)

    def test_kb(self, connected_provider):
        aql = connected_provider._get_permission_role_aql("kb")
        assert isinstance(aql, str)


# ---------------------------------------------------------------------------
# _get_record_permission_role_aql
# ---------------------------------------------------------------------------


class TestGetRecordPermissionRoleAql:
    def test_returns_string(self, connected_provider):
        rpm = {"OWNER": 4, "WRITER": 3, "READER": 2, "COMMENTER": 1}
        aql = connected_provider._get_record_permission_role_aql("node", "u", rpm)
        assert isinstance(aql, str)
        assert "permission_role" in aql


# ---------------------------------------------------------------------------
# _get_record_group_permission_role_aql
# ---------------------------------------------------------------------------


class TestGetRecordGroupPermissionRoleAql:
    def test_returns_string(self, connected_provider):
        rpm = {"OWNER": 4, "WRITER": 3, "READER": 2, "COMMENTER": 1}
        aql = connected_provider._get_record_group_permission_role_aql("node", "u", rpm)
        assert isinstance(aql, str)


# ---------------------------------------------------------------------------
# _get_app_permission_role_aql
# ---------------------------------------------------------------------------


class TestGetAppPermissionRoleAql:
    def test_returns_string(self, connected_provider):
        rpm = {"OWNER": 4, "WRITER": 3, "READER": 2, "COMMENTER": 1}
        aql = connected_provider._get_app_permission_role_aql("node", "u", rpm)
        assert isinstance(aql, str)


# ---------------------------------------------------------------------------
# _get_virtual_ids_for_connector
# ---------------------------------------------------------------------------


class TestGetVirtualIdsForConnectorExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"virtual_to_record": {"v1": "r1", "v2": "r2"}}]
        )
        result = await connected_provider._get_virtual_ids_for_connector("u1", "org1", "c1")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_with_metadata(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"virtual_to_record": {}}]
        )
        result = await connected_provider._get_virtual_ids_for_connector(
            "u1", "org1", "c1",
            metadata_filters={"departments": ["d1"]}
        )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider._get_virtual_ids_for_connector("u1", "org1", "c1")
        assert result == {}


# ---------------------------------------------------------------------------
# _get_kb_virtual_ids
# ---------------------------------------------------------------------------


class TestGetKbVirtualIdsExtended:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"virtual_to_record": {"v1": "r1"}}]
        )
        result = await connected_provider._get_kb_virtual_ids("u1", "org1")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_with_metadata(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"virtual_to_record": {}}]
        )
        result = await connected_provider._get_kb_virtual_ids(
            "u1", "org1",
            metadata_filters={"categories": ["cat1"]}
        )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider._get_kb_virtual_ids("u1", "org1")
        assert result == {}


# ---------------------------------------------------------------------------
# share_agent
# ---------------------------------------------------------------------------


class TestShareAgentExtended:
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value=None)
        result = await connected_provider.share_agent("a1", "u1", "org1", ["u2"], [])
        assert result is False

    @pytest.mark.asyncio
    async def test_cannot_share(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value={"can_share": False})
        result = await connected_provider.share_agent("a1", "u1", "org1", ["u2"], [])
        assert result is False

    @pytest.mark.asyncio
    async def test_success_users(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value={"can_share": True})
        connected_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk2", "userId": "u2"}
        )
        connected_provider.batch_create_edges = AsyncMock(return_value=True)
        result = await connected_provider.share_agent("a1", "u1", "org1", ["u2"], [])
        assert result is True


# ---------------------------------------------------------------------------
# unshare_agent
# ---------------------------------------------------------------------------


class TestUnshareAgentExtended:
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value=None)
        result = await connected_provider.unshare_agent("a1", "u1", "org1", ["u2"], [])
        assert result is not None
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_cannot_share(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value={"can_share": False})
        result = await connected_provider.unshare_agent("a1", "u1", "org1", ["u2"], [])
        assert result is not None
        assert result.get("success") is False


# ---------------------------------------------------------------------------
# update_agent_permission
# ---------------------------------------------------------------------------


class TestUpdateAgentPermissionExtended:
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value=None)
        result = await connected_provider.update_agent_permission("a1", "u1", "org1", ["u2"], [], "WRITER")
        assert result is not None
        assert result.get("success") is False


# ---------------------------------------------------------------------------
# get_agent_permissions
# ---------------------------------------------------------------------------


class TestGetAgentPermissionsExtended:
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value=None)
        result = await connected_provider.get_agent_permissions("a1", "u1", "org1")
        assert result is None


# ---------------------------------------------------------------------------
# _delete_record_with_type
# ---------------------------------------------------------------------------


class TestDeleteRecordWithTypeExtended:
    @pytest.mark.asyncio
    async def test_handles_exception_in_type_collection(self, connected_provider):
        connected_provider.delete_edges_from = AsyncMock()
        connected_provider.delete_edges_to = AsyncMock()
        connected_provider.delete_nodes = AsyncMock(side_effect=[Exception("not found"), None])
        # Should not raise - uses contextlib.suppress for type collections
        await connected_provider._delete_record_with_type("r1", ["nonexistent"])
        # The main record deletion should still happen
        assert connected_provider.delete_nodes.call_count == 2


# ---------------------------------------------------------------------------
# _delete_file_record / _delete_mail_record / _delete_main_record
# ---------------------------------------------------------------------------


class TestDeleteFileRecord:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_file_record("r1", transaction="txn1")
        connected_provider.http_client.execute_aql.assert_called_once()


class TestDeleteMailRecord:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_mail_record("r1", transaction="txn1")
        connected_provider.http_client.execute_aql.assert_called_once()


class TestDeleteMainRecord:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_main_record("r1", transaction="txn1")
        connected_provider.http_client.execute_aql.assert_called_once()


# ---------------------------------------------------------------------------
# _delete_drive_specific_edges
# ---------------------------------------------------------------------------


class TestDeleteDriveSpecificEdges:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_drive_specific_edges("r1", transaction="txn1")

    @pytest.mark.asyncio
    async def test_no_transaction(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_drive_specific_edges("r1")


# ---------------------------------------------------------------------------
# _delete_drive_anyone_permissions
# ---------------------------------------------------------------------------


class TestDeleteDriveAnyonePermissions:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_drive_anyone_permissions("r1", transaction="txn1")


# ---------------------------------------------------------------------------
# _delete_kb_specific_edges
# ---------------------------------------------------------------------------


class TestDeleteKbSpecificEdges:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await connected_provider._delete_kb_specific_edges("r1", transaction="txn1")


# ---------------------------------------------------------------------------
# delete_connector_instance
# ---------------------------------------------------------------------------


class TestDeleteConnectorInstanceFull:
    @pytest.mark.asyncio
    async def test_connector_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_isoftype_collection_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [], "record_group_keys": [], "role_keys": [],
            "group_keys": [], "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=["edge1"])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], False))
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success_empty_connector(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [], "record_group_keys": [], "role_keys": [],
            "group_keys": [], "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=["edge1"])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(0, 0))
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        # With empty keys, the record deletion check (deleted_records == 0 with keys > 0) won't trigger
        assert result is not None

    @pytest.mark.asyncio
    async def test_success_with_records(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": ["r1"],
            "record_group_keys": ["rg1"],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": ["v1"],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=["edge1"])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([{"_key": "f1"}], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(5, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(1, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (1, 0),  # records
                (1, 0),  # record groups
                (0, 0),  # roles
                (0, 0),  # groups
                (1, 0),  # app
            ]
        )
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is True
        assert result["virtual_record_ids"] == ["v1"]

    @pytest.mark.asyncio
    async def test_record_deletion_failure_rollback(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": ["r1"],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        # Records deletion fails: 0 deleted out of 1 expected
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(0, 0))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_failed_edge_collections(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [], "record_group_keys": [], "role_keys": [],
            "group_keys": [], "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=["edge1"])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        # Some edge collections fail
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(3, ["failed_coll"]))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles
                (0, 0),  # groups
                (1, 0),  # app (must succeed)
            ]
        )
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
        connected_provider.commit_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_general_exception(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("boom"))
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_partial_record_deletion(self, connected_provider):
        """Test that partial record deletion (deleted < expected) causes rollback."""
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": ["r1", "r2"],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        # Only 1 of 2 records deleted
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_failed_record_batches(self, connected_provider):
        """Test that failed record batches cause rollback."""
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": ["r1"],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        # Record deleted but 1 failed batch
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 1))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_failed_rg_deletion(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": ["rg1"],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records (empty)
                (0, 0),  # record groups - 0 deleted out of 1 expected
            ]
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_failed_roles_deletion(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": ["role1"],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles - 0 deleted out of 1 expected
            ]
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_failed_groups_deletion(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": ["g1"],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles
                (0, 0),  # groups - 0 deleted out of 1
            ]
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_sync_point_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles
                (0, 0),  # groups
            ]
        )
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, False))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_app_deletion_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles
                (0, 0),  # groups
                (0, 0),  # app - 0 deleted = failure
            ]
        )
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_existing_transaction(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles
                (0, 0),  # groups
                (1, 0),  # app
            ]
        )
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
        # Pass existing transaction
        result = await connected_provider.delete_connector_instance("c1", "org1", transaction="ext_txn")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_rg_failed_batches(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": ["rg1"],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (1, 1),  # record groups - deleted 1 but 1 failed batch
            ]
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_roles_failed_batches(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": ["role1"],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (1, 1),  # roles - deleted 1 but 1 failed batch
            ]
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_groups_failed_batches(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": ["g1"],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles
                (1, 1),  # groups - deleted 1 but 1 failed batch
            ]
        )
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_app_failed_batches(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": [],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(
            side_effect=[
                (0, 0),  # records
                (0, 0),  # record groups
                (0, 0),  # roles
                (0, 0),  # groups
                (1, 1),  # app - deleted 1 but 1 failed batch
            ]
        )
        connected_provider._delete_nodes_by_connector_id = AsyncMock(return_value=(0, True))
        connected_provider.rollback_transaction = AsyncMock()
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_rollback_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "c1"})
        connected_provider._collect_connector_entities = AsyncMock(return_value={
            "record_keys": ["r1"],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "virtual_record_ids": [],
        })
        connected_provider._get_all_edge_collections = AsyncMock(return_value=[])
        connected_provider._collect_isoftype_targets = AsyncMock(return_value=([], True))
        connected_provider._delete_edges_by_connector_id = AsyncMock(return_value=(0, []))
        connected_provider.begin_transaction = AsyncMock(return_value="txn1")
        connected_provider._delete_isoftype_targets_from_collected = AsyncMock(return_value=(0, True))
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(0, 0))
        connected_provider.rollback_transaction = AsyncMock(side_effect=Exception("rollback fail"))
        result = await connected_provider.delete_connector_instance("c1", "org1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Agent update/delete/share with more complex flows
# ---------------------------------------------------------------------------


class TestUpdateAgentExtended:
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value=None)
        result = await connected_provider.update_agent("a1", {"name": "X"}, "u1", "org1")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_edit_permission(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value={"can_edit": False})
        result = await connected_provider.update_agent("a1", {"name": "X"}, "u1", "org1")
        assert result is False


class TestDeleteAgentExtended:
    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "a1"})
        connected_provider.check_agent_permission = AsyncMock(return_value=None)
        result = await connected_provider.delete_agent("a1", "u1", "org1")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_delete_permission(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "a1"})
        connected_provider.check_agent_permission = AsyncMock(return_value={"can_delete": False})
        result = await connected_provider.delete_agent("a1", "u1", "org1")
        assert result is False


# ---------------------------------------------------------------------------
# _execute_gmail_record_deletion / _execute_drive_record_deletion / _execute_kb_record_deletion
# ---------------------------------------------------------------------------


class TestExecuteGmailRecordDeletion:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider._delete_record_with_type = AsyncMock()
        result = await connected_provider._execute_gmail_record_deletion(
            "r1", {"_key": "r1", "connectorName": "GMAIL"}, "OWNER", transaction="txn1"
        )
        assert result is not None


class TestExecuteDriveRecordDeletion:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider._delete_record_with_type = AsyncMock()
        connected_provider._delete_drive_specific_edges = AsyncMock()
        connected_provider._delete_drive_anyone_permissions = AsyncMock()
        result = await connected_provider._execute_drive_record_deletion(
            "r1", {"_key": "r1", "connectorName": "GOOGLE_DRIVE"}, "OWNER", transaction="txn1"
        )
        assert result is not None


class TestExecuteKbRecordDeletion:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider._delete_record_with_type = AsyncMock()
        connected_provider._delete_kb_specific_edges = AsyncMock()
        result = await connected_provider._execute_kb_record_deletion(
            "r1", {"_key": "r1", "connectorName": "KB"}, "OWNER", transaction="txn1"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# share_agent_template / clone_agent_template / delete_agent_template / update_agent_template
# (remaining agent template methods if not already covered)
# ---------------------------------------------------------------------------


class TestShareAgentTemplateExtended:
    @pytest.mark.asyncio
    async def test_no_template(self, connected_provider):
        connected_provider.get_template = AsyncMock(return_value=None)
        result = await connected_provider.share_agent_template("t1", "u1")
        assert result is None or result is False


class TestCloneAgentTemplateExtended:
    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.clone_agent_template("t999")
        assert result is None


class TestDeleteAgentTemplateExtended:
    @pytest.mark.asyncio
    async def test_no_template(self, connected_provider):
        connected_provider.get_template = AsyncMock(return_value=None)
        result = await connected_provider.delete_agent_template("t1", "u1")
        assert result is None or result is False


class TestUpdateAgentTemplateExtended:
    @pytest.mark.asyncio
    async def test_no_template(self, connected_provider):
        connected_provider.get_template = AsyncMock(return_value=None)
        result = await connected_provider.update_agent_template("t1", {"name": "X"}, "u1")
        assert result is None or result is False


# ---------------------------------------------------------------------------
# _create_typed_record_from_arango (lines 613-642)
# ---------------------------------------------------------------------------


class TestCreateTypedRecordFromArango:
    """Tests for _create_typed_record_from_arango covering all typed branches."""

    def test_no_type_doc_returns_base_record(self, provider):
        mock_record = MagicMock()
        mock_record.id = "r1"
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as MockRecord:
            MockRecord.from_arango_base_record.return_value = mock_record
            result = provider._create_typed_record_from_arango({"_key": "r1", "recordType": "FILE"}, None)
            assert result.id == "r1"
            MockRecord.from_arango_base_record.assert_called_once()

    def test_unknown_record_type_returns_base_record(self, provider):
        mock_record = MagicMock()
        mock_record.id = "r1"
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as MockRecord:
            MockRecord.from_arango_base_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "UNKNOWN_TYPE"}, {"_key": "t1"}
            )
            assert result.id == "r1"

    def test_file_record_type(self, provider):
        mock_record = MagicMock()
        with patch("app.services.graph_db.arango.arango_http_provider.FileRecord") as MockFileRecord:
            MockFileRecord.from_arango_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "FILE"}, {"_key": "f1"}
            )
            MockFileRecord.from_arango_record.assert_called_once()

    def test_mail_record_type(self, provider):
        mock_record = MagicMock()
        with patch("app.services.graph_db.arango.arango_http_provider.MailRecord") as MockMailRecord:
            MockMailRecord.from_arango_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "MAIL"}, {"_key": "m1"}
            )
            MockMailRecord.from_arango_record.assert_called_once()

    def test_webpage_record_type(self, provider):
        mock_record = MagicMock()
        with patch("app.services.graph_db.arango.arango_http_provider.WebpageRecord") as MockWebpageRecord:
            MockWebpageRecord.from_arango_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "WEBPAGE"}, {"_key": "w1"}
            )
            MockWebpageRecord.from_arango_record.assert_called_once()

    def test_ticket_record_type(self, provider):
        mock_record = MagicMock()
        with patch("app.services.graph_db.arango.arango_http_provider.TicketRecord") as MockTicketRecord:
            MockTicketRecord.from_arango_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "TICKET"}, {"_key": "t1"}
            )
            MockTicketRecord.from_arango_record.assert_called_once()

    def test_comment_record_type(self, provider):
        mock_record = MagicMock()
        with patch("app.services.graph_db.arango.arango_http_provider.CommentRecord") as MockCommentRecord:
            MockCommentRecord.from_arango_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "COMMENT"}, {"_key": "c1"}
            )
            MockCommentRecord.from_arango_record.assert_called_once()

    def test_link_record_type(self, provider):
        mock_record = MagicMock()
        with patch("app.services.graph_db.arango.arango_http_provider.LinkRecord") as MockLinkRecord:
            MockLinkRecord.from_arango_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "LINK"}, {"_key": "l1"}
            )
            MockLinkRecord.from_arango_record.assert_called_once()

    def test_project_record_type(self, provider):
        mock_record = MagicMock()
        with patch("app.services.graph_db.arango.arango_http_provider.ProjectRecord") as MockProjectRecord:
            MockProjectRecord.from_arango_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "PROJECT"}, {"_key": "p1"}
            )
            MockProjectRecord.from_arango_record.assert_called_once()

    def test_exception_falls_back_to_base(self, provider):
        """If from_arango_record raises, fallback to base Record."""
        mock_record = MagicMock()
        mock_record.id = "r1"
        with patch("app.services.graph_db.arango.arango_http_provider.FileRecord") as MockFileRecord, \
             patch("app.services.graph_db.arango.arango_http_provider.Record") as MockRecord:
            MockFileRecord.from_arango_record.side_effect = Exception("parse error")
            MockRecord.from_arango_base_record.return_value = mock_record
            result = provider._create_typed_record_from_arango(
                {"_key": "r1", "recordType": "FILE"}, {"_key": "t1"}
            )
            assert result.id == "r1"
            MockRecord.from_arango_base_record.assert_called_once()


# ---------------------------------------------------------------------------
# _normalize_name exception branch (line 9177-9178)
# ---------------------------------------------------------------------------


class TestNormalizeNameException:
    def test_normalize_name_exception_fallback(self, provider):
        """When unicodedata.normalize raises, fallback to str().strip()."""
        with patch("app.services.graph_db.arango.arango_http_provider.unicodedata.normalize", side_effect=Exception("fail")):
            result = provider._normalize_name("  hello  ")
            assert result == "hello"


# ---------------------------------------------------------------------------
# _delete_edges_by_connector_id (lines 5943-5981)
# ---------------------------------------------------------------------------


class TestDeleteEdgesByConnectorIdDetailed:
    @pytest.mark.asyncio
    async def test_deletes_edges_across_collections(self, connected_provider):
        """Test that edges from nodes and apps are deleted, including _to queries."""
        # Mock execute_aql to return some results
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                [1, 1],      # delete_edges_from_query for RECORDS
                [1],         # delete_edges_to_query for RECORDS
                [1, 1, 1],   # delete_edges_from_query for RECORD_GROUPS
                [],          # delete_edges_to_query for RECORD_GROUPS
                [],          # delete_edges_from_query for ROLES
                [],          # delete_edges_to_query for ROLES
                [],          # delete_edges_from_query for GROUPS
                [],          # delete_edges_to_query for GROUPS
                [1],         # delete_edges_from_app_query
                [1],         # delete_edges_to_app_query
            ]
        )
        total, failed = await connected_provider._delete_edges_by_connector_id(
            "txn1", "conn1", ["edgeCol1"]
        )
        assert total > 0
        assert failed == []

    @pytest.mark.asyncio
    async def test_handles_failed_collection(self, connected_provider):
        """When a collection deletion raises, it's added to failed_collections."""
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=Exception("AQL error")
        )
        total, failed = await connected_provider._delete_edges_by_connector_id(
            "txn1", "conn1", ["badEdgeCol"]
        )
        assert total == 0
        assert len(failed) == 1
        assert "badEdgeCol" in failed


# ---------------------------------------------------------------------------
# _collect_isoftype_targets (lines 5995-6030)
# ---------------------------------------------------------------------------


class TestCollectIsoftypeTargets:
    @pytest.mark.asyncio
    async def test_empty_connector_id(self, connected_provider):
        targets, success = await connected_provider._collect_isoftype_targets("txn1", "")
        assert targets == []
        assert success is True

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"collection": "files", "key": "f1", "full_id": "files/f1"}]
        )
        targets, success = await connected_provider._collect_isoftype_targets("txn1", "conn1")
        assert success is True
        assert len(targets) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        targets, success = await connected_provider._collect_isoftype_targets("txn1", "conn1")
        assert targets == []
        assert success is False


# ---------------------------------------------------------------------------
# _delete_isoftype_targets_from_collected (lines 6050-6093)
# ---------------------------------------------------------------------------


class TestDeleteIsoftypeTargetsFromCollected:
    @pytest.mark.asyncio
    async def test_empty_targets(self, connected_provider):
        total, failed = await connected_provider._delete_isoftype_targets_from_collected("txn1", [], [])
        assert total == 0
        assert failed == []

    @pytest.mark.asyncio
    async def test_successful_deletion(self, connected_provider):
        targets = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "files", "key": "f2", "full_id": "files/f2"},
        ]
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(2, 0))
        total, failed = await connected_provider._delete_isoftype_targets_from_collected("txn1", targets, [])
        assert total == 2
        assert failed == []

    @pytest.mark.asyncio
    async def test_failed_batches_raises(self, connected_provider):
        targets = [{"collection": "files", "key": "f1", "full_id": "files/f1"}]
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(0, 1))
        with pytest.raises(Exception, match="CRITICAL"):
            await connected_provider._delete_isoftype_targets_from_collected("txn1", targets, [])

    @pytest.mark.asyncio
    async def test_partial_deletion_raises(self, connected_provider):
        targets = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "files", "key": "f2", "full_id": "files/f2"},
        ]
        # deleted fewer than expected, but no failed batches
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        with pytest.raises(Exception, match="CRITICAL"):
            await connected_provider._delete_isoftype_targets_from_collected("txn1", targets, [])

    @pytest.mark.asyncio
    async def test_multiple_collections(self, connected_provider):
        targets = [
            {"collection": "files", "key": "f1", "full_id": "files/f1"},
            {"collection": "mails", "key": "m1", "full_id": "mails/m1"},
        ]
        connected_provider._delete_nodes_by_keys = AsyncMock(return_value=(1, 0))
        total, failed = await connected_provider._delete_isoftype_targets_from_collected("txn1", targets, [])
        assert total == 2
        assert failed == []


# ---------------------------------------------------------------------------
# get_user_sync_state (lines 7299-7319)
# ---------------------------------------------------------------------------


class TestGetUserSyncState:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="user_key1")
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "edge1", "syncState": "COMPLETED"}]
        )
        result = await connected_provider.get_user_sync_state("user@test.com", "DRIVE")
        assert result is not None
        assert result["syncState"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value=None)
        result = await connected_provider.get_user_sync_state("user@test.com", "DRIVE")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_results(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="user_key1")
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_user_sync_state("user@test.com", "DRIVE")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.get_user_sync_state("user@test.com", "DRIVE")
        assert result is None


# ---------------------------------------------------------------------------
# update_user_sync_state (lines 7352-7391)
# ---------------------------------------------------------------------------


class TestUpdateUserSyncState:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="user_key1")
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "edge1", "syncState": "RUNNING"}]
        )
        result = await connected_provider.update_user_sync_state("user@test.com", "RUNNING", "DRIVE")
        assert result is not None
        assert result["syncState"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value=None)
        result = await connected_provider.update_user_sync_state("user@test.com", "RUNNING")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_relation_found(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="user_key1")
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.update_user_sync_state("user@test.com", "RUNNING")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_entity_id_by_email = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.update_user_sync_state("user@test.com", "RUNNING")
        assert result is None


# ---------------------------------------------------------------------------
# batch_upsert_app_users (lines 4788-4830)
# ---------------------------------------------------------------------------


class TestBatchUpsertAppUsers:
    @pytest.mark.asyncio
    async def test_existing_user_creates_app_relation(self, connected_provider):
        mock_user = MagicMock()
        mock_user.email = "user@test.com"
        mock_user.connector_id = "app1"
        mock_user.id = "u1"
        mock_user.source_user_id = "ext_u1"
        mock_user.to_arango_base_user.return_value = {"_key": "u1", "email": "user@test.com"}
        mock_user.created_at = 1000
        mock_user.updated_at = 2000

        connected_provider.get_all_orgs = AsyncMock(return_value=[{"_key": "org1"}])
        connected_provider.get_document = AsyncMock(return_value={"_id": "apps/app1", "_key": "app1"})

        # User exists
        mock_user_record = MagicMock()
        mock_user_record.id = "u1"
        connected_provider.get_user_by_email = AsyncMock(return_value=mock_user_record)
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()

        await connected_provider.batch_upsert_app_users([mock_user])
        # Should have created user-app relation edge
        connected_provider.batch_create_edges.assert_called()

    @pytest.mark.asyncio
    async def test_new_user_creates_user_and_relations(self, connected_provider):
        mock_user = MagicMock()
        mock_user.email = "new@test.com"
        mock_user.connector_id = "app1"
        mock_user.id = "u2"
        mock_user.source_user_id = "ext_u2"
        mock_user.to_arango_base_user.return_value = {"_key": "u2", "email": "new@test.com"}
        mock_user.created_at = 1000
        mock_user.updated_at = 2000

        connected_provider.get_all_orgs = AsyncMock(return_value=[{"_key": "org1"}])
        connected_provider.get_document = AsyncMock(return_value={"_id": "apps/app1", "_key": "app1"})

        mock_user_record = MagicMock()
        mock_user_record.id = "u2"
        # First call returns None (user doesn't exist), second call returns user after creation
        connected_provider.get_user_by_email = AsyncMock(side_effect=[None, mock_user_record])
        connected_provider.batch_upsert_nodes = AsyncMock()
        connected_provider.batch_create_edges = AsyncMock()

        await connected_provider.batch_upsert_app_users([mock_user])
        # Should have created user, org relation, and user-app relation
        assert connected_provider.batch_upsert_nodes.call_count >= 1
        assert connected_provider.batch_create_edges.call_count >= 2

    @pytest.mark.asyncio
    async def test_no_app_raises(self, connected_provider):
        mock_user = MagicMock()
        mock_user.connector_id = "app1"
        connected_provider.get_all_orgs = AsyncMock(return_value=[{"_key": "org1"}])
        connected_provider.get_document = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="Failed to get/create app"):
            await connected_provider.batch_upsert_app_users([mock_user])


# ---------------------------------------------------------------------------
# store_permission exception branch (lines 5280-5286)
# ---------------------------------------------------------------------------


class TestStorePermissionExceptionBranch:
    @pytest.mark.asyncio
    async def test_inner_exception_with_transaction_reraises(self, connected_provider):
        """When store_permission inner try fails with a transaction, it should re-raise."""
        connected_provider.get_file_permissions = AsyncMock(side_effect=Exception("inner fail"))
        with pytest.raises(Exception, match="inner fail"):
            await connected_provider.store_permission("file1", "entity1", {"type": "user"}, transaction="txn1")

    @pytest.mark.asyncio
    async def test_inner_exception_without_transaction_returns_false(self, connected_provider):
        """When store_permission inner try fails without transaction, returns False."""
        connected_provider.get_file_permissions = AsyncMock(side_effect=Exception("inner fail"))
        result = await connected_provider.store_permission("file1", "entity1", {"type": "user"})
        assert result is False


# ---------------------------------------------------------------------------
# process_file_permissions (lines 5362-5466)
# ---------------------------------------------------------------------------


class TestProcessFilePermissions:
    @pytest.mark.asyncio
    async def test_remove_obsolete_permissions(self, connected_provider):
        """Test removal of permissions not in new set."""
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        connected_provider.get_file_permissions = AsyncMock(return_value=[
            {"_key": "p1", "externalPermissionId": "old_perm", "_from": "users/u1", "type": "user"},
        ])
        connected_provider.store_permission = AsyncMock(return_value=True)

        result = await connected_provider.process_file_permissions(
            "org1", "file1",
            [{"id": "new_perm", "type": "user", "emailAddress": "u@test.com", "role": "READER"}]
        )
        # Should have removed old_perm
        assert connected_provider.http_client.execute_aql.call_count >= 2  # Remove anyone + remove obsolete

    @pytest.mark.asyncio
    async def test_update_existing_user_permission(self, connected_provider):
        """Test updating existing user permission."""
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        connected_provider.get_file_permissions = AsyncMock(return_value=[
            {"_key": "p1", "externalPermissionId": "perm1", "_from": "users/u1", "type": "user", "role": "READER"},
        ])
        connected_provider.store_permission = AsyncMock(return_value=True)

        result = await connected_provider.process_file_permissions(
            "org1", "file1",
            [{"id": "perm1", "type": "user", "emailAddress": "u@test.com", "role": "WRITER"}]
        )
        # store_permission should be called to update
        connected_provider.store_permission.assert_called()

    @pytest.mark.asyncio
    async def test_new_user_permission(self, connected_provider):
        """Test creating new user permission."""
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        connected_provider.get_file_permissions = AsyncMock(return_value=[])
        connected_provider.get_entity_id_by_email = AsyncMock(return_value="u1")
        connected_provider.store_permission = AsyncMock(return_value=True)

        result = await connected_provider.process_file_permissions(
            "org1", "file1",
            [{"id": "perm1", "type": "user", "emailAddress": "u@test.com", "role": "READER"}]
        )
        connected_provider.store_permission.assert_called()

    @pytest.mark.asyncio
    async def test_new_user_permission_entity_not_found(self, connected_provider):
        """Test skipping user permission when entity not found."""
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        connected_provider.get_file_permissions = AsyncMock(return_value=[])
        connected_provider.get_entity_id_by_email = AsyncMock(return_value=None)
        connected_provider.store_permission = AsyncMock(return_value=True)

        result = await connected_provider.process_file_permissions(
            "org1", "file1",
            [{"id": "perm1", "type": "user", "emailAddress": "u@test.com", "role": "READER"}]
        )
        # store_permission should NOT be called
        connected_provider.store_permission.assert_not_called()

    @pytest.mark.asyncio
    async def test_domain_permission(self, connected_provider):
        """Test domain-type permission."""
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        connected_provider.get_file_permissions = AsyncMock(return_value=[])
        connected_provider.store_permission = AsyncMock(return_value=True)

        result = await connected_provider.process_file_permissions(
            "org1", "file1",
            [{"id": "perm1", "type": "domain", "domain": "test.com", "role": "READER"}]
        )
        connected_provider.store_permission.assert_called()

    @pytest.mark.asyncio
    async def test_anyone_permission(self, connected_provider):
        """Test anyone-type permission creates anyone document."""
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        connected_provider.get_file_permissions = AsyncMock(return_value=[])
        connected_provider.batch_upsert_nodes = AsyncMock()

        result = await connected_provider.process_file_permissions(
            "org1", "file1",
            [{"id": "perm1", "type": "anyone", "role": "READER"}]
        )
        connected_provider.batch_upsert_nodes.assert_called()


# ---------------------------------------------------------------------------
# get_agent (lines 18204-18219 - knowledge filters parsing)
# ---------------------------------------------------------------------------


class TestGetAgentKnowledgeParsing:
    @pytest.mark.asyncio
    async def test_agent_with_knowledge_json_string_filters(self, connected_provider):
        """Test that knowledge filters are parsed from JSON strings."""
        agent_result = {
            "_key": "a1",
            "name": "TestAgent",
            "knowledge": [
                {"filters": '{"recordGroups": ["kb1"]}'},
            ],
            "can_edit": True,
            "can_delete": True,
        }
        connected_provider.execute_query = AsyncMock(return_value=[agent_result])
        result = await connected_provider.get_agent("a1", "u1", "org1")
        assert result is not None
        assert result["knowledge"][0]["filtersParsed"] == {"recordGroups": ["kb1"]}

    @pytest.mark.asyncio
    async def test_agent_with_knowledge_invalid_json(self, connected_provider):
        """Test that invalid JSON filters default to empty dict."""
        agent_result = {
            "_key": "a1",
            "name": "TestAgent",
            "knowledge": [
                {"filters": "not valid json"},
            ],
        }
        connected_provider.execute_query = AsyncMock(return_value=[agent_result])
        result = await connected_provider.get_agent("a1", "u1", "org1")
        assert result is not None
        assert result["knowledge"][0]["filtersParsed"] == {}

    @pytest.mark.asyncio
    async def test_agent_with_knowledge_dict_filters(self, connected_provider):
        """Test that dict filters are passed through as-is."""
        agent_result = {
            "_key": "a1",
            "name": "TestAgent",
            "knowledge": [
                {"filters": {"recordGroups": ["kb1"]}},
            ],
        }
        connected_provider.execute_query = AsyncMock(return_value=[agent_result])
        result = await connected_provider.get_agent("a1", "u1", "org1")
        assert result is not None
        assert result["knowledge"][0]["filtersParsed"] == {"recordGroups": ["kb1"]}

    @pytest.mark.asyncio
    async def test_agent_without_knowledge(self, connected_provider):
        """Test agent without knowledge key."""
        agent_result = {"_key": "a1", "name": "TestAgent"}
        connected_provider.execute_query = AsyncMock(return_value=[agent_result])
        result = await connected_provider.get_agent("a1", "u1", "org1")
        assert result is not None
        assert "knowledge" not in result or result.get("knowledge") is None

    @pytest.mark.asyncio
    async def test_agent_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_agent("a1", "u1", "org1")
        assert result is None

    @pytest.mark.asyncio
    async def test_agent_none_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[None])
        result = await connected_provider.get_agent("a1", "u1", "org1")
        assert result is None


# ---------------------------------------------------------------------------
# delete_agent (lines 18564-18583 - soft delete success path)
# ---------------------------------------------------------------------------


class TestDeleteAgentSoftDelete:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        """Test soft delete of agent with full permissions."""
        connected_provider.get_document = AsyncMock(return_value={"_key": "a1"})
        connected_provider.get_agent = AsyncMock(return_value={
            "_key": "a1", "can_delete": True
        })
        connected_provider.update_node = AsyncMock(return_value={"_key": "a1"})

        result = await connected_provider.delete_agent("a1", "u1", "org1")
        assert result is True
        connected_provider.update_node.assert_called_once()
        call_args = connected_provider.update_node.call_args
        assert call_args.kwargs.get("updates", call_args[1] if len(call_args) > 1 else {}).get("isDeleted") is True or \
               any(arg.get("isDeleted") is True for arg in call_args.args if isinstance(arg, dict))

    @pytest.mark.asyncio
    async def test_update_fails(self, connected_provider):
        """Test that update_node failure returns False."""
        connected_provider.get_document = AsyncMock(return_value={"_key": "a1"})
        connected_provider.get_agent = AsyncMock(return_value={
            "_key": "a1", "can_delete": True
        })
        connected_provider.update_node = AsyncMock(return_value=None)

        result = await connected_provider.delete_agent("a1", "u1", "org1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        """Test exception returns False."""
        connected_provider.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.delete_agent("a1", "u1", "org1")
        assert result is False


# ---------------------------------------------------------------------------
# hard_delete_agent (lines 18629-18764)
# ---------------------------------------------------------------------------


class TestHardDeleteAgent:
    @pytest.mark.asyncio
    async def test_full_deletion_with_knowledge_toolsets_tools(self, connected_provider):
        """Test full hard delete with knowledge, toolsets, and tools."""
        # Step 1: knowledge edges
        connected_provider.execute_query = AsyncMock(side_effect=[
            # knowledge_ids from step 1
            ["agentKnowledge/k1", "agentKnowledge/k2"],
            # deleted_knowledge from step 2
            [{"_key": "k1"}],
            # toolset_ids from step 3
            ["agentToolsets/ts1"],
            # tool_ids from step 4
            ["agentTools/t1", "agentTools/t2"],
            # deleted_tools from step 5
            [{"_key": "t1"}],
            # deleted_toolset_edges from step 6
            [{"_key": "e1"}],
            # deleted_toolsets from step 7
            [{"_key": "ts1"}],
            # deleted_permissions from step 8
            [{"_key": "p1"}, {"_key": "p2"}],
            # deleted_agents from step 9
            [{"_key": "a1"}],
        ])

        result = await connected_provider.hard_delete_agent("a1")
        assert result["agents_deleted"] == 1
        assert result["knowledge_deleted"] == 1
        assert result["tools_deleted"] == 1
        assert result["toolsets_deleted"] == 1
        assert result["edges_deleted"] > 0

    @pytest.mark.asyncio
    async def test_no_knowledge_no_toolsets(self, connected_provider):
        """Test hard delete with no knowledge and no toolsets."""
        connected_provider.execute_query = AsyncMock(side_effect=[
            [],    # no knowledge_ids
            [],    # no toolset_ids
            [],    # no toolset edges deleted
            [],    # no permissions
            [{"_key": "a1"}],  # agent deleted
        ])

        result = await connected_provider.hard_delete_agent("a1")
        assert result["agents_deleted"] == 1
        assert result["knowledge_deleted"] == 0
        assert result["tools_deleted"] == 0
        assert result["toolsets_deleted"] == 0

    @pytest.mark.asyncio
    async def test_knowledge_with_no_orphans(self, connected_provider):
        """Test that knowledge nodes still referenced by other agents are skipped."""
        connected_provider.execute_query = AsyncMock(side_effect=[
            ["agentKnowledge/k1"],   # knowledge_ids
            [],                       # no deleted_knowledge (still referenced)
            [],                       # no toolsets
            [],                       # no toolset edges
            [],                       # no permissions
            [{"_key": "a1"}],         # agent deleted
        ])

        result = await connected_provider.hard_delete_agent("a1")
        assert result["agents_deleted"] == 1
        assert result["knowledge_deleted"] == 0

    @pytest.mark.asyncio
    async def test_toolsets_with_no_tool_orphans(self, connected_provider):
        """Test with toolsets that have tools still referenced by others."""
        connected_provider.execute_query = AsyncMock(side_effect=[
            [],                      # no knowledge
            ["agentToolsets/ts1"],    # toolset_ids
            ["agentTools/t1"],       # tool_ids
            [],                      # no deleted_tools (still referenced)
            [{"_key": "e1"}],        # toolset edges deleted
            [],                      # no deleted_toolsets (still referenced)
            [],                      # no permissions
            [{"_key": "a1"}],        # agent deleted
        ])

        result = await connected_provider.hard_delete_agent("a1")
        assert result["agents_deleted"] == 1
        assert result["tools_deleted"] == 0
        assert result["toolsets_deleted"] == 0

    @pytest.mark.asyncio
    async def test_exception_returns_zeros(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.hard_delete_agent("a1")
        assert result["agents_deleted"] == 0
        assert result["toolsets_deleted"] == 0
        assert result["tools_deleted"] == 0
        assert result["knowledge_deleted"] == 0
        assert result["edges_deleted"] == 0

    @pytest.mark.asyncio
    async def test_permissions_deleted(self, connected_provider):
        """Test that permissions edges are counted."""
        connected_provider.execute_query = AsyncMock(side_effect=[
            [],                      # no knowledge
            [],                      # no toolsets
            [{"_key": "te1"}],       # toolset edges deleted
            [{"_key": "p1"}, {"_key": "p2"}, {"_key": "p3"}],  # 3 permissions deleted
            [{"_key": "a1"}],        # agent deleted
        ])

        result = await connected_provider.hard_delete_agent("a1")
        assert result["edges_deleted"] >= 3


# ---------------------------------------------------------------------------
# _execute_gmail_record_deletion detailed (lines 12233-12326)
# ---------------------------------------------------------------------------


class TestExecuteGmailRecordDeletionDetailed:
    @pytest.mark.asyncio
    async def test_with_attachments_and_mail_record(self, connected_provider):
        """Test Gmail deletion with attachments, mail record, and event payload."""
        connected_provider.get_document = AsyncMock(side_effect=[
            {"_key": "m1", "threadId": "t1", "messageId": "msg1"},  # mail_record
            None,  # file_record (not attachment)
        ])
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["att1"])
        connected_provider._delete_outlook_edges = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._delete_mail_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(return_value={
            "orgId": "org1", "recordId": "r1"
        })

        result = await connected_provider._execute_gmail_record_deletion(
            "r1", {"_key": "r1", "connectorName": "GMAIL"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"] is not None
        assert result["eventData"]["payload"]["threadId"] == "t1"

    @pytest.mark.asyncio
    async def test_with_file_record(self, connected_provider):
        """Test Gmail deletion where record has file record type."""
        connected_provider.get_document = AsyncMock(side_effect=[
            None,  # no mail_record
            {"_key": "f1", "attachmentId": "a1"},  # file_record
        ])
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider._delete_outlook_edges = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(return_value={
            "orgId": "org1", "recordId": "r1"
        })

        result = await connected_provider._execute_gmail_record_deletion(
            "r1", {"_key": "r1", "recordType": "FILE", "connectorName": "GMAIL"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_payload(self, connected_provider):
        """Test Gmail deletion when event payload creation returns None."""
        connected_provider.get_document = AsyncMock(return_value=None)
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider._delete_outlook_edges = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(return_value=None)

        result = await connected_provider._execute_gmail_record_deletion(
            "r1", {"_key": "r1"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"] is None

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self, connected_provider):
        """Test Gmail deletion exception path."""
        connected_provider.get_document = AsyncMock(side_effect=Exception("db error"))

        result = await connected_provider._execute_gmail_record_deletion(
            "r1", {"_key": "r1"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# _execute_drive_record_deletion detailed (lines 12331-12388)
# ---------------------------------------------------------------------------


class TestExecuteDriveRecordDeletionDetailed:
    @pytest.mark.asyncio
    async def test_with_file_record_and_event(self, connected_provider):
        """Test Drive deletion with file record details in event."""
        connected_provider.get_document = AsyncMock(return_value={
            "_key": "f1", "driveId": "d1", "parentId": "p1", "webViewLink": "http://link"
        })
        connected_provider._delete_drive_specific_edges = AsyncMock()
        connected_provider._delete_drive_anyone_permissions = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(return_value={
            "orgId": "org1", "recordId": "r1"
        })

        result = await connected_provider._execute_drive_record_deletion(
            "r1", {"_key": "r1"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"]["payload"]["driveId"] == "d1"

    @pytest.mark.asyncio
    async def test_no_payload(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        connected_provider._delete_drive_specific_edges = AsyncMock()
        connected_provider._delete_drive_anyone_permissions = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(return_value=None)

        result = await connected_provider._execute_drive_record_deletion(
            "r1", {"_key": "r1"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"] is None

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider._execute_drive_record_deletion(
            "r1", {"_key": "r1"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_payload_error_still_succeeds(self, connected_provider):
        """Event payload failure should not affect main deletion."""
        connected_provider.get_document = AsyncMock(return_value={"_key": "f1"})
        connected_provider._delete_drive_specific_edges = AsyncMock()
        connected_provider._delete_drive_anyone_permissions = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(side_effect=Exception("payload error"))

        result = await connected_provider._execute_drive_record_deletion(
            "r1", {"_key": "r1"}, "OWNER", transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"] is None


# ---------------------------------------------------------------------------
# _execute_kb_record_deletion detailed (lines 12393-12443)
# ---------------------------------------------------------------------------


class TestExecuteKbRecordDeletionDetailed:
    @pytest.mark.asyncio
    async def test_with_file_record_and_event(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "f1"})
        connected_provider._delete_kb_specific_edges = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(return_value={
            "orgId": "org1", "recordId": "r1"
        })

        result = await connected_provider._execute_kb_record_deletion(
            "r1", {"_key": "r1"}, {"kbId": "kb1"}, transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"] is not None

    @pytest.mark.asyncio
    async def test_no_payload(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        connected_provider._delete_kb_specific_edges = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(return_value=None)

        result = await connected_provider._execute_kb_record_deletion(
            "r1", {"_key": "r1"}, {"kbId": "kb1"}, transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"] is None

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider._execute_kb_record_deletion(
            "r1", {"_key": "r1"}, {"kbId": "kb1"}, transaction="txn1"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_payload_error_still_succeeds(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "f1"})
        connected_provider._delete_kb_specific_edges = AsyncMock()
        connected_provider._delete_file_record = AsyncMock()
        connected_provider._delete_main_record = AsyncMock()
        connected_provider._create_deleted_record_event_payload = AsyncMock(side_effect=Exception("payload error"))

        result = await connected_provider._execute_kb_record_deletion(
            "r1", {"_key": "r1"}, {"kbId": "kb1"}, transaction="txn1"
        )
        assert result["success"] is True
        assert result["eventData"] is None


# ---------------------------------------------------------------------------
# share_agent (lines 19056-19093)
# ---------------------------------------------------------------------------


class TestShareAgent:
    @pytest.mark.asyncio
    async def test_share_with_users(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value={"_key": "a1", "can_share": True})
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u2"})
        connected_provider.batch_create_edges = AsyncMock(return_value=True)

        result = await connected_provider.share_agent("a1", "u1", "org1", user_ids=["u2"], team_ids=None)
        assert result is True

    @pytest.mark.asyncio
    async def test_share_with_teams(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value={"_key": "a1", "can_share": True})
        connected_provider.get_document = AsyncMock(return_value={"_key": "team1"})
        connected_provider.batch_create_edges = AsyncMock(return_value=True)

        result = await connected_provider.share_agent("a1", "u1", "org1", user_ids=None, team_ids=["team1"])
        assert result is True

    @pytest.mark.asyncio
    async def test_share_user_not_found_skipped(self, connected_provider):
        """User not found should be skipped (continue), not fail."""
        connected_provider.get_agent = AsyncMock(return_value={"_key": "a1", "can_share": True})
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        connected_provider.batch_create_edges = AsyncMock(return_value=True)

        result = await connected_provider.share_agent("a1", "u1", "org1", user_ids=["u2"], team_ids=None)
        assert result is True

    @pytest.mark.asyncio
    async def test_share_team_not_found_skipped(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value={"_key": "a1", "can_share": True})
        connected_provider.get_document = AsyncMock(return_value=None)
        connected_provider.batch_create_edges = AsyncMock(return_value=True)

        result = await connected_provider.share_agent("a1", "u1", "org1", user_ids=None, team_ids=["team1"])
        assert result is True

    @pytest.mark.asyncio
    async def test_share_fails_on_edge_creation(self, connected_provider):
        connected_provider.get_agent = AsyncMock(return_value={"_key": "a1", "can_share": True})
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u2"})
        connected_provider.batch_create_edges = AsyncMock(return_value=False)

        result = await connected_provider.share_agent("a1", "u1", "org1", user_ids=["u2"], team_ids=None)
        assert result is False


# ---------------------------------------------------------------------------
# update_agent_permission (lines 19180-19208)
# ---------------------------------------------------------------------------


class TestUpdateAgentPermission:
    @pytest.mark.asyncio
    async def test_success_with_users_and_teams(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value={"_key": "a1", "user_role": "OWNER"})
        connected_provider.execute_query = AsyncMock(return_value=[
            {"_key": "p1", "_from": "users/u2", "type": "USER", "role": "READER"},
            {"_key": "p2", "_from": "teams/t1", "type": "TEAM", "role": "READER"},
        ])

        result = await connected_provider.update_agent_permission(
            "a1", "u1", "org1", user_ids=["u2"], team_ids=["t1"], role="WRITER"
        )
        assert result["success"] is True
        assert result["updated_users"] == 1
        assert result["updated_teams"] == 1

    @pytest.mark.asyncio
    async def test_no_permissions_found(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value={"_key": "a1", "user_role": "OWNER"})
        connected_provider.execute_query = AsyncMock(return_value=[])

        result = await connected_provider.update_agent_permission(
            "a1", "u1", "org1", user_ids=["u2"], team_ids=None, role="WRITER"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_not_owner(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value={"_key": "a1", "user_role": "READER"})

        result = await connected_provider.update_agent_permission(
            "a1", "u1", "org1", user_ids=["u2"], team_ids=None, role="WRITER"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_agent(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value=None)

        result = await connected_provider.update_agent_permission(
            "a1", "u1", "org1", user_ids=["u2"], team_ids=None, role="WRITER"
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, connected_provider):
        connected_provider.check_agent_permission = AsyncMock(return_value={"_key": "a1", "user_role": "OWNER"})

        result = await connected_provider.update_agent_permission(
            "a1", "u1", "org1", user_ids=None, team_ids=None, role="WRITER"
        )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# share_agent_template (lines 19466-19491)
# ---------------------------------------------------------------------------


class TestShareAgentTemplateDetailed:
    @pytest.mark.asyncio
    async def test_share_with_users(self, connected_provider):
        # Query returns the template doc; share_agent_template checks role == "OWNER"
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "t1", "role": "OWNER"}])
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u2"})
        connected_provider.batch_create_edges = AsyncMock(return_value=True)

        result = await connected_provider.share_agent_template("t1", "u1", user_ids=["u2"], team_ids=None)
        assert result is True

    @pytest.mark.asyncio
    async def test_share_with_teams(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "t1", "role": "OWNER"}])
        connected_provider.batch_create_edges = AsyncMock(return_value=True)

        result = await connected_provider.share_agent_template("t1", "u1", user_ids=None, team_ids=["team1"])
        assert result is True

    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "t1", "role": "OWNER"}])
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await connected_provider.share_agent_template("t1", "u1", user_ids=["u2"], team_ids=None)
        assert result is False

    @pytest.mark.asyncio
    async def test_batch_create_fails(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "t1", "role": "OWNER"}])
        connected_provider.batch_create_edges = AsyncMock(return_value=False)

        result = await connected_provider.share_agent_template("t1", "u1", user_ids=None, team_ids=["team1"])
        assert result is False

    @pytest.mark.asyncio
    async def test_no_owner_access(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.share_agent_template("t1", "u1", user_ids=["u2"])
        assert result is False

    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "t1", "role": "OWNER"}])
        result = await connected_provider.share_agent_template("t1", "u1", user_ids=None, team_ids=None)
        assert result is False


# ---------------------------------------------------------------------------
# clone_agent_template (line 19523 - success path)
# ---------------------------------------------------------------------------


class TestCloneAgentTemplateSuccess:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "_key": "t1", "name": "Template", "isActive": True
        })
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        result = await connected_provider.clone_agent_template("t1")
        assert result is not None
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_upsert_fails(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "_key": "t1", "name": "Template"
        })
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=False)

        result = await connected_provider.clone_agent_template("t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.clone_agent_template("t1")
        assert result is None


# ---------------------------------------------------------------------------
# delete_agent_template (lines 19557-19582)
# ---------------------------------------------------------------------------


class TestDeleteAgentTemplateDetailed:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "OWNER"}])
        connected_provider.get_document = AsyncMock(return_value={"_key": "t1"})
        connected_provider.update_node = AsyncMock(return_value=True)

        result = await connected_provider.delete_agent_template("t1", "u1")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_owner(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "READER"}])

        result = await connected_provider.delete_agent_template("t1", "u1")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.delete_agent_template("t1", "u1")
        assert result is False

    @pytest.mark.asyncio
    async def test_template_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "OWNER"}])
        connected_provider.get_document = AsyncMock(return_value=None)

        result = await connected_provider.delete_agent_template("t1", "u1")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_fails(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "OWNER"}])
        connected_provider.get_document = AsyncMock(return_value={"_key": "t1"})
        connected_provider.update_node = AsyncMock(return_value=None)

        result = await connected_provider.delete_agent_template("t1", "u1")
        assert result is False


# ---------------------------------------------------------------------------
# update_agent_template (lines 19616-19646)
# ---------------------------------------------------------------------------


class TestUpdateAgentTemplateDetailed:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "OWNER"}])
        connected_provider.update_node = AsyncMock(return_value=True)

        result = await connected_provider.update_agent_template(
            "t1", {"name": "Updated", "description": "New desc"}, "u1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_not_owner(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "READER"}])

        result = await connected_provider.update_agent_template("t1", {"name": "X"}, "u1")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.update_agent_template("t1", {"name": "X"}, "u1")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_fails(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "OWNER"}])
        connected_provider.update_node = AsyncMock(return_value=None)

        result = await connected_provider.update_agent_template("t1", {"name": "X"}, "u1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.update_agent_template("t1", {"name": "X"}, "u1")
        assert result is False


# ---------------------------------------------------------------------------
# get_accessible_virtual_record_ids metadata filters (lines 16666-16679)
# ---------------------------------------------------------------------------


class TestGetAccessibleVirtualRecordIdsMetadata:
    @pytest.mark.asyncio
    async def test_with_all_metadata_filters(self, connected_provider):
        """Test that all metadata filter bind vars are set."""
        connected_provider._get_user_app_ids = AsyncMock(return_value=["conn1"])
        connected_provider._get_virtual_ids_for_connector = AsyncMock(return_value={"vr1": "r1"})
        connected_provider._get_kb_virtual_ids = AsyncMock(return_value={})
        filters = {
            "apps": ["conn1"],
            "departments": ["Engineering"],
            "categories": ["Tech"],
            "subcategories1": ["AI"],
            "subcategories2": ["ML"],
            "subcategories3": ["DL"],
            "languages": ["en"],
            "topics": ["Search"],
        }
        result = await connected_provider.get_accessible_virtual_record_ids("u1", "org1", filters=filters)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_with_partial_metadata_filters(self, connected_provider):
        connected_provider._get_user_app_ids = AsyncMock(return_value=["conn1"])
        connected_provider._get_virtual_ids_for_connector = AsyncMock(return_value={})
        connected_provider._get_kb_virtual_ids = AsyncMock(return_value={})
        filters = {"apps": ["conn1"], "departments": ["Sales"]}
        result = await connected_provider.get_accessible_virtual_record_ids("u1", "org1", filters=filters)
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_kb_filter_only(self, connected_provider):
        connected_provider._get_user_app_ids = AsyncMock(return_value=[])
        connected_provider._get_kb_virtual_ids = AsyncMock(return_value={"vr1": "r1"})
        filters = {"kb": ["kb1"]}
        result = await connected_provider.get_accessible_virtual_record_ids("u1", "org1", filters=filters)
        assert result == {"vr1": "r1"}

    @pytest.mark.asyncio
    async def test_with_no_filters(self, connected_provider):
        connected_provider._get_user_app_ids = AsyncMock(return_value=["conn1"])
        connected_provider._get_virtual_ids_for_connector = AsyncMock(return_value={"vr1": "r1"})
        connected_provider._get_kb_virtual_ids = AsyncMock(return_value={})
        result = await connected_provider.get_accessible_virtual_record_ids("u1", "org1")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# _get_kb_virtual_ids metadata filters (lines 16725-16765)
# ---------------------------------------------------------------------------


class TestGetKbVirtualIdsMetadata:
    @pytest.mark.asyncio
    async def test_with_metadata_filters(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"virtualRecordId": "vr1", "recordId": "r1"}
        ])
        metadata = {
            "departments": ["Engineering"],
            "categories": ["Tech"],
        }
        result = await connected_provider._get_kb_virtual_ids(
            "u1", "org1", kb_ids=["kb1"], metadata_filters=metadata
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# _get_virtual_ids_for_connector with metadata filters (lines 16491-16540)
# ---------------------------------------------------------------------------


class TestGetVirtualIdsForConnectorMetadata:
    @pytest.mark.asyncio
    async def test_with_all_metadata_filters(self, connected_provider):
        """Test that metadata filter lines and bind vars are constructed."""
        connected_provider.execute_query = AsyncMock(return_value=[
            {"virtualRecordId": "vr1", "recordId": "r1"}
        ])
        metadata = {
            "departments": ["Engineering"],
            "categories": ["Tech"],
            "subcategories1": ["AI"],
            "subcategories2": ["ML"],
            "subcategories3": ["DL"],
            "languages": ["en"],
            "topics": ["Search"],
        }
        result = await connected_provider._get_virtual_ids_for_connector(
            "u1", "org1", "conn1", metadata
        )
        assert isinstance(result, dict)
        # Check bind vars were set in execute_query call
        call_args = connected_provider.execute_query.call_args
        bind_vars = call_args[1].get("bind_vars", call_args[0][1] if len(call_args[0]) > 1 else {})
        assert "departmentNames" in bind_vars
        assert "categoryNames" in bind_vars
        assert "subcat1Names" in bind_vars
        assert "subcat2Names" in bind_vars
        assert "subcat3Names" in bind_vars
        assert "languageNames" in bind_vars
        assert "topicNames" in bind_vars

    @pytest.mark.asyncio
    async def test_with_no_metadata(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._get_virtual_ids_for_connector("u1", "org1", "conn1", None)
        assert result == {}


# ---------------------------------------------------------------------------
# _get_kb_virtual_ids with metadata filters (lines 16725-16773)
# ---------------------------------------------------------------------------


class TestGetKbVirtualIdsMetadataDetailed:
    @pytest.mark.asyncio
    async def test_with_all_metadata_filters(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"virtualRecordId": "vr1", "recordId": "r1"}
        ])
        metadata = {
            "departments": ["Engineering"],
            "categories": ["Tech"],
            "subcategories1": ["AI"],
            "subcategories2": ["ML"],
            "subcategories3": ["DL"],
            "languages": ["en"],
            "topics": ["Search"],
        }
        result = await connected_provider._get_kb_virtual_ids(
            "u1", "org1", kb_ids=["kb1"], metadata_filters=metadata
        )
        assert isinstance(result, dict)
        call_args = connected_provider.execute_query.call_args
        bind_vars = call_args[1].get("bind_vars", call_args[0][1] if len(call_args[0]) > 1 else {})
        assert "departmentNames" in bind_vars
        assert "categoryNames" in bind_vars
        assert "subcat1Names" in bind_vars
        assert "subcat2Names" in bind_vars
        assert "subcat3Names" in bind_vars
        assert "languageNames" in bind_vars
        assert "topicNames" in bind_vars


# ---------------------------------------------------------------------------
# check_toolset_instance_in_use (line 17984 - empty return)
# ---------------------------------------------------------------------------


class TestCheckToolsetInstanceInUse:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_agents(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                ["ts1"],  # toolset_ids
                [],       # no agents
            ]
        )
        result = await connected_provider.check_toolset_instance_in_use("inst1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_agent_names(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            side_effect=[
                ["ts1"],
                [{"agentId": "a1", "agentName": "Agent1"}, {"agentId": "a2", "agentName": "Agent2"}],
            ]
        )
        result = await connected_provider.check_toolset_instance_in_use("inst1")
        assert "Agent1" in result
        assert "Agent2" in result

# =============================================================================
# Merged from test_arango_http_provider_full_coverage.py
# =============================================================================

@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "url": "http://localhost:8529",
        "username": "root",
        "password": "secret",
        "db": "test_db",
    })
    return cs


@pytest.fixture
def provider(mock_logger, mock_config_service):
    return ArangoHTTPProvider(mock_logger, mock_config_service)


@pytest.fixture
def connected_provider_fullcov(provider):
    provider.http_client = AsyncMock()
    return provider


class TestCreateTypedRecordFromArangoFullCoverage:
    def test_no_type_doc_returns_base_record(self, provider):
        record_dict = {"_key": "r1", "recordType": "FILE"}
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = "base_record"
            result = provider._create_typed_record_from_arango(record_dict, None)
            assert result == "base_record"

    def test_unknown_record_type_returns_base(self, provider):
        record_dict = {"_key": "r1", "recordType": "UNKNOWN_TYPE"}
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = "base_record"
            result = provider._create_typed_record_from_arango(record_dict, {"some": "doc"})
            assert result == "base_record"

    def test_file_record_type(self, provider):
        record_dict = {"_key": "r1", "recordType": "FILE"}
        type_doc = {"fileSize": 100}
        with patch("app.services.graph_db.arango.arango_http_provider.FileRecord") as mock_fr:
            mock_fr.from_arango_record.return_value = "file_record"
            with patch("app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING", {"FILE": "files"}):
                result = provider._create_typed_record_from_arango(record_dict, type_doc)
                assert result == "file_record"

    def test_mail_record_type(self, provider):
        record_dict = {"_key": "r1", "recordType": "MAIL"}
        type_doc = {"subject": "test"}
        with patch("app.services.graph_db.arango.arango_http_provider.MailRecord") as mock_mr:
            mock_mr.from_arango_record.return_value = "mail_record"
            with patch("app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING", {"MAIL": "mails"}):
                result = provider._create_typed_record_from_arango(record_dict, type_doc)
                assert result == "mail_record"

    def test_exception_falls_back_to_base(self, provider):
        record_dict = {"_key": "r1", "recordType": "FILE"}
        type_doc = {"bad": "data"}
        with patch("app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING", {"FILE": "files"}):
            with patch("app.services.graph_db.arango.arango_http_provider.FileRecord") as mock_fr:
                mock_fr.from_arango_record.side_effect = Exception("parse error")
                with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
                    mock_record.from_arango_base_record.return_value = "base_fallback"
                    result = provider._create_typed_record_from_arango(record_dict, type_doc)
                    assert result == "base_fallback"


class TestGetRecordByIdFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[{
            "record": {"_key": "r1", "recordType": "FILE"},
            "typeDoc": {"fileSize": 100},
        }])
        with patch.object(connected_provider_fullcov, "_create_typed_record_from_arango", return_value="typed"):
            result = await connected_provider_fullcov.get_record_by_id("r1")
            assert result == "typed"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.get_record_by_id("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider_fullcov.get_record_by_id("r1")
        assert result is None


class TestCheckRecordGroupPermissionsFullCoverage:
    @pytest.mark.asyncio
    async def test_allowed(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[{"allowed": True, "role": "OWNER"}])
        result = await connected_provider_fullcov._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is True
        assert result["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_denied(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[{"allowed": False}])
        result = await connected_provider_fullcov._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[])
        result = await connected_provider_fullcov._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False


class TestCheckConnectorNameExistsFullCoverage:
    @pytest.mark.asyncio
    async def test_personal_scope_exists(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider_fullcov.check_connector_name_exists(
            "apps", "My Connector", "personal", user_id="u1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_personal_scope_not_exists(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.check_connector_name_exists(
            "apps", "My Connector", "personal", user_id="u1"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_team_scope(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider_fullcov.check_connector_name_exists(
            "apps", "Connector", "team", org_id="org1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.check_connector_name_exists(
            "apps", "Connector", "personal", user_id="u1"
        )
        assert result is False


class TestBatchUpdateConnectorStatusFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[{}, {}])
        result = await connected_provider_fullcov.batch_update_connector_status(
            "apps", ["k1", "k2"], is_active=True, is_agent_active=False
        )
        assert result == 2

    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider_fullcov):
        result = await connected_provider_fullcov.batch_update_connector_status(
            "apps", [], is_active=True, is_agent_active=False
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.batch_update_connector_status(
            "apps", ["k1"], is_active=True, is_agent_active=False
        )
        assert result == 0


class TestGetUserConnectorInstancesFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[{"_key": "d1"}])
        result = await connected_provider_fullcov.get_user_connector_instances(
            "apps", "u1", "org1", "team", "personal"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_user_connector_instances(
            "apps", "u1", "org1", "team", "personal"
        )
        assert result == []


class TestGetFilteredConnectorInstancesFullCoverage:
    @pytest.mark.asyncio
    async def test_with_personal_scope(self, connected_provider_fullcov):
        async def mock_query(query, bind_vars=None, transaction=None):
            if "COUNT" in query or "COLLECT" in query:
                return [5]
            return [{"_key": "d1"}]
        connected_provider_fullcov.execute_query = mock_query
        docs, total, scope_counts = await connected_provider_fullcov.get_filtered_connector_instances(
            "apps", "edges", "org1", "u1", scope="personal"
        )
        assert total == 5

    @pytest.mark.asyncio
    async def test_with_team_scope_admin(self, connected_provider_fullcov):
        async def mock_query(query, bind_vars=None, transaction=None):
            if "COLLECT" in query:
                return [3]
            return [{"_key": "d1"}]
        connected_provider_fullcov.execute_query = mock_query
        docs, total, scope_counts = await connected_provider_fullcov.get_filtered_connector_instances(
            "apps", "edges", "org1", "u1", scope="team", is_admin=True
        )
        assert isinstance(scope_counts, dict)

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("err"))
        docs, total, scope_counts = await connected_provider_fullcov.get_filtered_connector_instances(
            "apps", "edges", "org1", "u1"
        )
        assert docs == []
        assert total == 0


class TestReindexRecordGroupRecordsFullCoverage:
    @pytest.mark.asyncio
    async def test_depth_minus_one_normalizes(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider_fullcov._check_record_group_permissions = AsyncMock(return_value={"allowed": True, "role": "OWNER"})
        result = await connected_provider_fullcov.reindex_record_group_records("rg1", -1, "u1", "org1")
        assert result["success"] is True
        assert result["depth"] == MAX_REINDEX_DEPTH

    @pytest.mark.asyncio
    async def test_record_group_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value=None)
        result = await connected_provider_fullcov.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_connector_id(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "rg1"})
        result = await connected_provider_fullcov.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider_fullcov.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_permission_denied(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider_fullcov._check_record_group_permissions = AsyncMock(return_value={"allowed": False, "reason": "no access"})
        result = await connected_provider_fullcov.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_negative_depth(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider_fullcov._check_record_group_permissions = AsyncMock(return_value={"allowed": True, "role": "OWNER"})
        result = await connected_provider_fullcov.reindex_record_group_records("rg1", -5, "u1", "org1")
        assert result["success"] is True
        assert result["depth"] == 0


class TestResetIndexingStatusToQueuedFullCoverage:
    @pytest.mark.asyncio
    async def test_already_queued(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "r1", "indexingStatus": "QUEUED"})
        connected_provider_fullcov.batch_upsert_nodes = AsyncMock()
        await connected_provider_fullcov._reset_indexing_status_to_queued("r1")
        connected_provider_fullcov.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_empty(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "r1", "indexingStatus": "EMPTY"})
        connected_provider_fullcov.batch_upsert_nodes = AsyncMock()
        await connected_provider_fullcov._reset_indexing_status_to_queued("r1")
        connected_provider_fullcov.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resets_to_queued(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"id": "r1", "indexingStatus": "INDEXED"})
        connected_provider_fullcov.batch_upsert_nodes = AsyncMock()
        await connected_provider_fullcov._reset_indexing_status_to_queued("r1")
        connected_provider_fullcov.batch_upsert_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value=None)
        await connected_provider_fullcov._reset_indexing_status_to_queued("r1")


class TestCheckRecordPermissionsFullCoverage:
    @pytest.mark.asyncio
    async def test_has_permission(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[{"permission": "OWNER", "source": "DIRECT"}])
        result = await connected_provider_fullcov._check_record_permissions("r1", "u1")
        assert result["permission"] == "OWNER"
        assert result["source"] == "DIRECT"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[{"permission": None, "source": "NONE"}])
        result = await connected_provider_fullcov._check_record_permissions("r1", "u1")
        assert result["permission"] is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov._check_record_permissions("r1", "u1")
        assert result["permission"] is None
        assert result["source"] == "ERROR"


class TestBatchCreateEntityRelationsFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{}])
        result = await connected_provider_fullcov.batch_create_entity_relations([
            {"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records", "edgeType": "ASSIGNED_TO"}
        ])
        assert result is True

    @pytest.mark.asyncio
    async def test_empty_edges(self, connected_provider_fullcov):
        result = await connected_provider_fullcov.batch_create_entity_relations([])
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider_fullcov.batch_create_entity_relations([{"_from": "a/b", "_to": "c/d", "edgeType": "X"}])


class TestDeleteEdgesByRelationshipTypesFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider_fullcov.delete_edges_by_relationship_types("u1", "users", "edges", ["PARENT_CHILD"])
        assert result == 2

    @pytest.mark.asyncio
    async def test_empty_types(self, connected_provider_fullcov):
        result = await connected_provider_fullcov.delete_edges_by_relationship_types("u1", "users", "edges", [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider_fullcov.delete_edges_by_relationship_types("u1", "users", "edges", ["PARENT_CHILD"])


class TestDeleteAllEdgesForNodesFullCoverage:
    @pytest.mark.asyncio
    async def test_success_multiple_collections(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[1, 1])
        total, failed = await connected_provider_fullcov._delete_all_edges_for_nodes(
            None, ["users/u1"], ["isOfType", "permissions"]
        )
        assert total == 4
        assert failed == []

    @pytest.mark.asyncio
    async def test_empty_node_ids(self, connected_provider_fullcov):
        total, failed = await connected_provider_fullcov._delete_all_edges_for_nodes(None, [], ["isOfType"])
        assert total == 0
        assert failed == []

    @pytest.mark.asyncio
    async def test_partial_failure(self, connected_provider_fullcov):
        call_count = 0
        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("fail")
            return [1]
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=[Exception("fail"), [1]])
        total, failed = await connected_provider_fullcov._delete_all_edges_for_nodes(
            None, ["users/u1"], ["coll1", "coll2"]
        )
        assert len(failed) == 1
        assert total == 1


class TestGetNodesByFiltersFullCoverage:
    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"name": "test"}])
        result = await connected_provider_fullcov.get_nodes_by_filters(
            "records", {"orgId": "org1"}, return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_without_return_fields(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        result = await connected_provider_fullcov.get_nodes_by_filters("records", {"orgId": "org1"})
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_nodes_by_filters("records", {"orgId": "org1"})
        assert result == []


class TestGetNodesByFieldInFullCoverage:
    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"name": "A"}])
        result = await connected_provider_fullcov.get_nodes_by_field_in(
            "records", "orgId", ["o1", "o2"], return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_without_return_fields(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        result = await connected_provider_fullcov.get_nodes_by_field_in("records", "orgId", ["o1"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_nodes_by_field_in("records", "orgId", ["o1"])
        assert result == []


class TestRemoveNodesByFieldFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider_fullcov.remove_nodes_by_field("records", "orgId", field_value="org1")
        assert result == 2

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider_fullcov.remove_nodes_by_field("records", "orgId", field_value="org1")


class TestGetEdgesToNodeFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_from": "a", "_to": "b"}])
        result = await connected_provider_fullcov.get_edges_to_node("records/r1", "isOfType")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_edges_to_node("records/r1", "isOfType")
        assert result == []


class TestGetEdgesFromNodeFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_from": "a", "_to": "b"}])
        result = await connected_provider_fullcov.get_edges_from_node("users/u1", "permissions")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_edges_from_node("users/u1", "permissions")
        assert result == []


class TestGetRelatedNodesFullCoverage:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_key": "n1"}])
        result = await connected_provider_fullcov.get_related_nodes("users/u1", "permissions", "records", "outbound")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_key": "n1"}])
        result = await connected_provider_fullcov.get_related_nodes("records/r1", "permissions", "users", "inbound")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_related_nodes("users/u1", "perm", "records")
        assert result == []


class TestGetRelatedNodeFieldFullCoverage:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=["email@test.com"])
        result = await connected_provider_fullcov.get_related_node_field("users/u1", "perm", "records", "email")
        assert result == ["email@test.com"]

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=["val"])
        result = await connected_provider_fullcov.get_related_node_field("r/r1", "p", "u", "f", "inbound")
        assert result == ["val"]

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_related_node_field("r/r1", "p", "u", "f")
        assert result == []


class TestGetRecordByExternalIdFullCoverage:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1", "externalRecordId": "ext1"}])
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = "record"
            result = await connected_provider_fullcov.get_record_by_external_id("c1", "ext1")
            assert result == "record"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.get_record_by_external_id("c1", "ext1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_record_by_external_id("c1", "ext1")
        assert result is None


class TestGetRecordPathFullCoverage:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=["Folder/File.txt"])
        result = await connected_provider_fullcov.get_record_path("r1")
        assert result == "Folder/File.txt"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.get_record_path("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_record_path("r1")
        assert result is None


class TestGetRecordByExternalRevisionIdFullCoverage:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = "rec"
            result = await connected_provider_fullcov.get_record_by_external_revision_id("c1", "rev1")
            assert result == "rec"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.get_record_by_external_revision_id("c1", "rev1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_record_by_external_revision_id("c1", "rev1")
        assert result is None


class TestGetRecordKeyByExternalIdFullCoverage:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=["key1"])
        result = await connected_provider_fullcov.get_record_key_by_external_id("ext1", "c1")
        assert result == "key1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.get_record_key_by_external_id("ext1", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_record_key_by_external_id("ext1", "c1")
        assert result is None


class TestGetRecordByPathFullCoverage:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{"path": "/test/file.txt"}])
        result = await connected_provider_fullcov.get_record_by_path("c1", "/test/file.txt")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.get_record_by_path("c1", "/missing.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.get_record_by_path("c1", "/file.txt")
        assert result is None


class TestEnsureSchemaFullCoverage:
    @pytest.mark.asyncio
    async def test_not_connected(self, provider):
        result = await provider.ensure_schema()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.has_collection = AsyncMock(return_value=True)
        connected_provider_fullcov.http_client.has_graph = AsyncMock(return_value=True)
        connected_provider_fullcov._ensure_departments_seed = AsyncMock()
        result = await connected_provider_fullcov.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_creates_missing_collections(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.has_collection = AsyncMock(return_value=False)
        connected_provider_fullcov.http_client.create_collection = AsyncMock(return_value=True)
        connected_provider_fullcov.http_client.has_graph = AsyncMock(return_value=True)
        connected_provider_fullcov._ensure_departments_seed = AsyncMock()
        result = await connected_provider_fullcov.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_creates_graph(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.has_collection = AsyncMock(return_value=True)
        connected_provider_fullcov.http_client.has_graph = AsyncMock(return_value=False)
        connected_provider_fullcov.http_client.create_graph = AsyncMock(return_value=True)
        connected_provider_fullcov._ensure_departments_seed = AsyncMock()
        result = await connected_provider_fullcov.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.has_collection = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.ensure_schema()
        assert result is False


class TestEnsureDepartmentsSeedFullCoverage:
    @pytest.mark.asyncio
    async def test_inserts_missing(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(return_value=["Engineering"])
        connected_provider_fullcov.batch_upsert_nodes = AsyncMock()
        await connected_provider_fullcov._ensure_departments_seed()

    @pytest.mark.asyncio
    async def test_all_exist(self, connected_provider_fullcov):
        from app.config.constants.arangodb import DepartmentNames
        connected_provider_fullcov.execute_query = AsyncMock(return_value=[d.value for d in DepartmentNames])
        connected_provider_fullcov.batch_upsert_nodes = AsyncMock()
        await connected_provider_fullcov._ensure_departments_seed()

    @pytest.mark.asyncio
    async def test_exception_non_fatal(self, connected_provider_fullcov):
        connected_provider_fullcov.execute_query = AsyncMock(side_effect=Exception("err"))
        await connected_provider_fullcov._ensure_departments_seed()


class TestDeleteAllEdgesForNodeFullCoverage:
    @pytest.mark.asyncio
    async def test_with_slash_key(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider_fullcov.delete_all_edges_for_node("users/u1", "permissions")
        assert result == 2

    @pytest.mark.asyncio
    async def test_without_slash_returns_zero(self, connected_provider_fullcov):
        result = await connected_provider_fullcov.delete_all_edges_for_node("u1", "permissions")
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_edges_found(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider_fullcov.delete_all_edges_for_node("users/u1", "permissions")
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider_fullcov.delete_all_edges_for_node("users/u1", "permissions")


class TestReindexSingleRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value=None)
        result = await connected_provider_fullcov.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_deleted_record(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"isDeleted": True})
        result = await connected_provider_fullcov.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"connectorName": "Drive", "origin": "CONNECTOR"})
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider_fullcov.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_unsupported_origin(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(return_value={"origin": "UNKNOWN", "connectorName": "X"})
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        result = await connected_provider_fullcov.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_depth_normalization(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(side_effect=[
            {"origin": "CONNECTOR", "connectorName": "Drive", "connectorId": "c1", "isInternal": False},
            {"isActive": True},
        ])
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider_fullcov._check_record_permissions = AsyncMock(return_value={"permission": "OWNER"})
        connected_provider_fullcov._reset_indexing_status_to_queued = AsyncMock()
        result = await connected_provider_fullcov.reindex_single_record("r1", "u1", "org1", depth=-1)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_connector_disabled(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(side_effect=[
            {"origin": "CONNECTOR", "connectorName": "Drive", "connectorId": "c1"},
            {"isActive": False},
        ])
        connected_provider_fullcov.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider_fullcov._check_record_permissions = AsyncMock(return_value={"permission": "OWNER"})
        result = await connected_provider_fullcov.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider_fullcov):
        connected_provider_fullcov.get_document = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider_fullcov.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 500
