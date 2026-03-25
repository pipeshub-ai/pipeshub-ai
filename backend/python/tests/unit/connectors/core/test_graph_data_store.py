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
