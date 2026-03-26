"""Tests for app.connectors.core.base.data_processor.data_source_entities_processor."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors as ConnectorsEnum,
    EntityRelations,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    PERMISSION_HIERARCHY,
    DataSourceEntitiesProcessor,
    RecordGroupWithPermissions,
    UserGroupWithMembers,
)
from app.models.entities import (
    FileRecord,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordType,
    TicketRecord,
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
    """Build a minimal Record for testing."""
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
    tx_store.delete_edge = AsyncMock()
    tx_store.create_inherit_permissions_relation_record_group = AsyncMock()
    tx_store.delete_inherit_permissions_relation_record_group = AsyncMock()
    tx_store.delete_edges_by_relationship_types = AsyncMock(return_value=0)
    tx_store.batch_create_entity_relations = AsyncMock()
    tx_store.get_edges_from_node = AsyncMock(return_value=[])
    tx_store.delete_record_by_key = AsyncMock()
    tx_store.get_app_role_by_external_id = AsyncMock(return_value=None)
    return tx_store


# ===========================================================================
# Constructor and initialization
# ===========================================================================


class TestConstructor:
    def test_init_sets_attributes(self):
        """Constructor sets logger, data_store_provider, and config_service."""
        logger = MagicMock()
        data_store = MagicMock()
        config_svc = AsyncMock()

        proc = DataSourceEntitiesProcessor(logger, data_store, config_svc)

        assert proc.logger is logger
        assert proc.data_store_provider is data_store
        assert proc.config_service is config_svc
        assert proc.org_id == ""


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_success(self):
        """Initialize sets up messaging_producer and org_id."""
        logger = MagicMock()
        data_store = MagicMock()
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={
            "brokers": "broker1:9092,broker2:9092",
            "client_id": "test",
            "ssl": False,
            "sasl": None,
        })

        proc = DataSourceEntitiesProcessor(logger, data_store, config_svc)

        tx_store = _make_tx_store()
        # Set up context manager mock
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        data_store.transaction.return_value = ctx

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.MessagingFactory"
        ) as MockFactory:
            mock_producer = AsyncMock()
            MockFactory.create_producer.return_value = mock_producer

            await proc.initialize()

        assert proc.org_id == "org-1"
        mock_producer.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_no_orgs_raises(self):
        """Initialize raises when no organizations found."""
        logger = MagicMock()
        data_store = MagicMock()
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={
            "brokers": "broker1:9092",
            "client_id": "test",
            "ssl": False,
            "sasl": None,
        })

        proc = DataSourceEntitiesProcessor(logger, data_store, config_svc)

        tx_store = AsyncMock()
        tx_store.get_all_orgs = AsyncMock(return_value=[])
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        data_store.transaction.return_value = ctx

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.MessagingFactory"
        ) as MockFactory:
            mock_producer = AsyncMock()
            MockFactory.create_producer.return_value = mock_producer

            with pytest.raises(Exception, match="No organizations found"):
                await proc.initialize()

    @pytest.mark.asyncio
    async def test_initialize_bootstrap_servers_as_list(self):
        """Initialize handles bootstrap_servers already as a list."""
        logger = MagicMock()
        data_store = MagicMock()
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={
            "bootstrap_servers": ["broker1:9092"],
            "client_id": "test",
            "ssl": False,
            "sasl": None,
        })

        proc = DataSourceEntitiesProcessor(logger, data_store, config_svc)

        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        data_store.transaction.return_value = ctx

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.MessagingFactory"
        ) as MockFactory:
            mock_producer = AsyncMock()
            MockFactory.create_producer.return_value = mock_producer

            await proc.initialize()

        assert proc.org_id == "org-1"


# ===========================================================================
# Permission hierarchy
# ===========================================================================


class TestPermissionHierarchy:
    def test_hierarchy_values(self):
        """Permission hierarchy has correct relative ordering."""
        assert PERMISSION_HIERARCHY["READER"] < PERMISSION_HIERARCHY["COMMENTER"]
        assert PERMISSION_HIERARCHY["COMMENTER"] < PERMISSION_HIERARCHY["WRITER"]
        assert PERMISSION_HIERARCHY["WRITER"] < PERMISSION_HIERARCHY["OWNER"]


# ===========================================================================
# _handle_new_record
# ===========================================================================


class TestHandleNewRecord:
    @pytest.mark.asyncio
    async def test_upserts_record(self):
        """_handle_new_record calls batch_upsert_records."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()

        await proc._handle_new_record(record, tx_store)

        tx_store.batch_upsert_records.assert_awaited_once_with([record])


# ===========================================================================
# _handle_updated_record
# ===========================================================================


class TestHandleUpdatedRecord:
    @pytest.mark.asyncio
    async def test_upserts_updated_record(self):
        """_handle_updated_record calls batch_upsert_records."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record(version=2)
        existing = _make_record(version=1)

        await proc._handle_updated_record(record, existing, tx_store)

        tx_store.batch_upsert_records.assert_awaited_once_with([record])


# ===========================================================================
# _handle_record_group
# ===========================================================================


class TestHandleRecordGroup:
    @pytest.mark.asyncio
    async def test_no_external_group_id_returns_none(self):
        """Returns None when record has no external_record_group_id."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.external_record_group_id = None

        result = await proc._handle_record_group(record, tx_store)

        assert result is None

    @pytest.mark.asyncio
    async def test_creates_new_group(self):
        """Creates a new record group when none exists."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.external_record_group_id = "ext-group-1"
        record.record_group_type = "DRIVE"

        # No existing group
        tx_store.get_record_group_by_external_id.return_value = None

        # Mock batch_upsert to set ID
        async def _mock_upsert(groups):
            for g in groups:
                if not g.id:
                    g.id = "new-group-id"

        tx_store.batch_upsert_record_groups = AsyncMock(side_effect=_mock_upsert)

        result = await proc._handle_record_group(record, tx_store)

        tx_store.batch_upsert_record_groups.assert_awaited_once()
        assert record.record_group_id is not None

    @pytest.mark.asyncio
    async def test_uses_existing_group(self):
        """Uses existing record group when found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.external_record_group_id = "ext-group-1"

        existing_group = MagicMock()
        existing_group.id = "existing-group-id"
        tx_store.get_record_group_by_external_id.return_value = existing_group

        result = await proc._handle_record_group(record, tx_store)

        assert result == "existing-group-id"
        assert record.record_group_id == "existing-group-id"


