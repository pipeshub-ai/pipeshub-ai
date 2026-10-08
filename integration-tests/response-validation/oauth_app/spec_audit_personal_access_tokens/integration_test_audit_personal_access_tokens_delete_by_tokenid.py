"""Strict OpenAPI audit of DELETE /api/v1/personal-access-tokens/:tokenId."""

from __future__ import annotations

import pytest
from personal_access_tokens_audit_support import (
    MALFORMED_TOKEN_ID,
    MISSING_TOKEN_ID,
    MintPat,
    PatsClient,
    request_as,
    request_with_token,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/personal-access-tokens/:tokenId"
NOT_FOUND = "Personal access token not found"


def test_revoke_own_token_then_token_and_id_are_dead(
    pats_client: PatsClient, mint_pat: MintPat
) -> None:
    token = mint_pat()
    base_url = pats_client._client.base_url
    path = f"/{MISSING_TOKEN_ID}"

    # A live PAT authenticates but is not an interactive session.
    live = request_with_token(base_url, token["accessToken"], "DELETE", path)
    assert live.status_code == 403, live.text[:500]
    assert_strict_openapi_exchange(live, ROUTE)

    resp = pats_client.revoke(token["id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "Personal access token revoked successfully"}

    listed = pats_client.list()
    assert listed.status_code == 200, listed.text[:500]
    assert token["id"] not in [item["id"] for item in listed.json()["tokens"]]

    # Once revoked the PAT no longer authenticates at all: 401 instead of 403.
    dead = request_with_token(base_url, token["accessToken"], "DELETE", path)
    assert dead.status_code == 401, dead.text[:500]
    assert_strict_openapi_exchange(dead, ROUTE)

    again = pats_client.revoke(token["id"])
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
    assert NOT_FOUND in again.text


def test_owner_route_hides_other_users_token_from_admin(
    pats_client: PatsClient, mint_pat: MintPat, second_user: SecondUser
) -> None:
    token = mint_pat(as_user=second_user, expiryDays=30)

    # The owner route filters on the caller's userId, admin or not.
    foreign = pats_client.revoke(token["id"])
    assert foreign.status_code == 404, foreign.text[:500]
    assert_strict_openapi_exchange(foreign, ROUTE)
    assert NOT_FOUND in foreign.text

    resp = request_as(
        second_user, "DELETE", f"/{token['id']}", json={"reason": "spec audit"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "Personal access token revoked successfully"}


def test_malformed_token_id_is_rejected(pats_client: PatsClient) -> None:
    resp = pats_client.revoke(MALFORMED_TOKEN_ID)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unauthenticated_is_rejected(pats_client: PatsClient) -> None:
    resp = pats_client.revoke(MISSING_TOKEN_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_oauth_token_is_not_a_session(oauth_pats_client: PatsClient) -> None:
    resp = oauth_pats_client.revoke(MISSING_TOKEN_ID)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_reason_that_is_not_a_string_is_ignored(
    pats_client: PatsClient, mint_pat: MintPat
) -> None:
    token = mint_pat()

    # No body validator: the controller only keeps `reason` when it is a string.
    with outside_request_contract("reason is documented as a string; the handler drops other types"):
        resp = pats_client.revoke(token["id"], json={"reason": {"why": 5}})
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"message": "Personal access token revoked successfully"}


def test_revoke_with_a_service_token_is_forbidden(
    pats_client: PatsClient, service_token: str
) -> None:
    resp = request_with_token(
        pats_client._client.base_url, service_token, "DELETE", f"/{MISSING_TOKEN_ID}"
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "interactive user session" in resp.text
    assert_strict_openapi_exchange(resp, ROUTE)
