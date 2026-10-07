"""Strict OpenAPI audit of PATCH /api/v1/search/:searchId/unshare."""

from __future__ import annotations

from typing import Any

import pytest
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from search_audit_support import (
    MALFORMED_SEARCH_ID,
    MISSING_SEARCH_ID,
    UNKNOWN_USER_ID,
    UNSHARE_TEMPLATE,
    SearchAuditClient,
    SeedSearch,
    error_of,
    request_as,
    validation_fields,
)
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = UNSHARE_TEMPLATE


def _shared(search_audit_client: SearchAuditClient, seed_search: SeedSearch, *user_ids: str) -> str:
    search_id = seed_search()
    resp = search_audit_client.patch(f"/{search_id}/share", json={"userIds": list(user_ids)})
    assert resp.status_code == 200, resp.text[:500]
    return search_id


def _row(search_audit_client: SearchAuditClient, search_id: str) -> dict[str, Any]:
    [row] = search_audit_client.get(f"/{search_id}").json()
    return row


def test_removing_the_last_user_stops_sharing_but_answers_with_the_old_state(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = _shared(search_audit_client, seed_search, second_user.user_id)

    resp = search_audit_client.patch(f"/{search_id}/unshare", json={"userIds": [second_user.user_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["id"], body["isShared"], body["unsharedUsers"]) == (search_id, True, [second_user.user_id])
    assert [s["userId"] for s in body["sharedWith"]] == [second_user.user_id]
    row = _row(search_audit_client, search_id)
    assert (row["isShared"], row["sharedWith"]) == (False, [])
    assert row["shareLink"] == body["shareLink"]
    assert request_as(second_user, "GET", f"/{search_id}").json() == []


def test_access_level_is_accepted_and_ignored(
    search_audit_client: SearchAuditClient,
    pipeshub_client: PipeshubClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
) -> None:
    search_id = _shared(search_audit_client, seed_search, second_user.user_id, pipeshub_client.acting_user_id)

    resp = search_audit_client.patch(
        f"/{search_id}/unshare", json={"userIds": [second_user.user_id], "accessLevel": "write"}
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    row = _row(search_audit_client, search_id)
    assert row["isShared"] is True
    assert [(s["userId"], s["accessLevel"]) for s in row["sharedWith"]] == [(pipeshub_client.acting_user_id, "read")]


def test_a_user_it_was_never_shared_with_is_echoed_and_ignored(
    search_audit_client: SearchAuditClient,
    pipeshub_client: PipeshubClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
) -> None:
    search_id = seed_search()

    resp = search_audit_client.patch(f"/{search_id}/unshare", json={"userIds": [second_user.user_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["isShared"], body["sharedWith"], body["unsharedUsers"]) == (False, [], [second_user.user_id])
    assert "shareLink" not in body


def test_a_recipient_can_unshare_themselves(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = _shared(search_audit_client, seed_search, second_user.user_id)

    resp = request_as(second_user, "PATCH", f"/{search_id}/unshare", json={"userIds": [second_user.user_id]})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _row(search_audit_client, search_id)["sharedWith"] == []


def test_unknown_user_fails_the_whole_request(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser
) -> None:
    search_id = _shared(search_audit_client, seed_search, second_user.user_id)

    resp = search_audit_client.patch(
        f"/{search_id}/unshare", json={"userIds": [second_user.user_id, UNKNOWN_USER_ID]}
    )

    error = error_of(resp, 400)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", f"User not found: {UNKNOWN_USER_ID}")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [s["userId"] for s in _row(search_audit_client, search_id)["sharedWith"]] == [second_user.user_id]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.userIds", id="user-ids-missing"),
        pytest.param({"userIds": []}, "body.userIds", id="user-ids-empty"),
        pytest.param({"userIds": ["x"]}, "body.userIds.0", id="user-id-malformed"),
        pytest.param({"userIds": [UNKNOWN_USER_ID], "accessLevel": "admin"}, "body.accessLevel", id="access-level"),
    ],
)
def test_invalid_body_is_rejected(
    search_audit_client: SearchAuditClient, body: dict[str, Any], field: str
) -> None:
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/unshare", json=body)

    assert validation_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_malformed_search_id_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(f"/{MALFORMED_SEARCH_ID}/unshare", json={"userIds": [UNKNOWN_USER_ID]})

    assert validation_fields(resp) == {"params.searchId"}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("case", ["missing", "not-shared", "archived"])
def test_a_search_the_caller_cannot_see_is_not_found(
    search_audit_client: SearchAuditClient, seed_search: SeedSearch, second_user: SecondUser, case: str
) -> None:
    body = {"userIds": [second_user.user_id]}
    if case == "missing":
        resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/unshare", json=body)
    elif case == "not-shared":
        resp = request_as(second_user, "PATCH", f"/{seed_search()}/unshare", json=body)
    else:
        search_id = _shared(search_audit_client, seed_search, second_user.user_id)
        assert search_audit_client.patch(f"/{search_id}/archive").status_code == 200
        resp = search_audit_client.patch(f"/{search_id}/unshare", json=body)

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Search Id not found or unauthorized")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unshare_without_token_is_unauthorized(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(
        f"/{MISSING_SEARCH_ID}/unshare", auth=False, json={"userIds": [UNKNOWN_USER_ID]}
    )

    assert error_of(resp, 401)["code"] == "HTTP_UNAUTHORIZED"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"search": "no-search-has-this-title"}, id="search-is-ignored"),
        pytest.param({"shared": "1", "startDate": "2020-01-01", "endDate": "2999-12-31"}, id="matching-filters"),
    ],
)
def test_unshare_with_query_filters_that_match(
    search_audit_client: SearchAuditClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
    params: dict[str, str],
) -> None:
    search_id = _shared(search_audit_client, seed_search, second_user.user_id)

    resp = search_audit_client.patch(
        f"/{search_id}/unshare", params=params, json={"userIds": [second_user.user_id]}
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _row(search_audit_client, search_id)["sharedWith"] == []


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"shared": "0"}, id="shared"),
        pytest.param({"endDate": "2000-01-01"}, id="end-date"),
    ],
)
def test_a_query_filter_that_does_not_match_is_not_found(
    search_audit_client: SearchAuditClient,
    seed_search: SeedSearch,
    second_user: SecondUser,
    params: dict[str, str],
) -> None:
    search_id = _shared(search_audit_client, seed_search, second_user.user_id)

    resp = search_audit_client.patch(
        f"/{search_id}/unshare", params=params, json={"userIds": [second_user.user_id]}
    )

    error = error_of(resp, 404)
    assert (error["code"], error["message"]) == ("HTTP_NOT_FOUND", "Search Id not found or unauthorized")
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [s["userId"] for s in _row(search_audit_client, search_id)["sharedWith"]] == [second_user.user_id]


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
    resp = search_audit_client.patch(f"/{MISSING_SEARCH_ID}/unshare", params=params, json={"userIds": [UNKNOWN_USER_ID]})

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_markup_in_search_filter_is_rejected(search_audit_client: SearchAuditClient) -> None:
    resp = search_audit_client.patch(
        f"/{MISSING_SEARCH_ID}/unshare", params={"search": "<script>x</script>"}, json={"userIds": [UNKNOWN_USER_ID]}
    )

    assert error_of(resp, 400)["code"] == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
