"""
Unit tests for ArangoHTTPProvider — second half coverage.

Lines 9500-21755: page tokens, edge checks, failed records, organization,
knowledge hub, knowledge base CRUD, duplicate detection, move record,
teams, users, agents, agent templates, and AQL builder helpers.
"""
import logging
import unicodedata
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


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
    provider.http_client = AsyncMock()
    provider.get_authenticated_as = AsyncMock(return_value=[])
    provider._get_authenticated_as_by_user_id = AsyncMock(return_value=[])
    provider._resolve_acting_user_key_for_node = AsyncMock(side_effect=lambda user_key, *_: user_key)
    provider._authenticated_as_apps = AsyncMock(return_value=[])
    return provider


# ===========================================================================
# Update Drive Sync State (lines 9492-9526)
# ===========================================================================

class TestUpdateDriveSyncState:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "d1"}])
        connected_provider.update_node = AsyncMock()
        await connected_provider.update_drive_sync_state("drive1", "SYNCED")
        connected_provider.update_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_drive_not_found(self, connected_provider):
        connected_provider.get_nodes_by_filters = AsyncMock(return_value=[])
        connected_provider.update_node = AsyncMock()
        await connected_provider.update_drive_sync_state("drive1", "SYNCED")
        connected_provider.update_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_nodes_by_filters = AsyncMock(side_effect=Exception("fail"))
        await connected_provider.update_drive_sync_state("drive1", "SYNCED")


# ===========================================================================
# Page Tokens (lines 9529-9647)
# ===========================================================================

class TestStorePageToken:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "tok1"}])
        result = await connected_provider.store_page_token("ch1", "res1", "u@e.com", "t1", "exp1")
        assert result == {"_key": "tok1"}
        connected_provider.http_client.execute_aql.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.store_page_token("ch1", "res1", "u@e.com", "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.store_page_token("ch1", "res1", "u@e.com", "t1")
        assert result is None


class TestGetPageTokenDb:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"token": "abc"}])
        result = await connected_provider.get_page_token_db(channel_id="ch1")
        assert result == {"token": "abc"}

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_page_token_db(channel_id="ch1")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_filters(self, connected_provider):
        result = await connected_provider.get_page_token_db()
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_filters(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"token": "x"}])
        result = await connected_provider.get_page_token_db(channel_id="c", resource_id="r", user_email="e@e.com")
        assert result == {"token": "x"}

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_page_token_db(channel_id="ch1")
        assert result is None


# ===========================================================================
# Check Collection / Edge (lines 9649-9689)
# ===========================================================================

class TestCheckCollectionHasDocument:
    @pytest.mark.asyncio
    async def test_exists(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"_key": "d1"})
        assert await connected_provider.check_collection_has_document("col", "d1") is True

    @pytest.mark.asyncio
    async def test_not_exists(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        assert await connected_provider.check_collection_has_document("col", "d1") is False


class TestCheckEdgeExists:
    @pytest.mark.asyncio
    async def test_exists(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "e1"}])
        assert await connected_provider.check_edge_exists("from1", "to1", "col") is True

    @pytest.mark.asyncio
    async def test_not_exists(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        assert await connected_provider.check_edge_exists("from1", "to1", "col") is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        assert await connected_provider.check_edge_exists("from1", "to1", "col") is False


# ===========================================================================
# Failed Records (lines 9691-9755)
# ===========================================================================

class TestGetFailedRecordsByOrg:
    @pytest.mark.asyncio
    async def test_delegates(self, connected_provider):
        connected_provider.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "r1"}])
        result = await connected_provider.get_failed_records_by_org("org1", "conn1")
        assert result == [{"_key": "r1"}]
        connected_provider.get_nodes_by_filters.assert_called_once()


# ===========================================================================
# Organization Exists (lines 9757-9786)
# ===========================================================================

class TestOrganizationExists:
    @pytest.mark.asyncio
    async def test_exists(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "o1"}])
        assert await connected_provider.organization_exists("TestOrg") is True

    @pytest.mark.asyncio
    async def test_not_exists(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        assert await connected_provider.organization_exists("NoOrg") is False

    @pytest.mark.asyncio
    async def test_external(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "o1"}])
        assert await connected_provider.organization_exists("Ext", is_external=True) is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        assert await connected_provider.organization_exists("Org") is False


# ===========================================================================
# Delete Edges to Groups (lines 9788-9841)
# ===========================================================================

class TestDeleteEdgesToGroups:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        count = await connected_provider.delete_edges_to_groups("id1", "users", "permission")
        assert count == 2

    @pytest.mark.asyncio
    async def test_no_edges(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        count = await connected_provider.delete_edges_to_groups("id1", "users", "permission")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        count = await connected_provider.delete_edges_to_groups("id1", "users", "permission")
        assert count == 0


# ===========================================================================
# Delete Edges Between Collections (lines 9843-9907)
# ===========================================================================

class TestDeleteEdgesBetweenCollections:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}])
        count = await connected_provider.delete_edges_between_collections("id1", "users", "edge_col", "groups")
        assert count == 1

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        count = await connected_provider.delete_edges_between_collections("id1", "users", "edge_col", "groups")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        count = await connected_provider.delete_edges_between_collections("id1", "users", "edge_col", "groups")
        assert count == 0


# ===========================================================================
# Delete Nodes and Edges (lines 9909-9996)
# ===========================================================================

