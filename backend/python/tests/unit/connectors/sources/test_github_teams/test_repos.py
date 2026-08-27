"""Unit tests for github_teams ReposSync (code file sync).

Covers:
- External id stability: blob/tree external ids are anchored on repo.id, not path.
- Full sync: flat recursive Git Tree -> folder + code file records.
- Full sync fallback: truncated tree -> per-subtree walk (BFS).
- Incremental sync: compare-commits classification (added/removed/modified/renamed).
- SHA reconciliation: delete+add pair sharing a blob SHA promoted to a rename.
- No-skip policy: every file gets a record; oversized files keep theirs with
  content indexing off (AUTO_INDEX_OFF at full sync, 413 at stream time).
- run(): checkpoint dispatch (no checkpoint -> full; unchanged HEAD -> skip;
  default-branch change -> re-baseline; incremental failure -> full fallback).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode

from app.connectors.sources.github_teams.constants import CODE_FILE_MAX_SIZE_BYTES
from app.connectors.sources.github_teams.models import (
    blob_external_id as _blob_external_id,
    tree_external_id as _tree_external_id,
)
from app.connectors.sources.github_teams.repos import ReposSync
from app.connectors.sources.github_teams.timestamps import (
    aggregate_folder_timestamps as _aggregate_folder_timestamps,
)
from app.config.constants.arangodb import ProgressStatus
from app.models.entities import CodeFileRecord

from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
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


class TestFolderTimestampAggregation:
    """Git has no folder dates — a folder's timestamps derive from its files:
    created = earliest child created, updated = latest child updated, applied
    to every ancestor of every file path."""

    def test_min_created_max_updated_across_ancestors(self) -> None:
        result = _aggregate_folder_timestamps({
            "src/app/main.py": (100, 500),
            "src/app/util.py": (200, 900),
            "src/README.md": (50, 60),
        })
        assert result["src/app"] == (100, 900)
        assert result["src"] == (50, 900)

    def test_root_level_files_produce_no_folders(self) -> None:
        assert _aggregate_folder_timestamps({"README.md": (1, 2)}) == {}

    def test_files_without_timestamps_contribute_nothing(self) -> None:
        result = _aggregate_folder_timestamps({
            "src/a.py": (None, None),
            "src/b.py": (300, 400),
        })
        assert result["src"] == (300, 400)

    def test_partial_timestamps_still_aggregate(self) -> None:
        result = _aggregate_folder_timestamps({
            "src/a.py": (None, 700),
            "src/b.py": (300, None),
        })
        assert result["src"] == (300, 700)


def _code_node(repo_id: int, path: str) -> dict:
    return {
        "id": f"node-blob-{path}",
        "externalRecordId": f"/{repo_id}/blob/{path}",
        "sourceCreatedAtTimestamp": None, "sourceLastModifiedTimestamp": None,
    }


def _folder_node(repo_id: int, path: str) -> dict:
    return {
        "id": f"node-tree-{path}",
        "externalRecordId": f"/{repo_id}/tree/{path}",
        "sourceCreatedAtTimestamp": None, "sourceLastModifiedTimestamp": None,
    }


def _patch_calls(c: object, collection: str) -> list[list[dict]]:
    """All batch_update_nodes patch lists written to *collection*."""
    return [
        call.args[0]
        for call in c.tx_store.batch_update_nodes.await_args_list
        if call.args[1] == collection
    ]


class TestTimestampBackfill:
    """The graph node carries only base-record fields — rebuilding a record
    from it via from_arango_record({}, node) made the file pass a silent
    no-op (file_path None) and corrupted folders (is_file defaulted True on
    re-upsert). The backfill therefore never builds records: it patches only
    the named properties via batch_update_nodes."""

    async def test_file_pass_recovers_paths_and_patches_dates(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)

        c.tx_store.get_nodes_by_filters = AsyncMock(
            side_effect=[[_code_node(1, "src/main.py")], [], []],
        )
        sync.timestamps.fetch_commit_dates = AsyncMock(return_value={"src/main.py": (100, 200)})

        await sync.timestamps.backfill_repo(repo)

        sync.timestamps.fetch_commit_dates.assert_awaited_once_with(
            repo.owner.login, repo.name, ["src/main.py"],
        )
        [patches] = _patch_calls(c, "records")
        [patch] = patches
        assert patch["id"] == "node-blob-src/main.py"
        assert patch["sourceCreatedAtTimestamp"] == 100
        assert patch["sourceLastModifiedTimestamp"] == 200

    async def test_file_pass_omits_absent_dates_from_patch(self) -> None:
        """A None value in a partial patch would DELETE the stored property
        (Neo4j SET n += null), so absent dates must be omitted, never sent."""
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)

        c.tx_store.get_nodes_by_filters = AsyncMock(
            side_effect=[[_code_node(1, "src/main.py")], [], []],
        )
        sync.timestamps.fetch_commit_dates = AsyncMock(return_value={"src/main.py": (None, 200)})

        await sync.timestamps.backfill_repo(repo)

        [patches] = _patch_calls(c, "records")
        [patch] = patches
        assert "sourceCreatedAtTimestamp" not in patch
        assert patch["sourceLastModifiedTimestamp"] == 200

    async def test_folder_pass_patches_dates_and_repairs_is_file(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)

        file_node = _code_node(1, "src/main.py")
        file_node["sourceCreatedAtTimestamp"] = 100
        file_node["sourceLastModifiedTimestamp"] = 200
        c.tx_store.get_nodes_by_filters = AsyncMock(
            side_effect=[[file_node], [_folder_node(1, "src")]],
        )

        await sync.timestamps._backfill_folder_timestamps(repo, "1-code-repository")

        [date_patches] = _patch_calls(c, "records")
        [date_patch] = date_patches
        assert date_patch["id"] == "node-tree-src"
        assert date_patch["sourceCreatedAtTimestamp"] == 100
        assert date_patch["sourceLastModifiedTimestamp"] == 200
        [repair_patches] = _patch_calls(c, "files")
        assert repair_patches == [{"id": "node-tree-src", "isFile": False}]

    async def test_folder_pass_skips_up_to_date_folder_but_still_repairs(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)

        file_node = _code_node(1, "src/main.py")
        file_node["sourceCreatedAtTimestamp"] = 100
        file_node["sourceLastModifiedTimestamp"] = 200
        folder_node = _folder_node(1, "src")
        folder_node["sourceCreatedAtTimestamp"] = 100
        folder_node["sourceLastModifiedTimestamp"] = 200
        c.tx_store.get_nodes_by_filters = AsyncMock(
            side_effect=[[file_node], [folder_node]],
        )

        await sync.timestamps._backfill_folder_timestamps(repo, "1-code-repository")

        assert _patch_calls(c, "records") == []
        [repair_patches] = _patch_calls(c, "files")
        assert repair_patches == [{"id": "node-tree-src", "isFile": False}]


def _graphql_response(target: dict) -> object:
    return ok_response({"repository": {"defaultBranchRef": {"target": target}}})


class TestFetchCommitDates:
    """Commit dates come from batched GraphQL (2 queries per 100 paths against
    GraphQL's own budget) instead of 2 REST calls per path — the REST method
    survives only as a per-chunk fallback."""

    async def test_single_commit_path_needs_one_query(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        c.runtime.ds_call = AsyncMock(return_value=_graphql_response({
            "p0": {
                "totalCount": 1,
                "nodes": [{"authoredDate": "2024-01-05T00:00:00Z"}],
                "pageInfo": {"endCursor": "abc 0"},
            },
        }))

        dates = await sync.timestamps.fetch_commit_dates("acme", "widgets", ["src/a.py"])

        assert dates == {"src/a.py": (1704412800000, 1704412800000)}
        assert c.runtime.ds_call.await_count == 1

    async def test_multi_commit_path_jumps_to_oldest_via_cursor(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        c.runtime.ds_call = AsyncMock(side_effect=[
            _graphql_response({
                "p0": {
                    "totalCount": 3,
                    "nodes": [{"authoredDate": "2024-01-05T00:00:00Z"}],
                    "pageInfo": {"endCursor": "abc 0"},
                },
            }),
            _graphql_response({
                "p0": {"nodes": [{"authoredDate": "2023-01-01T00:00:00Z"}]},
            }),
        ])

        dates = await sync.timestamps.fetch_commit_dates("acme", "widgets", ["src/a.py"])

        assert dates == {"src/a.py": (1672531200000, 1704412800000)}
        # The oldest commit is reached with a constructed cursor, not a walk:
        # "<anchor-oid> <totalCount - 2>" — offset 1 of 3 commits.
        second_query = c.runtime.ds_call.await_args_list[1].args[1]
        assert 'after: "abc 1"' in second_query

    async def test_graphql_failure_falls_back_to_rest(self) -> None:
        c = make_mock_connector()
        sync = ReposSync(c)
        commit_date = SimpleNamespace(
            commit=SimpleNamespace(author=SimpleNamespace(
                date=datetime(2024, 1, 5, tzinfo=timezone.utc),
            )),
        )

        async def fake_ds_call(method, *args, **kwargs) -> object:
            if method is c.data_source.graphql_query:
                return failed_response("bad gateway", status_code=502)
            assert method is c.data_source.list_commits_first_and_last
            return ok_response((commit_date, commit_date))

        c.runtime.ds_call = AsyncMock(side_effect=fake_ds_call)

        dates = await sync.timestamps.fetch_commit_dates("acme", "widgets", ["src/a.py"])

        assert dates == {"src/a.py": (1704412800000, 1704412800000)}


class TestIncrementalTimestampStamping:
    async def test_upserted_files_carry_exact_commit_dates(self) -> None:
        """Incremental changes stamp real dates at sync time — the processor
        carries stored dates forward on None, and the backfill only visits
        undated records, so a modified file's fresh date must come from here."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)
        sync.timestamps.fetch_commit_dates = AsyncMock(
            return_value={"src/a.py": (111, 222), "src/b.py": (None, None)},
        )

        ok = await sync._upsert_code_files(repo, {"src/a.py": "sha-a", "src/b.py": "sha-b"})

        assert ok is True
        persisted = {
            r.file_path: r
            for call in c.data_entities_processor.on_new_records.call_args_list
            for r, _perms in call.args[0]
            if getattr(r, "file_path", None)
        }
        assert persisted["src/a.py"].source_created_at == 111
        assert persisted["src/a.py"].source_updated_at == 222
        assert persisted["src/b.py"].source_created_at is None


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

    async def test_folders_follow_the_code_file_indexing_flag(self) -> None:
        """Folders hold no content — the indexing consumer discards them on
        mime_type. Leaving them at the default status while their files were
        stamped AUTO_INDEX_OFF published one event per folder purely for the
        consumer to throw away."""
        c = make_mock_connector()
        c.indexing_filters = SimpleNamespace(is_enabled=lambda _key: False)
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.return_value = ok_response(make_git_tree([
            make_tree_element("src", entry_type="tree", sha="sha-src"),
            make_tree_element("src/main.py", entry_type="blob", sha="sha-main", size=10),
        ]))

        sync = ReposSync(c)
        assert await sync._full_sync(repo, "head-sha") is True

        persisted = [
            record
            for call in c.data_entities_processor.on_new_records.call_args_list
            for record, _perms in call.args[0]
        ]
        assert {r.record_name for r in persisted} == {"src", "main.py"}
        assert all(r.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value for r in persisted)

    async def test_folders_stay_indexable_when_code_files_are(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.return_value = ok_response(make_git_tree([
            make_tree_element("src", entry_type="tree", sha="sha-src"),
        ]))

        sync = ReposSync(c)
        assert await sync._full_sync(repo, "head-sha") is True

        folder, _perms = c.data_entities_processor.on_new_records.call_args_list[0].args[0][0]
        assert folder.indexing_status != ProgressStatus.AUTO_INDEX_OFF.value

    async def test_truncated_root_walks_each_subtree_in_one_recursive_call(self) -> None:
        """The point of the fallback: a subtree that fits comes back whole in ONE
        recursive call. Walking it non-recursively would cost a call per
        directory, which is what made this path unusable on large repos."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        root_recursive = make_git_tree(
            [make_tree_element("src", entry_type="tree", sha="sha-src")], truncated=True,
        )
        root_flat = make_git_tree([
            make_tree_element("src", entry_type="tree", sha="sha-src"),
            make_tree_element("docs", entry_type="tree", sha="sha-docs"),
        ])
        # Recursive fetches that fit: each carries its whole subtree, and paths
        # are relative to the subtree root.
        src_subtree = make_git_tree([
            make_tree_element("lib", entry_type="tree", sha="sha-lib"),
            make_tree_element("lib/util.py", entry_type="blob", sha="sha-util", size=10),
        ])
        docs_subtree = make_git_tree(
            [make_tree_element("guide.md", entry_type="blob", sha="sha-guide", size=10)],
        )

        calls: list[tuple[str, bool]] = []

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            _owner, _name, sha, recursive = args
            calls.append((sha, recursive))
            if sha == "head-sha":
                return ok_response(root_recursive if recursive else root_flat)
            if sha == "sha-src" and recursive:
                return ok_response(src_subtree)
            if sha == "sha-docs" and recursive:
                return ok_response(docs_subtree)
            raise AssertionError(f"unexpected get_git_tree call: sha={sha} recursive={recursive}")

        c.runtime.ds_call.side_effect = dispatch
        assert await ReposSync(c)._full_sync(repo, "head-sha") is True

        # 1 truncated probe + 1 flat root listing + 1 recursive call per subtree.
        assert calls == [
            ("head-sha", True), ("head-sha", False),
            ("sha-src", True), ("sha-docs", True),
        ]
        persisted = {
            r.record_name
            for call in c.data_entities_processor.on_new_records.call_args_list
            for r, _perms in call.args[0]
        }
        assert {"src", "docs", "lib", "util.py", "guide.md"} <= persisted

    async def test_subtree_that_is_itself_truncated_is_split_further(self) -> None:
        """Recursion only deepens where the API limit is actually hit."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        truncated = make_git_tree(
            [make_tree_element("src", entry_type="tree", sha="sha-src")], truncated=True,
        )
        root_flat = make_git_tree([make_tree_element("src", entry_type="tree", sha="sha-src")])
        src_flat = make_git_tree([make_tree_element("main.py", entry_type="blob", sha="sha-main", size=10)])

        calls: list[tuple[str, bool]] = []

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            _owner, _name, sha, recursive = args
            calls.append((sha, recursive))
            if sha == "head-sha":
                return ok_response(truncated if recursive else root_flat)
            # "src" is too big to come back recursively; its flat listing fits.
            return ok_response(truncated if recursive else src_flat)

        c.runtime.ds_call.side_effect = dispatch
        assert await ReposSync(c)._full_sync(repo, "head-sha") is True

        assert ("sha-src", True) in calls and ("sha-src", False) in calls
        file_batch = c.data_entities_processor.on_new_records.call_args_list[-1].args[0]
        assert {r.record_name for r, _ in file_batch} == {"main.py"}

    async def test_directory_too_big_to_split_fails_without_pruning(self) -> None:
        """A directory whose flat listing is ALSO truncated cannot be split, so
        the walk is knowingly incomplete. Pruning on it would delete records for
        files that still exist — the old code did exactly that."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        always_truncated = make_git_tree(
            [make_tree_element("f.py", entry_type="blob", sha="sha-f", size=10)], truncated=True,
        )
        c.runtime.ds_call.return_value = ok_response(always_truncated)

        sync = ReposSync(c)
        sync._prune_deleted_paths = AsyncMock()

        assert await sync._full_sync(repo, "head-sha") is False
        sync._prune_deleted_paths.assert_not_awaited()

    async def test_entries_are_persisted_per_subtree_not_all_at_the_end(self) -> None:
        """Streaming persist: records land as the walk proceeds, so a walk that
        dies partway keeps what it already wrote."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        root_recursive = make_git_tree(
            [make_tree_element("a", entry_type="tree", sha="sha-a")], truncated=True,
        )
        root_flat = make_git_tree([
            make_tree_element("a", entry_type="tree", sha="sha-a"),
            make_tree_element("b", entry_type="tree", sha="sha-b"),
        ])
        sub = {
            "sha-a": make_git_tree([make_tree_element("a1.py", entry_type="blob", sha="s1", size=10)]),
            "sha-b": make_git_tree([make_tree_element("b1.py", entry_type="blob", sha="s2", size=10)]),
        }

        events: list[str] = []

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            _owner, _name, sha, recursive = args
            events.append(f"fetch:{sha}")
            if sha == "head-sha":
                return ok_response(root_recursive if recursive else root_flat)
            return ok_response(sub[str(sha)])

        async def record_persist(batch: object) -> None:
            events.append("persist")

        c.runtime.ds_call.side_effect = dispatch
        c.data_entities_processor.on_new_records.side_effect = record_persist

        assert await ReposSync(c)._full_sync(repo, "head-sha") is True

        # A persist must occur before the final fetch — otherwise everything was
        # buffered and written at the end.
        assert "persist" in events[: events.index(f"fetch:sha-b")], events

    async def test_every_file_gets_a_record_including_dotfiles_and_oversized(self) -> None:
        """No-skip policy: nothing in the tree is invisible. Oversized files get
        a record with content indexing off + reason; dotfiles sync normally."""
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
        by_name = {r.record_name: r for r, _ in file_batch}
        assert set(by_name) == {".env", "big.bin", "ok.py"}
        assert by_name["big.bin"].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert "content-indexing limit" in (by_name["big.bin"].reason or "")
        assert by_name["ok.py"].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value
        assert by_name[".env"].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


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

    def test_dotfiles_classified_like_any_other_file(self) -> None:
        """No-skip policy: dotfiles participate in the incremental buckets."""
        c = make_mock_connector()
        sync = ReposSync(c)
        files = [
            make_compare_file(filename=".env", status="added", sha="sha-env"),
            make_compare_file(filename=".gitignore", status="renamed", previous_filename="ignore.txt", sha="sha-x"),
        ]
        deletes, adds, modifies, renames = sync._classify_compare_files(files)
        assert adds == {".env": "sha-env"}
        assert renames == [("ignore.txt", ".gitignore", "sha-x")]
        assert not deletes and not modifies


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

    @pytest.mark.parametrize("status", ["diverged", "behind"])
    async def test_non_ahead_status_triggers_fallback(self, status: str) -> None:
        """"diverged" is a force-push; "behind" is a branch reset to an ancestor,
        where compare returns an EMPTY files list — applying it would advance
        the checkpoint past deletions the delta never saw."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        comparison = make_comparison([], status=status)
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
        record_update = sync._build_folder_record(
            make_repo(repo_id=1), "src", sha=None, code_files_enabled=True
        )

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
    """Regression tests: incrementally-added/modified files bypass the full-sync
    size stamp (Compare Commits entries carry no blob size), so oversized
    content must be refused at stream time with a 413 — TERMINAL for the
    indexing consumer, so the record fails once with a reason instead of
    entering a retry storm."""

    async def test_oversized_incremental_file_refused_with_413(self) -> None:
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
        with pytest.raises(HTTPException) as exc_info:
            await sync.fetch_code_file_content(record)
        assert exc_info.value.status_code == HttpStatusCode.PAYLOAD_TOO_LARGE.value
        assert "content-indexing limit" in exc_info.value.detail

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


class TestLargeBlobFallback:
    """The Contents API returns empty content above 1 MB while the size guard
    admits up to 5 MB, so those files must not index as empty."""

    def _record(self) -> CodeFileRecord:
        return CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="big.py",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/big.py", external_record_group_id="1-code-repository",
            file_path="big.py", file_hash="blobsha1",
        )

    async def test_empty_contents_payload_falls_back_to_git_blob(self) -> None:
        import base64

        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        # 2 MB file: within the guard, but Contents returns no content.
        empty_content = SimpleNamespace(size=2 * 1024 * 1024, decoded_content=b"", content=None)
        blob = SimpleNamespace(
            content=base64.b64encode(b"real file body").decode(), encoding="base64",
        )

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.get_repo_by_id:
                return ok_response(repo)
            if method is c.data_source.get_file_contents:
                return ok_response(empty_content)
            if method is c.data_source.get_git_blob:
                return ok_response(blob)
            raise AssertionError(f"unexpected ds_call for {method!r}")

        c.runtime.ds_call.side_effect = dispatch

        sync = ReposSync(c)
        assert await sync.fetch_code_file_content(self._record()) == b"real file body"

    async def test_raises_rather_than_returning_empty_when_blob_also_fails(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        empty_content = SimpleNamespace(size=2 * 1024 * 1024, decoded_content=b"", content=None)

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.get_repo_by_id:
                return ok_response(repo)
            if method is c.data_source.get_file_contents:
                return ok_response(empty_content)
            if method is c.data_source.get_git_blob:
                return failed_response("404")
            raise AssertionError(f"unexpected ds_call for {method!r}")

        c.runtime.ds_call.side_effect = dispatch

        sync = ReposSync(c)
        with pytest.raises(Exception, match="blobsha1"):
            await sync.fetch_code_file_content(self._record())


class TestPruneDeletedPaths:
    async def test_prunes_paths_absent_from_the_walk(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)
        sync._list_code_records_by_path = AsyncMock(
            return_value={"a.py": "rec-a", "b.py": "rec-b"}
        )

        await sync._prune_deleted_paths(repo, {"a.py"})

        c.data_entities_processor.on_records_deleted_cascade.assert_awaited_once()
        deleted = c.data_entities_processor.on_records_deleted_cascade.call_args.args[0]
        assert deleted == ["rec-b"]

    async def test_inventory_recovers_folder_paths_from_external_ids(self) -> None:
        """Folders are FileRecords with NO file_path attribute — keying the
        inventory on file_path alone silently excluded every folder, so a
        deleted directory's files were pruned while its folder record survived
        every full sync as an empty ghost."""
        c = make_mock_connector()
        sync = ReposSync(c)
        c.tx_store.get_record_group_by_external_id = AsyncMock(
            return_value=SimpleNamespace(id="rg-1")
        )
        c.tx_store.get_records_by_status = AsyncMock(return_value=[
            SimpleNamespace(id="rec-a", file_path="src/a.py",
                            external_record_id="/1/blob/src/a.py"),
            SimpleNamespace(id="rec-src", file_path=None,
                            external_record_id="/1/tree/src"),
            SimpleNamespace(id="rec-alien", file_path=None,
                            external_record_id="something-else"),
        ])

        by_path = await sync._list_code_records_by_path("1-code-repository")

        assert by_path == {"src/a.py": "rec-a", "src": "rec-src"}

    async def test_stale_folders_are_pruned_deepest_first(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)
        sync._list_code_records_by_path = AsyncMock(return_value={
            "src": "rec-src",
            "src/sub": "rec-sub",
            "src/sub/c.py": "rec-c",
            "kept.py": "rec-kept",
        })

        await sync._prune_deleted_paths(repo, {"kept.py"})

        deleted = c.data_entities_processor.on_records_deleted_cascade.call_args.args[0]
        assert deleted == ["rec-c", "rec-sub", "rec-src"]

    async def test_refuses_a_suspiciously_large_prune(self) -> None:
        """A truncated tree walk would otherwise delete most of a repo."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        sync = ReposSync(c)
        sync._list_code_records_by_path = AsyncMock(
            return_value={f"f{i}.py": f"rec-{i}" for i in range(20)}
        )

        await sync._prune_deleted_paths(repo, {"f0.py"})

        c.data_entities_processor.on_records_deleted_cascade.assert_not_awaited()


