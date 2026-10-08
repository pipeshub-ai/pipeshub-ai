"""Strict OpenAPI audit of GET /api/v1/users."""

from __future__ import annotations

import pytest
from helper.clients.users_client import UsersClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from users_audit_support import (
    INVALID_BEARER,
    MAX_LIST_PAGE,
    MISSING_USER_ID,
    SeededUser,
    SeedUser,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users"


def _ids(resp) -> list[str]:
    return [u["id"] for u in resp.json()["users"]]


def test_default_page_is_page_one_of_twenty_five(users_client: UsersClient) -> None:
    resp = users_client.get("/")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (1, 25), pagination
    assert pagination["hasPrevPage"] is False, pagination


def test_search_finds_a_seeded_user_with_its_enrichment(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    resp = users_client.get("/", params={"search": user["email"], "page": "1", "limit": "100"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (found,) = resp.json()["users"]
    assert found["id"] == found["userId"] == user["_id"], found
    assert found["email"] == user["email"], found
    assert (found["hasLoggedIn"], found["isBlocked"], found["isActive"]) == (False, False, False)
    assert found["role"] == "Member", found
    assert [g["type"] for g in found["userGroups"]] == ["everyone"], found
    assert found["groupCount"] == 0, found


def test_last_page_the_service_accepts(users_client: UsersClient) -> None:
    resp = users_client.get("/", params={"page": str(MAX_LIST_PAGE), "limit": "1"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["users"] == []


def test_is_blocked_true_lists_the_locked_out_user(
    users_client: UsersClient, blocked_user: SeededUser
) -> None:
    resp = users_client.get(
        "/", params={"isBlocked": "true", "search": blocked_user["email"]}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (found,) = resp.json()["users"]
    assert found["isBlocked"] is True and found["isActive"] is False, found

    resp = users_client.get(
        "/", params={"isBlocked": "false", "search": blocked_user["email"]}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["users"] == []


def test_has_logged_in_false_keeps_a_user_who_never_signed_in(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    for flag, expected in (("false", [user["_id"]]), ("true", [])):
        resp = users_client.get("/", params={"hasLoggedIn": flag, "search": user["email"]})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert _ids(resp) == expected, (flag, resp.json())


def test_group_ids_filter_by_the_everyone_group(
    users_client: UsersClient, seed_user: SeedUser
) -> None:
    user = seed_user()
    listed = users_client.get("/", params={"search": user["email"]})
    assert listed.status_code == 200, listed.text[:500]
    (everyone,) = listed.json()["users"][0]["userGroups"]

    # Empty segments are dropped, so stray commas are accepted.
    for group_ids in (everyone["_id"], f",{everyone['_id']},,{MISSING_USER_ID},"):
        resp = users_client.get("/", params={"groupIds": group_ids, "search": user["email"]})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert _ids(resp) == [user["_id"]], (group_ids, resp.json())

    resp = users_client.get("/", params={"groupIds": MISSING_USER_ID, "search": user["email"]})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["users"] == []


@pytest.mark.parametrize("group_ids", ["", ",,"], ids=["empty", "only-commas"])
def test_group_ids_with_no_id_is_no_filter(users_client: UsersClient, group_ids: str) -> None:
    resp = users_client.get("/", params={"groupIds": group_ids, "limit": "1"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["totalCount"] >= 1


def test_include_service_accounts_is_ignored_for_a_member(
    second_user: SecondUser,
) -> None:
    for flag in ("true", "false"):
        resp = request_as(second_user, "GET", "/", params={"includeServiceAccounts": flag, "limit": "100"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert all(u.get("kind") != "service" for u in resp.json()["users"]), resp.json()


def test_include_service_accounts_for_an_admin(users_client: UsersClient) -> None:
    resp = users_client.get("/", params={"includeServiceAccounts": "true", "limit": "100"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_an_unknown_query_parameter_is_ignored(users_client: UsersClient) -> None:
    # `blocked` was once a separate mode; it is now passed through and ignored.
    with outside_request_contract("an undocumented query parameter, to show it is ignored"):
        resp = users_client.get("/", params={"blocked": "true", "limit": "1"})
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert set(resp.json()) == {"users", "pagination"}


@pytest.mark.parametrize(
    "params",
    [
        {"page": "-1"},
        {"page": "1.5"},
        {"page": "abc"},
        {"page": ""},
        {"limit": "-10"},
        {"limit": "101"},
        {"limit": "1.5"},
        {"limit": "ten"},
        {"hasLoggedIn": "yes"},
        {"hasLoggedIn": ""},
        {"isBlocked": "no"},
        {"includeServiceAccounts": "yes"},
        {"groupIds": "not-an-objectid"},
        {"groupIds": f"{MISSING_USER_ID},not-an-objectid"},
        {"groupIds": MISSING_USER_ID * 2},
    ],
    ids=lambda p: "-".join(f"{k}={v}" for k, v in p.items()),
)
def test_invalid_query_is_a_validation_error(
    users_client: UsersClient, params: dict[str, str]
) -> None:
    resp = users_client.get("/", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [{"page": "0"}, {"limit": "0"}, {"page": str(MAX_LIST_PAGE + 1)}],
    ids=["page-0", "limit-0", "page-past-max"],
)
def test_out_of_range_pagination_passes_validation_and_is_an_internal_error(
    users_client: UsersClient, params: dict[str, str]
) -> None:
    resp = users_client.get("/", params=params)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.get("/", auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
