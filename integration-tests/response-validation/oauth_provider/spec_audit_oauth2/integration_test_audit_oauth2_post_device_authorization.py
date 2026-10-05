"""Strict OpenAPI audit of POST /api/v1/oauth2/device_authorization."""

from __future__ import annotations

import uuid

import pytest
from oauth2_audit_support import (
    FIRST_PARTY_DEVICE_CLIENT_ID,
    UNKNOWN_CLIENT_ID,
    OAuth2Client,
    StartDeviceGrant,
)
from helper.http.session_client import SessionClient
from helper.mcp_oauth import create_oauth_app, delete_oauth_app
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/device_authorization"

UNKNOWN_SCOPE = "spec-audit:no-such-scope"


def test_first_party_client_starts_a_device_grant(
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()
    resp = grant.response

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert set(body) == {
        "device_code",
        "user_code",
        "verification_uri",
        "verification_uri_complete",
        "expires_in",
        "interval",
    }
    assert len(body["user_code"]) == 9 and body["user_code"][4] == "-"
    assert body["verification_uri"].endswith("/oauth/device")
    assert (
        body["verification_uri_complete"]
        == f"{body['verification_uri']}?user_code={body['user_code']}"
    )
    assert body["expires_in"] == 600
    assert body["interval"] == 5


def test_missing_client_id_fails_validation(oauth2_client: OAuth2Client) -> None:
    resp = oauth2_client.device_authorization(scope="openid")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # Validation runs before the controller, so this is not an OAuth-style body.
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_client_is_invalid_client(oauth2_client: OAuth2Client) -> None:
    resp = oauth2_client.device_authorization(client_id=UNKNOWN_CLIENT_ID)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"] == "invalid_client"


def test_unknown_scope_is_invalid_scope(oauth2_client: OAuth2Client) -> None:
    resp = oauth2_client.device_authorization(
        client_id=FIRST_PARTY_DEVICE_CLIENT_ID, scope=UNKNOWN_SCOPE
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"] == "invalid_scope"


def test_client_without_device_grant_is_unsupported_grant_type(
    oauth2_client: OAuth2Client, user_session_client: SessionClient
) -> None:
    base_url, session_jwt = user_session_client.base_url, user_session_client.token
    app = create_oauth_app(
        base_url,
        session_jwt,
        f"spec-audit-oauth2-{uuid.uuid4().hex[:8]}",
        scopes=["openid"],
        grant_types=["authorization_code", "refresh_token"],
    )
    try:
        resp = oauth2_client.device_authorization(client_id=app.client_id)
    finally:
        delete_oauth_app(base_url, session_jwt, app.id)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"] == "unsupported_grant_type"
