"""Strict OpenAPI audit of GET /api/v1/teams/user/teams."""

from __future__ import annotations

import time
from typing import Any

import pytest
import requests
from helper.clients.teams_client import TeamsClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract
from teams_audit_support import (
    GHOST_USER_ID,
    INVALID_BEARER,
    USER_TEAMS_ROUTE,
    ScopedCaller,
    error_of,
    request_as,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit


def _listed(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_TEAMS_ROUTE)
    return resp.json()


def _ids(body: dict[str, Any]) -> set[str]:
    return {t["id"] for t in body["teams"]}


def test_search_finds_the_callers_team(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    body = _listed(teams_client.get_user_teams(search=team["name"]))
    assert body["status"] == "success" and body["message"] == "User teams fetched successfully"
    assert _ids(body) == {team["id"]}
    assert body["pagination"] == {
        "page": 1, "limit": 100, "total": 1, "pages": 1, "hasNext": False, "hasPrev": False,
    }


def test_member_sees_the_team_and_outsider_does_not(make_team: Any, team: dict[str, Any], second_user: SecondUser) -> None:
    shared = make_team(userRoles=[{"userId": second_user.user_id, "role": "READER"}])
    body = _listed(request_as(second_user, "GET", "/user/teams", params={"limit": 100}))
    assert shared["id"] in _ids(body)
    assert team["id"] not in _ids(body)


def test_filters_by_creator_and_creation_time(
    teams_client: TeamsClient, team: dict[str, Any], pipeshub_client: PipeshubClient
) -> None:
    created = team["createdAtTimestamp"]
    body = _listed(teams_client.get_user_teams(
        search=team["name"], created_by=pipeshub_client.acting_user_id,
        created_after=created - 1, created_before=created + 1,
    ))
    assert _ids(body) == {team["id"]}
    body = _listed(teams_client.get_user_teams(search=team["name"], created_before=created - 60_000))
    assert body["teams"] == []
    assert body["message"] == "No teams found"
    assert body["pagination"] == {"page": 1, "limit": 100, "total": 0, "pages": 0}


def test_page_past_the_end_reports_zero_pages(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    body = _listed(teams_client.get_user_teams(search=team["name"], page=5, limit=1))
    assert body["teams"] == []
    assert body["pagination"] == {"page": 5, "limit": 1, "total": 1, "pages": 0}


def test_creator_that_does_not_exist_answers_an_empty_list_without_status(teams_client: TeamsClient) -> None:
    body = _listed(teams_client.get_user_teams(created_by=GHOST_USER_ID))
    assert body == {"teams": [], "pagination": {"page": 1, "limit": 10, "total": 0, "pages": 0}}


@pytest.mark.parametrize("search", [pytest.param("", id="empty"), pytest.param("   ", id="whitespace")])
def test_blank_search_is_ignored(teams_client: TeamsClient, team: dict[str, Any], search: str) -> None:
    body = _listed(teams_client.get_user_teams(search=search, created_after=team["createdAtTimestamp"] - 1))
    assert team["id"] in _ids(body)


def test_unknown_query_parameters_are_dropped(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    with outside_request_contract("proves a query parameter the spec does not list is dropped, not refused"):
        body = _listed(teams_client.get_user_teams(search=team["name"], role="READER"))
    assert _ids(body) == {team["id"]}


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"page": "0"}, "query.page", id="page-zero"),
        pytest.param({"limit": "101"}, "query.limit", id="limit-over-100"),
        pytest.param({"limit": "0"}, "query.limit", id="limit-zero"),
        pytest.param({"search": "x" * 1001}, "query.search", id="search-too-long"),
        pytest.param({"search": "%x"}, "query.search", id="search-format-specifier"),
        pytest.param({"created_by": "bad"}, "query.created_by", id="created-by-not-an-object-id"),
        pytest.param({"created_after": "0"}, "query.created_after", id="created-after-zero"),
        pytest.param({"created_after": "abc"}, "query.created_after", id="created-after-not-a-number"),
        pytest.param({"created_before": "1.5"}, "query.created_before", id="created-before-fractional"),
    ],
)
def test_invalid_query_fails_validation(teams_client: TeamsClient, params: dict[str, str], field: str) -> None:
    resp = teams_client.get_user_teams(**params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_TEAMS_ROUTE)
    assert validation_fields(resp) == {field}


def test_markup_in_search_is_refused(teams_client: TeamsClient) -> None:
    resp = teams_client.get_user_teams(search="<i>x</i>")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_TEAMS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert_spec_forbids_request(resp, USER_TEAMS_ROUTE)


@pytest.mark.parametrize("name", ["page", "limit"])
def test_fractional_paging_answers_an_empty_list(teams_client: TeamsClient, name: str) -> None:
    # Python refuses 1.5 with 422; the gateway turns every non-200 from Python into this list.
    with outside_request_contract("a fractional page or limit passes the gateway and Python's refusal is hidden"):
        resp = teams_client.get_user_teams(**{name: "1.5"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, USER_TEAMS_ROUTE)
    assert resp.json() == {"teams": [], "pagination": {"page": 1, "limit": 10, "total": 0, "pages": 0}}


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(teams_client: TeamsClient, headers: dict[str, str]) -> None:
    resp = teams_client.get("/user/teams", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_TEAMS_ROUTE)


def test_oauth_token_without_team_read_is_forbidden(no_team_scope: ScopedCaller) -> None:
    resp = no_team_scope("GET", "/user/teams")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, USER_TEAMS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: team:read"


def test_created_after_now_excludes_existing_teams(teams_client: TeamsClient, team: dict[str, Any]) -> None:
    body = _listed(teams_client.get_user_teams(search=team["name"], created_after=int(time.time() * 1000) + 60_000))
    assert body["teams"] == []
