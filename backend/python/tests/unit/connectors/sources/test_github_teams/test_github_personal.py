"""Unit tests for the personal GitHub connector's ProjectsSync override.

Covers:
- _sync_repo_members: routes exclusively through creator_user_permission()
  (ConnectorGroup) — never calls list_collaborators/list_repo_teams.
- _resolve_repos_with_filters: no filter -> list_user_repos(owner); REPO_IDS
  "in" -> per-repo get_repo resolution; "not_in" -> exclusion from the
  discovered candidate list.
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


class TestResolveReposWithFilters:
    async def test_no_filter_uses_list_user_repos_owner_scope(self) -> None:
        c = make_mock_connector()
        c.sync_filters = None
        repos = [make_repo(repo_id=1, name="a"), make_repo(repo_id=2, name="b")]
        c.runtime.ds_call.return_value = ok_response(repos)

        sync = GitHubPersonalProjectsSync(c)
        result, discovery_complete = await sync._resolve_repos_with_filters()

        assert result == repos
        assert discovery_complete is True
        args = c.runtime.ds_call.call_args.args
        assert args[0] is c.data_source.list_user_repos
        assert args[1:] == (None, "owner")

    async def test_repo_ids_in_filter_resolves_each_by_full_name(self) -> None:
        c = make_mock_connector()
        repo_filter = SimpleNamespace(
            is_empty=lambda: False, value=["me/widgets", "me/gadgets"],
            operator=SimpleNamespace(value="in"),
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
        result, discovery_complete = await sync._resolve_repos_with_filters()

        assert {r.id for r in result} == {1, 2}
        assert discovery_complete is True

    async def test_repo_ids_not_in_filter_excludes_from_candidates(self) -> None:
        c = make_mock_connector()
        repo_filter = SimpleNamespace(
            is_empty=lambda: False, value=["me/excluded"],
            operator=SimpleNamespace(value="not_in"),
        )
        c.sync_filters = {SyncFilterKey.REPO_IDS: repo_filter}
        kept = make_repo(repo_id=1, owner_login="me", name="kept")
        excluded = make_repo(repo_id=2, owner_login="me", name="excluded")
        c.runtime.ds_call.return_value = ok_response([kept, excluded])

        sync = GitHubPersonalProjectsSync(c)
        result, discovery_complete = await sync._resolve_repos_with_filters()

        assert result == [kept]
        assert discovery_complete is True

    async def test_malformed_filter_value_skipped(self) -> None:
        c = make_mock_connector()
        repo_filter = SimpleNamespace(
            is_empty=lambda: False, value=["no-slash-here"],
            operator=SimpleNamespace(value="in"),
        )
        c.sync_filters = {SyncFilterKey.REPO_IDS: repo_filter}

        sync = GitHubPersonalProjectsSync(c)
        result, discovery_complete = await sync._resolve_repos_with_filters()

        assert result == []
        assert discovery_complete is False

    async def test_list_user_repos_failure_raises(self) -> None:
        c = make_mock_connector()
        c.sync_filters = None
        c.runtime.ds_call.return_value = failed_response("500")

        sync = GitHubPersonalProjectsSync(c)
        with pytest.raises(RuntimeError):
            await sync._resolve_repos_with_filters()
