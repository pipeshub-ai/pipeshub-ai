"""GitLab's member listing decides whether the user who authenticated the connector is linked to the
token's account and given the token's view.

The user sync puts every member row GitLab returned into the sync's ``SourceUserListing``. The creator
stub it injects is made here, not read from GitLab, so it is never a row; a creator email is put on
the token account's emailless row only when the creator holds the token. Any skipped or failed member
read cuts the listing, so it proves nobody absent.
"""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.connector.source_user_listing import (
    Presence,
    SourceUserListing,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab.projects import ProjectsSync
from app.connectors.sources.gitlab.users import UsersSync
from app.models.permission import PermissionType

from .conftest import make_mock_connector

pytestmark = pytest.mark.anyio

CREATOR = "creator@corp.com"
NOBODY = "nobody@corp.com"
MARKER_KEY = "/services/connectors/gitlab-conn-1/listedAuthenticators"


def _member(uid: int, email: str | None = "m@corp.com") -> SimpleNamespace:
    return SimpleNamespace(id=uid, email=email, public_email=None, username=f"u{uid}", name=f"U{uid}")


def _ok(rows: list) -> MagicMock:
    return MagicMock(success=True, data=rows, error=None)


def _failed() -> MagicMock:
    return MagicMock(success=False, data=None, error="403")


def _presence(c: MagicMock, email: str = NOBODY) -> Presence:
    return c._user_listing.presence(email)


def _app_user_ids(c: MagicMock) -> dict[str, str]:
    return {
        u.source_user_id: u.email
        for call in c.data_entities_processor.on_new_app_users.await_args_list
        for u in call.args[0]
    }


def _scoped(group_members: object, project_members: object = None, projects: list | None = None) -> tuple[MagicMock, UsersSync]:
    c = make_mock_connector()

    async def ds_call(fn, *_a, **_k) -> object:
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


def _unscoped(groups: object, projects: object) -> tuple[MagicMock, UsersSync]:
    c = make_mock_connector()
    c.scope.paged_list_groups_with_role_fallback = AsyncMock(return_value=groups)
    c.scope.paged_list_projects_with_role_fallback = AsyncMock(return_value=projects)
    c.runtime.ds_call = AsyncMock(return_value=_ok([_member(1)]))
    c.data_entities_processor.migrate_group_to_user_by_external_id = AsyncMock()
    users = UsersSync(c)
    users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: d)
    users._resolve_user_sync_scope = AsyncMock(return_value=None)
    return c, users


