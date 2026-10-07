"""Strict OpenAPI audit of POST /api/v1/oauth2/introspect."""

from __future__ import annotations

import pytest
from helper.mcp_oauth import OAuthApp
from oauth2_audit_support import (
    UNKNOWN_CLIENT_ID,
    OAuth2Client,
    authorization_code,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/introspect"

ACTIVE_FIELDS = {"active", "scope", "client_id", "token_type", "exp", "iat", "user_id", "iss", "jti"}


def _user_tokens(
    client: OAuth2Client, session_client: OAuth2Client, app: OAuthApp
) -> dict[str, str]:
    code, verifier = authorization_code(session_client, app, "openid offline_access")
    resp = client.token(
        grant_type="authorization_code",
        code=code,
        redirect_uri=app.redirect_uri,
        client_id=app.client_id,
        client_secret=app.client_secret,
        code_verifier=verifier,
    )
    assert resp.status_code == 200, resp.text[:500]
    return resp.json()


@pytest.mark.parametrize("form", [False, True], ids=["json", "form"])
def test_client_credentials_token_is_active_without_username(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp, form: bool
) -> None:
    token = oauth2_client.token(
        grant_type="client_credentials",
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
    ).json()["access_token"]

    resp = oauth2_client.introspect(
        form=form,
        token=token,
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert set(body) == ACTIVE_FIELDS, body
    assert body["active"] is True
    assert body["token_type"] == "Bearer"
    assert body["client_id"] == confidential_app.client_id
    # A client-credentials token is issued with the client id in the user id claim.
    assert body["user_id"] == confidential_app.client_id


@pytest.mark.parametrize(
    ("which", "token_type"),
    [
        pytest.param("access_token", "Bearer", id="access_token"),
        pytest.param("refresh_token", "refresh_token", id="refresh_token"),
    ],
)
def test_user_token_is_active_with_username(
    oauth2_client: OAuth2Client,
    oauth2_session_client: OAuth2Client,
    confidential_app: OAuthApp,
    which: str,
    token_type: str,
) -> None:
    tokens = _user_tokens(oauth2_client, oauth2_session_client, confidential_app)

    resp = oauth2_client.introspect(
        token=tokens[which],
        token_type_hint=which,
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["active"] is True
    assert body["token_type"] == token_type
    assert body["username"] == body["user_id"]
    assert body["scope"] == "openid offline_access"


def test_unknown_token_is_inactive(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    resp = oauth2_client.introspect(
        token="spec-audit-no-such-token",
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"active": False}


def test_another_clients_token_is_inactive(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    # The suite's own client-credentials token was issued to a different client.
    oauth2_client._client._ensure_access_token()
    resp = oauth2_client.introspect(
        token=oauth2_client._client._access_token,
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"active": False}


def test_without_client_secret_even_a_live_token_is_inactive(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    token = oauth2_client.token(
        grant_type="client_credentials",
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
    ).json()["access_token"]

    resp = oauth2_client.introspect(token=token, client_id=confidential_app.client_id)

    # The missing secret throws inside client authentication and the catch-all answers inactive.
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"active": False}


@pytest.mark.parametrize(
    ("client_id", "secret", "description"),
    [
        pytest.param(UNKNOWN_CLIENT_ID, "x", "Invalid client_id", id="unknown_client"),
        pytest.param(None, "wrong", "Invalid client credentials", id="wrong_secret"),
    ],
)
def test_client_authentication_failure_is_invalid_client(
    oauth2_client: OAuth2Client,
    confidential_app: OAuthApp,
    client_id: str | None,
    secret: str,
    description: str,
) -> None:
    resp = oauth2_client.introspect(
        token="spec-audit-no-such-token",
        client_id=client_id or confidential_app.client_id,
        client_secret=secret,
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"error": "invalid_client", "error_description": description}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"client_id": "abc"}, "body.token", id="missing_token"),
        pytest.param({"token": "", "client_id": "abc"}, "body.token", id="empty_token"),
        pytest.param({"token": "t"}, "body.client_id", id="missing_client_id"),
        pytest.param({"token": "t", "client_id": ""}, "body.client_id", id="empty_client_id"),
        pytest.param(
            {"token": "t", "client_id": "abc", "token_type_hint": "id_token"},
            "body.token_type_hint",
            id="hint_outside_enum",
        ),
    ],
)
def test_invalid_body_fails_validation(
    oauth2_client: OAuth2Client, body: dict[str, str], field: str
) -> None:
    resp = oauth2_client.introspect(**body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]
