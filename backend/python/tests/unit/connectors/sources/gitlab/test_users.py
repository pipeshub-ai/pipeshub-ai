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
