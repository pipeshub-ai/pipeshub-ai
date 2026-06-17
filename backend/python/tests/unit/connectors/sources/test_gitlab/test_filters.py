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
