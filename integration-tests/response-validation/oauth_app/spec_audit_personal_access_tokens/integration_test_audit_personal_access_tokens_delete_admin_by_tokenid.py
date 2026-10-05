"""Strict OpenAPI audit of DELETE /api/v1/personal-access-tokens/admin/:tokenId."""

from __future__ import annotations

import pytest
from personal_access_tokens_audit_support import (
    MALFORMED_TOKEN_ID,
    MISSING_TOKEN_ID,
    MintPat,
    PatsClient,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/personal-access-tokens/admin/:tokenId"
REVOKED_MESSAGE = "Personal access token revoked successfully"


def test_admin_revokes_another_users_token_then_reports_not_found(
    pats_client: PatsClient,
    second_user: SecondUser,
    mint_pat: MintPat,
) -> None:
    token = mint_pat(as_user=second_user, expiryDays=30)

    resp = pats_client.admin_revoke(token["id"], json={"reason": "spec audit"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": REVOKED_MESSAGE}

    # An already revoked token is indistinguishable from an unknown one.
    again = pats_client.admin_revoke(token["id"])
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)


def test_member_cannot_use_admin_revoke_on_own_token(
    pats_client: PatsClient,
    second_user: SecondUser,
    mint_pat: MintPat,
) -> None:
    token = mint_pat(as_user=second_user)

    resp = request_as(second_user, "DELETE", f"/admin/{token['id']}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    # The refused call must not have revoked it: the admin still can.
    survived = pats_client.admin_revoke(token["id"])
    assert survived.status_code == 200, survived.text[:500]


@pytest.mark.parametrize(
    ("token_id", "expected_status"),
    [
        pytest.param(MISSING_TOKEN_ID, 404, id="unknown-id"),
        pytest.param(MALFORMED_TOKEN_ID, 400, id="malformed-id"),
    ],
)
def test_admin_revoke_rejects_bad_token_id(
    pats_client: PatsClient, token_id: str, expected_status: int
) -> None:
    resp = pats_client.admin_revoke(token_id)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_admin_revoke_without_token_is_unauthorized(pats_client: PatsClient) -> None:
    resp = pats_client.admin_revoke(MISSING_TOKEN_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
