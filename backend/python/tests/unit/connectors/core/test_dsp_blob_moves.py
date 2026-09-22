"""Tests for DSP blob-move infrastructure: snapshot, flush, and integration.

Covers:
- _snapshot_old_paths: bounded concurrency, error handling, new vs existing
- _flush_pending_blob_moves: ordering, prefix rewriting, error resilience
- on_new_records: snapshot integration with _process_record
- on_records_moved: snapshot integration
- on_new_record_groups: snapshot integration for renamed groups
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors as ConnectorsEnum,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
    _NO_OLD_PATH,
)
from app.connectors.core.base.data_processor.storage_cleanup import StorageCleanupHelper
from app.models.entities import (
    FileRecord,
    Record,
    RecordGroup,
    RecordType,
)
from app.models.permission import Permission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_processor(*, with_storage_cleanup: bool = True):
    """Build a DSP with mocked dependencies and optional StorageCleanupHelper."""
    logger = MagicMock()
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    proc = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    proc.messaging_producer.send_messages = AsyncMock(
        side_effect=lambda topic, messages: [True] * len(messages)
    )
    data_store_provider.get_existing_record_keys = AsyncMock(
        side_effect=lambda ids: set(ids)
    )
    data_store_provider.compare_and_set_indexing_status = AsyncMock(
        side_effect=lambda ids, expected, new_status: list(ids)
    )

    if with_storage_cleanup:
        cleanup = AsyncMock(spec=StorageCleanupHelper)
        cleanup.move_record_tree = AsyncMock()
        cleanup.build_record_path = AsyncMock(return_value="records/conn-1/space/file.txt")
        cleanup.build_record_group_hierarchical_prefix = AsyncMock(
            return_value="records/conn-1/space"
        )
        proc._storage_cleanup = cleanup
    return proc


def _make_record(**overrides):
    defaults = {
        "org_id": "org-1",
        "external_record_id": f"ext-{uuid.uuid4().hex[:6]}",
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
    tx_store = AsyncMock()
    tx_store.txn = "txn-123"
    tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    tx_store.batch_upsert_records = AsyncMock()
    tx_store.batch_create_edges = AsyncMock()
    tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx_store.batch_upsert_record_groups = AsyncMock()
    tx_store.create_record_relation = AsyncMock()
    tx_store.get_record_by_key = AsyncMock(return_value=None)
    tx_store.get_user_by_email = AsyncMock(return_value=None)
    tx_store.get_user_group_by_external_id = AsyncMock(return_value=None)
    tx_store.delete_edges_to = AsyncMock(return_value=0)
    tx_store.delete_edges_from = AsyncMock(return_value=0)
    tx_store.delete_parent_child_edge_to_record = AsyncMock()
    tx_store.delete_edge = AsyncMock(return_value=True)
    tx_store.get_edge = AsyncMock(return_value=None)
    tx_store.delete_edges_by_relationship_types = AsyncMock(return_value=0)
    tx_store.batch_create_entity_relations = AsyncMock()
    tx_store.get_edges_from_node = AsyncMock(return_value=[])
    tx_store.get_app_role_by_external_id = AsyncMock(return_value=None)
    tx_store.get_edges_to_node = AsyncMock(return_value=[])
    tx_store.batch_upsert_record_relations = AsyncMock()
    tx_store.create_record_groups_relation = AsyncMock()
    return tx_store


def _make_ctx(tx_store):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ===========================================================================
# _snapshot_old_paths
# ===========================================================================


class TestSnapshotOldPaths:
    @pytest.mark.asyncio
    async def test_captures_paths_for_existing_records(self):
        """Existing records get their old path snapshotted."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        r1 = _make_record(external_record_id="ext-1")
        r2 = _make_record(external_record_id="ext-2")

        existing_r1 = _make_record(external_record_id="ext-1")
        existing_r1.id = "db-1"
        existing_r2 = _make_record(external_record_id="ext-2")
        existing_r2.id = "db-2"

        tx_store.get_record_by_external_id.side_effect = (
            lambda connector_id, external_id: {
                "ext-1": existing_r1,
                "ext-2": existing_r2,
            }.get(external_id)
        )
        proc._storage_cleanup.build_record_path.side_effect = (
            lambda rec, transaction=None: {
                "db-1": "records/conn-1/space/file1.txt",
                "db-2": "records/conn-1/space/file2.txt",
            }.get(rec.id)
        )

        result = await proc._snapshot_old_paths([(r1, []), (r2, [])], tx_store)

        assert result == {
            "ext-1": "records/conn-1/space/file1.txt",
            "ext-2": "records/conn-1/space/file2.txt",
        }

    @pytest.mark.asyncio
    async def test_skips_new_records_not_in_db(self):
        """Records not found in DB are excluded from the snapshot."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        r_new = _make_record(external_record_id="ext-new")
        r_existing = _make_record(external_record_id="ext-old")

        existing_rec = _make_record(external_record_id="ext-old")
        existing_rec.id = "db-old"

        tx_store.get_record_by_external_id.side_effect = (
            lambda connector_id, external_id: existing_rec if external_id == "ext-old" else None
        )
        proc._storage_cleanup.build_record_path.return_value = "records/conn-1/space/old.txt"

        result = await proc._snapshot_old_paths(
            [(r_new, []), (r_existing, [])], tx_store,
        )

        assert "ext-new" not in result
        assert result["ext-old"] == "records/conn-1/space/old.txt"

    @pytest.mark.asyncio
    async def test_handles_build_path_failure_gracefully(self):
        """When build_record_path raises, the record gets None in the snapshot."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        r1 = _make_record(external_record_id="ext-1")
        existing = _make_record(external_record_id="ext-1")
        existing.id = "db-1"

        tx_store.get_record_by_external_id.return_value = existing
        proc._storage_cleanup.build_record_path.side_effect = RuntimeError("graph error")

        result = await proc._snapshot_old_paths([(r1, [])], tx_store)

        assert result["ext-1"] is None

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_batch(self):
        proc = _make_processor()
        tx_store = _make_tx_store()
        result = await proc._snapshot_old_paths([], tx_store)
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_storage_cleanup(self):
        proc = _make_processor(with_storage_cleanup=False)
        tx_store = _make_tx_store()
        r1 = _make_record()
        result = await proc._snapshot_old_paths([(r1, [])], tx_store)
        assert result == {}

    @pytest.mark.asyncio
    async def test_bounded_concurrency_does_not_exceed_limit(self):
        """Concurrent in-flight snapshot reads should not exceed 8."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        original_get = tx_store.get_record_by_external_id

        async def tracking_get(connector_id, external_id):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.001)
            async with lock:
                current_concurrent -= 1
            rec = _make_record(external_record_id=external_id)
            rec.id = f"db-{external_id}"
            return rec

        tx_store.get_record_by_external_id = tracking_get
        proc._storage_cleanup.build_record_path.return_value = "records/conn-1/x.txt"

        records = [(_make_record(external_record_id=f"ext-{i}"), []) for i in range(50)]
        await proc._snapshot_old_paths(records, tx_store)

        assert max_concurrent <= 8, f"Expected max 8 concurrent, got {max_concurrent}"

    @pytest.mark.asyncio
    async def test_large_batch_completes_without_error(self):
        """A batch of 500 records completes successfully with parallelism."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        records = []
        for i in range(500):
            r = _make_record(external_record_id=f"ext-{i}")
            records.append((r, []))

        async def get_existing(connector_id, external_id):
            rec = _make_record(external_record_id=external_id)
            rec.id = f"db-{external_id}"
            return rec

        tx_store.get_record_by_external_id = get_existing
        proc._storage_cleanup.build_record_path = AsyncMock(
            return_value="records/conn-1/space/file.txt"
        )

        result = await proc._snapshot_old_paths(records, tx_store)

        assert len(result) == 500


