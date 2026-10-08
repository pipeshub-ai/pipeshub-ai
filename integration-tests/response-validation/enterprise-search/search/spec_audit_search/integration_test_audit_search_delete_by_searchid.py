"""Strict OpenAPI audit of DELETE /api/v1/search/:searchId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from search_audit_support import (
    MALFORMED_SEARCH_ID,
    MISSING_SEARCH_ID,
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
DELETED = {"message": "Search deleted successfully"}


def test_owner_deletes_and_a_second_delete_is_not_found(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch
) -> None:
    search_id = seed_search()

    first = search_audit_client.delete(f"/{search_id}")
    assert first.status_code == 200, first.text[:500]
    assert_strict_openapi_exchange(first, ROUTE)
    assert first.json() == DELETED
    assert search_audit_client.get(f"/{search_id}").json() == []

    second = search_audit_client.delete(f"/{search_id}")
    assert error_of(second, 404)["message"] == "Search Id not found"
    assert_strict_openapi_exchange(second, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"search": "no-search-has-this-title"}, id="search-is-ignored"),
        pytest.param({"shared": "0", "startDate": "2020-01-01", "endDate": "2999-12-31"}, id="matching-filters"),
    ],
)
def test_delete_with_filters_that_match(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, params: dict[str, str]
) -> None:
    search_id = seed_search()

    resp = search_audit_client.delete(f"/{search_id}", params=params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert search_audit_client.get(f"/{search_id}").json() == []


def test_a_read_only_recipient_can_delete_the_owner_s_search(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()
    shared = search_audit_client.patch(
        f"/{search_id}/share", json={"userIds": [second_user.user_id], "accessLevel": "read"}
    )
    assert shared.status_code == 200, shared.text[:500]

    resp = request_as(second_user, "DELETE", f"/{search_id}")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert search_audit_client.get(f"/{search_id}").json() == []


@pytest.mark.parametrize("case", ["missing", "not-shared", "archived", "filter-does-not-match"])
def test_a_search_the_caller_cannot_see_is_not_found(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser, case: str
) -> None:
    if case == "missing":
        resp = search_audit_client.delete(f"/{MISSING_SEARCH_ID}")
    else:
        search_id = seed_search()
        if case == "not-shared":
            resp = request_as(second_user, "DELETE", f"/{search_id}")
        elif case == "archived":
            assert search_audit_client.patch(f"/{search_id}/archive").status_code == 200
            resp = search_audit_client.delete(f"/{search_id}")
        else:
            resp = search_audit_client.delete(f"/{search_id}", params={"shared": "true"})

    assert error_of(resp, 404)["code"] == "HTTP_NOT_FOUND"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_search_id_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.delete(f"/{MALFORMED_SEARCH_ID}")

    assert validation_fields(resp) == {"params.searchId"}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"endDate": "not-a-date"}, id="end-date"),
        pytest.param({"shared": "maybe"}, id="shared"),
        pytest.param({"search": "a" * 1001}, id="search-too-long"),
    ],
)
def test_invalid_filter_is_rejected(search_audit_client: SearchAuditClient, params: dict[str, str]) -> None:
    resp = search_audit_client.delete(f"/{MISSING_SEARCH_ID}", params=params)

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_markup_in_search_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.delete(f"/{MISSING_SEARCH_ID}", params={"search": "<script>x</script>"})

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.delete(f"/{MISSING_SEARCH_ID}", auth=False)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)