class TestTheListing:
    async def test_a_listing_read_in_full_names_every_member_and_proves_anyone_else_absent(self) -> None:
        c, users = _scoped(_ok([_member(1, "A@Corp.com"), _member(2, "b@corp.com")]))
        c._user_listing = SourceUserListing(includes_inactive=True)

        await users.sync_users()

        assert c._user_listing.emails == {"a@corp.com", "b@corp.com"}
        assert _presence(c) is Presence.ABSENT

    async def test_a_skipped_member_read_cuts_the_listing(self) -> None:
        c, users = _scoped(_failed())
        c._gitlab_user_id, c.creator_email = 9, CREATOR

        await users.sync_users()

        assert c._user_listing.is_cut and _presence(c) is Presence.UNKNOWN

    async def test_a_group_member_read_that_raises_cuts_the_listing(self) -> None:
        c, users = _scoped(RuntimeError("boom"))
        c._gitlab_user_id, c.creator_email = 9, CREATOR

        await users.sync_users()

        assert c._user_listing.is_cut

    async def test_a_failed_project_member_read_under_a_group_cuts_the_listing(self) -> None:
        proj = SimpleNamespace(id=10, path_with_namespace="grp/p")
        c, users = _scoped(_ok([_member(1)]), project_members=_failed(), projects=[proj])

        await users.sync_users()

        assert c._user_listing.is_cut

    async def test_a_member_whose_profile_could_not_be_read_cuts_the_listing(self) -> None:
        c, users = _scoped(_ok([_member(1, None)]))
        users._enrich_members_with_full_user = UsersSync._enrich_members_with_full_user.__get__(users)

        async def ds_call(fn, *_a, **_k) -> object:
            return _ok([_member(1, None)]) if "list_group_members_all" in str(fn) else _failed()

        c.runtime.ds_call = AsyncMock(side_effect=ds_call)

        await users.sync_users()

        assert c._user_listing.is_cut

    async def test_an_empty_listing_with_no_creator_to_fall_back_on_stops_the_sync_and_proves_nothing(self) -> None:
        c, users = _scoped(_ok([]))

        with pytest.raises(RuntimeError):
            await users.sync_users()

        assert _presence(c) is Presence.UNKNOWN

    async def test_an_unscoped_walk_whose_group_list_failed_cuts_the_listing(self) -> None:
        c, users = _unscoped(MagicMock(success=False, data=None, error="x"), _ok([SimpleNamespace(id=5)]))

        await users.sync_users()

        assert c._user_listing.is_cut

    async def test_an_unscoped_walk_read_in_full_proves_anyone_else_absent(self) -> None:
        c, users = _unscoped(_ok([SimpleNamespace(id=3)]), _ok([SimpleNamespace(id=5)]))

        await users.sync_users()

        assert _presence(c) is Presence.ABSENT

    async def test_a_member_whose_email_is_not_shown_proves_nobody_absent(self) -> None:
        c, users = _scoped(_ok([_member(1, "a@corp.com"), _member(2, None)]))

        await users.sync_users()

        assert not c._user_listing.is_cut
        assert _presence(c) is Presence.UNKNOWN, "that member may be the creator under their own account"

    async def test_an_injected_creator_is_no_row(self) -> None:
        c, users = _scoped(_ok([_member(1, "a@corp.com")]))
        c._gitlab_user_id, c.creator_email = 9, "Creator@Corp.com"
        c._creator_token_view = True

        await users.sync_users()

        assert c._user_listing.emails == {"a@corp.com"}, "the stub is made here, not read from GitLab"
        assert _presence(c, CREATOR) is Presence.ABSENT
        assert _app_user_ids(c)["9"] == "Creator@Corp.com", "the stub still gives the creator their user"

    async def test_the_stub_is_decided_once_the_listing_is_finished(self) -> None:
        c, users = _scoped(_ok([_member(1, "a@corp.com")]))
        c._gitlab_user_id, c.creator_email = 9, CREATOR
        seen: list[Presence] = []
        c._decide_creator_token_view = AsyncMock(side_effect=lambda: seen.append(_presence(c, CREATOR)))

        await users.sync_users()

        assert seen == [Presence.ABSENT]
        assert "9" not in _app_user_ids(c), "no token view, no stub"

    @pytest.mark.parametrize("enrich", [False, True], ids=["member-payload", "enriched-profile"])
    async def test_the_listing_reads_no_field_gitlab_does_not_send(self, enrich: bool) -> None:
        rows = [_ApiRow(id=1, email="a@corp.com", public_email=None, username="u1", name="U1")]
        c, users = _scoped(_ok(rows))
        c._gitlab_user_id, c.creator_email = 9, CREATOR
        if enrich:
            users._enrich_members_with_full_user = AsyncMock(side_effect=lambda d: dict(d))

        await users.sync_users()

        assert rows[0].reads <= {"id", "email", "public_email", "username", "name"}
        assert c._user_listing.emails == {"a@corp.com"}

    async def test_a_real_row_for_the_token_account_is_listed(self) -> None:
        c, users = _scoped(_ok([_member(1, "a@corp.com"), _member(9, "Bot@Corp.com")]))
        c._gitlab_user_id, c.creator_email = 9, CREATOR

        await users.sync_users()

        assert c._user_listing.emails == {"a@corp.com", "bot@corp.com"}

    async def test_a_creator_alone_is_not_a_listing(self) -> None:
        c, users = _scoped(_ok([]))
        c._gitlab_user_id, c.creator_email = 9, CREATOR

        await users.sync_users()

        assert _presence(c, CREATOR) is Presence.UNKNOWN and _presence(c) is Presence.UNKNOWN

    async def test_the_token_accounts_own_row_counts_as_a_member_row_in_an_unscoped_walk(self) -> None:
        c, users = _unscoped(_ok([SimpleNamespace(id=3)]), _ok([SimpleNamespace(id=5)]))
        c._gitlab_user_id, c.creator_email = 9, CREATOR

        async def ds_call(fn: object, *_a: object, **_k: object) -> MagicMock:
            return _ok([_member(9, "bot@corp.com")]) if "list_project_members_all" in str(fn) else _ok([])

        c.runtime.ds_call = AsyncMock(side_effect=ds_call)

        await users.sync_users()

        assert c._user_listing.emails == {"bot@corp.com"}
        assert _presence(c) is Presence.ABSENT

    async def test_an_unscoped_walk_that_lists_only_the_injected_creator_proves_nothing(self) -> None:
        c, users = _unscoped(_ok([SimpleNamespace(id=3)]), _ok([SimpleNamespace(id=5)]))
        c._gitlab_user_id, c.creator_email = 9, CREATOR
        c.runtime.ds_call = AsyncMock(return_value=_ok([]))

        await users.sync_users()

        assert _presence(c, CREATOR) is Presence.UNKNOWN


