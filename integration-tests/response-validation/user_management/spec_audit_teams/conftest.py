"""Shared fixtures for the strict OpenAPI audit of /api/v1/teams."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.teams_client import TeamsClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from teams_audit_support import (  # noqa: E402
    NO_TEAM_SCOPE,
    TEAM_READ_SCOPE,
    TEAMS_ROUTE,
    ScopedCaller,
    forget_access_token,
    mint_scoped_token,
    unique_team_name,
)
from strict_openapi import assert_strict_openapi_exchange  # noqa: E402

TeamFactory = Callable[..., dict[str, Any]]


@pytest.fixture
def created_team_ids(teams_client: TeamsClient) -> Iterator[list[str]]:
    """Ids of teams a test created; each is deleted on teardown."""
    ids: list[str] = []
    yield ids
    for team_id in ids:
        resp = teams_client.delete_team(team_id)
        assert resp.status_code in (200, 404), f"cleanup of team {team_id}: {resp.status_code} {resp.text[:300]}"


@pytest.fixture
def make_team(teams_client: TeamsClient, created_team_ids: list[str]) -> TeamFactory:
    """Create a team as the admin (who becomes its OWNER) and return the created team."""

    def _make(**fields: Any) -> dict[str, Any]:
        fields.setdefault("name", unique_team_name())
        resp = teams_client.post("/", json=fields)
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
        team = resp.json()["data"]
        created_team_ids.append(team["id"])
        return team

    return _make


@pytest.fixture
def team(make_team: TeamFactory) -> dict[str, Any]:
    return make_team()


def _scoped(pipeshub_client: PipeshubClient, scope: str) -> Iterator[ScopedCaller]:
    token = mint_scoped_token(pipeshub_client.base_url, scope, pipeshub_client.timeout_seconds)
    try:
        yield ScopedCaller(pipeshub_client.base_url, token, pipeshub_client.timeout_seconds)
    finally:
        forget_access_token(token)


@pytest.fixture
def no_team_scope(pipeshub_client: PipeshubClient) -> Iterator[ScopedCaller]:
    """OAuth token of the suite's client that holds no ``team:*`` scope."""
    yield from _scoped(pipeshub_client, NO_TEAM_SCOPE)


@pytest.fixture
def team_read_scope(pipeshub_client: PipeshubClient) -> Iterator[ScopedCaller]:
    """OAuth token of the suite's client that holds ``team:read`` and not ``team:write``."""
    yield from _scoped(pipeshub_client, TEAM_READ_SCOPE)