class TestDeleteNodesAndEdges:
    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        await connected_provider.delete_nodes_and_edges([], "records")
        connected_provider.http_client.execute_aql.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_graph(self, connected_provider):
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [{"collection": "permission"}, {"collection": "belongsTo"}]}
        })
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider.begin_transaction = AsyncMock(return_value="own-txn")
        connected_provider.commit_transaction = AsyncMock()
        connected_provider.delete_nodes = AsyncMock()
        await connected_provider.delete_nodes_and_edges(["k1"], "records")
        # No transaction given: the delete runs the node delete inside its own.
        connected_provider.delete_nodes.assert_called_once_with(["k1"], "records", "own-txn")

    @pytest.mark.asyncio
    async def test_graph_not_found_raises_and_deletes_nothing(self, connected_provider: ArangoHTTPProvider) -> None:
        """A guessed list of edge collections left the knowledge graph's other record
        edges dangling; without the definition the record stays whole."""
        connected_provider.http_client.get_graph = AsyncMock(return_value=None)
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider.delete_nodes = AsyncMock()
        with pytest.raises(Exception, match="not found"):
            await connected_provider.delete_nodes_and_edges(["k1"], "records")
        connected_provider.http_client.execute_aql.assert_not_awaited()
        connected_provider.delete_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("miss", [None, {"graph": {"edgeDefinitions": []}}], ids=["not-found", "no-edges"])
    async def test_a_missed_graph_read_is_not_cached(self, connected_provider: ArangoHTTPProvider, miss: object) -> None:
        """get_graph answers None to a 404, any other status and a transport error; a miss
        must not stand in for the definition for the life of the provider."""
        real = {"graph": {"edgeDefinitions": [{"collection": "permission"}, {"collection": "belongsToTopic"}]}}
        connected_provider.http_client.get_graph = AsyncMock(side_effect=[miss, real])
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider.delete_nodes = AsyncMock()
        with pytest.raises(Exception):
            await connected_provider.delete_nodes_and_edges(["k1"], "records")
        await connected_provider.delete_nodes_and_edges(["k1"], "records")
        assert connected_provider.http_client.get_graph.await_count == 2
        assert [c.kwargs["bind_vars"]["@edge_collection"]
                for c in connected_provider.http_client.execute_aql.await_args_list] == [
            "permission", "permission", "belongsToTopic", "belongsToTopic",
        ]
        connected_provider.delete_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_raises(self, connected_provider):
        connected_provider.http_client.get_graph = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.delete_nodes_and_edges(["k1"], "records")

    @pytest.mark.asyncio
    async def test_edge_collections_are_read_once_per_provider(self, connected_provider: ArangoHTTPProvider) -> None:
        """A per-record delete must not refetch the graph definition every time."""
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [{"collection": "permission"}]}
        })
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider.delete_nodes = AsyncMock()
        await connected_provider.delete_nodes_and_edges(["k1"], "records")
        await connected_provider.delete_nodes_and_edges(["k2"], "records")
        assert connected_provider.http_client.get_graph.await_count == 1

    @pytest.mark.asyncio
    async def test_edges_are_removed_by_from_and_by_to_so_the_edge_index_is_used(self, connected_provider: ArangoHTTPProvider) -> None:
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [{"collection": "permission"}, {"collection": "belongsTo"}]}
        })
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider.delete_nodes = AsyncMock()
        await connected_provider.delete_nodes_and_edges(["k1"], "records", transaction="txn-1")

        calls = connected_provider.http_client.execute_aql.await_args_list
        assert len(calls) == 4, "one _from and one _to statement per edge collection"
        for call in calls:
            aql = call.args[0]
            assert " OR " not in aql, "an OR over _from and _to cannot use the edge index"
            assert ("edge._from IN @node_ids" in aql) != ("edge._to IN @node_ids" in aql)
            assert call.kwargs["bind_vars"]["node_ids"] == ["records/k1"]
            assert call.kwargs["txn_id"] == "txn-1"
        assert [c.kwargs["bind_vars"]["@edge_collection"] for c in calls] == [
            "permission", "permission", "belongsTo", "belongsTo",
        ]
        connected_provider.delete_nodes.assert_awaited_once_with(["k1"], "records", "txn-1")

    @pytest.mark.asyncio
    async def test_a_failed_edge_delete_inside_a_transaction_is_raised_before_the_node_delete(
        self, connected_provider: ArangoHTTPProvider
    ) -> None:
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [{"collection": "permission"}]}
        })
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("write-write conflict"))
        connected_provider.delete_nodes = AsyncMock()
        with pytest.raises(Exception, match="write-write conflict"):
            await connected_provider.delete_nodes_and_edges(["k1"], "records", transaction="txn-1")
        connected_provider.delete_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_a_transaction_the_whole_delete_runs_in_one_of_its_own(
        self, connected_provider: ArangoHTTPProvider
    ) -> None:
        """Each statement commits on its own without a transaction id, so a failure
        after the permission edges were removed left the record with no owner. The
        delete opens its own transaction over the vertex and edge collections."""
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [{"collection": "permission"}, {"collection": "belongsTo"}]}
        })
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[[], [], [], Exception("fail")])
        connected_provider.begin_transaction = AsyncMock(return_value="own-txn")
        connected_provider.commit_transaction = AsyncMock()
        connected_provider.rollback_transaction = AsyncMock()
        connected_provider.delete_nodes = AsyncMock()

        with pytest.raises(Exception, match="fail"):
            await connected_provider.delete_nodes_and_edges(["k1"], "records")

        began = connected_provider.begin_transaction.await_args.kwargs
        assert set(began["write"]) == {"records", "permission", "belongsTo"}
        assert all(c.kwargs["txn_id"] == "own-txn" for c in connected_provider.http_client.execute_aql.await_args_list)
        connected_provider.rollback_transaction.assert_awaited_once_with("own-txn")
        connected_provider.commit_transaction.assert_not_awaited()
        connected_provider.delete_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_a_transaction_a_successful_delete_is_committed(
        self, connected_provider: ArangoHTTPProvider
    ) -> None:
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [{"collection": "permission"}]}
        })
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider.begin_transaction = AsyncMock(return_value="own-txn")
        connected_provider.commit_transaction = AsyncMock()
        connected_provider.rollback_transaction = AsyncMock()
        connected_provider.delete_nodes = AsyncMock()

        await connected_provider.delete_nodes_and_edges(["k1"], "records")

        connected_provider.delete_nodes.assert_awaited_once_with(["k1"], "records", "own-txn")
        connected_provider.commit_transaction.assert_awaited_once_with("own-txn")
        connected_provider.rollback_transaction.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_given_transaction_is_used_and_left_open(self, connected_provider: ArangoHTTPProvider) -> None:
        connected_provider.http_client.get_graph = AsyncMock(return_value={
            "graph": {"edgeDefinitions": [{"collection": "permission"}]}
        })
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        connected_provider.begin_transaction = AsyncMock()
        connected_provider.commit_transaction = AsyncMock()
        connected_provider.delete_nodes = AsyncMock()

        await connected_provider.delete_nodes_and_edges(["k1"], "records", transaction="theirs")

        connected_provider.begin_transaction.assert_not_awaited()
        connected_provider.commit_transaction.assert_not_awaited()
        connected_provider.delete_nodes.assert_awaited_once_with(["k1"], "records", "theirs")