# ===========================================================================
# _process_record
# ===========================================================================


class TestProcessRecord:
    @pytest.mark.asyncio
    async def test_new_record_processed(self):
        """New record is upserted and returned."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()

        result = await proc._process_record(record, [], tx_store)

        assert result is not None
        assert result.org_id == "org-1"
        tx_store.batch_upsert_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_existing_record_with_new_revision(self):
        """Existing record with different revision gets updated."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        existing = _make_record(version=1)
        existing.id = "existing-id"
        existing.external_revision_id = "rev-1"
        tx_store.get_record_by_external_id.return_value = existing

        record = _make_record(version=2)
        record.external_revision_id = "rev-2"

        result = await proc._process_record(record, [], tx_store)

        assert result.id == "existing-id"
        # Should have been called at least once for initial upsert
        assert tx_store.batch_upsert_records.call_count >= 1

    @pytest.mark.asyncio
    async def test_existing_record_same_revision_not_updated(self):
        """Existing record with same revision is not updated again."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        existing = _make_record(version=1)
        existing.id = "existing-id"
        existing.external_revision_id = "rev-1"
        tx_store.get_record_by_external_id.return_value = existing

        record = _make_record(version=1)
        record.external_revision_id = "rev-1"

        result = await proc._process_record(record, [], tx_store)

        assert result.id == "existing-id"


# ===========================================================================
# on_new_records
# ===========================================================================


class TestOnNewRecords:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        """Empty list logs warning and returns."""
        proc = _make_processor()

        await proc.on_new_records([])

        proc.logger.warning.assert_called()
        proc.messaging_producer.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publishes_events(self):
        """New records are processed and events published."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"

        await proc.on_new_records([(record, [])])

        proc.messaging_producer.send_message.assert_awaited()

    @pytest.mark.asyncio
    async def test_auto_index_off_skips_publish(self):
        """Records with AUTO_INDEX_OFF don't get events published."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        await proc.on_new_records([(record, [])])

        proc.messaging_producer.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """Transaction errors propagate."""
        proc = _make_processor()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("tx error"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        with pytest.raises(RuntimeError, match="tx error"):
            await proc.on_new_records([(_make_record(), [])])


# ===========================================================================
# on_record_content_update
# ===========================================================================


class TestOnRecordContentUpdate:
    @pytest.mark.asyncio
    async def test_publishes_update_event(self):
        """Content update publishes updateRecord event."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"

        await proc.on_record_content_update(record)

        proc.messaging_producer.send_message.assert_awaited()
        call_args = proc.messaging_producer.send_message.call_args
        assert call_args[0][1]["eventType"] == "updateRecord"

    @pytest.mark.asyncio
    async def test_auto_index_off_skips(self):
        """Records with AUTO_INDEX_OFF don't get update events."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        await proc.on_record_content_update(record)

        proc.messaging_producer.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resets_status_to_queued_before_update(self):
        """Resets indexing status to QUEUED when not already QUEUED/EMPTY."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.indexing_status = ProgressStatus.COMPLETED.value

        mock_db_record = MagicMock()
        mock_db_record.indexing_status = ProgressStatus.COMPLETED.value
        tx_store.get_record_by_key.return_value = mock_db_record

        await proc.on_record_content_update(record)

        # Should have called batch_upsert_nodes for status reset
        tx_store.batch_upsert_nodes.assert_awaited()
        proc.messaging_producer.send_message.assert_awaited()


# ===========================================================================
# on_record_metadata_update
# ===========================================================================


class TestOnRecordMetadataUpdate:
    @pytest.mark.asyncio
    async def test_publishes_metadata_update(self):
        """Metadata update processes record and calls update."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"

        existing = _make_record()
        existing.id = "rec-1"
        tx_store.get_record_by_external_id.return_value = existing

        await proc.on_record_metadata_update(record)

        # Should have called batch_upsert_records for the update
        assert tx_store.batch_upsert_records.call_count >= 1


# ===========================================================================
# on_record_deleted
# ===========================================================================


class TestOnRecordDeleted:
    @pytest.mark.asyncio
    async def test_deletes_record(self):
        """Calls delete_record_by_key within a transaction."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        await proc.on_record_deleted("rec-1")

        tx_store.delete_record_by_key.assert_awaited_once_with("rec-1")


# ===========================================================================
# reindex_existing_records
# ===========================================================================


class TestReindexExistingRecords:
    @pytest.mark.asyncio
    async def test_empty_list_noop(self):
        """Empty list logs and returns without errors."""
        proc = _make_processor()

        await proc.reindex_existing_records([])

        proc.messaging_producer.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publishes_reindex_events(self):
        """Publishes reindexRecord events for each record."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record1 = _make_record()
        record1.id = "rec-1"
        record2 = _make_record(external_record_id="ext-2")
        record2.id = "rec-2"

        await proc.reindex_existing_records([record1, record2])

        assert proc.messaging_producer.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        """Errors during publishing propagate."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        proc.messaging_producer.send_message = AsyncMock(
            side_effect=RuntimeError("kafka error")
        )

        record = _make_record()
        record.id = "rec-1"

        with pytest.raises(RuntimeError, match="kafka error"):
            await proc.reindex_existing_records([record])


# ===========================================================================
# _reset_indexing_status_to_queued
# ===========================================================================


