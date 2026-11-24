"""GitLab links the user who authenticated the connector only after a complete member listing.

Any skipped or failed member read makes the listing incomplete, so the full pass only removes. Every
listed member's email, the injected creator's too, is passed as listed.
"""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab.projects import ProjectsSync
from app.connectors.sources.gitlab.users import UsersSync
from app.models.permission import PermissionType

from .conftest import make_mock_connector

pytestmark = pytest.mark.anyio


def _member(uid: int, email: str | None = "m@corp.com") -> SimpleNamespace:
    return SimpleNamespace(id=uid, email=email, public_email=None, username=f"u{uid}", name=f"U{uid}")


def _ok(rows: list) -> MagicMock:
    return MagicMock(success=True, data=rows, error=None)


def _failed() -> MagicMock:
    return MagicMock(success=False, data=None, error="403")


def _scoped(group_members: object, project_members: object = None, projects: list | None = None):
    c = make_mock_connector()

    async def ds_call(fn, *_a, **_k):
        name = str(fn)
        if "list_group_members_all" in name:
            if isinstance(group_members, Exception):
                raise group_members
            return group_members
        if "list_project_members_all" in name:
            return project_members if project_members is not None else _ok([])
        return _ok([])

    c.runtime.ds_call = AsyncMock(side_effect=ds_call)
    c.runtime.paged_list = AsyncMock(return_value=_ok(projects or []))
    c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()
    users = UsersSync(c)
    users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
    users._resolve_user_sync_scope = AsyncMock(return_value=(["grp"], []))
    return c, users


def _unscoped(groups: object, projects: object):
    c = make_mock_connector()
    c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=groups)
    c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=projects)
    c.runtime.ds_call = AsyncMock(return_value=_ok([_member(1)]))
    c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()
    users = UsersSync(c)
    users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
    users._resolve_user_sync_scope = AsyncMock(return_value=None)
    return c, users


async def test_a_listing_read_in_full_is_complete_and_names_every_member() -> None:
    c, users = _scoped(_ok([_member(1, "A@Corp.com"), _member(2, "b@corp.com")]))
    c._listed_user_emails = {"stale@corp.com"}

    await users.sync_users()

    assert c._users_listing_complete is True
    assert c._listed_user_emails == {"a@corp.com", "b@corp.com"}


async def test_a_skipped_member_read_is_incomplete() -> None:
    c, users = _scoped(_failed())
    c._gitlab_user_id, c.creator_email = 9, "creator@corp.com"
    c._users_listing_complete = True

    await users.sync_users()

    assert c._users_listing_complete is False


async def test_a_group_member_read_that_raises_is_incomplete() -> None:
    c, users = _scoped(RuntimeError("boom"))
    c._gitlab_user_id, c.creator_email = 9, "creator@corp.com"

    await users.sync_users()

    assert c._users_listing_complete is False


async def test_a_failed_project_member_read_under_a_group_is_incomplete() -> None:
    proj = SimpleNamespace(id=10, path_with_namespace="grp/p")
    c, users = _scoped(_ok([_member(1)]), project_members=_failed(), projects=[proj])

    await users.sync_users()

    assert c._users_listing_complete is False


async def test_a_member_whose_profile_could_not_be_read_is_incomplete() -> None:
    c, users = _scoped(_ok([_member(1, None)]))
    users._enrich_members_with_full_user = UsersSync._enrich_members_with_full_user.__get__(users)

    async def ds_call(fn, *_a, **_k):
        return _ok([_member(1, None)]) if "list_group_members_all" in str(fn) else _failed()

    c.runtime.ds_call = AsyncMock(side_effect=ds_call)

    await users.sync_users()

    assert c._users_listing_complete is False


async def test_an_empty_listing_is_incomplete() -> None:
    c, users = _scoped(_ok([]))
    c._users_listing_complete = True

    with pytest.raises(RuntimeError):  # no member and no creator to fall back on: the sync stops
        await users.sync_users()

    assert c._users_listing_complete is False


async def test_an_unscoped_walk_whose_group_list_failed_is_incomplete() -> None:
    c, users = _unscoped(MagicMock(success=False, data=None, error="x"), _ok([SimpleNamespace(id=5)]))

    await users.sync_users()

    assert c._users_listing_complete is False


async def test_an_unscoped_walk_read_in_full_is_complete() -> None:
    c, users = _unscoped(_ok([SimpleNamespace(id=3)]), _ok([SimpleNamespace(id=5)]))

    await users.sync_users()

    assert c._users_listing_complete is True


