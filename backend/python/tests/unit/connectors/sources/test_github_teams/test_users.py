"""Unit tests for github_teams UsersSync.

Covers:
- Principal discovery: org members unioned with outside collaborators, bots dropped.
- Phase A: cached AppUser lookup by GitHub numeric id.
- Phase B: org-verified-domain emails (batched GraphQL), members-only, best-effort.
- Phase C: public profile email via the GET /users/{login} fan-out.
- Identity binding: a resolved email is persisted as an AppUser regardless of
  whether it is already in the PipesHub directory.
- _resolve_target_orgs: ORG_IDS / REPO_IDS filter precedence.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.connectors.sources.github_teams import users as users_mod
from app.connectors.sources.github_teams.users import UsersSync, _is_noreply_email

from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    make_named_user,
    ok_response,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class TestIsNoreplyEmail:
    def test_noreply_suffix_detected(self) -> None:
        assert _is_noreply_email("123+octocat@users.noreply.github.com") is True

    def test_real_email_not_noreply(self) -> None:
        assert _is_noreply_email("octocat@example.com") is False

    def test_none_email(self) -> None:
        assert _is_noreply_email(None) is False


class TestResolveTargetOrgs:
    async def test_org_ids_in_filter_is_authoritative(self) -> None:
        c = make_mock_connector()
        import app.connectors.sources.github_teams.users as users_mod
        org_filter = MagicMock()
        org_filter.is_empty.return_value = False
        org_filter.value = ["acme"]
        org_filter.operator_value = "in"
        c.sync_filters = {users_mod.SyncFilterKey.ORG_IDS: org_filter}

        sync = UsersSync(c)
        orgs, orgs_ok = await sync._resolve_target_orgs()
        assert orgs_ok is True
        assert orgs == ["acme"]

    async def test_repo_ids_narrows_org_scope_without_org_filter(self) -> None:
        c = make_mock_connector()
        import app.connectors.sources.github_teams.users as users_mod
        repo_filter = MagicMock()
        repo_filter.is_empty.return_value = False
        repo_filter.value = ["acme/widgets", "acme/gadgets", "other/thing"]
        repo_filter.operator_value = "in"
        c.sync_filters = {users_mod.SyncFilterKey.REPO_IDS: repo_filter}

        sync = UsersSync(c)
        orgs, orgs_ok = await sync._resolve_target_orgs()
        assert orgs_ok is True
        assert orgs == ["acme", "other"]

    async def test_no_filters_discovers_all_visible_orgs(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login="acme"), SimpleNamespace(login="other"),
        ])
        sync = UsersSync(c)
        orgs, orgs_ok = await sync._resolve_target_orgs()
        assert orgs_ok is True
        assert orgs == ["acme", "other"]

    async def test_org_not_in_excludes_from_visible_list(self) -> None:
        c = make_mock_connector()
        org_filter = MagicMock()
        org_filter.is_empty.return_value = False
        org_filter.value = ["other"]
        org_filter.operator_value = "not_in"
        c.sync_filters = {users_mod.SyncFilterKey.ORG_IDS: org_filter}
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login="acme"), SimpleNamespace(login="other"),
        ])

        orgs, ok = await UsersSync(c)._resolve_target_orgs()
        assert ok is True
        assert orgs == ["acme"]

    async def test_repo_ids_without_slash_falls_through_to_visible_orgs(self) -> None:
        c = make_mock_connector()
        repo_filter = MagicMock()
        repo_filter.is_empty.return_value = False
        repo_filter.value = ["noslash"]
        repo_filter.operator_value = "in"
        c.sync_filters = {users_mod.SyncFilterKey.REPO_IDS: repo_filter}
        c.runtime.ds_call.return_value = ok_response([SimpleNamespace(login="acme")])

        orgs, ok = await UsersSync(c)._resolve_target_orgs()
        assert ok is True
        assert orgs == ["acme"]

    async def test_list_user_orgs_failure_is_not_empty_success(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("403")
        orgs, ok = await UsersSync(c)._list_all_visible_orgs(None)
        assert ok is False
        assert orgs == []


class TestSyncUsersGuards:
    async def test_missing_data_source_raises(self) -> None:
        c = make_mock_connector()
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await UsersSync(c).sync_users()

    async def test_org_discovery_failure_aborts(self) -> None:
        c = make_mock_connector()
        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(([], False))
        with pytest.raises(RuntimeError, match="org discovery failed"):
            await sync.sync_users()

    async def test_no_orgs_is_warning_not_abort(self) -> None:
        c = make_mock_connector()
        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(([], True))
        await sync.sync_users()
        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    async def test_invalid_org_login_skips_verified_domain_query(self) -> None:
        c = make_mock_connector()
        sync = UsersSync(c)
        sync._member_ids = {1}
        resolved = await sync._resolve_via_verified_domains(
            {1: make_named_user(user_id=1, login="alice")}, {1}, ["not a valid org!!"]
        )
        assert resolved == {}
        c.runtime.ds_call.assert_not_awaited()

    async def test_member_without_login_is_not_enriched(self) -> None:
        c = make_mock_connector()
        member = SimpleNamespace(id=1, login=None, email=None)
        enriched = await UsersSync(c)._enrich_members_with_full_profile({1: member}, {1})
        assert enriched[1] is member
        c.runtime.ds_call.assert_not_awaited()


class TestSyncUsersPhases:
    async def test_phase1_visible_email_resolved_directly(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            make_named_user(user_id=1, login="alice", email="alice@example.com", completed=True),
        ])
        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        c.data_entities_processor.on_new_app_users.assert_awaited_once()
        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert len(persisted) == 1
        assert persisted[0].email == "alice@example.com"
        assert persisted[0].source_user_id == "1"

    async def test_noreply_profile_email_is_skipped(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            make_named_user(
                user_id=1, login="alice",
                email="1+alice@users.noreply.github.com", completed=True,
            ),
        ])
        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    async def test_phase1_enrichment_recovers_email_for_partial_member(self) -> None:
        c = make_mock_connector()
        # list_org_members returns a partial (incomplete) member without email.
        partial = make_named_user(user_id=2, login="bob", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "get_user": ok_response(make_named_user(user_id=2, login="bob", email="bob@example.com", completed=True)),
        })
        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "bob@example.com"

    async def test_phase2_cached_app_user_resolves_remaining(self) -> None:
        c = make_mock_connector()
        partial = make_named_user(user_id=3, login="carol", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "get_user": failed_response("not found"),
        })
        cached_user = SimpleNamespace(source_user_id="3", email="carol@example.com")
        c.data_entities_processor.get_all_app_users.return_value = [cached_user]

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "carol@example.com"

    async def test_unresolvable_member_gets_no_node_and_no_placeholder(self) -> None:
        """A principal no phase can resolve has no PipesHub identity to grant to,
        so nothing is written. There is no placeholder group: on_new_record_groups
        rebuilds permission edges every sync, so the edge appears on its own once
        the identity resolves."""
        c = make_mock_connector()
        partial = make_named_user(user_id=6, login="frank", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "get_user": failed_response("not found"),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()
        c.data_entities_processor.on_new_user_groups.assert_not_awaited()

    async def test_member_listing_failure_aborts_rather_than_falling_back(self) -> None:
        """Identity comes solely from GitHub org membership — there is no
        configuring-user fallback, and an unreadable member list must not be
        mistaken for an empty org."""
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("403 forbidden")
        c.creator_email = "creator@example.com"
        c._github_login = "creator-login"

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        with pytest.raises(RuntimeError):
            await sync.sync_users()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    async def test_aborts_when_every_org_fails_and_no_creator(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("403 forbidden")
        c.creator_email = None

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        with pytest.raises(RuntimeError):
            await sync.sync_users()


class TestPrincipalDiscovery:
    """Who can hold a permission: org members UNION outside collaborators."""

    async def test_outside_collaborators_are_discovered_alongside_members(self) -> None:
        """GitHub omits outside collaborators from /orgs/{org}/members, but they
        appear in repo collaborator listings and receive permission edges. Missing
        them here makes them permanently unresolvable."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([
                make_named_user(user_id=1, login="member", email="member@corp.com", completed=True),
            ]),
            "list_org_outside_collaborators": ok_response([
                make_named_user(user_id=2, login="vendor", email="vendor@corp.com", completed=True),
            ]),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert sorted(u.source_user_id for u in persisted) == ["1", "2"]

    async def test_org_member_emails_exclude_outside_collaborators(self) -> None:
        """Internal repos are readable by enterprise members and explicitly not
        by outside collaborators, so this set — which drives that grant — must
        follow the endpoint each principal came from, not merely who resolved."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([
                make_named_user(user_id=1, login="member", email="member@corp.com", completed=True),
            ]),
            "list_org_outside_collaborators": ok_response([
                make_named_user(user_id=2, login="vendor", email="vendor@corp.com", completed=True),
            ]),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        # Both are principals and both get AppUsers; only the member is
        # eligible for internal-repo access.
        assert sync.org_member_emails() == {"member@corp.com"}

    async def test_user_owned_scope_404s_without_aborting_the_sync(self) -> None:
        """A personal repo reaches user sync through the REPO_IDS filter, and
        /orgs/{username}/members 404s because the login is an account, not an
        org. That is an answer, not an outage — treating it as failure aborted
        the ENTIRE sync (every other repo included) rather than syncing the repo
        from its visibility and collaborator grants."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": failed_response("Not Found", status_code=404),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["darshangodase"], True))

        await sync.sync_users()  # must not raise

        c.data_entities_processor.on_new_app_users.assert_not_called()
        assert sync.org_member_emails() == set()

    async def test_unreachable_org_still_aborts_the_sync(self) -> None:
        """A 403/5xx is genuinely unknown; treating it as "no members" would
        drop every AppUser and revoke everyone."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": failed_response("Server Error", status_code=500),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        with pytest.raises(RuntimeError):
            await sync.sync_users()

    async def test_bots_are_not_treated_as_principals(self) -> None:
        c = make_mock_connector()
        bot = make_named_user(user_id=9, login="dependabot", email=None, completed=False)
        bot.type = "Bot"
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([bot]),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        # No profile fetch on an account that can never hold a PipesHub identity.
        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    async def test_outside_collaborator_listing_failure_does_not_abort_the_sync(self) -> None:
        """Members still resolve; only the outside collaborators are lost."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([
                make_named_user(user_id=1, login="member", email="member@corp.com", completed=True),
            ]),
            "list_org_outside_collaborators": failed_response("403 forbidden"),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert [u.source_user_id for u in persisted] == ["1"]