class TestTheTokenAccountRow:
    """GitLab shows the token's own account no email. Whose row it is follows from GET /user."""

    async def test_a_creator_who_holds_the_token_is_named_on_its_row(self) -> None:
        c, users = _scoped(_ok([_member(1, "a@corp.com"), _member(9, None)]))
        c._gitlab_user_id, c.creator_email = 9, CREATOR
        c._token_account_email = CREATOR

        await users.sync_users()

        assert _app_user_ids(c)["9"] == CREATOR
        assert _presence(c, CREATOR) is Presence.PRESENT

    @pytest.mark.parametrize("token_account", [None, "bot@corp.com"], ids=["not-read", "another-account"])
    async def test_anyone_elses_token_row_stays_unidentified(self, token_account: str | None) -> None:
        c, users = _scoped(_ok([_member(1, "a@corp.com"), _member(9, None)]))
        c._gitlab_user_id, c.creator_email = 9, CREATOR
        c._token_account_email = token_account

        await users.sync_users()

        assert "9" not in _app_user_ids(c), "the creator's email is not put on someone else's account"
        assert c._user_listing.emails == {"a@corp.com"}
        assert _presence(c, CREATOR) is Presence.UNKNOWN, "that row may be the creator's own account"


class _ApiRow:
    """A member row that records every field the connector reads, as the GitLab simulator does:
    the connector may read only what GitLab sends."""

    def __init__(self, **fields: object) -> None:
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "reads", set())

    def __getattr__(self, name: str) -> object:
        self.reads.add(name)
        if name in self.fields:
            return self.fields[name]
        raise AttributeError(name)


class TestRunSyncOrder:
    def _conn(self, order: list[str], users_error: Exception | None = None) -> MagicMock:
        c = make_mock_connector()

        def step(name: str, error: Exception | None = None, result: object = None) -> AsyncMock:
            async def _run(*_a: object, **_k: object) -> object:
                order.append(name)
                if error:
                    raise error
                return result
            return AsyncMock(side_effect=_run)

        c.drop_authenticator_link_if_listed = step("drop")
        c.repos = MagicMock(cancel_timestamp_backfill=step("backfill"), schedule_timestamp_backfill=MagicMock())
        c.runtime.refresh_token_if_needed = step("token")
        c.authenticator_email = step("subject", result=CREATOR)
        c._read_token_account = step("me")
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
        assert order.index("subject") < order.index("me") < order.index("users")
        # The user sync decides the creator's token view itself, once its listing is finished.
        assert order.index("users") < order.index("register") < order.index("projects")

    async def test_no_full_pass_when_the_user_sync_raises(self) -> None:
        order: list[str] = []
        with pytest.raises(RuntimeError):
            await self._run(self._conn(order, RuntimeError("down")))

        assert "register" not in order and "projects" not in order and order[0] == "drop"

    async def test_a_listing_of_an_earlier_run_is_dropped_first(self) -> None:
        c = self._conn([], RuntimeError("down"))
        c._user_listing = SourceUserListing(includes_inactive=True)

        with pytest.raises(RuntimeError):
            await self._run(c)

        assert c._user_listing is None

    async def test_the_subject_is_the_user_the_processor_links(self) -> None:
        c = self._conn([])
        c.creator_email = "earlier@corp.com"

        await self._run(c)

        assert c.creator_email == CREATOR, "re-read each run: authenticatedBy, else the creator"