class TestResetIndexingStatusToQueued:
    @pytest.mark.asyncio
    async def test_resets_status(self):
        """Resets indexing status to QUEUED."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_record = MagicMock()
        mock_record.indexing_status = ProgressStatus.COMPLETED.value
        tx_store.get_record_by_key.return_value = mock_record

        await proc._reset_indexing_status_to_queued("rec-1", tx_store)

        tx_store.batch_upsert_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_if_already_queued(self):
        """Does not reset if already QUEUED."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_record = MagicMock()
        mock_record.indexing_status = ProgressStatus.QUEUED.value
        tx_store.get_record_by_key.return_value = mock_record

        await proc._reset_indexing_status_to_queued("rec-1", tx_store)

        tx_store.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_if_empty_status(self):
        """Does not reset if status is EMPTY."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_record = MagicMock()
        mock_record.indexing_status = ProgressStatus.EMPTY.value
        tx_store.get_record_by_key.return_value = mock_record

        await proc._reset_indexing_status_to_queued("rec-1", tx_store)

        tx_store.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_record_not_found_logs_warning(self):
        """Logs warning when record not found."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_key.return_value = None

        await proc._reset_indexing_status_to_queued("missing", tx_store)

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_exception_logged_not_raised(self):
        """Errors are logged but do not propagate."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_key.side_effect = RuntimeError("db error")

        # Should not raise
        await proc._reset_indexing_status_to_queued("rec-1", tx_store)

        proc.logger.error.assert_called()


# ===========================================================================
# _create_placeholder_parent_record
# ===========================================================================


class TestCreatePlaceholderParentRecord:
    def test_file_type(self):
        """Creates FileRecord placeholder for FILE type."""
        proc = _make_processor()
        record = _make_record()

        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.FILE, record
        )

        assert isinstance(result, FileRecord)
        assert result.external_record_id == "parent-ext-1"

    def test_ticket_type(self):
        """Creates TicketRecord placeholder for TICKET type."""
        proc = _make_processor()
        record = _make_record()

        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.TICKET, record
        )

        assert isinstance(result, TicketRecord)

    def test_project_type(self):
        """Creates ProjectRecord placeholder for PROJECT type."""
        proc = _make_processor()
        record = _make_record()

        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.PROJECT, record
        )

        assert isinstance(result, ProjectRecord)

    def test_unsupported_type_raises(self):
        """Raises ValueError for unsupported record type."""
        proc = _make_processor()
        record = _make_record()

        with pytest.raises(ValueError, match="Unsupported parent record type"):
            proc._create_placeholder_parent_record(
                "parent-ext-1", RecordType.OTHERS, record
            )


# ===========================================================================
# on_new_record_groups
# ===========================================================================


class TestOnNewRecordGroups:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        """Empty list logs warning and returns."""
        proc = _make_processor()

        await proc.on_new_record_groups([])

        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_creates_new_record_group(self):
        """New record group is created with edges."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        await proc.on_new_record_groups([(rg, [])])

        tx_store.batch_upsert_record_groups.assert_awaited()
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_record_group(self):
        """Existing record group is updated with fresh timestamp."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        existing_rg = MagicMock()
        existing_rg.id = "existing-rg-id"
        tx_store.get_record_group_by_external_id.return_value = existing_rg

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )

        await proc.on_new_record_groups([(rg, [])])

        assert rg.id == "existing-rg-id"
        tx_store.batch_upsert_record_groups.assert_awaited()


# ===========================================================================
# _handle_record_permissions
# ===========================================================================


class TestHandleRecordPermissions:
    @pytest.mark.asyncio
    async def test_user_permission_with_known_user(self):
        """Creates permission edge for known user."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.USER.value
        permission.email = "user@example.com"
        permission.external_id = None
        permission.to_arango_permission = MagicMock(return_value={"_from": "u/1", "_to": "r/1"})

        await proc._handle_record_permissions(record, [permission], tx_store)

        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_user_permission_unknown_user_skipped(self):
        """External user without record in DB is skipped."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_user_by_email.return_value = None

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.USER.value
        permission.email = "external@example.com"
        permission.external_id = None

        await proc._handle_record_permissions(record, [permission], tx_store)

        # No edges created for unknown user
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_permission(self):
        """Creates permission edge for known group."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_group = MagicMock()
        mock_group.id = "group-1"
        tx_store.get_user_group_by_external_id.return_value = mock_group

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.GROUP.value
        permission.external_id = "ext-group-1"
        permission.email = None
        permission.to_arango_permission = MagicMock(return_value={"_from": "g/1", "_to": "r/1"})

        await proc._handle_record_permissions(record, [permission], tx_store)

        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_org_permission(self):
        """Creates permission edge for organization entity."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.ORG.value
        permission.email = None
        permission.external_id = None
        permission.to_arango_permission = MagicMock(return_value={"_from": "o/1", "_to": "r/1"})

        await proc._handle_record_permissions(record, [permission], tx_store)

        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_logged(self):
        """Exceptions during permission handling are logged."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_user_by_email.side_effect = RuntimeError("db error")

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.USER.value
        permission.email = "user@example.com"

        await proc._handle_record_permissions(record, [permission], tx_store)

        proc.logger.error.assert_called()


# ===========================================================================
# on_updated_record_permissions
# ===========================================================================


