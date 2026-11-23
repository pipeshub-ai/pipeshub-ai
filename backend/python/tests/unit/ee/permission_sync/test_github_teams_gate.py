"""Without a licence key a company-wide GitHub connector reads no repository's audience from GitHub."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.github.connector import GitHubPersonalProjectsSync
from app.connectors.sources.github_teams.projects import ProjectsSync
from app.ee.permission_sync.github_teams import GitHubTeamsPermissionsMixin

PUBLIC_REPO = SimpleNamespace(id=1, name="r", full_name="o/r", visibility="public", owner=SimpleNamespace(type="Organization"))


def _sync(cls: type, *, gated: bool) -> ProjectsSync:
    c = MagicMock()
    c.connector_id = "connector-1"
    c.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    c.data_entities_processor.ensure_team_app_edge = AsyncMock()
    c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(success=True, data=[]))
    return cls(c)


@pytest.mark.asyncio
async def test_gated_repository_is_synced_with_no_grants_and_no_collaborator_read() -> None:
    sync = _sync(ProjectsSync, gated=True)

    # Empty, not None: None skips the repository, as when its collaborators could not be read.
    assert await sync._repo_permissions("o", "r", PUBLIC_REPO) == []

    sync.c.runtime.ds_call.assert_not_awaited()
    # A public repository does not open the connector to the company either.
    sync.c.data_entities_processor.ensure_team_app_edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_licensed_repository_still_reads_collaborators_and_visibility() -> None:
    sync = _sync(ProjectsSync, gated=False)

    grants = await sync._repo_permissions("o", "r", PUBLIC_REPO)

    sync.c.runtime.ds_call.assert_awaited_once()
    assert [g.entity_type.value for g in grants] == ["ORG"]
    sync.c.data_entities_processor.ensure_team_app_edge.assert_awaited_once_with("connector-1")


@pytest.mark.asyncio
async def test_personal_connector_reads_no_access_licensed_or_not() -> None:
    sync = _sync(GitHubPersonalProjectsSync, gated=False)

    assert await sync._repo_permissions("o", "r", PUBLIC_REPO) == []

    sync.c.runtime.ds_call.assert_not_awaited()
    sync.c.data_entities_processor.ensure_team_app_edge.assert_not_awaited()


def test_personal_connector_overrides_every_reading_entry_point() -> None:
    for name in ("_sync_repo_members", "_visibility_permissions"):
        assert getattr(GitHubPersonalProjectsSync, name) is not getattr(GitHubTeamsPermissionsMixin, name)
