"""Every /api/v1/oauth-clients route answers 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the token check.
"""

from __future__ import annotations

import pytest
from oauth_clients_audit_support import (
    ACTIVATE_ROUTE,
    APP_ROUTE,
    JSON_HEADERS,
    LIST_ROUTE,
    MALFORMED_JSON_BODY,
    MISSING_APP_ID,
    REGENERATE_SECRET_ROUTE,
    REVOKE_ALL_TOKENS_ROUTE,
    SCOPES_ROUTE,
    SUSPEND_ROUTE,
    TOKEN_IDENTITY_ROUTE,
    TOKENS_ROUTE,
    OAuthClientsAuditClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "", LIST_ROUTE),
        ("POST", "", LIST_ROUTE),
        ("GET", "/scopes", SCOPES_ROUTE),
        ("GET", f"/{MISSING_APP_ID}", APP_ROUTE),
        ("PUT", f"/{MISSING_APP_ID}", APP_ROUTE),
        ("DELETE", f"/{MISSING_APP_ID}", APP_ROUTE),
        ("POST", f"/{MISSING_APP_ID}/regenerate-secret", REGENERATE_SECRET_ROUTE),
        ("POST", f"/{MISSING_APP_ID}/suspend", SUSPEND_ROUTE),
        ("POST", f"/{MISSING_APP_ID}/activate", ACTIVATE_ROUTE),
        ("GET", f"/{MISSING_APP_ID}/tokens", TOKENS_ROUTE),
        ("POST", f"/{MISSING_APP_ID}/revoke-all-tokens", REVOKE_ALL_TOKENS_ROUTE),
        ("PUT", f"/{MISSING_APP_ID}/token-identity", TOKEN_IDENTITY_ROUTE),
    ],
    ids=[
        "list",
        "create",
        "scopes",
        "get",
        "update",
        "delete",
        "regenerate_secret",
        "suspend",
        "activate",
        "tokens",
        "revoke_all_tokens",
        "token_identity",
    ],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    oauth_clients_client: OAuthClientsAuditClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = oauth_clients_client._client.request(
        method,
        f"{oauth_clients_client.BASE}{sub_path}",
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
