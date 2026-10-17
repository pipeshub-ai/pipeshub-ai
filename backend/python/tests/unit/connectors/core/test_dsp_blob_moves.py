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
from app.services.graph_db.common.record_visibility import RecordVisibility


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
        cleanup.move_record_tree = AsyncMock(return_value={"moved": 1})
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


def _make_folder():
    folder = _make_record(virtual_record_id=None, record_name="a_b")
    folder.is_file = False
    return folder


class TestFolderMoveOwnership:
    """A folder has no vrid of its own, so its move names the vrids stored
    beneath it; a same-named sibling's content (e.g. "a/b" vs "a_b") is then
    left in place by the move-tree endpoint."""

    @pytest.mark.asyncio
    async def test_flush_sends_each_owner_kind_to_the_endpoint(self):
        proc = _make_processor()
        await proc._flush_pending_blob_moves([
            ("org-1", "records/kb/a_b", "records/kb/HR/a_b", ("v1", "v2")),
            ("org-1", "records/kb/empty", "records/kb/HR/empty", ()),
            ("org-1", "records/kb/file", "records/kb/HR/file", "v9"),
            ("org-1", "records/kb/legacy", "records/kb/HR/legacy", None),
        ])
        kwargs_by_old = {
            c.args[1]: c.kwargs for c in proc._storage_cleanup.move_record_tree.call_args_list
        }
        assert kwargs_by_old == {
            "records/kb/a_b": {"virtual_record_ids": ["v1", "v2"]},
            "records/kb/empty": {"virtual_record_ids": []},
            "records/kb/file": {"virtual_record_id": "v9"},
            "records/kb/legacy": {},
        }

    @pytest.mark.asyncio
    async def test_same_batch_child_moves_follow_a_collision_safe_folder_move(self):
        # One sync moved folder "a/b" into HR and renamed a file inside it; the
        # twin "a_b" (same storage dir) also has a pending move of its own.
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path))
            return {"moved": 2, "collision": "virtual_record_ids" in kwargs}

        proc._storage_cleanup.move_record_tree = move
        await proc._flush_pending_blob_moves([
            ("org-1", "records/kb/a_b", "records/kb/HR/a_b", ("v1", "v2")),
            ("org-1", "records/kb/a_b/Docs/old.txt", "records/kb/HR/a_b/Docs/new.txt", "v2"),
            ("org-1", "records/kb/a_b/under.txt", "records/kb/a_b/under-renamed.txt", "v9"),
        ])

        assert calls[0] == ("records/kb/a_b", "records/kb/HR/a_b")
        assert set(calls[1:]) == {
            # the folder's own file is looked up where the folder move put it
            ("records/kb/HR/a_b/Docs/old.txt", "records/kb/HR/a_b/Docs/new.txt"),
            # the twin's file never moved, so its move is left untouched
            ("records/kb/a_b/under.txt", "records/kb/a_b/under-renamed.txt"),
        }

    @pytest.mark.asyncio
    async def test_twin_folders_children_stay_when_a_childless_doc_is_renamed(self) -> None:
        # Doc "Reports" (v1, no children) and folder "Reports" share the prefix;
        # the folder's child c1 is stored under it and has a pending move too.
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path, kwargs))
            return {"moved": 2, "collision": True}

        proc._storage_cleanup.move_record_tree = move
        await proc._flush_pending_blob_moves([
            ("org-1", "records/c/Reports", "records/c/Q3", ("v1",)),
            ("org-1", "records/c/Reports/a.txt", "records/c/Reports/b.txt", "c1"),
        ])

        assert calls[0] == ("records/c/Reports", "records/c/Q3", {"virtual_record_ids": ["v1"]})
        assert calls[1][:2] == ("records/c/Reports/a.txt", "records/c/Reports/b.txt")

    @pytest.mark.asyncio
    async def test_single_vrid_collision_moved_the_subtree_but_not_the_twin(self) -> None:
        # A single-vrid move (descendants unknown) relocates everything below
        # old_path; only the twin's documents at old_path itself stay.
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path))
            return {"moved": 3, "collision": True}

        proc._storage_cleanup.move_record_tree = move
        await proc._flush_pending_blob_moves([
            ("org-1", "records/c/Invoice", "records/c/Paid/Invoice", "v1"),
            ("org-1", "records/c/Invoice/att.pdf", "records/c/Invoice/att2.pdf", "a1"),
            ("org-1", "records/c/Invoice", "records/c/Invoice-2", "v2"),
        ])

        assert calls[0] == ("records/c/Invoice", "records/c/Paid/Invoice")
        assert set(calls[1:]) == {
            ("records/c/Paid/Invoice/att.pdf", "records/c/Paid/Invoice/att2.pdf"),
            ("records/c/Invoice", "records/c/Invoice-2"),
        }

    @pytest.mark.asyncio
    async def test_single_vrid_without_collision_rewrites_children(self) -> None:
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path))
            return {"moved": 2, "collision": False}

        proc._storage_cleanup.move_record_tree = move
        await proc._flush_pending_blob_moves([
            ("org-1", "records/c/Mail", "records/c/Archive/Mail", "v1"),
            ("org-1", "records/c/Mail/att.pdf", "records/c/Mail/renamed.pdf", "a1"),
        ])

        assert calls[1] == ("records/c/Archive/Mail/att.pdf", "records/c/Archive/Mail/renamed.pdf")

    @pytest.mark.asyncio
    async def test_folder_too_large_for_one_request_falls_back(self):
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_descendant_virtual_record_ids = AsyncMock(
            return_value=[f"v{i}" for i in range(100_001)]
        )
        folder = _make_folder()

        assert await proc._blob_move_owner(folder, folder, cleanup) is None

    @pytest.mark.asyncio
    async def test_a_record_with_no_children_lists_only_itself(self) -> None:
        # Doc "Reports" next to folder "Reports": a single-vrid move would also
        # carry everything stored beneath the shared prefix, i.e. the folder's
        # children. Listing what it owns moves only the Doc's own documents.
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_descendant_virtual_record_ids = AsyncMock(return_value=[])
        record = _make_record(virtual_record_id="v1")

        assert await proc._blob_move_owner(record, record, cleanup) == ("v1",)

    @pytest.mark.asyncio
    async def test_a_page_with_children_names_itself_and_its_children(self):
        # "Q1: Plan" and "Q1_ Plan" share a prefix; the single-vrid guard would
        # still sweep up the twin page's child pages stored beneath it.
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_descendant_virtual_record_ids = AsyncMock(return_value=["c1", "c2"])
        page = _make_record(virtual_record_id="v1")

        assert await proc._blob_move_owner(page, page, cleanup) == ("v1", "c1", "c2")

    @pytest.mark.asyncio
    async def test_failed_lookup_keeps_the_single_vrid_move_for_content(self):
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_descendant_virtual_record_ids = AsyncMock(side_effect=RuntimeError("db down"))
        record = _make_record(virtual_record_id="v1")

        assert await proc._blob_move_owner(record, record, cleanup) == "v1"

    @pytest.mark.asyncio
    async def test_owner_is_the_content_beneath_a_folder(self):
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_descendant_virtual_record_ids = AsyncMock(return_value=["v1", "v2"])
        folder = _make_folder()

        owner = await proc._blob_move_owner(folder, folder, cleanup, transaction="txn-1")

        assert owner == ("v1", "v2")
        cleanup.graph_provider.get_descendant_virtual_record_ids.assert_awaited_once_with(
            folder.id, transaction="txn-1",
        )

    @pytest.mark.asyncio
    async def test_a_file_without_a_vrid_keeps_the_plain_move(self):
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_descendant_virtual_record_ids = AsyncMock()
        unindexed = _make_record(virtual_record_id=None)

        assert await proc._blob_move_owner(unindexed, unindexed, cleanup) is None
        cleanup.graph_provider.get_descendant_virtual_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_lookup_falls_back_to_the_whole_prefix_move(self):
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_descendant_virtual_record_ids = AsyncMock(side_effect=RuntimeError("db down"))
        folder = _make_folder()

        assert await proc._blob_move_owner(folder, folder, cleanup) is None