class TestOnUpdatedRecordPermissions:
    @pytest.mark.asyncio
    async def test_deletes_and_recreates_permissions(self):
        """Old permissions are deleted and new ones created."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = False
        record.external_record_group_id = None

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        permission = MagicMock()
        permission.entity_type = EntityType.USER.value
        permission.email = "user@example.com"
        permission.external_id = None
        permission.to_arango_permission = MagicMock(return_value={"_from": "u/1", "_to": "r/1"})

        await proc.on_updated_record_permissions(record, [permission])

        tx_store.delete_edges_to.assert_awaited()


# ===========================================================================
# _prepare_ticket_user_edge
# ===========================================================================


class TestPrepareTicketUserEdge:
    @pytest.mark.asyncio
    async def test_no_email_returns_none(self):
        """Returns None when no email is provided."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ticket = MagicMock()

        result = await proc._prepare_ticket_user_edge(
            ticket, None, EntityRelations.ASSIGNED_TO,
            "assignee_source_timestamp", "source_updated_at",
            tx_store, "ASSIGNED_TO"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_user_not_found_returns_none(self):
        """Returns None when user is not found by email."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_user_by_email.return_value = None
        ticket = MagicMock()

        result = await proc._prepare_ticket_user_edge(
            ticket, "unknown@example.com", EntityRelations.ASSIGNED_TO,
            "assignee_source_timestamp", "source_updated_at",
            tx_store, "ASSIGNED_TO"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_edge_data(self):
        """Returns edge data when user is found."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        ticket = MagicMock()
        ticket.id = "ticket-1"
        ticket.assignee_source_timestamp = 12345
        ticket.source_updated_at = 10000

        result = await proc._prepare_ticket_user_edge(
            ticket, "user@example.com", EntityRelations.ASSIGNED_TO,
            "assignee_source_timestamp", "source_updated_at",
            tx_store, "ASSIGNED_TO"
        )

        assert result is not None
        assert result["edgeType"] == EntityRelations.ASSIGNED_TO.value
        assert result["sourceTimestamp"] == 12345

    @pytest.mark.asyncio
    async def test_uses_fallback_timestamp(self):
        """Uses fallback timestamp when primary is None."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        ticket = MagicMock()
        ticket.id = "ticket-1"
        ticket.assignee_source_timestamp = None
        ticket.source_updated_at = 10000

        result = await proc._prepare_ticket_user_edge(
            ticket, "user@example.com", EntityRelations.ASSIGNED_TO,
            "assignee_source_timestamp", "source_updated_at",
            tx_store, "ASSIGNED_TO"
        )

        assert result is not None
        assert result["sourceTimestamp"] == 10000


# ===========================================================================
# Dataclass tests
# ===========================================================================


class TestDataclasses:
    def test_record_group_with_permissions(self):
        """RecordGroupWithPermissions holds expected fields."""
        rg = MagicMock()
        rgwp = RecordGroupWithPermissions(
            record_group=rg,
            users=[],
            user_groups=[],
            anyone_with_link=True,
        )
        assert rgwp.anyone_with_link is True
        assert rgwp.anyone_same_org is False
        assert rgwp.anyone_same_domain is False

    def test_user_group_with_members(self):
        """UserGroupWithMembers holds expected fields."""
        ug = MagicMock()
        ugwm = UserGroupWithMembers(
            user_group=ug,
            users=[("user", "perm")],
        )
        assert len(ugwm.users) == 1


# ===========================================================================
# ATTACHMENT_CONTAINER_TYPES and LINK_RELATION_TYPES
# ===========================================================================


class TestClassConstants:
    def test_attachment_container_types(self):
        """ATTACHMENT_CONTAINER_TYPES includes expected types."""
        types = DataSourceEntitiesProcessor.ATTACHMENT_CONTAINER_TYPES
        assert RecordType.MAIL in types
        assert RecordType.TICKET in types

    def test_link_relation_types(self):
        """LINK_RELATION_TYPES includes expected relation strings."""
        types = DataSourceEntitiesProcessor.LINK_RELATION_TYPES
        assert len(types) > 0
        # All should be strings (enum values)
        for t in types:
            assert isinstance(t, str)


# ===========================================================================
# _create_placeholder_parent_record - additional types
# ===========================================================================


class TestCreatePlaceholderParentRecordAdditional:
    def test_webpage_type(self):
        """Creates WebpageRecord placeholder for WEBPAGE type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import WebpageRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.WEBPAGE, record
        )
        assert isinstance(result, WebpageRecord)

    def test_confluence_page_type(self):
        """Creates WebpageRecord placeholder for CONFLUENCE_PAGE type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import WebpageRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.CONFLUENCE_PAGE, record
        )
        assert isinstance(result, WebpageRecord)

    def test_confluence_blogpost_type(self):
        """Creates WebpageRecord placeholder for CONFLUENCE_BLOGPOST type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import WebpageRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.CONFLUENCE_BLOGPOST, record
        )
        assert isinstance(result, WebpageRecord)

    def test_sharepoint_page_type(self):
        """Creates WebpageRecord placeholder for SHAREPOINT_PAGE type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import WebpageRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.SHAREPOINT_PAGE, record
        )
        assert isinstance(result, WebpageRecord)

    def test_mail_type(self):
        """Creates MailRecord placeholder for MAIL type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import MailRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.MAIL, record
        )
        assert isinstance(result, MailRecord)

    def test_group_mail_type(self):
        """Creates MailRecord placeholder for GROUP_MAIL type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import MailRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.GROUP_MAIL, record
        )
        assert isinstance(result, MailRecord)

    def test_comment_type(self):
        """Creates CommentRecord placeholder for COMMENT type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import CommentRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.COMMENT, record
        )
        assert isinstance(result, CommentRecord)
        assert result.author_source_id == ""

    def test_inline_comment_type(self):
        """Creates CommentRecord placeholder for INLINE_COMMENT type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import CommentRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.INLINE_COMMENT, record
        )
        assert isinstance(result, CommentRecord)

    def test_link_type(self):
        """Creates LinkRecord placeholder for LINK type."""
        proc = _make_processor()
        record = _make_record()
        from app.models.entities import LinkRecord
        result = proc._create_placeholder_parent_record(
            "parent-ext-1", RecordType.LINK, record
        )
        assert isinstance(result, LinkRecord)
        assert result.url == "parent-ext-1"


# ===========================================================================
# _handle_parent_record
# ===========================================================================


class TestHandleParentRecord:
    @pytest.mark.asyncio
    async def test_no_parent_external_id_does_nothing(self):
        """When record has no parent_external_record_id, does nothing."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.parent_external_record_id = None

        await proc._handle_parent_record(record, tx_store)

        tx_store.create_record_relation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_parent_child_relation(self):
        """Creates PARENT_CHILD relation when parent exists."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        parent = _make_record(external_record_id="parent-ext")
        parent.id = "parent-id"
        tx_store.get_record_by_external_id.return_value = parent

        record = _make_record()
        record.id = "child-id"
        record.parent_external_record_id = "parent-ext"
        record.parent_record_type = RecordType.WEBPAGE

        await proc._handle_parent_record(record, tx_store)

        tx_store.create_record_relation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_creates_attachment_relation_for_file_with_container_parent(self):
        """Creates ATTACHMENT relation when file record has attachment container parent."""
        from app.config.constants.arangodb import RecordRelations
        proc = _make_processor()
        tx_store = _make_tx_store()

        parent = _make_record(external_record_id="parent-ext")
        parent.id = "parent-id"
        tx_store.get_record_by_external_id.return_value = parent

        record = _make_record()
        record.id = "child-id"
        record.record_type = RecordType.FILE
        record.parent_external_record_id = "parent-ext"
        record.parent_record_type = RecordType.MAIL

        await proc._handle_parent_record(record, tx_store)

        call_args = tx_store.create_record_relation.call_args
        assert call_args[0][2] == RecordRelations.ATTACHMENT.value

    @pytest.mark.asyncio
    async def test_creates_placeholder_parent_when_not_found(self):
        """Creates placeholder parent record if parent not found in DB."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id.return_value = None

        record = _make_record()
        record.id = "child-id"
        record.parent_external_record_id = "parent-ext"
        record.parent_record_type = RecordType.TICKET
        record.external_record_group_id = None

        await proc._handle_parent_record(record, tx_store)

        tx_store.batch_upsert_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_deletes_old_parent_edge_when_parent_changed(self):
        """Deletes old parent-child edge when parent external ID changes."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        existing = _make_record(external_record_id="ext-1")
        existing.id = "child-id"
        existing.parent_external_record_id = "old-parent"

        record = _make_record()
        record.id = "child-id"
        record.parent_external_record_id = "new-parent"
        record.parent_record_type = RecordType.TICKET

        await proc._handle_parent_record(record, tx_store, existing_record=existing)

        tx_store.delete_parent_child_edge_to_record.assert_awaited_once_with("child-id")


# ===========================================================================
# _handle_related_external_records
# ===========================================================================


class TestHandleRelatedExternalRecords:
    @pytest.mark.asyncio
    async def test_deletes_existing_link_edges(self):
        """Always deletes existing link relation edges first."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"

        await proc._handle_related_external_records(record, [], tx_store)

        tx_store.delete_edges_by_relationship_types.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_non_related_external_record_type(self):
        """Skips items that are not RelatedExternalRecord instances."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"

        # Pass a plain dict instead of RelatedExternalRecord
        await proc._handle_related_external_records(record, [{"not": "valid"}], tx_store)

        # create_record_relation should NOT have been called
        tx_store.create_record_relation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_relation_for_existing_related_record(self):
        """Creates relation when related record exists in DB."""
        from app.config.constants.arangodb import RecordRelations
        from app.models.entities import RelatedExternalRecord

        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"

        related = _make_record(external_record_id="related-ext")
        related.id = "related-id"
        # First call returns None (for _process_record lookup), next returns the related record
        tx_store.get_record_by_external_id.return_value = related

        rel_ext = RelatedExternalRecord(
            external_record_id="related-ext",
            record_type=RecordType.TICKET,
            relation_type=RecordRelations.BLOCKS,
        )

        await proc._handle_related_external_records(record, [rel_ext], tx_store)

        tx_store.create_record_relation.assert_awaited()

    @pytest.mark.asyncio
    async def test_creates_placeholder_for_missing_related_record(self):
        """Creates placeholder record when related record not in DB."""
        from app.config.constants.arangodb import RecordRelations
        from app.models.entities import RelatedExternalRecord

        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_record_by_external_id.return_value = None
        record = _make_record()
        record.id = "rec-1"
        record.external_record_group_id = None

        rel_ext = RelatedExternalRecord(
            external_record_id="related-ext",
            record_type=RecordType.TICKET,
            relation_type=RecordRelations.DEPENDS_ON,
        )

        await proc._handle_related_external_records(record, [rel_ext], tx_store)

        tx_store.batch_upsert_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_empty_external_record_id(self):
        """Skips related records with empty external_record_id."""
        from app.config.constants.arangodb import RecordRelations
        from app.models.entities import RelatedExternalRecord

        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"

        rel_ext = RelatedExternalRecord(
            external_record_id="",
            record_type=RecordType.TICKET,
            relation_type=RecordRelations.BLOCKS,
        )

        await proc._handle_related_external_records(record, [rel_ext], tx_store)

        tx_store.create_record_relation.assert_not_awaited()


# ===========================================================================
# _link_record_to_group
# ===========================================================================


class TestLinkRecordToGroup:
    @pytest.mark.asyncio
    async def test_creates_group_relation_edge(self):
        """Creates record group relation edge."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = True

        await proc._link_record_to_group(record, "group-1", tx_store)

        tx_store.create_record_group_relation.assert_awaited_once_with("rec-1", "group-1")
        tx_store.create_inherit_permissions_relation_record_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_inherit_when_no_inherit(self):
        """Deletes inherit permissions edge when inherit is False."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = False

        await proc._link_record_to_group(record, "group-1", tx_store)

        tx_store.delete_inherit_permissions_relation_record_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_old_group_edge_when_group_changed(self):
        """Deletes old edge when group changes."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = True

        existing = _make_record()
        existing.id = "rec-1"
        existing.record_group_id = "old-group"

        await proc._link_record_to_group(record, "new-group", tx_store, existing_record=existing)

        # Should delete edge from old group
        tx_store.delete_edge.assert_awaited()

    @pytest.mark.asyncio
    async def test_shared_with_me_group_linked(self):
        """Creates relation for shared_with_me record group."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        record = _make_record()
        record.id = "rec-1"
        record.is_shared_with_me = True
        record.shared_with_me_record_group_id = "shared-ext-group"
        record.inherit_permissions = True

        shared_group = MagicMock()
        shared_group.id = "shared-group-internal-id"

        # First call for _handle_record_group, second for shared
        tx_store.get_record_group_by_external_id.return_value = shared_group

        await proc._link_record_to_group(record, "group-1", tx_store)

        # Should create relation for shared group too
        assert tx_store.create_record_group_relation.call_count >= 2


# ===========================================================================
# _handle_ticket_user_edges
# ===========================================================================


class TestHandleTicketUserEdges:
    @pytest.mark.asyncio
    async def test_creates_all_three_edges(self):
        """Creates ASSIGNED_TO, CREATED_BY, and REPORTED_BY edges."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        ticket = TicketRecord(
            record_name="TEST-1",
            record_type=RecordType.TICKET,
            external_record_id="ext-t-1",
            version=1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            assignee_email="a@test.com",
            creator_email="c@test.com",
            reporter_email="r@test.com",
            source_created_at=1000,
            source_updated_at=2000,
        )
        ticket.id = "ticket-1"

        await proc._handle_ticket_user_edges(ticket, tx_store)

        tx_store.batch_create_entity_relations.assert_awaited_once()
        edges = tx_store.batch_create_entity_relations.call_args[0][0]
        assert len(edges) == 3

    @pytest.mark.asyncio
    async def test_no_edges_when_no_emails(self):
        """Creates no edges when no emails are set."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        ticket = TicketRecord(
            record_name="TEST-1",
            record_type=RecordType.TICKET,
            external_record_id="ext-t-1",
            version=1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_created_at=1000,
            source_updated_at=2000,
        )
        ticket.id = "ticket-1"

        await proc._handle_ticket_user_edges(ticket, tx_store)

        tx_store.batch_create_entity_relations.assert_not_awaited()


# ===========================================================================
# _handle_project_lead_edge
# ===========================================================================


class TestHandleProjectLeadEdge:
    @pytest.mark.asyncio
    async def test_creates_lead_edge(self):
        """Creates LEAD_BY edge for project with lead."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        mock_user = MagicMock()
        mock_user.id = "user-1"
        tx_store.get_user_by_email.return_value = mock_user

        project = ProjectRecord(
            record_name="Proj-1",
            record_type=RecordType.PROJECT,
            external_record_id="ext-p-1",
            version=1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_created_at=1000,
            source_updated_at=2000,
        )
        project.id = "proj-1"
        project.lead_email = "lead@test.com"

        await proc._handle_project_lead_edge(project, tx_store)

        tx_store.batch_create_entity_relations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_edge_when_no_lead_email(self):
        """No edge created when project has no lead email."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        project = ProjectRecord(
            record_name="Proj-1",
            record_type=RecordType.PROJECT,
            external_record_id="ext-p-1",
            version=1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_created_at=1000,
            source_updated_at=2000,
        )
        project.id = "proj-1"
        project.lead_email = None

        await proc._handle_project_lead_edge(project, tx_store)

        tx_store.batch_create_entity_relations.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_edge_when_user_not_found(self):
        """No edge created when lead user not found in DB."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_user_by_email.return_value = None

        project = ProjectRecord(
            record_name="Proj-1",
            record_type=RecordType.PROJECT,
            external_record_id="ext-p-1",
            version=1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            source_created_at=1000,
            source_updated_at=2000,
        )
        project.id = "proj-1"
        project.lead_email = "missing@test.com"

        await proc._handle_project_lead_edge(project, tx_store)

        tx_store.batch_create_entity_relations.assert_not_awaited()


