"""Unit tests for gitlab UsersSync.

Covers:
- sync_users: routing between scoped and unscoped paths
- _resolve_user_sync_scope: filter combinations
- _sync_users_scoped: member collection, any_success, creator fallback
- _inject_creator_member_into: no duplicate injection
- _build_creator_member_stub: correct stub construction
- _enrich_members_with_full_user: partial failures don't abort
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.gitlab.users import UsersSync, _filter_op_val

from .conftest import make_mock_connector, paged_res, failed_res

pytestmark = pytest.mark.anyio


def _make_sync_filter(op: str, values: list) -> MagicMock:
    f = MagicMock()
    f.is_empty.return_value = not values
    f.operator = op
    f.value = values
    return f


def _make_member(uid: int = 1, email: str = "user@example.com") -> MagicMock:
    m = MagicMock()
    m.id = uid
    m.email = email
    m.username = f"user{uid}"
    return m


# ===========================================================================
# _filter_op_val helper
# ===========================================================================


class TestFilterOpVal:
    def test_string_operator(self) -> None:
        f = MagicMock()
        f.operator = "in"
        assert _filter_op_val(f) == "in"

    def test_enum_operator(self) -> None:
        class FakeOp:
            value = "not_in"

        f = MagicMock()
        f.operator = FakeOp()
        assert _filter_op_val(f) == "not_in"


# ===========================================================================
# _resolve_user_sync_scope
# ===========================================================================


class TestResolveUserSyncScope:
    async def test_no_sync_filters_returns_none(self) -> None:
        c = make_mock_connector()
        c.sync_filters = None
        users = UsersSync(c)
        result = await users._resolve_user_sync_scope()
        assert result is None

    async def test_empty_sync_filters_returns_none(self) -> None:
        c = make_mock_connector()
        c.sync_filters = {}
        users = UsersSync(c)
        result = await users._resolve_user_sync_scope()
        assert result is None

    async def test_project_ids_in_sets_project_targets(self) -> None:
        c = make_mock_connector()
        proj_filter = _make_sync_filter("in", ["ns/proj-a", "ns/proj-b"])
        from app.connectors.core.registry.filters import SyncFilterKey
        c.sync_filters = {SyncFilterKey.PROJECT_IDS: proj_filter}
        users = UsersSync(c)

        result = await users._resolve_user_sync_scope()
        assert result is not None
        group_targets, project_targets = result
        assert "ns/proj-a" in project_targets
        assert "ns/proj-b" in project_targets

    async def test_group_ids_in_sets_group_targets(self) -> None:
        c = make_mock_connector()
        grp_filter = _make_sync_filter("in", ["eng", "ops"])
        from app.connectors.core.registry.filters import SyncFilterKey
        c.sync_filters = {SyncFilterKey.GROUP_IDS: grp_filter}
        users = UsersSync(c)

        result = await users._resolve_user_sync_scope()
        assert result is not None
        group_targets, _ = result
        assert "eng" in group_targets

    async def test_project_ids_in_short_circuits_group_in(self) -> None:
        """When PROJECT_IDS IN is set, GROUP_IDS IN should be ignored."""
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        proj_filter = _make_sync_filter("in", ["ns/proj"])
        grp_filter = _make_sync_filter("in", ["eng"])
        c.sync_filters = {
            SyncFilterKey.PROJECT_IDS: proj_filter,
            SyncFilterKey.GROUP_IDS: grp_filter,
        }
        users = UsersSync(c)

        result = await users._resolve_user_sync_scope()
        assert result is not None
        group_targets, project_targets = result
        # PROJECT_IDS IN takes precedence; group targets should be empty
        assert group_targets == []
        assert "ns/proj" in project_targets


# ===========================================================================
# sync_users routing
# ===========================================================================


class TestSyncUsers:
    async def test_no_filters_calls_unscoped(self) -> None:
        c = make_mock_connector()
        c.sync_filters = None
        users = UsersSync(c)
        users._sync_users_unscoped = AsyncMock()
        users._sync_users_scoped = AsyncMock()
        users._resolve_user_sync_scope = AsyncMock(return_value=None)

        await users.sync_users()
        users._sync_users_unscoped.assert_called_once()
        users._sync_users_scoped.assert_not_called()

    async def test_with_scope_calls_scoped(self) -> None:
        c = make_mock_connector()
        users = UsersSync(c)
        users._sync_users_scoped = AsyncMock()
        users._sync_users_unscoped = AsyncMock()
        users._resolve_user_sync_scope = AsyncMock(return_value=(["eng"], ["ns/proj"]))

        await users.sync_users()
        users._sync_users_scoped.assert_called_once_with(["eng"], ["ns/proj"])
        users._sync_users_unscoped.assert_not_called()


# ===========================================================================
# _inject_creator_member_into
# ===========================================================================


class TestInjectCreatorMember:
    def test_does_not_inject_if_creator_already_present(self) -> None:
        c = make_mock_connector()
        c._gitlab_user_id = 42
        c.creator_email = "creator@example.com"
        users = UsersSync(c)

        existing_member = _make_member(uid=42, email="creator@example.com")
        member_dict = {42: existing_member}

        result = users._inject_creator_member_into(member_dict)
        assert len(member_dict) == 1
        assert result is True

    def test_injects_creator_when_not_present(self) -> None:
        c = make_mock_connector()
        c._gitlab_user_id = 99
        c.creator_email = "creator@example.com"
        users = UsersSync(c)

        member_dict: dict = {}

        result = users._inject_creator_member_into(member_dict)
        assert 99 in member_dict
        assert result is True


# ===========================================================================
# _build_creator_member_stub
# ===========================================================================


class TestBuildCreatorMemberStub:
    def test_returns_none_when_no_user_id_or_email(self) -> None:
        c = make_mock_connector()
        c._gitlab_user_id = None
        c.creator_email = None
        users = UsersSync(c)

        stub = users._build_creator_member_stub()
        assert stub is None

    def test_returns_stub_with_gitlab_user_id(self) -> None:
        c = make_mock_connector()
        c._gitlab_user_id = 77
        c.creator_email = "creator@example.com"
        users = UsersSync(c)

        stub = users._build_creator_member_stub()
        assert stub is not None
        assert getattr(stub, "id", None) == 77

    def test_returns_none_when_no_user_id_but_email_present(self) -> None:
        # Both user_id AND email are required; email alone is not enough.
        c = make_mock_connector()
        c._gitlab_user_id = None
        c.creator_email = "creator@example.com"
        users = UsersSync(c)

        stub = users._build_creator_member_stub()
        assert stub is None


# ===========================================================================
# _enrich_members_with_full_user (concurrency + partial failure)
# ===========================================================================


class TestEnrichMembersWithFullUser:
    async def test_partial_failure_does_not_abort(self) -> None:
        c = make_mock_connector()
        users = UsersSync(c)

        members = [_make_member(uid=i) for i in range(3)]
        dict_member = {m.id: m for m in members}

        async def _ds_call(fn, member_id, **kwargs):
            if member_id == 1:
                raise Exception("API error for uid=1")
            full = MagicMock()
            full.id = member_id
            full.public_email = f"user{member_id}@example.com"
            res = MagicMock()
            res.success = True
            res.data = full
            return res

        c.runtime.ds_call = AsyncMock(side_effect=_ds_call)
        enriched = await users._enrich_members_with_full_user(dict_member)

        # Should still return members despite one failure
        assert len(enriched) >= 2

    async def test_enriches_public_email(self) -> None:
        c = make_mock_connector()
        users = UsersSync(c)

        member = _make_member(uid=10)
        dict_member = {member.id: member}

        full = MagicMock()
        full.id = 10
        full.public_email = "enriched@example.com"
        res = MagicMock()
        res.success = True
        res.data = full
        c.runtime.ds_call = AsyncMock(return_value=res)

        enriched = await users._enrich_members_with_full_user(dict_member)

        # Enrichment should return dict with all members
        assert len(enriched) == 1

    async def test_get_user_failure_uses_member_payload(self) -> None:
        """When get_user fails, the original member payload is kept."""
        c = make_mock_connector()
        users = UsersSync(c)

        member = _make_member(uid=5, email="orig@example.com")
        dict_member = {member.id: member}

        fail_res = MagicMock()
        fail_res.success = False
        fail_res.data = None
        fail_res.error = "not found"
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        enriched = await users._enrich_members_with_full_user(dict_member)
        assert 5 in enriched
        # Original member returned
        assert enriched[5] is member


# ===========================================================================
# _resolve_user_sync_scope NOT_IN paths
# ===========================================================================


class TestResolveUserSyncScopeNotIn:
    async def test_group_ids_not_in_excludes_groups(self) -> None:
        """GROUP_IDS NOT_IN: visible groups minus excluded prefixes."""
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        grp_filter = _make_sync_filter("not_in", ["eng"])
        c.sync_filters = {SyncFilterKey.GROUP_IDS: grp_filter}

        # Simulate scope listing groups
        g_eng = MagicMock()
        g_eng.full_path = "eng"
        g_ops = MagicMock()
        g_ops.full_path = "ops"
        scope_res = MagicMock()
        scope_res.success = True
        scope_res.data = [g_eng, g_ops]
        c.scope = MagicMock()
        c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=scope_res)

        users = UsersSync(c)
        result = await users._resolve_user_sync_scope()
        assert result is not None
        group_targets, _ = result
        assert "eng" not in group_targets
        assert "ops" in group_targets

    async def test_group_ids_not_in_list_failure_empty_targets(self) -> None:
        """When group listing fails for NOT_IN, group_targets is empty."""
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        grp_filter = _make_sync_filter("not_in", ["eng"])
        c.sync_filters = {SyncFilterKey.GROUP_IDS: grp_filter}

        scope_res = MagicMock()
        scope_res.success = False
        scope_res.data = None
        scope_res.error = "API error"
        c.scope = MagicMock()
        c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=scope_res)

        users = UsersSync(c)
        result = await users._resolve_user_sync_scope()
        # Returns tuple with empty group_targets (not None) to avoid unscoped fallback
        assert result is not None
        group_targets, _ = result
        assert group_targets == []

    async def test_project_ids_not_in_excludes_projects(self) -> None:
        """PROJECT_IDS NOT_IN: visible projects minus excluded paths."""
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        proj_filter = _make_sync_filter("not_in", ["ns/excluded"])
        c.sync_filters = {SyncFilterKey.PROJECT_IDS: proj_filter}

        p_ok = MagicMock()
        p_ok.path_with_namespace = "ns/ok"
        p_excl = MagicMock()
        p_excl.path_with_namespace = "ns/excluded"
        proj_res = MagicMock()
        proj_res.success = True
        proj_res.data = [p_ok, p_excl]
        c.scope = MagicMock()
        c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=proj_res)

        users = UsersSync(c)
        result = await users._resolve_user_sync_scope()
        assert result is not None
        _, project_targets = result
        assert "ns/excluded" not in project_targets
        assert "ns/ok" in project_targets

    async def test_project_ids_not_in_list_failure_empty_targets(self) -> None:
        """When project listing fails for NOT_IN, project_targets is empty."""
        c = make_mock_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        proj_filter = _make_sync_filter("not_in", ["ns/excluded"])
        c.sync_filters = {SyncFilterKey.PROJECT_IDS: proj_filter}

        proj_res = MagicMock()
        proj_res.success = False
        proj_res.data = None
        proj_res.error = "API error"
        c.scope = MagicMock()
        c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=proj_res)

        users = UsersSync(c)
        result = await users._resolve_user_sync_scope()
        assert result is not None
        _, project_targets = result
        assert project_targets == []


# ===========================================================================
# _sync_users_scoped
# ===========================================================================


class TestSyncUsersScoped:
    async def test_scoped_happy_path_calls_sync_users_from_projects_groups(self) -> None:
        """Scoped path collects group members and calls _sync_users_from_projects_groups."""
        c = make_mock_connector()
        c._gitlab_user_id = 1
        c.creator_email = "creator@example.com"

        member = _make_member(uid=1, email="creator@example.com")

        # ds_call for group members_all
        members_res = MagicMock(success=True, data=[member], error=None)
        # paged_list for list_group_projects (subgroup expansion)
        proj = MagicMock()
        proj.id = 10
        proj.path_with_namespace = "grp/proj"
        proj_res = MagicMock(success=True, data=[proj], error=None)
        # ds_call for project members_all (subgroup project)
        proj_members_res = MagicMock(success=True, data=[member], error=None)

        call_count = 0

        async def ds_call_side(fn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if "list_group_members_all" in str(fn):
                return members_res
            if "list_project_members_all" in str(fn):
                return proj_members_res
            return MagicMock(success=True, data=[], error=None)

        c.runtime.ds_call = AsyncMock(side_effect=ds_call_side)
        c.runtime.paged_list = AsyncMock(return_value=proj_res)

        users = UsersSync(c)
        users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
        users._sync_users_from_projects_groups = AsyncMock()

        await users._sync_users_scoped(["grp"], [])

        users._sync_users_from_projects_groups.assert_called_once()

    async def test_scoped_group_member_failure_falls_back_gracefully(self) -> None:
        """When group member listing fails, the run continues with creator fallback."""
        c = make_mock_connector()
        c._gitlab_user_id = 99
        c.creator_email = "creator@example.com"

        members_fail = MagicMock(success=False, data=None, error="403 Forbidden")
        c.runtime.ds_call = AsyncMock(return_value=members_fail)
        proj_res = MagicMock(success=True, data=[], error=None)
        c.runtime.paged_list = AsyncMock(return_value=proj_res)

        users = UsersSync(c)
        users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
        users._sync_users_from_projects_groups = AsyncMock()

        # Should not raise — creator can be injected
        await users._sync_users_scoped(["grp"], [])
        users._sync_users_from_projects_groups.assert_called_once()

    async def test_scoped_explicit_project_path_walked(self) -> None:
        """Explicit project_paths are walked for members."""
        c = make_mock_connector()
        c._gitlab_user_id = 1
        c.creator_email = "creator@example.com"

        member = _make_member(uid=1)
        proj_members_res = MagicMock(success=True, data=[member], error=None)
        c.runtime.ds_call = AsyncMock(return_value=proj_members_res)

        users = UsersSync(c)
        users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
        users._sync_users_from_projects_groups = AsyncMock()

        await users._sync_users_scoped([], ["ns/proj"])

        users._sync_users_from_projects_groups.assert_called_once()
        # Verify ds_call was called for list_project_members_all
        c.runtime.ds_call.assert_called()


# ===========================================================================
# _sync_users_unscoped
# ===========================================================================


class TestSyncUsersUnscoped:
    async def test_unscoped_happy_path(self) -> None:
        """Unscoped path: groups + projects synced, _sync_users_from_projects_groups called."""
        c = make_mock_connector()
        c._gitlab_user_id = 1
        c.creator_email = "creator@example.com"

        group = MagicMock()
        group.id = 10
        groups_res = MagicMock(success=True, data=[group], error=None)

        project = MagicMock()
        project.id = 20
        projects_res = MagicMock(success=True, data=[project], error=None)

        member = _make_member(uid=1)
        members_res = MagicMock(success=True, data=[member], error=None)

        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})
        c.scope.list_projects_scope_kwargs = MagicMock(return_value={})
        c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=groups_res)
        c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=projects_res)

        c.runtime.ds_call = AsyncMock(return_value=members_res)

        users = UsersSync(c)
        users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
        users._sync_users_from_projects_groups = AsyncMock()

        await users._sync_users_unscoped()

        users._sync_users_from_projects_groups.assert_called_once()

    async def test_unscoped_both_fail_with_creator_does_not_raise(self) -> None:
        """Both groups and projects fail; creator present: no exception raised."""
        c = make_mock_connector()
        c._gitlab_user_id = 99
        c.creator_email = "creator@example.com"

        fail_res = MagicMock(success=False, data=None, error="timeout")
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})
        c.scope.list_projects_scope_kwargs = MagicMock(return_value={})
        c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=fail_res)
        c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=fail_res)

        users = UsersSync(c)
        users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
        users._sync_users_from_projects_groups = AsyncMock()

        # Should not raise since creator is available
        await users._sync_users_unscoped()
        users._sync_users_from_projects_groups.assert_called_once()

    async def test_unscoped_both_fail_no_creator_raises(self) -> None:
        """Both groups and projects fail AND no creator: RuntimeError raised."""
        c = make_mock_connector()
        c._gitlab_user_id = None
        c.creator_email = None

        fail_res = MagicMock(success=False, data=None, error="timeout")
        c.scope = MagicMock()
        c.scope.list_groups_scope_kwargs = MagicMock(return_value={})
        c.scope.list_projects_scope_kwargs = MagicMock(return_value={})
        c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=fail_res)
        c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=fail_res)

        users = UsersSync(c)
        users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
        users._sync_users_from_projects_groups = AsyncMock()

        with pytest.raises(RuntimeError):
            await users._sync_users_unscoped()


# ===========================================================================
# _inject_creator_member_into — stub=None path
# ===========================================================================


class TestInjectCreatorMemberStubNone:
    def test_returns_false_when_stub_is_none(self) -> None:
        """When _build_creator_member_stub returns None, inject returns False."""
        c = make_mock_connector()
        c._gitlab_user_id = None
        c.creator_email = None
        users = UsersSync(c)

        member_dict: dict = {1: _make_member(uid=1)}
        result = users._inject_creator_member_into(member_dict)
        assert result is False
        # Dict unchanged
        assert len(member_dict) == 1


# ===========================================================================
# _sync_users_from_projects_groups
# ===========================================================================


class TestSyncUsersFromProjectsGroups:
    async def test_creator_email_bypass(self) -> None:
        """Creator with no public_email uses PipesHub email."""
        c = make_mock_connector()
        c._gitlab_user_id = 42
        c.creator_email = "creator@example.com"

        # Member with no public_email and no email, but matches creator id
        member = SimpleNamespace(
            id=42,
            username="creator",
            name="Creator User",
            public_email="",
            email="",
        )
        dict_member = {42: member}
        users = UsersSync(c)

        c.data_entities_processor.on_new_app_users = AsyncMock()
        c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()

        await users._sync_users_from_projects_groups(dict_member)

        c.data_entities_processor.on_new_app_users.assert_called_once()
        call_args = c.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].email == "creator@example.com"

    async def test_skip_no_email_member(self) -> None:
        """Members without any email are skipped."""
        c = make_mock_connector()
        c._gitlab_user_id = None
        c.creator_email = None

        member = SimpleNamespace(
            id=5,
            username="noemail",
            name="No Email",
            public_email="",
            email="",
        )
        dict_member = {5: member}
        users = UsersSync(c)

        c.data_entities_processor.on_new_app_users = AsyncMock()

        await users._sync_users_from_projects_groups(dict_member)

        c.data_entities_processor.on_new_app_users.assert_not_called()

    async def test_pseudo_group_migration_called_for_each_user(self) -> None:
        """migrate_group_to_user_by_external_id is called for each synced user."""
        c = make_mock_connector()
        c._gitlab_user_id = None
        c.creator_email = None

        m1 = SimpleNamespace(id=1, username="alice", name="Alice", public_email="alice@example.com", email="")
        m2 = SimpleNamespace(id=2, username="bob", name="Bob", public_email="bob@example.com", email="")
        dict_member = {1: m1, 2: m2}

        users = UsersSync(c)
        c.data_entities_processor.on_new_app_users = AsyncMock()
        c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()

        await users._sync_users_from_projects_groups(dict_member)

        assert c.data_entities_processor.migrate_group_to_user_by_external_id.call_count == 2

    async def test_migration_failure_logged_not_raised(self) -> None:
        """A failed pseudo-group migration is logged but does not abort."""
        c = make_mock_connector()
        c._gitlab_user_id = None
        c.creator_email = None

        member = SimpleNamespace(id=3, username="charlie", name="Charlie", public_email="charlie@example.com", email="")
        dict_member = {3: member}

        users = UsersSync(c)
        c.data_entities_processor.on_new_app_users = AsyncMock()
        c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock(
            side_effect=Exception("migration failed")
        )

        # Should not raise
        await users._sync_users_from_projects_groups(dict_member)
        c.logger.warning.assert_called()