def _graphql_dispatches(c: MagicMock) -> list[object]:
    """Every ds_call await that targeted the mocked graphql_query method."""
    return [
        ca for ca in c.runtime.ds_call.await_args_list
        if ca.args and ca.args[0] is c.data_source.graphql_query
    ]


class TestVerifiedDomainEmails:
    """Phase B: batched GraphQL organizationVerifiedDomainEmails resolution."""

    async def test_verified_domain_email_wins_over_public_profile(self) -> None:
        """The verified-domain address is the corporate identity the PipesHub
        directory knows; a public profile email is often personal. When both
        exist, the corporate one must win — so Phase B runs before the profile
        fan-out and the profile fetch never happens for a resolved member."""
        c = make_mock_connector()
        partial = make_named_user(user_id=11, login="bob", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "graphql_query": ok_response(
                {"u0": {"organizationVerifiedDomainEmails": ["bob@corp.com"]}}
            ),
            "get_user": ok_response(
                make_named_user(user_id=11, login="bob", email="bob@gmail.com", completed=True)
            ),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert [(u.source_user_id, u.email) for u in persisted] == [("11", "bob@corp.com")]

    async def test_outside_collaborators_are_not_queried(self) -> None:
        """The field is defined per org membership, so an outside collaborator
        can never carry a verified-domain address — querying them wastes the
        whole alias slot. They resolve through the profile phase instead."""
        c = make_mock_connector()
        vendor = make_named_user(user_id=12, login="vendor", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([]),
            "list_org_outside_collaborators": ok_response([vendor]),
            "graphql_query": ok_response(
                {"u0": {"organizationVerifiedDomainEmails": ["vendor@corp.com"]}}
            ),
            "get_user": ok_response(
                make_named_user(user_id=12, login="vendor", email="vendor@gmail.com", completed=True)
            ),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        assert _graphql_dispatches(c) == []
        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "vendor@gmail.com"

    async def test_graphql_failure_falls_through_to_profile_phase(self) -> None:
        """Most tokens are not org owners and most orgs have no verified
        domain — a failed lookup is the expected case and must degrade to the
        later phases, never abort the sync."""
        c = make_mock_connector()
        partial = make_named_user(user_id=13, login="carla", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "graphql_query": failed_response("Resource not accessible by integration"),
            "get_user": ok_response(
                make_named_user(user_id=13, login="carla", email="carla@example.com", completed=True)
            ),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "carla@example.com"

    async def test_empty_email_list_falls_through(self) -> None:
        """An org owner token on an org without a verified domain gets [] —
        the member must still reach the profile phase."""
        c = make_mock_connector()
        partial = make_named_user(user_id=14, login="dana", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "graphql_query": ok_response(
                {"u0": {"organizationVerifiedDomainEmails": []}}
            ),
            "get_user": ok_response(
                make_named_user(user_id=14, login="dana", email="dana@example.com", completed=True)
            ),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "dana@example.com"

    async def test_chunking_issues_one_query_per_batch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(users_mod, "VERIFIED_DOMAIN_EMAIL_BATCH", 1)
        c = make_mock_connector()
        members = [
            make_named_user(user_id=uid, login=f"user{uid}", email=None, completed=False)
            for uid in (30, 31)
        ]
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response(members),
            "graphql_query": ok_response(
                {"u0": {"organizationVerifiedDomainEmails": ["someone@corp.com"]}}
            ),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        assert len(_graphql_dispatches(c)) == 2
        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert sorted(u.source_user_id for u in persisted) == ["30", "31"]

    async def test_login_outside_github_charset_is_never_interpolated(self) -> None:
        """Logins are dropped, not escaped: a value that could break out of the
        GraphQL string literal must never reach the query builder."""
        c = make_mock_connector()
        evil = make_named_user(user_id=15, login='x") { viewer { login } } #', email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([evil]),
            "get_user": failed_response("not found"),
        })

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        assert _graphql_dispatches(c) == []

    def test_query_shape(self) -> None:
        query = UsersSync._verified_domain_query("acme", ["alice", "bob"])
        assert 'u0: user(login: "alice")' in query
        assert 'u1: user(login: "bob")' in query
        assert query.count('organizationVerifiedDomainEmails(login: "acme")') == 2
        assert query.startswith("query {")


class TestIdentityBinding:
    """The connector must never invent a PipesHub identity from a GitHub email."""

    async def test_configuring_user_gets_no_special_treatment(self) -> None:
        """The team connector derives identity solely from GitHub org membership.
        Whoever configured the connector is resolved like any other principal —
        the connector never falls back to their known PipesHub email."""
        c = make_mock_connector()
        c.creator_email = "darshan.godase@pipeshub.com"
        c._github_login = "darshangodase"
        # The org listing returns them as a partial member (no email) — as GitHub does.
        c.runtime.ds_call.return_value = ok_response([
            make_named_user(user_id=142865992, login="darshangodase"),
        ])

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    async def test_email_outside_the_platform_directory_is_still_persisted(self) -> None:
        """A collaborator whose address PipesHub has not seen is still that
        person. Gating on the directory would silently drop real collaborators;
        creating the AppUser now means the permission is already in place when
        they are provisioned or first sign in."""
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            make_named_user(user_id=77, login="stranger", email="stranger@gmail.com", completed=True),
        ])

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert [(u.source_user_id, u.email) for u in persisted] == [("77", "stranger@gmail.com")]

    async def test_matching_email_still_binds_normally(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            make_named_user(user_id=88, login="real", email="Real.User@Pipeshub.com", completed=True),
        ])

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert [u.source_user_id for u in persisted] == ["88"]

    async def test_directory_read_is_not_required_to_bind(self) -> None:
        """AppUser creation must not depend on the platform directory at all."""
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            make_named_user(user_id=99, login="jill", email="jill@example.com", completed=True),
        ])

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async((["acme"], True))

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert [u.source_user_id for u in persisted] == ["99"]


