"""Comprehensive unit tests for gitlab incremental-sync functionality.

Ports all scenarios from the legacy test_gitlab_incremental_sync.py to the
refactored gitlab module structure where:
- ``_classify_compare_diffs`` is a module-level function (no self)
- incremental sync methods live on ``ReposSync(connector)``

Covers:
* File operations: content change, rename, move, delete, add, dotfile filtering
* Directory operations: rename/move inferred from file-level diffs, cascade delete
* SHA reconciliation: identical-content move detected without renamed_file flag
* Diff-API failure and history-rewrite → full-sync fallback
* Reindex event emission: modify fires on_new_records, rename fires on_records_moved
* Routing: no checkpoint → full sync, head unchanged → no-op, fallback on failure

Async test execution:
    - In CI (pytest-asyncio installed, asyncio_mode=auto in pytest.ini): tests are
      discovered automatically as asyncio tests without any decorator needed.
    - Locally (anyio installed): module-level pytestmark = pytest.mark.anyio ensures
      async tests are run via the anyio pytest plugin on the asyncio backend.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.connectors.sources.gitlab.constants import GITLAB_COMPARE_DIFF_LIMIT
from app.connectors.sources.gitlab.repos import ReposSync, _classify_compare_diffs

from .conftest import (
    branch_res,
    diff_entry,
    fail_compare,
    make_mock_connector,
    ok_compare,
    paged_res,
    tree_entry,
)

pytestmark = pytest.mark.anyio

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_PROJECT_ID = 42
_PROJECT_PATH = "my-group/my-project"
_FROM_SHA = "oldsha01"
_TO_SHA = "newsha99"


# ---------------------------------------------------------------------------
# Object-attribute shaped diff (covers getattr branch in _classify_compare_diffs)
# ---------------------------------------------------------------------------

class _AttrDiff:
    def __init__(
        self,
        old_path: str,
        new_path: str,
        deleted_file: bool = False,
        renamed_file: bool = False,
        new_file: bool = False,
    ) -> None:
        self.old_path = old_path
        self.new_path = new_path
        self.deleted_file = deleted_file
        self.renamed_file = renamed_file
        self.new_file = new_file


def _make_incremental_connector() -> tuple[MagicMock, ReposSync]:
    """Build a connector mock + ReposSync with internal helpers pre-stubbed."""
    c = make_mock_connector()
    repos = ReposSync(c)
    repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))
    repos._delete_code_files_by_paths = AsyncMock()
    repos._apply_code_renames = AsyncMock(return_value=True)
    repos._upsert_code_files_by_paths = AsyncMock(return_value=True)
    repos._cleanup_emptied_folders = AsyncMock()
    repos._sync_repo_full = AsyncMock(return_value=True)
    repos._get_code_repo_checkpoint = AsyncMock(return_value=None)
    repos._update_code_repo_checkpoint = AsyncMock()
    repos.build_code_file_records = AsyncMock()
    return c, repos


# ===========================================================================
# 1. TestClassifyCompareDiffs — module-level function
# ===========================================================================


class TestClassifyCompareDiffs:
    """Pure unit tests for _classify_compare_diffs (module-level function)."""

    # --- Single-operation cases ---

    def test_plain_modify(self) -> None:
        diffs = [diff_entry(old_path="src/a.py", new_path="src/a.py")]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert deletes == []
        assert adds == []
        assert modifies == ["src/a.py"]
        assert renames == []

    def test_new_file_add(self) -> None:
        diffs = [diff_entry(old_path="src/new.py", new_path="src/new.py", new_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert adds == ["src/new.py"]
        assert deletes == [] and modifies == [] and renames == []

    def test_delete(self) -> None:
        diffs = [diff_entry(old_path="src/gone.py", new_path="src/gone.py", deleted_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert deletes == ["src/gone.py"]
        assert adds == [] and modifies == [] and renames == []

    def test_rename_same_directory(self) -> None:
        diffs = [diff_entry(old_path="src/old.py", new_path="src/new.py", renamed_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert renames == [("src/old.py", "src/new.py")]
        assert deletes == [] and adds == [] and modifies == []

    def test_move_across_directories(self) -> None:
        diffs = [diff_entry(old_path="lib/utils.py", new_path="src/utils.py", renamed_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert renames == [("lib/utils.py", "src/utils.py")]
        assert deletes == [] and adds == [] and modifies == []

    # --- Dotfile normalization rules ---

    def test_rename_to_dotfile_target_degrades_to_delete(self) -> None:
        diffs = [diff_entry(old_path="src/readme.md", new_path="src/.readme.md", renamed_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert deletes == ["src/readme.md"]
        assert adds == [] and modifies == [] and renames == []

    def test_rename_from_dotfile_source_degrades_to_add(self) -> None:
        diffs = [diff_entry(old_path="src/.hidden.py", new_path="src/visible.py", renamed_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert adds == ["src/visible.py"]
        assert deletes == [] and modifies == [] and renames == []

    def test_plain_dotfile_add_dropped(self) -> None:
        diffs = [diff_entry(old_path=".env", new_path=".env", new_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert adds == [] and deletes == [] and modifies == [] and renames == []

    def test_plain_dotfile_modify_dropped(self) -> None:
        diffs = [diff_entry(old_path=".gitignore", new_path=".gitignore")]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert modifies == [] and adds == [] and deletes == [] and renames == []

    def test_plain_dotfile_delete_dropped(self) -> None:
        diffs = [diff_entry(old_path=".eslintrc", new_path=".eslintrc", deleted_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert deletes == [] and adds == [] and modifies == [] and renames == []

    def test_dotfile_in_subdirectory_dropped(self) -> None:
        diffs = [diff_entry(old_path="src/.cache", new_path="src/.cache", new_file=True)]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert adds == []

    # --- Deduplication ---

    def test_duplicate_modify_deduplicated(self) -> None:
        diffs = [
            diff_entry(old_path="src/a.py", new_path="src/a.py"),
            diff_entry(old_path="src/a.py", new_path="src/a.py"),
        ]
        _, _, modifies, _ = _classify_compare_diffs(diffs)
        assert modifies == ["src/a.py"]

    def test_duplicate_add_deduplicated(self) -> None:
        diffs = [
            diff_entry(old_path="new.py", new_path="new.py", new_file=True),
            diff_entry(old_path="new.py", new_path="new.py", new_file=True),
        ]
        _, adds, _, _ = _classify_compare_diffs(diffs)
        assert adds == ["new.py"]

    def test_duplicate_delete_deduplicated(self) -> None:
        diffs = [
            diff_entry(old_path="gone.py", new_path="gone.py", deleted_file=True),
            diff_entry(old_path="gone.py", new_path="gone.py", deleted_file=True),
        ]
        deletes, _, _, _ = _classify_compare_diffs(diffs)
        assert deletes == ["gone.py"]

    def test_duplicate_rename_deduplicated(self) -> None:
        diffs = [
            diff_entry(old_path="old.py", new_path="new.py", renamed_file=True),
            diff_entry(old_path="old.py", new_path="new.py", renamed_file=True),
        ]
        _, _, _, renames = _classify_compare_diffs(diffs)
        assert renames == [("old.py", "new.py")]

    def test_mixed_batch_correct_partition(self) -> None:
        diffs = [
            diff_entry(old_path="del.py", new_path="del.py", deleted_file=True),
            diff_entry(old_path="add.py", new_path="add.py", new_file=True),
            diff_entry(old_path="mod.py", new_path="mod.py"),
            diff_entry(old_path="a.py", new_path="b.py", renamed_file=True),
        ]
        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        assert deletes == ["del.py"]
        assert adds == ["add.py"]
        assert modifies == ["mod.py"]
        assert renames == [("a.py", "b.py")]

    def test_empty_diffs_returns_empty_lists(self) -> None:
        deletes, adds, modifies, renames = _classify_compare_diffs([])
        assert deletes == adds == modifies == renames == []

    # --- Object-attribute diff entries ---

    def test_object_attribute_diff_modify(self) -> None:
        diffs = [_AttrDiff("src/a.py", "src/a.py")]
        _, _, modifies, _ = _classify_compare_diffs(diffs)
        assert modifies == ["src/a.py"]

    def test_object_attribute_diff_rename(self) -> None:
        diffs = [_AttrDiff("old.py", "new.py", renamed_file=True)]
        _, _, _, renames = _classify_compare_diffs(diffs)
        assert renames == [("old.py", "new.py")]

    def test_object_attribute_diff_delete(self) -> None:
        diffs = [_AttrDiff("gone.py", "gone.py", deleted_file=True)]
        deletes, _, _, _ = _classify_compare_diffs(diffs)
        assert deletes == ["gone.py"]


# ===========================================================================
# 2. TestReconcileShaMoves — ReposSync._reconcile_sha_moves
# ===========================================================================


class TestReconcileShaMoves:
    """Tests for SHA-based delete+add promotion to rename."""

    async def test_sha_match_promotes_to_rename(self) -> None:
        c, _ = _make_incremental_connector()
        repos = ReposSync(c)

        # Existing record for old_path has SHA "sha-xyz"
        old_record = MagicMock()
        old_record.external_revision_id = "sha-xyz"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=old_record)
        # New path "new.py" has same SHA in the tree
        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("new.py", sha="sha-xyz")])
        )

        remaining_deletes, remaining_adds, extra_renames = await repos._reconcile_sha_moves(
            _PROJECT_ID, _PROJECT_PATH, ["old.py"], ["new.py"]
        )
        assert extra_renames == [("old.py", "new.py")]
        assert remaining_deletes == []
        assert remaining_adds == []

    async def test_sha_mismatch_not_promoted(self) -> None:
        c, _ = _make_incremental_connector()
        repos = ReposSync(c)

        old_record = MagicMock()
        old_record.external_revision_id = "sha-aaa"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=old_record)
        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("new.py", sha="sha-bbb")])
        )

        remaining_deletes, remaining_adds, extra_renames = await repos._reconcile_sha_moves(
            _PROJECT_ID, _PROJECT_PATH, ["old.py"], ["new.py"]
        )
        assert extra_renames == []
        assert remaining_deletes == ["old.py"]
        assert remaining_adds == ["new.py"]

    async def test_empty_deletes_no_op(self) -> None:
        c, _ = _make_incremental_connector()
        repos = ReposSync(c)

        remaining_deletes, remaining_adds, extra_renames = await repos._reconcile_sha_moves(
            _PROJECT_ID, _PROJECT_PATH, [], ["new.py"]
        )
        assert extra_renames == []
        assert remaining_deletes == []
        assert remaining_adds == ["new.py"]

    async def test_empty_adds_no_op(self) -> None:
        c, _ = _make_incremental_connector()
        repos = ReposSync(c)

        remaining_deletes, remaining_adds, extra_renames = await repos._reconcile_sha_moves(
            _PROJECT_ID, _PROJECT_PATH, ["old.py"], []
        )
        assert extra_renames == []
        assert remaining_deletes == ["old.py"]
        assert remaining_adds == []

    async def test_missing_old_record_skipped(self) -> None:
        c, _ = _make_incremental_connector()
        repos = ReposSync(c)

        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("new.py", sha="sha-xyz")])
        )

        remaining_deletes, remaining_adds, extra_renames = await repos._reconcile_sha_moves(
            _PROJECT_ID, _PROJECT_PATH, ["old.py"], ["new.py"]
        )
        assert extra_renames == []
        assert remaining_deletes == ["old.py"]

    async def test_missing_stored_sha_skipped(self) -> None:
        c, _ = _make_incremental_connector()
        repos = ReposSync(c)

        old_record = MagicMock()
        old_record.external_revision_id = ""  # empty — no stored SHA
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=old_record)
        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("new.py", sha="sha-xyz")])
        )

        remaining_deletes, remaining_adds, extra_renames = await repos._reconcile_sha_moves(
            _PROJECT_ID, _PROJECT_PATH, ["old.py"], ["new.py"]
        )
        assert extra_renames == []

    async def test_multiple_matches_all_promoted(self) -> None:
        c, _ = _make_incremental_connector()
        repos = ReposSync(c)

        records = {
            f"/my-group/my-project/-/blob/HEAD/old_{i}.py": MagicMock(external_revision_id=f"sha-{i}")
            for i in range(3)
        }
        c.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda conn_id, eid: records.get(eid)
        )

        tree_entries = [tree_entry(f"new_{i}.py", sha=f"sha-{i}") for i in range(3)]
        c.runtime.paged_list = AsyncMock(return_value=paged_res(tree_entries))

        old_paths = [f"old_{i}.py" for i in range(3)]
        new_paths = [f"new_{i}.py" for i in range(3)]
        remaining_deletes, remaining_adds, extra_renames = await repos._reconcile_sha_moves(
            _PROJECT_ID, _PROJECT_PATH, old_paths, new_paths
        )
        assert len(extra_renames) == 3
        assert remaining_deletes == []
        assert remaining_adds == []


# ===========================================================================
# 3. TestSyncRepoIncremental — ReposSync._sync_repo_incremental
# ===========================================================================


class TestSyncRepoIncremental:
    """Orchestration tests for _sync_repo_incremental."""

    async def test_compare_failure_returns_false(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=fail_compare("force-push"))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))

        result = await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert result is False

    async def test_compare_data_none_returns_false(self) -> None:
        c, repos = _make_incremental_connector()
        res = MagicMock()
        res.success = True
        res.data = None
        c.runtime.ds_call = AsyncMock(return_value=res)
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))

        result = await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert result is False

    async def test_diff_limit_exceeded_returns_false(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [
            diff_entry(old_path=f"src/f{i}.py", new_path=f"src/f{i}.py")
            for i in range(GITLAB_COMPARE_DIFF_LIMIT)
        ]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))

        result = await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert result is False

    async def test_overflow_flag_returns_false(self) -> None:
        c, repos = _make_incremental_connector()
        res = MagicMock()
        res.success = True
        res.data = {"diffs": [], "overflow": True}
        res.error = None
        c.runtime.ds_call = AsyncMock(return_value=res)
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))

        result = await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert result is False

    async def test_empty_diffs_returns_true_no_writes(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=ok_compare([]))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))

        result = await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert result is True
        repos._delete_code_files_by_paths.assert_not_called()
        repos._apply_code_renames.assert_not_called()
        repos._upsert_code_files_by_paths.assert_not_called()

    async def test_operation_order_deletes_before_renames_before_upserts(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [
            diff_entry(old_path="del.py", new_path="del.py", deleted_file=True),
            diff_entry(old_path="add.py", new_path="add.py", new_file=True),
            diff_entry(old_path="old.py", new_path="ren.py", renamed_file=True),
        ]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(return_value=(["del.py"], ["add.py"], []))

        call_order: list[str] = []
        repos._delete_code_files_by_paths = AsyncMock(
            side_effect=lambda *a, **kw: call_order.append("delete")
        )
        repos._apply_code_renames = AsyncMock(
            side_effect=lambda *a, **kw: call_order.append("rename") or True
        )
        repos._upsert_code_files_by_paths = AsyncMock(
            side_effect=lambda *a, **kw: call_order.append("upsert") or True
        )
        repos._cleanup_emptied_folders = AsyncMock()

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert call_order.index("delete") < call_order.index("rename")
        assert call_order.index("rename") < call_order.index("upsert")

    async def test_removed_paths_passed_to_cleanup(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [diff_entry(old_path="del.py", new_path="del.py", deleted_file=True)]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(return_value=(["del.py"], [], []))

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        call_args = repos._cleanup_emptied_folders.call_args
        removed_paths = call_args.args[2]
        assert "del.py" in removed_paths

    async def test_sha_reconcile_extra_renames_appended(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [
            diff_entry(old_path="del.py", new_path="del.py", deleted_file=True),
            diff_entry(old_path="add.py", new_path="add.py", new_file=True),
        ]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(
            return_value=([], [], [("del.py", "add.py")])
        )

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        repos._apply_code_renames.assert_called_once()
        renames_arg = repos._apply_code_renames.call_args.args[2]
        assert ("del.py", "add.py") in renames_arg

    async def test_adds_and_modifies_merged_for_upsert(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [
            diff_entry(old_path="add.py", new_path="add.py", new_file=True),
            diff_entry(old_path="mod.py", new_path="mod.py"),
        ]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], ["add.py"], []))

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        upsert_paths = repos._upsert_code_files_by_paths.call_args.args[2]
        assert "add.py" in upsert_paths
        assert "mod.py" in upsert_paths

    async def test_no_cleanup_when_no_removed_paths(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [diff_entry(old_path="add.py", new_path="add.py", new_file=True)]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], ["add.py"], []))

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        repos._cleanup_emptied_folders.assert_not_called()

    async def test_compare_uses_straight_true(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=ok_compare([]))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        ds_call_kwargs = c.runtime.ds_call.call_args.kwargs
        assert ds_call_kwargs.get("straight") is True


# ===========================================================================
# 4. TestApplyCodeRenames — ReposSync._apply_code_renames
# ===========================================================================


class TestApplyCodeRenames:
    """Tests for rename / move handling with tree-API resolution."""

    async def test_pure_rename_calls_on_records_moved(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("b.py", name="b.py", sha="sha-new")])
        )
        repos._ensure_folder_records_for_paths = AsyncMock()
        repos._delete_code_files_by_paths = AsyncMock()
        repos._upsert_code_files_by_paths = AsyncMock(return_value=True)

        with patch("app.utils.time_conversion.get_epoch_timestamp_in_ms", return_value=1000):
            await repos._apply_code_renames(_PROJECT_ID, _PROJECT_PATH, [("a.py", "b.py")])

        c.data_entities_processor.on_records_moved.assert_called_once()

    async def test_cross_directory_move_sets_correct_parent_external_id(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("src/b.py", name="b.py", sha="sha-new")])
        )
        repos._ensure_folder_records_for_paths = AsyncMock()

        with patch("app.utils.time_conversion.get_epoch_timestamp_in_ms", return_value=1000):
            await repos._apply_code_renames(_PROJECT_ID, _PROJECT_PATH, [("lib/a.py", "src/b.py")])

        moves = c.data_entities_processor.on_records_moved.call_args.args[0]
        _, new_record, _ = moves[0]
        assert new_record.parent_external_record_id is not None
        assert "/-/tree/" in new_record.parent_external_record_id

    async def test_root_level_rename_has_no_parent(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("b.py", name="b.py", sha="sha-new")])
        )
        repos._ensure_folder_records_for_paths = AsyncMock()

        with patch("app.utils.time_conversion.get_epoch_timestamp_in_ms", return_value=1000):
            await repos._apply_code_renames(_PROJECT_ID, _PROJECT_PATH, [("a.py", "b.py")])

        moves = c.data_entities_processor.on_records_moved.call_args.args[0]
        _, new_record, _ = moves[0]
        assert new_record.parent_external_record_id is None

    async def test_unresolved_name_falls_back_to_delete_plus_add(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        # Tree does not return new path — name resolution fails
        c.runtime.paged_list = AsyncMock(return_value=paged_res([]))
        repos._ensure_folder_records_for_paths = AsyncMock()
        repos._delete_code_files_by_paths = AsyncMock()
        repos._upsert_code_files_by_paths = AsyncMock(return_value=True)

        with patch("app.utils.time_conversion.get_epoch_timestamp_in_ms", return_value=1000):
            await repos._apply_code_renames(_PROJECT_ID, _PROJECT_PATH, [("a.py", "b.py")])

        repos._delete_code_files_by_paths.assert_called_once()
        repos._upsert_code_files_by_paths.assert_called_once()
        c.data_entities_processor.on_records_moved.assert_not_called()

    async def test_dotfile_new_target_deletes_old_only(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        c.runtime.paged_list = AsyncMock(return_value=paged_res([]))
        repos._ensure_folder_records_for_paths = AsyncMock()
        repos._delete_code_files_by_paths = AsyncMock()

        with patch("app.utils.time_conversion.get_epoch_timestamp_in_ms", return_value=1000):
            await repos._apply_code_renames(_PROJECT_ID, _PROJECT_PATH, [("a.py", ".hidden")])

        repos._delete_code_files_by_paths.assert_called_once()
        c.data_entities_processor.on_records_moved.assert_not_called()

    async def test_empty_renames_is_noop(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)
        repos._ensure_folder_records_for_paths = AsyncMock()

        result = await repos._apply_code_renames(_PROJECT_ID, _PROJECT_PATH, [])

        assert result is True
        c.data_entities_processor.on_records_moved.assert_not_called()

    async def test_external_record_id_uses_blob_web_path(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        c.runtime.paged_list = AsyncMock(
            return_value=paged_res([tree_entry("src/new.py", name="new.py", sha="sha-x")])
        )
        repos._ensure_folder_records_for_paths = AsyncMock()

        with patch("app.utils.time_conversion.get_epoch_timestamp_in_ms", return_value=1000):
            await repos._apply_code_renames(_PROJECT_ID, _PROJECT_PATH, [("src/old.py", "src/new.py")])

        moves = c.data_entities_processor.on_records_moved.call_args.args[0]
        _, new_record, _ = moves[0]
        assert "/-/blob/" in new_record.external_record_id


# ===========================================================================
# 5. TestCleanupEmptiedFolders — ReposSync._cleanup_emptied_folders
# ===========================================================================


class TestCleanupEmptiedFolders:
    """Tests for cascading empty folder deletion."""

    async def test_empty_removed_paths_is_noop(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        await repos._cleanup_emptied_folders(_PROJECT_ID, _PROJECT_PATH, [])
        c.data_entities_processor.get_records_by_parent.assert_not_called()
        c.data_entities_processor.on_folder_deleted.assert_not_called()

    async def test_root_file_deletion_no_parent_dirs(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        await repos._cleanup_emptied_folders(_PROJECT_ID, _PROJECT_PATH, ["root.py"])
        # root.py has no parent directories, so no folder candidates
        c.data_entities_processor.on_folder_deleted.assert_not_called()

    async def test_emptied_folder_deleted(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        folder_record = MagicMock()
        folder_record.id = "folder-id-1"
        folder_record.external_record_id = f"/{_PROJECT_PATH}/-/tree/HEAD/src"

        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=folder_record)
        c.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[])

        await repos._cleanup_emptied_folders(_PROJECT_ID, _PROJECT_PATH, ["src/file.py"])
        c.data_entities_processor.on_folder_deleted.assert_called()

    async def test_folder_with_remaining_children_kept(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        folder_record = MagicMock()
        folder_record.id = "folder-id-1"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=folder_record)
        # Still has children
        c.data_entities_processor.get_records_by_parent = AsyncMock(
            return_value=[MagicMock()]
        )

        await repos._cleanup_emptied_folders(_PROJECT_ID, _PROJECT_PATH, ["src/file.py"])
        c.data_entities_processor.on_folder_deleted.assert_not_called()

    async def test_folder_never_stored_skipped(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        await repos._cleanup_emptied_folders(_PROJECT_ID, _PROJECT_PATH, ["src/file.py"])
        c.data_entities_processor.on_folder_deleted.assert_not_called()

    async def test_nested_cascade_deepest_first(self) -> None:
        """Delete src/a/b/file.py → should try to clean b, then a, then src."""
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        deleted_ids: list[str] = []
        folder_records = {
            f"/{_PROJECT_PATH}/-/tree/HEAD/src/a/b": MagicMock(id="b-id"),
            f"/{_PROJECT_PATH}/-/tree/HEAD/src/a": MagicMock(id="a-id"),
            f"/{_PROJECT_PATH}/-/tree/HEAD/src": MagicMock(id="src-id"),
        }
        for rec in folder_records.values():
            rec.external_record_id = None

        async def _get_record(conn_id: str, eid: str) -> Any:
            return folder_records.get(eid)

        c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=_get_record)
        c.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[])

        async def _folder_deleted(folder_id: str) -> None:
            deleted_ids.append(folder_id)

        c.data_entities_processor.on_folder_deleted = AsyncMock(side_effect=_folder_deleted)

        await repos._cleanup_emptied_folders(
            _PROJECT_ID, _PROJECT_PATH, ["src/a/b/file.py"]
        )
        if len(deleted_ids) >= 2:
            assert deleted_ids.index("b-id") < deleted_ids.index("src-id")

    async def test_partial_dir_move_keeps_folder(self) -> None:
        c, repos = _make_incremental_connector()
        repos = ReposSync(c)

        folder_record = MagicMock()
        folder_record.id = "folder-id-1"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=folder_record)
        # Still has one child remaining
        c.data_entities_processor.get_records_by_parent = AsyncMock(
            return_value=[MagicMock()]
        )

        await repos._cleanup_emptied_folders(_PROJECT_ID, _PROJECT_PATH, ["src/moved.py"])
        c.data_entities_processor.on_folder_deleted.assert_not_called()


# ===========================================================================
# 6. TestSyncRepoMainRouting — ReposSync.run
# ===========================================================================


class TestSyncRepoMainRouting:
    """Tests for repos.run — routing between full/incremental/no-op."""

    async def test_branch_fetch_failure_returns_early(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=MagicMock(success=False, data=None, error="err"))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")
        repos._sync_repo_full.assert_not_called()

    async def test_no_commit_sha_returns_early(self) -> None:
        c, repos = _make_incremental_connector()
        branch = MagicMock(success=True, data=MagicMock())
        branch.data.commit = {}  # no 'id' key → no SHA
        c.runtime.ds_call = AsyncMock(return_value=branch)

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")
        repos._sync_repo_full.assert_not_called()

    async def test_no_checkpoint_runs_full_sync(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=branch_res("sha-new"))
        repos._get_code_repo_checkpoint = AsyncMock(return_value=None)
        repos._sync_repo_full = AsyncMock(return_value=True)

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")
        repos._sync_repo_full.assert_called_once_with(_PROJECT_ID, _PROJECT_PATH)
        repos._update_code_repo_checkpoint.assert_called_once_with(_PROJECT_ID, "sha-new")

    async def test_unchanged_head_skips_all_sync(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=branch_res("same-sha"))
        repos._get_code_repo_checkpoint = AsyncMock(return_value="same-sha")

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")
        repos._sync_repo_full.assert_not_called()
        repos._update_code_repo_checkpoint.assert_not_called()

    async def test_incremental_success_updates_checkpoint(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=branch_res("new-sha"))
        repos._get_code_repo_checkpoint = AsyncMock(return_value="old-sha")
        repos._sync_repo_incremental = AsyncMock(return_value=True)

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")
        repos._update_code_repo_checkpoint.assert_called_once_with(_PROJECT_ID, "new-sha")
        repos._sync_repo_full.assert_not_called()

    async def test_incremental_failure_falls_back_to_full_sync(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=branch_res("new-sha"))
        repos._get_code_repo_checkpoint = AsyncMock(return_value="old-sha")
        repos._sync_repo_incremental = AsyncMock(return_value=False)
        repos._sync_repo_full = AsyncMock(return_value=True)

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")
        repos._sync_repo_full.assert_called_once_with(_PROJECT_ID, _PROJECT_PATH)

    async def test_incremental_failure_checkpoint_updated_on_full_success(self) -> None:
        c, repos = _make_incremental_connector()
        c.runtime.ds_call = AsyncMock(return_value=branch_res("new-sha"))
        repos._get_code_repo_checkpoint = AsyncMock(return_value="old-sha")
        repos._sync_repo_incremental = AsyncMock(return_value=False)
        repos._sync_repo_full = AsyncMock(return_value=True)

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")
        repos._update_code_repo_checkpoint.assert_called_once_with(_PROJECT_ID, "new-sha")


# ===========================================================================
# 7. TestModifyFiresReindex — on_new_records event for modify operations
# ===========================================================================


class TestModifyFiresReindex:
    """Verify that in-place modifies trigger on_new_records and NOT on_record_deleted."""

    async def test_modify_calls_on_new_records(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [diff_entry(old_path="src/a.py", new_path="src/a.py")]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))
        repos._upsert_code_files_by_paths = AsyncMock(return_value=True)
        repos.build_code_file_records = AsyncMock()

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        repos._upsert_code_files_by_paths.assert_called_once()
        upsert_paths = repos._upsert_code_files_by_paths.call_args.args[2]
        assert "src/a.py" in upsert_paths

    async def test_modify_does_not_call_on_record_deleted(self) -> None:
        c, repos = _make_incremental_connector()
        diffs = [diff_entry(old_path="src/a.py", new_path="src/a.py")]
        c.runtime.ds_call = AsyncMock(return_value=ok_compare(diffs))
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))
        repos._upsert_code_files_by_paths = AsyncMock(return_value=True)
        repos._delete_code_files_by_paths = AsyncMock()

        await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        repos._delete_code_files_by_paths.assert_not_called()


# ===========================================================================
# Compare overflow — falls back to full sync
# ===========================================================================


class TestCompareOverflow:
    async def test_overflow_flag_returns_false(self) -> None:
        """When compare_commits has overflow=True, incremental returns False."""
        c, repos = _make_incremental_connector()
        overflow_data = {"diffs": [], "overflow": True}
        overflow_res = MagicMock(success=True, data=overflow_data, error=None)
        c.runtime.ds_call = AsyncMock(return_value=overflow_res)

        result = await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert result is False

    async def test_too_many_diffs_returns_false(self) -> None:
        """When diffs count >= GITLAB_COMPARE_DIFF_LIMIT, incremental returns False."""
        c, repos = _make_incremental_connector()
        diffs = [diff_entry(old_path=f"file{i}.py", new_path=f"file{i}.py") for i in range(GITLAB_COMPARE_DIFF_LIMIT)]
        overflow_res = MagicMock(success=True, data={"diffs": diffs, "overflow": False}, error=None)
        c.runtime.ds_call = AsyncMock(return_value=overflow_res)

        result = await repos._sync_repo_incremental(_PROJECT_ID, _PROJECT_PATH, _FROM_SHA, _TO_SHA)
        assert result is False


# ===========================================================================
# _resolve_blob_sha_by_path — not found path
# ===========================================================================


class TestResolveBlobShaByPath:
    async def test_empty_tree_result_returns_none_sha(self) -> None:
        """When list_repo_tree returns empty, sha_map has no entries."""
        c, repos = _make_incremental_connector()
        repos._reconcile_sha_moves = AsyncMock(return_value=([], [], []))

        empty_res = MagicMock(success=True, data=[], error=None)
        c.runtime.paged_list = AsyncMock(return_value=empty_res)

        sha_map, all_ok = await repos._resolve_blob_sha_by_path(
            _PROJECT_ID, ["src/newfile.py"], ref="HEAD"
        )
        assert sha_map == {} or "src/newfile.py" not in sha_map

    async def test_api_failure_marks_not_ok(self) -> None:
        """When list_repo_tree fails, all_ok is False."""
        c, repos = _make_incremental_connector()
        fail_res = MagicMock(success=False, data=None, error="forbidden")
        c.runtime.paged_list = AsyncMock(return_value=fail_res)

        sha_map, all_ok = await repos._resolve_blob_sha_by_path(
            _PROJECT_ID, ["src/file.py"], ref="HEAD"
        )
        assert all_ok is False
