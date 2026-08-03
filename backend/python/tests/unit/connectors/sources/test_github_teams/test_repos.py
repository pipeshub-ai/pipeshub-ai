"""Unit tests for github_teams ReposSync (code file sync).

Covers:
- External id stability: blob/tree external ids are anchored on repo.id, not path.
- Full sync: flat recursive Git Tree -> folder + code file records.
- Full sync fallback: truncated tree -> per-subtree walk (BFS).
- Incremental sync: compare-commits classification (added/removed/modified/renamed).
- SHA reconciliation: delete+add pair sharing a blob SHA promoted to a rename.
- Dotfile skipping, oversized blob skipping.
- run(): checkpoint dispatch (no checkpoint -> full; unchanged HEAD -> skip;
  default-branch change -> re-baseline; incremental failure -> full fallback).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.constants import CODE_FILE_MAX_SIZE_BYTES
from app.connectors.sources.github_teams.repos import ReposSync, _blob_external_id, _is_dotfile_path, _tree_external_id
from app.models.entities import CodeFileRecord

from .conftest import (
    make_comparison,
    make_compare_file,
    make_git_tree,
    make_mock_connector,
    make_repo,
    make_tree_element,
    ok_response,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class TestExternalIdHelpers:
    def test_blob_external_id_anchored_on_repo_id(self) -> None:
        assert _blob_external_id(555, "src/main.py") == "/555/blob/src/main.py"

    def test_blob_external_id_ignores_owner_and_name(self) -> None:
        """The id derives only from repo.id and path, so renames cannot change it."""
        renamed_before = make_repo(repo_id=555, owner_login="acme", name="widgets")
        renamed_after = make_repo(repo_id=555, owner_login="acme-new", name="gadgets")
        assert _blob_external_id(renamed_before.id, "src/main.py") == _blob_external_id(
            renamed_after.id, "src/main.py"
        )

    def test_tree_external_id_anchored_on_repo_id(self) -> None:
        assert _tree_external_id(555, "src") == "/555/tree/src"

    def test_dotfile_detection(self) -> None:
        """Only the final path component is checked — a dot-prefixed *ancestor*
        directory (e.g. ``.github/``) does not make its children dotfiles, since
        directories like ``.github/workflows/`` typically hold content users do
        want indexed (CI configs, etc.)."""
        assert _is_dotfile_path(".env") is True
        assert _is_dotfile_path("src/.env") is True
        assert _is_dotfile_path(".github/workflows/ci.yml") is False
        assert _is_dotfile_path("src/main.py") is False


class TestFullSync:
    async def test_flat_tree_persists_folders_before_files(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1, name="widgets")
        tree = make_git_tree([
            make_tree_element("src", entry_type="tree", sha="sha-src"),
            make_tree_element("src/main.py", entry_type="blob", sha="sha-main", size=100),
            make_tree_element("README.md", entry_type="blob", sha="sha-readme", size=50),
        ])
        c.runtime.ds_call.return_value = ok_response(tree)

        sync = ReposSync(c)
        ok = await sync._full_sync(repo, "head-sha")

        assert ok is True
        # First call persists the one folder record; second call persists blobs.
        calls = c.data_entities_processor.on_new_records.call_args_list
        assert len(calls) == 2
        folder_batch = calls[0].args[0]
        assert len(folder_batch) == 1
        assert folder_batch[0][0].record_name == "src"
        file_batch = calls[1].args[0]
        assert {r.record_name for r, _ in file_batch} == {"main.py", "README.md"}

    async def test_truncated_tree_falls_back_to_subtree_walk(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        root_tree_recursive = make_git_tree(
            [make_tree_element("src", entry_type="tree", sha="sha-src")], truncated=True,
        )
        # Non-recursive listing at the root: one subdirectory entry.
        root_tree_nonrecursive = make_git_tree(
            [make_tree_element("src", entry_type="tree", sha="sha-src")],
        )
        # Non-recursive listing inside "src": one file.
        subtree = make_git_tree(
            [make_tree_element("main.py", entry_type="blob", sha="sha-main", size=10)],
        )

        call_log: list[tuple] = []

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            call_log.append(args)
            _owner, _name, sha, recursive = args
            if recursive:
                return ok_response(root_tree_recursive)
            if sha == "head-sha":
                return ok_response(root_tree_nonrecursive)
            if sha == "sha-src":
                return ok_response(subtree)
            raise AssertionError(f"unexpected get_git_tree call: sha={sha} recursive={recursive}")

        c.runtime.ds_call.side_effect = dispatch

        sync = ReposSync(c)
        ok = await sync._full_sync(repo, "head-sha")

        assert ok is True
        # BFS should have descended into "src" (sha-src) after the root listing.
        assert any(args[2] == "sha-src" for args in call_log[1:])
        file_batch = c.data_entities_processor.on_new_records.call_args_list[-1].args[0]
        assert {r.record_name for r, _ in file_batch} == {"main.py"}

    async def test_dotfile_and_oversized_blob_skipped(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        tree = make_git_tree([
            make_tree_element(".env", entry_type="blob", sha="sha-env", size=10),
            make_tree_element("big.bin", entry_type="blob", sha="sha-big", size=999_999_999),
            make_tree_element("ok.py", entry_type="blob", sha="sha-ok", size=10),
        ])
        c.runtime.ds_call.return_value = ok_response(tree)

        sync = ReposSync(c)
        await sync._full_sync(repo, "head-sha")

        file_batch = c.data_entities_processor.on_new_records.call_args_list[-1].args[0]
        names = {r.record_name for r, _ in file_batch}
        assert names == {"ok.py"}


class TestIncrementalSyncClassification:
    def test_classify_added_removed_modified_renamed(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        files = [
            make_compare_file(filename="new.py", status="added", sha="sha-new"),
            make_compare_file(filename="old.py", status="removed", sha=""),
            make_compare_file(filename="changed.py", status="modified", sha="sha-changed"),
            make_compare_file(filename="new_name.py", status="renamed", previous_filename="old_name.py", sha="sha-renamed"),
        ]
        deletes, adds, modifies, renames = sync._classify_compare_files(files)
        assert adds == {"new.py": "sha-new"}
        assert deletes == {"old.py": ""}
        assert modifies == {"changed.py": "sha-changed"}
        assert renames == [("old_name.py", "new_name.py", "sha-renamed")]

    def test_dotfiles_excluded_from_all_buckets(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        files = [make_compare_file(filename=".env", status="added", sha="sha-env")]
        deletes, adds, modifies, renames = sync._classify_compare_files(files)
        assert not deletes and not adds and not modifies and not renames

    def test_rename_into_dotfile_becomes_delete(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        files = [make_compare_file(filename=".env", status="renamed", previous_filename="env.py", sha="sha-x")]
        deletes, adds, modifies, renames = sync._classify_compare_files(files)
        assert deletes == {"env.py": ""}
        assert not renames


class TestShaReconciliation:
    async def test_delete_add_pair_with_matching_sha_promoted_to_rename(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        repo = make_repo(repo_id=1)
        existing_record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="old.py",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/old.py", external_revision_id="shared-sha",
            file_path="old.py", file_hash="shared-sha",
        )
        c.data_entities_processor.get_record_by_external_id.return_value = existing_record

        deletes = {"old.py": ""}
        adds = {"new.py": "shared-sha"}
        remaining_deletes, remaining_adds, extra_renames = await sync._reconcile_sha_moves(repo, deletes, adds)

        assert remaining_deletes == {}
        assert remaining_adds == {}
        assert extra_renames == [("old.py", "new.py", "shared-sha")]

    async def test_no_match_leaves_delete_and_add_untouched(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        repo = make_repo(repo_id=1)
        c.data_entities_processor.get_record_by_external_id.return_value = None

        deletes = {"old.py": ""}
        adds = {"new.py": "different-sha"}
        remaining_deletes, remaining_adds, extra_renames = await sync._reconcile_sha_moves(repo, deletes, adds)

        assert remaining_deletes == {"old.py": ""}
        assert remaining_adds == {"new.py": "different-sha"}
        assert extra_renames == []


class TestIncrementalSyncEndToEnd:
    async def test_rename_via_compare_status_calls_on_records_moved(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        comparison = make_comparison([
            make_compare_file(filename="new_name.py", status="renamed", previous_filename="old_name.py", sha="sha-renamed"),
        ])
        c.runtime.ds_call.return_value = ok_response(comparison)
        c.data_entities_processor.get_record_by_external_id.return_value = None

        sync = ReposSync(c)
        ok = await sync._incremental_sync(repo, "old-sha", "new-sha")

        assert ok is True
        c.data_entities_processor.on_records_moved.assert_awaited_once()
        moves = c.data_entities_processor.on_records_moved.call_args.args[0]
        assert len(moves) == 1
        old_external_id, new_record, _perms = moves[0]
        assert old_external_id == "/1/blob/old_name.py"
        assert new_record.external_record_id == "/1/blob/new_name.py"
        assert new_record.external_revision_id == "sha-renamed"

    async def test_overflow_files_limit_triggers_fallback(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        from app.connectors.sources.github_teams.constants import COMPARE_COMMITS_FILES_LIMIT
        many_files = [
            make_compare_file(filename=f"f{i}.py", status="modified", sha=f"sha-{i}")
            for i in range(COMPARE_COMMITS_FILES_LIMIT)
        ]
        comparison = make_comparison(many_files)
        c.runtime.ds_call.return_value = ok_response(comparison)

        sync = ReposSync(c)
        ok = await sync._incremental_sync(repo, "old-sha", "new-sha")

        assert ok is False

    async def test_diverged_status_triggers_fallback(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        comparison = make_comparison([], status="diverged")
        c.runtime.ds_call.return_value = ok_response(comparison)

        sync = ReposSync(c)
        ok = await sync._incremental_sync(repo, "old-sha", "new-sha")

        assert ok is False


class TestRunDispatch:
    async def test_no_checkpoint_runs_full_sync(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.return_value = ok_response(
            type("Branch", (), {"commit": type("Commit", (), {"sha": "head-sha"})()})()
        )
        c.record_sync_point.read_sync_point.return_value = None

        sync = ReposSync(c)
        sync._full_sync = _async_return(True)
        sync._incremental_sync = _async_return(True)

        await sync.run(repo)

        sync._full_sync.assert_awaited_once()
        sync._incremental_sync.assert_not_awaited()
        c.record_sync_point.update_sync_point.assert_awaited_once()

    async def test_unchanged_head_skips_sync(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.return_value = ok_response(
            type("Branch", (), {"commit": type("Commit", (), {"sha": "same-sha"})()})()
        )
        c.record_sync_point.read_sync_point.return_value = {
            "last_commit_sha": "same-sha", "default_branch": "main",
        }

        sync = ReposSync(c)
        sync._full_sync = _async_return(True)
        sync._incremental_sync = _async_return(True)

        await sync.run(repo)

        sync._full_sync.assert_not_awaited()
        sync._incremental_sync.assert_not_awaited()
        c.record_sync_point.update_sync_point.assert_not_awaited()

    async def test_default_branch_change_forces_full_resync(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1, default_branch="main")
        c.runtime.ds_call.return_value = ok_response(
            type("Branch", (), {"commit": type("Commit", (), {"sha": "head-sha"})()})()
        )
        c.record_sync_point.read_sync_point.return_value = {
            "last_commit_sha": "old-sha", "default_branch": "old-default-branch",
        }

        sync = ReposSync(c)
        sync._full_sync = _async_return(True)
        sync._incremental_sync = _async_return(True)

        await sync.run(repo)

        sync._full_sync.assert_awaited_once()
        sync._incremental_sync.assert_not_awaited()

    async def test_incremental_failure_falls_back_to_full_sync(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1, default_branch="main")
        c.runtime.ds_call.return_value = ok_response(
            type("Branch", (), {"commit": type("Commit", (), {"sha": "head-sha"})()})()
        )
        c.record_sync_point.read_sync_point.return_value = {
            "last_commit_sha": "old-sha", "default_branch": "main",
        }

        sync = ReposSync(c)
        sync._full_sync = _async_return(True)
        sync._incremental_sync = _async_return(False)

        await sync.run(repo)

        sync._incremental_sync.assert_awaited_once()
        sync._full_sync.assert_awaited_once()
        c.record_sync_point.update_sync_point.assert_awaited_once()


class TestPersistenceFailurePropagation:
    """Regression tests: a silent `on_new_records`/`on_records_moved` failure
    must surface as `all_ok=False` so `run()` withholds the checkpoint advance
    instead of losing the batch forever (the incremental diff is computed
    against `last_sha`, so an advanced checkpoint would never re-attempt it)."""

    async def test_process_records_returns_false_on_persist_failure(self) -> None:
        c = make_mock_connector()
        c.data_entities_processor.on_new_records = AsyncMock(side_effect=RuntimeError("db down"))
        sync = ReposSync(c)
        record_update = sync._build_folder_record(make_repo(repo_id=1), "src", sha=None)

        ok = await sync._process_records([record_update])

        assert ok is False

    async def test_upsert_code_files_failure_propagates_to_incremental_sync(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        comparison = make_comparison([
            make_compare_file(filename="new.py", status="added", sha="sha-new"),
        ])
        c.runtime.ds_call.return_value = ok_response(comparison)
        c.data_entities_processor.get_record_by_external_id.return_value = None
        c.data_entities_processor.on_new_records = AsyncMock(side_effect=RuntimeError("db down"))

        sync = ReposSync(c)
        ok = await sync._incremental_sync(repo, "old-sha", "new-sha")

        assert ok is False

    async def test_apply_code_renames_failure_returns_false(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.data_entities_processor.get_record_by_external_id.return_value = None
        c.data_entities_processor.on_records_moved = AsyncMock(side_effect=RuntimeError("db down"))

        sync = ReposSync(c)
        ok = await sync._apply_code_renames(repo, [("old.py", "new.py", "sha-x")])

        assert ok is False


class TestFetchCodeFileContentSizeGuard:
    """Regression tests: incrementally-added/modified files skip the full-sync
    size guard (Compare Commits entries carry no blob size), so oversized
    content must be rejected at stream time instead."""

    async def test_oversized_incremental_file_rejected_at_stream_time(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="big.bin",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/big.bin", external_record_group_id="1-code-repository",
            file_path="big.bin",
        )
        oversized_content = SimpleNamespace(size=CODE_FILE_MAX_SIZE_BYTES + 1, decoded_content=b"x" * 10)

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.get_repo_by_id:
                return ok_response(repo)
            if method is c.data_source.get_file_contents:
                return ok_response(oversized_content)
            raise AssertionError(f"unexpected ds_call for {method!r}")

        c.runtime.ds_call.side_effect = dispatch

        sync = ReposSync(c)
        with pytest.raises(Exception, match="oversized"):
            await sync.fetch_code_file_content(record)

    async def test_normal_sized_file_streams_successfully(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="ok.py",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/ok.py", external_record_group_id="1-code-repository",
            file_path="ok.py",
        )
        content = SimpleNamespace(size=10, decoded_content=b"print(1)")

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.get_repo_by_id:
                return ok_response(repo)
            if method is c.data_source.get_file_contents:
                return ok_response(content)
            raise AssertionError(f"unexpected ds_call for {method!r}")

        c.runtime.ds_call.side_effect = dispatch

        sync = ReposSync(c)
        result = await sync.fetch_code_file_content(record)

        assert result == b"print(1)"


def _async_return(value: object) -> object:
    from unittest.mock import AsyncMock
    return AsyncMock(return_value=value)
