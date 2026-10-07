"""/api/v1/personal-access-tokens routes answer 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the token check.
"""

from __future__ import annotations

import pytest
from personal_access_tokens_audit_support import MISSING_TOKEN_ID, PATS_BASE, PatsClient
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    ("method", "sub_path", "route"),
    [
        ("GET", "", ""),
        ("POST", "", ""),
        ("GET", "/scopes", "/scopes"),
        ("DELETE", f"/{MISSING_TOKEN_ID}", "/:tokenId"),
        ("GET", "/admin", "/admin"),
        ("DELETE", f"/admin/{MISSING_TOKEN_ID}", "/admin/:tokenId"),
    ],
    ids=["list", "create", "scopes", "revoke", "admin_list", "admin_revoke"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    pats_client: PatsClient, method: str, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = pats_client._client.request(
        method,
        f"{PATS_BASE}{sub_path}",
        auth=False,
        data=MALFORMED_JSON_BODY,
        headers=JSON_HEADERS,
    )

    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"{PATS_BASE}{route}")
