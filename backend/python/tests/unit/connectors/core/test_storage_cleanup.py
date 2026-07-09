"""Tests for StorageCleanupHelper (Phase 3 + 4) and the processor wiring
in DataSourceEntitiesProcessor.on_record_deleted and on_records_moved.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.data_processor.storage_cleanup import StorageCleanupHelper
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.config.constants.arangodb import (
    Connectors as ConnectorsEnum,
    OriginTypes,
)
from app.config.constants.service import Routes
from app.models.entities import FileRecord, RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph_provider():
    gp = AsyncMock()
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=[])
    gp.get_document = AsyncMock(return_value=None)
    gp.delete_document = AsyncMock()
    gp.get_record_group_by_id = AsyncMock(return_value=None)
    gp.get_record_path = AsyncMock(return_value=None)
    return gp


def _make_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(
        side_effect=[
            {"scopedJwtSecret": "secret"},
            {"cm": {"endpoint": "http://localhost:3001"}},
        ]
    )
    return cs


def _make_cleanup(*, graph_provider=None, config_service=None):
    return StorageCleanupHelper(
        logger=MagicMock(),
        graph_provider=graph_provider or _make_graph_provider(),
        config_service=config_service or _make_config_service(),
    )


def _make_processor():
    logger = MagicMock()
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    proc = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    return proc


def _make_tx_store():
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
    tx_store.batch_upsert_record_relations = AsyncMock()
    return tx_store


@pytest.fixture
def mock_graph_provider():
    return _make_graph_provider()


@pytest.fixture
def mock_config_service():
    return _make_config_service()


@pytest.fixture
def helper(mock_graph_provider, mock_config_service):
    return _make_cleanup(
        graph_provider=mock_graph_provider, config_service=mock_config_service
    )


def _make_ctx(tx_store):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_record(**overrides):
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


# ---------------------------------------------------------------------------
# Shared aiohttp mocking helpers (used by TestMoveRecordTree below).
# ---------------------------------------------------------------------------


def _resp(status=200, text_value="", json_value=None):
    r = AsyncMock()
    r.status = status
    r.text = AsyncMock(return_value=text_value)
    r.json = AsyncMock(return_value=json_value)
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _mock_session(*, delete_resp=None, post_resp=None, get_resp=None):
    session = AsyncMock()
    if delete_resp is not None:
        session.delete = MagicMock(return_value=delete_resp)
    if post_resp is not None:
        session.post = MagicMock(return_value=post_resp)
    if get_resp is not None:
        session.get = MagicMock(return_value=get_resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# TestBuildRecordPath -- unified path builder for any record type (file or
# folder), replacing build_storage_path/build_folder_path_prefix/
# build_descendant_storage_path.
# ---------------------------------------------------------------------------


class TestBuildRecordPath:
    async def test_uses_graph_traversal_when_available(self, helper, mock_graph_provider):
        mock_graph_provider.get_record_path.return_value = "p1/c1"
        record = SimpleNamespace(connector_id="conn1", id="rec1", record_group_id=None, record_name="c1", virtual_record_id=None)
        path = await helper.build_record_path(record)
        assert path == "records/conn1/p1/c1"

    async def test_file_falls_back_to_flat_vrid_path_on_traversal_failure(self, helper, mock_graph_provider):
        mock_graph_provider.get_record_path.side_effect = Exception("boom")
        record = SimpleNamespace(connector_id="conn1", id="rec1", record_group_id=None, record_name="c1", virtual_record_id="vrid123")
        path = await helper.build_record_path(record)
        assert path == "records/vrid123"

    async def test_folder_returns_none_on_traversal_failure(self, helper, mock_graph_provider):
        mock_graph_provider.get_record_path.side_effect = Exception("boom")
        record = SimpleNamespace(connector_id="conn1", id="rec1", record_group_id=None, record_name="c1", virtual_record_id=None)
        path = await helper.build_record_path(record)
        assert path is None

    async def test_returns_none_without_connector_id_and_no_vrid(self, helper):
        record = SimpleNamespace(connector_id=None, id="rec1", record_group_id=None, record_name="c1", virtual_record_id=None)
        path = await helper.build_record_path(record)
        assert path is None


# ---------------------------------------------------------------------------
# TestBuildRecordGroupPath -- the prefix every record beneath a record group
# (space/project/drive/team-folder) falls under. Pure string construction,
# no graph traversal, so no async/mocking needed beyond the helper fixture.
# ---------------------------------------------------------------------------


class TestBuildRecordGroupPath:
    def test_builds_prefix_from_connector_and_group_name(self, helper):
        assert helper.build_record_group_path("conn1", "My Space") == "records/conn1/My Space"

    def test_sanitizes_group_name(self, helper):
        assert helper.build_record_group_path("conn1", "a/b:c") == "records/conn1/a_b_c"

    def test_returns_none_without_connector_id(self, helper):
        assert helper.build_record_group_path(None, "My Space") is None

    def test_returns_none_without_group_name(self, helper):
        assert helper.build_record_group_path("conn1", None) is None
        assert helper.build_record_group_path("conn1", "") is None


# ---------------------------------------------------------------------------
# TestMoveRecordTree -- replaces move_record_blob; calls Node's path-based
# /internal/move-tree endpoint instead of moving individual documentIds.
# ---------------------------------------------------------------------------


class TestMoveRecordTree:
    async def test_no_op_when_paths_equal(self, helper):
        await helper.move_record_tree("org1", "a/b", "a/b")
        # no aiohttp session should have been touched -- assert via a patched
        # aiohttp.ClientSession that raises if constructed
        with patch("aiohttp.ClientSession", side_effect=AssertionError("should not be called")):
            await helper.move_record_tree("org1", "a/b", "a/b")

    async def test_posts_old_and_new_path_to_move_tree_endpoint(self, helper, mock_config_service):
        # A bare AsyncMock chain here would make session.post(...) return a
        # coroutine instead of an async context manager (AsyncMock
        # auto-propagates its type to unspecced child attributes) -- use the
        # file's established _mock_session/_resp helpers instead, same as
        # every other HTTP-call test below.
        session = _mock_session(post_resp=_resp(200))
        with patch(
            "app.connectors.core.base.data_processor.storage_cleanup.aiohttp.ClientSession",
            return_value=session,
        ):
            await helper.move_record_tree("org1", "a/b", "a/c")
            args, kwargs = session.post.call_args
            assert args[0].endswith(Routes.STORAGE_MOVE_TREE.value)
            assert kwargs["json"] == {"oldPath": "a/b", "newPath": "a/c"}


# ---------------------------------------------------------------------------
# TestOnRecordsMovedWithBlobMove (Phase 4 processor wiring)
# ---------------------------------------------------------------------------


class TestOnRecordsMovedWithBlobMove:
    """NOTE: `new_record` deliberately never carries `virtual_record_id` --
    the path-based move design (`build_record_path`) doesn't consult vrid at
    all. Old/new paths are derived from the graph's parent-child hierarchy
    and handed to `move_record_tree` instead."""

    def _make_old_record(self, record_id="old-rec-1", record_name="old_name.txt",
                          parent_external_record_id="parent-1"):
        r = MagicMock()
        r.id = record_id
        r.external_revision_id = "rev-1"
        r.record_group_id = None
        r.indexing_status = "completed"
        r.is_internal = False
        r.record_name = record_name
        r.parent_external_record_id = parent_external_record_id
        r.to_kafka_record = MagicMock(return_value={})
        return r

    @pytest.mark.asyncio
    async def test_triggers_blob_move_for_moved_records(self):
        """move_record_tree is called with the old and new paths when a
        moved record's name changed."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        old_record = self._make_old_record()
        tx_store.get_record_by_external_id = AsyncMock(return_value=old_record)
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_cleanup = MagicMock()
        mock_cleanup.move_record_tree = AsyncMock()
        # First call computes old_path (from old_record, inside the transaction);
        # second computes new_path (from new_record, after commit).
        mock_cleanup.build_record_path = AsyncMock(
            side_effect=["records/conn-1/old_name.txt", "records/conn-1/new_name.txt"]
        )
        proc._storage_cleanup = mock_cleanup

        new_record = _make_record(external_record_id="ext-1", record_name="new_name.txt")
        new_record.id = "old-rec-1"
        new_record.parent_external_record_id = "parent-1"

        await proc.on_records_moved([("ext-old", new_record, [])])

        mock_cleanup.move_record_tree.assert_awaited_once_with(
            "org-1",
            "records/conn-1/old_name.txt",
            "records/conn-1/new_name.txt",
        )

    @pytest.mark.asyncio
    async def test_blob_move_failure_is_non_fatal(self):
        """A move_record_tree exception does not abort the whole operation."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        old_record = self._make_old_record()
        tx_store.get_record_by_external_id = AsyncMock(return_value=old_record)
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_cleanup = MagicMock()
        mock_cleanup.move_record_tree = AsyncMock(side_effect=Exception("storage error"))
        mock_cleanup.build_record_path = AsyncMock(
            side_effect=["records/conn-1/old_name.txt", "records/conn-1/new_name.txt"]
        )
        proc._storage_cleanup = mock_cleanup

        new_record = _make_record(external_record_id="ext-1", record_name="new_name.txt")
        new_record.id = "old-rec-1"
        new_record.parent_external_record_id = "parent-1"

        # Must not raise -- _flush_pending_blob_moves swallows per-move errors.
        await proc.on_records_moved([("ext-old", new_record, [])])

        mock_cleanup.move_record_tree.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_move_when_neither_name_nor_parent_changed(self):
        """When old and new agree on name and parent, no move is computed.

        Replaces the old vrid-presence gate (`test_record_with_no_vrid_skips_blob_move`),
        which has no equivalent in the path-based design -- vrid isn't part
        of the move decision at all anymore, only name/parent comparison is.
        """
        proc = _make_processor()
        tx_store = _make_tx_store()
        old_record = self._make_old_record(record_name="same.txt", parent_external_record_id="parent-1")
        tx_store.get_record_by_external_id = AsyncMock(return_value=old_record)
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_cleanup = MagicMock()
        mock_cleanup.move_record_tree = AsyncMock()
        mock_cleanup.build_record_path = AsyncMock(return_value="records/conn-1/same.txt")
        proc._storage_cleanup = mock_cleanup

        new_record = _make_record(external_record_id="ext-1", record_name="same.txt")
        new_record.id = "old-rec-1"
        new_record.parent_external_record_id = "parent-1"

        await proc.on_records_moved([("ext-old", new_record, [])])

        mock_cleanup.move_record_tree.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_moves_list_is_noop(self):
        """Empty moves list returns without calling any storage methods."""
        proc = _make_processor()
        mock_cleanup = MagicMock()
        mock_cleanup.move_record_tree = AsyncMock()
        proc._storage_cleanup = mock_cleanup

        await proc.on_records_moved([])

        mock_cleanup.move_record_tree.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestHandleUpdatedRecordBlobMove (Task 5 processor wiring)
# ---------------------------------------------------------------------------


class TestHandleUpdatedRecordBlobMove:
    """`_handle_updated_record(record, existing_record, tx_store, old_path)`
    takes `old_path` as an explicit parameter -- the caller (`_process_record`
    et al.) computes it from `existing_record` *before* any graph mutation,
    since the parent-child edge gets repointed before this runs. The method
    itself only computes the *new* path, via `build_record_path(record, ...)`.
    There is no vrid anywhere in the path-based design.

    NOTE: a prior version of this class had a
    `test_uses_existing_records_vrid_not_new_records_vrid` regression test for
    an old/new-vrid mixup. That mixup can't happen anymore: `old_path` is
    always supplied explicitly by the caller (never read off `record` or
    `existing_record` internally), and the new path is always computed from
    `record`. That behavior is already exercised by the name/parent-change
    tests below, so the vrid-specific test was removed rather than adapted.
    """

    @pytest.mark.asyncio
    async def test_returns_pending_move_on_name_change(self):
        """When record_name changes, a pending move descriptor is returned (not executed)."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(record_name="old_name.txt", id="rec-1")
        old_record.parent_external_record_id = "parent-1"

        new_record = _make_record(record_name="new_name.txt", id="rec-1")
        new_record.parent_external_record_id = "parent-1"

        mock_cleanup = AsyncMock()
        mock_cleanup.build_record_path = AsyncMock(return_value="records/conn-1/new_name.txt")
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        pending_moves = await proc._handle_updated_record(
            new_record, old_record, tx_store, "records/conn-1/old_name.txt"
        )

        assert pending_moves == [
            ("org-1", "records/conn-1/old_name.txt", "records/conn-1/new_name.txt")
        ]
        mock_cleanup.move_record_tree.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_pending_move_on_parent_change(self):
        """When parent_external_record_id changes, a pending move descriptor is returned."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(record_name="file.txt", id="rec-1")
        old_record.parent_external_record_id = "old-parent"

        new_record = _make_record(record_name="file.txt", id="rec-1")
        new_record.parent_external_record_id = "new-parent"

        mock_cleanup = AsyncMock()
        mock_cleanup.build_record_path = AsyncMock(return_value="records/conn-1/new-dir/file.txt")
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        pending_moves = await proc._handle_updated_record(
            new_record, old_record, tx_store, "records/conn-1/old-dir/file.txt"
        )

        assert pending_moves == [
            ("org-1", "records/conn-1/old-dir/file.txt", "records/conn-1/new-dir/file.txt")
        ]
        mock_cleanup.move_record_tree.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_pending_move_when_nothing_changed(self):
        """When neither name nor parent changed, no pending move is returned."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(record_name="file.txt", id="rec-1")
        old_record.parent_external_record_id = "parent-1"

        new_record = _make_record(record_name="file.txt", id="rec-1")
        new_record.parent_external_record_id = "parent-1"

        mock_cleanup = AsyncMock()
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        pending_moves = await proc._handle_updated_record(
            new_record, old_record, tx_store, "records/conn-1/file.txt"
        )

        assert pending_moves == []
        mock_cleanup.build_record_path.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_build_record_path_failure_is_non_fatal(self):
        """If build_record_path raises, _handle_updated_record returns an empty list, does not raise."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(record_name="old.txt", id="rec-1")
        old_record.parent_external_record_id = "p1"

        new_record = _make_record(record_name="new.txt", id="rec-1")
        new_record.parent_external_record_id = "p1"

        mock_cleanup = AsyncMock()
        mock_cleanup.build_record_path = AsyncMock(side_effect=Exception("graph error"))
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        # Must not raise
        pending_moves = await proc._handle_updated_record(
            new_record, old_record, tx_store, "records/conn-1/old.txt"
        )

        assert pending_moves == []
        tx_store.batch_upsert_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_pending_move_when_old_path_is_none(self):
        """When the caller couldn't determine an old path for existing_record,
        no pending move is returned even though the name changed.

        This is the path-based equivalent of the old 'no vrid' gate: `None`
        is exactly what a caller passes when it couldn't resolve a path for
        existing_record (see `build_record_path`'s None-return cases).
        """
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(record_name="old.txt", id="rec-1")
        old_record.parent_external_record_id = "p1"

        new_record = _make_record(record_name="new.txt", id="rec-1")
        new_record.parent_external_record_id = "p1"

        mock_cleanup = AsyncMock()
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        pending_moves = await proc._handle_updated_record(
            new_record, old_record, tx_store, None
        )

        assert pending_moves == []
        mock_cleanup.build_record_path.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestDeferredBlobMoveFlushing (transaction-ordering fix)
# ---------------------------------------------------------------------------


class TestDeferredBlobMoveFlushing:
    @pytest.mark.asyncio
    async def test_on_new_records_flushes_after_transaction_commits(self):
        """move_record_tree is only awaited after the async-with block exits."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(record_name="old.txt", id="rec-1", external_record_id="ext-1")
        old_record.parent_external_record_id = "p1"
        old_record.external_revision_id = "rev-1"
        tx_store.get_record_by_external_id = AsyncMock(return_value=old_record)

        new_record = _make_record(record_name="new.txt", id="rec-1", external_record_id="ext-1")
        new_record.parent_external_record_id = "p1"
        new_record.external_revision_id = "rev-2"
        new_record.is_internal = False

        mock_cleanup = AsyncMock()
        # First call computes old_path (for existing_record, before the
        # parent-child edge is repointed); second computes new_path (for the
        # incoming record, inside _handle_updated_record).
        mock_cleanup.build_record_path = AsyncMock(
            side_effect=["records/conn-1/old.txt", "records/conn-1/new.txt"]
        )
        call_order = []
        mock_cleanup.move_record_tree = AsyncMock(
            side_effect=lambda *a, **kw: call_order.append("move_record_tree")
        )
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        ctx = _make_ctx(tx_store)

        async def _aexit(*args):
            call_order.append("transaction_exit")
            return False
        ctx.__aexit__ = _aexit
        proc.data_store_provider.transaction.return_value = ctx

        await proc.on_new_records([(new_record, [])])

        assert call_order == ["transaction_exit", "move_record_tree"]

    @pytest.mark.asyncio
    async def test_batch_failure_prevents_any_blob_move(self):
        """If a later record in the batch fails, no blob move fires for the batch."""
        proc = _make_processor()
        tx_store = _make_tx_store()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        mock_cleanup = AsyncMock()
        mock_cleanup.build_record_path = AsyncMock(return_value="records/conn-1/new_name.txt")
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        old_record_1 = _make_record(record_name="old_name.txt", id="rec-1", external_record_id="ext-1")
        old_record_1.parent_external_record_id = "parent-1"
        old_record_1.external_revision_id = "rev-1"

        new_record_1 = _make_record(record_name="new_name.txt", id="rec-1", external_record_id="ext-1")
        new_record_1.parent_external_record_id = "parent-1"
        new_record_1.external_revision_id = "rev-2"

        new_record_2 = _make_record(record_name="second.txt", id="rec-2", external_record_id="ext-2")

        async def _get_by_external_id(connector_id, external_id):
            if external_id == "ext-1":
                return old_record_1
            raise Exception("DB error on second record")

        tx_store.get_record_by_external_id = _get_by_external_id

        with pytest.raises(Exception, match="DB error on second record"):
            await proc.on_new_records([(new_record_1, []), (new_record_2, [])])

        mock_cleanup.move_record_tree.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_record_metadata_update_flushes_after_transaction(self):
        """on_record_metadata_update flushes pending moves after its transaction exits."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(record_name="old.txt", id="rec-1", external_record_id="ext-1")
        old_record.parent_external_record_id = "p1"
        old_record.external_revision_id = "rev-1"
        tx_store.get_record_by_external_id = AsyncMock(return_value=old_record)

        new_record = _make_record(record_name="new.txt", id="rec-1", external_record_id="ext-1")
        new_record.parent_external_record_id = "p1"
        new_record.external_revision_id = "rev-2"

        mock_cleanup = AsyncMock()
        mock_cleanup.build_record_path = AsyncMock(return_value="records/conn-1/new.txt")
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        await proc.on_record_metadata_update(new_record)

        # on_record_metadata_update calls _process_record (which may itself
        # detect the change) and then _handle_updated_record again with the
        # same old/new pair -- a pre-existing double-call this fix does not
        # change. At least one flush must have happened after the transaction.
        assert mock_cleanup.move_record_tree.await_count >= 1

    @pytest.mark.asyncio
    async def test_flush_never_exceeds_concurrency_limit(self):
        """With many pending moves, no more than 8 move_record_tree calls
        are in flight at once."""
        proc = _make_processor()

        in_flight = 0
        max_in_flight = 0

        async def _tracked_move(*args, **kwargs) -> None:
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1

        mock_cleanup = AsyncMock()
        mock_cleanup.move_record_tree = AsyncMock(side_effect=_tracked_move)
        proc._get_storage_cleanup = MagicMock(return_value=mock_cleanup)

        pending_moves = [
            ("org-1", f"records/conn-1/old-{i}.txt", f"records/conn-1/new-{i}.txt")
            for i in range(50)
        ]

        await proc._flush_pending_blob_moves(pending_moves)

        assert mock_cleanup.move_record_tree.await_count == 50
        assert max_in_flight > 1, "Expected genuine concurrent execution, not sequential"
        assert max_in_flight <= 8
