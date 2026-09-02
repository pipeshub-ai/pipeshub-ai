"""Tests for new/uncovered methods in DataSourceEntitiesProcessor.

Covers the delegate methods (lines ~2029-2102), the user-group membership
and permission-migration methods (lines ~2104-2474), record-group and
app-role deletion (lines ~2529-2633), record permission helpers
(lines ~2657-2691), and the early-return in initialize() when org_id
is already supplied.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    ARANGO_NODE_ID_PARTS,
    PERMISSION_HIERARCHY,
    DataSourceEntitiesProcessor,
)
from app.models.entities import (
    AppMetadata,
    AppRole,
    AppUser,
    AppUserGroup,
    Record,
    RecordGroup,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tx_store():
    """Create a fully mocked transaction store."""
    tx = AsyncMock()
    tx.get_users = AsyncMock(return_value=[])
    tx.get_user_by_user_id = AsyncMock(return_value=None)
    tx.get_users_with_permission_to_node = AsyncMock(return_value=[])
    tx.get_app_users = AsyncMock(return_value=[])
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.get_records_by_status = AsyncMock(return_value=[])
    tx.get_app_by_id = AsyncMock(return_value=None)
    tx.get_user_by_email = AsyncMock(return_value=None)
    tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    tx.get_edge = AsyncMock(return_value=None)
    tx.delete_edge = AsyncMock(return_value=True)
    tx.batch_create_edges = AsyncMock()
    tx.delete_nodes_and_edges = AsyncMock()
    tx.get_edges_from_node = AsyncMock(return_value=[])
    tx.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1", "id": "org-1"}])
    tx.get_app_role_by_external_id = AsyncMock(return_value=None)
    tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx.delete_user_group_by_id = AsyncMock()
    tx.batch_create_edges = AsyncMock()
    tx.get_app_creator_user = AsyncMock(return_value=None)
    return tx


def _make_ctx(tx_store):
    """Wrap tx_store in an async context manager mock."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_processor(tx_store=None):
    """Build a DataSourceEntitiesProcessor with all dependencies mocked."""
    logger = MagicMock()
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    proc = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()

    if tx_store is None:
        tx_store = _make_tx_store()
    ctx = _make_ctx(tx_store)
    data_store_provider.transaction.return_value = ctx
    return proc, tx_store


def _make_user(user_id="user-1", email="alice@example.com", full_name="Alice"):
    user = MagicMock(spec=User)
    user.id = user_id
    user.email = email
    user.full_name = full_name
    user.is_active = True
    return user


def _make_user_group(group_id="group-1", name="Engineering", external_id="ext-g1"):
    group = MagicMock(spec=AppUserGroup)
    group.id = group_id
    group.name = name
    group.external_id = external_id
    return group


def _make_app_role(role_id="role-1", name="Admin", external_id="ext-r1"):
    role = MagicMock(spec=AppRole)
    role.id = role_id
    role.name = name
    role.external_id = external_id
    return role


def _make_record_group(rg_id="rg-1", name="Shared Drive", external_id="ext-rg1"):
    rg = MagicMock(spec=RecordGroup)
    rg.id = rg_id
    rg.name = name
    rg.external_id = external_id
    return rg


# ===========================================================================
# initialize() early-return when org_id is supplied
# ===========================================================================


class TestInitializeWithOrgId:
    @pytest.mark.asyncio
    async def test_initialize_with_org_id_skips_db_lookup(self):
        """When org_id is supplied, initialize() sets it directly and skips get_all_orgs."""
        logger = MagicMock()
        data_store = MagicMock()
        config_svc = AsyncMock()
        proc = DataSourceEntitiesProcessor(logger, data_store, config_svc)

        tx_store = _make_tx_store()
        ctx = _make_ctx(tx_store)
        data_store.transaction.return_value = ctx

        with (
            patch(
                "app.services.messaging.utils.MessagingUtils.create_producer_config_from_service",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "app.connectors.core.base.data_processor.data_source_entities_processor.MessagingFactory"
            ) as MockFactory,
        ):
            mock_producer = AsyncMock()
            MockFactory.create_producer.return_value = mock_producer

            await proc.initialize(org_id="supplied-org")

        assert proc.org_id == "supplied-org"
        mock_producer.initialize.assert_awaited_once()
        # transaction() should NOT have been called because we returned early
        data_store.transaction.assert_not_called()


