"""Strict OpenAPI audit of GET /api/v1/userGroups (admins only)."""

from __future__ import annotations

import datetime
from typing import Any

import pytest
import requests
from helper.clients.user_groups_client import UserGroupsClient
from helper.second_user import SecondUser
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange, outside_request_contract
from user_groups_audit_support import (
    ADMIN_REQUIRED,
    GROUPS_ROUTE,
    INVALID_BEARER,
    ScopedCaller,
    error_of,
    request_as,
    validation_fields,
)

pytestmark = pytest.mark.spec_audit


def _listed(resp: requests.Response) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    return resp.json()


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def test_search_finds_the_group_with_its_user_count(
    user_groups_client: UserGroupsClient, make_group: Any, second_user: SecondUser
) -> None:
    group = make_group()
    added = user_groups_client.add_users([second_user.user_id], [group["_id"]])
    assert added.status_code == 200, added.text[:300]
    body = _listed(user_groups_client.get("/", params={"search": group["name"]}))
    assert [g["_id"] for g in body["groups"]] == [group["_id"]]
    listed = body["groups"][0]
    assert listed["userCount"] == 1 and "users" not in listed
    assert body["pagination"] == {
        "page": 1, "limit": 25, "totalCount": 1, "totalPages": 1, "hasNextPage": False, "hasPrevPage": False,
    }


def test_paging(user_groups_client: UserGroupsClient) -> None:
    body = _listed(user_groups_client.get("/", params={"page": 1, "limit": 1}))
    assert len(body["groups"]) == 1
    assert body["pagination"]["limit"] == 1
    assert body["pagination"]["hasNextPage"] is (body["pagination"]["totalCount"] > 1)


def test_creation_date_filters(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    today = _today()
    body = _listed(user_groups_client.get("/", params={"search": group["name"], "createdAfter": today, "createdBefore": today}))
    assert [g["_id"] for g in body["groups"]] == [group["_id"]]
    body = _listed(user_groups_client.get("/", params={"search": group["name"], "createdBefore": "2020-01-01"}))
    assert body["groups"] == []


def test_deleted_groups_are_not_listed(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    assert user_groups_client.delete_group(group["_id"]).status_code == 200
    body = _listed(user_groups_client.get("/", params={"search": group["name"]}))
    assert body["groups"] == []


@pytest.mark.parametrize("search", [pytest.param("", id="empty"), pytest.param("%s", id="format-specifier")])
def test_search_without_markup_is_accepted(user_groups_client: UserGroupsClient, search: str) -> None:
    _listed(user_groups_client.get("/", params={"search": search, "limit": 1}))


def test_unknown_query_parameters_are_dropped(user_groups_client: UserGroupsClient, group: dict[str, Any]) -> None:
    with outside_request_contract("proves a query parameter the spec does not list (type) is dropped"):
        body = _listed(user_groups_client.get("/", params={"search": group["name"], "type": "everyone"}))
    assert [g["_id"] for g in body["groups"]] == [group["_id"]]


def test_fractional_page_is_truncated(user_groups_client: UserGroupsClient) -> None:
    with outside_request_contract("page is read with parseInt, so 1.5 is tolerated as 1"):
        body = _listed(user_groups_client.get("/", params={"page": "1.5", "limit": 1}))
    assert body["pagination"]["page"] == 1


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": "0"}, id="page-zero"),
        pytest.param({"limit": "101"}, id="limit-over-100"),
        pytest.param({"limit": "abc"}, id="limit-not-a-number"),
    ],
)
def test_out_of_range_paging_is_an_internal_error(user_groups_client: UserGroupsClient, params: dict[str, str]) -> None:
    resp = user_groups_client.get("/", params=params)
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp)["code"] == "INTERNAL_ERROR"
    assert_spec_forbids_request(resp, GROUPS_ROUTE)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        pytest.param({"createdAfter": "yesterday"}, "query.createdAfter", id="created-after-not-a-date"),
        pytest.param({"createdBefore": "2020-01-01T00:00:00Z"}, "query.createdBefore", id="created-before-datetime"),
        pytest.param({"createdAfter": "2020-13-45"}, "query.createdAfter", id="created-after-month-13"),
        pytest.param({"createdAfter": "2020-02-30"}, "query.createdAfter", id="created-after-february-30"),
        pytest.param({"createdBefore": "2021-02-29"}, "query.createdBefore", id="created-before-february-29-no-leap"),
    ],
)
def test_invalid_dates_fail_validation(user_groups_client: UserGroupsClient, params: dict[str, str], field: str) -> None:
    resp = user_groups_client.get("/", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert validation_fields(resp) == {field}


@pytest.mark.parametrize("name", ["createdAfter", "createdBefore"])
def test_leap_day_is_a_valid_date(user_groups_client: UserGroupsClient, name: str) -> None:
    resp = user_groups_client.get("/", params={name: "2020-02-29"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)


def test_markup_in_search_is_refused(user_groups_client: UserGroupsClient) -> None:
    resp = user_groups_client.get("/", params={"search": "<b>x</b>"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp)["code"] == "HTTP_BAD_REQUEST"
    assert_spec_forbids_request(resp, GROUPS_ROUTE)


def test_non_admin_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp)["message"] == ADMIN_REQUIRED


@pytest.mark.parametrize(
    "headers",
    [pytest.param({}, id="no-token"), pytest.param(INVALID_BEARER, id="malformed-bearer")],
)
def test_without_valid_token_is_unauthorized(user_groups_client: UserGroupsClient, headers: dict[str, str]) -> None:
    resp = user_groups_client.get("/", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)


def test_oauth_token_without_usergroup_read_is_forbidden(no_group_scope: ScopedCaller) -> None:
    resp = no_group_scope("GET", "")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, GROUPS_ROUTE)
    assert error_of(resp)["message"] == "Insufficient scope. Required: usergroup:read"
