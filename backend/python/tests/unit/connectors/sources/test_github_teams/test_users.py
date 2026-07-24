"""Unit tests for github_teams UsersSync (5-phase email resolution).

Covers:
- Phase 1: visible email (already-complete member) + enrichment fallback.
- Phase 2: cached AppUser lookup by GitHub numeric id.
- Phase 3: commit-email extraction (author-login search), noreply skip.
- Phase 4: platform reverse lookup (author-email search -> login match).
- Phase 5: pseudo-group fallback for members that stay unresolved.
- _resolve_target_orgs: ORG_IDS / REPO_IDS filter precedence.
- Creator injection when the org member listing fails entirely.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.connectors.sources.github_teams.users import UsersSync, _is_noreply_email

from .conftest import failed_response, make_mock_connector, make_named_user, ok_response

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
        org_filter.operator = SimpleNamespace(value="in")
        c.sync_filters = {users_mod.SyncFilterKey.ORG_IDS: org_filter}

        sync = UsersSync(c)
        orgs = await sync._resolve_target_orgs()
        assert orgs == ["acme"]

    async def test_repo_ids_narrows_org_scope_without_org_filter(self) -> None:
        c = make_mock_connector()
        import app.connectors.sources.github_teams.users as users_mod
        repo_filter = MagicMock()
        repo_filter.is_empty.return_value = False
        repo_filter.value = ["acme/widgets", "acme/gadgets", "other/thing"]
        repo_filter.operator = SimpleNamespace(value="in")
        c.sync_filters = {users_mod.SyncFilterKey.REPO_IDS: repo_filter}

        sync = UsersSync(c)
        orgs = await sync._resolve_target_orgs()
        assert orgs == ["acme", "other"]

    async def test_no_filters_discovers_all_visible_orgs(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            SimpleNamespace(login="acme"), SimpleNamespace(login="other"),
        ])
        sync = UsersSync(c)
        orgs = await sync._resolve_target_orgs()
        assert orgs == ["acme", "other"]


class TestSyncUsersPhases:
    async def test_phase1_visible_email_resolved_directly(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = ok_response([
            make_named_user(user_id=1, login="alice", email="alice@example.com", completed=True),
        ])
        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(["acme"])

        await sync.sync_users()

        c.data_entities_processor.on_new_app_users.assert_awaited_once()
        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert len(persisted) == 1
        assert persisted[0].email == "alice@example.com"
        assert persisted[0].source_user_id == "1"

    async def test_phase1_enrichment_recovers_email_for_partial_member(self) -> None:
        c = make_mock_connector()
        # list_org_members returns a partial (incomplete) member without email.
        partial = make_named_user(user_id=2, login="bob", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "get_user": ok_response(make_named_user(user_id=2, login="bob", email="bob@example.com", completed=True)),
        })
        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(["acme"])

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
        sync._resolve_target_orgs = MagicMock_async(["acme"])

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "carol@example.com"

    async def test_phase3_commit_email_extraction_skips_noreply(self) -> None:
        c = make_mock_connector()
        partial = make_named_user(user_id=4, login="dave", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "get_user": failed_response("not found"),
        })
        noreply_hit = SimpleNamespace(commit=SimpleNamespace(author=SimpleNamespace(email="4+dave@users.noreply.github.com")))
        real_hit = SimpleNamespace(commit=SimpleNamespace(author=SimpleNamespace(email="dave@example.com")))
        c.runtime.search_call.return_value = ok_response([noreply_hit, real_hit])

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(["acme"])

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "dave@example.com"

    async def test_phase4_reverse_lookup_by_author_email_search(self) -> None:
        c = make_mock_connector()
        partial = make_named_user(user_id=5, login="erin", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "get_user": failed_response("not found"),
        })
        # Phase 3 (commit-by-login) finds nothing.
        c.runtime.search_call.side_effect = _ds_call_by_method(c, {
            "search_commits_by_author_login": failed_response("no matches"),
            "search_commits_by_author_email": ok_response([SimpleNamespace(author=SimpleNamespace(login="erin"))]),
        })
        c.data_entities_processor.get_all_active_users.return_value = [
            SimpleNamespace(email="erin@example.com"),
        ]

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(["acme"])

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert persisted[0].email == "erin@example.com"

    async def test_phase5_pseudo_group_created_for_unresolved_member(self) -> None:
        c = make_mock_connector()
        partial = make_named_user(user_id=6, login="frank", email=None, completed=False)
        c.runtime.ds_call.side_effect = _ds_call_by_method(c, {
            "list_org_members": ok_response([partial]),
            "get_user": failed_response("not found"),
        })
        c.runtime.search_call.return_value = failed_response("no matches")
        c.data_entities_processor.get_all_active_users.return_value = []

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(["acme"])

        await sync.sync_users()

        c.data_entities_processor.on_new_app_users.assert_not_awaited()
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()
        groups_arg = c.data_entities_processor.on_new_user_groups.call_args.args[0]
        assert len(groups_arg) == 1
        pseudo_group, members = groups_arg[0]
        assert pseudo_group.source_user_group_id == "6"
        assert members == []

    async def test_creator_injected_when_member_listing_fails_entirely(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("403 forbidden")
        c.creator_email = "creator@example.com"
        c._github_user_id = 999
        c._github_login = "creator-login"

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(["acme"])

        await sync.sync_users()

        persisted = c.data_entities_processor.on_new_app_users.call_args.args[0]
        assert len(persisted) == 1
        assert persisted[0].email == "creator@example.com"

    async def test_aborts_when_every_org_fails_and_no_creator(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.return_value = failed_response("403 forbidden")
        c.creator_email = None
        c._github_user_id = None

        sync = UsersSync(c)
        sync._resolve_target_orgs = MagicMock_async(["acme"])

        with pytest.raises(RuntimeError):
            await sync.sync_users()


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
