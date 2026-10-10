"""Strict OpenAPI audit of GET /api/v1/teams/{teamId}."""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import assert_strict_openapi_exchange
from teams_audit_support import (
    INVALID_BEARER,
    INVALID_PATH_SEGMENT,
    MISSING_TEAM_ID,
    TEAM_ROUTE,
    UNSAFE_TEAM_IDS,
    ScopedCaller,
    error_of,
    members_by_user,
    request_as,
    validation_fields,
)

from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit


def _get(resp: Any) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert resp.json()["status"] == "success"
    assert resp.json()["message"] == "Team fetched successfully"
    return resp.json()["team"]


def test_owner_reads_the_team(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    got = _get(teams_client.get_team(team["id"]))
    assert got["id"] == team["id"]
    assert got["name"] == team["name"]
    assert got["canEdit"] is True and got["canDelete"] is True and got["canManageMembers"] is True


def test_member_reads_the_team_without_edit_rights(
    make_team: Any, second_user: SecondUser
) -> None:
    team = make_team(userRoles=[{"userId": second_user.user_id, "role": "READER"}])
    got = _get(request_as(second_user, "GET", f"/{team['id']}"))
    assert members_by_user(got)[second_user.user_id]["role"] == "READER"
    assert got["canEdit"] is False and got["canDelete"] is False and got["canManageMembers"] is False


def test_user_outside_the_team_can_still_read_it(team: dict[str, Any], second_user: SecondUser) -> None:
    got = _get(request_as(second_user, "GET", f"/{team['id']}"))
    assert got["id"] == team["id"]
    assert second_user.user_id not in members_by_user(got)
    assert got["canEdit"] is False and got["canDelete"] is False


def test_all_team_is_addressable(teams_client: TeamsClient, pipeshub_client: PipeshubClient) -> None:
    team_id = f"all_{pipeshub_client.org_id}"
    got = _get(teams_client.get_team(team_id))
    assert got["id"] == team_id


def test_team_that_does_not_exist_is_not_found(teams_client: TeamsClient) -> None:
    resp = teams_client.get_team(MISSING_TEAM_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == "Team not found"


@pytest.mark.parametrize(
    "team_id",
    [
        pytest.param("not-a-uuid", id="not-a-uuid"),
        pytest.param("00000000-0000-0000-0000-000000000001", id="uuid-version-0"),
        pytest.param("all_123", id="all-team-with-short-org-id"),
    ],
)
def test_malformed_team_id_fails_validation(teams_client: TeamsClient, team_id: str) -> None:
    resp = teams_client.get_team(team_id)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert validation_fields(resp) == {"params.teamId"}



@pytest.mark.parametrize("team_id", UNSAFE_TEAM_IDS)
def test_unsafe_team_id_is_refused_by_the_path_guard(teams_client: TeamsClient, team_id: str) -> None:
    resp = teams_client.get(f"/{team_id}")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    error = error_of(resp)
    assert error["code"] == "HTTP_BAD_REQUEST", error
    assert error["message"] == INVALID_PATH_SEGMENT


def test_path_guard_runs_before_authentication(teams_client: TeamsClient) -> None:
    resp = teams_client.get("/a%25b", auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == INVALID_PATH_SEGMENT

@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(teams_client: TeamsClient, headers: dict[str, str]) -> None:
    resp = teams_client.get(f"/{MISSING_TEAM_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)


def test_oauth_token_without_team_read_is_forbidden(no_team_scope: ScopedCaller, team: dict[str, Any]) -> None:
    resp = no_team_scope("GET", f"/{team['id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: team:read"