# ===========================================================================
# Update Edge (lines 9998-10027)
# ===========================================================================

class TestUpdateEdge:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"role": "OWNER"}])
        result = await connected_provider.update_edge("from1", "to1", {"role": "OWNER"}, "permission")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.update_edge("from1", "to1", {"role": "OWNER"}, "permission")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.update_edge("from1", "to1", {"role": "OWNER"}, "permission")
        assert result is False


# ===========================================================================
# Check Record Permission (lines 10031-10061)
# ===========================================================================

class TestCheckRecordPermission:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["OWNER"])
        result = await connected_provider._check_record_permission("rec1", "user1")
        assert result == "OWNER"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider._check_record_permission("rec1", "user1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider._check_record_permission("rec1", "user1")
        assert result is None


# ===========================================================================
# Check Drive Permissions (lines 10063-10135)
# ===========================================================================

class TestCheckDrivePermissions:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["READER"])
        result = await connected_provider._check_drive_permissions("rec1", "user1")
        assert result == "READER"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider._check_drive_permissions("rec1", "user1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider._check_drive_permissions("rec1", "user1")
        assert result is None


# ===========================================================================
# Check Gmail Permissions (lines 10137-10206)
# ===========================================================================

class TestCheckGmailPermissions:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["WRITER"])
        result = await connected_provider._check_gmail_permissions("rec1", "user1")
        assert result == "WRITER"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider._check_gmail_permissions("rec1", "user1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider._check_gmail_permissions("rec1", "user1")
        assert result is None


# ===========================================================================
# Get KB Context for Record (lines 10208-10248)
# ===========================================================================

class TestGetKBContextForRecord:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"kb_id": "kb1", "kb_name": "MyKB", "org_id": "org1"}]
        )
        result = await connected_provider._get_kb_context_for_record("rec1")
        assert result["kb_id"] == "kb1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider._get_kb_context_for_record("rec1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider._get_kb_context_for_record("rec1")
        assert result is None


# ===========================================================================
# Get User KB Permission (lines 10250-10338)
# ===========================================================================

class TestGetUserKBPermission:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["OWNER"])
        result = await connected_provider.get_user_kb_permission("kb1", "user1")
        assert result == "OWNER"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_user_kb_permission("kb1", "user1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.get_user_kb_permission("kb1", "user1")


# ===========================================================================
# List User Knowledge Bases (lines 10340-10683)
# ===========================================================================

class TestListUserKnowledgeBases:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [{"id": "kb1", "name": "KB1", "userRole": "OWNER"}],
            [1],
            [{"permission": "OWNER", "kb_name": "KB1"}],
        ])
        kbs, count, filters = await connected_provider.list_user_knowledge_bases("u1", "org1", 0, 10)
        assert len(kbs) == 1
        assert count == 1

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[[], [0], []])
        kbs, count, filters = await connected_provider.list_user_knowledge_bases(
            "u1", "org1", 0, 10, search="test"
        )
        assert kbs == []
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.list_user_knowledge_bases("u1", "org1", 0, 10)


# ===========================================================================
# Get KB Children (lines 10685-10965)
# ===========================================================================

class TestGetKbChildren:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "success": True, "container": {"id": "kb1"}, "folders": [], "records": [],
            "counts": {"totalItems": 0}, "level": 1, "totalCount": 0,
            "availableFilters": {}, "paginationMode": "folders_first"
        }])
        result = await connected_provider.get_kb_children("kb1", 0, 10)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_kb_children("kb1", 0, 10)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_kb_children("kb1", 0, 10)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_filters(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "success": True, "container": {}, "folders": [], "records": [],
            "counts": {"totalItems": 0}, "level": 1, "totalCount": 0,
            "availableFilters": {}, "paginationMode": "folders_first"
        }])
        result = await connected_provider.get_kb_children(
            "kb1", 0, 10, search="test", record_types=["FILE"], origins=["UPLOAD"],
            connectors=["KB"], indexing_status=["COMPLETED"]
        )
        assert result["success"] is True


class TestGetFolderChildren:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "success": True, "container": {}, "folders": [], "records": [],
            "counts": {"totalItems": 0}, "level": 1, "totalCount": 0,
            "availableFilters": {}, "paginationMode": "folders_first"
        }])
        result = await connected_provider.get_folder_children("kb1", "f1", 0, 10)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_folder_children("kb1", "f1", 0, 10)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_folder_children("kb1", "f1", 0, 10)
        assert result["success"] is False


# ===========================================================================
# Normalize Name helpers (lines 11249-11265)
# ===========================================================================

class TestNormalizeName:
    def test_normal(self, provider):
        assert provider._normalize_name("  hello  ") == "hello"

    def test_none(self, provider):
        assert provider._normalize_name(None) is None

    def test_unicode_nfc(self, provider):
        name = unicodedata.normalize("NFD", "café")
        result = provider._normalize_name(name)
        assert result == unicodedata.normalize("NFC", name)


class TestNormalizedNameVariantsLower:
    def test_returns_two_variants(self, provider):
        variants = provider._normalized_name_variants_lower("Hello")
        assert len(variants) == 2
        assert all(v == v.lower() for v in variants)


# ===========================================================================
# Fetch Existing File Names In Parent (lines 11267-11333)
# ===========================================================================