class TestRunDispatchEdgeCases:
    async def test_empty_default_branch_skips(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        repo.default_branch = ""
        sync = ReposSync(c)
        sync._full_sync = _async_return(True)

        await sync.run(repo)

        sync._full_sync.assert_not_awaited()
        c.runtime.ds_call.assert_not_awaited()

    async def test_branch_lookup_failure_skips(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("404")
        sync = ReposSync(c)
        sync._full_sync = _async_return(True)

        await sync.run(make_repo(repo_id=1))

        sync._full_sync.assert_not_awaited()

    async def test_missing_head_sha_skips(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response(SimpleNamespace(commit=None))
        sync = ReposSync(c)
        sync._full_sync = _async_return(True)

        await sync.run(make_repo(repo_id=1))

        sync._full_sync.assert_not_awaited()

    async def test_checkpoint_read_failure_treats_as_no_checkpoint(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response(
            type("Branch", (), {"commit": type("Commit", (), {"sha": "head-sha"})()})()
        )
        c.record_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("missing"))
        sync = ReposSync(c)
        sync._full_sync = _async_return(True)

        await sync.run(make_repo(repo_id=1))

        sync._full_sync.assert_awaited_once()

    async def test_full_sync_error_does_not_advance_checkpoint(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response(
            type("Branch", (), {"commit": type("Commit", (), {"sha": "head-sha"})()})()
        )
        c.record_sync_point.read_sync_point.return_value = None
        sync = ReposSync(c)
        sync._full_sync = _async_return(False)

        await sync.run(make_repo(repo_id=1))

        c.record_sync_point.update_sync_point.assert_not_awaited()


class TestFullSyncFailures:
    async def test_git_tree_failure_returns_false(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("500")
        assert await ReposSync(c)._full_sync(make_repo(repo_id=1), "head-sha") is False

    async def test_subtree_failure_returns_false_without_pruning(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.return_value = failed_response("500")
        sync = ReposSync(c)
        sync._persist_tree_entries = AsyncMock(return_value=(True, 0, 0))
        sync._prune_deleted_paths = AsyncMock()

        ok = await sync._full_sync_untruncated(repo, "head-sha")

        assert ok is False
        sync._prune_deleted_paths.assert_not_awaited()


class TestIncrementalCompareLimits:
    async def test_compare_files_cap_returns_false(self) -> None:
        from app.connectors.sources.github_teams.constants import COMPARE_COMMITS_FILES_LIMIT

        c = make_mock_connector()
        files = [
            make_compare_file(filename=f"f{i}.py", status="added", sha=f"s{i}")
            for i in range(COMPARE_COMMITS_FILES_LIMIT)
        ]
        c.runtime.ds_call.return_value = ok_response(make_comparison(files, total_commits=1))
        assert await ReposSync(c)._incremental_sync(make_repo(repo_id=1), "old", "new") is False

    async def test_compare_commits_cap_returns_false(self) -> None:
        from app.connectors.sources.github_teams.constants import COMPARE_COMMITS_TOTAL_LIMIT

        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response(
            make_comparison([make_compare_file(filename="a.py", status="added")], total_commits=COMPARE_COMMITS_TOTAL_LIMIT)
        )
        assert await ReposSync(c)._incremental_sync(make_repo(repo_id=1), "old", "new") is False

    def test_copied_is_classified_as_add(self) -> None:
        files = [make_compare_file(filename="copy.py", status="copied", sha="sha-c")]
        _deletes, adds, _modifies, _renames = ReposSync(make_mock_connector())._classify_compare_files(files)
        assert adds == {"copy.py": "sha-c"}

    def test_rename_without_paths_is_skipped(self) -> None:
        files = [SimpleNamespace(status="renamed", filename=None, previous_filename=None, sha="x")]
        deletes, adds, modifies, renames = ReposSync(make_mock_connector())._classify_compare_files(files)
        assert deletes == adds == modifies == {}
        assert renames == []


class TestFetchCodeFileContentErrors:
    async def test_missing_group_id_raises(self) -> None:
        record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="a.py",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/a.py", file_path="a.py",
        )
        with pytest.raises(Exception, match="Repository id not found"):
            await ReposSync(make_mock_connector()).fetch_code_file_content(record)

    async def test_repo_lookup_failure_raises(self) -> None:
        record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="a.py",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/a.py", external_record_group_id="1-code-repository",
            file_path="a.py",
        )
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("404")
        with pytest.raises(Exception, match="Failed to resolve repo"):
            await ReposSync(c).fetch_code_file_content(record)

    async def test_contents_failure_raises(self) -> None:
        record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="a.py",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/a.py", external_record_group_id="1-code-repository",
            file_path="a.py",
        )
        c = make_mock_connector()

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.get_repo_by_id:
                return ok_response(make_repo(repo_id=1))
            if method is c.data_source.get_file_contents:
                return failed_response("500")
            raise AssertionError(f"unexpected {method!r}")

        c.runtime.ds_call.side_effect = dispatch
        with pytest.raises(Exception, match="Failed to fetch content"):
            await ReposSync(c).fetch_code_file_content(record)

    async def test_missing_file_path_raises(self) -> None:
        record = CodeFileRecord(
            id="rec-1", org_id="org-1", record_name="a.py",
            record_type="CODE_FILE", version=0, origin="CONNECTOR",
            connector_name="GITHUB TEAMS", connector_id="github-conn-1",
            external_record_id="/1/blob/a.py", external_record_group_id="1-code-repository",
            file_path="",
        )
        with pytest.raises(Exception, match="Cannot resolve repo path"):
            await ReposSync(make_mock_connector()).fetch_code_file_content(record)


class TestTimestampLifecycle:
    async def test_cancel_is_noop_when_no_task(self) -> None:
        sync = ReposSync(make_mock_connector())
        await sync.timestamps.cancel()

    async def test_cancel_stops_running_task(self) -> None:
        import asyncio

        c = make_mock_connector()
        sync = ReposSync(c)

        async def hang() -> None:
            await asyncio.sleep(30)

        sync.timestamps._task = asyncio.create_task(hang())
        await sync.timestamps.cancel()
        assert sync.timestamps._task is None

    async def test_schedule_is_idempotent_while_running(self) -> None:
        import asyncio

        c = make_mock_connector()
        sync = ReposSync(c)
        existing = asyncio.create_task(asyncio.sleep(30))
        sync.timestamps._task = existing
        sync.timestamps.schedule()
        assert sync.timestamps._task is existing
        existing.cancel()
        await asyncio.gather(existing, return_exceptions=True)

    async def test_run_logs_and_clears_task_on_failure(self) -> None:
        c = make_mock_connector()
        c.projects._resolve_repos_with_filters = AsyncMock(side_effect=RuntimeError("no orgs"))
        sync = ReposSync(c)
        await sync.timestamps._run()
        c.logger.error.assert_called()
        assert sync.timestamps._task is None

    async def test_apply_patches_failure_is_logged(self) -> None:
        c = make_mock_connector()
        c.tx_store.batch_update_nodes = AsyncMock(side_effect=RuntimeError("db"))
        await ReposSync(c).timestamps._apply_patches(
            [{"id": "n1", "sourceCreatedAtTimestamp": 1}], "records", context="acme/widgets"
        )
        c.logger.warning.assert_called()

    def test_iso_and_commit_date_helpers(self) -> None:
        from app.connectors.sources.github_teams.timestamps import (
            _commit_authored_ms,
            _iso_to_ms,
        )

        assert _iso_to_ms(None) is None
        assert _iso_to_ms("not-a-date") is None
        assert _iso_to_ms("2024-01-05T00:00:00") == 1704412800000
        naive = datetime(2024, 1, 5)
        commit = SimpleNamespace(commit=SimpleNamespace(author=SimpleNamespace(date=naive)))
        assert _commit_authored_ms(commit) == 1704412800000
        assert _commit_authored_ms(SimpleNamespace(commit=None)) is None


def _async_return(value: object) -> object:
    from unittest.mock import AsyncMock
    return AsyncMock(return_value=value)
