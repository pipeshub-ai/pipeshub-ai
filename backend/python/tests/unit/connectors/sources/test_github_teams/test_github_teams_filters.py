"""Unit tests for github_teams FiltersHelper.

Covers:
- ORG_IDS picker: search filtering, pagination, has_more.
- REPO_IDS picker: search delegates to search_repositories; no-search
  delegates to list_user_repos with in-memory pagination.
- Unsupported filter key raises ValueError to the caller (not converted into a
  failure response).
- Uninitialized data source short-circuits with a failure response.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.filters import FiltersHelper
from app.connectors.core.registry.filters import SyncFilterKey

from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    ok_response,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class TestOrgFilterOptions:
    async def test_lists_and_sorts_orgs(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login="zebra", name="Zebra Corp"),
            SimpleNamespace(login="acme", name="Acme Inc"),
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value, page=1, limit=20)

        assert resp.success is True
        assert [o.id for o in resp.options] == ["acme", "zebra"]
        assert resp.has_more is False

    async def test_search_filters_by_login_or_name(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login="acme", name="Acme Inc"),
            SimpleNamespace(login="other", name="Other Org"),
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value, search="acme")

        assert len(resp.options) == 1
        assert resp.options[0].id == "acme"

    async def test_pagination_has_more(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login=f"org{i}", name=f"Org {i}") for i in range(5)
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value, page=1, limit=2)

        assert len(resp.options) == 2
        assert resp.has_more is True

    async def test_list_failure_returns_error_response(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("403 forbidden")
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value)

        assert resp.success is False
        assert resp.options == []


def _with_orgs(c: object, orgs: list[str] | None = None, ok: bool = True) -> None:
    """Give the mock connector a resolvable org scope for the repo picker."""
    c.users._resolve_target_orgs = AsyncMock(return_value=(orgs if orgs is not None else ["acme"], ok))
    c._github_login = None


class TestRepoFilterOptions:
    async def test_search_uses_search_repositories_scoped_to_orgs(self) -> None:
        c = make_mock_connector()
        _with_orgs(c)
        c.runtime.search_call.return_value = ok_response([
            SimpleNamespace(full_name="acme/widgets"),
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        assert resp.success is True
        # Must go through search_call (the 30 req/min pacer), not ds_call.
        called_method = c.runtime.search_call.call_args.args[0]
        assert called_method is c.data_source.search_repositories
        assert resp.options[0].id == "acme/widgets"

    async def test_search_query_is_restricted_to_accessible_orgs(self) -> None:
        """The scoped pass runs FIRST so a user's own repositories always rank
        above same-named public ones."""
        c = make_mock_connector()
        _with_orgs(c, ["acme", "other"])
        c.runtime.search_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        query = c.runtime.search_call.call_args_list[0].args[1]
        assert "org:acme" in query
        assert "org:other" in query

    async def test_public_repos_are_offered_when_the_scoped_pass_leaves_room(self) -> None:
        """A public repo the token cannot push to now syncs — its visibility
        grant is the whole ACL — so the picker must be able to offer it. The
        user's own repo still comes first."""
        c = make_mock_connector()
        _with_orgs(c, ["Personal-test-dash"])
        c.runtime.search_call.side_effect = [
            ok_response([SimpleNamespace(full_name="Personal-test-dash/pipeshub-ai")]),
            ok_response([
                SimpleNamespace(full_name="Personal-test-dash/pipeshub-ai"),  # dupe, dropped
                SimpleNamespace(full_name="pipeshub-ai/pipeshub-ai"),
            ]),
        ]
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="pipeshub-ai")

        assert [o.id for o in resp.options] == [
            "Personal-test-dash/pipeshub-ai",
            "pipeshub-ai/pipeshub-ai",
        ]
        assert "org:Personal-test-dash" in c.runtime.search_call.call_args_list[0].args[1]
        assert "org:" not in c.runtime.search_call.call_args_list[1].args[1]

    async def test_a_full_scoped_page_skips_the_public_search(self) -> None:
        """The Search API pool is 30 req/min, so the second call only happens
        when the first leaves room on the page."""
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        c.runtime.search_call.return_value = ok_response(
            [SimpleNamespace(full_name=f"acme/repo{i}") for i in range(60)]
        )
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="repo")

        assert c.runtime.search_call.await_count == 1

    async def test_public_search_failure_still_returns_the_scoped_rows(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        c.runtime.search_call.side_effect = [
            ok_response([SimpleNamespace(full_name="acme/widgets")]),
            failed_response("rate limited"),
        ]
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        assert resp.success is True
        assert [o.id for o in resp.options] == ["acme/widgets"]

    async def test_selected_orgs_win_over_saved_filters(self) -> None:
        """The admin picks orgs and repos in one sitting, so the org rows they
        just ticked are not saved yet — the in-flight selection must win."""
        c = make_mock_connector()
        _with_orgs(c, ["stale-saved-org"])
        c._request_filter_context_group_paths = ["freshly-picked-org"]
        c.runtime.search_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        query = c.runtime.search_call.call_args_list[0].args[1]
        assert "org:freshly-picked-org" in query
        assert "stale-saved-org" not in query

    async def test_excluded_orgs_are_removed_from_scope(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["keep-me", "drop-me"])
        c._request_filter_context_group_paths = []
        c._request_filter_context_exclude_group_paths = ["Drop-Me"]  # case-insensitive
        c.runtime.search_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        query = c.runtime.search_call.call_args_list[0].args[1]
        assert "org:keep-me" in query
        assert "drop-me" not in query.casefold().replace("keep-me", "")

    async def test_unsearched_picker_also_honours_selected_orgs(self) -> None:
        """Both picker branches must agree — otherwise typing a search changes
        which orgs the results come from."""
        c = make_mock_connector()
        _with_orgs(c, ["stale-saved-org"])
        c._request_filter_context_group_paths = ["freshly-picked-org"]
        c.runtime.ds_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        listed_orgs = [
            call.args[1]
            for call in c.runtime.ds_call.call_args_list
            if call.args and call.args[0] is c.data_source.list_org_repos
        ]
        assert listed_orgs == ["freshly-picked-org"]

    async def test_no_search_pages_scoped_repos(self) -> None:
        c = make_mock_connector()
        _with_orgs(c)
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(full_name=f"acme/repo{i}") for i in range(3)
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, page=1, limit=2)

        assert len(resp.options) == 2
        assert resp.has_more is True

    async def test_repo_list_failure_returns_error_response(self) -> None:
        c = make_mock_connector()
        _with_orgs(c)
        c.runtime.ds_call.return_value = failed_response("boom")
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        assert resp.success is False


