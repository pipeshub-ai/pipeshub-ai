"""Strict OpenAPI audit of PATCH /api/v1/search/:searchId/share."""

from __future__ import annotations

from typing import Any

import pytest
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from search_audit_support import (
    MALFORMED_SEARCH_ID,
    MISSING_SEARCH_ID,
    SHARE_TEMPLATE,
    UNKNOWN_USER_ID,
    SearchAuditClient,
    SeedSearch,
    error_of,
    request_as,
    validation_fields,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = SHARE_TEMPLATE


def _shared_with(search_audit_client: SearchAuditClient, search_id: str) -> list[tuple[str, str]]:
    [row] = search_audit_client.get(f"/{search_id}").json()
    return [(s["userId"], s["accessLevel"]) for s in row["sharedWith"]]


def test_first_share_answers_with_the_document_before_the_change(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(f"/{search_id}/share", json={"userIds": [second_user.user_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["_id"], body["isShared"], body["sharedWith"]) == (search_id, False, [])
    assert "shareLink" not in body
    assert _shared_with(search_audit_client, search_id) == [(second_user.user_id, "read")]
    assert [row["_id"] for row in request_as(second_user, "GET", f"/{search_id}").json()] == [search_id]


def test_sharing_again_updates_the_access_level(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()
    first = search_audit_client.patch(f"/{search_id}/share", json={"userIds": [second_user.user_id]})
    assert first.status_code == 200, first.text[:500]

    resp = search_audit_client.patch(
        f"/{search_id}/share", json={"userIds": [second_user.user_id], "accessLevel": "write"}
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["isShared"] is True and body["shareLink"].endswith(f"/api/v1/search/{search_id}")
    assert [(s["userId"], s["accessLevel"]) for s in body["sharedWith"]] == [(second_user.user_id, "read")]
    assert _shared_with(search_audit_client, search_id) == [(second_user.user_id, "write")]


def test_a_read_only_recipient_can_share_further(
    search_audit_client: SearchAuditClient,
    pipeshub_client: PipeshubClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
) -> None:
    search_id = seed_search()
    first = search_audit_client.patch(f"/{search_id}/share", json={"userIds": [second_user.user_id]})
    assert first.status_code == 200, first.text[:500]

    resp = request_as(
        second_user, "PATCH", f"/{search_id}/share", json={"userIds": [pipeshub_client.acting_user_id]}
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert (pipeshub_client.acting_user_id, "read") in _shared_with(search_audit_client, search_id)


def test_unknown_fields_are_dropped(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()

    with outside_request_contract("unknown body fields are stripped by the validator, not refused"):
        resp = search_audit_client.patch(
            f"/{search_id}/share", json={"userIds": [second_user.user_id], "isShared": False}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    [row] = search_audit_client.get(f"/{search_id}").json()
    assert row["isShared"] is True


def test_unknown_user_fails_the_whole_request(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(
        f"/{search_id}/share", json={"userIds": [second_user.user_id, UNKNOWN_USER_ID]}
    )

    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", f"User not found: {UNKNOWN_USER_ID}")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _shared_with(search_audit_client, search_id) == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.userIds", id="user-ids-missing"),
        pytest.param({"userIds": []}, "body.userIds", id="user-ids-empty"),
        pytest.param({"userIds": "x"}, "body.userIds", id="user-ids-not-a-list"),
        pytest.param({"userIds": ["x"]}, "body.userIds.0", id="user-id-malformed"),
        pytest.param({"userIds": [UNKNOWN_USER_ID], "accessLevel": "admin"}, "body.accessLevel", id="access-level"),
    ],
)
def test_invalid_body_is_rejected(
    search_audit_client: SearchAuditClient, body: dict[str, Any], field: str
) -> None:
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/share", json=body)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_search_id_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(f"/{MALFORMED_SEARCH_ID}/share", json={"userIds": [UNKNOWN_USER_ID]})

    assert validation_fields(resp) == {"params.searchId"}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("case", ["missing", "not-shared", "archived"])
def test_a_search_the_caller_cannot_see_is_not_found(
    search_audit_client: SearchAuditClient,
    pipeshub_client: PipeshubClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
    case: str,
) -> None:
    body = {"userIds": [second_user.user_id]}
    if case == "missing":
        resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/share", json=body)
    elif case == "not-shared":
        resp = request_as(
            second_user, "PATCH", f"/{seed_search()}/share", json={"userIds": [pipeshub_client.acting_user_id]}
        )
    else:
        search_id = seed_search()
        assert search_audit_client.patch(f"/{search_id}/archive").status_code == 200
        resp = search_audit_client.patch(f"/{search_id}/share", json=body)

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Search Id not found")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_share_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/share", auth=False, json={"userIds": [UNKNOWN_USER_ID]})

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"search": "no-search-has-this-title"}, id="search-is-ignored"),
        pytest.param({"shared": "false", "startDate": "2020-01-01", "endDate": "2999-12-31"}, id="matching-filters"),
    ],
)
def test_share_with_query_filters_that_match(
    search_audit_client: SearchAuditClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
    params: dict[str, str],
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(f"/{search_id}/share", params=params, json={"userIds": [second_user.user_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _shared_with(search_audit_client, search_id) == [(second_user.user_id, "read")]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"shared": "true"}, id="shared"),
        pytest.param({"endDate": "2000-01-01"}, id="end-date"),
    ],
)
def test_a_query_filter_that_does_not_match_is_not_found(
    search_audit_client: SearchAuditClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
    params: dict[str, str],
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(f"/{search_id}/share", params=params, json={"userIds": [second_user.user_id]})

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Search Id not found")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _shared_with(search_audit_client, search_id) == []


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
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/share", params=params, json={"userIds": [UNKNOWN_USER_ID]})

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_markup_in_search_filter_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(
        f"/{MISSING_SEARCH_ID}/share", params={"search": "<script>x</script>"}, json={"userIds": [UNKNOWN_USER_ID]}
    )

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
