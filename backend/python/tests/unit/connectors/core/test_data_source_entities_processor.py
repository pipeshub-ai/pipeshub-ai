"""Tests targeting uncovered lines in data_source_entities_processor.py.

Focuses on:
- on_new_record_groups: parent/child relations, app edge, role/org/group permissions
- migrate_group_permissions_to_user: all branches
- on_app_role_deleted, on_record_group_deleted
- on_user_group_member_removed/added
- on_new_user_groups, on_new_app_roles: user-not-found paths
- _handle_related_external_records: edge deletion logging
- _link_record_to_group: group change, shared_with_me, inherit_permissions
- _handle_record_permissions: ROLE entity type
- add/delete_permission_to_record, get_app_creator_user
- _create_placeholder_parent_record: PULL_REQUEST type
- _delete_group_organization_edges
- update_record_group_name error path
- on_new_app_users error path
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors as ConnectorsEnum,
    EntityRelations,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    ARANGO_NODE_ID_PARTS,
    PERMISSION_HIERARCHY,
    DataSourceEntitiesProcessor,
    RecordGroupWithPermissions,
    UserGroupWithMembers,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    PullRequestRecord,
    Record,
    RecordGroup,
    RecordType,
    RelatedExternalRecord,
    TicketRecord,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_processor():
    """Build a DataSourceEntitiesProcessor with all dependencies mocked."""
    logger = MagicMock()
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    proc = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    return proc


def _make_record(**overrides):
    """Build a minimal FileRecord for testing."""
    defaults = {
        "org_id": "org-1",
        "external_record_id": "ext-1",
        "record_name": "test_file.txt",
        "origin": OriginTypes.CONNECTOR.value,
        "connector_name": ConnectorsEnum.GOOGLE_MAIL,
        "connector_id": "conn-1",
        "record_type": RecordType.FILE,
        "version": 1,
        "mime_type": "text/plain",
        "source_created_at": 1000,
        "source_updated_at": 2000,
    }
    defaults.update(overrides)
    return FileRecord(
        is_file=True,
        extension="txt",
        size_in_bytes=100,
        weburl="https://example.com",
        **defaults,
    )


def _make_tx_store():
    """Create a fully mocked transaction store."""
    tx_store = AsyncMock()
    tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    tx_store.batch_upsert_records = AsyncMock()
    tx_store.batch_create_edges = AsyncMock()
    tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx_store.batch_upsert_record_groups = AsyncMock()
    tx_store.create_record_group_relation = AsyncMock()
    tx_store.create_record_relation = AsyncMock()
    tx_store.get_record_by_key = AsyncMock(return_value=None)
    tx_store.batch_upsert_nodes = AsyncMock()
    tx_store.get_user_by_email = AsyncMock(return_value=None)
    tx_store.get_user_group_by_external_id = AsyncMock(return_value=None)
    tx_store.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1", "id": "org-1"}])
    tx_store.delete_edges_to = AsyncMock(return_value=0)
    tx_store.delete_edges_from = AsyncMock(return_value=0)
    tx_store.delete_parent_child_edge_to_record = AsyncMock()
    tx_store.delete_edge = AsyncMock(return_value=True)
    tx_store.get_edge = AsyncMock(return_value=None)
    tx_store.create_inherit_permissions_relation_record_group = AsyncMock()
    tx_store.delete_inherit_permissions_relation_record_group = AsyncMock()
    tx_store.delete_edges_by_relationship_types = AsyncMock(return_value=0)
    tx_store.batch_create_entity_relations = AsyncMock()
    tx_store.get_edges_from_node = AsyncMock(return_value=[])
    tx_store.delete_record_by_key = AsyncMock()
    tx_store.get_app_role_by_external_id = AsyncMock(return_value=None)
    tx_store.batch_upsert_app_users = AsyncMock()
    tx_store.batch_upsert_user_groups = AsyncMock()
    tx_store.batch_upsert_app_roles = AsyncMock()
    tx_store.get_users = AsyncMock(return_value=[])
    tx_store.get_app_users = AsyncMock(return_value=[])
    tx_store.delete_user_group_by_id = AsyncMock()
    tx_store.delete_nodes_and_edges = AsyncMock()
    tx_store.get_app_creator_user = AsyncMock(return_value=None)
    tx_store.create_record_groups_relation = AsyncMock()
    tx_store.get_edges_to_node = AsyncMock(return_value=[])
    return tx_store


def _make_ctx(tx_store):
    """Create async context manager mock wrapping a tx_store."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ===========================================================================
# _create_placeholder_parent_record - PULL_REQUEST type (line 205)
# ===========================================================================


class TestCreatePlaceholderPullRequest:
    def test_pull_request_type(self):
        """Creates PullRequestRecord placeholder for PULL_REQUEST type."""
        proc = _make_processor()
        record = _make_record()

        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.PULL_REQUEST, record
        )

        assert isinstance(result, PullRequestRecord)
        assert result.external_record_id == "parent-ext-1"


# ===========================================================================
# _handle_related_external_records - edge deletion logged (lines 275-329)
# ===========================================================================


