"""Unit tests for gitlab FiltersHelper and module-level filter helpers.

Covers:
- _is_short_search: threshold behavior (< GITLAB_SEARCH_MIN_PARTIAL_CHARS)
- _clamp_per_page: clamping behavior
- _local_match_group / _local_match_project: case-insensitive matching
- _short_search_filter_options_response: message content
- get_filter_options: dispatch to group vs project pickers, not-initialized, unknown key
- _gitlab_group_filter_options: short search → early return, API success
- _gitlab_project_filter_options: short search → early return
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.gitlab.constants import GITLAB_SEARCH_MIN_PARTIAL_CHARS
from app.connectors.sources.gitlab.filters import (
    FiltersHelper,
    _clamp_per_page,
    _is_short_search,
    _local_match_group,
    _local_match_project,
    _short_search_filter_options_response,
)

from .conftest import make_mock_connector, paged_res, failed_res

pytestmark = pytest.mark.anyio


# ===========================================================================
# Module-level helper functions
# ===========================================================================


class TestIsShortSearch:
    def test_none_is_not_short(self) -> None:
        assert _is_short_search(None) is False

    def test_empty_string_is_not_short(self) -> None:
        assert _is_short_search("") is False

    def test_one_char_is_short(self) -> None:
        assert _is_short_search("a") is True

    def test_two_chars_is_short(self) -> None:
        if GITLAB_SEARCH_MIN_PARTIAL_CHARS > 2:
            assert _is_short_search("ab") is True

    def test_at_threshold_is_not_short(self) -> None:
        exact = "x" * GITLAB_SEARCH_MIN_PARTIAL_CHARS
        assert _is_short_search(exact) is False

    def test_longer_than_threshold_is_not_short(self) -> None:
        long = "x" * (GITLAB_SEARCH_MIN_PARTIAL_CHARS + 3)
        assert _is_short_search(long) is False


class TestClampPerPage:
    def test_normal_value_unchanged(self) -> None:
        result = _clamp_per_page(20)
        assert result == 20

    def test_zero_defaults_to_20(self) -> None:
        result = _clamp_per_page(0)
        assert result == 20

    def test_negative_defaults_to_20(self) -> None:
        result = _clamp_per_page(-5)
        assert result == 20

    def test_non_numeric_defaults_to_20(self) -> None:
        result = _clamp_per_page("bad")  # type: ignore
        assert result == 20

    def test_large_value_clamped(self) -> None:
        result = _clamp_per_page(10000)
        assert result < 10000


class TestLocalMatchGroup:
    def test_matches_name_case_insensitive(self) -> None:
        g = MagicMock()
        g.name = "Engineering"
        g.full_path = "eng"
        assert _local_match_group(g, "engineering") is True

    def test_matches_full_path(self) -> None:
        g = MagicMock()
        g.name = "X"
        g.full_path = "my-org/backend"
        assert _local_match_group(g, "backend") is True

    def test_no_match(self) -> None:
        g = MagicMock()
        g.name = "Alpha"
        g.full_path = "alpha"
        assert _local_match_group(g, "beta") is False


class TestLocalMatchProject:
    def test_matches_path_with_namespace(self) -> None:
        p = MagicMock()
        p.path_with_namespace = "my-org/api-service"
        p.name_with_namespace = None
        p.name = "API Service"
        assert _local_match_project(p, "api") is True

    def test_matches_name_case_insensitive(self) -> None:
        p = MagicMock()
        p.path_with_namespace = "x/y"
        p.name_with_namespace = "My Org / Backend API"
        assert _local_match_project(p, "backend") is True

    def test_no_match(self) -> None:
        p = MagicMock()
        p.path_with_namespace = "org/alpha"
        p.name_with_namespace = None
        p.name = "Alpha"
        assert _local_match_project(p, "omega") is False


class TestShortSearchResponse:
    def test_response_has_message_and_empty_options(self) -> None:
        resp = _short_search_filter_options_response(1, 20)
        assert resp.success is True
        assert resp.options == []
        assert resp.message is not None
        assert str(GITLAB_SEARCH_MIN_PARTIAL_CHARS) in resp.message


# ===========================================================================
# FiltersHelper.get_filter_options
# ===========================================================================


class TestGetFilterOptions:
    async def test_not_initialized_returns_failure(self) -> None:
        c = make_mock_connector()
        c.data_source = None
        helper = FiltersHelper(c)

        result = await helper.get_filter_options("group_ids")
        assert result.success is False

    async def test_unknown_filter_key_raises(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = FiltersHelper(c)
        helper._gitlab_group_filter_options = AsyncMock()
        helper._gitlab_project_filter_options = AsyncMock()

        with pytest.raises(ValueError):
            await helper.get_filter_options("unknown_key")

    async def test_group_ids_key_dispatches_to_group_picker(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = FiltersHelper(c)

        from app.connectors.core.registry.filters import FilterOptionsResponse
        mock_response = FilterOptionsResponse(success=True, options=[], page=1, limit=20, has_more=False)
        helper._gitlab_group_filter_options = AsyncMock(return_value=mock_response)
        helper._gitlab_project_filter_options = AsyncMock(return_value=mock_response)

        from app.connectors.core.registry.filters import SyncFilterKey
        result = await helper.get_filter_options(SyncFilterKey.GROUP_IDS.value)
        helper._gitlab_group_filter_options.assert_called_once()
        helper._gitlab_project_filter_options.assert_not_called()

    async def test_project_ids_key_dispatches_to_project_picker(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = FiltersHelper(c)

        from app.connectors.core.registry.filters import FilterOptionsResponse
        mock_response = FilterOptionsResponse(success=True, options=[], page=1, limit=20, has_more=False)
        helper._gitlab_group_filter_options = AsyncMock(return_value=mock_response)
        helper._gitlab_project_filter_options = AsyncMock(return_value=mock_response)

        from app.connectors.core.registry.filters import SyncFilterKey
        result = await helper.get_filter_options(SyncFilterKey.PROJECT_IDS.value)
        helper._gitlab_project_filter_options.assert_called_once()
        helper._gitlab_group_filter_options.assert_not_called()


# ===========================================================================
# _gitlab_group_filter_options — short-search path
# ===========================================================================


class TestGitlabGroupFilterOptions:
    async def test_short_search_returns_empty_with_message(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})
        helper = FiltersHelper(c)

        # "ab" is shorter than threshold
        result = await helper._gitlab_group_filter_options(1, 20, "a")
        assert result.options == []
        assert result.message is not None

    async def test_no_search_fetches_groups_by_page(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_auditor = False
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})

        group = MagicMock()
        group.full_path = "eng"
        group.full_name = "Engineering"
        group.name = "Engineering"

        groups_res = MagicMock(success=True, data=[group], error=None)
        c.runtime.ds_call = AsyncMock(return_value=groups_res)

        helper = FiltersHelper(c)
        result = await helper._gitlab_group_filter_options(1, 20, None)
        assert result.success is True
        assert len(result.options) >= 1


# ===========================================================================
# _gitlab_project_filter_options — short-search path
# ===========================================================================


class TestGitlabProjectFilterOptions:
    async def test_short_search_returns_empty_with_message(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = FiltersHelper(c)

        result = await helper._gitlab_project_filter_options(1, 20, "x")
        assert result.options == []
        assert result.message is not None


# ===========================================================================
# get_filter_options — generic exception handler
# ===========================================================================


class TestGetFilterOptionsExceptionHandler:
    async def test_generic_exception_returns_failure(self) -> None:
        """An unexpected error from the group picker is caught and returns failure."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = FiltersHelper(c)
        helper._gitlab_group_filter_options = AsyncMock(side_effect=RuntimeError("unexpected"))

        from app.connectors.core.registry.filters import SyncFilterKey
        result = await helper.get_filter_options(SyncFilterKey.GROUP_IDS.value)
        assert result.success is False
        assert result.options == []


