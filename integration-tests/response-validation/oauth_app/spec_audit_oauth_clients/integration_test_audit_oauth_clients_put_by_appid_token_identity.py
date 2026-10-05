"""Strict OpenAPI audit of PUT /api/v1/oauth-clients/:appId/token-identity."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from oauth_clients_audit_support import (
    MALFORMED_SERVICE_ACCOUNT_ID,
    MISSING_APP_ID,
    MISSING_SERVICE_ACCOUNT_ID,
    OAuthClientsAuditClient,
    SeedOAuthApp,
    SeedServiceAccount,
    request_as,
    token_identity_body,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth-clients/:appId/token-identity"


def test_set_token_identity_to_service_account_then_back_to_creator(
    oauth_clients_client: OAuthClientsAuditClient,
    seed_oauth_app: SeedOAuthApp,
    seed_service_account: SeedServiceAccount,
) -> None:
    # Only a seeded app: a success revokes every token the app has issued.
    app = seed_oauth_app()
    account_id = seed_service_account()["id"]

    resp = oauth_clients_client.set_token_identity(app["id"], json=token_identity_body(account_id))

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["message"] == "Application tokens now act as the service account"
    assert body["app"]["id"] == app["id"]
    assert body["app"]["clientId"] == app["clientId"]
    assert_strict_openapi_response(resp, ROUTE)

    resp = oauth_clients_client.set_token_identity(app["id"], json=token_identity_body(None))

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["message"] == "Application tokens now act as its creator"
    assert body["app"]["id"] == app["id"]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_token_identity_without_token_is_unauthorized(
    oauth_clients_client: OAuthClientsAuditClient,
) -> None:
    resp = oauth_clients_client.set_token_identity(
        MISSING_APP_ID, auth=False, json=token_identity_body(None)
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_set_token_identity_as_non_admin_is_forbidden(second_user: SecondUser) -> None:
    # The admin gate runs before validation and lookup, so the id need not exist.
    resp = request_as(
        second_user, "PUT", f"/{MISSING_APP_ID}/token-identity", json=token_identity_body(None)
    )

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("service_account_id", "expected_status"),
    [
        pytest.param(MALFORMED_SERVICE_ACCOUNT_ID, 400, id="malformed-service-account-id"),
        pytest.param(MISSING_SERVICE_ACCOUNT_ID, 404, id="missing-service-account"),
    ],
)
def test_set_token_identity_rejects_unusable_service_account(
    oauth_clients_client: OAuthClientsAuditClient,
    seed_oauth_app: SeedOAuthApp,
    service_account_id: str,
    expected_status: int,
) -> None:
    app = seed_oauth_app()

    resp = oauth_clients_client.set_token_identity(
        app["id"], json=token_identity_body(service_account_id)
    )

    assert resp.status_code == expected_status, resp.text[:500]
    if expected_status == 404:
        assert resp.json()["error"]["message"] == "Service account not found"
    assert_strict_openapi_response(resp, ROUTE)
