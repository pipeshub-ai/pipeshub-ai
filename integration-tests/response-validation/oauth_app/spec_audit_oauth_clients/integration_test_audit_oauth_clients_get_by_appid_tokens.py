"""Strict OpenAPI audit of GET /api/v1/oauth-clients/:appId/tokens.

401, 403, a malformed id (400) and an unknown or foreign app (404) are in
integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import (
    TOKENS_ROUTE,
    OAuthClientsAuditClient,
    SeedOAuthApp,
    mint_client_credentials_token,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = TOKENS_ROUTE


def test_tokens_lists_a_client_credentials_token_without_a_user(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app(allowedScopes=["org:read"])
    mint_client_credentials_token(oauth_clients_client._client.base_url, app, "org:read")

    resp = oauth_clients_client.list_tokens(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    tokens = resp.json()["tokens"]
    assert len(tokens) == 1
    token = tokens[0]
    assert token["tokenType"] == "access"
    assert token["scopes"] == ["org:read"]
    assert token["isRevoked"] is False
    assert "userId" not in token
    assert_strict_openapi_exchange(resp, ROUTE)


def test_tokens_of_a_fresh_app_is_empty(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app()

    resp = oauth_clients_client.list_tokens(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"tokens": []}
    assert_strict_openapi_exchange(resp, ROUTE)