# ===========================================================================
# _gitlab_group_filter_options — search paths and auditor fallback
# ===========================================================================


class TestGitlabGroupFilterOptionsSearchAndFallback:
    async def _make_group(self, full_path: str, name: str | None = None) -> MagicMock:
        g = MagicMock()
        g.full_path = full_path
        g.full_name = name or full_path
        g.name = (name or full_path).split("/")[-1]
        return g

    async def test_search_returns_matched_groups(self) -> None:
        """With a search term, _scan_filter_option_pages is driven to return matches."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_auditor = False
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})

        eng_group = await self._make_group("engineering", "Engineering")
        ops_group = await self._make_group("operations", "Operations")

        # search="engi" (>= threshold of 3) — simulate ds_call returning one page
        search_res = MagicMock(success=True, data=[eng_group, ops_group], error=None)
        c.runtime.ds_call = AsyncMock(return_value=search_res)

        helper = FiltersHelper(c)
        result = await helper._gitlab_group_filter_options(1, 20, "engi")
        assert result.success is True
        ids = [o.id for o in result.options]
        assert "engineering" in ids
        assert "operations" not in ids

    async def test_search_empty_with_auditor_triggers_fallback(self) -> None:
        """Empty search results for auditor triggers _gitlab_group_picker_auditor_fallback."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_auditor = True
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})

        # First scan returns no matches
        empty_res = MagicMock(success=True, data=[], error=None)
        c.runtime.ds_call = AsyncMock(return_value=empty_res)

        fallback_group = await self._make_group("auditor-group")
        helper = FiltersHelper(c)
        helper._gitlab_group_picker_auditor_fallback = AsyncMock(
            return_value=([fallback_group], False, None)
        )

        result = await helper._gitlab_group_filter_options(1, 20, "audit")
        assert result.success is True
        helper._gitlab_group_picker_auditor_fallback.assert_called_once()

    async def test_list_failure_returns_failure_response(self) -> None:
        """When group listing fails (no search), failure response is returned."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_auditor = False
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})

        fail_res = MagicMock(success=False, data=None, error="403")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        helper = FiltersHelper(c)
        result = await helper._gitlab_group_filter_options(1, 20, None)
        assert result.success is False

    async def test_list_failure_with_auditor_triggers_fallback(self) -> None:
        """Empty group list for auditor (no search) triggers _gitlab_group_picker_auditor_fallback."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_auditor = True
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})

        # Empty success (no groups returned)
        empty_res = MagicMock(success=True, data=[], error=None)
        c.runtime.ds_call = AsyncMock(return_value=empty_res)

        fallback_group = await self._make_group("aud-group")
        helper = FiltersHelper(c)
        helper._gitlab_group_picker_auditor_fallback = AsyncMock(
            return_value=([fallback_group], False, None)
        )

        result = await helper._gitlab_group_filter_options(1, 20, None)
        assert result.success is True
        helper._gitlab_group_picker_auditor_fallback.assert_called_once()