async def test_gitlab_injected_creator_counts_as_listed() -> None:
    c, users = _scoped(_ok([_member(1, "a@corp.com")]))
    c._gitlab_user_id, c.creator_email = 9, "Creator@Corp.com"

    await users.sync_users()

    assert c._users_listing_complete is True
    assert c._listed_user_emails == {"a@corp.com", "creator@corp.com"}


async def test_a_creator_alone_is_not_a_listing() -> None:
    c, users = _scoped(_ok([]))
    c._gitlab_user_id, c.creator_email = 9, "creator@corp.com"

    await users.sync_users()

    assert c._users_listing_complete is False


class TestRunSyncOrder:
    def _conn(self, order: list[str], users_error: Exception | None = None) -> MagicMock:
        c = make_mock_connector()

        def step(name: str, error: Exception | None = None) -> AsyncMock:
            async def _run(*_a: object, **_k: object) -> None:
                order.append(name)
                if error:
                    raise error
            return AsyncMock(side_effect=_run)

        c.drop_authenticator_link_if_listed = step("drop")
        c.repos = MagicMock(cancel_timestamp_backfill=step("backfill"), schedule_timestamp_backfill=MagicMock())
        c.runtime.refresh_token_if_needed = step("token")
        c.users = MagicMock(sync_users=step("users", users_error))
        c.projects = MagicMock(sync_all_projects=step("projects"))
        c._register_authenticated_identity = step("register")
        c.raise_if_sync_incomplete = MagicMock()
        c.config_service = AsyncMock()
        return c

    async def _run(self, c: MagicMock) -> None:
        with patch("app.connectors.sources.gitlab.connector.load_connector_filters",
                   AsyncMock(return_value=(None, None))):
            await GitLabConnector.run_sync(c)

    async def test_the_removal_pass_runs_first_and_the_full_pass_after_the_users(self) -> None:
        order: list[str] = []
        await self._run(self._conn(order))

        assert order[:2] == ["drop", "backfill"]
        assert order.index("users") < order.index("register") < order.index("projects")

    async def test_no_full_pass_when_the_user_sync_raises(self) -> None:
        order: list[str] = []
        with pytest.raises(RuntimeError):
            await self._run(self._conn(order, RuntimeError("down")))

        assert "register" not in order and order[0] == "drop"

    async def test_flags_of_an_earlier_run_are_reset_first(self) -> None:
        order: list[str] = []
        c = self._conn(order, RuntimeError("down"))
        c._users_listing_complete, c._listed_user_emails = True, {"stale@corp.com"}

        with pytest.raises(RuntimeError):
            await self._run(c)

        assert c._users_listing_complete is False and c._listed_user_emails == set()


async def test_the_full_pass_carries_the_listing_state() -> None:
    c = make_mock_connector()
    c._users_listing_complete, c._listed_user_emails = True, {"creator@corp.com"}
    c.register_authenticated_source_user = AsyncMock()
    c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(
        success=True, data=SimpleNamespace(email="a@corp.com", public_email=None, id=4)))

    await GitLabConnector._register_authenticated_identity(c)

    assert c.register_authenticated_source_user.await_args.kwargs == {
        "listing_complete": True, "extra_present_emails": {"creator@corp.com"},
    }


async def test_a_creator_bound_to_the_tokens_own_account_is_the_token_holder() -> None:
    c, users = _scoped(_ok([_member(1, "a@corp.com")]))
    c._gitlab_user_id, c.creator_email = 9, "Creator@Corp.com"

    await users.sync_users()

    assert c._creator_is_token_holder is True


async def test_a_creator_listed_under_their_own_account_is_not_the_token_holder() -> None:
    c, users = _scoped(_ok([_member(1, "creator@corp.com"), _member(9, "bot@corp.com")]))
    c._gitlab_user_id, c.creator_email = 9, "creator@corp.com"
    c._creator_is_token_holder = True

    await users.sync_users()

    assert c._creator_is_token_holder is False


class _Kv:
    """The configuration store as a dict, shared by connector instances like the real one."""

    def __init__(
        self, values: dict | None = None, error: Exception | None = None, *, writes_fail: bool = False,
    ) -> None:
        self.values = dict(values or {})
        self.error = error
        self.writes_fail = writes_fail

    async def get_config(self, key: str, **_: object) -> object:
        if self.error:
            raise self.error
        return self.values.get(key)

    async def set_config(self, key: str, value: object) -> bool:
        # The real service answers False on a store error instead of raising.
        if self.writes_fail:
            return False
        self.values[key] = value
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


