"""Strict OpenAPI audit of POST /api/v1/oauth2/token."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.mcp_oauth import OAuthApp
from oauth2_audit_support import (
    DEVICE_GRANT_TYPE,
    FIRST_PARTY_DEVICE_CLIENT_ID,
    UNKNOWN_CLIENT_ID,
    OAuth2Client,
    StartDeviceGrant,
    authorization_code,
    delete_issued_tokens,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/token"

ACCESS_TOKEN_FIELDS = {"access_token", "token_type", "expires_in", "scope"}


def _assert_tokens(resp: requests.Response, *, refresh: bool) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Pragma"] == "no-cache"
    body = resp.json()
    assert set(body) == ACCESS_TOKEN_FIELDS | ({"refresh_token"} if refresh else set()), body
    assert body["token_type"] == "Bearer"
    return body


def _cc_body(app: OAuthApp, **extra: Any) -> dict[str, Any]:
    return {
        "grant_type": "client_credentials",
        "client_id": app.client_id,
        "client_secret": app.client_secret,
        **extra,
    }


@pytest.mark.parametrize("form", [False, True], ids=["json", "form"])
def test_client_credentials_issues_an_access_token(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp, form: bool
) -> None:
    body = _assert_tokens(oauth2_client.token(form=form, **_cc_body(confidential_app)), refresh=False)
    # User-bound scopes are dropped from a client-credentials token.
    assert body["scope"] == ""


def test_client_credentials_with_basic_authentication(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    resp = oauth2_client.token_with_basic_auth(
        confidential_app.client_id,
        confidential_app.client_secret,
        grant_type="client_credentials",
    )
    _assert_tokens(resp, refresh=False)


@pytest.mark.parametrize(
    ("scope", "refresh"),
    [
        pytest.param("openid email offline_access", True, id="offline_access"),
        pytest.param("openid email", False, id="no_offline_access"),
    ],
)
def test_authorization_code_issues_a_refresh_token_only_for_offline_access(
    oauth2_client: OAuth2Client,
    oauth2_session_client: OAuth2Client,
    confidential_app: OAuthApp,
    scope: str,
    refresh: bool,
) -> None:
    code, verifier = authorization_code(oauth2_session_client, confidential_app, scope)
    issued = _assert_tokens(
        oauth2_client.token(
            grant_type="authorization_code",
            code=code,
            redirect_uri=confidential_app.redirect_uri,
            client_id=confidential_app.client_id,
            client_secret=confidential_app.client_secret,
            code_verifier=verifier,
        ),
        refresh=refresh,
    )
    assert issued["scope"] == scope


@pytest.mark.parametrize(
    ("scope", "refresh"),
    [
        pytest.param(None, True, id="same_scopes"),
        pytest.param("openid", False, id="narrowed_without_offline_access"),
    ],
)
def test_refresh_token_rotates(
    oauth2_client: OAuth2Client,
    oauth2_session_client: OAuth2Client,
    confidential_app: OAuthApp,
    scope: str | None,
    refresh: bool,
) -> None:
    code, verifier = authorization_code(
        oauth2_session_client, confidential_app, "openid offline_access"
    )
    issued = oauth2_client.token(
        grant_type="authorization_code",
        code=code,
        redirect_uri=confidential_app.redirect_uri,
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
        code_verifier=verifier,
    ).json()
    body: dict[str, Any] = {
        "grant_type": "refresh_token",
        "refresh_token": issued["refresh_token"],
        "client_id": confidential_app.client_id,
        "client_secret": confidential_app.client_secret,
    }
    if scope:
        body["scope"] = scope

    refreshed = _assert_tokens(oauth2_client.token(**body), refresh=refresh)
    assert refreshed["scope"] == (scope or "openid offline_access")

    # Rotation: the old refresh token is revoked, and reusing it is reported as a server error.
    reused = oauth2_client.token(**body)
    assert reused.status_code == 400, reused.text[:500]
    assert_strict_openapi_exchange(reused, ROUTE)
    assert reused.json()["error"] == "server_error"


def test_device_code_poll_until_approved(
    oauth2_client: OAuth2Client,
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()
    poll = {
        "grant_type": DEVICE_GRANT_TYPE,
        "client_id": FIRST_PARTY_DEVICE_CLIENT_ID,
        "device_code": grant.device_code,
    }

    pending = oauth2_client.token(**poll)
    assert pending.status_code == 400, pending.text[:500]
    assert_strict_openapi_exchange(pending, ROUTE)
    assert pending.json() == {
        "error": "authorization_pending",
        "error_description": "authorization is still pending",
    }

    too_soon = oauth2_client.token(**poll)
    assert too_soon.status_code == 400, too_soon.text[:500]
    assert_strict_openapi_exchange(too_soon, ROUTE)
    assert too_soon.json()["error"] == "slow_down"

    approved = oauth2_session_client.device_consent(user_code=grant.user_code, consent="granted")
    assert approved.status_code == 200, approved.text[:500]

    resp = oauth2_client.token(**poll)
    try:
        # pipeshub-agent does not hold offline_access, so no refresh token is issued.
        body = _assert_tokens(resp, refresh=False)
        assert "kb:read" in body["scope"].split()
    finally:
        if resp.status_code == 200:
            delete_issued_tokens(resp.json()["access_token"])

    claimed = oauth2_client.token(**poll)
    assert claimed.status_code == 400, claimed.text[:500]
    assert_strict_openapi_exchange(claimed, ROUTE)
    assert claimed.json()["error"] == "expired_token"


def test_device_code_poll_after_denial_is_access_denied(
    oauth2_client: OAuth2Client,
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()
    denied = oauth2_session_client.device_consent(user_code=grant.user_code, consent="denied")
    assert denied.status_code == 200, denied.text[:500]

    resp = oauth2_client.token(
        grant_type=DEVICE_GRANT_TYPE,
        client_id=FIRST_PARTY_DEVICE_CLIENT_ID,
        device_code=grant.device_code,
    )

    # RFC 8628 access_denied, but with 400: only the client-auth failure here is not a 400.
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"] == "access_denied"


@pytest.mark.parametrize(
    ("body", "error", "description"),
    [
        pytest.param(
            {"grant_type": "authorization_code"}, "invalid_grant", "code is required", id="no_code"
        ),
        pytest.param(
            {"grant_type": "authorization_code", "code": "spec-audit-no-such-code",
             "redirect_uri": "http://localhost/callback"},
            "invalid_grant",
            "Invalid or expired authorization code",
            id="unknown_code",
        ),
        pytest.param(
            {"grant_type": "refresh_token"}, "invalid_grant", "refresh_token is required",
            id="no_refresh_token",
        ),
        pytest.param(
            {"grant_type": "refresh_token", "refresh_token": "spec-audit-no-such-token"},
            "server_error",
            "Invalid refresh token",
            id="unknown_refresh_token",
        ),
        pytest.param(
            {"grant_type": "client_credentials", "scope": "kb:write"},
            "invalid_scope",
            "Scopes not allowed for this app: kb:write",
            id="scope_not_allowed",
        ),
        pytest.param(
            {"grant_type": DEVICE_GRANT_TYPE, "device_code": "spec-audit-no-such-code"},
            "unsupported_grant_type",
            "device_code grant not allowed for this app",
            id="app_without_device_grant",
        ),
    ],
)
def test_grant_refusal_is_an_oauth_400(
    oauth2_client: OAuth2Client,
    confidential_app: OAuthApp,
    body: dict[str, str],
    error: str,
    description: str,
) -> None:
    resp = oauth2_client.token(
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
        **body,
    )

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"error": error, "error_description": description}


@pytest.mark.parametrize(
    ("body", "error"),
    [
        pytest.param({"device_code": "spec-audit-no-such-code"}, "expired_token", id="unknown_device_code"),
        pytest.param({}, "invalid_grant", id="no_device_code"),
    ],
)
def test_device_code_refusal_is_an_oauth_400(
    oauth2_client: OAuth2Client, body: dict[str, str], error: str
) -> None:
    resp = oauth2_client.token(
        grant_type=DEVICE_GRANT_TYPE, client_id=FIRST_PARTY_DEVICE_CLIENT_ID, **body
    )

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"] == error


def test_client_credentials_without_secret_is_a_server_error_400(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    resp = oauth2_client.token(grant_type="client_credentials", client_id=confidential_app.client_id)

    # The missing secret reaches the hash function and its TypeError is reported as the error.
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["error"] == "server_error"
    assert "Received undefined" in body["error_description"]


@pytest.mark.parametrize(
    ("body", "description"),
    [
        pytest.param({"grant_type": "client_credentials"}, "client_id is required", id="no_client_id"),
        pytest.param(
            {"grant_type": "client_credentials", "client_id": UNKNOWN_CLIENT_ID, "client_secret": "x"},
            "Invalid client_id",
            id="unknown_client",
        ),
        pytest.param(
            {"grant_type": "client_credentials", "client_secret": "wrong"},
            "Invalid client credentials",
            id="wrong_secret",
        ),
        pytest.param(
            {"grant_type": "authorization_code", "code": "x", "redirect_uri": "http://localhost/callback"},
            "client_secret required for confidential clients",
            id="confidential_client_without_secret",
        ),
    ],
)
def test_client_authentication_failure_is_invalid_client(
    oauth2_client: OAuth2Client,
    confidential_app: OAuthApp,
    body: dict[str, str],
    description: str,
) -> None:
    if body["grant_type"] != "client_credentials" or "client_secret" in body and "client_id" not in body:
        body = {"client_id": confidential_app.client_id, **body}

    resp = oauth2_client.token(**body)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"error": "invalid_client", "error_description": description}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"client_id": "abc"}, "body.grant_type", id="missing_grant_type"),
        pytest.param({"grant_type": "password", "client_id": "abc"}, "body.grant_type", id="password_grant"),
        pytest.param(
            {"grant_type": "authorization_code", "client_id": "abc", "code": "x", "code_verifier": "short"},
            "body.code_verifier",
            id="malformed_code_verifier",
        ),
        pytest.param({"grant_type": "client_credentials", "client_id": ""}, "body.client_id", id="empty_client_id"),
        pytest.param(
            {"grant_type": DEVICE_GRANT_TYPE, "client_id": FIRST_PARTY_DEVICE_CLIENT_ID, "device_code": ""},
            "body.device_code",
            id="empty_device_code",
        ),
        pytest.param(
            {"grant_type": "client_credentials", "client_id": "abc", "scope": 7},
            "body.scope",
            id="scope_not_a_string",
        ),
    ],
)
def test_invalid_body_fails_validation(
    oauth2_client: OAuth2Client, body: dict[str, Any], field: str
) -> None:
    resp = oauth2_client.token(**body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]