# ===========================================================================
# _flush_pending_blob_moves
# ===========================================================================


class TestFlushPendingBlobMoves:
    @pytest.mark.asyncio
    async def test_parent_moves_before_child(self):
        """Moves are sorted by old_path length so parents execute first."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path):
            move_calls.append((old_path, new_path))

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "records/conn/space/parent/child.txt", "records/conn/space/parent_new/child.txt"),
            ("org-1", "records/conn/space/parent", "records/conn/space/parent_new"),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert move_calls[0] == ("records/conn/space/parent", "records/conn/space/parent_new")

    @pytest.mark.asyncio
    async def test_prefix_rewrite_after_parent_move(self):
        """Child's old_path is rewritten after parent move succeeds."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path):
            move_calls.append((old_path, new_path))

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "records/conn/space/parent", "records/conn/space/renamed"),
            ("org-1", "records/conn/space/parent/child.txt", "records/conn/space/renamed/child_new.txt"),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert len(move_calls) == 2
        assert move_calls[0] == ("records/conn/space/parent", "records/conn/space/renamed")
        # Child's old_path was rewritten from .../parent/child.txt to .../renamed/child.txt
        assert move_calls[1] == (
            "records/conn/space/renamed/child.txt",
            "records/conn/space/renamed/child_new.txt",
        )

    @pytest.mark.asyncio
    async def test_noop_moves_skipped(self):
        """Moves where old_path == new_path are skipped entirely."""
        proc = _make_processor()
        moves = [
            ("org-1", "records/conn/space/file.txt", "records/conn/space/file.txt"),
        ]
        await proc._flush_pending_blob_moves(moves)
        proc._storage_cleanup.move_record_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_rewritten_noop_skipped(self):
        """After parent move, child becomes a noop and is skipped."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path):
            move_calls.append((old_path, new_path))

        proc._storage_cleanup.move_record_tree = track_move

        # Parent renamed, child not renamed — after prefix rewrite, child becomes noop
        moves = [
            ("org-1", "records/conn/old_space", "records/conn/new_space"),
            ("org-1", "records/conn/old_space/file.txt", "records/conn/new_space/file.txt"),
        ]
        await proc._flush_pending_blob_moves(moves)

        # Only the parent move should execute; child's rewritten old matches new
        assert len(move_calls) == 1
        assert move_calls[0] == ("records/conn/old_space", "records/conn/new_space")

    @pytest.mark.asyncio
    async def test_error_in_one_move_does_not_block_others(self):
        """A failed move logs error and continues with remaining moves."""
        proc = _make_processor()
        call_count = 0

        async def failing_then_ok(org_id, old_path, new_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("network error")

        proc._storage_cleanup.move_record_tree = failing_then_ok

        moves = [
            ("org-1", "records/a", "records/b"),
            ("org-1", "records/c", "records/d"),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert call_count == 2
        proc.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_moves_is_noop(self):
        proc = _make_processor()
        await proc._flush_pending_blob_moves([])
        proc._storage_cleanup.move_record_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_storage_cleanup_is_noop(self):
        proc = _make_processor(with_storage_cleanup=False)
        await proc._flush_pending_blob_moves([("org-1", "a", "b")])

    @pytest.mark.asyncio
    async def test_deep_hierarchy_prefix_rewrite_chain(self):
        """A 3-level rename cascade: grandparent → parent → child."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path):
            move_calls.append((old_path, new_path))

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "r/c/gp", "r/c/GP"),
            ("org-1", "r/c/gp/parent", "r/c/GP/Parent"),
            ("org-1", "r/c/gp/parent/child.txt", "r/c/GP/Parent/Child.txt"),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert len(move_calls) == 3
        # Grandparent moves first
        assert move_calls[0] == ("r/c/gp", "r/c/GP")
        # Parent's old was rewritten: r/c/gp/parent → r/c/GP/parent
        assert move_calls[1] == ("r/c/GP/parent", "r/c/GP/Parent")
        # Child's old was rewritten twice: gp→GP then parent→Parent
        assert move_calls[2] == ("r/c/GP/Parent/child.txt", "r/c/GP/Parent/Child.txt")

    @pytest.mark.asyncio
    async def test_new_path_rewrite_after_parent_rename(self):
        """Child reparented under a page that was renamed in the same batch.

        The child's new_path is computed from a partially-mutated graph and
        contains the OLD parent name.  After the parent rename succeeds, the
        flush must rewrite the child's new_path so it lands under the
        correct (renamed) directory.

        Reproduces: parent "Release Notes" renamed to "Release Notes-v2",
        child "Combined Summary From Prior" moved from Overview/ to under
        "Release Notes".  The child has a longer name so the parent sorts
        first by old_path length.
        """
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path):
            move_calls.append((old_path, new_path))

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            # Parent rename — shorter old_path, runs first after sort
            ("org-1", "r/c/space/Overview/Release Notes", "r/c/space/Overview/Release Notes-v2"),
            # Child reparent — longer old_path name, new_path uses stale parent name
            ("org-1", "r/c/space/Overview/Combined Summary From Prior", "r/c/space/Overview/Release Notes/Combined Summary From Prior"),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert len(move_calls) == 2
        assert move_calls[0] == ("r/c/space/Overview/Release Notes", "r/c/space/Overview/Release Notes-v2")
        # Child's new_path must be rewritten: Release Notes → Release Notes-v2
        assert move_calls[1] == (
            "r/c/space/Overview/Combined Summary From Prior",
            "r/c/space/Overview/Release Notes-v2/Combined Summary From Prior",
        )

    @pytest.mark.asyncio
    async def test_new_path_rewrite_does_not_false_match_siblings(self):
        """new_path rewrite must not match siblings with a shared prefix.

        "Release Notes" must not match "Release Notes-v2/" as a prefix —
        the check requires a "/" separator after the old_path.
        """
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path):
            move_calls.append((old_path, new_path))

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "r/c/space/A", "r/c/space/B"),
            # This move's new_path starts with "r/c/space/AB" — not a child of "r/c/space/A"
            ("org-1", "r/c/space/X", "r/c/space/AB/child"),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert len(move_calls) == 2
        # Second move's new_path must NOT be rewritten — "AB" is not "A/"
        assert move_calls[1] == ("r/c/space/X", "r/c/space/AB/child")


