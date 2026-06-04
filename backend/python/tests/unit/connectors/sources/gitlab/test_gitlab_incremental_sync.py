"""Comprehensive unit tests for GitLab incremental-sync functionality.

These tests cover every scenario that the incremental code-sync path handles:

* File operations: content change, rename, move, delete, add, dotfile filtering
* Directory operations: rename/move inferred from file-level diffs, cascade delete
* SHA reconciliation: identical-content move detected without renamed_file flag
* Diff-API failure and history-rewrite → full-sync fallback
* Reindex event emission:
    - content-changed rename fires updateRecord (reindex event via on_records_moved)
    - pure rename (same blob SHA) fires NO reindex event
* In-place modify fires on_new_records (newRecord event — the index/reindex path)

Each test documents the exact GitLab compare-API diff entry it simulates in a comment.

Async test execution:
    - In CI (pytest-asyncio installed, asyncio_mode=auto in pytest.ini): tests are
      discovered automatically as asyncio tests without any decorator needed.
    - Locally (anyio installed): module-level pytestmark = pytest.mark.anyio ensures
      async tests are run via the anyio pytest plugin on the asyncio backend.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.connectors.sources.gitlab.connector import (
    GITLAB_COMPARE_DIFF_LIMIT,
    GitLabConnector,
)
from app.models.entities import CodeFileRecord, RecordType

# Make async tests run in both anyio (local) and pytest-asyncio (CI) environments.
# anyio uses this marker; pytest-asyncio with asyncio_mode=auto ignores it harmlessly.
pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NS = "my-group/my-project"
_REF = "HEAD"


def _make_connector() -> GitLabConnector:
    """Build a GitLabConnector with all external dependencies mocked."""
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_records_moved = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_folder_deleted = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_records_by_parent = AsyncMock(return_value=[])

    dsp = MagicMock()
    config_service = AsyncMock()

    connector = GitLabConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=config_service,
        connector_id="gitlab-conn-1",
        scope="personal",
        created_by="test-user-1",
    )
    connector._gitlab_base_url = "https://gitlab.com"
    # data_source starts as None in __init__; all tests that call methods which
    # access self.data_source.<method> as a positional arg to _ds_call need this
    # set so attribute lookup doesn't fail before the mocked _ds_call runs.
    connector.data_source = MagicMock()
    return connector


def _diff_entry(
    *,
    old_path: str,
    new_path: str,
    deleted_file: bool = False,
    renamed_file: bool = False,
    new_file: bool = False,
) -> dict:
    """Return a dict-shaped GitLab compare diff entry (REST API shape)."""
    return {
        "old_path": old_path,
        "new_path": new_path,
        "deleted_file": deleted_file,
        "renamed_file": renamed_file,
        "new_file": new_file,
    }


class _AttrDiff:
    """Object-attribute-shaped diff entry (covers the getattr branch in _compare_diff_entry)."""

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


def _ok_compare(diffs: list) -> MagicMock:
    """Return a successful compare_commits mock response."""
    res = MagicMock()
    res.success = True
    res.data = {"diffs": diffs}
    res.error = None
    return res


def _fail_compare(error: str = "not found") -> MagicMock:
    """Return a failed compare_commits mock response."""
    res = MagicMock()
    res.success = False
    res.data = None
    res.error = error
    return res


def _tree_entry(
    path: str,
    *,
    name: str | None = None,
    sha: str = "sha-abc",
    entry_type: str = "blob",
) -> dict:
    """Return a repo-tree entry dict (list_repo_tree API shape)."""
    return {
        "path": path,
        "name": name or path.rsplit("/", 1)[-1],
        "id": sha,
        "type": entry_type,
    }


def _branch_res(sha: str = "newhead00") -> MagicMock:
    res = MagicMock()
    res.success = True
    res.error = None
    res.data = MagicMock()
    res.data.commit = {"id": sha}
    return res


# ===========================================================================
# 1. TestClassifyCompareDiffs
# ===========================================================================


class TestClassifyCompareDiffs:
    """Pure unit tests for _classify_compare_diffs.

    No mocks needed for this purely functional method — it only reads
    diff attributes and uses self._should_skip_dotfile_repo_path.
    """

    def setup_method(self) -> None:
        self.c = _make_connector()

    # --- Single-operation cases ---

    def test_plain_modify(self) -> None:
        # git diff: in-place content change (same path, no flag set)
        #   { "old_path": "src/a.py", "new_path": "src/a.py",
        #     "new_file": False, "renamed_file": False, "deleted_file": False }
        diffs = [_diff_entry(old_path="src/a.py", new_path="src/a.py")]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert deletes == []
        assert adds == []
        assert modifies == ["src/a.py"]
        assert renames == []

    def test_new_file_add(self) -> None:
        # git diff: brand-new file (new_file=True)
        #   { "old_path": "src/new.py", "new_path": "src/new.py",
        #     "new_file": True, "renamed_file": False, "deleted_file": False }
        diffs = [_diff_entry(old_path="src/new.py", new_path="src/new.py", new_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert deletes == []
        assert adds == ["src/new.py"]
        assert modifies == []
        assert renames == []

    def test_delete(self) -> None:
        # git diff: file deletion (deleted_file=True)
        #   { "old_path": "src/gone.py", "new_path": "src/gone.py",
        #     "deleted_file": True, "renamed_file": False, "new_file": False }
        diffs = [_diff_entry(old_path="src/gone.py", new_path="src/gone.py", deleted_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert deletes == ["src/gone.py"]
        assert adds == []
        assert modifies == []
        assert renames == []

    def test_rename_same_directory(self) -> None:
        # git diff: rename within the same directory (renamed_file=True)
        #   { "old_path": "src/old.py", "new_path": "src/new.py",
        #     "renamed_file": True, "deleted_file": False, "new_file": False }
        diffs = [_diff_entry(old_path="src/old.py", new_path="src/new.py", renamed_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert deletes == []
        assert adds == []
        assert modifies == []
        assert renames == [("src/old.py", "src/new.py")]

    def test_move_across_directories(self) -> None:
        # git diff: file moved between directories (renamed_file=True, paths differ)
        #   { "old_path": "lib/utils.py", "new_path": "src/utils.py",
        #     "renamed_file": True, "deleted_file": False, "new_file": False }
        diffs = [_diff_entry(old_path="lib/utils.py", new_path="src/utils.py", renamed_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert renames == [("lib/utils.py", "src/utils.py")]
        assert deletes == [] and adds == [] and modifies == []

    # --- Dotfile normalization rules ---

    def test_rename_to_dotfile_target_degrades_to_delete(self) -> None:
        # git diff: file renamed so new name starts with '.'
        #   { "old_path": "src/readme.md", "new_path": "src/.readme.md",
        #     "renamed_file": True }
        # Expected: delete of old_path only; new dotfile path is skipped.
        diffs = [_diff_entry(old_path="src/readme.md", new_path="src/.readme.md", renamed_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert deletes == ["src/readme.md"]
        assert adds == [] and modifies == [] and renames == []

    def test_rename_from_dotfile_source_degrades_to_add(self) -> None:
        # git diff: a dotfile is renamed to a normal file
        #   { "old_path": "src/.hidden.py", "new_path": "src/visible.py",
        #     "renamed_file": True }
        # Expected: add of new_path; old dotfile was never stored.
        diffs = [_diff_entry(old_path="src/.hidden.py", new_path="src/visible.py", renamed_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert adds == ["src/visible.py"]
        assert deletes == [] and modifies == [] and renames == []

    def test_plain_dotfile_add_dropped(self) -> None:
        # git diff: new_file is a dotfile — must be silently dropped
        #   { "old_path": ".env", "new_path": ".env", "new_file": True }
        diffs = [_diff_entry(old_path=".env", new_path=".env", new_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert adds == [] and deletes == [] and modifies == [] and renames == []

    def test_plain_dotfile_modify_dropped(self) -> None:
        # git diff: in-place modify of a dotfile — must be silently dropped
        #   { "old_path": ".gitignore", "new_path": ".gitignore" }
        diffs = [_diff_entry(old_path=".gitignore", new_path=".gitignore")]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert modifies == [] and adds == [] and deletes == [] and renames == []

    def test_plain_dotfile_delete_dropped(self) -> None:
        # git diff: deletion of a dotfile — must be silently dropped
        #   { "old_path": ".eslintrc", "new_path": ".eslintrc", "deleted_file": True }
        diffs = [_diff_entry(old_path=".eslintrc", new_path=".eslintrc", deleted_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert deletes == [] and adds == [] and modifies == [] and renames == []

    def test_dotfile_in_subdirectory_dropped(self) -> None:
        # git diff: dotfile inside a subfolder must also be dropped
        #   { "old_path": "src/.cache", "new_path": "src/.cache", "new_file": True }
        diffs = [_diff_entry(old_path="src/.cache", new_path="src/.cache", new_file=True)]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert adds == []

    # --- Deduplication ---

    def test_duplicate_modify_deduplicated(self) -> None:
        # Two diff entries for the same path (e.g. binary patch + text patch);
        # only the first occurrence should appear in modifies.
        diffs = [
            _diff_entry(old_path="src/a.py", new_path="src/a.py"),
            _diff_entry(old_path="src/a.py", new_path="src/a.py"),
        ]
        _, _, modifies, _ = self.c._classify_compare_diffs(diffs)
        assert modifies == ["src/a.py"]

    def test_duplicate_add_deduplicated(self) -> None:
        diffs = [
            _diff_entry(old_path="new.py", new_path="new.py", new_file=True),
            _diff_entry(old_path="new.py", new_path="new.py", new_file=True),
        ]
        _, adds, _, _ = self.c._classify_compare_diffs(diffs)
        assert adds == ["new.py"]

    def test_duplicate_delete_deduplicated(self) -> None:
        diffs = [
            _diff_entry(old_path="gone.py", new_path="gone.py", deleted_file=True),
            _diff_entry(old_path="gone.py", new_path="gone.py", deleted_file=True),
        ]
        deletes, _, _, _ = self.c._classify_compare_diffs(diffs)
        assert deletes == ["gone.py"]

    def test_duplicate_rename_deduplicated(self) -> None:
        diffs = [
            _diff_entry(old_path="a.py", new_path="b.py", renamed_file=True),
            _diff_entry(old_path="a.py", new_path="c.py", renamed_file=True),
        ]
        _, _, _, renames = self.c._classify_compare_diffs(diffs)
        # Second rename with same old_path is dropped (dedup by old_path)
        assert renames == [("a.py", "b.py")]

    # --- Mixed batch ---

    def test_mixed_batch_correct_partition(self) -> None:
        # git diff: mixed batch — delete, add, modify, rename in one compare response
        #   [ { "old_path": "removed.py", ..., "deleted_file": True },
        #     { "old_path": "added.py",   ..., "new_file": True },
        #     { "old_path": "changed.py", "new_path": "changed.py" },
        #     { "old_path": "old.py",     "new_path": "new.py", "renamed_file": True } ]
        diffs = [
            _diff_entry(old_path="removed.py", new_path="removed.py", deleted_file=True),
            _diff_entry(old_path="added.py", new_path="added.py", new_file=True),
            _diff_entry(old_path="changed.py", new_path="changed.py"),
            _diff_entry(old_path="old.py", new_path="new.py", renamed_file=True),
        ]
        deletes, adds, modifies, renames = self.c._classify_compare_diffs(diffs)
        assert deletes == ["removed.py"]
        assert adds == ["added.py"]
        assert modifies == ["changed.py"]
        assert renames == [("old.py", "new.py")]

    def test_empty_diffs_returns_empty_lists(self) -> None:
        deletes, adds, modifies, renames = self.c._classify_compare_diffs([])
        assert deletes == [] and adds == [] and modifies == [] and renames == []

    # --- Object-attribute diff entries (covers getattr branch) ---

    def test_object_attribute_diff_modify(self) -> None:
        # Same as plain modify but diff is an object, not a dict.
        # Exercises the `getattr(diff, key, None)` branch in _compare_diff_entry.
        diff_obj = _AttrDiff(old_path="src/a.py", new_path="src/a.py")
        _, _, modifies, _ = self.c._classify_compare_diffs([diff_obj])
        assert modifies == ["src/a.py"]

    def test_object_attribute_diff_rename(self) -> None:
        diff_obj = _AttrDiff(old_path="src/a.py", new_path="src/b.py", renamed_file=True)
        _, _, _, renames = self.c._classify_compare_diffs([diff_obj])
        assert renames == [("src/a.py", "src/b.py")]

    def test_object_attribute_diff_delete(self) -> None:
        diff_obj = _AttrDiff(old_path="gone.py", new_path="gone.py", deleted_file=True)
        deletes, _, _, _ = self.c._classify_compare_diffs([diff_obj])
        assert deletes == ["gone.py"]


# ===========================================================================
# 2. TestReconcileShaMoves
# ===========================================================================


class TestReconcileShaMoves:
    """Tests for _reconcile_sha_moves — promotes delete+add pairs with the same
    blob SHA to renames, catching moves that GitLab did not flag as renamed_file.

    git diff shape for an identical-content move (no renamed_file):
      [ { "old_path": "lib/util.py", "new_path": "lib/util.py", "deleted_file": True },
        { "old_path": "src/util.py", "new_path": "src/util.py", "new_file": True  } ]
    GitLab does not set renamed_file=True because the files are binary-identical
    copies (no content similarity threshold was needed). SHA reconciliation detects
    the match by comparing the stored blob SHA of the deleted record with the live
    tree SHA of the added path.
    """

    async def test_sha_match_promotes_to_rename(self) -> None:
        """delete+add pair with identical SHA is promoted to a rename."""
        c = _make_connector()

        # The deleted record has stored SHA "sha-abc"
        old_record = MagicMock()
        old_record.external_revision_id = "sha-abc"

        # The newly added path also has SHA "sha-abc" in the live tree
        c._resolve_blob_sha_by_path = AsyncMock(return_value={"src/util.py": "sha-abc"})
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=old_record)

        remaining_deletes, remaining_adds, extra_renames = await c._reconcile_sha_moves(
            project_id=10,
            project_path=_NS,
            deletes=["lib/util.py"],
            adds=["src/util.py"],
        )

        assert extra_renames == [("lib/util.py", "src/util.py")]
        assert remaining_deletes == []
        assert remaining_adds == []

    async def test_sha_mismatch_not_promoted(self) -> None:
        """delete+add pair with different SHAs is NOT promoted — independent ops."""
        c = _make_connector()

        old_record = MagicMock()
        old_record.external_revision_id = "sha-old"

        c._resolve_blob_sha_by_path = AsyncMock(return_value={"src/util.py": "sha-new"})
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=old_record)

        remaining_deletes, remaining_adds, extra_renames = await c._reconcile_sha_moves(
            project_id=10,
            project_path=_NS,
            deletes=["lib/util.py"],
            adds=["src/util.py"],
        )

        assert extra_renames == []
        assert remaining_deletes == ["lib/util.py"]
        assert remaining_adds == ["src/util.py"]

    async def test_empty_deletes_no_op(self) -> None:
        """No deletes → nothing to reconcile → adds/deletes unchanged."""
        c = _make_connector()
        c._resolve_blob_sha_by_path = AsyncMock(return_value={})

        remaining_deletes, remaining_adds, extra_renames = await c._reconcile_sha_moves(
            project_id=10,
            project_path=_NS,
            deletes=[],
            adds=["src/a.py"],
        )

        assert extra_renames == []
        assert remaining_adds == ["src/a.py"]
        assert remaining_deletes == []
        # _resolve_blob_sha_by_path must NOT be called (guard condition)
        c._resolve_blob_sha_by_path.assert_not_called()

    async def test_empty_adds_no_op(self) -> None:
        """No adds → nothing to reconcile → deletes unchanged."""
        c = _make_connector()
        c._resolve_blob_sha_by_path = AsyncMock(return_value={})

        remaining_deletes, remaining_adds, extra_renames = await c._reconcile_sha_moves(
            project_id=10,
            project_path=_NS,
            deletes=["lib/gone.py"],
            adds=[],
        )

        assert extra_renames == []
        assert remaining_deletes == ["lib/gone.py"]
        assert remaining_adds == []
        c._resolve_blob_sha_by_path.assert_not_called()

    async def test_missing_old_record_skipped(self) -> None:
        """Deleted path not found in DB → skip promotion (never stored)."""
        c = _make_connector()

        c._resolve_blob_sha_by_path = AsyncMock(return_value={"src/a.py": "sha-abc"})
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        remaining_deletes, remaining_adds, extra_renames = await c._reconcile_sha_moves(
            project_id=10,
            project_path=_NS,
            deletes=["lib/a.py"],
            adds=["src/a.py"],
        )

        assert extra_renames == []
        assert remaining_deletes == ["lib/a.py"]
        assert remaining_adds == ["src/a.py"]

    async def test_missing_stored_sha_skipped(self) -> None:
        """Old record found but has no stored SHA → skip promotion."""
        c = _make_connector()

        old_record = MagicMock()
        old_record.external_revision_id = ""

        c._resolve_blob_sha_by_path = AsyncMock(return_value={"src/a.py": "sha-abc"})
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=old_record)

        remaining_deletes, remaining_adds, extra_renames = await c._reconcile_sha_moves(
            project_id=10,
            project_path=_NS,
            deletes=["lib/a.py"],
            adds=["src/a.py"],
        )

        assert extra_renames == []

    async def test_multiple_matches_all_promoted(self) -> None:
        """Two independent delete+add pairs both with matching SHAs are promoted."""
        c = _make_connector()

        records = {
            GitLabConnector._code_blob_web_path(_NS, "lib/a.py"): MagicMock(external_revision_id="sha-1"),
            GitLabConnector._code_blob_web_path(_NS, "lib/b.py"): MagicMock(external_revision_id="sha-2"),
        }
        c.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda cid, eid: records.get(eid)
        )
        c._resolve_blob_sha_by_path = AsyncMock(
            return_value={"src/a.py": "sha-1", "src/b.py": "sha-2"}
        )

        remaining_deletes, remaining_adds, extra_renames = await c._reconcile_sha_moves(
            project_id=10,
            project_path=_NS,
            deletes=["lib/a.py", "lib/b.py"],
            adds=["src/a.py", "src/b.py"],
        )

        assert set(extra_renames) == {("lib/a.py", "src/a.py"), ("lib/b.py", "src/b.py")}
        assert remaining_deletes == []
        assert remaining_adds == []


# ===========================================================================
# 3. TestSyncRepoIncremental
# ===========================================================================


class TestSyncRepoIncremental:
    """Tests for _sync_repo_incremental orchestration.

    The method fetches a compare-API response, classifies diffs,
    reconciles SHA moves, then applies: deletes → renames → upserts,
    followed by emptied-folder cleanup.
    """

    def _make_incremental_connector(self) -> GitLabConnector:
        """Return a connector with all internal sub-operations mocked."""
        c = _make_connector()
        c._ds_call = AsyncMock(return_value=_ok_compare([]))
        c._classify_compare_diffs = MagicMock(return_value=([], [], [], []))
        c._reconcile_sha_moves = AsyncMock(return_value=([], [], []))
        c._delete_code_files_by_paths = AsyncMock()
        c._apply_code_renames = AsyncMock()
        c._upsert_code_files_by_paths = AsyncMock()
        c._cleanup_emptied_folders = AsyncMock()
        return c

    async def test_compare_failure_returns_false(self) -> None:
        """When compare_commits fails (e.g. history rewritten / force-push),
        _sync_repo_incremental must return False so _sync_repo_main falls back
        to a full sync.

        git history rewrite scenario: after a `git push --force` the from_sha
        no longer exists in history, so GitLab's diff API returns a 4xx error.
        """
        c = _make_connector()
        c._ds_call = AsyncMock(return_value=_fail_compare("The source ref does not exist"))

        result = await c._sync_repo_incremental(10, _NS, "deadbeef", "cafebabe")

        assert result is False

    async def test_compare_data_none_returns_false(self) -> None:
        """compare_commits success=True but data=None also returns False."""
        c = _make_connector()
        res = MagicMock(success=True, data=None, error=None)
        c._ds_call = AsyncMock(return_value=res)

        result = await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        assert result is False

    async def test_diff_limit_exceeded_returns_false(self) -> None:
        """When the compare API returns >= GITLAB_COMPARE_DIFF_LIMIT diffs, fall back.

        This guards against very large commits or a squash that touches thousands
        of files — incremental would be too expensive or incorrect to apply,
        so a full sync is safer.
        """
        diffs = [
            _diff_entry(old_path=f"f{i}.py", new_path=f"f{i}.py")
            for i in range(GITLAB_COMPARE_DIFF_LIMIT)
        ]
        c = _make_connector()
        c._ds_call = AsyncMock(return_value=_ok_compare(diffs))

        result = await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        assert result is False

    async def test_empty_diffs_returns_true_no_writes(self) -> None:
        """Empty diff list means nothing changed — returns True, nothing written."""
        c = self._make_incremental_connector()
        c._ds_call = AsyncMock(return_value=_ok_compare([]))

        result = await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        assert result is True
        c._delete_code_files_by_paths.assert_not_called()
        c._apply_code_renames.assert_not_called()
        c._upsert_code_files_by_paths.assert_not_called()
        c._cleanup_emptied_folders.assert_not_called()

    async def test_operation_order_deletes_before_renames_before_upserts(self) -> None:
        """Operations must be applied in order: deletes → renames → upserts → cleanup.

        Applying renames before deletes could match the stale path.
        Applying upserts before renames could create a duplicate record at the new path.
        """
        call_order: list[str] = []

        c = _make_connector()

        diffs = [
            _diff_entry(old_path="old.py", new_path="old.py", deleted_file=True),
            _diff_entry(old_path="src/a.py", new_path="src/b.py", renamed_file=True),
            _diff_entry(old_path="new.py", new_path="new.py", new_file=True),
        ]
        c._ds_call = AsyncMock(return_value=_ok_compare(diffs))
        # Let _reconcile_sha_moves be a no-op (no SHA-promoted renames)
        c._reconcile_sha_moves = AsyncMock(
            side_effect=lambda pid, pp, d, a: (d, a, [])
        )

        async def track_delete(*a, **kw):
            call_order.append("delete")

        async def track_renames(*a, **kw):
            call_order.append("renames")

        async def track_upserts(*a, **kw):
            call_order.append("upserts")

        async def track_cleanup(*a, **kw):
            call_order.append("cleanup")

        c._delete_code_files_by_paths = track_delete
        c._apply_code_renames = track_renames
        c._upsert_code_files_by_paths = track_upserts
        c._cleanup_emptied_folders = track_cleanup

        result = await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        assert result is True
        assert call_order == ["delete", "renames", "upserts", "cleanup"]

    async def test_removed_paths_passed_to_cleanup(self) -> None:
        """_cleanup_emptied_folders must receive deletes + rename old-paths.

        Both deleted files AND the old paths of renamed files vacate their
        parent directories. The cleanup must check all of them.
        """
        c = _make_connector()

        diffs = [
            _diff_entry(old_path="lib/gone.py", new_path="lib/gone.py", deleted_file=True),
            _diff_entry(old_path="lib/old.py", new_path="src/old.py", renamed_file=True),
        ]
        c._ds_call = AsyncMock(return_value=_ok_compare(diffs))
        c._reconcile_sha_moves = AsyncMock(side_effect=lambda pid, pp, d, a: (d, a, []))
        c._delete_code_files_by_paths = AsyncMock()
        c._apply_code_renames = AsyncMock()
        c._upsert_code_files_by_paths = AsyncMock()
        c._cleanup_emptied_folders = AsyncMock()

        await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        cleanup_call = c._cleanup_emptied_folders.call_args
        _, _, removed_paths = cleanup_call.args
        assert "lib/gone.py" in removed_paths
        assert "lib/old.py" in removed_paths

    async def test_sha_reconcile_extra_renames_appended(self) -> None:
        """Extra renames from _reconcile_sha_moves are merged into the rename list."""
        c = _make_connector()

        # One GitLab-flagged rename + one SHA-reconciled rename
        diffs = [
            _diff_entry(old_path="a.py", new_path="b.py", renamed_file=True),
            _diff_entry(old_path="lib/x.py", new_path="lib/x.py", deleted_file=True),
            _diff_entry(old_path="src/x.py", new_path="src/x.py", new_file=True),
        ]
        c._ds_call = AsyncMock(return_value=_ok_compare(diffs))
        # Reconcile promotes the delete+add pair to an extra rename
        c._reconcile_sha_moves = AsyncMock(
            side_effect=lambda pid, pp, d, a: ([], [], [("lib/x.py", "src/x.py")])
        )
        c._delete_code_files_by_paths = AsyncMock()
        c._apply_code_renames = AsyncMock()
        c._upsert_code_files_by_paths = AsyncMock()
        c._cleanup_emptied_folders = AsyncMock()

        await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        # Both renames (GitLab-flagged + SHA-reconciled) passed to _apply_code_renames
        rename_call_args = c._apply_code_renames.call_args.args
        _, _, renames = rename_call_args
        assert ("a.py", "b.py") in renames
        assert ("lib/x.py", "src/x.py") in renames

    async def test_adds_and_modifies_merged_for_upsert(self) -> None:
        """Adds and modifies are merged (deduplicated) and sent to _upsert_code_files_by_paths."""
        c = _make_connector()

        diffs = [
            _diff_entry(old_path="new.py", new_path="new.py", new_file=True),
            _diff_entry(old_path="changed.py", new_path="changed.py"),
        ]
        c._ds_call = AsyncMock(return_value=_ok_compare(diffs))
        c._reconcile_sha_moves = AsyncMock(side_effect=lambda pid, pp, d, a: (d, a, []))
        c._delete_code_files_by_paths = AsyncMock()
        c._apply_code_renames = AsyncMock()
        c._upsert_code_files_by_paths = AsyncMock()
        c._cleanup_emptied_folders = AsyncMock()

        await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        upsert_call = c._upsert_code_files_by_paths.call_args.args
        _, _, upsert_paths = upsert_call
        assert "new.py" in upsert_paths
        assert "changed.py" in upsert_paths

    async def test_no_cleanup_when_no_removed_paths(self) -> None:
        """If only adds/modifies occurred (no deletes or renames), cleanup is skipped."""
        c = _make_connector()

        diffs = [
            _diff_entry(old_path="new.py", new_path="new.py", new_file=True),
        ]
        c._ds_call = AsyncMock(return_value=_ok_compare(diffs))
        c._reconcile_sha_moves = AsyncMock(side_effect=lambda pid, pp, d, a: (d, a, []))
        c._delete_code_files_by_paths = AsyncMock()
        c._apply_code_renames = AsyncMock()
        c._upsert_code_files_by_paths = AsyncMock()
        c._cleanup_emptied_folders = AsyncMock()

        await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        c._cleanup_emptied_folders.assert_not_called()

    async def test_compare_uses_straight_true(self) -> None:
        """compare_commits must be called with straight=True.

        straight=True means 'show me the direct two-commit diff' rather
        than the merge-base diff. This correctly handles force-push / rebase
        scenarios where the merge-base may no longer exist.
        """
        c = _make_connector()
        c._ds_call = AsyncMock(return_value=_ok_compare([]))
        c._reconcile_sha_moves = AsyncMock(side_effect=lambda pid, pp, d, a: (d, a, []))

        await c._sync_repo_incremental(10, _NS, "sha1", "sha2")

        _, kwargs = c._ds_call.call_args
        assert kwargs.get("straight") is True


# ===========================================================================
# 4. TestApplyCodeRenames
# ===========================================================================


class TestApplyCodeRenames:
    """Tests for _apply_code_renames.

    Each rename/move is applied in-place: the existing DB record is updated
    (same id) with the new path/name/SHA, and the parent-child edge is
    re-pointed to the new folder. on_records_moved handles all of this.
    """

    def _tree_res(self, entries: list[dict]) -> MagicMock:
        res = MagicMock()
        res.success = True
        res.data = entries
        res.error = None
        return res

    async def test_pure_rename_calls_on_records_moved(self) -> None:
        """A simple file rename (same dir, different name) calls on_records_moved.

        git diff: { "old_path": "src/old.py", "new_path": "src/new.py",
                    "renamed_file": True }
        """
        c = _make_connector()
        c._ensure_folder_records_for_paths = AsyncMock()
        c._ds_call = AsyncMock(
            return_value=self._tree_res([
                _tree_entry("src/new.py", name="new.py", sha="sha-xyz"),
            ])
        )

        renames = [("src/old.py", "src/new.py")]
        await c._apply_code_renames(10, _NS, renames)

        c.data_entities_processor.on_records_moved.assert_awaited_once()
        move_args = c.data_entities_processor.on_records_moved.call_args.args[0]
        old_eid, new_record, _perms = move_args[0]

        assert old_eid == GitLabConnector._code_blob_web_path(_NS, "src/old.py")
        assert new_record.record_name == "new.py"
        assert new_record.external_revision_id == "sha-xyz"

    async def test_cross_directory_move_sets_correct_parent_external_id(self) -> None:
        """Moving a file to a different directory sets parent_external_record_id to the
        new directory's tree external_id (/-/tree/... not /-/blob/...).

        git diff: { "old_path": "lib/util.py", "new_path": "src/helpers/util.py",
                    "renamed_file": True }
        """
        c = _make_connector()
        c._ensure_folder_records_for_paths = AsyncMock()
        c._ds_call = AsyncMock(
            return_value=self._tree_res([
                _tree_entry("src/helpers/util.py", name="util.py", sha="sha-new"),
            ])
        )

        await c._apply_code_renames(10, _NS, [("lib/util.py", "src/helpers/util.py")])

        move_args = c.data_entities_processor.on_records_moved.call_args.args[0]
        _, new_record, _ = move_args[0]

        # Parent must be the tree URL (/-/tree/) not blob URL
        assert "/-/tree/" in (new_record.parent_external_record_id or "")
        assert "/-/blob/" not in (new_record.parent_external_record_id or "")

    async def test_root_level_rename_has_no_parent(self) -> None:
        """Renaming a root-level file (no directory separator) has parent=None."""
        c = _make_connector()
        c._ensure_folder_records_for_paths = AsyncMock()
        c._ds_call = AsyncMock(
            return_value=self._tree_res([
                _tree_entry("newname.py", name="newname.py", sha="sha-x"),
            ])
        )

        await c._apply_code_renames(10, _NS, [("oldname.py", "newname.py")])

        move_args = c.data_entities_processor.on_records_moved.call_args.args[0]
        _, new_record, _ = move_args[0]
        assert new_record.parent_external_record_id is None

    async def test_unresolved_name_falls_back_to_delete_plus_add(self) -> None:
        """If the new path is not found in the tree, fall back to delete+add.

        This can happen when list_repo_tree fails or the path was deleted before
        the rename was applied (e.g. concurrent force-push).
        """
        c = _make_connector()
        c._ensure_folder_records_for_paths = AsyncMock()
        c._ds_call = AsyncMock(return_value=self._tree_res([]))  # empty tree response
        c._delete_code_files_by_paths = AsyncMock()
        c._upsert_code_files_by_paths = AsyncMock()

        await c._apply_code_renames(10, _NS, [("src/old.py", "src/new.py")])

        c._delete_code_files_by_paths.assert_awaited_once_with(10, _NS, ["src/old.py"])
        c._upsert_code_files_by_paths.assert_awaited_once_with(10, _NS, ["src/new.py"])
        c.data_entities_processor.on_records_moved.assert_not_awaited()

    async def test_dotfile_new_target_deletes_old_only(self) -> None:
        """Rename target is a dotfile → only delete the old record; no add.

        This is a defensive guard (classifier should catch it, but belt-and-braces).
        git diff: { "old_path": "src/readme.md", "new_path": "src/.readme.md",
                    "renamed_file": True }
        """
        c = _make_connector()
        c._ensure_folder_records_for_paths = AsyncMock()
        c._ds_call = AsyncMock(
            return_value=self._tree_res([
                _tree_entry("src/.readme.md", name=".readme.md", sha="sha-x"),
            ])
        )
        c._delete_code_files_by_paths = AsyncMock()

        await c._apply_code_renames(10, _NS, [("src/readme.md", "src/.readme.md")])

        c._delete_code_files_by_paths.assert_awaited_once_with(10, _NS, ["src/readme.md"])
        c.data_entities_processor.on_records_moved.assert_not_awaited()

    async def test_empty_renames_is_noop(self) -> None:
        """Empty renames list → no API calls made."""
        c = _make_connector()
        c._ensure_folder_records_for_paths = AsyncMock()
        c._ds_call = AsyncMock()

        await c._apply_code_renames(10, _NS, [])

        c._ensure_folder_records_for_paths.assert_not_awaited()
        c._ds_call.assert_not_awaited()
        c.data_entities_processor.on_records_moved.assert_not_awaited()

    async def test_external_record_id_uses_blob_web_path(self) -> None:
        """The new CodeFileRecord's external_record_id must use /-/blob/ shape."""
        c = _make_connector()
        c._ensure_folder_records_for_paths = AsyncMock()
        c._ds_call = AsyncMock(
            return_value=self._tree_res([
                _tree_entry("src/new.py", name="new.py", sha="sha-z"),
            ])
        )

        await c._apply_code_renames(10, _NS, [("src/old.py", "src/new.py")])

        move_args = c.data_entities_processor.on_records_moved.call_args.args[0]
        _, new_record, _ = move_args[0]

        expected_eid = GitLabConnector._code_blob_web_path(_NS, "src/new.py")
        assert new_record.external_record_id == expected_eid
        assert "/-/blob/" in new_record.external_record_id