def MagicMock_async(return_value: object) -> object:
    """Return an async-callable MagicMock stand-in returning ``return_value``."""
    from unittest.mock import AsyncMock
    return AsyncMock(return_value=return_value)


def _ds_call_by_method(c: MagicMock, mapping: dict[str, object]) -> object:
    """Build a ``ds_call`` side_effect dispatching on the mocked data_source
    method *object identity* (``c.data_source.<name>``) — robust against
    ``MagicMock`` not preserving ``__name__`` on child mocks."""
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _dispatch(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        return failed_response(f"unmocked method {method!r}")

    return _dispatch


class TestResolveCollaboratorPrincipals:
    """Identity source for individual-owned repos: their collaborators exist in
    no org listing, so they are bound to AppUsers from the repo's own
    collaborator rows (public-profile phase only). Additive — the org flow
    never calls this."""

    @staticmethod
    def _collab(uid: int = 7, login: str = "bob", email: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(id=uid, login=login, email=email, type="User")

    async def test_resolves_via_profile_and_persists_app_user(self) -> None:
        c = make_mock_connector()
        sync = UsersSync(c)
        c.runtime.ds_call.return_value = ok_response(
            SimpleNamespace(id=7, login="bob", name="Bob", email="bob@example.com", type="User")
        )

        resolved = await sync.resolve_collaborator_principals({7: self._collab()})

        assert resolved == {7}
        c.data_entities_processor.on_new_app_users.assert_awaited_once()
        (app_users,) = c.data_entities_processor.on_new_app_users.await_args.args
        assert app_users[0].source_user_id == "7"
        assert app_users[0].email == "bob@example.com"

    async def test_payload_email_needs_no_profile_fetch(self) -> None:
        c = make_mock_connector()
        sync = UsersSync(c)

        resolved = await sync.resolve_collaborator_principals(
            {7: self._collab(email="bob@example.com")}
        )

        assert resolved == {7}
        c.runtime.ds_call.assert_not_awaited()

    async def test_principals_already_enumerated_are_skipped(self) -> None:
        """An org member collaborating on an individual repo was already
        resolved (or found unresolvable) by user sync — no per-repo refetch."""
        c = make_mock_connector()
        sync = UsersSync(c)
        sync._principal_ids = {7}

        resolved = await sync.resolve_collaborator_principals({7: self._collab()})

        assert resolved == set()
        c.runtime.ds_call.assert_not_awaited()
        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    async def test_unresolvable_id_is_attempted_once_per_sync(self) -> None:
        c = make_mock_connector()
        sync = UsersSync(c)
        c.runtime.ds_call.return_value = ok_response(
            SimpleNamespace(id=7, login="bob", name=None, email=None, type="User")
        )

        first = await sync.resolve_collaborator_principals({7: self._collab()})
        second = await sync.resolve_collaborator_principals({7: self._collab()})

        assert first == set() and second == set()
        c.runtime.ds_call.assert_awaited_once()  # not refetched for repo #2

    async def test_bot_rows_are_ignored(self) -> None:
        c = make_mock_connector()
        sync = UsersSync(c)

        resolved = await sync.resolve_collaborator_principals(
            {9: SimpleNamespace(id=9, login="dependabot[bot]", email=None, type="Bot")}
        )

        assert resolved == set()
        c.runtime.ds_call.assert_not_awaited()