class TestHandleRelatedExternalRecordsEdgeDeletion:
    @pytest.mark.asyncio
    async def test_logs_when_edges_deleted(self):
        """Logs when existing edges are deleted (deleted_count > 0)."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"
        # Make it return > 0 to hit the logging branch
        tx_store.delete_edges_by_relationship_types.return_value = 3

        await proc._handle_related_external_records(record, [], tx_store)

        proc.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_warning_on_delete_edges_exception(self):
        """Logs warning when delete_edges_by_relationship_types raises."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"
        tx_store.delete_edges_by_relationship_types.side_effect = RuntimeError("db fail")

        await proc._handle_related_external_records(record, [], tx_store)

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_creates_placeholder_and_links_to_group(self):
        """Creates placeholder record and links to record group when group found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id.return_value = None

        record = _make_record()
        record.id = "rec-1"
        record.external_record_group_id = "ext-grp-1"
        record.record_group_type = "DRIVE"

        # Mock so _handle_record_group returns a group_id
        mock_group = MagicMock()
        mock_group.id = "grp-id-1"
        tx_store.get_record_group_by_external_id.return_value = mock_group

        rel_ext = RelatedExternalRecord(
            external_record_id="related-ext",
            record_type=RecordType.TICKET,
            relation_type=RecordRelations.DEPENDS_ON,
        )

        await proc._handle_related_external_records(record, [rel_ext], tx_store)

        # Should have upserted the placeholder
        tx_store.batch_upsert_records.assert_awaited()
        # Should have linked to group
        tx_store.create_record_group_relation.assert_awaited()


# ===========================================================================
# _link_record_to_group - shared_with_me not found (line 400)
# ===========================================================================


class TestLinkRecordToGroupSharedNotFound:
    @pytest.mark.asyncio
    async def test_shared_with_me_group_not_found_logs_warning(self):
        """Logs warning when shared_with_me record group is not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"
        record.is_shared_with_me = True
        record.shared_with_me_record_group_id = "shared-ext-group"
        record.inherit_permissions = True

        # shared_with_me group lookup returns None
        tx_store.get_record_group_by_external_id.return_value = None

        await proc._link_record_to_group(record, "group-1", tx_store)

        proc.logger.warning.assert_called()
        # Verify the warning message contains the expected text
        warning_calls = [str(c) for c in proc.logger.warning.call_args_list]
        assert any("shared-ext-group" in w for w in warning_calls)


# ===========================================================================
# _handle_record_permissions - ROLE entity type (lines 619-660)
# ===========================================================================


class TestHandleRecordPermissionsRole:
    @pytest.mark.asyncio
    async def test_role_permission_with_known_role(self):
        """Creates permission edge for known role."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_role = MagicMock()
        mock_role.id = "role-1"
        tx_store.get_app_role_by_external_id.return_value = mock_role

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.ROLE.value
        permission.external_id = "ext-role-1"
        permission.email = None
        permission.to_arango_permission = MagicMock(
            return_value={"_from": "r/1", "_to": "rec/1"}
        )

        await proc._handle_record_permissions(record, [permission], tx_store)

        tx_store.get_app_role_by_external_id.assert_awaited_once()
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_role_permission_unknown_role_skipped(self):
        """Skips permission when role not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_app_role_by_external_id.return_value = None

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.ROLE.value
        permission.external_id = "ext-role-1"
        permission.email = None

        await proc._handle_record_permissions(record, [permission], tx_store)

        proc.logger.warning.assert_called()
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_role_permission_no_external_id(self):
        """Skips role permission when no external_id."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.ROLE.value
        permission.external_id = None
        permission.email = None

        await proc._handle_record_permissions(record, [permission], tx_store)

        # Role not found (None external_id means no lookup), logs warning
        proc.logger.warning.assert_called()
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_permission_no_external_id(self):
        """Skips group permission when no external_id."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.GROUP.value
        permission.external_id = None
        permission.email = None

        await proc._handle_record_permissions(record, [permission], tx_store)

        proc.logger.warning.assert_called()
        tx_store.batch_create_edges.assert_not_awaited()


# ===========================================================================
# on_new_record_groups - parent/child, app edge, permissions (lines 1022-1149)
# ===========================================================================