# ===========================================================================
# on_new_app_users
# ===========================================================================


class TestOnNewAppUsers:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        proc = _make_processor()
        await proc.on_new_app_users([])
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_upserts_app_users(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        from app.models.entities import AppUser, Connectors
        user = AppUser(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="su-1",
            email="test@test.com",
            full_name="Test User",
        )
        await proc.on_new_app_users([user])
        tx_store.batch_upsert_app_users.assert_awaited_once()


# ===========================================================================
# on_new_user_groups
# ===========================================================================


class TestOnNewUserGroups:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        proc = _make_processor()
        await proc.on_new_user_groups([])
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_creates_new_user_group(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        from app.models.entities import AppUserGroup, Connectors
        group = AppUserGroup(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_group_id="sg-1",
            name="Test Group",
        )
        await proc.on_new_user_groups([(group, [])])
        tx_store.batch_upsert_user_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_user_group(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        existing = MagicMock()
        existing.id = "existing-ug-id"
        tx_store.get_user_group_by_external_id.return_value = existing

        from app.models.entities import AppUserGroup, Connectors
        group = AppUserGroup(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_group_id="sg-1",
            name="Test Group",
        )
        await proc.on_new_user_groups([(group, [])])
        assert group.id == "existing-ug-id"
        tx_store.delete_edges_to.assert_awaited()


# ===========================================================================
# on_new_app_roles
# ===========================================================================


class TestOnNewAppRoles:
    @pytest.mark.asyncio
    async def test_empty_list_skips(self):
        proc = _make_processor()
        await proc.on_new_app_roles([])
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_creates_new_role(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        from app.models.entities import AppRole, Connectors
        role = AppRole(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_role_id="sr-1",
            name="Admin",
        )
        await proc.on_new_app_roles([(role, [])])
        tx_store.batch_upsert_app_roles.assert_awaited()


# ===========================================================================
# _upsert_external_person
# ===========================================================================


class TestUpsertExternalPerson:
    @pytest.mark.asyncio
    async def test_returns_person_id(self):
        proc = _make_processor()
        tx_store = _make_tx_store()

        result = await proc._upsert_external_person("ext@test.com", tx_store)
        assert result is not None
        tx_store.batch_upsert_people.assert_awaited()

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.batch_upsert_people.side_effect = Exception("db fail")

        result = await proc._upsert_external_person("ext@test.com", tx_store)
        assert result is None


# ===========================================================================
# update_record_group_name
# ===========================================================================


class TestUpdateRecordGroupName:
    @pytest.mark.asyncio
    async def test_updates_name(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        existing_group = MagicMock()
        existing_group.id = "group-1"
        existing_group.name = "Old Name"
        tx_store.get_record_group_by_external_id.return_value = existing_group

        await proc.update_record_group_name("folder-1", "New Name", "Old Name", "conn-1")

        assert existing_group.name == "New Name"
        tx_store.batch_upsert_record_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_group_not_found_logs_warning(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.get_record_group_by_external_id.return_value = None

        await proc.update_record_group_name("folder-1", "New Name", connector_id="conn-1")

        proc.logger.warning.assert_called()


# ===========================================================================
# on_new_app / on_new_app_group
# ===========================================================================


class TestOnNewAppAndGroup:
    @pytest.mark.asyncio
    async def test_on_new_app_noop(self):
        proc = _make_processor()
        await proc.on_new_app(MagicMock())

    @pytest.mark.asyncio
    async def test_on_new_app_group_noop(self):
        proc = _make_processor()
        await proc.on_new_app_group(MagicMock())


# ===========================================================================
# get_all_active_users / get_all_app_users / get_record_by_external_id
# ===========================================================================


class TestGetters:
    @pytest.mark.asyncio
    async def test_get_all_active_users(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_users = AsyncMock(return_value=[])
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        result = await proc.get_all_active_users()
        tx_store.get_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_all_app_users(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_app_users = AsyncMock(return_value=[])
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        result = await proc.get_all_app_users("conn-1")
        tx_store.get_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_record_by_external_id(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        await proc.get_record_by_external_id("conn-1", "ext-1")
        tx_store.get_record_by_external_id.assert_awaited()


# ===========================================================================
# on_user_group_member_removed
# ===========================================================================


class TestOnUserGroupMemberRemoved:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.get_user_by_email.return_value = None

        result = await proc.on_user_group_member_removed("ext-g", "user@test.com", "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_group_not_found_returns_false(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_user_group_by_external_id.return_value = None

        result = await proc.on_user_group_member_removed("ext-g", "user@test.com", "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_removal(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        mock_group = MagicMock()
        mock_group.id = "g1"
        mock_group.name = "Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.delete_edge.return_value = True

        result = await proc.on_user_group_member_removed("ext-g", "user@test.com", "conn-1")
        assert result is True


# ===========================================================================
# on_user_group_member_added
# ===========================================================================


class TestOnUserGroupMemberAdded:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_false(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.get_user_by_email.return_value = None

        result = await proc.on_user_group_member_added("ext-g", "user@test.com", PermissionType.READ, "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_group_not_found_returns_false(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_user_group_by_external_id.return_value = None

        result = await proc.on_user_group_member_added("ext-g", "user@test.com", PermissionType.READ, "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_existing_edge_returns_false(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        mock_group = MagicMock()
        mock_group.id = "g1"
        mock_group.name = "Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.get_edge = AsyncMock(return_value={"_key": "edge-1"})

        result = await proc.on_user_group_member_added("ext-g", "user@test.com", PermissionType.READ, "conn-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_add(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        mock_group = MagicMock()
        mock_group.id = "g1"
        mock_group.name = "Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.get_edge = AsyncMock(return_value=None)

        result = await proc.on_user_group_member_added("ext-g", "user@test.com", PermissionType.READ, "conn-1")
        assert result is True
        tx_store.batch_create_edges.assert_awaited()


# ===========================================================================
# on_user_group_deleted
# ===========================================================================


class TestOnUserGroupDeleted:
    @pytest.mark.asyncio
    async def test_group_not_found_returns_true(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.get_user_group_by_external_id.return_value = None

        result = await proc.on_user_group_deleted("ext-g", "conn-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_successful_delete(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_group = MagicMock()
        mock_group.id = "g1"
        mock_group.name = "Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group
        tx_store.delete_nodes_and_edges = AsyncMock()

        result = await proc.on_user_group_deleted("ext-g", "conn-1")
        assert result is True
        tx_store.delete_nodes_and_edges.assert_awaited()


# ===========================================================================
# delete_user_group_by_id
# ===========================================================================


class TestDeleteUserGroupById:
    @pytest.mark.asyncio
    async def test_deletes_group(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.delete_user_group_by_id = AsyncMock()

        await proc.delete_user_group_by_id("g1")
        tx_store.delete_user_group_by_id.assert_awaited_once_with("g1")

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.delete_user_group_by_id = AsyncMock(side_effect=RuntimeError("fail"))

        with pytest.raises(RuntimeError):
            await proc.delete_user_group_by_id("g1")


# ===========================================================================
# migrate_group_permissions_to_user
# ===========================================================================


class TestMigrateGroupPermissionsToUser:
    @pytest.mark.asyncio
    async def test_user_not_found_returns_early(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.get_user_by_email.return_value = None

        await proc.migrate_group_permissions_to_user("g1", "user@test.com", "conn-1")
        proc.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_permission_edges_returns_early(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_edges_from_node.return_value = []

        await proc.migrate_group_permissions_to_user("g1", "user@test.com", "conn-1")
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_permission_edges(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_edges_from_node.return_value = [
            {"_to": f"{CollectionNames.RECORDS.value}/rec-1", "role": "READER"}
        ]
        tx_store.get_edge = AsyncMock(return_value=None)

        await proc.migrate_group_permissions_to_user("g1", "user@test.com", "conn-1")
        tx_store.batch_create_edges.assert_awaited()


# ===========================================================================
# on_updated_record_permissions - additional branches
# ===========================================================================


class TestOnUpdatedRecordPermissionsAdditional:
    @pytest.mark.asyncio
    async def test_inherit_permissions_creates_edge(self):
        """When inherit_permissions is True, creates inherit edge."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = True
        record.external_record_group_id = "ext-rg-1"

        mock_rg = MagicMock()
        mock_rg.id = "rg-1"
        tx_store.get_record_group_by_external_id.return_value = mock_rg

        await proc.on_updated_record_permissions(record, [])

        tx_store.create_inherit_permissions_relation_record_group.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_belongs_to_triggers_process_record(self):
        """When BELONGS_TO is empty, _process_record is called."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.inherit_permissions = False
        record.external_record_group_id = None

        tx_store.get_edges_from_node.return_value = []

        await proc.on_updated_record_permissions(record, [])

        # _process_record should have been called, which calls batch_upsert_records
        tx_store.batch_upsert_records.assert_awaited()


# ===========================================================================
# _handle_record_permissions - ROLE entity type
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
        permission.to_arango_permission = MagicMock(return_value={"_from": "r/1", "_to": "rec/1"})

        await proc._handle_record_permissions(record, [permission], tx_store)

        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_role_not_found_skipped(self):
        """Unknown role is skipped with warning."""
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

        tx_store.batch_create_edges.assert_not_awaited()


# ===========================================================================
# internal record skip
# ===========================================================================


class TestInternalRecordSkip:
    @pytest.mark.asyncio
    async def test_internal_record_skips_publish(self):
        """Internal records don't get indexing events published."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.is_internal = True

        await proc.on_new_records([(record, [])])

        proc.messaging_producer.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reindex_skips_internal(self):
        """Reindex skips internal records."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        record = _make_record()
        record.id = "rec-1"
        record.is_internal = True

        await proc.reindex_existing_records([record])

        proc.messaging_producer.send_message.assert_not_awaited()


# ===========================================================================
# on_new_record_groups - additional branches (permissions, parent groups, app edges)
# ===========================================================================


# ===========================================================================
# migrate_group_to_user_by_external_id
# ===========================================================================


class TestMigrateGroupToUserByExternalId:
    @pytest.mark.asyncio
    async def test_group_not_found_returns_early(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx
        tx_store.get_user_group_by_external_id.return_value = None

        await proc.migrate_group_to_user_by_external_id("ext-g", "user@test.com", "conn-1")
        proc.logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_found_group_migrates_and_deletes(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_group = MagicMock()
        mock_group.id = "g1"
        mock_group.name = "Test Group"
        tx_store.get_user_group_by_external_id.return_value = mock_group

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user
        tx_store.get_edges_from_node.return_value = []
        tx_store.delete_user_group_by_id = AsyncMock()

        await proc.migrate_group_to_user_by_external_id("ext-g", "user@test.com", "conn-1")


# ===========================================================================
# _handle_record_permissions - permission edge for groups not found
# ===========================================================================


class TestHandleRecordPermissionsGroupNotFound:
    @pytest.mark.asyncio
    async def test_group_not_found_skipped(self):
        """Unknown group is skipped with warning."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        tx_store.get_user_group_by_external_id.return_value = None

        record = _make_record()
        record.id = "rec-1"

        permission = MagicMock()
        permission.entity_type = EntityType.GROUP.value
        permission.external_id = "ext-group-unknown"
        permission.email = None

        await proc._handle_record_permissions(record, [permission], tx_store)

        tx_store.batch_create_edges.assert_not_awaited()
        proc.logger.warning.assert_called()


# ===========================================================================
# on_new_record_groups - with ROLE permissions and ORG permission
# ===========================================================================


class TestOnNewRecordGroupsPermissions:
    @pytest.mark.asyncio
    async def test_with_role_permissions(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

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
        perm = MagicMock()
        perm.entity_type = EntityType.ROLE
        perm.external_id = "ext-role-1"
        perm.email = None
        perm.to_arango_permission = MagicMock(return_value={"_from": "r/1", "_to": "rg/1"})

        await proc.on_new_record_groups([(rg, [perm])])
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_with_org_permissions(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )
        perm = MagicMock()
        perm.entity_type = EntityType.ORG
        perm.external_id = None
        perm.email = None
        perm.to_arango_permission = MagicMock(return_value={"_from": "o/1", "_to": "rg/1"})

        await proc.on_new_record_groups([(rg, [perm])])
        tx_store.batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_with_group_permissions(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_group = MagicMock()
        mock_group.id = "ug-1"
        tx_store.get_user_group_by_external_id.side_effect = [None, mock_group]

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )
        perm = MagicMock()
        perm.entity_type = EntityType.GROUP
        perm.external_id = "ext-ug-1"
        perm.email = None
        perm.to_arango_permission = MagicMock(return_value={"_from": "g/1", "_to": "rg/1"})

        await proc.on_new_record_groups([(rg, [perm])])
        tx_store.batch_create_edges.assert_awaited()


# ===========================================================================
# on_new_user_groups - with member lookup
# ===========================================================================


class TestOnNewUserGroupsWithMembers:
    @pytest.mark.asyncio
    async def test_creates_member_permissions(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user

        from app.models.entities import AppUser, AppUserGroup, Connectors
        group = AppUserGroup(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_group_id="sg-1",
            name="Test Group",
        )
        member = AppUser(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="su-1",
            email="member@test.com",
            full_name="Member User",
        )

        await proc.on_new_user_groups([(group, [member])])
        tx_store.batch_create_edges.assert_awaited()


# ===========================================================================
# on_new_app_roles - with member lookup
# ===========================================================================


class TestOnNewAppRolesWithMembers:
    @pytest.mark.asyncio
    async def test_creates_role_member_permissions(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user

        from app.models.entities import AppRole, AppUser, Connectors
        role = AppRole(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_role_id="sr-1",
            name="Admin",
        )
        member = AppUser(
            app_name=Connectors.GOOGLE_MAIL,
            connector_id="conn-1",
            source_user_id="su-1",
            email="admin@test.com",
            full_name="Admin User",
        )

        await proc.on_new_app_roles([(role, [member])])
        tx_store.batch_create_edges.assert_awaited()


class TestOnNewRecordGroupsAdditional:
    @pytest.mark.asyncio
    async def test_with_permissions(self):
        """Record group with USER permissions creates edges."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        mock_user = MagicMock()
        mock_user.id = "u1"
        tx_store.get_user_by_email.return_value = mock_user

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Test Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
        )
        perm = MagicMock()
        perm.entity_type = EntityType.USER
        perm.email = "u@test.com"
        perm.external_id = None
        perm.to_arango_permission = MagicMock(return_value={"_from": "u/1", "_to": "rg/1"})

        await proc.on_new_record_groups([(rg, [perm])])

        # Permission edges should be created
        assert tx_store.batch_create_edges.call_count >= 2  # BELONGS_TO + PERMISSION

    @pytest.mark.asyncio
    async def test_with_parent_external_group(self):
        """Record group with parent external group creates BELONGS_TO edge to parent."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=False)
        proc.data_store_provider.transaction.return_value = ctx

        parent_rg = MagicMock()
        parent_rg.id = "parent-rg-id"
        parent_rg.name = "Parent Group"
        tx_store.get_record_group_by_external_id.side_effect = [None, parent_rg]

        rg = RecordGroup(
            external_group_id="ext-g1",
            name="Child Group",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="conn-1",
            parent_external_group_id="parent-ext-g",
        )

        await proc.on_new_record_groups([(rg, [])])

        # Should have created multiple edges including parent BELONGS_TO
        assert tx_store.batch_create_edges.call_count >= 2
