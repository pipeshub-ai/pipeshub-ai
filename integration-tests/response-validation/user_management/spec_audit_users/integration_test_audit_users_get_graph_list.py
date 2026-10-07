"""Strict OpenAPI audit of GET /api/v1/users/graph/list.

Node forwards page, limit and search to the connector service's
/api/v1/entity/user/list and enriches each user from MongoDB.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.clients.users_client import UsersClient
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)
from users_audit_support import INVALID_BEARER, TINY_PNG, display_picture_request

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/users/graph/list"
PATH = "/graph/list"


def _list(users_client: UsersClient, **params: Any):
    return users_client.get(PATH, params=params)


def test_the_first_page_uses_a_limit_of_100(users_client: UsersClient) -> None:
    resp = _list(users_client)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["status"], body["message"]) == ("success", "Users fetched successfully"), body
    pagination = body["pagination"]
    assert (pagination["page"], pagination["limit"], pagination["hasPrev"]) == (1, 100, False), pagination
    assert len(body["users"]) == min(100, pagination["total"])


def test_a_member_is_found_and_enriched(users_client: UsersClient, second_user: SecondUser) -> None:
    resp = _list(users_client, search=second_user.email)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (user,) = [u for u in resp.json()["users"] if u["userId"] == second_user.user_id]
    assert (user["email"], user["hasLoggedIn"], user["role"]) == (second_user.email, True, "Member"), user
    assert "everyone" in {g["type"] for g in user["userGroups"]}, user
    assert user["groupCount"] == len([g for g in user["userGroups"] if g["type"] != "everyone"])
    assert user["_id"] == f"users/{user['_key']}" and user["id"] == user["_key"], user


def test_the_admin_is_shown_as_admin(
    users_client: UsersClient, pipeshub_client: PipeshubClient
) -> None:
    me = users_client.get(f"/{pipeshub_client.acting_user_id}").json()
    resp = _list(users_client, search=me["email"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    roles = {u["role"] for u in resp.json()["users"] if u["userId"] == pipeshub_client.acting_user_id}
    assert roles == {"Admin"}, resp.json()["users"]


def test_a_display_picture_is_inlined(users_client: UsersClient, no_picture: SecondUser) -> None:
    assert display_picture_request(
        no_picture, "PUT", files={"file": ("dp.png", TINY_PNG, "image/png")}
    ).status_code == 201
    resp = _list(users_client, search=no_picture.email)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    (user,) = [u for u in resp.json()["users"] if u["userId"] == no_picture.user_id]
    assert user["profilePicture"].startswith("data:image/jpeg;base64,"), user["profilePicture"][:60]


def test_a_middle_page_has_both_neighbours(users_client: UsersClient) -> None:
    resp = _list(users_client, page="2", limit="1")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert pagination["total"] >= 3, "the org should have at least three users"
    assert (pagination["page"], pagination["limit"], pagination["hasPrev"], pagination["hasNext"]) == (2, 1, True, True)
    assert len(resp.json()["users"]) == 1


@pytest.mark.parametrize(
    "params", [{"page": "100000"}, {"search": "spec-audit-no-such-user-zzz"}], ids=["past-the-end", "no-match"]
)
def test_an_empty_page_has_a_short_pagination(users_client: UsersClient, params: dict[str, str]) -> None:
    resp = _list(users_client, **params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert (body["message"], body["users"], body["pagination"]["pages"]) == ("No users found", [], 0), body
    assert set(body["pagination"]) == {"page", "limit", "total", "pages"}, body


def test_other_query_parameters_are_ignored(users_client: UsersClient) -> None:
    with outside_request_contract("an unlisted query parameter, which is not forwarded"):
        resp = _list(users_client, limit="1", orgId="0123456789abcdef01234567")
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.status_code == 200, resp.text[:500]
    assert len(resp.json()["users"]) == 1


@pytest.mark.parametrize("params", [{"page": "0"}, {"limit": "0"}], ids=["page-0", "limit-0"])
def test_zero_passes_validation_and_the_connector_refusal_is_a_bad_request(
    users_client: UsersClient, params: dict[str, str]
) -> None:
    resp = _list(users_client, **params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert "Failed to get users" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [{"page": "abc"}, {"page": "-1"}, {"page": "1.5"}, {"limit": "101"}, {"limit": "ten"}],
    ids=["page-word", "page-negative", "page-fraction", "limit-over-100", "limit-word"],
)
def test_a_bad_page_or_limit_is_a_validation_error(
    users_client: UsersClient, params: dict[str, str]
) -> None:
    resp = _list(users_client, **params)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_search_over_1000_characters_is_a_bad_request(users_client: UsersClient) -> None:
    resp = _list(users_client, search="a" * 1001)
    assert resp.status_code == 400, resp.text[:500]
    assert "Search parameter too long" in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("search", "message"),
    [("<b>spec</b>", "HTML tags"), ("spec %s audit", "format specifiers")],
    ids=["html", "format-specifier"],
)
def test_a_dangerous_search_is_a_bad_request(
    users_client: UsersClient, search: str, message: str
) -> None:
    resp = _list(users_client, search=search)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert message in resp.text, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("headers", "message"),
    [({}, "No token provided"), (INVALID_BEARER, "Invalid token")],
    ids=["no-token", "not-a-jwt"],
)
def test_rejected_credentials_are_unauthorized(
    users_client: UsersClient, headers: dict[str, str], message: str
) -> None:
    resp = users_client.get(PATH, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert message in resp.text, resp.text[:500]