GRANT_KEY = "/services/connectors/gitlab-conn-1/authenticatorGrant"
CREATOR = "creator@corp.com"
_LISTED = {CREATOR, "bot@corp.com"}
_UNLISTED = {"bot@corp.com"}
# (listed emails, token holder, gets the creator's OWNER)
_CASES = {
    "listed": (_LISTED, False, False),
    "unlisted": (_UNLISTED, False, True),
    "token_holder": (_LISTED, True, True),
}


def _with_creator_gate(c: MagicMock, kv: _Kv | None = None) -> MagicMock:
    c.creator_email = CREATOR
    c.config_service = kv or _Kv()
    c.creator_user_permission = partial(GitLabConnector.creator_user_permission, c)
    c.creator_token_view_permission = partial(GitLabConnector.creator_token_view_permission, c)
    c.authenticator_grant_kept = partial(BaseConnector.authenticator_grant_kept, c)
    c.remember_authenticator_grant = partial(BaseConnector.remember_authenticator_grant, c)
    return c


async def _decided(
    *, listed: set[str], complete: bool = True, token_holder: bool = False, kv: _Kv | None = None,
) -> MagicMock:
    c = _with_creator_gate(make_mock_connector(), kv)
    c._listed_user_emails, c._users_listing_complete = listed, complete
    c._creator_is_token_holder = token_holder
    await GitLabConnector._decide_creator_token_view(c)
    return c


def _readable_group(c: MagicMock) -> MagicMock:
    group = SimpleNamespace(full_path="eng", name="Eng", web_url=None)

    async def ds_call(fn: object, *_a: object, **_k: object) -> MagicMock:
        return MagicMock(success=True, data=group, error=None) if "get_group" in str(fn) else _ok([])

    c.runtime.ds_call = AsyncMock(side_effect=ds_call)
    return c


def _owners(c: MagicMock) -> set[str]:
    return {
        p.email
        for call in c.data_entities_processor.on_new_record_groups.await_args_list
        for _rg, perms in call.args[0]
        for p in perms or []
        if p.type == PermissionType.OWNER
    }


async def _group_grantees(c: MagicMock) -> set[str]:
    await ProjectsSync(_readable_group(c))._ensure_gitlab_group_record_groups(["eng"])
    return _owners(c)


def _project() -> MagicMock:
    project = MagicMock()
    project.id, project.name, project.path_with_namespace = 7, "p", "eng/p"
    return project


class TestCreatorTokenViewOnGroups:
    async def test_a_listed_creator_who_is_not_the_token_holder_gets_no_group_grant(self) -> None:
        c = await _decided(listed=_LISTED)

        assert await _group_grantees(c) == set()

    async def test_an_unlisted_creator_still_gets_the_group_grant(self) -> None:
        c = await _decided(listed=_UNLISTED)

        assert await _group_grantees(c) == {CREATOR}

    async def test_an_incomplete_listing_grants_nothing_new(self) -> None:
        c = await _decided(listed=_UNLISTED, complete=False)

        assert await _group_grantees(c) == set()

    @pytest.mark.parametrize("complete", [True, False])
    async def test_the_token_holder_keeps_the_group_grant(self, complete: bool) -> None:
        c = await _decided(listed=_LISTED, complete=complete, token_holder=True)

        assert await _group_grantees(c) == {CREATOR}

    async def test_the_grant_is_decided_after_the_user_sync_and_before_the_projects(self) -> None:
        order: list[str] = []
        c = TestRunSyncOrder()._conn(order)
        c._decide_creator_token_view = AsyncMock(side_effect=lambda: order.append("decide"))

        await TestRunSyncOrder()._run(c)

        assert order.index("users") < order.index("decide") < order.index("projects")


