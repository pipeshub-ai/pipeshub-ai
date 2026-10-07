"""Strict OpenAPI audit of PATCH /api/v1/search/:searchId/archive."""

from __future__ import annotations

import pytest
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from search_audit_support import (
    ARCHIVE_TEMPLATE,
    MALFORMED_SEARCH_ID,
    MISSING_SEARCH_ID,
    SearchAuditClient,
    SeedSearch,
    error_of,
    request_as,
    validation_fields,
)
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = ARCHIVE_TEMPLATE
NOT_FOUND = "Search Id not found or no archive permission"


def test_owner_archives_and_the_search_leaves_the_history(
    search_audit_client: SearchAuditClient, pipeshub_client: PipeshubClient, seed_search: SeedSearch
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(f"/{search_id}/archive")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["id"], body["status"], body["archivedBy"]) == (search_id, "archived", pipeshub_client.acting_user_id)
    history = search_audit_client.get("/", params={"limit": 100}).json()["searchHistory"]
    assert search_id not in {row["_id"] for row in history}


def test_archiving_twice_is_not_found_not_a_bad_request(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch
) -> None:
    search_id = seed_search()
    assert search_audit_client.patch(f"/{search_id}/archive").status_code == 200

    resp = search_audit_client.patch(f"/{search_id}/archive")

    assert error_of(resp, 404)["message"] == NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_read_only_recipient_can_archive_the_owner_s_search(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()
    shared = search_audit_client.patch(f"/{search_id}/share", json={"userIds": [second_user.user_id]})
    assert shared.status_code == 200, shared.text[:500]

    resp = request_as(second_user, "PATCH", f"/{search_id}/archive")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["archivedBy"] == second_user.user_id
    assert search_audit_client.get(f"/{search_id}").json() == []


@pytest.mark.parametrize("case", ["missing", "not-shared"])
def test_a_search_the_caller_cannot_see_is_not_found(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser, case: str
) -> None:
    if case == "missing":
        resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/archive")
    else:
        resp = request_as(second_user, "PATCH", f"/{seed_search()}/archive")

    assert error_of(resp, 404)["message"] == NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_search_id_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(f"/{MALFORMED_SEARCH_ID}/archive")

    assert validation_fields(resp) == {"params.searchId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_archive_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/archive", auth=False)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"search": "no-search-has-this-title"}, id="search-is-ignored"),
        pytest.param({"shared": "false", "startDate": "2020-01-01", "endDate": "2999-12-31"}, id="matching-filters"),
    ],
)
def test_archive_with_query_filters_that_match(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, params: dict[str, str]
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(f"/{search_id}/archive", params=params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert search_audit_client.get(f"/{search_id}").json() == []


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"shared": "true"}, id="shared"),
        pytest.param({"endDate": "2000-01-01"}, id="end-date"),
        pytest.param({"startDate": "2999-01-01T00:00:00Z"}, id="start-date"),
    ],
)
def test_a_query_filter_that_does_not_match_is_not_found(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, params: dict[str, str]
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(f"/{search_id}/archive", params=params)

    assert error_of(resp, 404)["message"] == NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [row["_id"] for row in search_audit_client.get(f"/{search_id}").json()] == [search_id]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"startDate": "not-a-date"}, id="start-date"),
        pytest.param({"endDate": "not-a-date"}, id="end-date"),
        pytest.param({"shared": "maybe"}, id="shared"),
        pytest.param({"search": "a" * 1001}, id="search-too-long"),
    ],
)
def test_invalid_query_filter_is_rejected(search_audit_client: SearchAuditClient, params: dict[str, str]) -> None:
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/archive", params=params)

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_markup_in_search_filter_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(
        f"/{MISSING_SEARCH_ID}/archive", params={"search": "<script>x</script>"}
    )

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
