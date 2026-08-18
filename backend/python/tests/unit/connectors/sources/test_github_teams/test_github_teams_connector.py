"""Unit tests for GitHubTeamsConnector.run_sync wiring.

``run_sync`` is exercised as an unbound method against the shared mock
connector: instantiating the real class would require a live config service,
OAuth client and graph provider, none of which this behaviour depends on.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams import connector as connector_mod
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

from tests.unit.connectors.sources.test_github_teams.conftest import make_mock_connector

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _runnable_connector() -> object:
    c = make_mock_connector()
    c.repos.timestamps.cancel = AsyncMock()
    c.repos.timestamps.schedule = lambda: None
    c.users.sync_users = AsyncMock()
    c.projects.sync_all_repos = AsyncMock()
    return c


class TestTeamAppEdge:
    """Without a Teams->App edge the record-access query's
    ``connectorId IN user_apps_ids`` pre-filter excludes every GitHub record,
    making a public repo's ORG grant unreachable for users whose GitHub
    account never resolved to an AppUser."""

    async def test_run_sync_ensures_the_team_app_edge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c = _runnable_connector()
        monkeypatch.setattr(
            connector_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        await GitHubTeamsConnector.run_sync(c)

        c.tx_store.ensure_team_app_edge.assert_awaited_once_with(
            c.connector_id, c.data_entities_processor.org_id,
        )

    async def test_edge_is_established_before_user_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A user sync that raises (e.g. org discovery failure) must not leave
        the connector unreachable for the whole org."""
        c = _runnable_connector()
        c.users.sync_users = AsyncMock(side_effect=RuntimeError("org discovery failed"))
        monkeypatch.setattr(
            connector_mod, "load_connector_filters", AsyncMock(return_value=({}, {})),
        )

        with pytest.raises(RuntimeError):
            await GitHubTeamsConnector.run_sync(c)

        c.tx_store.ensure_team_app_edge.assert_awaited_once()