class TestFlushPendingBlobMoves:
    @pytest.mark.asyncio
    async def test_parent_moves_before_child(self):
        """Moves are sorted by old_path length so parents execute first."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "records/conn/space/parent/child.txt", "records/conn/space/parent_new/child.txt", None),
            ("org-1", "records/conn/space/parent", "records/conn/space/parent_new", None),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert move_calls[0] == ("records/conn/space/parent", "records/conn/space/parent_new")

    @pytest.mark.asyncio
    async def test_prefix_rewrite_after_parent_move(self):
        """Child's old_path is rewritten after parent move succeeds."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "records/conn/space/parent", "records/conn/space/renamed", None),
            ("org-1", "records/conn/space/parent/child.txt", "records/conn/space/renamed/child_new.txt", None),
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
    async def test_partial_parent_move_retries_then_moves_child_from_both_paths(self):
        """Documents the parent move left behind still get the child's rename."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            if old_path == "records/conn/space/parent":
                return {"moved": 1, "failed": ["doc-stuck"]}
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "records/conn/space/parent", "records/conn/space/renamed", None),
            ("org-1", "records/conn/space/parent/child.txt", "records/conn/space/renamed/child_new.txt", None),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert move_calls == [
            ("records/conn/space/parent", "records/conn/space/renamed"),
            ("records/conn/space/parent", "records/conn/space/renamed"),  # retry
            ("records/conn/space/renamed/child.txt", "records/conn/space/renamed/child_new.txt"),
            ("records/conn/space/parent/child.txt", "records/conn/space/renamed/child_new.txt"),
        ]

    @pytest.mark.asyncio
    async def test_retry_that_heals_the_parent_adds_no_extra_child_move(self):
        proc = _make_processor()
        move_calls = []
        attempts = {"parent": 0}

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            if old_path == "records/conn/space/parent":
                attempts["parent"] += 1
                if attempts["parent"] == 1:
                    return {"moved": 1, "failed": ["doc-transient"]}
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "records/conn/space/parent", "records/conn/space/renamed", None),
            ("org-1", "records/conn/space/parent/child.txt", "records/conn/space/renamed/child_new.txt", None),
        ]
        await proc._flush_pending_blob_moves(moves)

        assert len(move_calls) == 3
        assert move_calls[2] == (
            "records/conn/space/renamed/child.txt",
            "records/conn/space/renamed/child_new.txt",
        )

    @pytest.mark.asyncio
    async def test_noop_moves_skipped(self):
        """Moves where old_path == new_path are skipped entirely."""
        proc = _make_processor()
        moves = [
            ("org-1", "records/conn/space/file.txt", "records/conn/space/file.txt", None),
        ]
        await proc._flush_pending_blob_moves(moves)
        proc._storage_cleanup.move_record_tree.assert_not_called()

    @pytest.mark.asyncio
    async def test_rewritten_noop_skipped(self):
        """After parent move, child becomes a noop and is skipped."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        # Parent renamed, child not renamed — after prefix rewrite, child becomes noop
        moves = [
            ("org-1", "records/conn/old_space", "records/conn/new_space", None),
            ("org-1", "records/conn/old_space/file.txt", "records/conn/new_space/file.txt", None),
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

        async def failing_then_ok(org_id, old_path, new_path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("network error")
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = failing_then_ok

        moves = [
            ("org-1", "records/a", "records/b", None),
            ("org-1", "records/c", "records/d", None),
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
        await proc._flush_pending_blob_moves([("org-1", "a", "b", None)])

    @pytest.mark.asyncio
    async def test_deep_hierarchy_prefix_rewrite_chain(self):
        """A 3-level rename cascade: grandparent → parent → child."""
        proc = _make_processor()
        move_calls = []

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "r/c/gp", "r/c/GP", None),
            ("org-1", "r/c/gp/parent", "r/c/GP/Parent", None),
            ("org-1", "r/c/gp/parent/child.txt", "r/c/GP/Parent/Child.txt", None),
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

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            # Parent rename — shorter old_path, runs first after sort
            ("org-1", "r/c/space/Overview/Release Notes", "r/c/space/Overview/Release Notes-v2", None),
            # Child reparent — longer old_path name, new_path uses stale parent name
            ("org-1", "r/c/space/Overview/Combined Summary From Prior", "r/c/space/Overview/Release Notes/Combined Summary From Prior", None),
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

        async def track_move(org_id, old_path, new_path, **kwargs):
            move_calls.append((old_path, new_path))
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track_move

        moves = [
            ("org-1", "r/c/space/A", "r/c/space/B", None),
            # This move's new_path starts with "r/c/space/AB" — not a child of "r/c/space/A"
            ("org-1", "r/c/space/X", "r/c/space/AB/child", None),
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

        async def capturing_process(rec, perms, store, moved_vrids=None, *, publishes_event=True, pre_old_path=_NO_OLD_PATH, narrowed_connectors=None, permissions_fetched=False):
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

        async def lookup(*_args, visibility=RecordVisibility.ALL, **_kwargs) -> object:
            return None if visibility is RecordVisibility.DELETED else old_record

        tx_store.get_record_by_external_id.side_effect = lookup

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
        existing_group.parent_external_group_id = None

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

        async def track(org_id, old_path, new_path, **kwargs):
            executed.append((old_path, new_path))
            return {"moved": 1}

        proc._storage_cleanup.move_record_tree = track

        moves = [
            ("org-1", f"records/conn/space/file_{i}.txt", f"records/conn/space/renamed_{i}.txt", None)
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


class TransientError(Exception):
    """Stand-in for neo4j.exceptions.TransientError; matched by class name."""


class TestOnNewRecordsDeadlockRetry:
    @pytest.mark.asyncio
    async def test_deadlock_retries_batch_and_flushes_moves_of_final_attempt_only(self):
        proc = _make_processor()
        first_tx, second_tx = _make_tx_store(), _make_tx_store()
        proc.data_store_provider.transaction = MagicMock(
            side_effect=[_make_ctx(first_tx), _make_ctx(second_tx)]
        )
        proc._snapshot_old_paths = AsyncMock(return_value={})
        move_a, move_b = object(), object()
        rec_a = _make_record(external_record_id="a")
        rec_b = _make_record(external_record_id="b")
        calls = {"n": 0}

        async def process(record, permissions, tx_store, moved, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise TransientError("Neo.TransientError.Transaction.DeadlockDetected")
            return record, [move_a if record is rec_a else move_b]

        proc._process_record = process
        proc._flush_pending_blob_moves = AsyncMock()

        with patch(
            "app.connectors.core.base.data_store.graph_data_store.asyncio.sleep",
            AsyncMock(),
        ):
            await proc.on_new_records([(rec_a, []), (rec_b, [])])

        proc._flush_pending_blob_moves.assert_awaited_once_with([move_a, move_b])
        assert proc.data_store_provider.transaction.call_count == 2


class TestMoveFromActualPath:
    """A file renamed while its folder is renamed by another request computes its
    old path from before the folder moved; the move finds nothing there."""

    @staticmethod
    def _proc(
        actual: str | None, holders: tuple | Exception = ("r1",), holder_path: str | None = None,
    ) -> tuple[DataSourceEntitiesProcessor, list]:
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path, kwargs))
            return {"moved": 0} if len(calls) == 1 else {"moved": 2}

        proc._storage_cleanup.move_record_tree = move
        proc._storage_cleanup.current_content_path = AsyncMock(return_value=actual)
        proc._storage_cleanup.graph_provider = MagicMock()
        proc._storage_cleanup.graph_provider.get_records_by_virtual_record_id = (
            AsyncMock(side_effect=holders) if isinstance(holders, Exception) else AsyncMock(return_value=list(holders))
        )
        proc._storage_cleanup.graph_provider.get_record_by_id = AsyncMock(return_value=MagicMock())
        # Where the holder's own path is now; the fallback only serves a move to it.
        proc._storage_cleanup.build_record_path = AsyncMock(return_value=holder_path)
        return proc, calls

    @pytest.mark.asyncio
    async def test_shared_content_stays_with_the_record_it_is_filed_under(self) -> None:
        # A second holder's own path never held the copy, so its rename finds
        # nothing there; that must not pull the copy out of the first holder's folder.
        proc, calls = self._proc("records/kb1/Here/shared", holders=("r1", "r2"))
        await proc._flush_pending_blob_moves([
            ("org-1", "records/kb2/There/copy", "records/kb2/There/renamed", ("v1",)),
        ])
        assert len(calls) == 1
        proc._storage_cleanup.graph_provider.get_records_by_virtual_record_id.assert_awaited_once_with(
            "v1", raise_on_error=True,
        )

    @pytest.mark.asyncio
    async def test_content_stays_put_when_its_holders_cannot_be_listed(self) -> None:
        proc, calls = self._proc("records/kb/elsewhere", holders=RuntimeError("graph down"))
        await proc._flush_pending_blob_moves([("org-1", "records/kb/a", "records/kb/b", "v1")])
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_record_is_moved_from_where_it_actually_is(self) -> None:
        proc, calls = self._proc("records/kb/Team-r1/Notes/f1", holder_path="records/kb/Team-r1/Notes/f1-r1")
        await proc._flush_pending_blob_moves([
            ("org-1", "records/kb/Team/Notes/f1", "records/kb/Team-r1/Notes/f1-r1", ("v1",)),
        ])
        assert calls[1] == (
            "records/kb/Team-r1/Notes/f1", "records/kb/Team-r1/Notes/f1-r1",
            {"virtual_record_ids": ["v1"]},
        )
        proc._storage_cleanup.current_content_path.assert_awaited_once_with("org-1", "v1")

    @pytest.mark.asyncio
    async def test_nothing_more_when_the_record_has_no_stored_content(self) -> None:
        proc, calls = self._proc(None, holder_path="records/kb/b")
        await proc._flush_pending_blob_moves([("org-1", "records/kb/a", "records/kb/b", "v1")])
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_nothing_more_when_it_already_sits_at_the_new_path(self) -> None:
        proc, calls = self._proc("records/kb/b", holder_path="records/kb/b")
        await proc._flush_pending_blob_moves([("org-1", "records/kb/a", "records/kb/b", "v1")])
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_a_group_move_owning_one_record_does_not_pull_it_up(self) -> None:
        # Group "Team" holds one file stored elsewhere; renaming the group must not
        # move that file's content to the group's own folder path.
        proc, calls = self._proc("records/kb/flat/f1", holder_path="records/kb/Team2/f1")
        await proc._flush_pending_blob_moves([("org-1", "records/kb/Team", "records/kb/Team2", ("v1",))])
        assert len(calls) == 1
        proc._storage_cleanup.current_content_path.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_folders_are_not_relocated_this_way(self) -> None:
        proc, calls = self._proc("records/kb/elsewhere")
        await proc._flush_pending_blob_moves([("org-1", "records/kb/a", "records/kb/b", ("c1", "c2"))])
        assert len(calls) == 1
        proc._storage_cleanup.current_content_path.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_move_that_moved_something_is_not_redone(self) -> None:
        proc = _make_processor()
        proc._storage_cleanup.move_record_tree = AsyncMock(return_value={"moved": 2})
        proc._storage_cleanup.current_content_path = AsyncMock()
        await proc._flush_pending_blob_moves([("org-1", "records/kb/a", "records/kb/b", "v1")])
        proc._storage_cleanup.current_content_path.assert_not_awaited()


class TestGroupMoveOwnership:
    """L09: two record groups whose names sanitize alike share one storage prefix."""

    @pytest.mark.asyncio
    async def test_owner_is_every_vrid_stored_in_the_group(self) -> None:
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_record_group_virtual_record_ids = AsyncMock(return_value=["v1", "v2"])
        assert await proc._group_move_owner(cleanup, "rg-1", transaction="t") == [("v1", "v2")]
        cleanup.graph_provider.get_record_group_virtual_record_ids.assert_awaited_once_with("rg-1", transaction="t")

    @pytest.mark.asyncio
    async def test_an_empty_group_owns_nothing(self) -> None:
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_record_group_virtual_record_ids = AsyncMock(return_value=[])
        assert await proc._group_move_owner(cleanup, "rg-1") == [()]

    @pytest.mark.asyncio
    async def test_a_failed_lookup_moves_nothing_rather_than_the_whole_prefix(self) -> None:
        # The whole prefix would carry a same-named group's files along.
        proc = _make_processor()
        cleanup = MagicMock()
        cleanup.graph_provider.get_record_group_virtual_record_ids = AsyncMock(side_effect=RuntimeError("db"))
        assert await proc._group_move_owner(cleanup, "rg-1") == []

    @pytest.mark.asyncio
    async def test_a_large_group_is_moved_in_named_batches(self) -> None:
        proc = _make_processor()
        cleanup = MagicMock()
        vrids = [f"v{i}" for i in range(100_001)]
        cleanup.graph_provider.get_record_group_virtual_record_ids = AsyncMock(return_value=vrids)
        owners = await proc._group_move_owner(cleanup, "rg-1")
        assert [len(o) for o in owners] == [100_000, 1]
        assert [v for o in owners for v in o] == vrids

    @pytest.mark.asyncio
    async def test_each_batch_of_a_large_group_is_moved_from_the_old_prefix(self) -> None:
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path, kwargs.get("virtual_record_ids")))
            return {"moved": 1, "collision": True}

        proc._storage_cleanup.move_record_tree = move
        await proc._flush_pending_blob_moves([
            ("org-1", "records/c/A", "records/c/B", ("a1", "a2")),
            ("org-1", "records/c/A", "records/c/B", ("a3",)),
        ])
        assert calls == [
            ("records/c/A", "records/c/B", ["a1", "a2"]),
            ("records/c/A", "records/c/B", ["a3"]),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("collision", [True, False])
    async def test_chain_and_swap_in_one_batch_name_each_groups_content(self, collision) -> None:
        # A->B and B->C, plus a swap X<->Y, in one sync: with each move naming
        # its own vrids, the endpoint never carries one group's files with another's.
        # Without a collision (nothing else under the source) the next move must
        # still not be redirected onto the first one's destination.
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path, kwargs.get("virtual_record_ids")))
            return {"moved": 1, "collision": collision}

        proc._storage_cleanup.move_record_tree = move
        await proc._flush_pending_blob_moves([
            ("org-1", "records/c/A", "records/c/B", ("a1",)),
            ("org-1", "records/c/B", "records/c/C", ("b1",)),
            ("org-1", "records/c/X", "records/c/Y", ("x1",)),
            ("org-1", "records/c/Y", "records/c/X", ("y1",)),
        ])
        assert sorted(calls) == sorted([
            ("records/c/A", "records/c/B", ["a1"]),
            ("records/c/B", "records/c/C", ["b1"]),
            ("records/c/X", "records/c/Y", ["x1"]),
            ("records/c/Y", "records/c/X", ["y1"]),
        ])


    @pytest.mark.asyncio
    async def test_a_child_of_unknown_content_follows_a_whole_prefix_move(self) -> None:
        # No collision: the parent's move carried everything under it, so a child
        # move whose content is unknown now starts from the new location.
        proc = _make_processor()
        calls = []

        async def move(org_id: str, old_path: str, new_path: str, **kwargs: object) -> dict:
            calls.append((old_path, new_path))
            return {"moved": 1, "collision": False}

        proc._storage_cleanup.move_record_tree = move
        await proc._flush_pending_blob_moves([
            ("org-1", "records/c/P", "records/c/Q", ("p1", "c1")),
            ("org-1", "records/c/P/child", "records/c/P/kid", None),
        ])
        assert calls == [("records/c/P", "records/c/Q"), ("records/c/Q/child", "records/c/Q/kid")]


