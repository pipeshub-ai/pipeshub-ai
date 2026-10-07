"""Strict OpenAPI audit of POST /api/v1/oauth2/device/verify."""

from __future__ import annotations

from typing import Any, Callable

import pytest
import requests
from oauth2_audit_support import (
    assert_form_schema_refuses,
    MALFORMED_USER_CODE,
    OAUTH2_BASE,
    UNKNOWN_CLIENT_ID,
    UNKNOWN_USER_CODE,
    OAuth2Client,
    StartDeviceGrant,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

SPEC_PATH = "/oauth2/device/verify"
ROUTE = "/api/v1/oauth2/device/verify"


def test_verify_returns_consent_data_for_pending_user_code(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_verify(user_code=grant.user_code)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_verify_with_oauth_access_token_is_forbidden(
    oauth2_client: OAuth2Client,
) -> None:
    resp = oauth2_client.device_verify(user_code=UNKNOWN_USER_CODE)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_verify_overlong_user_code_fails_validation(
    oauth2_session_client: OAuth2Client,
) -> None:
    resp = oauth2_session_client.device_verify(user_code=MALFORMED_USER_CODE)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_verify_unknown_user_code_is_invalid_request(
    oauth2_session_client: OAuth2Client,
) -> None:
    resp = oauth2_session_client.device_verify(user_code=UNKNOWN_USER_CODE)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "error": "invalid_request",
        "error_description": "Unknown user_code",
    }


@pytest.mark.parametrize(
    ("spell", "extra"),
    [
        pytest.param(lambda code: code.replace("-", "").lower(), {}, id="lowercase_without_dash"),
        pytest.param(lambda code: code, {"consent": "denied"}, id="consent_field_ignored"),
    ],
)
def test_verify_accepts_other_spellings_and_ignores_consent(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
    spell: Callable[[str], str],
    extra: dict[str, str],
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_verify(user_code=spell(grant.user_code), **extra)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    # Still pending: consent sent here is validated and then dropped.
    again = oauth2_session_client.device_verify(user_code=grant.user_code)
    assert again.status_code == 200, again.text[:500]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({}, "body.user_code", id="missing_user_code"),
        pytest.param({"user_code": ""}, "body.user_code", id="empty_user_code"),
        pytest.param({"user_code": "ABCD-EFGH", "consent": "maybe"}, "body.consent", id="consent_outside_enum"),
    ],
)
def test_verify_invalid_body_fails_validation(
    oauth2_session_client: OAuth2Client, body: dict[str, Any], field: str
) -> None:
    resp = oauth2_session_client.device_verify(**body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


def test_verify_code_without_letters_or_digits_is_invalid_request(
    oauth2_session_client: OAuth2Client,
) -> None:
    resp = oauth2_session_client.device_verify(user_code="----")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"error": "invalid_request", "error_description": "user_code is required"}


def test_verify_decided_code_is_invalid_request(
    oauth2_session_client: OAuth2Client, start_device_grant: StartDeviceGrant
) -> None:
    grant = start_device_grant()
    decided = oauth2_session_client.device_consent(user_code=grant.user_code, consent="denied")
    assert decided.status_code == 200, decided.text[:500]

    resp = oauth2_session_client.device_verify(user_code=grant.user_code)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "error": "invalid_request",
        "error_description": "user_code has already been used",
    }


def test_verify_expired_code_is_expired_token(
    oauth2_session_client: OAuth2Client, inserted_device_grant: Callable[..., str]
) -> None:
    user_code = inserted_device_grant(expires_in=-60)

    resp = oauth2_session_client.device_verify(user_code=user_code)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"error": "expired_token", "error_description": "user_code has expired"}


def test_verify_code_of_a_removed_app_is_unauthorized(
    oauth2_session_client: OAuth2Client, inserted_device_grant: Callable[..., str]
) -> None:
    user_code = inserted_device_grant(client_id=UNKNOWN_CLIENT_ID)

    resp = oauth2_session_client.device_verify(user_code=user_code)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "OAUTH_INVALID_CLIENT"


def test_verify_with_deleted_users_session_is_unauthorized(
    oauth2_client: OAuth2Client, deleted_user_session_jwt: str
) -> None:
    resp = requests.post(
        f"{oauth2_client._client.base_url}{OAUTH2_BASE}/device/verify",
        headers={"Authorization": f"Bearer {deleted_user_session_jwt}"},
        json={"user_code": UNKNOWN_USER_CODE},
        timeout=30,
    )

    # The authentication middleware finds the account gone before the route's own 404 check.
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_UNAUTHORIZED"


def test_verify_accepts_a_form_encoded_body(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_verify(form=True, user_code=grant.user_code)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["requiresConsent"] is True


def test_verify_validates_a_form_encoded_body(oauth2_session_client: OAuth2Client) -> None:
    with outside_request_contract(
        "the gate does not read form bodies; assert_form_schema_refuses checks the request instead"
    ):
        resp = oauth2_session_client.device_verify(form=True, user_code=MALFORMED_USER_CODE)
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert_form_schema_refuses(resp, SPEC_PATH)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == ["body.user_code"]
