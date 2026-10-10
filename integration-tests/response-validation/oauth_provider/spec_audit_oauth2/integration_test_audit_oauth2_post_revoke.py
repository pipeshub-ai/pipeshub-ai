"""Strict OpenAPI audit of POST /api/v1/oauth2/revoke."""

from __future__ import annotations

from typing import Any

import pytest
from helper.mcp_oauth import OAuthApp
from oauth2_audit_support import UNKNOWN_CLIENT_ID, OAuth2Client
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth2/revoke"


def _access_token(client: OAuth2Client, app: OAuthApp) -> str:
    resp = client.token(
        grant_type="client_credentials", client_id=app.client_id, client_secret=app.client_secret
    )
    assert resp.status_code == 200, resp.text[:500]
    return resp.json()["access_token"]


def _is_active(client: OAuth2Client, app: OAuthApp, token: str) -> bool:
    resp = client.introspect(token=token, client_id=app.client_id, client_secret=app.client_secret)
    assert resp.status_code == 200, resp.text[:500]
    return resp.json()["active"]


@pytest.mark.parametrize(
    ("form", "hint"),
    [
        pytest.param(False, None, id="json"),
        pytest.param(False, "access_token", id="json_with_hint"),
        pytest.param(True, None, id="form"),
    ],
)
def test_revoke_answers_200_without_a_body_and_revokes(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp, form: bool, hint: str | None
) -> None:
    token = _access_token(oauth2_client, confidential_app)
    body: dict[str, Any] = {
        "token": token,
        "client_id": confidential_app.client_id,
        "client_secret": confidential_app.client_secret,
    }
    if hint:
        body["token_type_hint"] = hint

    resp = oauth2_client.revoke(form=form, **body)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b""
    assert not _is_active(oauth2_client, confidential_app, token)


def test_unknown_token_is_still_200(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    resp = oauth2_client.revoke(
        token="spec-audit-no-such-token",
        client_id=confidential_app.client_id,
        client_secret=confidential_app.client_secret,
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.content == b""


def test_without_client_secret_answers_200_and_revokes_nothing(
    oauth2_client: OAuth2Client, confidential_app: OAuthApp
) -> None:
    token = _access_token(oauth2_client, confidential_app)

    resp = oauth2_client.revoke(token=token, client_id=confidential_app.client_id)

    # The missing secret throws inside client authentication and the catch-all answers 200.
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _is_active(oauth2_client, confidential_app, token)


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
    resp = oauth2_client.revoke(
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
    resp = oauth2_client.revoke(**body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in error["metadata"]["errors"]] == [field]