class TestRecordGroupReparented:
    """L20: a group moved under another parent keeps its name; its storage moves
    and its old parent and inherit edges go."""

    @staticmethod
    def _setup(old_parent: str | None, new_parent: str | None) -> tuple[DataSourceEntitiesProcessor, MagicMock, RecordGroup]:
        proc = _make_processor()
        tx_store = _make_tx_store()
        existing = MagicMock()
        existing.id = "rg-1"
        existing.name = "Reports"
        existing.connector_id = "conn-1"
        existing.parent_external_group_id = old_parent
        tx_store.get_record_group_by_external_id.return_value = existing
        tx_store.delete_edges_between_collections = AsyncMock()
        proc.data_store_provider.transaction.return_value = _make_ctx(tx_store)
        state = {"parent": old_parent}

        # The prefix follows the parent edge as it is in the graph right now.
        async def prefix(rg_id, cid, *, override_leaf_name=None, transaction=None) -> str:
            return f"records/{cid}/{state['parent'] or 'ROOT'}/{override_leaf_name or 'Reports'}"

        async def drop_old_parent(*args, **kwargs) -> None:
            state["parent"] = new_parent

        tx_store.delete_edges_between_collections.side_effect = drop_old_parent
        proc._storage_cleanup.build_record_group_hierarchical_prefix = AsyncMock(side_effect=prefix)
        proc._storage_cleanup.graph_provider = MagicMock()
        proc._storage_cleanup.graph_provider.get_record_group_virtual_record_ids = AsyncMock(return_value=["v1"])
        proc._storage_cleanup.move_record_tree = AsyncMock(return_value={"moved": 1})
        group = RecordGroup(
            external_group_id="ext-rg-1", name="Reports", group_type="DRIVE",
            connector_name=ConnectorsEnum.GOOGLE_DRIVE, connector_id="conn-1",
            parent_external_group_id=new_parent,
        )
        return proc, tx_store, group

    @pytest.mark.asyncio
    async def test_moving_to_another_parent_moves_storage_and_drops_the_old_edge(self) -> None:
        proc, tx_store, group = self._setup("Sales", "Finance")

        await proc.on_new_record_groups([(group, [])])

        # Both its parent edge and its inherit edge to the old parent go: keeping
        # the inherit edge would leave the old parent's readers with access.
        assert [c.args for c in tx_store.delete_edges_between_collections.await_args_list] == [
            ("rg-1", "recordGroups", "belongsTo", "recordGroups"),
            ("rg-1", "recordGroups", "inheritPermissions", "recordGroups"),
            ("rg-1", "recordGroups", "belongsTo", "apps"),
        ]
        proc._storage_cleanup.move_record_tree.assert_awaited_once_with(
            "org-1", "records/conn-1/Sales/Reports", "records/conn-1/Finance/Reports",
            virtual_record_ids=["v1"],
        )

    @pytest.mark.asyncio
    async def test_moving_to_the_root_drops_the_parent_edge(self) -> None:
        proc, tx_store, group = self._setup("Sales", None)

        await proc.on_new_record_groups([(group, [])])

        assert tx_store.delete_edges_between_collections.await_count == 2
        proc._storage_cleanup.move_record_tree.assert_awaited_once_with(
            "org-1", "records/conn-1/Sales/Reports", "records/conn-1/ROOT/Reports",
            virtual_record_ids=["v1"],
        )

    @pytest.mark.asyncio
    async def test_a_root_group_that_gains_a_parent_loses_its_app_edge(self) -> None:
        proc, tx_store, group = self._setup(None, "Finance")

        await proc.on_new_record_groups([(group, [])])

        assert ("rg-1", "recordGroups", "belongsTo", "apps") in [
            c.args for c in tx_store.delete_edges_between_collections.await_args_list
        ]

    @pytest.mark.asyncio
    async def test_a_failed_prefix_lookup_skips_only_the_storage_move(self) -> None:
        proc, tx_store, group = self._setup("Sales", "Finance")
        calls = {"n": 0}

        async def prefix(rg_id, cid, *, override_leaf_name=None, transaction=None) -> str:
            calls["n"] += 1
            if calls["n"] > 1:  # the snapshot succeeds; the new prefix cannot be read
                raise RuntimeError("graph timeout")
            return "records/conn-1/Sales/Reports"

        proc._storage_cleanup.build_record_group_hierarchical_prefix = AsyncMock(side_effect=prefix)

        await proc.on_new_record_groups([(group, [])])

        tx_store.batch_upsert_record_groups.assert_awaited_once()
        proc._storage_cleanup.move_record_tree.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unlisted_content_is_not_moved(self) -> None:
        proc, tx_store, group = self._setup("Sales", "Finance")
        proc._storage_cleanup.graph_provider.get_record_group_virtual_record_ids = AsyncMock(
            side_effect=RuntimeError("db"),
        )

        await proc.on_new_record_groups([(group, [])])

        tx_store.batch_upsert_record_groups.assert_awaited_once()
        proc._storage_cleanup.move_record_tree.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_same_parent_and_name_does_nothing(self) -> None:
        proc, tx_store, group = self._setup("Sales", "Sales")

        await proc.on_new_record_groups([(group, [])])

        tx_store.delete_edges_between_collections.assert_not_awaited()
        proc._storage_cleanup.move_record_tree.assert_not_awaited()
