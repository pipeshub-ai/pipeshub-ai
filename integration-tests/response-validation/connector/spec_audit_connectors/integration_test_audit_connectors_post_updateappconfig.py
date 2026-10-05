"""Strict OpenAPI audit of POST /api/v1/connectors/updateAppConfig.

The route takes a service token (scope fetch:config) signed with
SCOPED_JWT_SECRET, not a user session token.
"""

from __future__ import annotations

import os

import pytest
from app.utils.jwt import mint_service_token
from connectors_audit_support import ConnectorsAuditClient
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/updateAppConfig"
PATH = "/updateAppConfig"
FETCH_CONFIG_SCOPE = "fetch:config"
OTHER_SERVICE_SCOPE = "storage:token"


def _service_headers(org_id: str, scope: str) -> dict[str, str]:
    secret = os.getenv("SCOPED_JWT_SECRET", "").strip()
    if not secret:
        pytest.skip("SCOPED_JWT_SECRET is not set; cannot mint a service token")
    token = mint_service_token(secret, {"orgId": org_id, "scopes": [scope]})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def deployment_signing_secret(
    connectors_client: ConnectorsAuditClient, pipeshub_client: PipeshubClient
) -> None:
    """Skip unless SCOPED_JWT_SECRET is the secret this deployment verifies with.

    A token with the wrong scope is refused either way, but a good signature gets
    "Invalid scope" and a bad one "Invalid token" (AuthTokenService.verifyScopedToken).
    """
    probe = connectors_client.post(
        PATH,
        auth=False,
        headers=_service_headers(pipeshub_client.org_id, OTHER_SERVICE_SCOPE),
    )
    assert probe.status_code == 401, probe.text[:500]
    if probe.json()["error"]["message"] != "Invalid scope":
        pytest.skip(
            "SCOPED_JWT_SECRET is not the scoped JWT secret this deployment verifies "
            "with (it answers 'Invalid token' to a token signed with it), so a "
            "fetch:config service token cannot be minted"
        )


@pytest.mark.usefixtures("deployment_signing_secret")
def test_fetch_config_service_token_reloads_app_config(
    connectors_client: ConnectorsAuditClient, pipeshub_client: PipeshubClient
) -> None:
    # Only re-reads AppConfig from the config store and rebinds it; nothing is changed.
    resp = connectors_client.post(
        PATH,
        auth=False,
        headers=_service_headers(pipeshub_client.org_id, FETCH_CONFIG_SCOPE),
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Connectors configuration updated successfully"}


def test_without_token_is_unauthorized(connectors_client: ConnectorsAuditClient) -> None:
    resp = connectors_client.post(PATH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_admin_session_token_is_not_a_service_token(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # The user access token is signed with the session secret, so scoped verification fails.
    resp = connectors_client.post(PATH)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_service_token_with_another_scope_is_unauthorized(
    connectors_client: ConnectorsAuditClient, pipeshub_client: PipeshubClient
) -> None:
    resp = connectors_client.post(
        PATH,
        auth=False,
        headers=_service_headers(pipeshub_client.org_id, OTHER_SERVICE_SCOPE),
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