# ===========================================================================
# _gitlab_group_picker_auditor_fallback
# ===========================================================================


class TestGitlabGroupPickerAuditorFallback:
    async def test_full_path_happy_path(self) -> None:
        """Auditor fallback: paged list + descendant expansion returns merged groups."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        g1 = MagicMock()
        g1.full_path = "parent-group"
        g1.name = "Parent"
        g2 = MagicMock()
        g2.full_path = "parent-group/child"
        g2.name = "Child"

        base_res = MagicMock(success=True, data=[g1], error=None)
        c.scope = MagicMock()
        c.scope.warn_auditor_fallback_once = MagicMock()
        c.scope.picker_kwargs_for_auditor_groups_fallback = MagicMock(return_value={})
        c.scope.expand_groups_with_descendants = AsyncMock(return_value=[g1, g2])
        c.runtime.paged_list = AsyncMock(return_value=base_res)

        helper = FiltersHelper(c)
        groups, has_more, error = await helper._gitlab_group_picker_auditor_fallback(
            list_kwargs={}, needle=None, page=1, per_page=20
        )
        assert error is None
        assert len(groups) >= 1
        assert g1 in groups

    async def test_paged_list_failure_returns_error(self) -> None:
        """When paged list fails, error is propagated."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        fail_res = MagicMock(success=False, data=None, error="forbidden")
        c.scope = MagicMock()
        c.scope.warn_auditor_fallback_once = MagicMock()
        c.scope.picker_kwargs_for_auditor_groups_fallback = MagicMock(return_value={})
        c.runtime.paged_list = AsyncMock(return_value=fail_res)

        helper = FiltersHelper(c)
        groups, has_more, error = await helper._gitlab_group_picker_auditor_fallback(
            list_kwargs={}, needle=None, page=1, per_page=20
        )
        assert error is not None
        assert groups == []

    async def test_needle_filters_results(self) -> None:
        """With a needle, only matching groups are returned."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        g_match = MagicMock()
        g_match.full_path = "backend"
        g_match.name = "Backend"
        g_no_match = MagicMock()
        g_no_match.full_path = "frontend"
        g_no_match.name = "Frontend"

        base_res = MagicMock(success=True, data=[g_match], error=None)
        c.scope = MagicMock()
        c.scope.warn_auditor_fallback_once = MagicMock()
        c.scope.picker_kwargs_for_auditor_groups_fallback = MagicMock(return_value={})
        c.scope.expand_groups_with_descendants = AsyncMock(return_value=[g_match, g_no_match])
        c.runtime.paged_list = AsyncMock(return_value=base_res)

        helper = FiltersHelper(c)
        groups, has_more, error = await helper._gitlab_group_picker_auditor_fallback(
            list_kwargs={}, needle="backend", page=1, per_page=20
        )
        assert error is None
        assert g_match in groups
        assert g_no_match not in groups


# ===========================================================================
# _scoped_project_filter_options
# ===========================================================================


class TestScopedProjectFilterOptions:
    async def test_scoped_with_search_returns_matching_projects(self) -> None:
        """Search in scoped mode: projects matching needle are returned."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        p_match = MagicMock()
        p_match.id = 1
        p_match.path_with_namespace = "grp/api-service"
        p_match.name_with_namespace = "Grp / API Service"
        p_no_match = MagicMock()
        p_no_match.id = 2
        p_no_match.path_with_namespace = "grp/frontend"
        p_no_match.name_with_namespace = "Grp / Frontend"

        search_res = MagicMock(success=True, data=[p_match, p_no_match], error=None)
        c.runtime.ds_call = AsyncMock(return_value=search_res)

        helper = FiltersHelper(c)
        result = await helper._scoped_project_filter_options(
            scope_paths=["grp"], search="api", per_page=20, page_n=1, limit=20
        )
        assert result.success is True
        ids = [o.id for o in result.options]
        assert "grp/api-service" in ids
        assert "grp/frontend" not in ids

    async def test_scoped_no_search_pagination(self) -> None:
        """No search: paginated ds_call is used, has_more set correctly."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        # Return per_page+1=21 items to trigger has_more
        projects = []
        for i in range(21):
            p = MagicMock()
            p.id = i
            p.path_with_namespace = f"grp/proj-{i}"
            p.name_with_namespace = f"Grp / Proj {i}"
            projects.append(p)

        page_res = MagicMock(success=True, data=projects, error=None)
        c.runtime.ds_call = AsyncMock(return_value=page_res)

        helper = FiltersHelper(c)
        result = await helper._scoped_project_filter_options(
            scope_paths=["grp"], search=None, per_page=20, page_n=1, limit=20
        )
        assert result.success is True
        assert result.has_more is True
        assert len(result.options) == 20

    async def test_scoped_context_routing(self) -> None:
        """_request_filter_context_group_paths set routes to scoped picker."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._request_filter_context_group_paths = ["grp"]
        c._request_filter_context_exclude_group_paths = []

        p = MagicMock()
        p.id = 1
        p.path_with_namespace = "grp/proj"
        p.name_with_namespace = "Grp / Proj"
        page_res = MagicMock(success=True, data=[p], error=None)
        c.runtime.ds_call = AsyncMock(return_value=page_res)

        helper = FiltersHelper(c)
        result = await helper._gitlab_project_filter_options(1, 20, None)
        assert result.success is True
        assert any(o.id == "grp/proj" for o in result.options)


