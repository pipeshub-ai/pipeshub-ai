"""Branch selection for GitLab code sync.

The admin may pick any branch in the "Branch" sync filter; unset keeps the
project's default branch. Record ids always name HEAD (they were GitLab's
``webPath`` of a HEAD walk before branches could be picked), so picking or
switching a branch updates records in place instead of creating a second set.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey
from app.connectors.sources.gitlab.filters import FiltersHelper
from app.connectors.sources.gitlab.repos import ReposSync
from app.sources.external.gitlab.gitlab_data_source import GitLabDataSource

from .conftest import branch_res, make_mock_connector

pytestmark = pytest.mark.anyio

_PROJECT_ID = 42
_PROJECT_PATH = "ns/proj"


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _branch_filters(value: object) -> FilterCollection:
    return FilterCollection.model_validate({"filters": [{
        "key": SyncFilterKey.BRANCH.value, "type": "select", "operator": "in", "value": value,
    }]})


def _routed(sync_filters: FilterCollection | None, checkpoint: tuple) -> tuple[MagicMock, ReposSync]:
    c = make_mock_connector()
    c.sync_filters = sync_filters
    c.runtime.ds_call = AsyncMock(return_value=branch_res("head-sha"))
    repos = ReposSync(c)
    repos._load_linguist_exclusions = AsyncMock(return_value=None)
    repos._get_code_repo_checkpoint = AsyncMock(return_value=checkpoint)
    repos._update_code_repo_checkpoint = AsyncMock()
    repos._sync_repo_full = AsyncMock(return_value=True)
    repos._sync_repo_incremental = AsyncMock(return_value=True)
    return c, repos


def _page(trees: list[dict], blobs: list[dict]) -> MagicMock:
    body = {"data": {"project": {"repository": {"paginatedTree": {
        "nodes": [{"trees": {"nodes": trees}, "blobs": {"nodes": blobs}}],
        "pageInfo": {"hasNextPage": False, "endCursor": ""},
    }}}}}
    return MagicMock(success=True, error=None, data=json.dumps(body))


class TestSyncRouting:
    async def test_unset_branch_walks_head_on_the_default_branch(self) -> None:
        c, repos = _routed(None, (None, None, None))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        assert c.runtime.ds_call.await_args.kwargs["branch"] == "main"
        repos._sync_repo_full.assert_awaited_once_with(_PROJECT_ID, _PROJECT_PATH, "HEAD")
        repos._update_code_repo_checkpoint.assert_awaited_once_with(
            _PROJECT_ID, "head-sha", _PROJECT_PATH, "main",
        )

    async def test_chosen_branch_is_walked_at_its_head_commit(self) -> None:
        c, repos = _routed(_branch_filters(["release/2.x"]), (None, None, None))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        assert c.runtime.ds_call.await_args.kwargs["branch"] == "release/2.x"
        repos._sync_repo_full.assert_awaited_once_with(_PROJECT_ID, _PROJECT_PATH, "head-sha")
        repos._update_code_repo_checkpoint.assert_awaited_once_with(
            _PROJECT_ID, "head-sha", _PROJECT_PATH, "release/2.x",
        )

    async def test_switching_branch_rewalks_the_tree(self) -> None:
        _c, repos = _routed(_branch_filters(["release/2.x"]), ("old-sha", _PROJECT_PATH, "main"))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        repos._sync_repo_full.assert_awaited_once()
        repos._sync_repo_incremental.assert_not_awaited()

    async def test_checkpoint_from_before_branches_counts_as_default_branch(self) -> None:
        _c, repos = _routed(None, ("old-sha", _PROJECT_PATH, None))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        repos._sync_repo_incremental.assert_awaited_once()
        repos._sync_repo_full.assert_not_awaited()

    async def test_old_checkpoint_with_a_chosen_branch_rewalks(self) -> None:
        _c, repos = _routed(_branch_filters(["release/2.x"]), ("old-sha", _PROJECT_PATH, None))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        repos._sync_repo_full.assert_awaited_once()
        repos._sync_repo_incremental.assert_not_awaited()

    async def test_missing_branch_is_a_sync_problem_and_keeps_records(self) -> None:
        c, repos = _routed(_branch_filters(["gone"]), ("old-sha", _PROJECT_PATH, "gone"))
        c.runtime.ds_call = AsyncMock(return_value=MagicMock(
            success=False, data=None, error="404 Branch Not Found", status_code=404,
        ))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        repos._sync_repo_full.assert_not_awaited()
        repos._sync_repo_incremental.assert_not_awaited()
        repos._update_code_repo_checkpoint.assert_not_awaited()
        [problem] = [call.args[0] for call in c.note_sync_problem.call_args_list]
        assert "'gone'" in problem and "does not exist" in problem
        c.note_failed_read.assert_not_called()

    async def test_empty_repository_on_default_branch_is_still_an_answer(self) -> None:
        c, repos = _routed(None, (None, None, None))
        c.runtime.ds_call = AsyncMock(return_value=MagicMock(
            success=False, data=None, error="404", status_code=404,
        ))

        await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

        c.note_failed_read.assert_called_once()
        c.note_sync_problem.assert_not_called()


class TestWalkAtCommit:
    async def test_entries_are_keyed_as_head_and_linked_to_the_branch(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        c.runtime.ds_call_async = AsyncMock(return_value=_page(
            [{"name": "src", "path": "src", "sha": "t1", "type": "tree",
              "webPath": "/ns/proj/-/tree/head-sha/src",
              "webUrl": "https://gitlab.com/ns/proj/-/tree/head-sha/src"}],
            [{"name": "a.py", "path": "src/a.py", "sha": "b1", "type": "blob",
              "webPath": "/ns/proj/-/blob/head-sha/src/a.py",
              "webUrl": "https://gitlab.com/ns/proj/-/blob/head-sha/src/a.py"}],
        ))

        kind, trees, blobs, _info, _err, _wait = await ReposSync(c)._fetch_entries_page_once(
            _PROJECT_PATH, _PROJECT_ID, "", "head-sha",
        )

        assert kind == "data"
        assert c.runtime.ds_call_async.await_args.kwargs["ref"] == "head-sha"
        assert trees[0]["webPath"] == "/ns/proj/-/tree/HEAD/src"
        assert trees[0]["webUrl"] == "https://gitlab.com/ns/proj/-/tree/release/2.x/src"
        assert blobs[0]["webPath"] == "/ns/proj/-/blob/HEAD/src/a.py"
        # A file stays pinned to the commit it was read at.
        assert blobs[0]["webUrl"] == "https://gitlab.com/ns/proj/-/blob/head-sha/src/a.py"

    async def test_unexpected_web_path_is_rebuilt_from_the_path(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call_async = AsyncMock(return_value=_page([], [
            {"name": "a.py", "path": "src/a.py", "sha": "b1", "type": "blob",
             "webPath": "/ns/proj/-/blob/main/src/a.py", "webUrl": "https://gitlab.com/x"},
        ]))

        _kind, _trees, blobs, *_ = await ReposSync(c)._fetch_entries_page_once(
            _PROJECT_PATH, _PROJECT_ID, "", "head-sha",
        )

        assert blobs[0]["webPath"] == "/ns/proj/-/blob/HEAD/src/a.py"

    async def test_head_walk_is_left_as_gitlab_returned_it(self) -> None:
        c = make_mock_connector()
        tree = {"name": "src", "path": "src", "sha": "t1", "type": "tree",
                "webPath": "/ns/proj/-/tree/HEAD/src", "webUrl": "https://gitlab.com/ns/proj/-/tree/HEAD/src"}
        c.runtime.ds_call_async = AsyncMock(return_value=_page([tree], []))

        _kind, trees, *_ = await ReposSync(c)._fetch_entries_page_once(_PROJECT_PATH, _PROJECT_ID, "")

        assert c.runtime.ds_call_async.await_args.kwargs["ref"] == "HEAD"
        assert trees == [tree]

    async def test_files_walked_at_a_commit_are_pinned_to_it(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call_async = AsyncMock(return_value=_page([], [
            {"name": "a.py", "path": "a.py", "sha": "b1", "type": "blob",
             "webPath": "/ns/proj/-/blob/head-sha/a.py", "webUrl": "https://gitlab.com/x"},
        ]))
        repos = ReposSync(c)
        repos.build_code_file_records = AsyncMock(return_value=True)
        repos._remove_unwalked = AsyncMock(return_value=True)

        assert await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH, "head-sha") is True

        assert repos.build_code_file_records.await_args.kwargs["commit_sha"] == "head-sha"
        repos._remove_unwalked.assert_awaited_once_with(_PROJECT_ID, {"/ns/proj/-/blob/HEAD/a.py"})

    async def test_files_walked_at_head_are_not_pinned(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call_async = AsyncMock(return_value=_page([], [
            {"name": "a.py", "path": "a.py", "sha": "b1", "type": "blob",
             "webPath": "/ns/proj/-/blob/HEAD/a.py", "webUrl": "https://gitlab.com/x"},
        ]))
        repos = ReposSync(c)
        repos.build_code_file_records = AsyncMock(return_value=True)
        repos._remove_unwalked = AsyncMock(return_value=True)

        await repos._sync_repo_full(_PROJECT_ID, _PROJECT_PATH)

        assert repos.build_code_file_records.await_args.kwargs["commit_sha"] is None


class TestIncrementalFoldersAndDates:
    async def test_new_folder_links_to_the_chosen_branch(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        repos = ReposSync(c)
        repos._process_records = AsyncMock(return_value=True)

        await repos._ensure_folder_records_for_paths(_PROJECT_ID, _PROJECT_PATH, ["src/a.py"], ref="new-sha")

        [update] = repos._process_records.await_args.args[0]
        assert update.record.external_record_id == "/ns/proj/-/tree/HEAD/src"
        assert update.record.weburl == "https://gitlab.com/ns/proj/-/tree/release/2.x/src"

    async def test_new_folder_on_the_default_branch_links_head(self) -> None:
        c = make_mock_connector()
        repos = ReposSync(c)
        repos._process_records = AsyncMock(return_value=True)

        await repos._ensure_folder_records_for_paths(_PROJECT_ID, _PROJECT_PATH, ["src/a.py"], ref="new-sha")

        [update] = repos._process_records.await_args.args[0]
        assert update.record.weburl == "https://gitlab.com/ns/proj/-/tree/HEAD/src"

    async def test_dates_come_from_the_chosen_branch(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        c.runtime.ds_call = AsyncMock(return_value=MagicMock(success=True, data={
            "oldest_committed_date": "2024-01-01T00:00:00Z", "newest_committed_date": "2024-01-05T00:00:00Z",
        }))

        await ReposSync(c)._fetch_code_file_timestamps_batch(_PROJECT_ID, ["src/a.py"])

        assert c.runtime.ds_call.await_args.kwargs["ref_name"] == "release/2.x"


class TestCheckpoint:
    async def test_branch_is_read_back(self) -> None:
        c = make_mock_connector()
        c.record_sync_point.read_sync_point = AsyncMock(return_value={
            "last_commit_sha": "abc", "project_path": _PROJECT_PATH, "branch": "release/2.x",
        })

        assert await ReposSync(c)._get_code_repo_checkpoint(_PROJECT_ID) == ("abc", _PROJECT_PATH, "release/2.x")


class TestListBranches:
    def test_search_is_sent_only_when_given(self) -> None:
        ds = GitLabDataSource.__new__(GitLabDataSource)
        project = MagicMock()
        ds._project = MagicMock(return_value=project)

        ds.list_branches("ns/proj", page=2, per_page=10)
        ds.list_branches("ns/proj", search="rel")

        assert project.branches.list.call_args_list[0].kwargs == {"page": 2, "per_page": 10, "get_all": False}
        assert project.branches.list.call_args_list[1].kwargs == {
            "page": 1, "per_page": 20, "get_all": False, "search": "rel",
        }

    def test_failure_carries_the_status(self) -> None:
        ds = GitLabDataSource.__new__(GitLabDataSource)
        error = Exception("404 Project Not Found")
        error.response_code = 404
        ds._project = MagicMock(side_effect=error)

        res = ds.list_branches("ns/gone")

        assert res.success is False
        assert res.status_code == 404


class TestBranchPicker:
    def _connector(self, rows: list, project_default: str | None = None) -> MagicMock:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c._request_filter_context_group_paths = ["ns/proj"]

        async def ds_call(method, *args, **kwargs) -> object:
            if method is c.data_source.list_branches:
                return MagicMock(success=True, data=rows)
            return MagicMock(success=True, data=SimpleNamespace(default_branch=project_default))

        c.runtime.ds_call = AsyncMock(side_effect=ds_call)
        return c

    async def test_no_project_in_the_form_lists_nothing(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c._request_filter_context_group_paths = []

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value)

        assert resp.options == []
        assert resp.message == "Select a repository to list its branches."

    async def test_default_branch_on_the_page_leads(self) -> None:
        c = self._connector([
            SimpleNamespace(name="dev", default=False), SimpleNamespace(name="main", default=True),
        ])

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, limit=2)

        assert [(o.id, o.label) for o in resp.options] == [("main", "main (default)"), ("dev", "dev")]
        assert resp.has_more is True
        list_call = c.runtime.ds_call.await_args_list[0]
        assert list_call.args[1:] == ("ns/proj",)
        assert list_call.kwargs == {"search": None, "page": 1, "per_page": 2}

    async def test_default_branch_off_the_page_is_looked_up_for_page_one(self) -> None:
        c = self._connector([SimpleNamespace(name="dev", default=False)], project_default="trunk")

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value)

        assert [o.id for o in resp.options] == ["trunk", "dev"]
        assert resp.has_more is False

    async def test_later_pages_do_not_repeat_the_default_branch(self) -> None:
        c = self._connector([
            SimpleNamespace(name="main", default=True), SimpleNamespace(name="zeta", default=False),
        ])

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, page=2)

        assert [o.id for o in resp.options] == ["zeta"]

    async def test_search_leaves_out_a_default_branch_that_does_not_match(self) -> None:
        c = self._connector([SimpleNamespace(name="release/2.x", default=False)], project_default="main")

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, search="rel")

        assert [o.id for o in resp.options] == ["release/2.x"]
        assert c.runtime.ds_call.await_args_list[0].kwargs["search"] == "rel"

    async def test_pattern_search_keeps_the_default_branch_gitlab_matched(self) -> None:
        # GitLab reads ^, $ and * as a pattern, so "^ma" matches main although the
        # text "^ma" is not in it: what GitLab returned decides, not a substring test.
        c = self._connector([SimpleNamespace(name="main", default=True), SimpleNamespace(name="maint", default=False)])

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, search="^ma")

        assert [(o.id, o.label) for o in resp.options] == [("main", "main (default)"), ("maint", "maint")]

    async def test_search_keeps_gitlab_order_and_a_default_branch_on_a_later_page(self) -> None:
        # A search does not move the default branch to page 1, so a later page must
        # not drop it either: each match is listed once, where GitLab put it.
        c = self._connector([SimpleNamespace(name="main", default=True), SimpleNamespace(name="mainline", default=False)])

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, page=2, search="main")

        assert [o.id for o in resp.options] == ["main", "mainline"]
        assert all(call.args[0] != c.data_source.get_project for call in c.runtime.ds_call.await_args_list)