class TestCreatorStub:
    """The stub takes the token account's GitLab id, so its user takes over that account's pseudo-group
    grants: only a creator who gets the token's view has it."""

    async def _sync(self, rows: list, *, token_account_email: str = "bot@corp.com") -> MagicMock:
        c, users = _scoped(_ok(rows))
        _with_creator_gate(c)
        c._gitlab_user_id = 9
        c._token_account_email = token_account_email

        await users.sync_users()
        return c

    @staticmethod
    def _migrated(c: MagicMock) -> dict[str, str]:
        return {
            call.kwargs["group_external_id"]: call.kwargs["user_email"]
            for call in c.data_entities_processor.migrate_group_to_user_by_external_id.await_args_list
        }

    async def test_a_listed_creator_who_does_not_hold_the_token_has_no_stub_and_inherits_nothing(self) -> None:
        c = await self._sync([_member(1, "other@corp.com"), _member(5, CREATOR)])

        assert "9" not in _app_user_ids(c)
        assert "9" not in self._migrated(c), "the token account's pseudo-group stays its own"
        assert c._creator_token_view is False

    async def test_an_unlisted_creator_has_the_stub(self) -> None:
        c = await self._sync([_member(1, "other@corp.com")])

        assert _app_user_ids(c)["9"] == CREATOR
        assert self._migrated(c)["9"] == CREATOR
        assert c._creator_token_view is True

    async def test_the_token_holder_has_the_stub_even_when_listed(self) -> None:
        c = await self._sync([_member(1, "other@corp.com"), _member(5, CREATOR)], token_account_email=CREATOR)

        assert _app_user_ids(c)["9"] == CREATOR

    async def test_a_creator_once_listed_has_no_stub(self) -> None:
        c, users = _scoped(_ok([_member(1, "other@corp.com")]))
        _with_creator_gate(c, _Kv({MARKER_KEY: ["creator-key"]}))
        c._gitlab_user_id, c._token_account_email = 9, "bot@corp.com"

        await users.sync_users()

        assert "9" not in _app_user_ids(c)


class TestTokenAccount:
    async def test_the_full_pass_carries_the_listing(self) -> None:
        c = make_mock_connector()
        c._user_listing = SourceUserListing(includes_inactive=True)
        c.register_authenticated_source_user = AsyncMock()
        c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(
            success=True, data=SimpleNamespace(email="A@corp.com", public_email=None, id=4)))

        await GitLabConnector._read_token_account(c)
        await GitLabConnector._register_authenticated_identity(c)

        c.register_authenticated_source_user.assert_awaited_once_with("a@corp.com", "4", listing=c._user_listing)

    @pytest.mark.parametrize(("me", "stored"), [
        (SimpleNamespace(email=" Bot@Corp.com ", public_email=None, id=9), "bot@corp.com"),
        (SimpleNamespace(email=None, public_email="Bot@Corp.com", id=9), "bot@corp.com"),
        (SimpleNamespace(email=None, public_email=None, id=9), None),
    ])
    async def test_the_read_keeps_the_token_accounts_own_email(self, me: SimpleNamespace, stored: str | None) -> None:
        c = make_mock_connector()
        c._token_account_email = "stale@corp.com"
        c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(success=True, data=me))

        await GitLabConnector._read_token_account(c)

        assert (c._token_account_email, c._token_account_id) == (stored, "9")

    async def test_a_failed_read_leaves_no_token_account_and_registers_nothing(self) -> None:
        c = make_mock_connector()
        c._token_account_email, c._token_account_read = "stale@corp.com", True
        c.register_authenticated_source_user = AsyncMock()
        c.runtime.ds_call = AsyncMock(side_effect=RuntimeError("down"))

        await GitLabConnector._read_token_account(c)
        await GitLabConnector._register_authenticated_identity(c)

        assert c._token_account_email is None
        c.register_authenticated_source_user.assert_not_awaited()


class _Kv:
    """The configuration store as a dict, shared by the connector and the processor like the real one."""

    def __init__(self, values: dict | None = None, error: Exception | None = None) -> None:
        self.values = dict(values or {})
        self.error = error

    async def get_config(self, key: str, *_: object, **__: object) -> object:
        if self.error:
            raise self.error
        return self.values.get(key)

    async def set_config(self, key: str, value: object) -> bool:
        self.values[key] = value
        return True


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
    c._decide_creator_token_view = partial(GitLabConnector._decide_creator_token_view, c)
    for name in ("authenticator_gets_token_view", "_marker_holds", "authenticator_email"):
        setattr(c, name, partial(getattr(BaseConnector, name), c))
    c.data_entities_processor.get_app_authenticator_user = AsyncMock(
        return_value={"_key": "creator-key", "email": CREATOR},
    )
    return c