class TestFetchExistingFileNamesInParent:
    @pytest.mark.asyncio
    async def test_with_parent(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"name_lower": "file.pdf", "mime_type": "application/pdf"}
        ])
        result = await connected_provider._fetch_existing_file_names_in_parent("kb1", "parent1")
        assert ("file.pdf", "application/pdf") in result

    @pytest.mark.asyncio
    async def test_at_root(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._fetch_existing_file_names_in_parent("kb1", None)
        assert result == set()

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider._fetch_existing_file_names_in_parent("kb1", "p1")
        assert result == set()


# ===========================================================================
# KB Exists (lines 11335-11356)
# ===========================================================================

class TestKbExists:
    @pytest.mark.asyncio
    async def test_exists(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[1])
        assert await connected_provider.kb_exists("kb1") is True

    @pytest.mark.asyncio
    async def test_not_exists(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        assert await connected_provider.kb_exists("kb1") is False


# ===========================================================================
# Get Knowledge Base (lines 11358-11419)
# ===========================================================================

class TestGetKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        connected_provider.execute_query = AsyncMock(return_value=[{
            "id": "kb1", "name": "KB1", "userRole": "OWNER"
        }])
        result = await connected_provider.get_knowledge_base("kb1", "u1")
        assert result["id"] == "kb1"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value=None)
        connected_provider.execute_query = AsyncMock(return_value=[{"id": "kb1", "name": "KB1"}])
        result = await connected_provider.get_knowledge_base("kb1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_knowledge_base("kb1", "u1")
        assert result is None


# ===========================================================================
# Update Knowledge Base (lines 11421-11453)
# ===========================================================================

class TestUpdateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"id": "kb1"}])
        result = await connected_provider.update_knowledge_base("kb1", {"name": "New"})
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.update_knowledge_base("kb1", {"name": "New"})
        assert result is False


# ===========================================================================
# Create Deleted Record Event Payload (lines 11459-11484)
# ===========================================================================

class TestCreateDeletedRecordEventPayload:
    @pytest.mark.asyncio
    async def test_with_file_record(self, connected_provider):
        record = {"orgId": "o1", "_key": "r1", "version": 1}
        file_rec = {"extension": ".pdf", "mimeType": "application/pdf"}
        result = await connected_provider._create_deleted_record_event_payload(record, file_rec)
        assert result["extension"] == ".pdf"
        assert result["orgId"] == "o1"

    @pytest.mark.asyncio
    async def test_without_file_record(self, connected_provider):
        record = {"orgId": "o1", "_key": "r1"}
        result = await connected_provider._create_deleted_record_event_payload(record)
        assert result["extension"] == ""


# ===========================================================================
# Validate Folder Creation (lines 11566-11595)
# ===========================================================================

class TestValidateFolderCreation:
    @pytest.mark.asyncio
    async def test_valid(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        connected_provider.kb_exists = AsyncMock(return_value=True)
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_kb_not_found(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        connected_provider.kb_exists = AsyncMock(return_value=False)
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        connected_provider.kb_exists = AsyncMock(return_value=True)
        connected_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        connected_provider._fetch_kb_name = AsyncMock(return_value="TestKB")
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_no_permission_at_all(self, connected_provider):
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        connected_provider.kb_exists = AsyncMock(return_value=True)
        connected_provider.get_user_kb_permission = AsyncMock(return_value=None)
        result = await connected_provider._validate_folder_creation("kb1", "u1")
        assert result["valid"] is False
        assert result["code"] == 404


# ===========================================================================
# Find Folder / File By Name (lines 11597-11782)
# ===========================================================================

class TestFindFolderByNameInParent:
    @pytest.mark.asyncio
    async def test_found_at_root(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "f1", "name": "Folder"}])
        result = await connected_provider.find_folder_by_name_in_parent("kb1", "Folder")
        assert result["_key"] == "f1"

    @pytest.mark.asyncio
    async def test_found_in_parent(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "f2"}])
        result = await connected_provider.find_folder_by_name_in_parent("kb1", "Sub", parent_folder_id="f1")
        assert result["_key"] == "f2"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.find_folder_by_name_in_parent("kb1", "None")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.find_folder_by_name_in_parent("kb1", "Folder")
        assert result is None


class TestFindFileByNameInParent:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "r1"}])
        result = await connected_provider.find_file_by_name_in_parent("kb1", "file.pdf", "application/pdf")
        assert result["_key"] == "r1"

    @pytest.mark.asyncio
    async def test_in_folder(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "r2"}])
        result = await connected_provider.find_file_by_name_in_parent(
            "kb1", "file.pdf", "application/pdf", parent_folder_id="f1"
        )
        assert result["_key"] == "r2"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.find_file_by_name_in_parent("kb1", "x.pdf", "app/pdf")
        assert result is None


# ===========================================================================
# Get And Validate Folder In KB (lines 11784-11837)
# ===========================================================================

class TestGetAndValidateFolderInKb:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "f1", "name": "Folder"}])
        result = await connected_provider.get_and_validate_folder_in_kb("kb1", "f1")
        assert result["_key"] == "f1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_and_validate_folder_in_kb("kb1", "f1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_and_validate_folder_in_kb("kb1", "f1")
        assert result is None


# ===========================================================================
# Validate Folder In KB (lines 11857-11950)
# ===========================================================================

class TestValidateFolderInKb:
    @pytest.mark.asyncio
    async def test_valid(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[True])
        assert await connected_provider.validate_folder_in_kb("kb1", "f1") is True

    @pytest.mark.asyncio
    async def test_invalid(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[False])
        assert await connected_provider.validate_folder_in_kb("kb1", "f1") is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        assert await connected_provider.validate_folder_in_kb("kb1", "f1") is False


class TestValidateFolderExistsInKb:
    @pytest.mark.asyncio
    async def test_valid(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[True])
        assert await connected_provider.validate_folder_exists_in_kb("kb1", "f1") is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        assert await connected_provider.validate_folder_exists_in_kb("kb1", "f1") is False


# ===========================================================================
# Knowledge Hub Root Nodes (lines 13682-13786)
# ===========================================================================

