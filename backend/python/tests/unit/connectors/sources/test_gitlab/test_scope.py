"""Unit tests for gitlab ScopeHelper.

Covers:
- list_projects_scope_kwargs: admin/auditor vs member
- list_groups_scope_kwargs: admin/auditor vs member
- warn_auditor_fallback_once: single-fire guard
- paged_list_groups_with_role_fallback: primary success, auditor empty fallback
- paged_list_projects_with_role_fallback: primary success, auditor empty fallback
- expand_groups_with_descendants: dedup by full_path, sub-group traversal
- picker_kwargs_for_auditor_groups_fallback / _projects_fallback (static)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.gitlab.scope import ScopeHelper
from app.sources.client.gitlab.gitlab import GitLabResponse

from .conftest import make_mock_connector, paged_res, failed_res

pytestmark = pytest.mark.anyio


def _make_scope(is_admin: bool = False, is_auditor: bool = False) -> tuple[MagicMock, ScopeHelper]:
    c = make_mock_connector()
    c._is_admin = is_admin
    c._is_auditor = is_auditor
    c._auditor_fallback_warned = False
    scope = ScopeHelper(c)
    return c, scope


def _group(full_path: str, gid: int = 1) -> MagicMock:
    g = MagicMock()
    g.full_path = full_path
    g.id = gid
    return g


# ===========================================================================
# Scope kwargs
# ===========================================================================


class TestListProjectsScopeKwargs:
    def test_admin_returns_empty_dict(self) -> None:
        _, scope = _make_scope(is_admin=True)
        assert scope.list_projects_scope_kwargs() == {}

    def test_auditor_returns_empty_dict(self) -> None:
        _, scope = _make_scope(is_auditor=True)
        assert scope.list_projects_scope_kwargs() == {}

    def test_regular_member_returns_membership_true(self) -> None:
        _, scope = _make_scope()
        assert scope.list_projects_scope_kwargs() == {"membership": True}


class TestListGroupsScopeKwargs:
    def test_admin_returns_all_available(self) -> None:
        _, scope = _make_scope(is_admin=True)
        assert scope.list_groups_scope_kwargs() == {"all_available": True}

    def test_auditor_returns_all_available(self) -> None:
        _, scope = _make_scope(is_auditor=True)
        assert scope.list_groups_scope_kwargs() == {"all_available": True}

    def test_regular_member_returns_min_access_level(self) -> None:
        _, scope = _make_scope()
        assert scope.list_groups_scope_kwargs() == {"min_access_level": 10}


# ===========================================================================
# warn_auditor_fallback_once
# ===========================================================================


class TestWarnAuditorFallbackOnce:
    def test_logs_warning_first_call(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        scope.warn_auditor_fallback_once("groups")
        c.logger.warning.assert_called_once()
        assert c._auditor_fallback_warned is True

    def test_does_not_log_twice(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        scope.warn_auditor_fallback_once("groups")
        scope.warn_auditor_fallback_once("groups")
        assert c.logger.warning.call_count == 1

    def test_already_warned_skips_log(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        c._auditor_fallback_warned = True
        scope.warn_auditor_fallback_once("projects")
        c.logger.warning.assert_not_called()


# ===========================================================================
# paged_list_groups_with_role_fallback
# ===========================================================================


class TestPagedListGroupsWithRoleFallback:
    async def test_non_auditor_returns_primary_result(self) -> None:
        c, scope = _make_scope(is_auditor=False)
        primary_result = paged_res([_group("eng")])
        c.runtime.paged_list = AsyncMock(return_value=primary_result)

        res = await scope.paged_list_groups_with_role_fallback(progress_label="test")
        assert res.success is True
        assert len(res.data) == 1
        c.runtime.paged_list.assert_called_once()

    async def test_auditor_primary_returns_data_no_fallback(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        primary_result = paged_res([_group("eng")])
        c.runtime.paged_list = AsyncMock(return_value=primary_result)

        res = await scope.paged_list_groups_with_role_fallback(progress_label="test")
        assert res.success is True
        c.runtime.paged_list.assert_called_once()

    async def test_auditor_empty_primary_triggers_fallback(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        empty_result = paged_res([])
        fallback_result = paged_res([_group("eng")])

        scope.expand_groups_with_descendants = AsyncMock(
            return_value=[_group("eng"), _group("eng/sub")]
        )
        c.runtime.paged_list = AsyncMock(side_effect=[empty_result, fallback_result])

        res = await scope.paged_list_groups_with_role_fallback(progress_label="test")
        assert c.runtime.paged_list.call_count == 2
        # fallback triggers descendant expansion
        scope.expand_groups_with_descendants.assert_called_once()

    async def test_caller_override_disables_auditor_fallback(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        empty_result = paged_res([])
        c.runtime.paged_list = AsyncMock(return_value=empty_result)

        # Caller supplies min_access_level — overrides auditor scope
        res = await scope.paged_list_groups_with_role_fallback(
            progress_label="test", min_access_level=50
        )
        c.runtime.paged_list.assert_called_once()

    async def test_auditor_primary_failure_returned_as_is(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        fail_result = failed_res("network error")
        c.runtime.paged_list = AsyncMock(return_value=fail_result)

        res = await scope.paged_list_groups_with_role_fallback(progress_label="test")
        assert res.success is False


# ===========================================================================
# paged_list_projects_with_role_fallback
# ===========================================================================


class TestPagedListProjectsWithRoleFallback:
    async def test_non_auditor_primary_result_returned(self) -> None:
        c, scope = _make_scope(is_auditor=False)
        c.runtime.paged_list = AsyncMock(return_value=paged_res([MagicMock()]))

        res = await scope.paged_list_projects_with_role_fallback(progress_label="test")
        assert res.success is True
        c.runtime.paged_list.assert_called_once()

    async def test_auditor_empty_primary_triggers_membership_fallback(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        empty = paged_res([])
        fallback_data = paged_res([MagicMock()])
        c.runtime.paged_list = AsyncMock(side_effect=[empty, fallback_data])

        res = await scope.paged_list_projects_with_role_fallback(progress_label="test")
        assert c.runtime.paged_list.call_count == 2
        # Second call should use membership=True
        second_call_kwargs = c.runtime.paged_list.call_args_list[1].kwargs
        assert second_call_kwargs.get("membership") is True

    async def test_auditor_with_data_no_fallback(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        c.runtime.paged_list = AsyncMock(return_value=paged_res([MagicMock()]))

        res = await scope.paged_list_projects_with_role_fallback(progress_label="test")
        c.runtime.paged_list.assert_called_once()


# ===========================================================================
# expand_groups_with_descendants
# ===========================================================================


class TestExpandGroupsWithDescendants:
    async def test_empty_input_returns_empty(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        result = await scope.expand_groups_with_descendants(
            [], progress_label="test"
        )
        assert result == []

    async def test_dedups_by_full_path(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        g1 = _group("eng", gid=1)
        g2 = _group("eng", gid=2)  # same full_path, different object
        c.runtime.paged_list = AsyncMock(return_value=paged_res([]))

        result = await scope.expand_groups_with_descendants(
            [g1, g2], progress_label="test"
        )
        # Only one 'eng' group
        paths = [g.full_path for g in result]
        assert paths.count("eng") == 1

    async def test_fetches_descendant_groups(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        parent = _group("eng", gid=1)
        child = _group("eng/backend", gid=2)
        c.runtime.paged_list = AsyncMock(return_value=paged_res([child]))

        result = await scope.expand_groups_with_descendants(
            [parent], progress_label="test"
        )
        assert len(result) == 2
        paths = {g.full_path for g in result}
        assert "eng" in paths
        assert "eng/backend" in paths

    async def test_descendant_fetch_failure_skips_gracefully(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        parent = _group("eng", gid=1)
        c.runtime.paged_list = AsyncMock(return_value=failed_res("forbidden"))

        result = await scope.expand_groups_with_descendants(
            [parent], progress_label="test"
        )
        # Parent still included; descendant walk failed but not raised
        assert len(result) == 1

    async def test_descendant_dedup_across_multiple_parents(self) -> None:
        c, scope = _make_scope(is_auditor=True)
        p1 = _group("eng", gid=1)
        p2 = _group("ops", gid=2)
        shared_child = _group("shared/team", gid=3)
        # Both parents return the same child
        c.runtime.paged_list = AsyncMock(return_value=paged_res([shared_child]))

        result = await scope.expand_groups_with_descendants(
            [p1, p2], progress_label="test"
        )
        paths = [g.full_path for g in result]
        assert paths.count("shared/team") == 1


# ===========================================================================
# Static picker fallback helpers
# ===========================================================================


class TestPickerFallbackKwargs:
    def test_auditor_groups_fallback_replaces_all_available(self) -> None:
        kwargs = {"all_available": True, "order_by": "name"}
        result = ScopeHelper.picker_kwargs_for_auditor_groups_fallback(kwargs)
        assert "all_available" not in result
        assert result["min_access_level"] == 10
        assert result["order_by"] == "name"

    def test_auditor_projects_fallback_adds_min_access_level(self) -> None:
        kwargs = {"order_by": "name"}
        result = ScopeHelper.picker_kwargs_for_auditor_projects_fallback(kwargs)
        assert result["min_access_level"] == 10
        assert result["order_by"] == "name"
