"""/api/v1/service-tokens routes answer 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the token check.
"""

from __future__ import annotations

import pytest
from service_tokens_audit_support import (
    MISSING_SERVICE_ACCOUNT_ID,
    MISSING_TOKEN_ID,
    SERVICE_TOKENS_BASE,
    ServiceTokensClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}
ACCOUNT_QUERY = f"?serviceAccountId={MISSING_SERVICE_ACCOUNT_ID}"


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", ACCOUNT_QUERY, ""),
        ("POST", "", ""),
        ("GET", "/scopes", "/scopes"),
        ("DELETE", f"/{MISSING_TOKEN_ID}{ACCOUNT_QUERY}", "/:tokenId"),
    ],
    ids=["list", "create", "scopes", "revoke"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    service_tokens_client: ServiceTokensClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = service_tokens_client._client.request(
        method,
        f"{SERVICE_TOKENS_BASE}{sub_path}",
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{SERVICE_TOKENS_BASE}{route}")
