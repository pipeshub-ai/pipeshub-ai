"""Unit tests for gitlab ProjectsSync and module-level namespace helpers.

Covers:
- _namespace_full_path, _namespace_is_group, _namespace_under_any_prefix,
  _longest_matching_group_path: pure static helpers
- _resolve_projects_with_filters: PROJECT_IDS IN, GROUP_IDS IN, NOT_IN, unscoped
- _sync_projects: error isolation — one project failure does not abort others
- _create_permission_from_principal: user found, user missing + pseudo-group
- _transform_restrictions_to_permissions: wires through to _create_permission
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.gitlab.projects import (
    ProjectsSync,
    _longest_matching_group_path,
    _namespace_full_path,
    _namespace_is_group,
    _namespace_under_any_prefix,
)

from .conftest import make_mock_connector, paged_res, failed_res

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Project factory helpers
# ---------------------------------------------------------------------------

def _project(pid: int, path: str, ns_kind: str = "group", ns_path: str | None = None) -> MagicMock:
    p = MagicMock()
    p.id = pid
    p.path_with_namespace = path
    p.default_branch = "main"
    ns = MagicMock()
    ns.kind = ns_kind
    ns.full_path = ns_path or path.rsplit("/", 1)[0]
    p.namespace = ns
    return p


def _member(uid: int = 1, access_level: int = 40) -> MagicMock:
    m = MagicMock()
    m.id = uid
    m.access_level = access_level
    return m


def _make_sync_filter(op: str, values: list) -> MagicMock:
    f = MagicMock()
    f.is_empty.return_value = not values
    f.operator = op
    f.value = values
    return f


# ===========================================================================
# Static helper tests
# ===========================================================================


class TestNamespaceFullPath:
    def test_returns_full_path_from_namespace(self) -> None:
        p = _project(1, "eng/proj", ns_path="eng")
        assert _namespace_full_path(p) == "eng"

    def test_returns_none_when_no_namespace(self) -> None:
        p = MagicMock()
        p.namespace = None
        assert _namespace_full_path(p) is None

    def test_returns_none_when_no_full_path(self) -> None:
        p = MagicMock()
        p.namespace = MagicMock()
        p.namespace.full_path = None
        assert _namespace_full_path(p) is None

    def test_dict_namespace(self) -> None:
        p = MagicMock()
        p.namespace = {"full_path": "my-org"}
        assert _namespace_full_path(p) == "my-org"


class TestNamespaceIsGroup:
    def test_group_kind_returns_true(self) -> None:
        p = _project(1, "eng/proj", ns_kind="group")
        assert _namespace_is_group(p) is True

    def test_user_kind_returns_false(self) -> None:
        p = _project(1, "user/proj", ns_kind="user")
        assert _namespace_is_group(p) is False

    def test_no_namespace_returns_false(self) -> None:
        p = MagicMock()
        p.namespace = None
        assert _namespace_is_group(p) is False


class TestNamespaceUnderAnyPrefix:
    def test_exact_match(self) -> None:
        assert _namespace_under_any_prefix("eng", ["eng"]) is True

    def test_child_match(self) -> None:
        assert _namespace_under_any_prefix("eng/backend", ["eng"]) is True

    def test_no_match(self) -> None:
        assert _namespace_under_any_prefix("other", ["eng"]) is False

    def test_none_namespace(self) -> None:
        assert _namespace_under_any_prefix(None, ["eng"]) is False

    def test_partial_prefix_no_match(self) -> None:
        # "engineering" should NOT match prefix "eng" (no trailing slash boundary)
        assert _namespace_under_any_prefix("engineering", ["eng"]) is False


class TestLongestMatchingGroupPath:
    def test_returns_longest_matching_prefix(self) -> None:
        result = _longest_matching_group_path("eng/backend/api", ["eng", "eng/backend"])
        assert result == "eng/backend"

    def test_no_match_returns_none(self) -> None:
        assert _longest_matching_group_path("other/team", ["eng"]) is None

    def test_empty_inputs(self) -> None:
        assert _longest_matching_group_path(None, ["eng"]) is None
        assert _longest_matching_group_path("eng", []) is None


# ===========================================================================
# _resolve_projects_with_filters
# ===========================================================================


class TestResolveProjectsWithFilters:
    async def test_project_ids_in_fetches_specific_projects(self) -> None:
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        proj_filter = _make_sync_filter("in", ["eng/proj-a"])
        c.sync_filters = {SyncFilterKey.PROJECT_IDS: proj_filter}
        c.data_source = MagicMock()

        proj = _project(1, "eng/proj-a")
        res = MagicMock(success=True, data=proj, error=None)
        c.runtime.ds_call = AsyncMock(return_value=res)

        projects_sync = ProjectsSync(c)
        projects_sync._build_included_group_hierarchy = AsyncMock(return_value=[])

        result = await projects_sync._resolve_projects_with_filters()
        assert len(result) == 1

    async def test_group_ids_in_expands_to_projects(self) -> None:
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        grp_filter = _make_sync_filter("in", ["eng"])
        c.sync_filters = {SyncFilterKey.GROUP_IDS: grp_filter}
        c.data_source = MagicMock()

        p1 = _project(1, "eng/proj-a")
        p2 = _project(2, "eng/sub/proj-b")
        c.runtime.paged_list = AsyncMock(return_value=paged_res([p1, p2]))

        projects_sync = ProjectsSync(c)
        projects_sync._build_included_group_hierarchy = AsyncMock(return_value=[])

        result = await projects_sync._resolve_projects_with_filters()
        assert len(result) == 2

    async def test_no_filters_lists_all_projects(self) -> None:
        c = make_mock_connector()
        c.sync_filters = None
        c.data_source = MagicMock()

        p1 = _project(1, "ns/proj")
        c.scope = MagicMock()
        c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=paged_res([p1]))

        projects_sync = ProjectsSync(c)
        projects_sync._build_included_group_hierarchy = AsyncMock(return_value=[])

        result = await projects_sync._resolve_projects_with_filters()
        assert len(result) >= 1

    async def test_project_ids_not_in_excludes_project(self) -> None:
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        proj_filter = _make_sync_filter("not_in", ["eng/excluded"])
        c.sync_filters = {SyncFilterKey.PROJECT_IDS: proj_filter}
        c.data_source = MagicMock()

        p1 = _project(1, "eng/keep")
        p2 = _project(2, "eng/excluded")
        c.scope = MagicMock()
        c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=paged_res([p1, p2]))

        projects_sync = ProjectsSync(c)
        projects_sync._build_included_group_hierarchy = AsyncMock(return_value=[])

        result = await projects_sync._resolve_projects_with_filters()
        paths = [p.path_with_namespace for p in result]
        assert "eng/excluded" not in paths


# ===========================================================================
# _sync_projects error isolation
# ===========================================================================


class TestSyncProjectsErrorIsolation:
    async def test_single_project_failure_does_not_abort_others(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()

        p1 = _project(1, "eng/proj-a")
        p2 = _project(2, "eng/proj-b")

        projects_sync = ProjectsSync(c)
        projects_sync._resolve_projects_with_filters = AsyncMock(return_value=[p1, p2])
        projects_sync._sync_project_members_as_pseudo = AsyncMock(
            side_effect=[Exception("boom for proj-a"), None]
        )

        c.issues = MagicMock()
        c.issues.fetch_issues_batched = AsyncMock()
        c.merge_requests = MagicMock()
        c.merge_requests.fetch_prs_batched = AsyncMock()
        c.repos = MagicMock()
        c.repos.run = AsyncMock()

        await projects_sync._sync_projects()
        # Both projects should have been attempted (issues/MRs for p2 should be called)
        assert c.issues.fetch_issues_batched.call_count >= 1


# ===========================================================================
# _create_permission_from_principal
# ===========================================================================


class TestCreatePermissionFromPrincipal:
    async def test_user_found_returns_user_permission(self) -> None:
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)

        user = MagicMock()
        user.email = "found@example.com"

        tx_store = MagicMock()
        tx_store.get_user_by_source_id = AsyncMock(return_value=user)
        tx_store.get_user_group_by_external_id = AsyncMock(return_value=None)

        context_manager = MagicMock()
        context_manager.__aenter__ = AsyncMock(return_value=tx_store)
        context_manager.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=context_manager)

        from app.models.permission import EntityType, PermissionType
        result = await projects_sync._create_permission_from_principal(
            EntityType.USER.value, "user-123", PermissionType.OWNER.value,
        )
        assert result is not None
        assert result.email == "found@example.com"

    async def test_user_not_found_creates_pseudo_group(self) -> None:
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)

        tx_store = MagicMock()
        tx_store.get_user_by_source_id = AsyncMock(return_value=None)
        tx_store.get_user_group_by_external_id = AsyncMock(return_value=None)

        context_manager = MagicMock()
        context_manager.__aenter__ = AsyncMock(return_value=tx_store)
        context_manager.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=context_manager)

        pseudo = MagicMock()
        pseudo.source_user_group_id = "pseudo-123"
        projects_sync._create_pseudo_group = AsyncMock(return_value=pseudo)
        c.data_entities_processor = MagicMock()
        c.data_entities_processor.org_id = "org-1"
        c.data_entities_processor.on_new_user_groups = AsyncMock()

        from app.models.permission import EntityType, PermissionType
        result = await projects_sync._create_permission_from_principal(
            EntityType.USER.value,
            "user-999",
            PermissionType.OWNER.value,
            create_pseudo_group_if_missing=True,
        )
        assert result is not None

    async def test_exception_returns_none(self) -> None:
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)

        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(side_effect=Exception("DB error"))

        from app.models.permission import EntityType, PermissionType
        result = await projects_sync._create_permission_from_principal(
            EntityType.USER.value, "user-999", PermissionType.OWNER.value,
        )
        assert result is None


# ===========================================================================
# sync_all_projects — checkpoint update
# ===========================================================================


class TestSyncAllProjectsCheckpoint:
    async def test_updates_checkpoint_after_sync(self) -> None:
        """sync_all_projects calls update_sync_point after _sync_projects."""
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)
        projects_sync._sync_projects = AsyncMock()

        await projects_sync.sync_all_projects()

        c.record_sync_point.update_sync_point.assert_called_once()
        call_kwargs = c.record_sync_point.update_sync_point.call_args[0]
        assert call_kwargs is not None


# ===========================================================================
# _sync_projects — empty project list warning
# ===========================================================================


class TestSyncProjectsEmptyWarning:
    async def test_warns_when_no_projects(self) -> None:
        """Warning logged when _resolve_projects_with_filters returns empty list."""
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)
        projects_sync._resolve_projects_with_filters = AsyncMock(return_value=[])

        await projects_sync._sync_projects()

        c.logger.warning.assert_called()


# ===========================================================================
# _build_included_group_hierarchy
# ===========================================================================


class TestBuildIncludedGroupHierarchy:
    async def test_grp_in_paths_returned(self) -> None:
        """GROUP_IDS IN: the listed group paths are included in hierarchy."""
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)

        p = _project(1, "eng/proj", ns_path="eng")
        result = await projects_sync._build_included_group_hierarchy(
            candidates=[p], grp_in=["eng"], grp_not_in=[], proj_in=[]
        )
        assert "eng" in result

    async def test_proj_in_namespace_paths_added(self) -> None:
        """PROJECT_IDS IN: namespace paths of candidate group projects added."""
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)

        p = _project(1, "eng/proj", ns_kind="group", ns_path="eng")
        result = await projects_sync._build_included_group_hierarchy(
            candidates=[p], grp_in=[], grp_not_in=[], proj_in=["eng/proj"]
        )
        assert "eng" in result

    async def test_grp_not_in_excludes_groups(self) -> None:
        """GROUP_IDS NOT_IN: excluded group paths are not in hierarchy."""
        c = make_mock_connector()

        g_ok = MagicMock()
        g_ok.full_path = "keep"
        g_excl = MagicMock()
        g_excl.full_path = "exclude"
        scope_res = MagicMock(success=True, data=[g_ok, g_excl], error=None)
        c.scope = MagicMock()
        c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=scope_res)

        p = _project(1, "keep/proj", ns_path="keep")
        projects_sync = ProjectsSync(c)
        result = await projects_sync._build_included_group_hierarchy(
            candidates=[p], grp_in=[], grp_not_in=["exclude"], proj_in=[]
        )
        assert "exclude" not in result
        assert "keep" in result


# ===========================================================================
# _ensure_gitlab_group_record_groups
# ===========================================================================


class TestEnsureGitlabGroupRecordGroups:
    async def test_happy_path_creates_record_group(self) -> None:
        """Group members found → RecordGroup created with permissions."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        group = MagicMock()
        group.full_path = "eng"
        group.name = "Engineering"
        group.web_url = "https://gitlab.com/eng"
        group_res = MagicMock(success=True, data=group, error=None)

        member = _member(uid=1, access_level=40)
        members_res = MagicMock(success=True, data=[member], error=None)

        async def ds_call_side(fn, *args, **kwargs):
            if "get_group" in str(fn):
                return group_res
            if "list_group_members_all" in str(fn):
                return members_res
            return MagicMock(success=True, data=[], error=None)

        c.runtime.ds_call = AsyncMock(side_effect=ds_call_side)
        c.creator_user_permission = MagicMock(return_value=None)

        projects_sync = ProjectsSync(c)
        from app.models.permission import EntityType, PermissionType, Permission
        perm = Permission(email="dev@example.com", type=PermissionType.OWNER.value, entity_type=EntityType.USER)
        projects_sync._transform_restrictions_to_permissions = AsyncMock(return_value=perm)

        await projects_sync._ensure_gitlab_group_record_groups(["eng"])
        c.data_entities_processor.on_new_record_groups.assert_called_once()

    async def test_group_not_found_uses_creator_fallback(self) -> None:
        """Group not accessible → creator-only RecordGroup created."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        fail_res = MagicMock(success=False, data=None, error="forbidden")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        from app.models.permission import Permission, EntityType, PermissionType
        creator_perm = Permission(email="creator@example.com", type=PermissionType.OWNER.value, entity_type=EntityType.USER)
        c.creator_user_permission = MagicMock(return_value=creator_perm)

        projects_sync = ProjectsSync(c)
        await projects_sync._ensure_gitlab_group_record_groups(["eng"])

        c.data_entities_processor.on_new_record_groups.assert_called_once()

    async def test_member_list_failure_triggers_child_project_union(self) -> None:
        """Group found but member listing fails → child-project union attempted."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        group = MagicMock()
        group.full_path = "eng"
        group.name = "Engineering"
        group.web_url = "https://gitlab.com/eng"
        group_res = MagicMock(success=True, data=group, error=None)
        members_fail_res = MagicMock(success=False, data=None, error="forbidden")

        async def ds_call_side(fn, *args, **kwargs):
            if "get_group" in str(fn):
                return group_res
            if "list_group_members_all" in str(fn):
                return members_fail_res
            return MagicMock(success=True, data=[], error=None)

        c.runtime.ds_call = AsyncMock(side_effect=ds_call_side)
        c.creator_user_permission = MagicMock(return_value=None)

        projects_sync = ProjectsSync(c)
        projects_sync._group_permissions_from_child_projects = AsyncMock(return_value=[])
        projects_sync._transform_restrictions_to_permissions = AsyncMock(return_value=None)

        p = _project(1, "eng/proj", ns_path="eng")
        await projects_sync._ensure_gitlab_group_record_groups(["eng"], candidate_projects=[p])
        projects_sync._group_permissions_from_child_projects.assert_called_once()


