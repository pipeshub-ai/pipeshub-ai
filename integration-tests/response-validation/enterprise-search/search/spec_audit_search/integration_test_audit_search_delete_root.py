"""Strict OpenAPI audit of DELETE /api/v1/search (clear search history).

The success path runs as the session's own non-admin user so it never wipes the
shared admin's history. That user has no searches of its own (it cannot see any
indexed document), so what it clears is the admin's searches shared with it.
"""

from __future__ import annotations

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
)

pytestmark = pytest.mark.spec_audit

ROUTE = ROOT_TEMPLATE
CLEARED = {"message": "Search history deleted successfully"}
NOT_FOUND = "Search history not found"


def _shared_with(search_audit_client: SearchAuditClient, seed_search: SeedSearch, user: SecondUser) -> str:
    search_id = seed_search()
    resp = search_audit_client.patch(f"/{search_id}/share", json={"userIds": [user.user_id]})
    assert resp.status_code == 200, resp.text[:500]
    return search_id


@pytest.fixture
def cleared_history(second_user: SecondUser) -> None:
    """Start from a history the member cannot clear any further."""
    resp = request_as(second_user, "DELETE")
    assert resp.status_code in (200, 404), resp.text[:500]


@pytest.mark.usefixtures("cleared_history")
def test_a_recipient_clearing_history_deletes_the_owner_s_shared_searches(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    shared_id = _shared_with(search_audit_client, seed_search, second_user)
    private_id = seed_search()

    resp = request_as(second_user, "DELETE")

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == CLEARED
    assert search_audit_client.get(f"/{shared_id}").json() == []
    assert len(search_audit_client.get(f"/{private_id}").json()) == 1

    again = request_as(second_user, "DELETE")
    assert error_of(again, 404)["message"] == NOT_FOUND
    assert_strict_openapi_exchange(again, ROUTE)


@pytest.mark.usefixtures("cleared_history")
@pytest.mark.parametrize(
    ("params", "deleted"),
    [
        pytest.param({"shared": "1", "startDate": "2020-01-01", "endDate": "2999-12-31T00:00:00Z"}, True, id="matching"),
        pytest.param({"search": "no-search-has-this-title"}, True, id="search-is-ignored"),
        pytest.param({"shared": "false"}, False, id="shared-false"),
        pytest.param({"startDate": "2999-01-01"}, False, id="future-start"),
    ],
)
def test_filters_choose_what_is_cleared(
    search_audit_client: SearchAuditClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
    params: dict[str, str],
    deleted: bool,
) -> None:
    search_id = _shared_with(search_audit_client, seed_search, second_user)

    resp = request_as(second_user, "DELETE", params=params)

    if deleted:
        assert resp.status_code == 200, resp.text[:500]
        assert search_audit_client.get(f"/{search_id}").json() == []
    else:
        assert error_of(resp, 404)["message"] == NOT_FOUND
        assert len(search_audit_client.get(f"/{search_id}").json()) == 1
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        pytest.param({"startDate": "not-a-date"}, "Invalid start date format", id="start-date"),
        pytest.param({"endDate": "not-a-date"}, "Invalid end date format", id="end-date"),
        pytest.param({"shared": "maybe"}, "shared parameter must be a valid boolean value (true/false)", id="shared"),
        pytest.param({"search": "a" * 1001}, "Search parameter too long (max 1000 characters)", id="search-too-long"),
    ],
)
def test_invalid_filter_is_rejected(second_user: SecondUser, params: dict[str, Any], message: str) -> None:
    resp = request_as(second_user, "DELETE", params=params)

    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_markup_in_search_is_rejected(second_user: SecondUser) -> None:
    resp = request_as(second_user, "DELETE", params={"search": "<script>x</script>"})

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_clear_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.delete("/", auth=False)

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)
