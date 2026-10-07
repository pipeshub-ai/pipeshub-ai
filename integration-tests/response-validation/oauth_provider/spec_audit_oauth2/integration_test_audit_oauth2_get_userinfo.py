"""Strict OpenAPI audit of GET /api/v1/oauth2/userinfo."""

from __future__ import annotations

import pytest
from helper.http.session_client import SessionClient
from helper.pipeshub_client import PipeshubClient
from oauth2_audit_support import (
    MALFORMED_ACCESS_TOKEN,
    OIDC_SCOPES,
    OAuth2Client,
    user_bound_access_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/userinfo"

PROFILE_CLAIMS = {"name", "given_name", "family_name", "updated_at"}
EMAIL_CLAIMS = {"email", "email_verified"}


@pytest.mark.parametrize(
    ("scopes", "allowed_claims"),
    [
        pytest.param(OIDC_SCOPES, PROFILE_CLAIMS | EMAIL_CLAIMS, id="openid_profile_email"),
        pytest.param(("openid",), set(), id="openid_only"),
    ],
)
def test_userinfo_returns_the_claims_the_scopes_grant(
    oauth2_client: OAuth2Client,
    pipeshub_client: PipeshubClient,
    user_session_client: SessionClient,
    scopes: tuple[str, ...],
    allowed_claims: set[str],
) -> None:
    with user_bound_access_token(
        pipeshub_client.base_url, user_session_client.token, scopes
    ) as token:
        resp = oauth2_client.userinfo(token)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert isinstance(body["user_id"], str) and body["user_id"]
    # Unset user fields are dropped from the JSON, so the claim set is an upper bound.
    assert set(body) - {"user_id"} <= allowed_claims, body
    if "email" in scopes:
        assert body.get("email"), body


@pytest.mark.parametrize(
    ("access_token", "error"),
    [
        pytest.param(None, "invalid_request", id="no_token"),
        pytest.param(MALFORMED_ACCESS_TOKEN, "invalid_token", id="malformed_token"),
    ],
)
def test_userinfo_without_a_valid_token_is_unauthorized(
    oauth2_client: OAuth2Client, access_token: str | None, error: str
) -> None:
    resp = oauth2_client.userinfo(access_token)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"] == error
    assert resp.headers["WWW-Authenticate"].startswith('Bearer realm="oauth"')


def test_userinfo_without_openid_scope_is_forbidden(
    oauth2_client: OAuth2Client,
    pipeshub_client: PipeshubClient,
    user_session_client: SessionClient,
) -> None:
    with user_bound_access_token(
        pipeshub_client.base_url, user_session_client.token, ("profile", "email")
    ) as token:
        resp = oauth2_client.userinfo(token)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "error": "insufficient_scope",
        "error_description": "Required scopes: openid",
        "scope": "openid",
    }


def test_userinfo_ignores_query_parameters(
    oauth2_client: OAuth2Client,
    pipeshub_client: PipeshubClient,
    user_session_client: SessionClient,
) -> None:
    with user_bound_access_token(
        pipeshub_client.base_url, user_session_client.token, ("openid",)
    ) as token:
        with outside_request_contract("the route takes no parameters; this shows it ignores them"):
            resp = oauth2_client.get(
                "/userinfo",
                auth=False,
                params={"spec_audit": "ignored"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text[:500]
            assert_strict_openapi_exchange(resp, ROUTE)
    assert set(resp.json()) == {"user_id"}
