"""GitLab links the user who authenticated the connector only after a complete member listing.

Any skipped or failed member read makes the listing incomplete, so the full pass only removes. Every
listed member's email, the injected creator's too, is passed as listed.
"""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def _group_owner_grant(
    *, listed: set[str], complete: bool = True, token_holder: bool = False, previous: bool = False,
) -> MagicMock:
    c = make_mock_connector()
    c.creator_email = "creator@corp.com"
    c._listed_user_emails, c._users_listing_complete = listed, complete
    c._creator_is_token_holder, c._creator_group_grant = token_holder, previous
    c.creator_user_permission = partial(GitLabConnector.creator_user_permission, c)
    c.creator_group_permission = partial(GitLabConnector.creator_group_permission, c)
    GitLabConnector._decide_creator_group_grant(c)
    group = SimpleNamespace(full_path="eng", name="Eng", web_url=None)

    async def ds_call(fn: object, *_a: object, **_k: object) -> MagicMock:
        return MagicMock(success=True, data=group, error=None) if "get_group" in str(fn) else _ok([])

    c.runtime.ds_call = AsyncMock(side_effect=ds_call)
    return c


async def _group_grantees(c: MagicMock) -> set[str]:
    await ProjectsSync(c)._ensure_gitlab_group_record_groups(["eng"])
    _rg, perms = c.data_entities_processor.on_new_record_groups.await_args.args[0][0]
    return {p.email for p in perms if p.type == PermissionType.OWNER}


class TestCreatorGroupOwnerGrant:
    async def test_a_listed_creator_who_is_not_the_token_holder_gets_no_group_grant(self) -> None:
        c = _group_owner_grant(listed={"creator@corp.com", "bot@corp.com"})

        assert await _group_grantees(c) == set()

    async def test_an_unlisted_creator_still_gets_the_group_grant(self) -> None:
        c = _group_owner_grant(listed={"bot@corp.com"})

        assert await _group_grantees(c) == {"creator@corp.com"}

    async def test_an_incomplete_listing_grants_nothing_new(self) -> None:
        c = _group_owner_grant(listed={"bot@corp.com"}, complete=False)

        assert await _group_grantees(c) == set()

    async def test_an_incomplete_listing_keeps_the_last_grant(self) -> None:
        c = _group_owner_grant(listed={"bot@corp.com"}, complete=False, previous=True)

        assert await _group_grantees(c) == {"creator@corp.com"}

    @pytest.mark.parametrize("complete", [True, False])
    async def test_the_token_holder_keeps_the_group_grant(self, complete: bool) -> None:
        c = _group_owner_grant(listed={"creator@corp.com"}, complete=complete, token_holder=True)

        assert await _group_grantees(c) == {"creator@corp.com"}

    async def test_the_grant_is_decided_after_the_user_sync_and_before_the_projects(self) -> None:
        order: list[str] = []
        c = TestRunSyncOrder()._conn(order)
        c._decide_creator_group_grant = MagicMock(side_effect=lambda: order.append("decide"))

        await TestRunSyncOrder()._run(c)

        assert order.index("users") < order.index("decide") < order.index("projects")
