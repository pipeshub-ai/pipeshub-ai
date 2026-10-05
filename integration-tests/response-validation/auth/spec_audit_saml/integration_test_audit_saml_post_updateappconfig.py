"""Strict OpenAPI audit of POST /api/v1/saml/updateAppConfig."""

from __future__ import annotations

import pytest
from saml_audit_support import (
    FETCH_CONFIG_SCOPE,
    OTHER_SCOPE,
    WRONG_SECRET,
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


@pytest.fixture(scope="module")
def deployment_secret(
    saml_client: SamlClient, pipeshub_client: PipeshubClient, scoped_secret: str
) -> str:
    """Skip unless SCOPED_JWT_SECRET is the secret this deployment verifies with.

    A wrong-scope token is refused either way, but a good signature gets
    "Invalid scope" and a bad one "Invalid token" (AuthTokenService.verifyScopedToken).
    """
    probe = saml_client.update_app_config(
        token=mint_scoped_token(
            scoped_secret,
            [OTHER_SCOPE],
            userId=pipeshub_client.acting_user_id,
            orgId=pipeshub_client.org_id,
        )
    )
    assert probe.status_code == 401, probe.text[:500]
    if probe.json()["error"]["message"] != "Invalid scope":
        pytest.skip(
            "SCOPED_JWT_SECRET is not the scoped JWT secret this deployment verifies "
            "with (it answers 'Invalid token' to a token signed with it), so a "
            "fetch:config service token cannot be minted"
        )
    return scoped_secret


def test_fetch_config_token_reloads_auth_config(
    saml_client: SamlClient, pipeshub_client: PipeshubClient, deployment_secret: str
) -> None:
    # Only re-reads AppConfig from the config store and rebinds it; nothing is changed.
    token = mint_scoped_token(
        deployment_secret,
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
    assert error["code"] == "HTTP_UNAUTHORIZED"
    assert error["message"] == "No token provided"


def test_admin_session_token_is_unauthorized(saml_client: SamlClient) -> None:
    # The route verifies against the scoped-token key only, so even an admin's
    # session token is refused with 401 rather than 403.
    resp = saml_client.update_app_config()
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_UNAUTHORIZED"


def test_token_signed_with_another_secret_is_unauthorized(
    saml_client: SamlClient, pipeshub_client: PipeshubClient
) -> None:
    token = mint_scoped_token(
        WRONG_SECRET,
        [FETCH_CONFIG_SCOPE],
        userId=pipeshub_client.acting_user_id,
        orgId=pipeshub_client.org_id,
    )

    resp = saml_client.update_app_config(token=token)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_UNAUTHORIZED"
    assert error["message"] == "Invalid token"


def test_token_with_another_scope_is_unauthorized(
    saml_client: SamlClient, pipeshub_client: PipeshubClient, deployment_secret: str
) -> None:
    token = mint_scoped_token(
        deployment_secret,
        [OTHER_SCOPE],
        userId=pipeshub_client.acting_user_id,
        orgId=pipeshub_client.org_id,
    )

    resp = saml_client.update_app_config(token=token)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_UNAUTHORIZED"
    assert error["message"] == "Invalid scope"
