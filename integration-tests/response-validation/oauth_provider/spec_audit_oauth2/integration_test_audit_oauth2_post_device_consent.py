"""Strict OpenAPI audit of POST /api/v1/oauth2/device/consent."""

from __future__ import annotations

import pytest
from oauth2_audit_support import (
    UNKNOWN_USER_CODE,
    OAuth2Client,
    StartDeviceGrant,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/device/consent"


def test_consent_granted_then_replay_is_invalid_request(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_consent(
        user_code=grant.user_code, consent="granted"
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"ok": True, "consent": "granted"}

    # Only pending codes are found, so a decided one looks unknown.
    again = oauth2_session_client.device_consent(
        user_code=grant.user_code, consent="denied"
    )
    assert again.status_code == 400, again.text[:500]
    assert_strict_openapi_response(again, ROUTE)
    assert again.json()["error"] == "invalid_request"


def test_member_denies_device_grant(
    second_user: SecondUser,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = request_as(
        second_user,
        "POST",
        "/device/consent",
        json={"user_code": grant.user_code, "consent": "denied"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"ok": True, "consent": "denied"}


def test_consent_without_token_is_unauthorized(oauth2_client: OAuth2Client) -> None:
    resp = oauth2_client.device_consent(
        auth=False, user_code=UNKNOWN_USER_CODE, consent="granted"
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_consent_with_oauth_token_is_forbidden(
    oauth2_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    # A real pending code: the refusal must come from the session-only gate, not the lookup.
    grant = start_device_grant()

    resp = oauth2_client.device_consent(user_code=grant.user_code, consent="granted")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_consent_value_outside_enum_is_rejected(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_consent(
        user_code=grant.user_code, consent="approved"
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json().get("error") != "invalid_request", (
        "zod must reject the body before the user_code lookup"
    )
