"""Strict OpenAPI audit of POST /api/v1/oauth2/device/consent."""

from __future__ import annotations

from typing import Any, Callable

import pytest
import requests
from oauth2_audit_support import (
    assert_form_schema_refuses,
    OAUTH2_BASE,
    UNKNOWN_CLIENT_ID,
    UNKNOWN_USER_CODE,
    OAuth2Client,
    StartDeviceGrant,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

SPEC_PATH = "/oauth2/device/consent"
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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"ok": True, "consent": "granted"}

    # Only pending codes are found, so a decided one looks unknown.
    again = oauth2_session_client.device_consent(
        user_code=grant.user_code, consent="denied"
    )
    assert again.status_code == 400, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"ok": True, "consent": "denied"}


def test_consent_without_token_is_unauthorized(oauth2_client: OAuth2Client) -> None:
    resp = oauth2_client.device_consent(
        auth=False, user_code=UNKNOWN_USER_CODE, consent="granted"
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_consent_with_oauth_token_is_forbidden(
    oauth2_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    # A real pending code: the refusal must come from the session-only gate, not the lookup.
    grant = start_device_grant()

    resp = oauth2_client.device_consent(user_code=grant.user_code, consent="granted")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_consent_value_outside_enum_is_rejected(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_consent(
        user_code=grant.user_code, consent="approved"
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json().get("error") != "invalid_request", (
        "zod must reject the body before the user_code lookup"
    )


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"user_code": "ABCD-EFGH"}, "body.consent", id="missing_consent"),
        pytest.param({"consent": "granted"}, "body.user_code", id="missing_user_code"),
        pytest.param({"user_code": "A" * 33, "consent": "granted"}, "body.user_code", id="overlong_user_code"),
    ],
)
def test_consent_invalid_body_fails_validation(
    oauth2_session_client: OAuth2Client, body: dict[str, Any], field: str
) -> None:
    resp = oauth2_session_client.device_consent(**body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


@pytest.mark.parametrize(
    ("user_code", "description"),
    [
        pytest.param(UNKNOWN_USER_CODE, "Unknown user_code", id="unknown_code"),
        pytest.param("----", "user_code is required", id="no_letters_or_digits"),
    ],
)
def test_consent_unusable_code_is_invalid_request(
    oauth2_session_client: OAuth2Client, user_code: str, description: str
) -> None:
    resp = oauth2_session_client.device_consent(user_code=user_code, consent="granted")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"error": "invalid_request", "error_description": description}


def test_consent_expired_code_is_expired_token(
    oauth2_session_client: OAuth2Client, inserted_device_grant: Callable[..., str]
) -> None:
    user_code = inserted_device_grant(expires_in=-60)

    resp = oauth2_session_client.device_consent(user_code=user_code, consent="granted")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"error": "expired_token", "error_description": "user_code has expired"}


def test_consent_to_a_code_of_a_removed_app_is_recorded(
    oauth2_session_client: OAuth2Client, inserted_device_grant: Callable[..., str]
) -> None:
    user_code = inserted_device_grant(client_id=UNKNOWN_CLIENT_ID)

    resp = oauth2_session_client.device_consent(user_code=user_code, consent="granted")

    # Unlike /device/verify, consent does not look the app up.
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"ok": True, "consent": "granted"}


def test_consent_with_deleted_users_session_is_unauthorized(
    oauth2_client: OAuth2Client, deleted_user_session_jwt: str
) -> None:
    resp = requests.post(
        f"{oauth2_client._client.base_url}{OAUTH2_BASE}/device/consent",
        headers={"Authorization": f"Bearer {deleted_user_session_jwt}"},
        json={"user_code": UNKNOWN_USER_CODE, "consent": "denied"},
        timeout=30,
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_UNAUTHORIZED"


def test_consent_accepts_a_form_encoded_body(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    resp = oauth2_session_client.device_consent(
        form=True, user_code=grant.user_code, consent="denied"
    )

    assert resp.status_code == 200, resp.text[:500]
    assert resp.request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"ok": True, "consent": "denied"}


def test_consent_validates_a_form_encoded_body(
    oauth2_session_client: OAuth2Client,
    start_device_grant: StartDeviceGrant,
) -> None:
    grant = start_device_grant()

    with outside_request_contract(
        "the gate does not read form bodies; assert_form_schema_refuses checks the request instead"
    ):
        resp = oauth2_session_client.device_consent(
            form=True, user_code=grant.user_code, consent="maybe"
        )
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert_form_schema_refuses(resp, SPEC_PATH)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == ["body.consent"]
