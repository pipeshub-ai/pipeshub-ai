"""Strict OpenAPI audit of DELETE /api/v1/teams/{teamId}.

Only an OWNER may delete. A team that does not exist and a team the caller has
no role on get the same 404, so the status does not reveal which ids exist;
a member who is not an owner gets 403.
"""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from teams_audit_support import (
    INVALID_BEARER,
    INVALID_PATH_SEGMENT,
    MISSING_TEAM_ID,
    TEAM_ROUTE,
    UNSAFE_TEAM_IDS,
    ScopedCaller,
    error_of,
    request_as,
    validation_fields,
)

from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

NOT_FOUND_OR_NO_ACCESS = "This team was removed, or you no longer have access. Refresh the page and try again."


def _deleted(resp: Any) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert resp.json() == {"status": "success", "message": "Team deleted successfully"}


def _not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == NOT_FOUND_OR_NO_ACCESS


def test_owner_deletes_the_team_and_a_second_delete_is_not_found(
    teams_client: TeamsClient, team: dict[str, Any]
) -> None:
    _deleted(teams_client.delete_team(team["id"]))
    _not_found(teams_client.delete_team(team["id"]))
    gone = teams_client.get_team(team["id"])
    assert gone.status_code == 404, gone.text[:300]


def test_body_is_ignored(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    with outside_request_contract("DELETE documents no body; one sent anyway is ignored"):
        _deleted(teams_client.delete(f"/{team['id']}", json={"force": True}))


def test_team_that_does_not_exist_is_not_found(teams_client: TeamsClient) -> None:
    _not_found(teams_client.delete_team(MISSING_TEAM_ID))


def test_team_the_caller_has_no_role_on_is_not_found(team: dict[str, Any], second_user: SecondUser) -> None:
    _not_found(request_as(second_user, "DELETE", f"/{team['id']}"))


@pytest.mark.parametrize("role", ["READER", "WRITER"])
def test_member_who_is_not_owner_is_forbidden(make_team: Any, second_user: SecondUser, role: str) -> None:
    team = make_team(userRoles=[{"userId": second_user.user_id, "role": role}])
    resp = request_as(second_user, "DELETE", f"/{team['id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == "User does not have permission to delete this team"


def test_the_all_team_cannot_be_deleted(teams_client: TeamsClient, pipeshub_client: PipeshubClient) -> None:
    resp = teams_client.delete_team(f"all_{pipeshub_client.org_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == "The default All team cannot be deleted"


def test_all_team_of_another_org_is_not_found(teams_client: TeamsClient) -> None:
    _not_found(teams_client.delete_team(f"all_{'a' * 24}"))


@pytest.mark.parametrize(
    "team_id",
    [
        pytest.param("not-a-uuid", id="not-a-uuid"),
        pytest.param("00000000-0000-0000-0000-000000000001", id="uuid-version-0"),
        pytest.param("all_123", id="all-team-with-short-org-id"),
    ],
)
def test_malformed_team_id_fails_validation(teams_client: TeamsClient, team_id: str) -> None:
    resp = teams_client.delete_team(team_id)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert validation_fields(resp) == {"params.teamId"}



@pytest.mark.parametrize("team_id", UNSAFE_TEAM_IDS)
def test_unsafe_team_id_is_refused_by_the_path_guard(teams_client: TeamsClient, team_id: str) -> None:
    resp = teams_client.delete(f"/{team_id}")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    error = error_of(resp)
    assert error["code"] == "HTTP_BAD_REQUEST", error
    assert error["message"] == INVALID_PATH_SEGMENT

@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(teams_client: TeamsClient, headers: dict[str, str]) -> None:
    resp = teams_client.delete(f"/{MISSING_TEAM_ID}", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)


def test_oauth_token_without_team_write_is_forbidden(team_read_scope: ScopedCaller, team: dict[str, Any]) -> None:
    resp = team_read_scope("DELETE", f"/{team['id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: team:write"
