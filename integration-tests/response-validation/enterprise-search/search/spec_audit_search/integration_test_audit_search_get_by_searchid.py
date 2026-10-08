"""Strict OpenAPI audit of GET /api/v1/search/:searchId."""

from __future__ import annotations

from typing import Any

import pytest
from helper.second_user import SecondUser
from search_audit_support import (
    MALFORMED_SEARCH_ID,
    MISSING_SEARCH_ID,
    SEARCH_QUERY,
    SEARCH_TEMPLATE,
    SearchAuditClient,
    SeedSearch,
    error_of,
    request_as,
    validation_fields,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
)

pytestmark = pytest.mark.spec_audit

ROUTE = SEARCH_TEMPLATE


def test_owner_gets_the_search_with_its_citations(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch
) -> None:
    search_id = seed_search()

    resp = search_audit_client.get(f"/{search_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    [row] = resp.json()
    assert (row["_id"], row["query"], row["limit"]) == (search_id, SEARCH_QUERY, 1)
    assert row["citationIds"] and all(isinstance(c, dict) and c["content"] for c in row["citationIds"])


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"shared": "false"}, id="shared-false"),
        pytest.param({"startDate": "2020-01-01", "endDate": "2999-12-31T23:59:59Z"}, id="date-range"),
        pytest.param({"search": "no-search-has-this-title"}, id="search-is-ignored"),
    ],
)
def test_filters_that_match_still_return_the_search(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, params: dict[str, str]
) -> None:
    search_id = seed_search()

    resp = search_audit_client.get(f"/{search_id}", params=params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [row["_id"] for row in resp.json()] == [search_id]


def test_a_filter_that_does_not_match_gives_an_empty_list(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch
) -> None:
    search_id = seed_search()

    resp = search_audit_client.get(f"/{search_id}", params={"endDate": "2020-01-01"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == []


def test_recipient_sees_a_shared_search(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()
    assert request_as(second_user, "GET", f"/{search_id}").json() == []
    shared = search_audit_client.patch(f"/{search_id}/share", json={"userIds": [second_user.user_id]})
    assert shared.status_code == 200, shared.text[:500]

    resp = request_as(second_user, "GET", f"/{search_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    [row] = resp.json()
    assert row["isShared"] is True
    assert [(s["userId"], s["accessLevel"]) for s in row["sharedWith"]] == [(second_user.user_id, "read")]


def _unknown(search_audit_client: SearchAuditClient, seed_search: SeedSearch, case: str, second_user: SecondUser) -> Any:
    if case == "missing":
        return search_audit_client.get(f"/{MISSING_SEARCH_ID}")
    search_id = seed_search()
    if case == "not-shared":
        return request_as(second_user, "GET", f"/{search_id}")
    archived = search_audit_client.patch(f"/{search_id}/archive")
    assert archived.status_code == 200, archived.text[:500]
    return search_audit_client.get(f"/{search_id}")


@pytest.mark.parametrize("case", ["missing", "not-shared", "archived"])
def test_a_search_the_caller_cannot_see_is_an_empty_list_not_404(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser, case: str
) -> None:
    resp = _unknown(search_audit_client, seed_search, case, second_user)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == []


def test_malformed_search_id_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.get(f"/{MALFORMED_SEARCH_ID}")

    assert validation_fields(resp) == {"params.searchId"}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"startDate": "not-a-date"}, id="start-date"),
        pytest.param({"shared": "maybe"}, id="shared"),
        pytest.param({"search": "a" * 1001}, id="search-too-long"),
    ],
)
def test_invalid_filter_is_rejected(search_audit_client: SearchAuditClient, params: dict[str, str]) -> None:
    resp = search_audit_client.get(f"/{MISSING_SEARCH_ID}", params=params)

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_get_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.get(f"/{MISSING_SEARCH_ID}", auth=False)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)
