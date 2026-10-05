"""Strict OpenAPI audit of POST /api/v1/oauth2/device/verify."""

from __future__ import annotations

import pytest
from oauth2_audit_support import (
    MALFORMED_USER_CODE,
    UNKNOWN_USER_CODE,
    OAuth2Client,
    StartDeviceGrant,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/device/verify"


def test_verify_returns_consent_data_for_pending_user_code(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_verify(user_code=grant.user_code)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["requiresConsent"] is True
    consent_data = body["consentData"]
    assert consent_data["app"]["name"]
    assert isinstance(consent_data["scopes"], list)
    assert consent_data["user"]["email"]
    # The device flow has no redirect, so the consent page gets empty placeholders.
    assert consent_data["redirectUri"] == ""
    assert consent_data["state"] == ""


def test_verify_without_token_is_unauthorized(oauth2_client: OAuth2Client) -> None:
    resp = oauth2_client.device_verify(auth=False, user_code=UNKNOWN_USER_CODE)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_verify_with_oauth_access_token_is_forbidden(
    oauth2_client: OAuth2Client,
) -> None:
    resp = oauth2_client.device_verify(user_code=UNKNOWN_USER_CODE)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_verify_overlong_user_code_fails_validation(
    oauth2_session_client: OAuth2Client,
) -> None:
    resp = oauth2_session_client.device_verify(user_code=MALFORMED_USER_CODE)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_verify_unknown_user_code_is_invalid_request(
    oauth2_session_client: OAuth2Client,
) -> None:
    resp = oauth2_session_client.device_verify(user_code=UNKNOWN_USER_CODE)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "error": "invalid_request",
        "error_description": "Unknown user_code",
    }