# ===========================================================================
# Knowledge Hub Children (lines 13788-13880)
# ===========================================================================

# ===========================================================================
# Knowledge Hub Static Helpers (lines 13883-13997)
# ===========================================================================

class TestKnowledgeHubTimestampExprs:
    def test_rg_created(self):
        expr = ArangoHTTPProvider._knowledge_hub_rg_projected_created_at_expr("r")
        assert "r_parent_app" in expr

    def test_rg_updated(self):
        expr = ArangoHTTPProvider._knowledge_hub_rg_projected_updated_at_expr("r")
        assert "sourceLastModifiedTimestamp" in expr

    def test_record_created(self):
        expr = ArangoHTTPProvider._knowledge_hub_record_projected_created_at_expr("r")
        assert "sourceCreatedAtTimestamp" in expr

    def test_record_updated(self):
        expr = ArangoHTTPProvider._knowledge_hub_record_projected_updated_at_expr("r")
        assert "sourceLastModifiedTimestamp" in expr


# ===========================================================================
# Traversal / AQL Builder Helpers (lines 13953-14100)
# ===========================================================================

# ===========================================================================
# Knowledge Hub Breadcrumbs (lines 14812-14979)
# ===========================================================================

class TestGetKnowledgeHubBreadcrumbs:
    @pytest.mark.asyncio
    async def test_single_node(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[
            [{"id": "n1", "name": "Node1", "nodeType": "app", "parentId": None}],
        ])
        connected_provider.check_access = AsyncMock(
            side_effect=lambda user_key, org_id, *, node_ids=(), **k: AccessCheck(node_ids=frozenset(node_ids))
        )
        crumbs = await connected_provider.get_knowledge_hub_breadcrumbs("n1", "u1", "org1")
        assert len(crumbs) == 1
        assert crumbs[0]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_chain(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[
            [{"id": "n2", "name": "Child", "nodeType": "record", "parentId": "n1"}],
            [{"id": "n1", "name": "Parent", "nodeType": "app", "parentId": None}],
        ])
        connected_provider.check_access = AsyncMock(
            side_effect=lambda user_key, org_id, *, node_ids=(), **k: AccessCheck(node_ids=frozenset(node_ids))
        )
        crumbs = await connected_provider.get_knowledge_hub_breadcrumbs("n2", "u1", "org1")
        assert len(crumbs) == 2
        assert crumbs[0]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        connected_provider.check_access = AsyncMock(
            side_effect=lambda user_key, org_id, *, node_ids=(), **k: AccessCheck(node_ids=frozenset(node_ids))
        )
        crumbs = await connected_provider.get_knowledge_hub_breadcrumbs("n1", "u1", "org1")
        assert crumbs == []


# ===========================================================================
# Filter Nodes With Permission Role (lines 14981-15064)
# ===========================================================================

# ===========================================================================
# Get Record Parent Adjacency (lines 15066-15202)
# ===========================================================================

class TestGetRecordParentAdjacency:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[
            {"id": "r1", "type": "record", "name": "Rec1", "parents": []}
        ])
        result = await connected_provider.get_record_parent_adjacency(["r1"], "org1")
        assert "r1" in result["nodes"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        result = await connected_provider.get_record_parent_adjacency([], "org1")
        assert result == {"nodes": {}, "parents": {}}

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_parent_adjacency(["r1"], "org1")
        assert result == {"nodes": {}, "parents": {}}


# ===========================================================================
# Get User App IDs / Permission App IDs (lines 15204-15270)
# ===========================================================================

class TestGetUserAppIds:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        """The user-app relation, direct or through a team: an extension hook,
        not the gate, as on Neo4j."""
        connected_provider.execute_query = AsyncMock(return_value=["a1", "a2"])
        result = await connected_provider.get_user_app_ids("u1")
        assert result == ["a1", "a2"]
        assert connected_provider.execute_query.await_args.kwargs["bind_vars"] == {"user_from": "users/u1"}

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_user_app_ids("u1")
        assert result == []


class TestGetUserPermissionAppIds:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[["a1", "a2"]])
        result = await connected_provider.get_user_permission_app_ids("u1", "org1")
        assert "a1" in result

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[[]])
        result = await connected_provider.get_user_permission_app_ids("u1", "org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_user_permission_app_ids("u1", "org1")
        assert result == []


# ===========================================================================
# Knowledge Hub Context Permissions (lines 15272-15392)
# ===========================================================================

class TestGetKnowledgeHubContextPermissions:
    @pytest.mark.asyncio
    async def test_no_parent(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"role": "ADMIN", "canUpload": True, "canCreateFolders": True,
                           "canEdit": True, "canDelete": True, "canManagePermissions": True}]
        )
        result = await connected_provider.get_knowledge_hub_context_permissions("u1", "org1", None)
        assert result["role"] == "ADMIN"

    @pytest.mark.asyncio
    async def test_record_parent(self, connected_provider):
        connected_provider._get_permission_role_aql = MagicMock(return_value="LET permission_role = 'OWNER'")
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"role": "OWNER", "canUpload": True, "canCreateFolders": True,
                           "canEdit": True, "canDelete": True, "canManagePermissions": True}]
        )
        result = await connected_provider.get_knowledge_hub_context_permissions(
            "u1", "org1", "r1", parent_type="record"
        )
        assert result["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_invalid_parent_type(self, connected_provider):
        with pytest.raises(ValueError):
            await connected_provider.get_knowledge_hub_context_permissions(
                "u1", "org1", "x1", parent_type="banana"
            )

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_knowledge_hub_context_permissions("u1", "org1", None)
        assert result["canUpload"] is False


# ===========================================================================
# Get Knowledge Hub Node Info (lines 15394-15429)
# ===========================================================================

# ===========================================================================
# Get Knowledge Hub Node Access (lines 15431-15555)
# ===========================================================================

class TestGetKnowledgeHubNodeAccess:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"n1"})))
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"result": {"id": "n1", "nodeType": "record"}, "kbId": "kb1"}]
        )
        connected_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        result = await connected_provider.get_knowledge_hub_node_access("n1", "u1", "org1", [])
        assert result["userRole"] == "OWNER"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"n1"})))
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_knowledge_hub_node_access("n1", "u1", "org1", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_a_failed_check_raises(self, connected_provider):
        """The check runs first; a graph that cannot answer it is not a node
        that does not exist."""
        from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError

        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(PermissionVerificationUnavailableError):
            await connected_provider.get_knowledge_hub_node_access("n1", "u1", "org1", [])


# ===========================================================================
# Get Linked Records (lines 15557-15636)
# ===========================================================================

class TestGetLinkedRecords:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"id": "r2", "name": "Linked"}, {"id": "r3", "name": "Hidden"}]
        )
        connected_provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"r2"})))
        result = await connected_provider.get_linked_records("r1", "org1", "u1", ["RELATED"])
        assert [r["id"] for r in result] == ["r2"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_linked_records("r1", "org1", "u1", ["RELATED"])
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_linked_records("r1", "org1", "u1", ["RELATED"])
        assert result == []


# ===========================================================================
# Get Knowledge Hub Parent Node (lines 15638-15756)
# ===========================================================================

class TestGetKnowledgeHubParentNode:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"id": "p1", "name": "Parent", "nodeType": "app"}]
        )
        result = await connected_provider.get_knowledge_hub_parent_node("n1", [])
        assert result["id"] == "p1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_knowledge_hub_parent_node("n1", [])
        assert result is None


