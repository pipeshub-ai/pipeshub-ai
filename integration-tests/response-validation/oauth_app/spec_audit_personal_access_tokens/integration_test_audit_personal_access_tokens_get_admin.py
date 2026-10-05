"""Strict OpenAPI audit of GET /api/v1/personal-access-tokens/admin.

Chain: authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller
-> userAdminCheck -> zod query -> adminListTokens.
"""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from personal_access_tokens_audit_support import MintPat, PatsClient, request_as
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/personal-access-tokens/admin"
DEFAULT_LIMIT = 100
ADMIN_REQUIRED = "You need admin access to do this. Ask an admin in your organisation."


def test_admin_list_shows_every_owners_tokens_with_owner_fields(
    pats_client: PatsClient, mint_pat: MintPat, second_user: SecondUser
) -> None:
    own = mint_pat()
    members = mint_pat(as_user=second_user, expiryDays=90)

    resp = pats_client.admin_list()

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"data", "pagination"}, body.keys()
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == DEFAULT_LIMIT
    # Newest first, so tokens minted a moment ago are on the first page.
    rows = {row["id"]: row for row in body["data"]}
    assert own["id"] in rows, f"admin's own token missing from {len(rows)} rows"
    assert members["id"] in rows, f"member's token missing from {len(rows)} rows"
    member_row = rows[members["id"]]
    assert member_row["userId"] == second_user.user_id
    assert member_row["ownerEmail"] == second_user.email
    assert member_row["ownerDeleted"] is False
    assert member_row["name"] == members["name"]
    assert rows[own["id"]]["userId"] != second_user.user_id
    assert all("accessToken" not in row for row in body["data"])
    assert_strict_openapi_response(resp, ROUTE)


def test_admin_list_page_and_limit_are_applied(
    pats_client: PatsClient, mint_pat: MintPat
) -> None:
    mint_pat()
    mint_pat()

    resp = pats_client.admin_list(page=2, limit=1)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert len(body["data"]) == 1
    pagination = body["pagination"]
    assert pagination["page"] == 2
    assert pagination["limit"] == 1
    assert pagination["total"] >= 2
    assert pagination["totalPages"] == pagination["total"]
    assert_strict_openapi_response(resp, ROUTE)


def test_admin_list_without_token_is_unauthorized(pats_client: PatsClient) -> None:
    resp = pats_client.admin_list(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_admin_list_refuses_member_and_oauth_token(
    oauth_pats_client: PatsClient, second_user: SecondUser
) -> None:
    # limit=0 would fail validation, but userAdminCheck runs before the validator.
    as_member = request_as(second_user, "GET", "/admin", params={"limit": 0})

    assert as_member.status_code == 403, as_member.text[:500]
    assert ADMIN_REQUIRED in as_member.text
    assert_strict_openapi_response(as_member, ROUTE)

    # The admin himself, but behind an OAuth token: requireSessionAuth refuses it.
    as_oauth = oauth_pats_client.admin_list()

    assert as_oauth.status_code == 403, as_oauth.text[:500]
    assert "interactive user session" in as_oauth.text
    assert_strict_openapi_response(as_oauth, ROUTE)


def test_admin_list_out_of_range_query_is_rejected(pats_client: PatsClient) -> None:
    for query in ({"limit": 101}, {"limit": 0}, {"page": 0}, {"page": "abc"}):
        resp = pats_client.admin_list(**query)

        assert resp.status_code == 400, f"{query}: {resp.text[:500]}"
        assert_strict_openapi_response(resp, ROUTE)
