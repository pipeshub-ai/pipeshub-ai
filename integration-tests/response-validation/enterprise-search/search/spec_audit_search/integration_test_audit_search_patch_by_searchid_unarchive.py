"""Strict OpenAPI audit of PATCH /api/v1/search/:searchId/unarchive."""

from __future__ import annotations

import pytest
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from search_audit_support import (
    MALFORMED_SEARCH_ID,
    MISSING_SEARCH_ID,
    UNARCHIVE_TEMPLATE,
    SearchAuditClient,
    SeedSearch,
    error_of,
    request_as,
    validation_fields,
)
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = UNARCHIVE_TEMPLATE
NOT_FOUND = "Search Id not found or no unarchive permission"


def _archived(search_audit_client: SearchAuditClient, seed_search: SeedSearch) -> str:
    search_id = seed_search()
    resp = search_audit_client.patch(f"/{search_id}/archive")
    assert resp.status_code == 200, resp.text[:500]
    return search_id


def test_owner_unarchives_and_the_search_is_back(
    search_audit_client: SearchAuditClient, pipeshub_client: PipeshubClient, seed_search: SeedSearch
) -> None:
    search_id = _archived(search_audit_client, seed_search)

    resp = search_audit_client.patch(f"/{search_id}/unarchive")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["id"], body["status"], body["unarchivedBy"]) == (
        search_id,
        "unarchived",
        pipeshub_client.acting_user_id,
    )
    [row] = search_audit_client.get(f"/{search_id}").json()
    assert row["isArchived"] is False


def test_unarchiving_an_active_search_is_not_found_not_a_bad_request(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch
) -> None:
    resp = search_audit_client.patch(f"/{seed_search()}/unarchive")

    assert error_of(resp, 404)["message"] == NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_recipient_can_unarchive_a_shared_search(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()
    shared = search_audit_client.patch(f"/{search_id}/share", json={"userIds": [second_user.user_id]})
    assert shared.status_code == 200, shared.text[:500]
    assert search_audit_client.patch(f"/{search_id}/archive").status_code == 200

    resp = request_as(second_user, "PATCH", f"/{search_id}/unarchive")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["unarchivedBy"] == second_user.user_id


@pytest.mark.parametrize("case", ["missing", "not-shared"])
def test_a_search_the_caller_cannot_see_is_not_found(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser, case: str
) -> None:
    if case == "missing":
        resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/unarchive")
    else:
        resp = request_as(second_user, "PATCH", f"/{_archived(search_audit_client, seed_search)}/unarchive")

    assert error_of(resp, 404)["message"] == NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_search_id_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(f"/{MALFORMED_SEARCH_ID}/unarchive")

    assert validation_fields(resp) == {"params.searchId"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unarchive_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/unarchive", auth=False)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"search": "no-search-has-this-title"}, id="search-is-ignored"),
        pytest.param({"shared": "0", "startDate": "2020-01-01", "endDate": "2999-12-31"}, id="matching-filters"),
    ],
)
def test_unarchive_with_query_filters_that_match(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, params: dict[str, str]
) -> None:
    search_id = _archived(search_audit_client, seed_search)

    resp = search_audit_client.patch(f"/{search_id}/unarchive", params=params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [row["_id"] for row in search_audit_client.get(f"/{search_id}").json()] == [search_id]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"shared": "1"}, id="shared"),
        pytest.param({"endDate": "2000-01-01"}, id="end-date"),
    ],
)
def test_a_query_filter_that_does_not_match_is_not_found(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, params: dict[str, str]
) -> None:
    search_id = _archived(search_audit_client, seed_search)

    resp = search_audit_client.patch(f"/{search_id}/unarchive", params=params)

    assert error_of(resp, 404)["message"] == NOT_FOUND
    assert_strict_openapi_exchange(resp, ROUTE)
    assert search_audit_client.get(f"/{search_id}").json() == []


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
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/unarchive", params=params)

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_markup_in_search_filter_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(
        f"/{MISSING_SEARCH_ID}/unarchive", params={"search": "<script>x</script>"}
    )

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
