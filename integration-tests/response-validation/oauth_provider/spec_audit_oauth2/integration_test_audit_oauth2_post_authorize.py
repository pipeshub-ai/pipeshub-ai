"""Strict OpenAPI audit of POST /api/v1/oauth2/authorize (the consent decision)."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from helper.http.session_client import SESSION_REQUIRED_MESSAGE
from helper.mcp_oauth import OAuthApp
from oauth2_audit_support import (
    assert_form_schema_refuses,
    UNKNOWN_CLIENT_ID,
    OAuth2Client,
    consent_body,
    pkce_pair,
    redirect_params,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

SPEC_PATH = "/oauth2/authorize"
ROUTE = "/api/v1/oauth2/authorize"


def test_granted_consent_redirects_with_a_code(
    oauth2_session_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    _, challenge = pkce_pair()
    body = consent_body(confidential_app, code_challenge=challenge, code_challenge_method="S256")

    resp = oauth2_session_client.authorize_consent(**body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    redirect_url = resp.json()["redirectUrl"]
    assert redirect_url.startswith(confidential_app.redirect_uri + "?")
    query = redirect_params(redirect_url)
    assert set(query) == {"code", "state"}
    assert query["state"] == body["state"]


def test_confidential_client_may_omit_pkce(
    oauth2_session_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    resp = oauth2_session_client.authorize_consent(**consent_body(confidential_app))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "code" in redirect_params(resp.json()["redirectUrl"])


@pytest.mark.parametrize(
    ("app_fixture", "consent", "error"),
    [
        pytest.param("confidential_app", "denied", "access_denied", id="denied"),
        pytest.param("public_app", "granted", "invalid_request", id="public_client_without_pkce"),
    ],
)
def test_refused_consent_redirects_with_an_error(
    request: pytest.FixtureRequest,
    oauth2_session_client: OAuth2Client,
    app_fixture: str,
    consent: str,
    error: str,
) -> None:
    app: OAuthApp = request.getfixturevalue(app_fixture)
    body = consent_body(app, consent=consent)

    resp = oauth2_session_client.authorize_consent(**body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    query = redirect_params(resp.json()["redirectUrl"])
    assert query["error"] == error
    assert query["state"] == body["state"]
    assert "code" not in query


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        pytest.param(
            {"client_id": UNKNOWN_CLIENT_ID}, 401, "OAUTH_INVALID_CLIENT", id="unknown_client"
        ),
        pytest.param(
            {"redirect_uri": "http://localhost/not-registered"},
            400,
            "OAUTH_INVALID_REDIRECT_URI",
            id="unregistered_redirect_uri",
        ),
        pytest.param({"scope": "kb:write"}, 400, "OAUTH_INVALID_SCOPE", id="scope_not_allowed"),
    ],
)
def test_client_redirect_or_scope_refusal_is_an_error_envelope(
    oauth2_session_client: OAuth2Client,
    confidential_app: OAuthApp,
    overrides: dict[str, str],
    status: int,
    code: str,
) -> None:
    resp = oauth2_session_client.authorize_consent(**consent_body(confidential_app, **overrides))

    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == code


def test_without_token_is_unauthorized(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    resp = oauth2_client.authorize_consent(auth=False, **consent_body(confidential_app))

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_deleted_users_session_is_unauthorized(
    deleted_user_session_jwt: str, confidential_app: OAuthApp, oauth2_client: OAuth2Client
) -> None:
    resp = requests.post(
        f"{oauth2_client._client.base_url}{ROUTE}",
        headers={"Authorization": f"Bearer {deleted_user_session_jwt}"},
        json=consent_body(confidential_app),
        timeout=30,
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_oauth_access_token_is_forbidden(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    resp = oauth2_client.authorize_consent(**consent_body(confidential_app))

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert SESSION_REQUIRED_MESSAGE in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"consent": None}, "body.consent", id="missing_consent"),
        pytest.param({"consent": "maybe"}, "body.consent", id="consent_outside_enum"),
        pytest.param({"state": ""}, "body.state", id="empty_state"),
        pytest.param({"scope": ""}, "body.scope", id="empty_scope"),
        pytest.param({"client_id": ""}, "body.client_id", id="empty_client_id"),
        pytest.param({"code_challenge": "too-short"}, "body.code_challenge", id="bad_challenge"),
        pytest.param(
            {"code_challenge": "A" * 43, "code_challenge_method": "plain"},
            "body.code_challenge_method",
            id="plain_challenge_method",
        ),
    ],
)
def test_invalid_body_fails_validation(
    oauth2_session_client: OAuth2Client,
    confidential_app: OAuthApp,
    overrides: dict[str, Any],
    field: str,
) -> None:
    body = {k: v for k, v in consent_body(confidential_app, **overrides).items() if v is not None}

    resp = oauth2_session_client.authorize_consent(**body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]


def test_form_encoded_consent_is_accepted(
    oauth2_session_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    body = consent_body(confidential_app)

    resp = oauth2_session_client.authorize_consent(form=True, **body)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert_strict_openapi_exchange(resp, ROUTE)
    query = redirect_params(resp.json()["redirectUrl"])
    assert set(query) == {"code", "state"}
    assert query["state"] == body["state"]


def test_form_encoded_consent_is_validated(
    oauth2_session_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    with outside_request_contract(
        "the gate does not read form bodies; assert_form_schema_refuses checks the request instead"
    ):
        resp = oauth2_session_client.authorize_consent(
            form=True, **consent_body(confidential_app, consent="maybe")
        )
        assert resp.status_code == 400, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert_form_schema_refuses(resp, SPEC_PATH)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == ["body.consent"]
