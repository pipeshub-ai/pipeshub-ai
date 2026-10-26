"""Branch selection for GitHub code sync.

The admin may pick any branch in the "Branch" sync filter; unset keeps the
repository's default branch. Covers the filter read, the sync routing (which
branch is read, when a branch switch re-walks the tree, a branch that does not
exist), links and the streaming ref, commit dates, and the branch picker.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.code_indexing_flags import (
    branch_filter_field,
    code_branch,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterType,
    OptionSourceType,
    SyncFilterKey,
)
from app.connectors.sources.github_teams.filters import FiltersHelper
from app.connectors.sources.github_teams.repos import ReposSync
from app.sources.external.github.github_async import GitHubAsyncDataSource
from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    make_repo,
    ok_response,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _branch_filters(value: object) -> FilterCollection:
    return FilterCollection.model_validate({"filters": [{
        "key": SyncFilterKey.BRANCH.value, "type": "select", "operator": "in", "value": value,
    }]})


def _branch(sha: str) -> object:
    return ok_response(SimpleNamespace(commit=SimpleNamespace(sha=sha)))


def _sync(c: MagicMock) -> ReposSync:
    sync = ReposSync(c)
    sync._full_sync = AsyncMock(return_value=True)
    sync._incremental_sync = AsyncMock(return_value=True)
    sync._load_linguist_exclusions = AsyncMock(return_value=None)
    return sync


class TestCodeBranch:
    def test_unset_means_default_branch(self) -> None:
        assert code_branch(None) is None
        assert code_branch(FilterCollection()) is None
        assert code_branch(_branch_filters([])) is None
        assert code_branch(_branch_filters(["  "])) is None

    def test_select_value_is_a_one_element_list(self) -> None:
        assert code_branch(_branch_filters(["release/2.x"])) == "release/2.x"

    def test_field_is_an_optional_dynamic_select(self) -> None:
        field = branch_filter_field()
        assert field.name == SyncFilterKey.BRANCH.value
        assert field.filter_type == FilterType.SELECT
        assert field.option_source_type == OptionSourceType.DYNAMIC
        assert not field.required


class TestSyncRouting:
    async def test_unset_branch_reads_the_default_branch(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call = AsyncMock(return_value=_branch("head-sha"))
        c.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.record_sync_point.update_sync_point = AsyncMock()
        sync = _sync(c)

        assert await sync.run(make_repo(default_branch="main")) is True

        assert c.runtime.ds_call.await_args.args[1:] == ("acme", "widgets", "main")
        checkpoint = c.record_sync_point.update_sync_point.await_args.args[1]
        assert checkpoint["default_branch"] == "main"

    async def test_chosen_branch_is_read_and_checkpointed(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        c.runtime.ds_call = AsyncMock(return_value=_branch("head-sha"))
        c.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.record_sync_point.update_sync_point = AsyncMock()
        sync = _sync(c)

        assert await sync.run(make_repo(default_branch="main")) is True

        assert c.runtime.ds_call.await_args.args[1:] == ("acme", "widgets", "release/2.x")
        sync._full_sync.assert_awaited_once()
        checkpoint = c.record_sync_point.update_sync_point.await_args.args[1]
        # The key predates branch selection; it now holds the synced branch.
        assert checkpoint == {
            "last_commit_sha": "head-sha", "default_branch": "release/2.x", "full_name": "acme/widgets",
        }

    async def test_switching_branch_rewalks_the_tree(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        c.runtime.ds_call = AsyncMock(return_value=_branch("head-sha"))
        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_commit_sha": "old-sha", "default_branch": "main"},
        )
        c.record_sync_point.update_sync_point = AsyncMock()
        sync = _sync(c)

        await sync.run(make_repo(default_branch="main"))

        sync._full_sync.assert_awaited_once()
        sync._incremental_sync.assert_not_awaited()

    async def test_same_branch_stays_incremental(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        c.runtime.ds_call = AsyncMock(return_value=_branch("head-sha"))
        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_commit_sha": "old-sha", "default_branch": "release/2.x"},
        )
        c.record_sync_point.update_sync_point = AsyncMock()
        sync = _sync(c)

        await sync.run(make_repo(default_branch="main"))

        sync._incremental_sync.assert_awaited_once()
        sync._full_sync.assert_not_awaited()

    async def test_missing_branch_fails_without_touching_records(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["gone"])
        c.runtime.ds_call = AsyncMock(
            return_value=failed_response("Branch not found", status_code=HttpStatusCode.NOT_FOUND.value),
        )
        c.record_sync_point.update_sync_point = AsyncMock()
        sync = _sync(c)

        assert await sync.run(make_repo(default_branch="main")) is False

        assert c.runtime.ds_call.await_count == 1
        sync._full_sync.assert_not_awaited()
        sync._incremental_sync.assert_not_awaited()
        c.record_sync_point.update_sync_point.assert_not_awaited()
        assert "does not exist" in c.logger.error.call_args.args[0]


class TestLinksAndStreaming:
    def test_folder_links_to_the_chosen_branch(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        record = ReposSync(c)._build_folder_record(make_repo(), "src/app", "tree-sha", code_files_enabled=True)
        assert record.weburl == "https://github.com/acme/widgets/tree/release/2.x/src/app"

    def test_unpinned_file_links_to_the_chosen_branch(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        sync = ReposSync(c)
        unpinned = sync._build_code_file_record(make_repo(), "src/a.py", "blob-sha", True, False)
        pinned = sync._build_code_file_record(make_repo(), "src/a.py", "blob-sha", True, False, commit_sha="c0ffee")
        assert unpinned.weburl == "https://github.com/acme/widgets/blob/release/2.x/src/a.py"
        assert pinned.weburl == "https://github.com/acme/widgets/blob/c0ffee/src/a.py"

    async def test_unpinned_record_streams_from_the_chosen_branch(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        repo = make_repo(repo_id=7)
        c.runtime.get_repo_by_id = AsyncMock(return_value=ok_response(repo))
        c.runtime.ds_call = AsyncMock(return_value=ok_response(
            SimpleNamespace(content="aGk=", encoding="base64", size=2),
        ))
        record = SimpleNamespace(
            id="r1", external_record_group_id="7-code-repository", file_path="src/a.py",
            file_hash=None, size_in_bytes=None, commit_sha=None,
        )

        await ReposSync(c).fetch_code_file_content(record)

        assert c.runtime.ds_call.await_args.args[1:] == ("acme", "widgets", "src/a.py", "release/2.x")


class TestCommitDates:
    async def test_graphql_reads_history_of_the_given_ref(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call = AsyncMock(return_value=ok_response({"repository": {"object": {
            "p0": {"totalCount": 1, "nodes": [{"authoredDate": "2024-01-05T00:00:00Z"}],
                   "pageInfo": {"endCursor": "abc 0"}},
        }}}))

        dates = await ReposSync(c).timestamps.fetch_commit_dates("acme", "widgets", ["a.py"], "release/2.x")

        assert dates == {"a.py": (1704412800000, 1704412800000)}
        query, variables = c.runtime.ds_call.await_args.args[1:]
        assert "object(expression: $ref)" in query
        assert variables["ref"] == "release/2.x"

    async def test_rest_fallback_reads_history_of_the_given_ref(self) -> None:
        c = make_mock_connector()

        async def fake_ds_call(method, *args, **kwargs) -> object:
            if method is c.data_source.graphql_query:
                return failed_response("bad gateway", status_code=502)
            return ok_response((None, None))

        c.runtime.ds_call = AsyncMock(side_effect=fake_ds_call)

        await ReposSync(c).timestamps.fetch_commit_dates("acme", "widgets", ["a.py"], "release/2.x")

        rest_call = c.runtime.ds_call.await_args_list[-1]
        assert rest_call.args[1:] == ("acme", "widgets", "a.py", "release/2.x")

    async def test_backfill_dates_files_from_the_synced_branch(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _branch_filters(["release/2.x"])
        c.runtime.refresh_token_if_needed = AsyncMock()
        sync = ReposSync(c)
        c.repos = sync
        c.data_entities_processor.get_nodes_by_filters = AsyncMock(side_effect=[[{
            "id": "n1", "externalRecordId": "/1/blob/src/a.py",
            "sourceCreatedAtTimestamp": None, "sourceLastModifiedTimestamp": None,
        }], [], []])
        sync.timestamps._fetch_commit_dates = AsyncMock(return_value=({}, None))

        await sync.timestamps.backfill_repo(make_repo(repo_id=1))

        assert sync.timestamps._fetch_commit_dates.await_args.args[3] == "release/2.x"


class TestListBranches:
    async def test_parses_one_graphql_page(self) -> None:
        ds = GitHubAsyncDataSource.__new__(GitHubAsyncDataSource)
        ds.graphql_query = AsyncMock(return_value=ok_response({"repository": {
            "defaultBranchRef": {"name": "main"},
            "refs": {"nodes": [{"name": "dev"}, {"name": "main"}],
                     "pageInfo": {"hasNextPage": True, "endCursor": "cur-2"}},
        }}))

        res = await ds.list_branches("acme", "widgets", per_page=2, after="cur-1", query="d")

        assert res.success is True
        assert res.data == {
            "default_branch": "main", "names": ["dev", "main"], "has_next_page": True, "end_cursor": "cur-2",
        }
        variables = ds.graphql_query.await_args.args[1]
        assert variables == {"owner": "acme", "name": "widgets", "first": 2, "after": "cur-1", "query": "d"}

    async def test_missing_repository_is_a_404(self) -> None:
        ds = GitHubAsyncDataSource.__new__(GitHubAsyncDataSource)
        ds.graphql_query = AsyncMock(return_value=ok_response({"repository": None}))

        res = await ds.list_branches("acme", "gone")

        assert res.success is False
        assert res.status_code == HttpStatusCode.NOT_FOUND.value


class TestBranchPicker:
    async def test_no_repository_in_the_form_lists_nothing(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c._request_filter_context_group_paths = []

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value)

        assert resp.success is True
        assert resp.options == []
        assert resp.message == "Select a repository to list its branches."
        c.runtime.ds_call.assert_not_called()

    async def test_first_page_leads_with_the_default_branch(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c._request_filter_context_group_paths = ["acme/widgets"]
        c.runtime.ds_call = AsyncMock(return_value=ok_response({
            "default_branch": "main", "names": ["dev", "main", "release/2.x"],
            "has_next_page": True, "end_cursor": "cur-1",
        }))

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, limit=3)

        assert [(o.id, o.label) for o in resp.options] == [
            ("main", "main (default)"), ("dev", "dev"), ("release/2.x", "release/2.x"),
        ]
        assert resp.has_more is True
        assert resp.cursor == "cur-1"
        call = c.runtime.ds_call.await_args
        assert call.args[1:] == ("acme", "widgets")
        assert call.kwargs == {"per_page": 3, "after": None, "query": None}

    async def test_later_pages_skip_the_default_branch(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c._request_filter_context_group_paths = ["acme/widgets"]
        c.runtime.ds_call = AsyncMock(return_value=ok_response({
            "default_branch": "main", "names": ["main", "zeta"], "has_next_page": False, "end_cursor": None,
        }))

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, cursor="cur-1")

        assert [o.id for o in resp.options] == ["zeta"]
        assert resp.cursor is None
        assert c.runtime.ds_call.await_args.kwargs["after"] == "cur-1"

    async def test_search_leaves_out_a_default_branch_that_does_not_match(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c._request_filter_context_group_paths = ["acme/widgets"]
        c.runtime.ds_call = AsyncMock(return_value=ok_response({
            "default_branch": "main", "names": ["release/2.x"], "has_next_page": False, "end_cursor": None,
        }))

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value, search="rel")

        assert [o.id for o in resp.options] == ["release/2.x"]
        assert c.runtime.ds_call.await_args.kwargs["query"] == "rel"

    async def test_listing_failure_is_reported(self) -> None:
        c = make_mock_connector()
        c.runtime.refresh_token_if_needed = AsyncMock()
        c._request_filter_context_group_paths = ["acme/widgets"]
        c.runtime.ds_call = AsyncMock(return_value=failed_response("Bad credentials", status_code=401))

        resp = await FiltersHelper(c).get_filter_options(SyncFilterKey.BRANCH.value)

        assert resp.success is False
        assert resp.message == "Bad credentials"