# ===========================================================================
# 5. TestCleanupEmptiedFolders
# ===========================================================================


class TestCleanupEmptiedFolders:
    """Tests for _cleanup_emptied_folders.

    Git emits no directory diffs — directory deletion/move is inferred from
    file-level diffs. After file ops are applied, we check each parent prefix
    of every removed path and delete folder records that have no children left.

    git diff for directory deletion scenario (all files in 'lib/' removed):
      [ { "old_path": "lib/a.py", "deleted_file": True },
        { "old_path": "lib/b.py", "deleted_file": True } ]
    After _delete_code_files_by_paths runs, _cleanup_emptied_folders checks
    'lib' and deletes it if it has no remaining children.
    """

    def _folder_record(self, folder_id: str = "folder-1") -> MagicMock:
        rec = MagicMock()
        rec.id = folder_id
        return rec

    async def test_empty_removed_paths_is_noop(self) -> None:
        """No removed paths → nothing to check, no DB calls."""
        c = _make_connector()

        await c._cleanup_emptied_folders(10, _NS, [])

        c.data_entities_processor.get_record_by_external_id.assert_not_called()
        c.data_entities_processor.on_folder_deleted.assert_not_awaited()

    async def test_root_file_deletion_no_parent_dirs(self) -> None:
        """Deleting a root-level file (no '/' in path) has no candidate directories."""
        c = _make_connector()

        await c._cleanup_emptied_folders(10, _NS, ["root.py"])

        c.data_entities_processor.on_folder_deleted.assert_not_awaited()

    async def test_emptied_folder_deleted(self) -> None:
        """After all files in a directory are removed, the folder record is deleted."""
        c = _make_connector()

        folder = self._folder_record("dir-folder-id")
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=folder)
        c.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[])

        await c._cleanup_emptied_folders(10, _NS, ["lib/a.py"])

        c.data_entities_processor.on_folder_deleted.assert_awaited_once_with("dir-folder-id")

    async def test_folder_with_remaining_children_kept(self) -> None:
        """Folder still has other files → should NOT be deleted.

        Scenario: only one of two files in 'src/' was removed.
        git diff: [ { "old_path": "src/a.py", "deleted_file": True } ]
        src/b.py still exists, so src/ is kept.
        """
        c = _make_connector()

        folder = self._folder_record("src-folder")
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=folder)
        # get_records_by_parent returns non-empty (another file still there)
        remaining_child = MagicMock()
        c.data_entities_processor.get_records_by_parent = AsyncMock(
            return_value=[remaining_child]
        )

        await c._cleanup_emptied_folders(10, _NS, ["src/a.py"])

        c.data_entities_processor.on_folder_deleted.assert_not_awaited()

    async def test_folder_never_stored_skipped(self) -> None:
        """Folder record not in DB (never stored, e.g. dotfile dir) → skipped silently."""
        c = _make_connector()
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        await c._cleanup_emptied_folders(10, _NS, ["dotdir/a.py"])

        c.data_entities_processor.on_folder_deleted.assert_not_awaited()

    async def test_nested_cascade_deepest_first(self) -> None:
        """Nested empty directories cascade: deepest deleted first so parent
        sees no children when it's evaluated.

        Scenario: 'a/b/c.py' is deleted.
        - 'a/b' becomes empty → deleted
        - then 'a' becomes empty → deleted
        Processing order must be deepest ('a/b') before shallower ('a').
        """
        c = _make_connector()

        deletion_order: list[str] = []

        def make_folder(fid: str):
            rec = MagicMock()
            rec.id = fid
            return rec

        folder_a_b = make_folder("folder-a-b")
        folder_a = make_folder("folder-a")

        web_path_a_b = GitLabConnector._code_tree_web_path(_NS, "a/b")
        web_path_a = GitLabConnector._code_tree_web_path(_NS, "a")

        async def get_record(connector_id, external_id):
            if external_id == web_path_a_b:
                return folder_a_b
            if external_id == web_path_a:
                return folder_a
            return None

        # After 'a/b' is deleted, 'a' also has no children
        async def get_children(connector_id, external_id):
            return []

        async def mock_folder_deleted(record_id):
            deletion_order.append(record_id)

        c.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=get_record
        )
        c.data_entities_processor.get_records_by_parent = AsyncMock(
            side_effect=get_children
        )
        c.data_entities_processor.on_folder_deleted = AsyncMock(
            side_effect=mock_folder_deleted
        )

        await c._cleanup_emptied_folders(10, _NS, ["a/b/c.py"])

        assert deletion_order == ["folder-a-b", "folder-a"]

    async def test_partial_dir_move_keeps_folder(self) -> None:
        """When a directory is partially moved (only some files removed),
        the folder record is preserved.

        Scenario: 'src/a.py' renamed to 'dst/a.py', but 'src/b.py' remains.
        The old_path 'src/a.py' is in removed_paths, but src/ still has children.
        """
        c = _make_connector()

        folder = self._folder_record("src-folder")
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=folder)
        c.data_entities_processor.get_records_by_parent = AsyncMock(
            return_value=[MagicMock()]  # b.py still here
        )

        await c._cleanup_emptied_folders(10, _NS, ["src/a.py"])

        c.data_entities_processor.on_folder_deleted.assert_not_awaited()


