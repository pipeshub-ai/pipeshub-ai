"""Unit tests for the personal GitHub connector's ProjectsSync override.

Covers:
- _sync_repo_members: routes exclusively through creator_user_permission()
  (ConnectorGroup) — never calls list_collaborators/list_repo_teams.
- _resolve_repos_with_filters: no filter -> list_user_repos(all); REPO_IDS
  "in" -> per-repo get_repo resolution regardless of owner; "not_in" ->
  exclusion from the discovered candidate list.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.connectors.sources.github.connector import GitHubPersonalProjectsSync
from app.connectors.core.registry.filters import SyncFilterKey
from app.models.permission import EntityType, Permission, PermissionType

from .conftest import failed_response, make_mock_connector, make_repo, ok_response

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class TestSyncRepoMembers:
    async def test_routes_through_creator_permission_only(self) -> None:
        c = make_mock_connector()
        permission = Permission(entity_type=EntityType.USER, email="me@example.com", type=PermissionType.OWNER)
        c.creator_user_permission = lambda: permission

        sync = GitHubPersonalProjectsSync(c)
        perms = await sync._sync_repo_members("me", "widgets")

        assert perms == [permission]
        c.runtime.ds_call.assert_not_awaited()

    async def test_no_creator_permission_returns_empty(self) -> None:
        c = make_mock_connector()
        c.creator_user_permission = lambda: None

        sync = GitHubPersonalProjectsSync(c)
        perms = await sync._sync_repo_members("me", "widgets")

        assert perms == []

    @pytest.mark.parametrize("visibility", ["public", "internal", "private"])
    async def test_visibility_never_grants_beyond_the_connector_group(self, visibility: str) -> None:
        """The team connector maps a public repo to Permission(READ, ORG) —
        that is its real GitHub audience. A personal connector must not: an ORG
        grant survives removing someone from ConnectorGroup, defeating the
        single-edge revocation the group exists for. Every other personal
        connector emits zero ORG grants."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        repo.visibility = visibility

        sync = GitHubPersonalProjectsSync(c)

        assert sync._visibility_permissions(repo) == []


class TestResolveReposWithFilters:
    async def test_no_filter_lists_every_repo_the_account_reaches(self) -> None:
        """``type="all"`` — owner, organization member and collaborator. Every
        other Personal connector means "content my account can see"; ``owner``
        excluded org repos the token could read perfectly well."""
        c = make_mock_connector()
        c.sync_filters = None
        repos = [make_repo(repo_id=1, name="a"), make_repo(repo_id=2, name="b")]
        c.runtime.ds_call.return_value = ok_response(repos)

        sync = GitHubPersonalProjectsSync(c)
        result = await sync._resolve_repos_with_filters()

        assert result == repos
        args = c.runtime.ds_call.call_args.args
        assert args[0] is c.data_source.list_user_repos
        assert args[1:] == (None, "all")

    async def test_repo_owned_by_someone_else_is_accepted(self) -> None:
        """An ownership gate here rejected org and public repos the picker had
        just offered, so a selected repo synced nothing but an error line.
        ``get_repo`` succeeding IS the access check — same as the team
        connector's explicit path."""
        c = make_mock_connector()
        c._github_login = "darshangodase"
        c.sync_filters = {
            SyncFilterKey.REPO_IDS: SimpleNamespace(
                is_empty=lambda: False, value=["pipeshub-ai/pipeshub-ai"], operator_value="in",
            )
        }
        foreign = make_repo(repo_id=944080744, owner_login="pipeshub-ai", name="pipeshub-ai")
        c.runtime.ds_call.return_value = ok_response(foreign)

        sync = GitHubPersonalProjectsSync(c)
        result = await sync._resolve_repos_with_filters()

        assert [r.id for r in result] == [944080744]

    async def test_repo_ids_in_filter_resolves_each_by_full_name(self) -> None:
        c = make_mock_connector()
        repo_filter = SimpleNamespace(
            is_empty=lambda: False, value=["me/widgets", "me/gadgets"],
            operator_value="in",
        )
        c.sync_filters = {SyncFilterKey.REPO_IDS: repo_filter}
        widgets = make_repo(repo_id=1, owner_login="me", name="widgets")
        gadgets = make_repo(repo_id=2, owner_login="me", name="gadgets")

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.get_repo:
                _owner, name = args
                return ok_response(widgets if name == "widgets" else gadgets)
            raise AssertionError("unexpected ds_call")

        c.runtime.ds_call.side_effect = dispatch

        sync = GitHubPersonalProjectsSync(c)
        result = await sync._resolve_repos_with_filters()

        assert {r.id for r in result} == {1, 2}

    async def test_repo_ids_not_in_filter_excludes_from_candidates(self) -> None:
        c = make_mock_connector()
        repo_filter = SimpleNamespace(
            is_empty=lambda: False, value=["me/excluded"],
            operator_value="not_in",
        )
        c.sync_filters = {SyncFilterKey.REPO_IDS: repo_filter}
        kept = make_repo(repo_id=1, owner_login="me", name="kept")
        excluded = make_repo(repo_id=2, owner_login="me", name="excluded")
        c.runtime.ds_call.return_value = ok_response([kept, excluded])

        sync = GitHubPersonalProjectsSync(c)
        result = await sync._resolve_repos_with_filters()

        assert result == [kept]

    async def test_malformed_filter_value_skipped(self) -> None:
        c = make_mock_connector()
        repo_filter = SimpleNamespace(
            is_empty=lambda: False, value=["no-slash-here"],
            operator_value="in",
        )
        c.sync_filters = {SyncFilterKey.REPO_IDS: repo_filter}

        sync = GitHubPersonalProjectsSync(c)
        result = await sync._resolve_repos_with_filters()

        assert result == []

    async def test_list_user_repos_failure_returns_empty(self) -> None:
        c = make_mock_connector()
        c.sync_filters = None
        c.runtime.ds_call.return_value = failed_response("500")

        sync = GitHubPersonalProjectsSync(c)
        result = await sync._resolve_repos_with_filters()

        assert result == []