# ===========================================================================
# on_new_records — snapshot integration
# ===========================================================================


class TestOnNewRecordsSnapshotIntegration:
    @pytest.mark.asyncio
    async def test_uses_snapshot_path_for_existing_record(self):
        """on_new_records passes pre_old_path from snapshot, not live graph."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        record = _make_record(external_record_id="ext-1", record_name="renamed.txt")
        existing = _make_record(external_record_id="ext-1", record_name="old.txt")
        existing.id = "db-1"
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.external_revision_id = "rev-1"
        record.external_revision_id = "rev-1"

        tx_store.get_record_by_external_id.return_value = existing

        proc._storage_cleanup.build_record_path.side_effect = [
            # Called during snapshot (before mutations) — returns old path
            "records/conn-1/space/old.txt",
            # Called during _process_record (after mutations) — returns new path
            "records/conn-1/space/renamed.txt",
        ]

        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        await proc.on_new_records([(record, [])])

        # The flush should have received the snapshot's old path, not the post-mutation path
        build_calls = proc._storage_cleanup.build_record_path.call_args_list
        assert len(build_calls) >= 1
        # First call is from snapshot — uses existing record + transaction
        assert build_calls[0].args[0].id == "db-1"

    @pytest.mark.asyncio
    async def test_new_record_gets_no_old_path_sentinel(self):
        """Records not in snapshot get _NO_OLD_PATH so they use live graph."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        record = _make_record(external_record_id="ext-new")
        # Not in DB = new record
        tx_store.get_record_by_external_id.return_value = None

        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        old_process = proc._process_record

        captured_pre_old_path = []

        async def capturing_process(rec, perms, store, *, publishes_event=True, pre_old_path=_NO_OLD_PATH):
            captured_pre_old_path.append(pre_old_path)
            return (rec, [])

        proc._process_record = capturing_process

        await proc.on_new_records([(record, [])])

        assert captured_pre_old_path[0] is _NO_OLD_PATH