class TestGetFilterOptionsDispatch:
    async def test_unsupported_key_raises_value_error(self) -> None:
        """ValueError for an unknown filter key is deliberately re-raised (not
        swallowed into a generic failure response) so a caller passing a typo'd
        key gets a loud signal rather than a silently empty picker."""
        c = make_mock_connector()
        helper = FiltersHelper(c)

        with pytest.raises(ValueError):
            await helper.get_filter_options("not-a-real-key")

    async def test_uninitialized_data_source_short_circuits(self) -> None:
        c = make_mock_connector()
        c.data_source = None
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value)

        assert resp.success is False
        assert "not initialized" in (resp.message or "").lower()

    async def test_unexpected_exception_returns_failure_response(self) -> None:
        c = make_mock_connector()
        helper = FiltersHelper(c)
        helper._org_filter_options = AsyncMock(side_effect=RuntimeError("picker exploded"))

        resp = await helper.get_filter_options(SyncFilterKey.ORG_IDS.value)

        assert resp.success is False
        assert "picker exploded" in (resp.message or "")


class TestClampPerPage:
    def test_invalid_and_non_positive_fall_back_to_20(self) -> None:
        from app.connectors.sources.github_teams.filters import _clamp_per_page

        assert _clamp_per_page("nope") == 20
        assert _clamp_per_page(0) == 20
        assert _clamp_per_page(-3) == 20

    def test_caps_at_max_per_page(self) -> None:
        from app.connectors.sources.github_teams.constants import _FILTER_OPTIONS_MAX_PER_PAGE
        from app.connectors.sources.github_teams.filters import _clamp_per_page

        assert _clamp_per_page(10_000) == _FILTER_OPTIONS_MAX_PER_PAGE


