"""Strict OpenAPI audit of GET /api/v1/personal-access-tokens.

authenticate -> requireSessionAuth -> rate limiter -> refuseServiceAccountCaller -> listTokens.
"""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from personal_access_tokens_audit_support import (
    MintPat,
    PatsClient,
    request_as,
    request_with_token,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/personal-access-tokens"
ROW_KEYS = {"id", "name", "scopes", "createdAt", "expiresAt", "lastUsedAt"}


def test_list_returns_only_the_callers_tokens_without_secrets(
    pats_client: PatsClient, mint_pat: MintPat, second_user: SecondUser
) -> None:
    dated = mint_pat(expiryDays=30)
    endless = mint_pat(expiryDays="never")
    foreign = mint_pat(as_user=second_user)

    resp = pats_client.list()

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"tokens"}, body.keys()
    rows = {row["id"]: row for row in body["tokens"]}
    assert dated["id"] in rows
    assert endless["id"] in rows
    assert foreign["id"] not in rows
    for minted in (dated, endless):
        row = rows[minted["id"]]
        assert row["name"] == minted["name"]
        assert row["scopes"] == minted["scopes"]
        assert "accessToken" not in row
        assert set(row) <= ROW_KEYS, row.keys()
    assert_strict_openapi_response(resp, ROUTE)


def test_list_as_member_is_allowed_and_scoped_to_the_member(
    mint_pat: MintPat, second_user: SecondUser
) -> None:
    own = mint_pat(as_user=second_user, expiryDays=90)
    admins = mint_pat()

    resp = request_as(second_user, "GET")

    assert resp.status_code == 200, resp.text[:500]
    ids = [row["id"] for row in resp.json()["tokens"]]
    assert own["id"] in ids
    assert admins["id"] not in ids
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_token_is_unauthorized(pats_client: PatsClient) -> None:
    resp = pats_client.list(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_with_oauth_token_is_forbidden(oauth_pats_client: PatsClient) -> None:
    resp = oauth_pats_client.list()

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_with_a_pat_as_credential_is_forbidden(
    pats_client: PatsClient, mint_pat: MintPat
) -> None:
    token = mint_pat()

    resp = request_with_token(pats_client._client.base_url, token["accessToken"], "GET")

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
