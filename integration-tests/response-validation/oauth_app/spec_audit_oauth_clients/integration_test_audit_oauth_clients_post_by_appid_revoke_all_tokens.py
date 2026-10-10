"""Strict OpenAPI audit of POST /api/v1/oauth-clients/:appId/revoke-all-tokens.

401, 403, a malformed id (400) and an unknown or foreign app (404) are in
integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import (
    REVOKE_ALL_TOKENS_ROUTE,
    OAuthClientsAuditClient,
    SeedOAuthApp,
    mint_client_credentials_token,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = REVOKE_ALL_TOKENS_ROUTE


def test_revoke_all_empties_the_token_list_and_reports_no_count(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app(allowedScopes=["org:read"])
    base_url = oauth_clients_client._client.base_url
    mint_client_credentials_token(base_url, app, "org:read")
    mint_client_credentials_token(base_url, app, "org:read")
    assert len(oauth_clients_client.list_tokens(app["id"]).json()["tokens"]) == 2

    resp = oauth_clients_client.revoke_all_tokens(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"message": "All tokens revoked successfully"}
    assert_strict_openapi_exchange(resp, ROUTE)
    assert oauth_clients_client.list_tokens(app["id"]).json() == {"tokens": []}
