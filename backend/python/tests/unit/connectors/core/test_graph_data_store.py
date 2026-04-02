"""Tests for GraphDataStore and GraphTransactionStore."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    GraphTransactionStore,
)


@pytest.fixture
def mock_graph_provider():
    """Create a mock IGraphDBProvider."""
    provider = MagicMock()
    provider.logger = logging.getLogger("test_graph")
    # Make all methods async
    provider.begin_transaction = AsyncMock(return_value="txn-123")
    provider.commit_transaction = AsyncMock()
    provider.rollback_transaction = AsyncMock()
    provider.get_document = AsyncMock(return_value=None)
    provider.get_record_by_external_id = AsyncMock(return_value=None)
    provider.get_record_by_external_revision_id = AsyncMock(return_value=None)
    provider.get_record_by_issue_key = AsyncMock(return_value=None)
    provider.get_records_by_status = AsyncMock(return_value=[])
    provider.get_record_group_by_external_id = AsyncMock(return_value=None)
    provider.get_record_by_path = AsyncMock(return_value=None)
    provider.get_file_record_by_id = AsyncMock(return_value=None)
    provider.get_record_group_by_id = AsyncMock(return_value=None)
    provider.create_record_groups_relation = AsyncMock()
    provider.get_user_by_email = AsyncMock(return_value=None)
    provider.get_user_by_source_id = AsyncMock(return_value=None)
    provider.get_app_user_by_email = AsyncMock(return_value=None)
    provider.get_record_owner_source_user_email = AsyncMock(return_value=None)
    provider.get_user_by_user_id = AsyncMock(return_value=None)
    provider.delete_nodes = AsyncMock()
    provider.delete_record_by_external_id = AsyncMock()
    provider.remove_user_access_to_record = AsyncMock()
    provider.delete_record_group_by_external_id = AsyncMock()
    provider.delete_edge = AsyncMock()
    provider.delete_edges_from = AsyncMock()
    provider.delete_edges_to = AsyncMock()
    provider.delete_parent_child_edge_to_record = AsyncMock(return_value=1)
    provider.delete_edges_to_groups = AsyncMock()
    provider.delete_edges_between_collections = AsyncMock()
    provider.delete_edges_by_relationship_types = AsyncMock(return_value=2)
    provider.delete_nodes_and_edges = AsyncMock()
    provider.get_user_group_by_external_id = AsyncMock(return_value=None)
    provider.get_app_role_by_external_id = AsyncMock(return_value=None)
    provider.get_users = AsyncMock(return_value=[])
    provider.get_app_users = AsyncMock(return_value=[])
    provider.get_user_groups = AsyncMock(return_value=[])
    provider.batch_upsert_people = AsyncMock()
    provider.batch_upsert_records = AsyncMock()
    provider.batch_upsert_record_groups = AsyncMock()
    provider.batch_upsert_record_permissions = AsyncMock()
    provider.batch_create_user_app_edges = AsyncMock(return_value=5)
    provider.batch_upsert_user_groups = AsyncMock()
    provider.batch_upsert_app_roles = AsyncMock()
    provider.batch_upsert_app_users = AsyncMock()
    provider.batch_upsert_orgs = AsyncMock()
    provider.batch_upsert_domains = AsyncMock()
    provider.batch_upsert_anyone = AsyncMock()
    provider.batch_upsert_anyone_with_link = AsyncMock()
    provider.batch_upsert_anyone_same_org = AsyncMock()
    provider.batch_upsert_nodes = AsyncMock()
    provider.batch_create_edges = AsyncMock()
    provider.batch_create_entity_relations = AsyncMock()
    provider.create_record_relation = AsyncMock()
    provider.create_record_group_relation = AsyncMock()
    provider.create_inherit_permissions_relation_record_group = AsyncMock()
    provider.delete_inherit_permissions_relation_record_group = AsyncMock()
    provider.get_sync_point = AsyncMock(return_value=None)
    provider.upsert_sync_point = AsyncMock()
    provider.remove_sync_point = AsyncMock()
    provider.get_all_orgs = AsyncMock(return_value=[])
    provider.get_record_by_conversation_index = AsyncMock(return_value=None)
    provider.get_record_by_weburl = AsyncMock(return_value=None)
    provider.get_records_by_parent = AsyncMock(return_value=[])
    provider.get_record_path = AsyncMock(return_value=None)
    provider.get_app_creator_user = AsyncMock(return_value=None)
    provider.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
    provider.get_users_with_permission_to_node = AsyncMock(return_value=[])
    provider.get_edges_to_node = AsyncMock(return_value=[])
    provider.get_edges_from_node = AsyncMock(return_value=[])
    provider.get_related_node_field = AsyncMock(return_value=[])
    provider.delete_records_and_relations = AsyncMock()
    provider.process_file_permissions = AsyncMock()
    provider.get_nodes_by_field_in = AsyncMock(return_value=[])
    provider.remove_nodes_by_field = AsyncMock(return_value=0)
    provider.get_nodes_by_filters = AsyncMock(return_value=[])
    return provider


class TestGraphTransactionStore:
    """Tests for GraphTransactionStore delegating to graph_provider."""

    @pytest.fixture
    def tx_store(self, mock_graph_provider):
        return GraphTransactionStore(mock_graph_provider, "txn-123")

    @pytest.mark.asyncio
    async def test_commit(self, tx_store, mock_graph_provider):
        await tx_store.commit()
        mock_graph_provider.commit_transaction.assert_awaited_once_with("txn-123")

    @pytest.mark.asyncio
    async def test_rollback(self, tx_store, mock_graph_provider):
        await tx_store.rollback()
        mock_graph_provider.rollback_transaction.assert_awaited_once_with("txn-123")

    @pytest.mark.asyncio
    async def test_get_record_by_key(self, tx_store, mock_graph_provider):
        await tx_store.get_record_by_key("key1")
        mock_graph_provider.get_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_record_by_external_id(self, tx_store, mock_graph_provider):
        await tx_store.get_record_by_external_id("conn1", "ext1")
        mock_graph_provider.get_record_by_external_id.assert_awaited_once_with("conn1", "ext1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_get_record_by_external_revision_id(self, tx_store, mock_graph_provider):
        await tx_store.get_record_by_external_revision_id("conn1", "rev1")
        mock_graph_provider.get_record_by_external_revision_id.assert_awaited_once_with("conn1", "rev1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_get_records_by_status(self, tx_store, mock_graph_provider):
        result = await tx_store.get_records_by_status("org1", "conn1", ["active"], limit=10, offset=0)
        assert result == []
        mock_graph_provider.get_records_by_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_batch_upsert_records(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_records([])
        mock_graph_provider.batch_upsert_records.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_record_groups(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_record_groups([])
        mock_graph_provider.batch_upsert_record_groups.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_record_permissions(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_record_permissions("rec1", [])
        mock_graph_provider.batch_upsert_record_permissions.assert_awaited_once_with("rec1", [], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_delete_record_by_key(self, tx_store, mock_graph_provider):
        await tx_store.delete_record_by_key("key1")
        mock_graph_provider.delete_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_edge(self, tx_store, mock_graph_provider):
        await tx_store.delete_edge("from1", "from_coll", "to1", "to_coll", "edge_coll")
        mock_graph_provider.delete_edge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_parent_child_edge_to_record(self, tx_store, mock_graph_provider):
        result = await tx_store.delete_parent_child_edge_to_record("rec1")
        assert result == 1
        mock_graph_provider.delete_parent_child_edge_to_record.assert_awaited_once_with("rec1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_delete_edges_by_relationship_types(self, tx_store, mock_graph_provider):
        result = await tx_store.delete_edges_by_relationship_types("from1", "from_coll", "edge_coll", ["TYPE_A"])
        assert result == 2

    @pytest.mark.asyncio
    async def test_get_user_by_email(self, tx_store, mock_graph_provider):
        result = await tx_store.get_user_by_email("test@example.com")
        assert result is None
        mock_graph_provider.get_user_by_email.assert_awaited_once_with("test@example.com", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_get_user_by_source_id(self, tx_store, mock_graph_provider):
        await tx_store.get_user_by_source_id("src1", "conn1")
        mock_graph_provider.get_user_by_source_id.assert_awaited_once_with("src1", "conn1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_create_record_relation(self, tx_store, mock_graph_provider):
        await tx_store.create_record_relation("from1", "to1", "BLOCKS")
        mock_graph_provider.create_record_relation.assert_awaited_once_with("from1", "to1", "BLOCKS", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_create_record_group_relation(self, tx_store, mock_graph_provider):
        await tx_store.create_record_group_relation("rec1", "grp1")
        mock_graph_provider.create_record_group_relation.assert_awaited_once_with("rec1", "grp1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_get_users_returns_typed_list(self, tx_store, mock_graph_provider):
        mock_graph_provider.get_users.return_value = []
        result = await tx_store.get_users("org1", active=True)
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_upsert_people(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_people([])
        mock_graph_provider.batch_upsert_people.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_orgs(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_orgs([])
        mock_graph_provider.batch_upsert_orgs.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_domains(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_domains([])
        mock_graph_provider.batch_upsert_domains.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_anyone(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_anyone([])
        mock_graph_provider.batch_upsert_anyone.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_anyone_with_link(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_anyone_with_link([])
        mock_graph_provider.batch_upsert_anyone_with_link.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_anyone_same_org(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_anyone_same_org([])
        mock_graph_provider.batch_upsert_anyone_same_org.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_create_sync_point(self, tx_store, mock_graph_provider):
        await tx_store.create_sync_point("sp1", {"data": "val"})
        mock_graph_provider.upsert_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_sync_point(self, tx_store, mock_graph_provider):
        await tx_store.delete_sync_point("sp1")
        mock_graph_provider.remove_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_read_sync_point(self, tx_store, mock_graph_provider):
        await tx_store.read_sync_point("sp1")
        mock_graph_provider.get_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_sync_point(self, tx_store, mock_graph_provider):
        await tx_store.update_sync_point("sp1", {"data": "new"})
        mock_graph_provider.upsert_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_record_by_issue_key(self, tx_store, mock_graph_provider):
        await tx_store.get_record_by_issue_key("conn1", "PROJ-123")
        mock_graph_provider.get_record_by_issue_key.assert_awaited_once_with("conn1", "PROJ-123", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_get_records_by_parent(self, tx_store, mock_graph_provider):
        await tx_store.get_records_by_parent("conn1", "parent1", record_type="file")
        mock_graph_provider.get_records_by_parent.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_record_path(self, tx_store, mock_graph_provider):
        await tx_store.get_record_path("rec1")
        mock_graph_provider.get_record_path.assert_awaited_once_with("rec1", transaction="txn-123")

    # --- Delegation methods that were previously uncovered ---

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_nodes([{"_key": "n1"}], "records")
        mock_graph_provider.batch_upsert_nodes.assert_awaited_once_with(
            [{"_key": "n1"}], "records", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_record_by_path(self, tx_store, mock_graph_provider):
        await tx_store.get_record_by_path("conn1", "/some/path")
        mock_graph_provider.get_record_by_path.assert_awaited_once_with(
            "conn1", "/some/path", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_record_group_by_external_id(self, tx_store, mock_graph_provider):
        await tx_store.get_record_group_by_external_id("conn1", "ext1")
        mock_graph_provider.get_record_group_by_external_id.assert_awaited_once_with(
            "conn1", "ext1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_file_record_by_id(self, tx_store, mock_graph_provider):
        await tx_store.get_file_record_by_id("file1")
        mock_graph_provider.get_file_record_by_id.assert_awaited_once_with("file1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_get_record_group_by_id(self, tx_store, mock_graph_provider):
        await tx_store.get_record_group_by_id("grp1")
        mock_graph_provider.get_record_group_by_id.assert_awaited_once_with("grp1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_create_record_groups_relation(self, tx_store, mock_graph_provider):
        await tx_store.create_record_groups_relation("child1", "parent1")
        mock_graph_provider.create_record_groups_relation.assert_awaited_once_with(
            "child1", "parent1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_app_user_by_email(self, tx_store, mock_graph_provider):
        await tx_store.get_app_user_by_email("test@test.com", "conn1")
        mock_graph_provider.get_app_user_by_email.assert_awaited_once_with(
            "test@test.com", "conn1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_record_owner_source_user_email(self, tx_store, mock_graph_provider):
        await tx_store.get_record_owner_source_user_email("rec1")
        mock_graph_provider.get_record_owner_source_user_email.assert_awaited_once_with(
            "rec1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_user_by_user_id(self, tx_store, mock_graph_provider):
        await tx_store.get_user_by_user_id("user1")
        mock_graph_provider.get_user_by_user_id.assert_awaited_once_with("user1")

    @pytest.mark.asyncio
    async def test_delete_record_by_external_id(self, tx_store, mock_graph_provider):
        await tx_store.delete_record_by_external_id("conn1", "ext1", "user1")
        mock_graph_provider.delete_record_by_external_id.assert_awaited_once_with("conn1", "ext1", "user1")

    @pytest.mark.asyncio
    async def test_remove_user_access_to_record(self, tx_store, mock_graph_provider):
        await tx_store.remove_user_access_to_record("conn1", "ext1", "user1")
        mock_graph_provider.remove_user_access_to_record.assert_awaited_once_with("conn1", "ext1", "user1")

    @pytest.mark.asyncio
    async def test_delete_record_group_by_external_id(self, tx_store, mock_graph_provider):
        await tx_store.delete_record_group_by_external_id("conn1", "ext1")
        mock_graph_provider.delete_record_group_by_external_id.assert_awaited_once_with(
            "conn1", "ext1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_nodes(self, tx_store, mock_graph_provider):
        await tx_store.delete_nodes(["k1", "k2"], "records")
        mock_graph_provider.delete_nodes.assert_awaited_with(["k1", "k2"], "records", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_delete_edges_from(self, tx_store, mock_graph_provider):
        await tx_store.delete_edges_from("from1", "from_coll", "edge_coll")
        mock_graph_provider.delete_edges_from.assert_awaited_once_with(
            "from1", "from_coll", "edge_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_edges_to(self, tx_store, mock_graph_provider):
        await tx_store.delete_edges_to("to1", "to_coll", "edge_coll")
        mock_graph_provider.delete_edges_to.assert_awaited_once_with(
            "to1", "to_coll", "edge_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_edges_to_groups(self, tx_store, mock_graph_provider):
        await tx_store.delete_edges_to_groups("from1", "from_coll", "edge_coll")
        mock_graph_provider.delete_edges_to_groups.assert_awaited_once_with(
            "from1", "from_coll", "edge_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_edges_between_collections(self, tx_store, mock_graph_provider):
        await tx_store.delete_edges_between_collections("from1", "from_coll", "edge_coll", "to_coll")
        mock_graph_provider.delete_edges_between_collections.assert_awaited_once_with(
            "from1", "from_coll", "edge_coll", "to_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_nodes_and_edges(self, tx_store, mock_graph_provider):
        await tx_store.delete_nodes_and_edges(["k1"], "records")
        mock_graph_provider.delete_nodes_and_edges.assert_awaited_once_with(
            ["k1"], "records", graph_name="knowledgeGraph", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_user_group_by_external_id(self, tx_store, mock_graph_provider):
        await tx_store.get_user_group_by_external_id("conn1", "ext1")
        mock_graph_provider.get_user_group_by_external_id.assert_awaited_once_with(
            "conn1", "ext1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_user_group_by_id(self, tx_store, mock_graph_provider):
        await tx_store.delete_user_group_by_id("grp1")
        mock_graph_provider.delete_nodes_and_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_app_role_by_external_id(self, tx_store, mock_graph_provider):
        await tx_store.get_app_role_by_external_id("conn1", "role1")
        mock_graph_provider.get_app_role_by_external_id.assert_awaited_once_with(
            "conn1", "role1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_users_with_data(self, tx_store, mock_graph_provider):
        mock_user = {"_key": "u1", "email": "test@test.com", "firstName": "Test", "lastName": "User",
                     "orgId": "org1", "status": "active", "createdAtTimestamp": 123, "updatedAtTimestamp": 123}
        mock_graph_provider.get_users.return_value = [mock_user]
        result = await tx_store.get_users("org1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_app_users_with_data(self, tx_store, mock_graph_provider):
        mock_app_user = {"_key": "au1", "email": "test@test.com", "firstName": "Test", "lastName": "User",
                         "fullName": "Test User", "orgId": "org1", "connectorId": "conn1",
                         "sourceUserId": "src1", "appName": "UNKNOWN",
                         "createdAtTimestamp": 123, "updatedAtTimestamp": 123}
        mock_graph_provider.get_app_users.return_value = [mock_app_user]
        result = await tx_store.get_app_users("org1", "conn1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_user_groups(self, tx_store, mock_graph_provider):
        await tx_store.get_user_groups("conn1", "org1")
        mock_graph_provider.get_user_groups.assert_awaited_once_with("conn1", "org1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_create_user_app_edges(self, tx_store, mock_graph_provider):
        result = await tx_store.batch_create_user_app_edges([{"edge": "data"}])
        assert result == 5
        mock_graph_provider.batch_create_user_app_edges.assert_awaited_once_with([{"edge": "data"}])

    @pytest.mark.asyncio
    async def test_batch_upsert_user_groups(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_user_groups([])
        mock_graph_provider.batch_upsert_user_groups.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_app_roles(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_app_roles([])
        mock_graph_provider.batch_upsert_app_roles.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_batch_upsert_app_users(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_app_users([])
        mock_graph_provider.batch_upsert_app_users.assert_awaited_once_with([], transaction="txn-123")

    @pytest.mark.asyncio
    async def test_get_first_user_with_permission_to_node(self, tx_store, mock_graph_provider):
        await tx_store.get_first_user_with_permission_to_node("node1", "records")
        mock_graph_provider.get_first_user_with_permission_to_node.assert_awaited_once_with(
            "node1", "records", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_users_with_permission_to_node(self, tx_store, mock_graph_provider):
        await tx_store.get_users_with_permission_to_node("node1", "records")
        mock_graph_provider.get_users_with_permission_to_node.assert_awaited_once_with(
            "node1", "records", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_edge(self, tx_store, mock_graph_provider):
        mock_graph_provider.get_edge = AsyncMock(return_value=None)
        await tx_store.get_edge("from1", "from_coll", "to1", "to_coll", "edge_coll")
        mock_graph_provider.get_edge.assert_awaited_once_with(
            "from1", "from_coll", "to1", "to_coll", "edge_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_record_by_conversation_index(self, tx_store, mock_graph_provider):
        await tx_store.get_record_by_conversation_index("conn1", "idx1", "thread1", "org1", "user1")
        mock_graph_provider.get_record_by_conversation_index.assert_awaited_once_with(
            "conn1", "idx1", "thread1", "org1", "user1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_record_by_weburl(self, tx_store, mock_graph_provider):
        await tx_store.get_record_by_weburl("https://example.com", org_id="org1")
        mock_graph_provider.get_record_by_weburl.assert_awaited_once_with(
            "https://example.com", "org1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_app_creator_user(self, tx_store, mock_graph_provider):
        await tx_store.get_app_creator_user("conn1")
        mock_graph_provider.get_app_creator_user.assert_awaited_once_with("conn1", transaction="txn-123")

    @pytest.mark.asyncio
    async def test_create_inherit_permissions_relation_record_group(self, tx_store, mock_graph_provider):
        await tx_store.create_inherit_permissions_relation_record_group("rec1", "grp1")
        mock_graph_provider.create_inherit_permissions_relation_record_group.assert_awaited_once_with(
            "rec1", "grp1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_inherit_permissions_relation_record_group(self, tx_store, mock_graph_provider):
        await tx_store.delete_inherit_permissions_relation_record_group("rec1", "grp1")
        mock_graph_provider.delete_inherit_permissions_relation_record_group.assert_awaited_once_with(
            "rec1", "grp1", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_create_inherit_permissions_relation_record(self, tx_store, mock_graph_provider):
        await tx_store.create_inherit_permissions_relation_record("child1", "parent1")
        mock_graph_provider.batch_create_edges.assert_awaited_once()
        call_args = mock_graph_provider.batch_create_edges.call_args
        edges = call_args[0][0]
        assert len(edges) == 1
        assert edges[0]["from_id"] == "child1"
        assert edges[0]["to_id"] == "parent1"

    @pytest.mark.asyncio
    async def test_get_sync_point(self, tx_store, mock_graph_provider):
        await tx_store.get_sync_point("sp1")
        mock_graph_provider.get_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_all_orgs(self, tx_store, mock_graph_provider):
        result = await tx_store.get_all_orgs()
        assert result == []
        mock_graph_provider.get_all_orgs.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_user_groups(self, tx_store, mock_graph_provider):
        group = MagicMock()
        group.to_arango_base_user_group.return_value = {"_key": "g1"}
        await tx_store.create_user_groups([group])
        mock_graph_provider.batch_upsert_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_users(self, tx_store, mock_graph_provider):
        user = MagicMock()
        user.to_arango_base_user.return_value = {"_key": "u1"}
        await tx_store.create_users([user])
        mock_graph_provider.batch_upsert_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_orgs(self, tx_store, mock_graph_provider):
        org = MagicMock()
        org.to_arango_base_org.return_value = {"_key": "o1"}
        await tx_store.create_orgs([org])
        mock_graph_provider.batch_upsert_nodes.assert_awaited_once()

    # Note: create_domains, create_anyone, create_anyone_with_link, create_anyone_same_org
    # reference CollectionNames enum values (DOMAINS, ANYONE, etc.) that don't exist,
    # so those methods are dead code and not testable.

    @pytest.mark.asyncio
    async def test_batch_create_edges(self, tx_store, mock_graph_provider):
        await tx_store.batch_create_edges([{"edge": "data"}], "edge_coll")
        mock_graph_provider.batch_create_edges.assert_awaited_once_with(
            [{"edge": "data"}], collection="edge_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_batch_create_entity_relations(self, tx_store, mock_graph_provider):
        await tx_store.batch_create_entity_relations([{"edge": "data"}])
        mock_graph_provider.batch_create_entity_relations.assert_awaited_once_with(
            [{"edge": "data"}], transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_edges_to_node(self, tx_store, mock_graph_provider):
        await tx_store.get_edges_to_node("node1", "edge_coll")
        mock_graph_provider.get_edges_to_node.assert_awaited_once_with(
            "node1", "edge_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_edges_from_node(self, tx_store, mock_graph_provider):
        await tx_store.get_edges_from_node("node1", "edge_coll")
        mock_graph_provider.get_edges_from_node.assert_awaited_once_with(
            "node1", "edge_coll", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_related_node_field(self, tx_store, mock_graph_provider):
        await tx_store.get_related_node_field("node1", "edge_coll", "target_coll", "name", direction="outbound")
        mock_graph_provider.get_related_node_field.assert_awaited_once_with(
            "node1", "edge_coll", "target_coll", "name", "outbound", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_delete_records_and_relations(self, tx_store, mock_graph_provider):
        await tx_store.delete_records_and_relations("rec1", hard_delete=True)
        mock_graph_provider.delete_records_and_relations.assert_awaited_once_with(
            "rec1", hard_delete=True, transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_process_file_permissions(self, tx_store, mock_graph_provider):
        await tx_store.process_file_permissions("org1", "file1", [{"perm": "data"}])
        mock_graph_provider.process_file_permissions.assert_awaited_once_with(
            "org1", "file1", [{"perm": "data"}], transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_nodes_by_field_in(self, tx_store, mock_graph_provider):
        await tx_store.get_nodes_by_field_in("records", "status", ["active"], return_fields=["_key"])
        mock_graph_provider.get_nodes_by_field_in.assert_awaited_once_with(
            "records", "status", ["active"], ["_key"], transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_remove_nodes_by_field(self, tx_store, mock_graph_provider):
        result = await tx_store.remove_nodes_by_field("records", "status", "deleted")
        assert result == 0
        mock_graph_provider.remove_nodes_by_field.assert_awaited_once_with(
            "records", "status", "deleted", transaction="txn-123"
        )

    @pytest.mark.asyncio
    async def test_get_nodes_by_filters(self, tx_store, mock_graph_provider):
        await tx_store.get_nodes_by_filters("records", {"status": "active"}, return_fields=["_key"])
        mock_graph_provider.get_nodes_by_filters.assert_awaited_once_with(
            collection="records", filters={"status": "active"}, return_fields=["_key"], transaction="txn-123"
        )


class TestGraphTransactionStoreUserGroupHierarchy:
    """Tests for create_user_group_hierarchy."""

    @pytest.fixture
    def tx_store(self, mock_graph_provider):
        return GraphTransactionStore(mock_graph_provider, "txn-123")

    @pytest.mark.asyncio
    async def test_hierarchy_child_not_found(self, tx_store, mock_graph_provider):
        mock_graph_provider.get_user_group_by_external_id.return_value = None
        result = await tx_store.create_user_group_hierarchy("child1", "parent1", "conn1")
        assert result is False

    @pytest.mark.asyncio
    async def test_hierarchy_parent_not_found(self, tx_store, mock_graph_provider):
        child = MagicMock(id="child_id", name="child_name")
        mock_graph_provider.get_user_group_by_external_id.side_effect = [child, None]
        result = await tx_store.create_user_group_hierarchy("child1", "parent1", "conn1")
        assert result is False

    @pytest.mark.asyncio
    async def test_hierarchy_success(self, tx_store, mock_graph_provider):
        child = MagicMock(id="child_id", name="child_name")
        parent = MagicMock(id="parent_id", name="parent_name")
        mock_graph_provider.get_user_group_by_external_id.side_effect = [child, parent]
        result = await tx_store.create_user_group_hierarchy("child1", "parent1", "conn1")
        assert result is True
        mock_graph_provider.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hierarchy_exception_returns_false(self, tx_store, mock_graph_provider):
        mock_graph_provider.get_user_group_by_external_id.side_effect = RuntimeError("db error")
        result = await tx_store.create_user_group_hierarchy("child1", "parent1", "conn1")
        assert result is False


class TestGraphTransactionStoreUserGroupMembership:
    """Tests for create_user_group_membership."""

    @pytest.fixture
    def tx_store(self, mock_graph_provider):
        return GraphTransactionStore(mock_graph_provider, "txn-123")

    @pytest.mark.asyncio
    async def test_membership_user_not_found(self, tx_store, mock_graph_provider):
        mock_graph_provider.get_user_by_source_id.return_value = None
        result = await tx_store.create_user_group_membership("user1", "group1", "conn1")
        assert result is False

    @pytest.mark.asyncio
    async def test_membership_group_not_found(self, tx_store, mock_graph_provider):
        user = MagicMock(id="user_id", email="test@example.com")
        mock_graph_provider.get_user_by_source_id.return_value = user
        mock_graph_provider.get_user_group_by_external_id.return_value = None
        result = await tx_store.create_user_group_membership("user1", "group1", "conn1")
        assert result is False

    @pytest.mark.asyncio
    async def test_membership_success(self, tx_store, mock_graph_provider):
        user = MagicMock(id="user_id", email="test@example.com")
        group = MagicMock(id="group_id", name="group_name")
        mock_graph_provider.get_user_by_source_id.return_value = user
        mock_graph_provider.get_user_group_by_external_id.return_value = group
        result = await tx_store.create_user_group_membership("user1", "group1", "conn1")
        assert result is True
        mock_graph_provider.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_membership_exception_returns_false(self, tx_store, mock_graph_provider):
        mock_graph_provider.get_user_by_source_id.side_effect = RuntimeError("db error")
        result = await tx_store.create_user_group_membership("user1", "group1", "conn1")
        assert result is False


class TestGraphTransactionStoreRecordGroupPermissions:
    """Tests for batch_upsert_record_group_permissions."""

    @pytest.fixture
    def tx_store(self, mock_graph_provider):
        return GraphTransactionStore(mock_graph_provider, "txn-123")

    @pytest.mark.asyncio
    async def test_empty_permissions_returns_early(self, tx_store, mock_graph_provider):
        await tx_store.batch_upsert_record_group_permissions("grp1", [], "conn1")
        mock_graph_provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_permission_user_not_found_skipped(self, tx_store, mock_graph_provider):
        perm = MagicMock()
        perm.entity_type.value = "USER"
        perm.email = "missing@example.com"
        mock_graph_provider.get_user_by_email.return_value = None

        await tx_store.batch_upsert_record_group_permissions("grp1", [perm], "conn1")
        mock_graph_provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_user_permission_success(self, tx_store, mock_graph_provider):
        user = MagicMock(id="user_id")
        mock_graph_provider.get_user_by_email.return_value = user

        perm = MagicMock()
        perm.entity_type.value = "USER"
        perm.email = "user@example.com"
        perm.to_arango_permission = MagicMock(return_value={"edge": "data"})

        await tx_store.batch_upsert_record_group_permissions("grp1", [perm], "conn1")
        mock_graph_provider.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_group_permission_group_not_found_skipped(self, tx_store, mock_graph_provider):
        perm = MagicMock()
        perm.entity_type.value = "GROUP"
        perm.external_id = "missing_group"
        mock_graph_provider.get_user_group_by_external_id.return_value = None

        await tx_store.batch_upsert_record_group_permissions("grp1", [perm], "conn1")
        mock_graph_provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_permission_success(self, tx_store, mock_graph_provider):
        group = MagicMock(id="group_id")
        mock_graph_provider.get_user_group_by_external_id.return_value = group

        perm = MagicMock()
        perm.entity_type.value = "GROUP"
        perm.external_id = "ext_group_1"
        perm.to_arango_permission = MagicMock(return_value={"edge": "data"})

        await tx_store.batch_upsert_record_group_permissions("grp1", [perm], "conn1")
        mock_graph_provider.batch_create_edges.assert_awaited_once()


class TestGraphDataStore:
    """Tests for GraphDataStore transaction context manager."""

    @pytest.mark.asyncio
    async def test_transaction_commits_on_success(self, mock_graph_provider):
        store = GraphDataStore(logging.getLogger("test"), mock_graph_provider)

        async with store.transaction() as tx_store:
            assert isinstance(tx_store, GraphTransactionStore)
            assert tx_store.txn == "txn-123"

        mock_graph_provider.commit_transaction.assert_awaited_once_with("txn-123")
        mock_graph_provider.rollback_transaction.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transaction_rolls_back_on_exception(self, mock_graph_provider):
        store = GraphDataStore(logging.getLogger("test"), mock_graph_provider)

        with pytest.raises(ValueError, match="test error"):
            async with store.transaction() as tx_store:
                raise ValueError("test error")

        mock_graph_provider.rollback_transaction.assert_awaited_once_with("txn-123")
        mock_graph_provider.commit_transaction.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_in_transaction(self, mock_graph_provider):
        store = GraphDataStore(logging.getLogger("test"), mock_graph_provider)

        async def my_func(tx_store):
            return "result"

        result = await store.execute_in_transaction(my_func)
        assert result == "result"
        mock_graph_provider.commit_transaction.assert_awaited_once()