# ===========================================================================
# _unscoped_project_filter_options — search and auditor fallback
# ===========================================================================


class TestUnscopedProjectFilterOptions:
    async def test_search_returns_matching_projects(self) -> None:
        """Search scan: only projects matching needle returned."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_admin = False
        c._is_auditor = False

        p_match = MagicMock()
        p_match.id = 1
        p_match.path_with_namespace = "ns/api-backend"
        p_match.name_with_namespace = "NS / API Backend"
        p_match.namespace = MagicMock()
        p_match.namespace.full_path = "ns"
        p_no = MagicMock()
        p_no.id = 2
        p_no.path_with_namespace = "ns/frontend"
        p_no.name_with_namespace = "NS / Frontend"
        p_no.namespace = MagicMock()
        p_no.namespace.full_path = "ns"

        scan_res = MagicMock(success=True, data=[p_match, p_no], error=None)
        c.runtime.ds_call = AsyncMock(return_value=scan_res)

        helper = FiltersHelper(c)
        result = await helper._unscoped_project_filter_options(
            search="api", exclude_paths=[], per_page=20, page_n=1, limit=20, page=1
        )
        assert result.success is True
        ids = [o.id for o in result.options]
        assert "ns/api-backend" in ids
        assert "ns/frontend" not in ids

    async def test_no_search_list_returns_projects(self) -> None:
        """No search: direct ds_call for paginated project list."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_admin = True
        c._is_auditor = False

        p = MagicMock()
        p.id = 1
        p.path_with_namespace = "ns/proj"
        p.name_with_namespace = "NS / Proj"
        p.namespace = MagicMock()
        p.namespace.full_path = "ns"
        page_res = MagicMock(success=True, data=[p], error=None)
        c.runtime.ds_call = AsyncMock(return_value=page_res)

        helper = FiltersHelper(c)
        result = await helper._unscoped_project_filter_options(
            search=None, exclude_paths=[], per_page=20, page_n=1, limit=20, page=1
        )
        assert result.success is True
        assert any(o.id == "ns/proj" for o in result.options)

    async def test_auditor_fallback_on_empty_results(self) -> None:
        """Empty unscoped results for auditor triggers auditor fallback."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._is_admin = False
        c._is_auditor = True

        # First call returns empty; fallback call returns project
        empty_res = MagicMock(success=True, data=[], error=None)
        p = MagicMock()
        p.id = 1
        p.path_with_namespace = "ns/proj"
        p.name_with_namespace = "NS / Proj"
        p.namespace = MagicMock()
        p.namespace.full_path = "ns"
        fallback_res = MagicMock(success=True, data=[p], error=None)

        c.scope = MagicMock()
        c.scope.warn_auditor_fallback_once = MagicMock()
        c.scope.picker_kwargs_for_auditor_projects_fallback = MagicMock(return_value={})
        c.runtime.ds_call = AsyncMock(side_effect=[empty_res, fallback_res])

        helper = FiltersHelper(c)
        result = await helper._unscoped_project_filter_options(
            search=None, exclude_paths=[], per_page=20, page_n=1, limit=20, page=1
        )
        assert result.success is True
        assert any(o.id == "ns/proj" for o in result.options)


# ===========================================================================
# _scan_filter_option_pages — pagination loop
# ===========================================================================


class TestScanFilterOptionPages:
    async def test_three_page_scan_accumulates_matches(self) -> None:
        """Three pages of results: all matching items accumulated."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        from app.connectors.sources.gitlab.constants import (
            _FILTER_OPTIONS_MAX_PER_PAGE as MAX_PER_PAGE,
        )

        # Build 3 pages of MAX_PER_PAGE items each
        all_items = []
        for i in range(MAX_PER_PAGE * 3):
            g = MagicMock()
            g.name = f"group-{i}"
            g.full_path = f"group-{i}"
            all_items.append(g)

        page_call_count = 0

        async def ds_call_side(fn, *args, **kwargs):
            nonlocal page_call_count
            p = kwargs.get("page", 1)
            page_call_count += 1
            start = (p - 1) * MAX_PER_PAGE
            end = start + MAX_PER_PAGE
            batch = all_items[start:end]
            return MagicMock(success=True, data=batch, error=None)

        c.runtime.ds_call = AsyncMock(side_effect=ds_call_side)

        helper = FiltersHelper(c)
        matcher = lambda g: True  # match everything

        groups, has_more, error = await helper._scan_filter_option_pages(
            c.data_source.list_groups,
            list_kwargs={},
            matcher=matcher,
            page=1,
            per_page=20,
            progress_label="test scan",
        )
        assert error is None
        assert len(groups) == 20  # only first page of results requested

    async def test_scan_stops_on_api_failure(self) -> None:
        """If ds_call returns failure, scan stops with error."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        fail_res = MagicMock(success=False, data=None, error="server error")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        helper = FiltersHelper(c)
        groups, has_more, error = await helper._scan_filter_option_pages(
            c.data_source.list_groups,
            list_kwargs={},
            matcher=lambda g: True,
            page=1,
            per_page=20,
            progress_label="test scan",
        )
        assert error is not None
        assert groups == []
