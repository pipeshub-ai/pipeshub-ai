"""Strict OpenAPI audit of DELETE /api/v1/oauth-clients/:appId.

401, 403, a malformed id (400) and an unknown or foreign app (404) are in
integration_test_audit_oauth_clients_app_refusals.py.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import (
    APP_ROUTE,
    OAuthClientsAuditClient,
    SeedOAuthApp,
    mint_client_credentials_token,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = APP_ROUTE


def test_delete_soft_deletes_the_app_once(
    oauth_clients_client: OAuthClientsAuditClient, seed_oauth_app: SeedOAuthApp
) -> None:
    app = seed_oauth_app(allowedScopes=["org:read"])
    mint_client_credentials_token(oauth_clients_client._client.base_url, app, "org:read")

    resp = oauth_clients_client.delete_app(app["id"])

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == {"message": "OAuth app deleted successfully"}
    assert_strict_openapi_exchange(resp, ROUTE)

    again = oauth_clients_client.delete_app(app["id"])
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)