# ===========================================================================
# Get Knowledge Hub Filter Options (lines 15758-15789)
# ===========================================================================

class TestGetKnowledgeHubFilterOptions:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_gated_apps = AsyncMock(return_value=[
            {"_key": "a1", "name": "Drive", "type": "DRIVE"},
            {"_key": "a2", "name": "KB", "type": "KB"},
        ])
        result = await connected_provider.get_knowledge_hub_filter_options("u1", "org1")
        assert len(result["apps"]) == 1
        assert result["apps"][0]["type"] == "DRIVE"

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_gated_apps = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_knowledge_hub_filter_options("u1", "org1")
        assert result == {"apps": []}


# ===========================================================================
# Get Account Type (lines 16250-16298)
# ===========================================================================

class TestGetAccountType:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["ENTERPRISE"])
        result = await connected_provider.get_account_type("org1")
        assert result == "ENTERPRISE"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_account_type("org1")
        assert result is None

    @pytest.mark.asyncio
    async def test_external(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["INDIVIDUAL"])
        result = await connected_provider.get_account_type("org1", is_external=True)
        assert result == "INDIVIDUAL"

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_account_type("org1")
        assert result is None


# ===========================================================================
# Get Connector Stats (lines 16300-16395)
# ===========================================================================

class TestGetConnectorStats:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"type": "DRIVE"})
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 5}
        ])
        with patch("app.services.graph_db.arango.arango_http_provider.build_connector_stats_response",
                    return_value={"totalRecords": 5}):
            result = await connected_provider.get_connector_stats("org1", "conn1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_connector_stats("org1", "conn1")
        assert result["success"] is False


# ===========================================================================
# Subquery Builders (lines 16397-17121)
# ===========================================================================

# ===========================================================================
# Knowledge Hub Filter Conditions Builder (lines 17123-17230)
# ===========================================================================

# ===========================================================================
# Permission Role AQL Builders (lines 17232-17711)
# ===========================================================================

class TestGetPermissionRoleAql:
    def test_record_type(self, provider):
        aql = provider._get_permission_role_aql("record", "rec", "u")
        assert "permission_role" in aql
        assert "inheritPermissions" in aql

    def test_record_group_type(self, provider):
        aql = provider._get_permission_role_aql("recordGroup", "rg", "u")
        assert "permission_role" in aql

    def test_kb_type(self, provider):
        aql = provider._get_permission_role_aql("kb", "rg", "u")
        assert "permission_role" in aql

    def test_app_type(self, provider):
        aql = provider._get_permission_role_aql("app", "app", "u")
        assert "permission_role" in aql
        assert "userAppRelation" in aql

    def test_invalid_type(self, provider):
        with pytest.raises(ValueError, match="Unsupported node_type"):
            provider._get_permission_role_aql("invalid")


# ===========================================================================
# Scope Filters (lines 17713-17832)
# ===========================================================================

# ===========================================================================
# Children Intersection AQL (lines 17834-17965)
# ===========================================================================

# ===========================================================================
# Move Record Methods (lines 17967-18155)
# ===========================================================================

class TestIsRecordDescendantOf:
    @pytest.mark.asyncio
    async def test_is_descendant(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[1])
        assert await connected_provider.is_record_descendant_of("child", "parent") is True

    @pytest.mark.asyncio
    async def test_not_descendant(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        assert await connected_provider.is_record_descendant_of("child", "parent") is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        assert await connected_provider.is_record_descendant_of("child", "parent") is False


class TestGetRecordParentInfo:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"id": "p1", "type": "record"}]
        )
        result = await connected_provider.get_record_parent_info("r1")
        assert result["id"] == "p1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await connected_provider.get_record_parent_info("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_record_parent_info("r1")
        assert result is None


