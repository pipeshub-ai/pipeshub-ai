"""Strict OpenAPI audit of GET /api/v1/search (search history)."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest
from helper.second_user import SecondUser
from search_audit_support import (
    ROOT_TEMPLATE,
    SearchAuditClient,
    SeedSearch,
    error_of,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = ROOT_TEMPLATE


def _ids(resp: Any) -> list[str]:
    return [row["_id"] for row in resp.json()["searchHistory"]]


def test_history_lists_the_caller_s_searches(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch
) -> None:
    search_id = seed_search()

    resp = search_audit_client.get("/", params={"limit": 100})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert search_id in _ids(resp)
    body = resp.json()
    assert body["pagination"]["limit"] == 100
    assert body["filters"]["applied"]["values"] == {"page": 1, "limit": 100}


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": 1, "limit": 1}, id="page-and-limit"),
        pytest.param({"sortBy": "createdAt", "sortOrder": "asc"}, id="sort-created-asc"),
        pytest.param({"sortBy": "title", "sortOrder": "desc"}, id="sort-title-desc"),
        pytest.param({"sortBy": "lastActivityAt"}, id="sort-last-activity"),
        pytest.param({"shared": "true"}, id="shared-true"),
        pytest.param({"shared": "0"}, id="shared-0"),
        pytest.param({"startDate": "2020-01-01T00:00:00Z", "endDate": "2999-01-01T00:00:00.000Z"}, id="date-range"),
        pytest.param({"startDate": "2020-01-01"}, id="date-only"),
        pytest.param({"page": 1001}, id="page-over-1000"),
        pytest.param({"page": ""}, id="page-empty"),
    ],
)
def test_history_accepts_documented_parameters(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, params: dict[str, Any]
) -> None:
    seed_search()

    resp = search_audit_client.get("/", params=params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_shared_filter_narrows_the_list(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    shared_id = seed_search()
    private_id = seed_search()
    shared = search_audit_client.patch(f"/{shared_id}/share", json={"userIds": [second_user.user_id]})
    assert shared.status_code == 200, shared.text[:500]

    only_shared = search_audit_client.get("/", params={"shared": "true", "limit": 100})
    not_shared = search_audit_client.get("/", params={"shared": "false", "limit": 100})

    for resp in (only_shared, not_shared):
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert shared_id in _ids(only_shared) and private_id not in _ids(only_shared)
    assert private_id in _ids(not_shared) and shared_id not in _ids(not_shared)


def test_search_parameter_is_validated_but_does_not_filter(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch
) -> None:
    search_id = seed_search()

    resp = search_audit_client.get("/", params={"search": "no-search-has-this-title", "limit": 100})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert search_id in _ids(resp)


def test_total_count_counts_every_stored_search(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    seed_search()

    resp = request_as(second_user, "GET", params={"limit": 1})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["searchHistory"] == []
    assert body["pagination"]["totalCount"] >= 1
    assert body["pagination"]["hasNextPage"] is True


@pytest.mark.parametrize(
    ("params", "echoed"),
    [
        pytest.param({"page": "2.5"}, {"page": 2, "limit": 20}, id="fractional-page"),
        pytest.param({"limit": "3abc"}, {"page": 1, "limit": 3}, id="limit-with-trailing-text"),
        pytest.param({"sortBy": "bogus"}, {"sortBy": "bogus", "page": 1, "limit": 20}, id="unknown-sort-field"),
        pytest.param({"sortOrder": "sideways"}, {"sortOrder": "sideways", "page": 1, "limit": 20}, id="unknown-sort-order"),
        pytest.param({"shared": "TRUE"}, {"shared": "TRUE", "page": 1, "limit": 20}, id="shared-upper-case"),
        pytest.param({"foo": "bar"}, {"page": 1, "limit": 20}, id="unknown-parameter"),
    ],
)
def test_history_tolerates_values_outside_the_contract(
    search_audit_client: SearchAuditClient, params: dict[str, str], echoed: dict[str, Any]
) -> None:
    with outside_request_contract("the handler reads these leniently instead of refusing them"):
        resp = search_audit_client.get("/", params=params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["filters"]["applied"]["values"] == echoed


@pytest.mark.parametrize(
    ("sort_order", "documented"),
    [
        pytest.param("asc", True, id="asc"),
        pytest.param("desc", True, id="desc"),
        pytest.param("sideways", False, id="unknown"),
        pytest.param("ASC", False, id="upper-case-asc"),
    ],
)
def test_any_sort_order_but_asc_sorts_descending(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, sort_order: str, documented: bool
) -> None:
    first, second = seed_search(), seed_search()
    [first_row] = search_audit_client.get(f"/{first}").json()
    params = {"sortBy": "createdAt", "sortOrder": sort_order, "startDate": first_row["createdAt"], "limit": 100}

    with outside_request_contract("sortOrder is not checked against its enum") if not documented else nullcontext():
        resp = search_audit_client.get("/", params=params)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    mine = [i for i in _ids(resp) if i in (first, second)]
    assert mine == ([first, second] if sort_order == "asc" else [second, first])
    assert resp.json()["filters"]["applied"]["values"]["sortOrder"] == sort_order


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param({"page": 0}, "Number must be at least 1: 0", id="page-zero"),
        pytest.param({"page": "abc"}, "Invalid number: abc", id="page-not-a-number"),
        pytest.param({"limit": 0}, "Number must be at least 1: 0", id="limit-zero"),
        pytest.param({"limit": 101}, "Number must be at most 100: 101", id="limit-over-100"),
        pytest.param({"shared": "maybe"}, "shared parameter must be a valid boolean value (true/false)", id="shared"),
        pytest.param({"startDate": "not-a-date"}, "Invalid start date format", id="start-date"),
        pytest.param({"endDate": "not-a-date"}, "Invalid end date format", id="end-date"),
        pytest.param({"search": "a" * 1001}, "Search parameter too long (max 1000 characters)", id="search-too-long"),
    ],
)
def test_invalid_parameter_is_rejected(
    search_audit_client: SearchAuditClient, params: dict[str, Any], message: str
) -> None:
    resp = search_audit_client.get("/", params=params)

    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"search": "<script>alert(1)</script>"}, id="search"),
        pytest.param({"page": "<b>1</b>"}, id="page"),
    ],
)
def test_markup_in_a_parameter_is_rejected(
    search_audit_client: SearchAuditClient, params: dict[str, str]
) -> None:
    resp = search_audit_client.get("/", params=params)

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_history_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.get("/", auth=False)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)