# ===========================================================================
# Simple delegate methods (lines ~2029-2102)
# ===========================================================================


class TestDelegateMethods:
    """Test the thin delegation methods that open a transaction and call tx_store."""

    @pytest.mark.asyncio
    async def test_get_all_active_users(self):
        proc, tx = _make_processor()
        sentinel = [_make_user()]
        tx.get_users.return_value = sentinel
        result = await proc.get_all_active_users()
        assert result is sentinel
        tx.get_users.assert_awaited_once_with("org-1", active=True)

    @pytest.mark.asyncio
    async def test_get_user_by_user_id_found(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_user_id.return_value = user
        result = await proc.get_user_by_user_id("user-1")
        assert result is user

    @pytest.mark.asyncio
    async def test_get_user_by_user_id_not_found(self):
        proc, tx = _make_processor()
        tx.get_user_by_user_id.return_value = None
        result = await proc.get_user_by_user_id("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_user_id_returns_dict(self):
        """When tx_store returns a dict, the method converts via User.from_arango_user."""
        proc, tx = _make_processor()
        raw = {"_key": "u1", "email": "a@b.com", "firstName": "A", "lastName": "B"}
        tx.get_user_by_user_id.return_value = raw
        with patch.object(User, "from_arango_user", return_value=_make_user()) as mock_from:
            result = await proc.get_user_by_user_id("u1")
            mock_from.assert_called_once_with(raw)
            assert result is not None

    @pytest.mark.asyncio
    async def test_get_users_with_permission_to_node(self):
        proc, tx = _make_processor()
        sentinel = [_make_user()]
        tx.get_users_with_permission_to_node.return_value = sentinel
        result = await proc.get_users_with_permission_to_node("node-1", "records")
        assert result is sentinel

    @pytest.mark.asyncio
    async def test_get_all_app_users(self):
        proc, tx = _make_processor()
        sentinel = [MagicMock(spec=AppUser)]
        tx.get_app_users.return_value = sentinel
        result = await proc.get_all_app_users("conn-1")
        assert result is sentinel
        tx.get_app_users.assert_awaited_once_with("org-1", "conn-1")

    @pytest.mark.asyncio
    async def test_get_record_by_external_id(self):
        proc, tx = _make_processor()
        mock_rec = MagicMock(spec=Record)
        tx.get_record_by_external_id.return_value = mock_rec
        result = await proc.get_record_by_external_id("conn-1", "ext-rec-1")
        assert result is mock_rec

    @pytest.mark.asyncio
    async def test_get_records_by_parent(self):
        proc, tx = _make_processor()
        sentinel = [MagicMock(spec=Record)]
        tx.get_records_by_parent.return_value = sentinel
        result = await proc.get_records_by_parent("conn-1", "parent-ext-1", record_type="FILE")
        assert result is sentinel
        tx.get_records_by_parent.assert_awaited_once_with(
            connector_id="conn-1",
            parent_external_record_id="parent-ext-1",
            record_type="FILE",
        )

    @pytest.mark.asyncio
    async def test_get_placeholder_records(self):
        proc, tx = _make_processor()
        sentinel = [MagicMock(spec=Record)]
        tx.get_records_by_status.return_value = sentinel
        result = await proc.get_placeholder_records("conn-1", record_group_id="rg-1")
        assert result is sentinel
        tx.get_records_by_status.assert_awaited_once_with(
            org_id="org-1",
            connector_id="conn-1",
            status_filters=None,
            record_group_id="rg-1",
            is_placeholder=True,
        )

    @pytest.mark.asyncio
    async def test_get_app_by_id(self):
        proc, tx = _make_processor()
        mock_app = MagicMock(spec=AppMetadata)
        tx.get_app_by_id.return_value = mock_app
        result = await proc.get_app_by_id("conn-1")
        assert result is mock_app

    @pytest.mark.asyncio
    async def test_get_app_creator_user(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_app_creator_user.return_value = user
        result = await proc.get_app_creator_user("conn-1")
        assert result is user


# ===========================================================================
# on_user_group_member_removed (lines ~2104-2162)
# ===========================================================================


class TestOnUserGroupMemberRemoved:

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = None
        result = await proc.on_user_group_member_removed("ext-g1", "missing@x.com", "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = _make_user()
        tx.get_user_group_by_external_id.return_value = None
        result = await proc.on_user_group_member_removed("ext-g1", "alice@x.com", "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_edge_deleted_successfully(self):
        proc, tx = _make_processor()
        user = _make_user()
        group = _make_user_group()
        tx.get_user_by_email.return_value = user
        tx.get_user_group_by_external_id.return_value = group
        tx.delete_edge.return_value = True
        result = await proc.on_user_group_member_removed("ext-g1", "alice@x.com", "conn-1")
        assert result is True
        tx.delete_edge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_edge_to_delete(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = _make_user()
        tx.get_user_group_by_external_id.return_value = _make_user_group()
        tx.delete_edge.return_value = False
        result = await proc.on_user_group_member_removed("ext-g1", "alice@x.com", "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.side_effect = Exception("db down")
        result = await proc.on_user_group_member_removed("ext-g1", "alice@x.com", "conn-1")
        assert result is False


# ===========================================================================
# on_user_group_member_added (lines ~2165-2239)
# ===========================================================================


class TestOnUserGroupMemberAdded:

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = None
        result = await proc.on_user_group_member_added("ext-g1", "missing@x.com", PermissionType.READ, "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = _make_user()
        tx.get_user_group_by_external_id.return_value = None
        result = await proc.on_user_group_member_added("ext-g1", "alice@x.com", PermissionType.READ, "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_edge_already_exists(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = _make_user()
        tx.get_user_group_by_external_id.return_value = _make_user_group()
        tx.get_edge.return_value = {"_key": "existing"}
        result = await proc.on_user_group_member_added("ext-g1", "alice@x.com", PermissionType.READ, "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_creates_permission_edge(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = _make_user()
        tx.get_user_group_by_external_id.return_value = _make_user_group()
        tx.get_edge.return_value = None  # no existing edge
        result = await proc.on_user_group_member_added("ext-g1", "alice@x.com", PermissionType.READ, "conn-1")
        assert result is True
        tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.side_effect = Exception("db down")
        result = await proc.on_user_group_member_added("ext-g1", "a@x.com", PermissionType.READ, "conn-1")
        assert result is False


# ===========================================================================
# on_user_group_deleted (lines ~2242-2291)
# ===========================================================================


class TestOnUserGroupDeleted:

    @pytest.mark.asyncio
    async def test_group_not_found_returns_true(self):
        proc, tx = _make_processor()
        tx.get_user_group_by_external_id.return_value = None
        result = await proc.on_user_group_deleted("ext-g1", "conn-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_deletes_group_and_edges(self):
        proc, tx = _make_processor()
        group = _make_user_group()
        tx.get_user_group_by_external_id.return_value = group
        result = await proc.on_user_group_deleted("ext-g1", "conn-1")
        assert result is True
        tx.delete_nodes_and_edges.assert_awaited_once_with(
            [group.id], CollectionNames.GROUPS.value
        )

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        proc, tx = _make_processor()
        tx.get_user_group_by_external_id.side_effect = Exception("db down")
        result = await proc.on_user_group_deleted("ext-g1", "conn-1")
        assert result is False


# ===========================================================================
# delete_user_group_by_id (lines ~2294-2307)
# ===========================================================================


class TestDeleteUserGroupById:

    @pytest.mark.asyncio
    async def test_deletes_successfully(self):
        proc, tx = _make_processor()
        await proc.delete_user_group_by_id("group-1")
        tx.delete_user_group_by_id.assert_awaited_once_with("group-1")

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        proc, tx = _make_processor()
        tx.delete_user_group_by_id.side_effect = Exception("db error")
        with pytest.raises(Exception, match="db error"):
            await proc.delete_user_group_by_id("group-1")


# ===========================================================================
# migrate_group_permissions_to_user (lines ~2310-2474)
# ===========================================================================


class TestMigrateGroupPermissionsToUser:

    @pytest.mark.asyncio
    async def test_user_not_found_returns_none(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = None
        result = await proc.migrate_group_permissions_to_user("group-1", "missing@x.com", "conn-1", tx)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_permissions_returns_none(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = _make_user()
        tx.get_edges_from_node.return_value = []
        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1", tx)
        assert result is None

    @pytest.mark.asyncio
    async def test_creates_new_permission_edges(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_email.return_value = user
        tx.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "READER"},
        ]
        tx.get_edge.return_value = None  # no existing permission for user

        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1", tx)
        assert result is None
        tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upgrades_existing_permission(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_email.return_value = user
        tx.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "WRITER"},
        ]
        # User already has READER permission (lower), should be upgraded to WRITER
        tx.get_edge.return_value = {"role": "READER"}
        tx.delete_edge.return_value = True

        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1", tx)
        assert result is None
        # Should have deleted old edge and batch-created new one
        tx.delete_edge.assert_awaited()
        tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_existing_permission_is_higher(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_email.return_value = user
        tx.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "READER"},
        ]
        # User already has OWNER (higher than READER group permission)
        tx.get_edge.return_value = {"role": "OWNER"}

        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1", tx)
        assert result is None
        # batch_create_edges should not be called since no new edges needed
        tx.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_edges_with_bad_to_format(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_email.return_value = user
        tx.get_edges_from_node.return_value = [
            {"_to": "bad-format-no-slash", "role": "READER"},
            {"role": "READER"},  # missing _to entirely
        ]

        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1", tx)
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_invalid_permission_role(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_email.return_value = user
        tx.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "INVALID_ROLE"},
        ]
        tx.get_edge.return_value = None

        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1", tx)
        assert result is None
        # Should have defaulted to READ and created edge
        tx.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_opens_own_transaction_when_none_provided(self):
        """When tx_store is None, the method creates its own transaction."""
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = None
        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_in_edge_processing_continues(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_email.return_value = user
        tx.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "READER", "_key": "e1"},
            {"_to": f"{CollectionNames.RECORDS.value}/rec-2", "role": "READER", "_key": "e2"},
        ]
        # First edge throws, second succeeds
        tx.get_edge.side_effect = [Exception("transient"), None]

        result = await proc.migrate_group_permissions_to_user("group-1", "alice@x.com", "conn-1", tx)
        assert result is None
        # Second edge should still have been processed
        tx.batch_create_edges.assert_awaited_once()


# ===========================================================================
# migrate_group_to_user_by_external_id (lines ~2477-2526)
# ===========================================================================


class TestMigrateGroupToUserByExternalId:

    @pytest.mark.asyncio
    async def test_group_not_found_returns_early(self):
        proc, tx = _make_processor()
        tx.get_user_group_by_external_id.return_value = None
        await proc.migrate_group_to_user_by_external_id("ext-g1", "alice@x.com", "conn-1")
        # delete_user_group_by_id should not be called
        tx.delete_user_group_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_migrates_and_deletes_group(self):
        proc, tx = _make_processor()
        group = _make_user_group()
        tx.get_user_group_by_external_id.return_value = group
        tx.get_user_by_email.return_value = _make_user()
        tx.get_edges_from_node.return_value = []

        await proc.migrate_group_to_user_by_external_id("ext-g1", "alice@x.com", "conn-1")
        tx.delete_user_group_by_id.assert_awaited_once_with(group.id)


# ===========================================================================
# on_app_role_deleted (lines ~2529-2578)
# ===========================================================================


class TestOnAppRoleDeleted:

    @pytest.mark.asyncio
    async def test_role_not_found(self):
        proc, tx = _make_processor()
        tx.get_app_role_by_external_id.return_value = None
        result = await proc.on_app_role_deleted("ext-r1", "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_deletes_role_and_edges(self):
        proc, tx = _make_processor()
        role = _make_app_role()
        tx.get_app_role_by_external_id.return_value = role
        result = await proc.on_app_role_deleted("ext-r1", "conn-1")
        assert result is True
        tx.delete_nodes_and_edges.assert_awaited_once_with(
            [role.id], CollectionNames.ROLES.value
        )

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        proc, tx = _make_processor()
        tx.get_app_role_by_external_id.side_effect = Exception("db error")
        result = await proc.on_app_role_deleted("ext-r1", "conn-1")
        assert result is False


# ===========================================================================
# on_record_group_deleted (lines ~2581-2633)
# ===========================================================================


class TestOnRecordGroupDeleted:

    @pytest.mark.asyncio
    async def test_record_group_not_found(self):
        proc, tx = _make_processor()
        tx.get_record_group_by_external_id.return_value = None
        result = await proc.on_record_group_deleted("ext-rg1", "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_deletes_record_group_and_edges(self):
        proc, tx = _make_processor()
        rg = _make_record_group()
        tx.get_record_group_by_external_id.return_value = rg
        result = await proc.on_record_group_deleted("ext-rg1", "conn-1")
        assert result is True
        tx.delete_nodes_and_edges.assert_awaited_once_with(
            [rg.id], CollectionNames.RECORD_GROUPS.value
        )

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        proc, tx = _make_processor()
        tx.get_record_group_by_external_id.side_effect = Exception("db error")
        result = await proc.on_record_group_deleted("ext-rg1", "conn-1")
        assert result is False


# ===========================================================================
# add_permission_to_record / delete_permission_from_record (lines ~2657-2684)
# ===========================================================================


class TestRecordPermissionHelpers:

    @pytest.mark.asyncio
    async def test_delete_permission_user_not_found(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = None
        await proc.delete_permission_from_record("rec-1", "missing@x.com")
        tx.delete_edge.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_permission_success(self):
        proc, tx = _make_processor()
        user = _make_user()
        tx.get_user_by_email.return_value = user
        tx.delete_edge.return_value = True
        await proc.delete_permission_from_record("rec-1", "alice@x.com")
        tx.delete_edge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_permission_edge_not_found(self):
        proc, tx = _make_processor()
        tx.get_user_by_email.return_value = _make_user()
        tx.delete_edge.return_value = False
        await proc.delete_permission_from_record("rec-1", "alice@x.com")
        # Should log warning but not raise


# ===========================================================================
# _delete_group_organization_edges (lines ~2636-2654)
# ===========================================================================


class TestDeleteGroupOrganizationEdges:

    @pytest.mark.asyncio
    async def test_edge_deleted_successfully(self):
        proc, tx = _make_processor()
        tx.delete_edge.return_value = True
        await proc._delete_group_organization_edges(tx, "group-1")
        tx.delete_edge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_edge_found(self):
        proc, tx = _make_processor()
        tx.delete_edge.return_value = False
        await proc._delete_group_organization_edges(tx, "group-1")

    @pytest.mark.asyncio
    async def test_exception_logged(self):
        proc, tx = _make_processor()
        tx.delete_edge.side_effect = Exception("db error")
        await proc._delete_group_organization_edges(tx, "group-1")
        proc.logger.error.assert_called()
