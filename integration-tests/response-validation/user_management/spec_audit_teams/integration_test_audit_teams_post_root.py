"""Strict OpenAPI audit of POST /api/v1/teams.

The Node validator trims ``name`` and drops ``userRoles`` entries that lack a
role or a non-empty ``userId`` before it validates; the team service then makes
the caller the OWNER whatever the body says about them.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest
from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from teams_audit_support import (
    GHOST_USER_ID,
    INVALID_BEARER,
    PEOPLE_GONE,
    TEAMS_ROUTE,
    ScopedCaller,
    error_of,
    members_by_user,
    request_as,
    unique_team_name,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit

TeamFactory = Callable[..., dict[str, Any]]


def _create(teams_client: TeamsClient, created_team_ids: list[str], body: Any) -> dict[str, Any]:
    resp = teams_client.post("/", json=body)
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
    payload = resp.json()
    assert payload["status"] == "success"
    assert payload["message"] == "Team created successfully"
    created_team_ids.append(payload["data"]["id"])
    return payload["data"]


def _admin_user_id(pipeshub_client: PipeshubClient) -> str:
    return str(pipeshub_client.acting_user_id)


def test_minimal_body_makes_the_caller_sole_owner(
    teams_client: TeamsClient, pipeshub_client: PipeshubClient, created_team_ids: list[str]
) -> None:
    name = unique_team_name("minimal")
    team = _create(teams_client, created_team_ids, {"name": name})
    assert team["name"] == name
    assert team["description"] is None
    assert team["memberCount"] == 1
    owner = members_by_user(team)[_admin_user_id(pipeshub_client)]
    assert owner["role"] == "OWNER" and owner["isOwner"] is True
    assert team["createdByUser"]["userId"] == _admin_user_id(pipeshub_client)
    assert team["canEdit"] is True and team["canDelete"] is True


def test_every_field_with_a_member(
    teams_client: TeamsClient, second_user: SecondUser, created_team_ids: list[str]
) -> None:
    name = unique_team_name("full")
    team = _create(
        teams_client,
        created_team_ids,
        {"name": f"  {name}  ", "description": "x" * 500, "userRoles": [{"userId": second_user.user_id, "role": "WRITER"}]},
    )
    assert team["name"] == name, "the name is stored trimmed"
    assert team["description"] == "x" * 500
    assert team["memberCount"] == 2
    member = members_by_user(team)[second_user.user_id]
    assert member["role"] == "WRITER" and member["isOwner"] is False


def test_caller_listed_as_reader_stays_owner(
    teams_client: TeamsClient, pipeshub_client: PipeshubClient, created_team_ids: list[str]
) -> None:
    admin = _admin_user_id(pipeshub_client)
    team = _create(
        teams_client,
        created_team_ids,
        {"name": unique_team_name("self"), "userRoles": [{"userId": admin, "role": "READER"}]},
    )
    assert team["memberCount"] == 1
    assert members_by_user(team)[admin]["role"] == "OWNER"


def test_incomplete_member_entries_are_dropped_not_refused(
    teams_client: TeamsClient, second_user: SecondUser, created_team_ids: list[str]
) -> None:
    body = {
        "name": unique_team_name("partial"),
        "userRoles": [{"userId": second_user.user_id}, {"userId": "", "role": "READER"}, {"role": "WRITER"}],
    }
    with outside_request_contract("userRoles entries without a role or userId are tolerated and dropped"):
        team = _create(teams_client, created_team_ids, body)
    assert team["memberCount"] == 1
    assert second_user.user_id not in members_by_user(team)


def test_unknown_fields_are_stripped_including_legacy_member_fields(
    teams_client: TeamsClient, second_user: SecondUser, created_team_ids: list[str]
) -> None:
    body = {
        "name": unique_team_name("legacy"),
        "userIds": [second_user.user_id],
        "role": "WRITER",
        "orgId": "0123456789abcdef01234567",
    }
    with outside_request_contract("proves fields outside the schema, legacy userIds/role included, are dropped"):
        team = _create(teams_client, created_team_ids, body)
    assert team["memberCount"] == 1
    assert second_user.user_id not in members_by_user(team)
    assert team["orgId"] != body["orgId"]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.name", id="empty-body"),
        pytest.param({"name": ""}, "body.name", id="empty-name"),
        pytest.param({"name": "   "}, "body.name", id="blank-name"),
        pytest.param({"name": 5}, "body.name", id="name-not-string"),
        pytest.param({"name": "x" * 101}, "body.name", id="name-over-100"),
        pytest.param({"name": "ok", "description": "x" * 501}, "body.description", id="description-over-500"),
        pytest.param({"name": "ok", "description": None}, "body.description", id="description-null"),
        pytest.param({"name": "ok", "userRoles": "all"}, "body.userRoles", id="user-roles-not-array"),
        pytest.param(
            {"name": "ok", "userRoles": [{"userId": "abc", "role": "READER"}]},
            "body.userRoles.0.userId",
            id="member-id-not-object-id",
        ),
        pytest.param(
            {"name": "ok", "userRoles": [{"userId": GHOST_USER_ID, "role": "ADMIN"}]},
            "body.userRoles.0.role",
            id="unknown-role",
        ),
    ],
)
def test_invalid_body_fails_validation(teams_client: TeamsClient, body: Any, field: str) -> None:
    resp = teams_client.post("/", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
    assert field in validation_fields(resp)


def test_missing_body_fails_validation(teams_client: TeamsClient) -> None:
    resp = teams_client.post("/")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
    assert "body.name" in validation_fields(resp)


def test_member_that_does_not_exist_is_refused(teams_client: TeamsClient) -> None:
    resp = teams_client.post(
        "/", json={"name": unique_team_name("ghost"), "userRoles": [{"userId": GHOST_USER_ID, "role": "READER"}]}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert error_of(resp)["message"] == PEOPLE_GONE


def test_non_admin_member_can_create_a_team(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", json={"name": unique_team_name("member")})
    assert resp.status_code == 201, resp.text[:500]
    team = resp.json()["data"]
    try:
        assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
        assert members_by_user(team)[second_user.user_id]["role"] == "OWNER"
    finally:
        # Only the owner can delete it; the admin is answered 404.
        deleted = request_as(second_user, "DELETE", f"/{team['id']}")
        assert deleted.status_code == 200, deleted.text[:300]


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(teams_client: TeamsClient, headers: dict[str, str]) -> None:
    resp = teams_client.post("/", auth=False, headers=headers, json={"name": unique_team_name("anon")})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)


def test_oauth_token_without_team_write_is_forbidden(team_read_scope: ScopedCaller) -> None:
    resp = team_read_scope("POST", json={"name": unique_team_name("scope")})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAMS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: team:write"