def _listing(emails: set[str], *, complete: bool) -> SourceUserListing:
    listing = SourceUserListing(includes_inactive=True)
    for email in emails:
        listing.add_row(email)
    if complete:
        listing.finish()
    else:
        listing.cut("a member read failed")
    return listing


async def _decided(
    *, listed: set[str], complete: bool = True, token_holder: bool = False, kv: _Kv | None = None,
) -> MagicMock:
    c = _with_creator_gate(make_mock_connector(), kv)
    c._user_listing = _listing(listed, complete=complete)
    # The token account's own email, as GitLab returns it for the token: the only proof of a token holder.
    c._token_account_email = CREATOR if token_holder else "bot@corp.com"
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

    async def test_an_unlisted_creator_gets_the_group_grant(self) -> None:
        c = await _decided(listed=_UNLISTED)

        assert await _group_grantees(c) == {CREATOR}

    async def test_an_incomplete_listing_grants_nothing(self) -> None:
        c = await _decided(listed=_UNLISTED, complete=False)

        assert await _group_grantees(c) == set()

    @pytest.mark.parametrize("complete", [True, False])
    async def test_the_token_holder_keeps_the_group_grant(self, complete: bool) -> None:
        c = await _decided(listed=_LISTED, complete=complete, token_holder=True)

        assert await _group_grantees(c) == {CREATOR}

    async def test_a_creator_once_listed_gets_no_grant(self) -> None:
        c = await _decided(listed=_UNLISTED, kv=_Kv({MARKER_KEY: ["creator-key"]}))

        assert await _group_grantees(c) == set()

    async def test_a_marker_that_cannot_be_read_grants_nothing(self) -> None:
        c = await _decided(listed=_UNLISTED, kv=_Kv(error=RuntimeError("kv down")))

        assert await _group_grantees(c) == set()
        c.logger.warning.assert_called()

    async def test_nothing_is_stored_and_a_later_incomplete_listing_grants_nothing(self) -> None:
        kv = _Kv()
        first = await _decided(listed=_UNLISTED, kv=kv)
        assert first._creator_token_view is True
        assert kv.values == {}

        later = await _decided(listed=_UNLISTED, complete=False, kv=kv)

        assert await _group_grantees(later) == set()

    async def test_no_creator_gets_nothing(self) -> None:
        c = await _decided(listed=_UNLISTED)
        c.creator_email = None

        await GitLabConnector._decide_creator_token_view(c)

        assert c._creator_token_view is False


class TestCreatorTokenViewOnFallbacks:
    """Where GitLab gives no member list, the creator alone used to be granted. They are now only
    when they get the token's view: not a creator listed under their own account."""

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

    @pytest.mark.parametrize("case", list(_CASES))
    async def test_a_project_whose_members_cannot_be_read_keeps_its_stored_grants(self, case: str) -> None:
        listed, token_holder, _granted = _CASES[case]
        c = await _decided(listed=listed, token_holder=token_holder)
        c._gitlab_included_group_paths = None
        c.runtime.ds_call = AsyncMock(return_value=MagicMock(success=False, data=None, error="500", status_code=500))
        c.data_entities_processor.has_stored_permissions = AsyncMock(return_value=True)

        await ProjectsSync(c)._sync_project_members_as_pseudo(_project())

        (written,) = c.data_entities_processor.on_new_record_groups.await_args.args
        assert len(written) == 5 and all(perms is None for _group, perms in written)

    @pytest.mark.parametrize("case", list(_CASES))
    async def test_an_unreadable_group_with_stored_grants_keeps_them(self, case: str) -> None:
        listed, token_holder, _granted = _CASES[case]
        c = await _decided(listed=listed, token_holder=token_holder)
        c.runtime.ds_call = AsyncMock(return_value=MagicMock(success=False, data=None, error="500", status_code=500))
        c.data_entities_processor.has_stored_permissions = AsyncMock(return_value=True)

        await ProjectsSync(c)._ensure_gitlab_group_record_groups(["eng"])

        c.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.parametrize("token_view", [True, False])
    async def test_the_creator_is_injected_into_a_readable_member_list_only_with_the_token_view(
        self, token_view: bool,
    ) -> None:
        c = make_mock_connector()
        c._creator_token_view = token_view
        c._gitlab_included_group_paths = None
        c.runtime.ds_call = AsyncMock(return_value=_ok([SimpleNamespace(id=1, access_level=30)]))
        c.users._inject_creator_member_into = MagicMock()
        projects = ProjectsSync(c)
        projects._transform_restrictions_to_permissions = AsyncMock(return_value=None)

        await projects._sync_project_members_as_pseudo(_project())

        assert c.users._inject_creator_member_into.called is token_view