class TestCreatorTokenViewOnFallbacks:
    """Where GitLab gives no member list, the creator alone used to be granted. They are now only
    when the listing allows them the token's view: not a creator listed under their own account."""

    @pytest.mark.parametrize("case", list(_CASES))
    async def test_an_unreadable_group(self, case: str) -> None:
        listed, token_holder, granted = _CASES[case]
        c = await _decided(listed=listed, token_holder=token_holder)
        c.runtime.ds_call = AsyncMock(return_value=MagicMock(success=False, data=None, error="500", status_code=500))

        await ProjectsSync(c)._ensure_gitlab_group_record_groups(["eng"])

        assert _owners(c) == ({CREATOR} if granted else set())

    @pytest.mark.parametrize("members", [
        MagicMock(success=False, data=None, error="500", status_code=500),
        MagicMock(success=True, data=[], error=None),
    ], ids=["failed", "empty"])
    @pytest.mark.parametrize("case", list(_CASES))
    async def test_a_project_whose_members_cannot_be_read(self, case: str, members: MagicMock) -> None:
        listed, token_holder, granted = _CASES[case]
        c = await _decided(listed=listed, token_holder=token_holder)
        c._gitlab_included_group_paths = None
        c.runtime.ds_call = AsyncMock(return_value=members)

        await ProjectsSync(c)._sync_project_members_as_pseudo(_project())

        assert _owners(c) == ({CREATOR} if granted else set())

    @pytest.mark.parametrize("case", list(_CASES))
    async def test_the_creator_only_project_fallback(self, case: str) -> None:
        listed, token_holder, granted = _CASES[case]
        c = await _decided(listed=listed, token_holder=token_holder)
        c._gitlab_included_group_paths = None

        await ProjectsSync(c)._apply_creator_fallback_for_project(_project())

        assert _owners(c) == ({CREATOR} if granted else set())


class TestCreatorTokenViewRecord:
    async def test_a_complete_listing_stores_the_decision(self) -> None:
        kv = _Kv()
        await _decided(listed=_UNLISTED, kv=kv)
        assert kv.values[GRANT_KEY] == {"email": CREATOR, "granted": True}

        await _decided(listed=_LISTED, kv=kv)
        assert kv.values[GRANT_KEY] == {"email": CREATOR, "granted": False}

    async def test_after_a_restart_an_incomplete_listing_keeps_the_stored_grant(self) -> None:
        kv = _Kv()
        await _decided(listed=_UNLISTED, kv=kv)

        fresh = await _decided(listed=_UNLISTED, complete=False, kv=kv)

        assert await _group_grantees(fresh) == {CREATOR}

    @pytest.mark.parametrize("stored", [None, {"email": CREATOR, "granted": False}])
    async def test_an_incomplete_listing_with_no_stored_grant_grants_nothing(self, stored: object) -> None:
        c = await _decided(listed=_UNLISTED, complete=False, kv=_Kv({GRANT_KEY: stored}))

        assert await _group_grantees(c) == set()

    async def test_a_withdrawn_grant_whose_write_fails_is_not_revived_by_an_incomplete_listing(self) -> None:
        kv = _Kv({GRANT_KEY: {"email": CREATOR, "granted": True}}, writes_fail=True)
        await _decided(listed=_LISTED, kv=kv)

        fresh = await _decided(listed=_UNLISTED, complete=False, kv=kv)

        assert await _group_grantees(fresh) == set()

    async def test_an_old_creators_grant_does_not_come_back_after_a_creator_swap(self) -> None:
        kv = _Kv({GRANT_KEY: {"email": "previous@corp.com", "granted": True}})

        c = await _decided(listed=_UNLISTED, complete=False, kv=kv)

        assert await _group_grantees(c) == set()
        assert kv.values[GRANT_KEY] == {"email": CREATOR, "granted": False}

    async def test_an_incomplete_listing_changes_no_stored_decision(self) -> None:
        kv = _Kv({GRANT_KEY: {"email": CREATOR, "granted": True}})

        await _decided(listed=_LISTED, complete=False, kv=kv)

        assert kv.values[GRANT_KEY] == {"email": CREATOR, "granted": True}

    async def test_a_failed_read_of_the_stored_grant_grants_nothing(self) -> None:
        c = await _decided(listed=_UNLISTED, complete=False, kv=_Kv(error=RuntimeError("kv down")))

        assert await _group_grantees(c) == set()
        c.logger.warning.assert_called()


class TestCreatorTokenViewEndToEnd:
    """The real member listing decides, and the real group write follows it."""

    @pytest.mark.parametrize(("creator_row", "granted"), [(True, False), (False, True)])
    async def test_the_listing_decides_the_creator_group_grant(self, creator_row: bool, granted: bool) -> None:
        rows = [_member(9, "bot@corp.com"), _member(1, "other@corp.com")]
        if creator_row:
            rows.append(_member(5, CREATOR))
        c, users = _scoped(_ok(rows))
        _with_creator_gate(c)
        c._gitlab_user_id = 9

        await users.sync_users()
        await GitLabConnector._decide_creator_token_view(c)
        c.data_entities_processor.on_new_record_groups.reset_mock()

        assert c._users_listing_complete is True and c._creator_is_token_holder is False
        assert await _group_grantees(c) == ({CREATOR} if granted else set())