# ===========================================================================
# 6. TestSyncRepoMainRouting
# ===========================================================================


class TestSyncRepoMainRouting:
    """Tests for _sync_repo_main routing logic.

    _sync_repo_main decides between full sync, no-op, and incremental sync
    based on the stored checkpoint and the current branch HEAD SHA.
    """

    async def test_branch_fetch_failure_returns_early(self) -> None:
        """If get_branch fails, no sync should happen."""
        c = _make_connector()
        c.data_source = MagicMock()
        fail_res = MagicMock(success=False, data=None, error="403 Forbidden")
        c.data_source.get_branch = MagicMock(return_value=fail_res)
        c._sync_repo_full = AsyncMock()
        c._sync_repo_incremental = AsyncMock()

        await c._sync_repo_main(10, _NS, "main")

        c._sync_repo_full.assert_not_called()
        c._sync_repo_incremental.assert_not_called()

    async def test_no_commit_sha_returns_early(self) -> None:
        """If branch data has no commit SHA, no sync should happen."""
        c = _make_connector()
        c.data_source = MagicMock()
        branch_data = MagicMock()
        branch_data.commit = None  # triggers _branch_head_commit_sha to return None
        res = MagicMock(success=True, data=branch_data, error=None)
        c.data_source.get_branch = MagicMock(return_value=res)
        c._sync_repo_full = AsyncMock()
        c._sync_repo_incremental = AsyncMock()

        await c._sync_repo_main(10, _NS, "main")

        c._sync_repo_full.assert_not_called()
        c._sync_repo_incremental.assert_not_called()

    async def test_no_checkpoint_runs_full_sync(self) -> None:
        """First-time sync (no checkpoint stored) always runs full sync."""
        c = _make_connector()
        c.data_source = MagicMock()
        c.data_source.get_branch = MagicMock(return_value=_branch_res("newsha"))
        c._get_code_repo_checkpoint = AsyncMock(return_value=None)
        c._update_code_repo_checkpoint = AsyncMock()
        c._sync_repo_full = AsyncMock()
        c._sync_repo_incremental = AsyncMock()

        await c._sync_repo_main(10, _NS, "main")

        c._sync_repo_full.assert_awaited_once_with(10, _NS)
        c._sync_repo_incremental.assert_not_awaited()
        c._update_code_repo_checkpoint.assert_awaited_once_with(10, "newsha")

    async def test_unchanged_head_skips_all_sync(self) -> None:
        """If HEAD SHA == checkpoint SHA, nothing is synced (no-op)."""
        c = _make_connector()
        c.data_source = MagicMock()
        c.data_source.get_branch = MagicMock(return_value=_branch_res("same001"))
        c._get_code_repo_checkpoint = AsyncMock(return_value="same001")
        c._sync_repo_full = AsyncMock()
        c._sync_repo_incremental = AsyncMock()
        c._update_code_repo_checkpoint = AsyncMock()

        await c._sync_repo_main(10, _NS, "main")

        c._sync_repo_full.assert_not_called()
        c._sync_repo_incremental.assert_not_called()
        c._update_code_repo_checkpoint.assert_not_called()

    async def test_incremental_success_updates_checkpoint(self) -> None:
        """Successful incremental sync updates the checkpoint to the new SHA."""
        c = _make_connector()
        c.data_source = MagicMock()
        c.data_source.get_branch = MagicMock(return_value=_branch_res("newsha2"))
        c._get_code_repo_checkpoint = AsyncMock(return_value="oldsha2")
        c._update_code_repo_checkpoint = AsyncMock()
        c._sync_repo_full = AsyncMock()
        c._sync_repo_incremental = AsyncMock(return_value=True)

        await c._sync_repo_main(10, _NS, "main")

        c._sync_repo_incremental.assert_awaited_once_with(10, _NS, "oldsha2", "newsha2")
        c._sync_repo_full.assert_not_called()
        c._update_code_repo_checkpoint.assert_awaited_once_with(10, "newsha2")

    async def test_incremental_failure_falls_back_to_full_sync(self) -> None:
        """When incremental sync returns False (e.g. history rewritten, diff API failed),
        full sync runs as fallback and the checkpoint is still updated.

        History-rewrite scenario: a force-push replaced the branch history,
        making the stored from_sha invalid. compare_commits returns False,
        so we fall back to a full sync.
        """
        c = _make_connector()
        c.data_source = MagicMock()
        c.data_source.get_branch = MagicMock(return_value=_branch_res("newsha3"))
        c._get_code_repo_checkpoint = AsyncMock(return_value="oldsha3")
        c._update_code_repo_checkpoint = AsyncMock()
        c._sync_repo_full = AsyncMock()
        c._sync_repo_incremental = AsyncMock(return_value=False)

        await c._sync_repo_main(10, _NS, "main")

        c._sync_repo_incremental.assert_awaited_once()
        c._sync_repo_full.assert_awaited_once_with(10, _NS)
        c._update_code_repo_checkpoint.assert_awaited_once_with(10, "newsha3")

    async def test_incremental_failure_checkpoint_still_updated(self) -> None:
        """Even when incremental fails and falls back to full sync, the checkpoint
        must be updated to the current SHA so the next run doesn't re-compare
        the same (now-invalid) range again."""
        c = _make_connector()
        c.data_source = MagicMock()
        c.data_source.get_branch = MagicMock(return_value=_branch_res("force-push-sha"))
        c._get_code_repo_checkpoint = AsyncMock(return_value="pre-force-push")
        c._update_code_repo_checkpoint = AsyncMock()
        c._sync_repo_full = AsyncMock()
        c._sync_repo_incremental = AsyncMock(return_value=False)

        await c._sync_repo_main(10, _NS, "main")

        c._update_code_repo_checkpoint.assert_awaited_once_with(10, "force-push-sha")