def _processor_store(kv: _Kv) -> tuple[DataSourceEntitiesProcessor, AsyncMock]:
    """The real processor over a graph store that knows the creator and the token account."""
    tx = AsyncMock()
    tx.get_edges_from_node = AsyncMock(return_value=[])
    tx.get_app_by_id = AsyncMock(return_value=MagicMock(authenticated_by=None))
    tx.get_user_by_user_id = AsyncMock(return_value={"_key": "creator-key", "email": CREATOR})
    tx.get_user_by_email = AsyncMock(return_value=MagicMock(id="bot-user-key", email="bot@corp.com"))
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=tx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    provider = MagicMock(transaction=MagicMock(return_value=ctx))
    proc = DataSourceEntitiesProcessor(MagicMock(), provider, kv)
    proc.org_id = "org-1"
    return proc, tx


class TestEndToEnd:
    """The real member listing, the real link decision of the processor and the real grant gate."""

    async def _sync(self, rows: list, *, token_account_email: str) -> tuple[MagicMock, AsyncMock, _Kv]:
        kv = _Kv()
        c, users = _scoped(_ok(rows))
        _with_creator_gate(c, kv)
        proc, tx = _processor_store(kv)
        c.created_by = "creator-user-id"
        c.connector_name = Connectors.GITLAB
        c.notify = AsyncMock()
        c.data_entities_processor.get_app_authenticator_user = proc.get_app_authenticator_user
        c.data_entities_processor.link_authenticator_to_source_user = proc.link_authenticator_to_source_user
        for name in ("register_authenticated_source_user", "_authenticator_presence", "_link_authenticator"):
            setattr(c, name, partial(getattr(BaseConnector, name), c))
        # GET /user: the token signs in as GitLab account 9.
        c._gitlab_user_id = 9
        c._token_account_email, c._token_account_id, c._token_account_read = token_account_email, "9", True

        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.notify_connector_sync_completed",
            AsyncMock(),
        ):
            # As run_sync: the user sync decides the token view once its listing is finished.
            await users.sync_users()
            await GitLabConnector._register_authenticated_identity(c)
        return c, tx, kv

    async def test_a_creator_with_no_account_whose_stub_was_injected_is_linked_and_gets_the_token_view(self) -> None:
        # The token account (an admin persona) is no member row, so the creator's stub takes its place.
        c, tx, kv = await self._sync([_member(1, "other@corp.com")], token_account_email="bot@corp.com")

        assert _app_user_ids(c)["9"] == CREATOR, "the stub was injected"
        assert _presence(c, CREATOR) is Presence.ABSENT, "the injected stub is no row"
        tx.upsert_authenticated_as.assert_awaited_once_with("creator-key", "bot-user-key", "gitlab-conn-1", "org-1")
        assert await _group_grantees(c) == {CREATOR}
        assert MARKER_KEY not in kv.values

    async def test_a_creator_listed_under_their_own_account_gets_neither(self) -> None:
        c, tx, kv = await self._sync(
            [_member(9, "bot@corp.com"), _member(1, "other@corp.com"), _member(5, CREATOR)],
            token_account_email="bot@corp.com",
        )

        tx.upsert_authenticated_as.assert_not_called()
        tx.remove_authenticated_as.assert_awaited_once_with("gitlab-conn-1")
        assert kv.values[MARKER_KEY] == ["creator-key"], "never linked or granted again"
        assert await _group_grantees(c) == set()

    async def test_a_bot_token_whose_email_is_hidden_leaves_its_row_unidentified(self) -> None:
        # The bot's GET /user email is not the creator's: the creator does not hold the token, so
        # the bot's emailless row is not the creator's, and may be anyone's.
        c, tx, _kv = await self._sync([_member(9, None), _member(1, "other@corp.com")],
                                      token_account_email="bot@corp.com")

        assert "9" not in _app_user_ids(c)
        assert _presence(c, CREATOR) is Presence.UNKNOWN
        tx.upsert_authenticated_as.assert_not_called()
        assert c._creator_token_view is False
        assert await _group_grantees(c) == set()

