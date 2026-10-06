"""Strict OpenAPI audit of POST /api/v1/saml/updateAppConfig."""

from __future__ import annotations

import pytest
from helper.pipeshub_client import PipeshubClient
from saml_audit_support import (
    FETCH_CONFIG_SCOPE,
    OTHER_SCOPE,
    WRONG_SECRET,
    SamlClient,
    mint_scoped_token,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/saml/updateAppConfig"
UPDATED = {"message": "Auth configuration updated successfully"}


@pytest.fixture(scope="module")
def deployment_secret(
    saml_client: SamlClient, pipeshub_client: PipeshubClient, scoped_secret: str
) -> str:
    """Fail unless SCOPED_JWT_SECRET is the secret this deployment verifies with.

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
        pytest.fail(
            "SCOPED_JWT_SECRET is not the scoped JWT secret this deployment verifies "
            "with (it answers 'Invalid token' to a token signed with it), so a "
            "fetch:config service token cannot be minted"
        )
    return scoped_secret


@pytest.fixture
def fetch_config_token(pipeshub_client: PipeshubClient, deployment_secret: str) -> str:
    return mint_scoped_token(
        deployment_secret,
        [FETCH_CONFIG_SCOPE],
        userId=pipeshub_client.acting_user_id,
        orgId=pipeshub_client.org_id,
    )


def test_fetch_config_token_reloads_auth_config(
    saml_client: SamlClient, fetch_config_token: str
) -> None:
    # Only re-reads AppConfig from the config store and rebinds it; nothing is changed.
    resp = saml_client.update_app_config(token=fetch_config_token)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED


def test_token_without_user_or_org_claims_is_enough(
    saml_client: SamlClient, deployment_secret: str
) -> None:
    resp = saml_client.update_app_config(
        token=mint_scoped_token(deployment_secret, [FETCH_CONFIG_SCOPE])
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED


def test_body_and_query_string_are_not_read(
    saml_client: SamlClient, fetch_config_token: str
) -> None:
    with outside_request_contract("the handler never reads the body or the query string"):
        resp = saml_client.update_app_config(
            token=fetch_config_token,
            json={"specAuditUnknown": True},
            params={"specAuditUnknown": "1"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED


def test_no_token_is_unauthorized(saml_client: SamlClient) -> None:
    resp = saml_client.update_app_config(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_UNAUTHORIZED"
    assert error["message"] == "No token provided"


def test_admin_session_token_is_unauthorized(saml_client: SamlClient) -> None:
    # The route verifies against the scoped-token key only, so even an admin's
    # token is refused with 401 rather than 403.
    resp = saml_client.update_app_config()
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["code"] == "HTTP_UNAUTHORIZED"


@pytest.mark.parametrize(
    ("secret_is_right", "scopes", "ttl_seconds", "message"),
    [
        pytest.param(False, [FETCH_CONFIG_SCOPE], 3600, "Invalid token", id="another_secret"),
        pytest.param(True, [OTHER_SCOPE], 3600, "Invalid scope", id="another_scope"),
        pytest.param(True, None, 3600, "Invalid scope", id="no_scopes_claim"),
        pytest.param(True, [FETCH_CONFIG_SCOPE], -60, None, id="expired"),
    ],
)
def test_unusable_scoped_token_is_unauthorized(
    saml_client: SamlClient,
    pipeshub_client: PipeshubClient,
    deployment_secret: str,
    secret_is_right: bool,
    scopes: list[str] | None,
    ttl_seconds: int,
    message: str | None,
) -> None:
    token = mint_scoped_token(
        deployment_secret if secret_is_right else WRONG_SECRET,
        scopes,
        ttl_seconds=ttl_seconds,
        userId=pipeshub_client.acting_user_id,
        orgId=pipeshub_client.org_id,
    )

    resp = saml_client.update_app_config(token=token)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == "HTTP_UNAUTHORIZED"
    if message is not None:
        assert error["message"] == message
