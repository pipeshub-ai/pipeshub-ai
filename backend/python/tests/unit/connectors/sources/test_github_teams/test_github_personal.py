"""Unit tests for the personal GitHub connector's ProjectsSync override.

Covers:
- _sync_repo_members: routes exclusively through creator_user_permission()
  (ConnectorGroup) — never calls list_collaborators.
- _resolve_repos_with_filters: no filter -> list_user_repos(all); REPO_IDS
  "in" -> per-repo get_repo resolution regardless of owner; "not_in" ->
  exclusion from the discovered candidate list.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.github.connector import GitHubPersonalProjectsSync, GithubConnector
from app.connectors.sources.github import connector as personal_mod
from app.connectors.core.registry.filters import SyncFilterKey
from app.models.permission import EntityType, Permission, PermissionType

from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    make_repo,
    ok_response,
)

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

    async def test_inaccessible_repo_in_filter_is_skipped(self) -> None:
        c = make_mock_connector()
        c.sync_filters = {
            SyncFilterKey.REPO_IDS: SimpleNamespace(
                is_empty=lambda: False, value=["me/gone", "me/kept"], operator_value="in",
            )
        }
        kept = make_repo(repo_id=2, owner_login="me", name="kept")

        def dispatch(method: object, *args: object, **kwargs: object) -> object:
            if method is c.data_source.get_repo:
                _owner, name = args
                if name == "gone":
                    return failed_response("404")
                return ok_response(kept)
            raise AssertionError("unexpected ds_call")

        c.runtime.ds_call.side_effect = dispatch

        result = await GitHubPersonalProjectsSync(c)._resolve_repos_with_filters()

        assert [r.id for r in result] == [2]


class TestPersonalConnectorLifecycle:
    async def test_run_sync_skips_users_and_ensures_connector_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = make_mock_connector()
        c.creator_email = "me@example.com"
        c.created_by = "user-1"
        c.repos.timestamps.cancel = AsyncMock()
        c.repos.timestamps.schedule = MagicMock()
        c.projects.sync_all_repos = AsyncMock()
        c.ensure_connector_group_permission = AsyncMock()
        c._load_creator_email = AsyncMock()
        monkeypatch.setattr(
            personal_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        await GithubConnector.run_sync(c)

        c._load_creator_email.assert_not_awaited()
        c.ensure_connector_group_permission.assert_awaited_once()
        c.projects.sync_all_repos.assert_awaited_once()
        c.repos.timestamps.schedule.assert_called_once()
        assert c.record_sync_point.org_id == c.data_entities_processor.org_id

    async def test_run_sync_loads_creator_email_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = make_mock_connector()
        c.creator_email = None
        c.created_by = "user-1"
        c.repos.timestamps.cancel = AsyncMock()
        c.repos.timestamps.schedule = MagicMock()
        c.projects.sync_all_repos = AsyncMock()
        c.ensure_connector_group_permission = AsyncMock()

        async def load_email() -> None:
            c.creator_email = "loaded@example.com"

        c._load_creator_email = AsyncMock(side_effect=load_email)
        monkeypatch.setattr(
            personal_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        await GithubConnector.run_sync(c)

        c._load_creator_email.assert_awaited_once()
        c.ensure_connector_group_permission.assert_awaited_once()

    async def test_run_sync_warns_when_no_creator_email(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = make_mock_connector()
        c.creator_email = None
        c.created_by = None
        c.repos.timestamps.cancel = AsyncMock()
        c.repos.timestamps.schedule = MagicMock()
        c.projects.sync_all_repos = AsyncMock()
        c.ensure_connector_group_permission = AsyncMock()
        monkeypatch.setattr(
            personal_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        await GithubConnector.run_sync(c)

        c.ensure_connector_group_permission.assert_not_awaited()
        c.projects.sync_all_repos.assert_awaited_once()
        c.logger.warning.assert_called()

    async def test_run_sync_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = make_mock_connector()
        c.creator_email = "me@example.com"
        c.repos.timestamps.cancel = AsyncMock()
        c.projects.sync_all_repos = AsyncMock(side_effect=RuntimeError("api down"))
        c.ensure_connector_group_permission = AsyncMock()
        monkeypatch.setattr(
            personal_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        with pytest.raises(RuntimeError, match="api down"):
            await GithubConnector.run_sync(c)

    async def test_run_incremental_sync_delegates(self) -> None:
        c = make_mock_connector()
        c.run_sync = AsyncMock()
        await GithubConnector.run_incremental_sync(c)
        c.run_sync.assert_awaited_once()

    def test_creator_user_permission_returns_cached_group_permission(self) -> None:
        fake = MagicMock()
        perm = Permission(entity_type=EntityType.GROUP, external_id="internal-1", type=PermissionType.OWNER)
        fake._connector_group_permission = perm
        assert GithubConnector.creator_user_permission(fake) is perm

    async def test_create_connector_builds_personal_instance(self) -> None:
        processor = MagicMock()
        processor.org_id = "org-1"

        connector = await GithubConnector.create_connector(
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="conn-personal-1",
            scope="personal",
            created_by="creator-1",
            data_entities_processor=processor,
        )

        assert isinstance(connector, GithubConnector)
        assert connector.connector_id == "conn-personal-1"
        assert connector.created_by == "creator-1"