# ===========================================================================
# on_records_moved — snapshot integration
# ===========================================================================


class TestOnRecordsMovedSnapshotIntegration:
    @pytest.mark.asyncio
    async def test_snapshot_captured_before_mutations(self):
        """on_records_moved snapshots old paths before any upserts."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        old_record = _make_record(external_record_id="ext-old", record_name="old.txt")
        old_record.id = "db-1"
        old_record.external_revision_id = "rev-1"
        old_record.indexing_status = ProgressStatus.COMPLETED.value

        new_record = _make_record(external_record_id="ext-new", record_name="new.txt")
        new_record.external_revision_id = "rev-1"

        tx_store.get_record_by_external_id.return_value = old_record

        proc._storage_cleanup.build_record_path.side_effect = [
            # Snapshot call
            "records/conn-1/space/old.txt",
            # Post-mutation call for new path
            "records/conn-1/space/new.txt",
        ]

        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        await proc.on_records_moved([("ext-old", new_record, [])])

        # Verify build_record_path was called at least once during snapshot
        assert proc._storage_cleanup.build_record_path.call_count >= 1


# ===========================================================================
# on_new_record_groups — snapshot integration
# ===========================================================================


class TestOnNewRecordGroupsSnapshotIntegration:
    @pytest.mark.asyncio
    async def test_renamed_group_gets_old_prefix_snapshot(self):
        """When a group is renamed, old prefix is captured via snapshot."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        existing_group = MagicMock()
        existing_group.id = "rg-1"
        existing_group.name = "Old Space"
        existing_group.connector_id = "conn-1"

        new_group = RecordGroup(
            external_group_id="ext-rg-1",
            name="New Space",
            group_type="CONFLUENCE_SPACES",
            connector_name=ConnectorsEnum.CONFLUENCE,
            connector_id="conn-1",
        )

        call_sequence = []

        async def tracking_get_rg(connector_id, external_id):
            call_sequence.append("get_rg")
            return existing_group

        tx_store.get_record_group_by_external_id = tracking_get_rg

        proc._storage_cleanup.build_record_group_hierarchical_prefix.side_effect = [
            "records/conn-1/Old Space",
            "records/conn-1/New Space",
        ]

        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        await proc.on_new_record_groups([(new_group, [])])

        # Verify both old and new prefixes were computed
        prefix_calls = proc._storage_cleanup.build_record_group_hierarchical_prefix.call_args_list
        assert len(prefix_calls) >= 1
        # First call should use override_leaf_name for old name
        first_call_kwargs = prefix_calls[0].kwargs if prefix_calls[0].kwargs else {}
        # Check it used override_leaf_name="Old Space"
        assert first_call_kwargs.get("override_leaf_name") == "Old Space"

    @pytest.mark.asyncio
    async def test_unchanged_group_name_skips_snapshot(self):
        """Groups whose name didn't change are not snapshotted."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        existing_group = MagicMock()
        existing_group.id = "rg-1"
        existing_group.name = "Same Name"
        existing_group.connector_id = "conn-1"

        new_group = RecordGroup(
            external_group_id="ext-rg-1",
            name="Same Name",
            group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_DRIVE,
            connector_id="conn-1",
        )

        tx_store.get_record_group_by_external_id.return_value = existing_group

        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)

        await proc.on_new_record_groups([(new_group, [])])

        # build_record_group_hierarchical_prefix should not be called for unchanged name
        proc._storage_cleanup.build_record_group_hierarchical_prefix.assert_not_called()


# ===========================================================================
# Edge cases and stress
# ===========================================================================


class TestBlobMoveEdgeCases:
    @pytest.mark.asyncio
    async def test_flush_many_moves_sequential(self):
        """100 moves execute sequentially without errors."""
        proc = _make_processor()
        executed = []

        async def track(org_id, old_path, new_path):
            executed.append((old_path, new_path))

        proc._storage_cleanup.move_record_tree = track

        moves = [
            ("org-1", f"records/conn/space/file_{i}.txt", f"records/conn/space/renamed_{i}.txt")
            for i in range(100)
        ]
        await proc._flush_pending_blob_moves(moves)

        assert len(executed) == 100

    @pytest.mark.asyncio
    async def test_snapshot_partial_failures_dont_block_batch(self):
        """If some records fail path computation, others still get snapshotted."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        r1 = _make_record(external_record_id="ext-ok")
        r2 = _make_record(external_record_id="ext-fail")
        r3 = _make_record(external_record_id="ext-ok2")

        call_count = 0

        async def get_record(connector_id, external_id):
            rec = _make_record(external_record_id=external_id)
            rec.id = f"db-{external_id}"
            return rec

        async def build_path(rec, transaction=None):
            if rec.id == "db-ext-fail":
                raise RuntimeError("traversal timeout")
            return f"records/conn-1/{rec.id}"

        tx_store.get_record_by_external_id = get_record
        proc._storage_cleanup.build_record_path = build_path

        result = await proc._snapshot_old_paths(
            [(r1, []), (r2, []), (r3, [])], tx_store,
        )

        assert result["ext-ok"] == "records/conn-1/db-ext-ok"
        assert result["ext-fail"] is None
        assert result["ext-ok2"] == "records/conn-1/db-ext-ok2"

    @pytest.mark.asyncio
    async def test_concurrent_snapshot_with_mixed_results(self):
        """Large batch with mix of existing, new, and erroring records."""
        proc = _make_processor()
        tx_store = _make_tx_store()

        records = []
        for i in range(200):
            records.append((_make_record(external_record_id=f"ext-{i}"), []))

        async def get_record(connector_id, external_id):
            idx = int(external_id.split("-")[1])
            if idx % 3 == 0:
                return None  # new record
            rec = _make_record(external_record_id=external_id)
            rec.id = f"db-{external_id}"
            return rec

        async def build_path(rec, transaction=None):
            idx = int(rec.id.split("-")[-1])
            if idx % 7 == 0:
                raise RuntimeError("intermittent failure")
            return f"records/path/{rec.id}"

        tx_store.get_record_by_external_id = get_record
        proc._storage_cleanup.build_record_path = build_path

        result = await proc._snapshot_old_paths(records, tx_store)

        # New records (i % 3 == 0) should be absent
        for i in range(200):
            if i % 3 == 0:
                assert f"ext-{i}" not in result
            elif i % 7 == 0:
                assert result[f"ext-{i}"] is None
            else:
                assert result[f"ext-{i}"] is not None
