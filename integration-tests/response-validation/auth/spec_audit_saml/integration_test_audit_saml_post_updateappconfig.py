"""Strict OpenAPI audit of POST /api/v1/saml/updateAppConfig."""

from __future__ import annotations

import pytest
from saml_audit_support import (
    FETCH_CONFIG_SCOPE,
    OTHER_SCOPE,
    SamlClient,
    mint_scoped_token,
    scoped_jwt_secret,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/saml/updateAppConfig"


@pytest.fixture(scope="module")
def scoped_secret() -> str:
    secret = scoped_jwt_secret()
    if not secret:
        pytest.skip("SCOPED_JWT_SECRET is not set; cannot mint a service token")
    return secret


def test_fetch_config_token_reloads_auth_config(
    saml_client: SamlClient, pipeshub_client: PipeshubClient, scoped_secret: str
) -> None:
    token = mint_scoped_token(
        scoped_secret,
        [FETCH_CONFIG_SCOPE],
        userId=pipeshub_client.acting_user_id,
        orgId=pipeshub_client.org_id,
    )

    resp = saml_client.update_app_config(token=token)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Auth configuration updated successfully"}


def test_no_token_is_unauthorized(saml_client: SamlClient) -> None:
    resp = saml_client.update_app_config(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "UNAUTHORIZED"
    assert error["message"] == "No token provided"


def test_admin_session_token_is_unauthorized(saml_client: SamlClient) -> None:
    # The route verifies against the scoped-token key only, so even an admin's
    # session token is refused with 401 rather than 403.
    resp = saml_client.update_app_config()
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    ("scopes", "ttl_seconds", "message"),
    [
        pytest.param([OTHER_SCOPE], 3600, "Invalid scope", id="wrong_scope"),
        pytest.param([FETCH_CONFIG_SCOPE], -60, "Invalid token", id="expired"),
    ],
)
def test_unusable_scoped_token_is_unauthorized(
    saml_client: SamlClient,
    pipeshub_client: PipeshubClient,
    scoped_secret: str,
    scopes: list[str],
    ttl_seconds: int,
    message: str,
) -> None:
    token = mint_scoped_token(
        scoped_secret,
        scopes,
        ttl_seconds=ttl_seconds,
        userId=pipeshub_client.acting_user_id,
        orgId=pipeshub_client.org_id,
    )

    resp = saml_client.update_app_config(token=token)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "UNAUTHORIZED"
    assert error["message"] == message
