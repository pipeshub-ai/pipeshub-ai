"""Strict OpenAPI audit of PUT /api/v1/teams/{teamId}.

Only a team OWNER may update, and the owner check runs before the team is
looked up, so a team that does not exist is answered 403 rather than 404.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract
from teams_audit_support import (
    GHOST_USER_ID,
    INVALID_BEARER,
    INVALID_PATH_SEGMENT,
    LAST_OWNER,
    MISSING_TEAM_ID,
    NOT_OWNER_ON_UPDATE,
    PEOPLE_GONE,
    TEAM_ROUTE,
    UNSAFE_TEAM_IDS,
    ScopedCaller,
    error_of,
    members_by_user,
    request_as,
    unique_team_name,
    validation_fields,
)

from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit


def _updated(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert resp.json()["status"] == "success"
    assert resp.json()["message"] == "Team updated successfully"
    return resp.json()["team"]


def _put(teams_client: TeamsClient, team_id: str, body: Any) -> dict[str, Any]:
    return _updated(teams_client.put(f"/{team_id}", json=body))


def _admin(pipeshub_client: PipeshubClient) -> str:
    return str(pipeshub_client.acting_user_id)


def test_name_and_description_are_updated(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    name = unique_team_name("renamed")
    got = _put(teams_client, team["id"], {"name": f" {name} ", "description": "updated"})
    assert got["name"] == name
    assert got["description"] == "updated"
    assert got["updatedAtTimestamp"] >= team["updatedAtTimestamp"]


def test_member_lifecycle(teams_client: TeamsClient, team: dict[str, Any], second_user: SecondUser) -> None:
    uid = second_user.user_id
    got = _put(teams_client, team["id"], {"addUserRoles": [{"userId": uid, "role": "READER"}]})
    assert members_by_user(got)[uid]["role"] == "READER"
    got = _put(teams_client, team["id"], {"updateUserRoles": [{"userId": uid, "role": "WRITER"}]})
    assert members_by_user(got)[uid]["role"] == "WRITER"
    got = _put(teams_client, team["id"], {"removeUserIds": [uid]})
    assert uid not in members_by_user(got)
    assert got["memberCount"] == 1


def test_adding_an_existing_member_changes_their_role(
    teams_client: TeamsClient, make_team: Any, second_user: SecondUser
) -> None:
    uid = second_user.user_id
    team = make_team(userRoles=[{"userId": uid, "role": "READER"}])
    got = _put(teams_client, team["id"], {"addUserRoles": [{"userId": uid, "role": "WRITER"}]})
    assert members_by_user(got)[uid]["role"] == "WRITER"
    assert got["memberCount"] == 2


def test_changes_for_people_outside_the_team_are_ignored(
    teams_client: TeamsClient, team: dict[str, Any], second_user: SecondUser
) -> None:
    uid = second_user.user_id
    got = _put(
        teams_client,
        team["id"],
        {"updateUserRoles": [{"userId": uid, "role": "WRITER"}], "removeUserIds": [uid]},
    )
    assert uid not in members_by_user(got)
    assert got["memberCount"] == 1


def test_second_owner_may_be_appointed_and_then_the_first_demoted(
    teams_client: TeamsClient, pipeshub_client: PipeshubClient, make_team: Any, second_user: SecondUser
) -> None:
    uid = second_user.user_id
    team = make_team(userRoles=[{"userId": uid, "role": "READER"}])
    got = _put(teams_client, team["id"], {"updateUserRoles": [{"userId": uid, "role": "OWNER"}]})
    assert members_by_user(got)[uid]["isOwner"] is True
    got = _updated(request_as(second_user, "PUT", f"/{team['id']}", json={"description": "by the second owner"}))
    assert got["description"] == "by the second owner"


@pytest.mark.parametrize(
    "body",
    [pytest.param({}, id="empty-object"), pytest.param(None, id="no-body")],
)
def test_empty_update_changes_only_the_timestamp(
    teams_client: TeamsClient, team: dict[str, Any], body: Any
) -> None:
    resp = teams_client.put(f"/{team['id']}") if body is None else teams_client.put(f"/{team['id']}", json=body)
    got = _updated(resp)
    assert got["name"] == team["name"]
    assert got["description"] == team["description"]


def test_unknown_fields_are_stripped_including_legacy_member_fields(
    teams_client: TeamsClient, team: dict[str, Any], second_user: SecondUser
) -> None:
    body = {"addUserIds": [second_user.user_id], "role": "WRITER", "orgId": "0123456789abcdef01234567"}
    with outside_request_contract("proves fields outside the schema, legacy addUserIds/role included, are dropped"):
        got = _put(teams_client, team["id"], body)
    assert second_user.user_id not in members_by_user(got)


def test_incomplete_member_entries_are_dropped_not_refused(
    teams_client: TeamsClient, team: dict[str, Any], second_user: SecondUser
) -> None:
    body = {"addUserRoles": [{"userId": second_user.user_id}], "updateUserRoles": [{"userId": "", "role": "READER"}]}
    with outside_request_contract("addUserRoles/updateUserRoles entries without a role or userId are dropped"):
        got = _put(teams_client, team["id"], body)
    assert second_user.user_id not in members_by_user(got)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"name": ""}, "body.name", id="empty-name"),
        pytest.param({"name": "   "}, "body.name", id="blank-name"),
        pytest.param({"name": "x" * 101}, "body.name", id="name-over-100"),
        pytest.param({"description": "x" * 501}, "body.description", id="description-over-500"),
        pytest.param({"description": None}, "body.description", id="description-null"),
        pytest.param({"removeUserIds": ["abc"]}, "body.removeUserIds.0", id="remove-id-not-object-id"),
        pytest.param({"removeUserIds": GHOST_USER_ID}, "body.removeUserIds", id="remove-ids-not-array"),
        pytest.param(
            {"addUserRoles": [{"userId": GHOST_USER_ID, "role": "ADMIN"}]}, "body.addUserRoles.0.role", id="unknown-role"
        ),
        pytest.param(
            {"updateUserRoles": [{"userId": "abc", "role": "READER"}]},
            "body.updateUserRoles.0.userId",
            id="update-id-not-object-id",
        ),
    ],
)
def test_invalid_body_fails_validation(
    teams_client: TeamsClient, team: dict[str, Any], body: Any, field: str
) -> None:
    resp = teams_client.put(f"/{team['id']}", json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert field in validation_fields(resp)


@pytest.mark.parametrize(
    "body_for",
    [
        pytest.param(lambda admin: {"removeUserIds": [admin]}, id="remove-last-owner"),
        pytest.param(lambda admin: {"updateUserRoles": [{"userId": admin, "role": "READER"}]}, id="demote-last-owner"),
    ],
)
def test_last_owner_cannot_be_removed_or_demoted(
    teams_client: TeamsClient, pipeshub_client: PipeshubClient, team: dict[str, Any], body_for: Any
) -> None:
    resp = teams_client.put(f"/{team['id']}", json=body_for(_admin(pipeshub_client)))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert error_of(resp)["message"] == LAST_OWNER


def test_member_that_does_not_exist_is_refused(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    resp = teams_client.put(f"/{team['id']}", json={"addUserRoles": [{"userId": GHOST_USER_ID, "role": "READER"}]})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == PEOPLE_GONE


def test_malformed_team_id_fails_validation(teams_client: TeamsClient) -> None:
    resp = teams_client.put("/not-a-uuid", json={"name": "x"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert validation_fields(resp) == {"params.teamId"}


def test_team_that_does_not_exist_is_forbidden_not_missing(teams_client: TeamsClient) -> None:
    resp = teams_client.put(f"/{MISSING_TEAM_ID}", json={"name": "x"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == NOT_OWNER_ON_UPDATE


@pytest.mark.parametrize("role", [None, "READER", "WRITER"])
def test_non_owner_is_forbidden(
    make_team: Any, second_user: SecondUser, role: str | None
) -> None:
    roles = [] if role is None else [{"userId": second_user.user_id, "role": role}]
    team = make_team(userRoles=roles) if roles else make_team()
    resp = request_as(second_user, "PUT", f"/{team['id']}", json={"name": "taken over"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == NOT_OWNER_ON_UPDATE



@pytest.mark.parametrize("team_id", UNSAFE_TEAM_IDS)
def test_unsafe_team_id_is_refused_by_the_path_guard(teams_client: TeamsClient, team_id: str) -> None:
    resp = teams_client.put(f"/{team_id}", json={"name": "x"})
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
    resp = teams_client.put(f"/{MISSING_TEAM_ID}", auth=False, headers=headers, json={"name": "x"})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)


def test_oauth_token_without_team_write_is_forbidden(team_read_scope: ScopedCaller, team: dict[str, Any]) -> None:
    resp = team_read_scope("PUT", f"/{team['id']}", json={"name": "x"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: team:write"