class TestDeleteParentChildEdgeToRecord:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        count = await connected_provider.delete_parent_child_edge_to_record("r1")
        assert count == 2

    @pytest.mark.asyncio
    async def test_none(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        count = await connected_provider.delete_parent_child_edge_to_record("r1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception_no_txn(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        count = await connected_provider.delete_parent_child_edge_to_record("r1")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception_with_txn_raises(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.delete_parent_child_edge_to_record("r1", transaction="t1")


class TestIsRecordFolder:
    @pytest.mark.asyncio
    async def test_is_folder(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[True])
        assert await connected_provider.is_record_folder("r1") is True

    @pytest.mark.asyncio
    async def test_not_folder(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[False])
        assert await connected_provider.is_record_folder("r1") is False

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        assert await connected_provider.is_record_folder("r1") is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        assert await connected_provider.is_record_folder("r1") is False


# ===========================================================================
# Duplicate Detection (lines 18157-18404)
# ===========================================================================

class TestFindDuplicateRecords:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(
            return_value=[{"_key": "r2", "md5Checksum": "abc"}]
        )
        result = await connected_provider.find_duplicate_records("r1", "abc", org_id="org-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_filters(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.find_duplicate_records(
            "r1", "abc", org_id="org-1", record_type="FILE", size_in_bytes=1000
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.find_duplicate_records("r1", "abc", org_id="org-1")
        assert result == []


class TestFindNextQueuedDuplicate:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[
            [{"_key": "r1", "md5Checksum": "abc", "sizeInBytes": 100, "orgId": "org-1"}],
            [{"_key": "r2", "indexingStatus": "QUEUED"}],
        ])
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result["_key"] == "r2"

    @pytest.mark.asyncio
    async def test_no_ref_record(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_checksum(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[
            [{"_key": "r1"}],
        ])
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.find_next_queued_duplicate("r1")
        assert result is None


class TestCopyDocumentRelationships:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[
            {"from": "records/s1", "to": "departments/d1", "timestamp": 123}
        ])
        connected_provider.http_client.create_document = AsyncMock()
        result = await connected_provider.copy_document_relationships("s1", "t1")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_edges(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.copy_document_relationships("s1", "t1")
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.copy_document_relationships("s1", "t1")
        assert result is False


# ===========================================================================
# Append Source Created Time Filters (lines 18406-18454)
# ===========================================================================

class TestAppendSourceCreatedTimeFilters:
    def test_no_time_range(self):
        result = ArangoHTTPProvider._append_source_created_time_filters("", None, {})
        assert result == ""

    def test_created_after(self):
        bv = {}
        result = ArangoHTTPProvider._append_source_created_time_filters(
            "", {"source_created_after_ms": 1000}, bv
        )
        assert "sourceCreatedAfterMs" in bv
        assert "sourceCreatedAtTimestamp" in result

    def test_all_filters(self):
        bv = {}
        tr = {
            "source_created_after_ms": 100,
            "source_created_before_ms": 200,
            "source_updated_after_ms": 300,
            "source_updated_before_ms": 400,
        }
        result = ArangoHTTPProvider._append_source_created_time_filters("existing", tr, bv)
        assert len(bv) == 4
        assert "existing" in result

    def test_appends_to_existing(self):
        bv = {}
        result = ArangoHTTPProvider._append_source_created_time_filters(
            "FILTER doc.x == 1", {"source_created_after_ms": 1000}, bv
        )
        assert "FILTER doc.x == 1" in result
        assert "sourceCreatedAtTimestamp" in result


# ===========================================================================
# Get Records By Record IDs (lines 19035-19078)
# ===========================================================================

class TestGetRecordsByRecordIds:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "r1"}, {"_key": "r2"}])
        result = await connected_provider.get_records_by_record_ids(["r1", "r2"], "org1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_input(self, connected_provider):
        result = await connected_provider.get_records_by_record_ids([], "org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_records_by_record_ids(["r1"], "org1")
        assert result == []


# ===========================================================================
# Team Methods (lines 19200-19700)
# ===========================================================================

class TestGetTeamWithUsers:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "id": "t1", "name": "Team1", "members": [{"id": "u1"}], "memberCount": 1,
            "canEdit": True, "canDelete": True, "canManageMembers": True
        }])
        connected_provider._enrich_created_by_user = AsyncMock()
        result = await connected_provider.get_team_with_users("t1", "u1")
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_with_users("t1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_team_with_users("t1", "u1")
        assert result is None


class TestGetUserTeams:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [2],
            [{"id": "t1", "name": "Team1"}, {"id": "t2", "name": "Team2"}],
        ])
        connected_provider._enrich_created_by_user = AsyncMock()
        teams, count = await connected_provider.get_user_teams("u1")
        assert count == 2
        assert len(teams) == 2

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[[0], []])
        connected_provider._enrich_created_by_user = AsyncMock()
        teams, count = await connected_provider.get_user_teams("u1", search="dev")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        teams, count = await connected_provider.get_user_teams("u1")
        assert teams == []
        assert count == 0


class TestGetTeamUsers:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "id": "t1", "name": "Team1", "members": [{"id": "u1"}], "memberCount": 1,
            "canEdit": True, "canDelete": True, "canManageMembers": True
        }])
        connected_provider._enrich_created_by_user = AsyncMock()
        result = await connected_provider.get_team_users("t1", "org1", "u1")
        assert result["id"] == "t1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_users("t1", "org1", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_team_users("t1", "org1", "u1")
        assert result is None


class TestDeleteTeamMemberEdges:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "e1"}])
        result = await connected_provider.delete_team_member_edges("t1", ["u1"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.delete_team_member_edges("t1", ["u1"])
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.delete_team_member_edges("t1", ["u1"])
        assert result == []


class TestBatchUpdateTeamMemberRoles:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"role": "OWNER"}])
        result = await connected_provider.batch_update_team_member_roles(
            "t1", [{"userId": "u1", "role": "OWNER"}], 123
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.batch_update_team_member_roles("t1", [], 123)
        assert result == []


class TestDeleteAllTeamPermissions:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        await connected_provider.delete_all_team_permissions("t1")

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.delete_all_team_permissions("t1")


class TestGetTeamOwnerRemovalInfo:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "ownersBeingRemoved": ["u1"], "totalOwnerCount": 2
        }])
        result = await connected_provider.get_team_owner_removal_info("t1", ["u1"])
        assert result["total_owner_count"] == 2
        assert "u1" in result["owners_being_removed"]

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_owner_removal_info("t1", ["u1"])
        assert result["total_owner_count"] == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.get_team_owner_removal_info("t1", ["u1"])