class TestOnNewRecordGroupsAdvanced:
    @pytest.mark.asyncio
    async def test_creates_app_edge_when_no_parent(self):
        """Creates BELONGS_TO edge to app when no parent record group edge."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        # No parent edge exists
        tx_store.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.ORGS.value}/org-1"}
        ]

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        await proc.on_new_record_groups([(rg, [])])

        # Should create 2 edges: org relation + app relation
        assert tx_store.batch_create_edges.call_count >= 2

    @pytest.mark.asyncio
    async def test_skips_app_edge_when_parent_group_edge_exists(self):
        """Does not create app edge when parent record group edge already exists."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        # Parent record group edge exists
        tx_store.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.RECORD_GROUPS.value}/parent-rg-id"}
        ]

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        await proc.on_new_record_groups([(rg, [])])

        # Should only have org relation, not app relation
        assert tx_store.batch_create_edges.call_count == 1

    @pytest.mark.asyncio
    async def test_creates_parent_child_relation(self):
        """Creates BELONGS_TO edge to parent record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        parent_rg = MagicMock()
        parent_rg.id = "parent-rg-id"
        parent_rg.name = "Parent Group"

        # First call returns None (for existing check), second returns parent
        call_count = [0]
        async def mock_get_rg(connector_id, external_id):
            call_count[0] += 1
            if external_id == "parent-ext":
                return parent_rg
            return None
        tx_store.get_record_group_by_external_id.side_effect = mock_get_rg

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Child Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            parent_external_group_id="parent-ext",
        )

        await proc.on_new_record_groups([(rg, [])])

        # Should create edges for org + parent
        assert tx_store.batch_create_edges.call_count >= 2

    @pytest.mark.asyncio
    async def test_creates_inherit_permissions_edge(self):
        """Creates INHERIT_PERMISSIONS edge when inherit_permissions is True."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        parent_rg = MagicMock()
        parent_rg.id = "parent-rg-id"
        parent_rg.name = "Parent Group"

        async def mock_get_rg(connector_id, external_id):
            if external_id == "parent-ext":
                return parent_rg
            return None
        tx_store.get_record_group_by_external_id.side_effect = mock_get_rg

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Child Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            parent_external_group_id="parent-ext",
            inherit_permissions=True,
        )

        await proc.on_new_record_groups([(rg, [])])

        # Should include INHERIT_PERMISSIONS batch_create_edges call
        all_calls = tx_store.batch_create_edges.call_args_list
        collections_used = [c.kwargs.get("collection") or c[1].get("collection", "") for c in all_calls]
        assert CollectionNames.INHERIT_PERMISSIONS.value in collections_used

    @pytest.mark.asyncio
    async def test_parent_not_found_logs_warning(self):
        """Logs warning when parent record group not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        tx_store.get_record_group_by_external_id.return_value = None

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Child Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            parent_external_group_id="parent-ext-nonexistent",
        )

        await proc.on_new_record_groups([(rg, [])])

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_user_permission_in_record_group(self):
        """Creates USER permission edge for record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            email="user@example.com",
            type=PermissionType.READ,
            entity_type=EntityType.USER,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        # Should create permission edges
        all_calls = tx_store.batch_create_edges.call_args_list
        collections_used = [c.kwargs.get("collection") or c[1].get("collection", "") for c in all_calls]
        assert CollectionNames.PERMISSION.value in collections_used

    @pytest.mark.asyncio
    async def test_user_permission_not_found(self):
        """Logs warning when user for permission not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        tx_store.get_user_by_email.return_value = None

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            email="unknown@example.com",
            type=PermissionType.READ,
            entity_type=EntityType.USER,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_group_permission_in_record_group(self):
        """Creates GROUP permission edge for record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_group = MagicMock()
        mock_group.id = "group-1"
        tx_store.get_user_group_by_external_id.return_value = mock_group

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            external_id="ext-grp-perm",
            type=PermissionType.READ,
            entity_type=EntityType.GROUP,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        all_calls = tx_store.batch_create_edges.call_args_list
        collections_used = [c.kwargs.get("collection") or c[1].get("collection", "") for c in all_calls]
        assert CollectionNames.PERMISSION.value in collections_used

    @pytest.mark.asyncio
    async def test_group_permission_not_found(self):
        """Logs warning when group for permission not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        tx_store.get_user_group_by_external_id.return_value = None

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            external_id="ext-grp-perm",
            type=PermissionType.READ,
            entity_type=EntityType.GROUP,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_role_permission_in_record_group(self):
        """Creates ROLE permission edge for record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_role = MagicMock()
        mock_role.id = "role-1"
        tx_store.get_app_role_by_external_id.return_value = mock_role

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            external_id="ext-role-perm",
            type=PermissionType.READ,
            entity_type=EntityType.ROLE,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        all_calls = tx_store.batch_create_edges.call_args_list
        collections_used = [c.kwargs.get("collection") or c[1].get("collection", "") for c in all_calls]
        assert CollectionNames.PERMISSION.value in collections_used

    @pytest.mark.asyncio
    async def test_role_permission_not_found(self):
        """Logs warning when role for permission not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        tx_store.get_app_role_by_external_id.return_value = None

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            external_id="ext-role-perm",
            type=PermissionType.READ,
            entity_type=EntityType.ROLE,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_org_permission_in_record_group(self):
        """Creates ORG permission edge for record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            type=PermissionType.READ,
            entity_type=EntityType.ORG,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        all_calls = tx_store.batch_create_edges.call_args_list
        collections_used = [c.kwargs.get("collection") or c[1].get("collection", "") for c in all_calls]
        assert CollectionNames.PERMISSION.value in collections_used

    @pytest.mark.asyncio
    async def test_parent_record_group_id_creates_relation(self):
        """Creates record groups relation when parent_record_group_id is set."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Child Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            parent_record_group_id="parent-rg-internal-id",
        )

        # Add a permission so we reach the parent_record_group_id check after permissions
        perm = Permission(
            type=PermissionType.READ,
            entity_type=EntityType.ORG,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        tx_store.create_record_groups_relation.assert_awaited_once_with(
            rg.id, "parent-rg-internal-id"
        )

    @pytest.mark.asyncio
    async def test_exception_in_on_new_record_groups(self):
        """Transaction error in on_new_record_groups is logged and re-raised."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.batch_upsert_record_groups.side_effect = RuntimeError("db error")
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        with pytest.raises(RuntimeError, match="db error"):
            await proc.on_new_record_groups([(rg, [])])

        proc.logger.error.assert_called()


# ===========================================================================
# on_new_user_groups - user not found (lines 1245-1275)
# ===========================================================================