# ===========================================================================
# _group_permissions_from_child_projects
# ===========================================================================


class TestGroupPermissionsFromChildProjects:
    async def test_returns_permissions_from_child_projects(self) -> None:
        """Members from child projects under group path produce permissions."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        p = _project(1, "eng/proj", ns_path="eng")
        member = _member(uid=1, access_level=40)
        member.id = 1
        pm_res = MagicMock(success=True, data=[member], error=None)
        c.runtime.ds_call = AsyncMock(return_value=pm_res)

        from app.models.permission import Permission, EntityType, PermissionType
        perm = Permission(email="dev@example.com", type=PermissionType.OWNER.value, entity_type=EntityType.USER)

        projects_sync = ProjectsSync(c)
        projects_sync._transform_restrictions_to_permissions = AsyncMock(return_value=perm)

        result = await projects_sync._group_permissions_from_child_projects(
            group_path="eng", candidate_projects=[p]
        )
        assert len(result) >= 1

    async def test_no_child_projects_returns_empty(self) -> None:
        """No candidate projects under group → empty permissions."""
        c = make_mock_connector()
        projects_sync = ProjectsSync(c)

        p = _project(1, "other/proj", ns_path="other")
        result = await projects_sync._group_permissions_from_child_projects(
            group_path="eng", candidate_projects=[p]
        )
        assert result == []


# ===========================================================================
# _sync_project_members_as_pseudo
# ===========================================================================


class TestSyncProjectMembersAsPseudo:
    async def test_tiered_access_creates_four_record_groups(self) -> None:
        """Members with various access levels produce 4 RecordGroups."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c._gitlab_included_group_paths = None

        project = _project(1, "eng/proj")
        project.name = "proj"

        # One member with DEVELOPER access (level=30)
        member = _member(uid=1, access_level=30)
        member.id = 1
        members_res = MagicMock(success=True, data=[member], error=None)
        c.runtime.ds_call = AsyncMock(return_value=members_res)

        from app.models.permission import Permission, EntityType, PermissionType
        perm = Permission(email="dev@example.com", type=PermissionType.OWNER.value, entity_type=EntityType.USER)

        c.users = MagicMock()
        c.users._inject_creator_member_into = MagicMock()

        projects_sync = ProjectsSync(c)
        projects_sync._transform_restrictions_to_permissions = AsyncMock(return_value=perm)

        await projects_sync._sync_project_members_as_pseudo(project)

        c.data_entities_processor.on_new_record_groups.assert_called_once()
        # 4 record groups passed
        call_args = c.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(call_args) == 4

    async def test_member_listing_failure_calls_creator_fallback(self) -> None:
        """Listing failure → _apply_creator_fallback_for_project called."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        project = _project(1, "eng/proj")
        project.name = "proj"

        fail_res = MagicMock(success=False, data=None, error="forbidden")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        from app.models.permission import Permission, EntityType, PermissionType
        creator_perm = Permission(email="creator@example.com", type=PermissionType.OWNER.value, entity_type=EntityType.USER)
        c.creator_user_permission = MagicMock(return_value=creator_perm)

        projects_sync = ProjectsSync(c)
        projects_sync._apply_creator_fallback_for_project = AsyncMock()

        await projects_sync._sync_project_members_as_pseudo(project)

        projects_sync._apply_creator_fallback_for_project.assert_called_once()


# ===========================================================================
# _build_project_record_groups — parent group path
# ===========================================================================


class TestBuildProjectRecordGroupsParentPath:
    def test_sets_parent_for_group_namespace(self) -> None:
        """Project under a group namespace gets parent_external_group_id set."""
        c = make_mock_connector()
        c._gitlab_included_group_paths = ["eng"]

        p = _project(1, "eng/proj", ns_path="eng")
        projects_sync = ProjectsSync(c)
        rgs = projects_sync._build_project_record_groups(p)
        project_rg = rgs[0]
        # Parent should be set to "eng"
        assert project_rg.parent_external_group_id == "eng"


# ===========================================================================
# _create_pseudo_group
# ===========================================================================


class TestCreatePseudoGroup:
    async def test_creates_and_returns_app_user_group(self) -> None:
        """_create_pseudo_group creates AppUserGroup and calls on_new_user_groups."""
        c = make_mock_connector()
        c.data_entities_processor.on_new_user_groups = AsyncMock()

        projects_sync = ProjectsSync(c)
        result = await projects_sync._create_pseudo_group("user-99")

        assert result is not None
        assert result.source_user_group_id == "user-99"
        c.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_exception_returns_none(self) -> None:
        """Exception during creation returns None."""
        c = make_mock_connector()
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=Exception("DB error"))

        projects_sync = ProjectsSync(c)
        result = await projects_sync._create_pseudo_group("user-99")
        assert result is None


# ===========================================================================
# _longest_matching_group_path — prefix match
# ===========================================================================


class TestLongestMatchingGroupPathPrefixMatch:
    def test_exact_match_works(self) -> None:
        """Exact path match returns that path."""
        result = _longest_matching_group_path("eng", ["eng", "other"])
        assert result == "eng"

    def test_child_path_returns_parent(self) -> None:
        """Child path with deep namespace returns longest matching prefix."""
        result = _longest_matching_group_path("eng/backend/api", ["eng", "eng/backend", "ops"])
        assert result == "eng/backend"
