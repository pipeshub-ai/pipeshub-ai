"""Strict OpenAPI audit of GET /api/v1/teams/{teamId}/users."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from teams_audit_support import (
    INVALID_BEARER,
    INVALID_PATH_SEGMENT,
    MISSING_TEAM_ID,
    TEAM_USERS_ROUTE,
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


def _listed(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    body = resp.json()
    assert body["status"] == "success"
    assert body["message"] == "Team users fetched successfully"
    return body


@pytest.fixture
def two_member_team(make_team: Any, second_user: SecondUser) -> dict[str, Any]:
    return make_team(userRoles=[{"userId": second_user.user_id, "role": "READER"}])


def test_owner_lists_the_members(
    teams_client: TeamsClient, two_member_team: dict[str, Any], second_user: SecondUser, pipeshub_client: PipeshubClient
) -> None:
    body = _listed(teams_client.get(f"/{two_member_team['id']}/users"))
    members = members_by_user(body["team"])
    assert set(members) == {second_user.user_id, str(pipeshub_client.acting_user_id)}
    assert body["team"]["canManageMembers"] is True
    assert body["pagination"] == {
        "page": 1, "limit": 100, "totalCount": 2, "totalPages": 1, "hasNextPage": False, "hasPrevPage": False,
    }


def test_pages_of_one_member(teams_client: TeamsClient, two_member_team: dict[str, Any]) -> None:
    first = _listed(teams_client.get(f"/{two_member_team['id']}/users", params={"page": 1, "limit": 1}))
    second = _listed(teams_client.get(f"/{two_member_team['id']}/users", params={"page": 2, "limit": 1}))
    assert len(first["team"]["members"]) == 1 and len(second["team"]["members"]) == 1
    assert set(members_by_user(first["team"])).isdisjoint(members_by_user(second["team"]))
    assert first["pagination"]["hasNextPage"] is True and first["pagination"]["totalPages"] == 2
    assert second["pagination"]["hasPrevPage"] is True and second["pagination"]["hasNextPage"] is False


def test_search_filters_members_by_email(
    teams_client: TeamsClient, two_member_team: dict[str, Any], second_user: SecondUser
) -> None:
    body = _listed(teams_client.get(f"/{two_member_team['id']}/users", params={"search": second_user.email}))
    assert set(members_by_user(body["team"])) == {second_user.user_id}


@pytest.mark.parametrize("search", [pytest.param("", id="empty"), pytest.param("   ", id="whitespace")])
def test_blank_search_is_ignored(teams_client: TeamsClient, two_member_team: dict[str, Any], search: str) -> None:
    body = _listed(teams_client.get(f"/{two_member_team['id']}/users", params={"search": search}))
    assert body["team"]["memberCount"] == 2 and len(body["team"]["members"]) == 2


def test_unknown_query_parameters_are_dropped(teams_client: TeamsClient, two_member_team: dict[str, Any]) -> None:
    with outside_request_contract("proves a query parameter the spec does not list is dropped, not refused"):
        body = _listed(teams_client.get(f"/{two_member_team['id']}/users", params={"role": "OWNER"}))
    assert len(body["team"]["members"]) == 2


def test_member_reads_without_manage_rights(two_member_team: dict[str, Any], second_user: SecondUser) -> None:
    body = _listed(request_as(second_user, "GET", f"/{two_member_team['id']}/users"))
    assert body["team"]["canManageMembers"] is False and body["team"]["canEdit"] is False


def test_user_outside_the_team_can_still_list_its_members(team: dict[str, Any], second_user: SecondUser) -> None:
    body = _listed(request_as(second_user, "GET", f"/{team['id']}/users"))
    assert second_user.user_id not in members_by_user(body["team"])
    assert body["team"]["memberCount"] == 1


def test_all_team_is_addressable(teams_client: TeamsClient, pipeshub_client: PipeshubClient) -> None:
    body = _listed(teams_client.get(f"/all_{pipeshub_client.org_id}/users", params={"limit": 2}))
    assert body["team"]["id"] == f"all_{pipeshub_client.org_id}"
    assert len(body["team"]["members"]) <= 2


def test_team_that_does_not_exist_is_not_found(teams_client: TeamsClient) -> None:
    resp = teams_client.get(f"/{MISSING_TEAM_ID}/users")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    assert error_of(resp)["message"] == "Team not found"


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"page": "0"}, "query.page", id="page-zero"),
        pytest.param({"limit": "101"}, "query.limit", id="limit-over-100"),
        pytest.param({"limit": "abc"}, "query.limit", id="limit-not-a-number"),
        pytest.param({"search": "x" * 1001}, "query.search", id="search-too-long"),
        pytest.param({"search": "%s"}, "query.search", id="search-format-specifier"),
    ],
)
def test_invalid_query_fails_validation(
    teams_client: TeamsClient, team: dict[str, Any], params: dict[str, str], field: str
) -> None:
    resp = teams_client.get(f"/{team['id']}/users", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    assert validation_fields(resp) == {field}


def test_markup_in_search_is_refused(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    resp = teams_client.get(f"/{team['id']}/users", params={"search": "<b>x</b>"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert_spec_forbids_request(resp, TEAM_USERS_ROUTE)


@pytest.mark.parametrize("name", ["page", "limit"])
def test_fractional_paging_passes_the_gateway_and_is_refused_by_the_service(
    teams_client: TeamsClient, team: dict[str, Any], name: str
) -> None:
    resp = teams_client.get(f"/{team['id']}/users", params={name: "1.5"})
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_UNPROCESSABLE_ENTITY"
    assert_spec_forbids_request(resp, TEAM_USERS_ROUTE)


def test_malformed_team_id_fails_validation(teams_client: TeamsClient) -> None:
    resp = teams_client.get("/not-a-uuid/users")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    assert validation_fields(resp) == {"params.teamId"}



@pytest.mark.parametrize("team_id", UNSAFE_TEAM_IDS)
def test_unsafe_team_id_is_refused_by_the_path_guard(teams_client: TeamsClient, team_id: str) -> None:
    resp = teams_client.get(f"/{team_id}/users")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    error = error_of(resp)
    assert error["code"] == "HTTP_BAD_REQUEST", error
    assert error["message"] == INVALID_PATH_SEGMENT

@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(teams_client: TeamsClient, headers: dict[str, str]) -> None:
    resp = teams_client.get(f"/{MISSING_TEAM_ID}/users", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)


def test_oauth_token_without_team_read_is_forbidden(no_team_scope: ScopedCaller, team: dict[str, Any]) -> None:
    resp = no_team_scope("GET", f"/{team['id']}/users")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, TEAM_USERS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: team:read"