class TestOnNewUserGroups:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        """Empty list logs warning."""
        proc = _make_processor()
        await proc.on_new_user_groups([])
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_creates_new_user_group_with_members(self):
        """Creates user group and permission edges for known members."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-internal-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_user_group_by_external_id.return_value = None

        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_group_id="ext-ug-1",
            name="Test Group",
        )

        member = AppUser(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="src-user-1",
            email="member@example.com",
            full_name="Test Member",
        )

        await proc.on_new_user_groups([(ug, [member])])

        tx_store.batch_upsert_user_groups.assert_awaited()
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_user_not_found_logs_warning(self):
        """Logs warning when member user not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        tx_store.get_user_by_email.return_value = None
        tx_store.get_user_group_by_external_id.return_value = None

        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_group_id="ext-ug-1",
            name="Test Group",
        )

        member = AppUser(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="src-user-1",
            email="unknown@example.com",
            full_name="Unknown User",
        )

        await proc.on_new_user_groups([(ug, [member])])

        proc.logger.warning.assert_called()
        # No permission edges created
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_user_group(self):
        """Updates existing user group and deletes old permission edges."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        existing_ug = MagicMock()
        existing_ug.id = "existing-ug-id"
        tx_store.get_user_group_by_external_id.return_value = existing_ug

        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_group_id="ext-ug-1",
            name="Test Group",
        )

        await proc.on_new_user_groups([(ug, [])])

        assert ug.id == "existing-ug-id"
        tx_store.delete_edges_to.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_logged_and_raised(self):
        """Transaction errors are logged and re-raised."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.batch_upsert_user_groups.side_effect = RuntimeError("db fail")
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_group_id="ext-ug-1",
            name="Test Group",
        )

        with pytest.raises(RuntimeError, match="db fail"):
            await proc.on_new_user_groups([(ug, [])])

        proc.logger.error.assert_called()


# ===========================================================================
# on_new_app_roles - user not found (lines 1306-1358)
# ===========================================================================


class TestOnNewAppRoles:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        """Empty list logs warning."""
        proc = _make_processor()
        await proc.on_new_app_roles([])
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_creates_new_role_with_members(self):
        """Creates role and permission edges for known members."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-internal-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_app_role_by_external_id.return_value = None

        role = AppRole(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_role_id="ext-role-1",
            name="Admin Role",
        )

        member = AppUser(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="src-user-1",
            email="member@example.com",
            full_name="Test Member",
        )

        await proc.on_new_app_roles([(role, [member])])

        tx_store.batch_upsert_app_roles.assert_awaited()
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_user_not_found_logs_warning(self):
        """Logs warning when member user not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        tx_store.get_user_by_email.return_value = None
        tx_store.get_app_role_by_external_id.return_value = None

        role = AppRole(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_role_id="ext-role-1",
            name="Admin Role",
        )

        member = AppUser(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="src-user-1",
            email="unknown@example.com",
            full_name="Unknown User",
        )

        await proc.on_new_app_roles([(role, [member])])

        proc.logger.warning.assert_called()
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_role(self):
        """Updates existing role and deletes old permission edges."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        existing_role = MagicMock()
        existing_role.id = "existing-role-id"
        tx_store.get_app_role_by_external_id.return_value = existing_role

        role = AppRole(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_role_id="ext-role-1",
            name="Admin Role",
        )

        await proc.on_new_app_roles([(role, [])])

        assert role.id == "existing-role-id"
        tx_store.delete_edges_to.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_logged_and_raised(self):
        """Transaction errors are logged and re-raised."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.batch_upsert_app_roles.side_effect = RuntimeError("db fail")
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        role = AppRole(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_role_id="ext-role-1",
            name="Admin Role",
        )

        with pytest.raises(RuntimeError, match="db fail"):
            await proc.on_new_app_roles([(role, [])])

        proc.logger.error.assert_called()


# ===========================================================================
# on_user_group_member_removed (lines 1425-1436)
# ===========================================================================