# ===========================================================================
# 7. TestModifyFiresReindex
# ===========================================================================


class TestModifyFiresReindex:
    """Tests that an in-place file content change (modify diff) drives the
    on_new_records call, which emits a 'newRecord' Kafka event for re-indexing.

    In-place modify flow:
      _sync_repo_incremental
        → _upsert_code_files_by_paths
          → build_code_file_records (builds CodeFileRecord list)
            → _process_new_records
              → data_entities_processor.on_new_records (emits newRecord event)

    Note: the event type is 'newRecord', NOT 'updateRecord', because the
    connector upserts code files via the same path as new records.
    _process_new_records / on_new_records handles the upsert logic, emitting
    a 'newRecord' event regardless of whether the record already exists.
    A 'rename with content change' emits 'updateRecord' via on_records_moved.
    """

    async def test_modify_calls_on_new_records(self) -> None:
        """A modify diff drives _upsert_code_files_by_paths, which calls
        build_code_file_records then _process_new_records, which calls
        data_entities_processor.on_new_records.

        git diff: { "old_path": "src/main.py", "new_path": "src/main.py",
                    "new_file": False, "renamed_file": False, "deleted_file": False }
        """
        c = _make_connector()

        modify_diff = _diff_entry(old_path="src/main.py", new_path="src/main.py")
        c._ds_call = AsyncMock(side_effect=[
            # compare_commits
            _ok_compare([modify_diff]),
            # list_repo_tree for parent dir "src"
            MagicMock(
                success=True,
                data=[_tree_entry("src/main.py", name="main.py", sha="new-content-sha")],
                error=None,
            ),
        ])
        c._reconcile_sha_moves = AsyncMock(side_effect=lambda pid, pp, d, a: (d, a, []))
        c._ensure_folder_records_for_paths = AsyncMock()
        c._delete_code_files_by_paths = AsyncMock()
        c._apply_code_renames = AsyncMock()
        c._cleanup_emptied_folders = AsyncMock()

        await c._sync_repo_incremental(10, _NS, "oldsha", "newsha")

        # The key assertion: on_new_records was called (the re-index event path)
        c.data_entities_processor.on_new_records.assert_awaited()

    async def test_modify_does_not_call_on_record_deleted(self) -> None:
        """A content modify must NOT trigger deletion of the record."""
        c = _make_connector()

        modify_diff = _diff_entry(old_path="README.md", new_path="README.md")
        c._ds_call = AsyncMock(side_effect=[
            _ok_compare([modify_diff]),
            MagicMock(
                success=True,
                data=[_tree_entry("README.md", name="README.md", sha="sha-upd")],
                error=None,
            ),
        ])
        c._reconcile_sha_moves = AsyncMock(side_effect=lambda pid, pp, d, a: (d, a, []))
        c._ensure_folder_records_for_paths = AsyncMock()
        c._delete_code_files_by_paths = AsyncMock()
        c._apply_code_renames = AsyncMock()
        c._cleanup_emptied_folders = AsyncMock()

        await c._sync_repo_incremental(10, _NS, "oldsha", "newsha")

        c._delete_code_files_by_paths.assert_not_awaited()
        c.data_entities_processor.on_record_deleted.assert_not_awaited()