class TestRepoPickerEdgeCases:
    async def test_org_scope_failure_returns_error(self) -> None:
        c = make_mock_connector()
        c.users._resolve_target_orgs = AsyncMock(return_value=([], False))
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        assert resp.success is False
        assert "organizations" in (resp.message or "").lower()

    async def test_search_includes_user_qualifier_when_login_known(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        c._github_login = "octocat"
        c.runtime.search_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        query = c.runtime.search_call.call_args_list[0].args[1]
        assert "user:octocat" in query

    async def test_no_org_scope_lists_owner_repos(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, [])
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(full_name="me/personal"),
        ])
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        assert resp.success is True
        assert [o.id for o in resp.options] == ["me/personal"]
        assert c.runtime.ds_call.call_args.args[0] is c.data_source.list_user_repos

    async def test_multi_org_merge_and_pagination(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme", "other"])

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.list_org_repos:
                org = args[0]
                return ok_response([
                    SimpleNamespace(full_name=f"{org}/alpha"),
                    SimpleNamespace(full_name=f"{org}/zeta"),
                ])
            raise AssertionError(f"unexpected {method!r}")

        c.runtime.ds_call.side_effect = dispatch
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, page=1, limit=3)

        assert resp.success is True
        assert [o.id for o in resp.options] == ["acme/alpha", "acme/zeta", "other/alpha"]
        assert resp.has_more is True

    async def test_multi_org_all_failures_return_error(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme", "other"])
        c.runtime.ds_call.return_value = failed_response("403")
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        assert resp.success is False


class TestRepoPickerPagination:
    """Pins the pagination contracts: exact page size (no +1 over-fetch, which
    shifts GitHub's page offset and skips one row per boundary), full_name
    sort for stable multi-org merges, and cursor-exact search continuation."""

    async def test_single_source_requests_exact_page_size_and_sort(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        c.runtime.ds_call.return_value = ok_response(
            [SimpleNamespace(full_name=f"acme/r{i}") for i in range(20)]
        )
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, page=2, limit=20)

        kwargs = c.runtime.ds_call.call_args.kwargs
        assert kwargs["per_page"] == 20
        assert kwargs["page"] == 2
        assert kwargs["sort"] == "full_name"
        assert resp.has_more is True  # a full page implies more may exist

    async def test_single_source_short_page_ends_pagination(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        c.runtime.ds_call.return_value = ok_response(
            [SimpleNamespace(full_name="acme/only")]
        )
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, page=1, limit=20)

        assert [o.id for o in resp.options] == ["acme/only"]
        assert resp.has_more is False

    async def test_multi_org_pages_are_fetched_in_full_name_order(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme", "other"])
        c.runtime.ds_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        for call in c.runtime.ds_call.call_args_list:
            if call.args and call.args[0] is c.data_source.list_org_repos:
                assert call.kwargs["sort"] == "full_name"

    async def test_search_queries_include_forks(self) -> None:
        """GitHub Search silently drops forks by default; fork-heavy names
        (searching a well-known repo) would return a fraction of the matches."""
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        c.runtime.search_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="pipeshub-ai")

        for call in c.runtime.search_call.call_args_list:
            assert "fork:true" in call.args[1]

    @staticmethod
    def _own_plus_public(c: object) -> None:
        own = SimpleNamespace(full_name="acme/own")
        public = [SimpleNamespace(full_name=f"pub/repo{i:03d}") for i in range(100)]

        def dispatch(method: object, query: str, **kwargs: object) -> object:
            return ok_response([own] if "org:" in query else public)

        c.runtime.search_call.side_effect = dispatch

    async def test_search_cursor_continues_without_skips_or_dupes(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        self._own_plus_public(c)
        helper = FiltersHelper(c)

        first = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="repo", limit=3)
        assert [o.id for o in first.options] == ["acme/own", "pub/repo000", "pub/repo001"]
        assert first.has_more is True
        assert first.cursor

        second = await helper.get_filter_options(
            SyncFilterKey.REPO_IDS.value, search="repo", limit=3, page=2, cursor=first.cursor,
        )
        assert [o.id for o in second.options] == ["pub/repo002", "pub/repo003", "pub/repo004"]
        assert second.has_more is True

    async def test_search_page_fallback_without_cursor_replays_the_stream(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        self._own_plus_public(c)
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(
            SyncFilterKey.REPO_IDS.value, search="repo", limit=3, page=2,
        )
        assert [o.id for o in resp.options] == ["pub/repo002", "pub/repo003", "pub/repo004"]

    async def test_org_scope_is_cached_between_requests(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        c.runtime.ds_call.return_value = ok_response([])
        helper = FiltersHelper(c)

        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)
        await helper.get_filter_options(SyncFilterKey.REPO_IDS.value)

        assert c.users._resolve_target_orgs.await_count == 1

    async def test_scoped_search_failure_degrades_to_public_only(self) -> None:
        c = make_mock_connector()
        _with_orgs(c, ["acme"])
        public = [SimpleNamespace(full_name="pub/widgets")]
        c.runtime.search_call.side_effect = [failed_response("boom"), ok_response(public)]
        helper = FiltersHelper(c)

        resp = await helper.get_filter_options(SyncFilterKey.REPO_IDS.value, search="widgets")

        assert resp.success is True
        assert [o.id for o in resp.options] == ["pub/widgets"]