class TestOnUserGroupMemberRemoved:
    @pytest.mark.asyncio
    async def test_user_not_found(self):
        """Returns False when user not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_user_by_email.return_value = None

        result = await proc.on_user_group_member_removed("ext-grp", "unknown@test.com", "conn-1")

        assert result is False
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        """Returns False when group not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_user_group_by_external_id.return_value = None

        result = await proc.on_user_group_member_removed("ext-grp", "user@test.com", "conn-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_edge_deleted_successfully(self):
        """Returns True when edge is deleted."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        mock_group = MagicMock()
        mock_group.id = "group-1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.delete_edge.return_value = True

        result = await proc.on_user_group_member_removed("ext-grp", "user@test.com", "conn-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_edge_not_found(self):
        """Returns False when permission edge not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        mock_group = MagicMock()
        mock_group.id = "group-1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.delete_edge.return_value = False

        result = await proc.on_user_group_member_removed("ext-grp", "user@test.com", "conn-1")

        assert result is False
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Returns False on exception."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = _make_ctx(tx_store)
        ctx.__aenter__.side_effect = RuntimeError("db fail")
        proc.data_store_provider.transaction.return_value = ctx

        result = await proc.on_user_group_member_removed("ext-grp", "user@test.com", "conn-1")

        assert result is False
        proc.logger.error.assert_called()


# ===========================================================================
# on_user_group_member_added (lines 1507-1563)
# ===========================================================================


class TestOnUserGroupMemberAdded:
    @pytest.mark.asyncio
    async def test_user_not_found(self):
        """Returns False when user not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_user_by_email.return_value = None

        result = await proc.on_user_group_member_added(
            "ext-grp", "unknown@test.com", PermissionType.READ, "conn-1"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        """Returns False when group not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_user_group_by_external_id.return_value = None

        result = await proc.on_user_group_member_added(
            "ext-grp", "user@test.com", PermissionType.READ, "conn-1"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_edge_already_exists(self):
        """Returns False when permission edge already exists."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        mock_group = MagicMock()
        mock_group.id = "group-1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.get_edge.return_value = {"_key": "existing-edge"}

        result = await proc.on_user_group_member_added(
            "ext-grp", "user@test.com", PermissionType.READ, "conn-1"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_creates_new_permission_edge(self):
        """Creates permission edge and returns True on success."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        mock_group = MagicMock()
        mock_group.id = "group-1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.get_edge.return_value = None

        result = await proc.on_user_group_member_added(
            "ext-grp", "user@test.com", PermissionType.READ, "conn-1"
        )

        assert result is True
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Returns False on exception."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = _make_ctx(tx_store)
        ctx.__aenter__.side_effect = RuntimeError("db fail")
        proc.data_store_provider.transaction.return_value = ctx

        result = await proc.on_user_group_member_added(
            "ext-grp", "user@test.com", PermissionType.READ, "conn-1"
        )

        assert result is False
        proc.logger.error.assert_called()


# ===========================================================================
# on_user_group_deleted (lines 1558-1563)
# ===========================================================================


class TestOnUserGroupDeleted:
    @pytest.mark.asyncio
    async def test_group_not_found_returns_true(self):
        """Returns True when group not found (already deleted)."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_user_group_by_external_id.return_value = None

        result = await proc.on_user_group_deleted("ext-grp", "conn-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_deletes_group_successfully(self):
        """Deletes group and returns True."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_group = MagicMock()
        mock_group.id = "group-1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group

        result = await proc.on_user_group_deleted("ext-grp", "conn-1")

        assert result is True
        tx_store.delete_nodes_and_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Returns False on exception."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_group = MagicMock()
        mock_group.id = "group-1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.delete_nodes_and_edges.side_effect = RuntimeError("db fail")

        result = await proc.on_user_group_deleted("ext-grp", "conn-1")

        assert result is False
        proc.logger.error.assert_called()


# ===========================================================================
# migrate_group_permissions_to_user (lines 1640-1744)
# ===========================================================================


class TestMigrateGroupPermissionsToUser:
    @pytest.mark.asyncio
    async def test_no_tx_store_creates_transaction(self):
        """Creates transaction when tx_store is None."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        tx_store.get_user_by_email.return_value = None

        await proc.migrate_group_permissions_to_user("grp-1", "user@test.com", "conn-1")

        # Should have called transaction
        proc.data_store_provider.transaction.assert_called()

    @pytest.mark.asyncio
    async def test_user_not_found_returns_none(self):
        """Returns None when user not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_user_by_email.return_value = None

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "unknown@test.com", "conn-1", tx_store
        )

        assert result is None
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_permission_edges_returns_none(self):
        """Returns None when no permission edges found for group."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_edges_from_node.return_value = []

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_edge_without_to(self):
        """Skips edges without _to field."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        # Edge without _to
        tx_store.get_edges_from_node.return_value = [{"_key": "e1"}]

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        assert result is None
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_edge_with_invalid_to_format(self):
        """Skips edges with _to that doesn't have collection/id format."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        # Edge with invalid _to (no slash)
        tx_store.get_edges_from_node.return_value = [{"_key": "e1", "_to": "noslash"}]

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        assert result is None
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_permission_edges(self):
        """Creates new permission edges when user has no existing permissions."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        tx_store.get_edges_from_node.return_value = [
            {"_key": "e1", "_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "READER"},
            {"_key": "e2", "_to": f"{CollectionNames.RECORDS.value}/rec-2", "role": "WRITER"},
        ]
        tx_store.get_edge.return_value = None  # No existing user permission

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        assert result is None
        tx_store.batch_create_edges.assert_awaited_once()
        # Should have created 2 edges
        edges = tx_store.batch_create_edges.call_args[0][0]
        assert len(edges) == 2

    @pytest.mark.asyncio
    async def test_upgrades_existing_permission(self):
        """Upgrades permission when new level is higher."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        tx_store.get_edges_from_node.return_value = [
            {"_key": "e1", "_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "WRITER"},
        ]
        # User has existing READER permission
        tx_store.get_edge.return_value = {"_key": "existing", "role": "READER"}

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        assert result is None
        # Should delete old edge and create new one
        tx_store.delete_edge.assert_awaited()
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_existing_permission_same_or_higher(self):
        """Skips when existing permission is same or higher level."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        tx_store.get_edges_from_node.return_value = [
            {"_key": "e1", "_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "READER"},
        ]
        # User already has OWNER permission (higher)
        tx_store.get_edge.return_value = {"_key": "existing", "role": "OWNER"}

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        assert result is None
        # No new edges created
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_role_string_uses_default(self):
        """Falls back to READ permission for invalid role strings."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        tx_store.get_edges_from_node.return_value = [
            {"_key": "e1", "_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "INVALID_ROLE"},
        ]
        tx_store.get_edge.return_value = None

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        assert result is None
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_edge_processing_continues(self):
        """Continues processing when individual edge fails."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        tx_store.get_edges_from_node.return_value = [
            {"_key": "e1", "_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "READER"},
            {"_key": "e2", "_to": f"{CollectionNames.RECORDS.value}/rec-2", "role": "READER"},
        ]

        # First get_edge call raises, second returns None
        call_count = [0]
        async def mock_get_edge(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("edge error")
            return None
        tx_store.get_edge.side_effect = mock_get_edge

        result = await proc.migrate_group_permissions_to_user(
            "grp-1", "user@test.com", "conn-1", tx_store
        )

        # Should log warning for first edge
        proc.logger.warning.assert_called()
        # Should still create edge for second
        tx_store.batch_create_edges.assert_awaited()


# ===========================================================================
# on_app_role_deleted (lines 1812-1846)
# ===========================================================================


class TestOnAppRoleDeleted:
    @pytest.mark.asyncio
    async def test_role_not_found(self):
        """Returns False when role not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_app_role_by_external_id.return_value = None

        result = await proc.on_app_role_deleted("ext-role-1", "conn-1")

        assert result is False
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_deletes_role_successfully(self):
        """Deletes role and returns True."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_role = MagicMock()
        mock_role.id = "role-internal-1"
        mock_role.name = "Admin"
        tx_store.get_app_role_by_external_id.return_value = mock_role

        result = await proc.on_app_role_deleted("ext-role-1", "conn-1")

        assert result is True
        tx_store.delete_nodes_and_edges.assert_awaited_once_with(
            ["role-internal-1"], CollectionNames.ROLES.value
        )

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Returns False on exception."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_role = MagicMock()
        mock_role.id = "role-internal-1"
        mock_role.name = "Admin"
        tx_store.get_app_role_by_external_id.return_value = mock_role
        tx_store.delete_nodes_and_edges.side_effect = RuntimeError("db fail")

        result = await proc.on_app_role_deleted("ext-role-1", "conn-1")

        assert result is False
        proc.logger.error.assert_called()


# ===========================================================================
# on_record_group_deleted (lines 1863-1900)
# ===========================================================================


class TestOnRecordGroupDeleted:
    @pytest.mark.asyncio
    async def test_group_not_found(self):
        """Returns False when group not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_record_group_by_external_id.return_value = None

        result = await proc.on_record_group_deleted("ext-grp-1", "conn-1")

        assert result is False
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_deletes_group_successfully(self):
        """Deletes record group and returns True."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_group = MagicMock()
        mock_group.id = "rg-internal-1"
        mock_group.name = "Test RG"
        tx_store.get_record_group_by_external_id.return_value = mock_group

        result = await proc.on_record_group_deleted("ext-grp-1", "conn-1")

        assert result is True
        tx_store.delete_nodes_and_edges.assert_awaited_once_with(
            ["rg-internal-1"], CollectionNames.RECORD_GROUPS.value
        )

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Returns False on exception."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_group = MagicMock()
        mock_group.id = "rg-internal-1"
        mock_group.name = "Test RG"
        tx_store.get_record_group_by_external_id.return_value = mock_group
        tx_store.delete_nodes_and_edges.side_effect = RuntimeError("db fail")

        result = await proc.on_record_group_deleted("ext-grp-1", "conn-1")

        assert result is False
        proc.logger.error.assert_called()


# ===========================================================================
# _delete_group_organization_edges (lines 1905-1921)
# ===========================================================================


class TestDeleteGroupOrganizationEdges:
    @pytest.mark.asyncio
    async def test_edge_deleted_successfully(self):
        """Logs info when edge deleted."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.delete_edge.return_value = True

        await proc._delete_group_organization_edges(tx_store, "grp-1")

        proc.logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_edge_not_found(self):
        """Logs debug when no edge found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.delete_edge.return_value = False

        await proc._delete_group_organization_edges(tx_store, "grp-1")

        proc.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_exception_logged(self):
        """Logs error on exception."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.delete_edge.side_effect = RuntimeError("db fail")

        await proc._delete_group_organization_edges(tx_store, "grp-1")

        proc.logger.error.assert_called()


# ===========================================================================
# add_permission_to_record (lines 1926-1927)
# ===========================================================================


class TestAddPermissionToRecord:
    @pytest.mark.asyncio
    async def test_adds_permissions(self):
        """Delegates to _handle_record_permissions within transaction."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        record = _make_record()
        record.id = "rec-1"

        perm = MagicMock()
        perm.entity_type = EntityType.USER.value
        perm.email = "user@test.com"
        perm.external_id = None
        perm.to_arango_permission = MagicMock(return_value={"_from": "u/1", "_to": "r/1"})

        await proc.add_permission_to_record(record, [perm])

        tx_store.batch_create_edges.assert_awaited()


# ===========================================================================
# delete_permission_from_record (lines 1932-1949)
# ===========================================================================


class TestDeletePermissionFromRecord:
    @pytest.mark.asyncio
    async def test_user_not_found(self):
        """Logs warning when user not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_user_by_email.return_value = None

        await proc.delete_permission_from_record("rec-1", "unknown@test.com")

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_deletes_permission_successfully(self):
        """Deletes permission edge and logs success."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.delete_edge.return_value = True

        await proc.delete_permission_from_record("rec-1", "user@test.com")

        tx_store.delete_edge.assert_awaited()
        proc.logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_delete_fails_logs_warning(self):
        """Logs warning when delete edge returns False."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.delete_edge.return_value = False

        await proc.delete_permission_from_record("rec-1", "user@test.com")

        proc.logger.warning.assert_called()


# ===========================================================================
# get_app_creator_user (lines 1955-1956)
# ===========================================================================


class TestGetAppCreatorUser:
    @pytest.mark.asyncio
    async def test_returns_creator_user(self):
        """Returns creator user from tx_store."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_user = User(email="creator@test.com", id="creator-1")
        tx_store.get_app_creator_user.return_value = mock_user

        result = await proc.get_app_creator_user("conn-1")

        assert result == mock_user
        tx_store.get_app_creator_user.assert_awaited_once_with("conn-1")

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        """Returns None when no creator user found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_app_creator_user.return_value = None

        result = await proc.get_app_creator_user("conn-1")

        assert result is None


# ===========================================================================
# on_new_app_users - error path (lines 1176-1191)
# ===========================================================================


class TestOnNewAppUsers:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        """Empty list logs warning."""
        proc = _make_processor()
        await proc.on_new_app_users([])
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_upserts_users(self):
        """Upserts users within transaction."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        user = AppUser(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="src-1",
            email="user@test.com",
            full_name="Test User",
        )

        await proc.on_new_app_users([user])

        tx_store.batch_upsert_app_users.assert_awaited_once_with([user])

    @pytest.mark.asyncio
    async def test_exception_logged_and_raised(self):
        """Transaction errors are logged and re-raised."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.batch_upsert_app_users.side_effect = RuntimeError("db fail")
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        user = AppUser(
            app_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="src-1",
            email="user@test.com",
            full_name="Test User",
        )

        with pytest.raises(RuntimeError, match="db fail"):
            await proc.on_new_app_users([user])

        proc.logger.error.assert_called()


# ===========================================================================
# update_record_group_name - error path (lines 1176-1178)
# ===========================================================================


class TestUpdateRecordGroupName:
    @pytest.mark.asyncio
    async def test_group_not_found(self):
        """Logs warning when group not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_record_group_by_external_id.return_value = None

        await proc.update_record_group_name("ext-folder", "New Name", connector_id="conn-1")

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_renames_successfully(self):
        """Renames record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        existing = MagicMock()
        existing.id = "rg-1"
        tx_store.get_record_group_by_external_id.return_value = existing

        await proc.update_record_group_name(
            "ext-folder", "New Name", old_name="Old Name", connector_id="conn-1"
        )

        assert existing.name == "New Name"
        tx_store.batch_upsert_record_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_logged_and_raised(self):
        """Errors are logged and re-raised."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        existing = MagicMock()
        existing.id = "rg-1"
        tx_store.get_record_group_by_external_id.return_value = existing
        tx_store.batch_upsert_record_groups.side_effect = RuntimeError("db fail")

        with pytest.raises(RuntimeError, match="db fail"):
            await proc.update_record_group_name(
                "ext-folder", "New Name", connector_id="conn-1"
            )

        proc.logger.error.assert_called()


# ===========================================================================
# on_updated_record_permissions - additional branches (lines 730-752)
# ===========================================================================


class TestOnUpdatedRecordPermissionsAdditional:
    @pytest.mark.asyncio
    async def test_inherit_permissions_true_creates_edge(self):
        """Creates inherit permissions edge when inherit_permissions is True."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = True
        record.external_record_group_id = "ext-grp-1"

        mock_rg = MagicMock()
        mock_rg.id = "rg-1"
        tx_store.get_record_group_by_external_id.return_value = mock_rg

        await proc.on_updated_record_permissions(record, [])

        tx_store.create_inherit_permissions_relation_record_group.assert_awaited()

    @pytest.mark.asyncio
    async def test_inherit_permissions_false_deletes_edge(self):
        """Deletes inherit permissions edge when inherit_permissions is False."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = False
        record.external_record_group_id = "ext-grp-1"

        mock_rg = MagicMock()
        mock_rg.id = "rg-1"
        tx_store.get_record_group_by_external_id.return_value = mock_rg

        await proc.on_updated_record_permissions(record, [])

        tx_store.delete_edge.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_logged_and_raised(self):
        """Errors are logged and re-raised."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        # Force error on first call
        tx_store.get_edges_from_node.side_effect = RuntimeError("db fail")

        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = False

        with pytest.raises(RuntimeError, match="db fail"):
            await proc.on_updated_record_permissions(record, [])

        proc.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_no_belongs_to_edges_runs_process_record(self):
        """Runs _process_record when no BELONGS_TO edges exist."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        # No BELONGS_TO edges
        tx_store.get_edges_from_node.return_value = []

        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = False
        record.external_record_group_id = None

        await proc.on_updated_record_permissions(record, [])

        # _process_record was called (it calls batch_upsert_records)
        proc.logger.info.assert_called()


# ===========================================================================
# _handle_parent_record - PARENT_CHILD relation type (line 251)
# ===========================================================================


class TestHandleParentRecordParentChild:
    @pytest.mark.asyncio
    async def test_non_attachment_creates_parent_child(self):
        """Non-attachment parent creates PARENT_CHILD relation."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        parent = _make_record(external_record_id="parent-ext")
        parent.id = "parent-id"
        tx_store.get_record_by_external_id.return_value = parent

        # record_type is FILE but parent_record_type is FILE (not in ATTACHMENT_CONTAINER_TYPES)
        record = _make_record()
        record.id = "child-id"
        record.parent_external_record_id = "parent-ext"
        record.parent_record_type = RecordType.FILE

        await proc._handle_parent_record(record, tx_store)

        call_args = tx_store.create_record_relation.call_args
        assert call_args[0][2] == RecordRelations.PARENT_CHILD.value

    @pytest.mark.asyncio
    async def test_parent_found_but_group_id_linked(self):
        """When placeholder parent is created with record group, links to group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id.return_value = None

        record = _make_record()
        record.id = "child-id"
        record.parent_external_record_id = "parent-ext"
        record.parent_record_type = RecordType.TICKET
        record.external_record_group_id = "ext-grp-1"
        record.record_group_type = "DRIVE"

        # Mock so _handle_record_group returns a group_id
        mock_group = MagicMock()
        mock_group.id = "grp-internal-1"
        tx_store.get_record_group_by_external_id.return_value = mock_group

        await proc._handle_parent_record(record, tx_store)

        tx_store.batch_upsert_records.assert_awaited()
        tx_store.create_record_group_relation.assert_awaited()


# ===========================================================================
# delete_user_group_by_id
# ===========================================================================


class TestDeleteUserGroupById:
    @pytest.mark.asyncio
    async def test_deletes_successfully(self):
        """Deletes user group by ID."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        await proc.delete_user_group_by_id("grp-1")

        tx_store.delete_user_group_by_id.assert_awaited_once_with("grp-1")

    @pytest.mark.asyncio
    async def test_exception_logged_and_raised(self):
        """Errors are logged and re-raised."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.delete_user_group_by_id.side_effect = RuntimeError("db fail")
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        with pytest.raises(RuntimeError, match="db fail"):
            await proc.delete_user_group_by_id("grp-1")


# ===========================================================================
# migrate_group_to_user_by_external_id
# ===========================================================================


class TestMigrateGroupToUserByExternalId:
    @pytest.mark.asyncio
    async def test_group_not_found_returns_early(self):
        """Returns early when group not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        tx_store.get_user_group_by_external_id.return_value = None

        await proc.migrate_group_to_user_by_external_id(
            "ext-grp", "user@test.com", "conn-1"
        )

        proc.logger.debug.assert_called()
        tx_store.delete_user_group_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_migrates_and_deletes_group(self):
        """Migrates permissions and deletes group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_group = MagicMock()
        mock_group.id = "grp-1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group

        # User for migration
        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_edges_from_node.return_value = []

        await proc.migrate_group_to_user_by_external_id(
            "ext-grp", "user@test.com", "conn-1"
        )

        tx_store.delete_user_group_by_id.assert_awaited_once_with("grp-1")


# ===========================================================================
# _process_record with TicketRecord (lines 784-793)
# ===========================================================================


class TestProcessRecordTicket:
    @pytest.mark.asyncio
    async def test_ticket_record_calls_related_and_user_edges(self):
        """TicketRecord triggers _handle_related_external_records and _handle_ticket_user_edges."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ticket = TicketRecord(
            org_id="org-1",
            external_record_id="ext-ticket-1",
            record_name="TEST-123",
            origin=OriginTypes.CONNECTOR.value,
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            record_type=RecordType.TICKET,
            version=1,
            mime_type="text/plain",
            source_created_at=1000,
            source_updated_at=2000,
        )

        result = await proc._process_record(ticket, [], tx_store)

        assert result is not None
        # Should have called delete_edges_by_relationship_types (from _handle_related_external_records)
        tx_store.delete_edges_by_relationship_types.assert_awaited()
        # Should have called delete_edges_from (from _handle_ticket_user_edges)
        tx_store.delete_edges_from.assert_awaited()


# ===========================================================================
# on_new_records with internal record (line 866-868)
# ===========================================================================


class TestOnNewRecordsInternal:
    @pytest.mark.asyncio
    async def test_internal_record_skips_publish(self):
        """Internal records don't get events published."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        record = _make_record()
        record.id = "rec-1"
        record.is_internal = True

        await proc.on_new_records([(record, [])])

        proc.messaging_producer.send_message.assert_not_awaited()


# ===========================================================================
# reindex_existing_records with internal record (line 936-943)
# ===========================================================================


class TestReindexInternalRecords:
    @pytest.mark.asyncio
    async def test_internal_record_skipped(self):
        """Internal records are skipped during reindex."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        record = _make_record()
        record.id = "rec-1"
        record.is_internal = True

        await proc.reindex_existing_records([record])

        proc.messaging_producer.send_message.assert_not_awaited()


# ===========================================================================
# _handle_record_group - returns None at end (line 374)
# ===========================================================================


class TestHandleRecordGroupReturnsNone:
    @pytest.mark.asyncio
    async def test_returns_none_when_group_creation_yields_no_group(self):
        """Returns None when new group has no ID after upsert (edge case)."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.external_record_group_id = "ext-grp-1"
        record.record_group_type = "DRIVE"

        # Neither existing nor newly created group has an ID
        tx_store.get_record_group_by_external_id.return_value = None

        # Mock upsert to NOT set any id (leaving it as empty/falsy)
        async def mock_upsert(groups):
            for g in groups:
                g.id = None  # Simulate edge case where id is None
        tx_store.batch_upsert_record_groups.side_effect = mock_upsert

        result = await proc._handle_record_group(record, tx_store)

        assert result is None


# ===========================================================================
# get_all_active_users and get_all_app_users
# ===========================================================================


class TestGetUsers:
    @pytest.mark.asyncio
    async def test_get_all_active_users(self):
        """Returns active users."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_users = [User(email="u1@test.com"), User(email="u2@test.com")]
        tx_store.get_users.return_value = mock_users

        result = await proc.get_all_active_users()

        assert result == mock_users
        tx_store.get_users.assert_awaited_once_with("org-1", active=True)

    @pytest.mark.asyncio
    async def test_get_all_app_users(self):
        """Returns app users for a connector."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_users = [MagicMock()]
        tx_store.get_app_users.return_value = mock_users

        result = await proc.get_all_app_users("conn-1")

        assert result == mock_users

    @pytest.mark.asyncio
    async def test_get_record_by_external_id(self):
        """Returns record by external ID."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_record = _make_record()
        tx_store.get_record_by_external_id.return_value = mock_record

        result = await proc.get_record_by_external_id("conn-1", "ext-1")

        assert result == mock_record


# ===========================================================================
# on_new_record_groups - group permission with no external_id (line 1101)
# ===========================================================================


class TestOnNewRecordGroupsGroupPermNoExtId:
    @pytest.mark.asyncio
    async def test_group_permission_no_external_id(self):
        """Logs warning when GROUP permission has no external_id."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            external_id=None,
            type=PermissionType.READ,
            entity_type=EntityType.GROUP,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_role_permission_no_external_id_in_rg(self):
        """Logs warning when ROLE permission has no external_id in record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            external_id=None,
            type=PermissionType.READ,
            entity_type=EntityType.ROLE,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_user_permission_no_email_in_rg(self):
        """Logs warning when USER permission has no email in record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        perm = Permission(
            email=None,
            type=PermissionType.READ,
            entity_type=EntityType.USER,
        )

        await proc.on_new_record_groups([(rg, [perm])])

        proc.logger.warning.assert_called()