class TestGetTeamPermissionsAndOwnerCount:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "team": {"_key": "t1"}, "permissions": [{"userId": "u1", "role": "OWNER"}], "ownerCount": 1
        }])
        result = await connected_provider.get_team_permissions_and_owner_count("t1", ["u1"])
        assert result["owner_count"] == 1
        assert result["permissions"]["u1"] == "OWNER"

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_team_permissions_and_owner_count("t1", ["u1"])
        assert result["team"] is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.get_team_permissions_and_owner_count("t1", ["u1"])


# ===========================================================================
# Organization Users (lines 19703-19781)
# ===========================================================================

class TestGetOrganizationUsers:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [5],
            [{"id": "u1", "name": "User1"}],
        ])
        users, count = await connected_provider.get_organization_users("org1")
        assert count == 5
        assert len(users) == 1

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[[0], []])
        users, count = await connected_provider.get_organization_users("org1", search="john")
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        users, count = await connected_provider.get_organization_users("org1")
        assert users == []
        assert count == 0


# ===========================================================================
# Check Toolset Instance / Connector In Use (lines 19783-19874)
# ===========================================================================

class TestCheckToolsetInstanceInUse:
    @pytest.mark.asyncio
    async def test_in_use(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[
            ["agentToolsets/ts1"],
            [{"agentId": "a1", "agentName": "Agent1"}],
        ])
        with patch("app.services.graph_db.arango.arango_http_provider.dedupe_agents_by_id",
                    return_value=["Agent1"]):
            result = await connected_provider.check_toolset_instance_in_use("inst1")
        assert "Agent1" in result

    @pytest.mark.asyncio
    async def test_not_in_use(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.check_toolset_instance_in_use("inst1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connected_provider.check_toolset_instance_in_use("inst1")


class TestCheckConnectorInUse:
    @pytest.mark.asyncio
    async def test_in_use(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[
            ["agentKnowledge/k1"],
            [{"agentId": "a1", "agentName": "Agent1"}],
        ])
        with patch("app.services.graph_db.arango.arango_http_provider.dedupe_agents_by_id",
                    return_value=["Agent1"]):
            result = await connected_provider.check_connector_in_use("conn1")
        assert "Agent1" in result

    @pytest.mark.asyncio
    async def test_not_in_use(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.check_connector_in_use("conn1")
        assert result == []


# ===========================================================================
# Get Agent (lines 19876-20089)
# ===========================================================================

class TestGetAgent:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "_key": "a1", "name": "Agent1", "toolsets": [], "mcpServers": [],
            "knowledge": [], "skills": [], "shareWithOrg": False
        }])
        result = await connected_provider.get_agent("a1", "org1")
        assert result["name"] == "Agent1"

    @pytest.mark.asyncio
    async def test_with_knowledge_filters_json_str(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "_key": "a1", "name": "Agent1", "toolsets": [], "mcpServers": [],
            "knowledge": [{"_key": "k1", "filters": '{"depts": ["HR"]}', "name": "KB1"}],
            "skills": [], "shareWithOrg": False
        }])
        result = await connected_provider.get_agent("a1")
        assert result["knowledge"][0]["filtersParsed"] == {"depts": ["HR"]}

    @pytest.mark.asyncio
    async def test_knowledge_filters_none(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "_key": "a1", "name": "Agent1", "toolsets": [], "mcpServers": [],
            "knowledge": [{"_key": "k1", "filters": None, "name": "KB1"}],
            "skills": [], "shareWithOrg": False
        }])
        result = await connected_provider.get_agent("a1")
        assert result["knowledge"][0]["filtersParsed"] == {}

    @pytest.mark.asyncio
    async def test_knowledge_filters_dict(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "_key": "a1", "name": "Agent1", "toolsets": [], "mcpServers": [],
            "knowledge": [{"_key": "k1", "filters": {"x": 1}, "name": "KB1"}],
            "skills": [], "shareWithOrg": False
        }])
        result = await connected_provider.get_agent("a1")
        assert result["knowledge"][0]["filtersParsed"] == {"x": 1}

    @pytest.mark.asyncio
    async def test_knowledge_filters_invalid_json(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "_key": "a1", "name": "Agent1", "toolsets": [], "mcpServers": [],
            "knowledge": [{"_key": "k1", "filters": "not-json", "name": "KB1"}],
            "skills": [], "shareWithOrg": False
        }])
        result = await connected_provider.get_agent("a1")
        assert result["knowledge"][0]["filtersParsed"] == {}

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_agent("a1")
        assert result is None

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[None])
        result = await connected_provider.get_agent("a1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_agent("a1")
        assert result is None


# ===========================================================================
# Check Agent Permission (lines 20091-20184)
# ===========================================================================

class TestCheckAgentPermission:
    @pytest.mark.asyncio
    async def test_has_access(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "access_type": "INDIVIDUAL", "user_role": "OWNER",
            "can_edit": True, "can_delete": True, "can_share": True, "can_view": True
        }])
        result = await connected_provider.check_agent_permission("a1", "u1", "org1")
        assert result["access_type"] == "INDIVIDUAL"

    @pytest.mark.asyncio
    async def test_no_access(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[None])
        result = await connected_provider.check_agent_permission("a1", "u1", "org1")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.check_agent_permission("a1", "u1", "org1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.check_agent_permission("a1", "u1", "org1")
        assert result is None


# ===========================================================================
# Get Agents by Web Search Provider / Model Key (lines 20186-20305)
# ===========================================================================

class TestGetAgentsByWebSearchProvider:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"name": "Agent1", "_key": "a1", "creatorName": "User1"}
        ])
        result = await connected_provider.get_agents_by_web_search_provider("org1", "BRAVE")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_agents_by_web_search_provider("org1", "BRAVE")
        assert result == []

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=None)
        result = await connected_provider.get_agents_by_web_search_provider("org1", "BRAVE")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_agents_by_web_search_provider("org1", "BRAVE")
        assert result == []


class TestGetAgentsByModelKey:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"name": "Agent1", "_key": "a1"}
        ])
        result = await connected_provider.get_agents_by_model_key("org1", "gpt-4")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=None)
        result = await connected_provider.get_agents_by_model_key("org1", "gpt-4")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_agents_by_model_key("org1", "gpt-4")
        assert result == []
