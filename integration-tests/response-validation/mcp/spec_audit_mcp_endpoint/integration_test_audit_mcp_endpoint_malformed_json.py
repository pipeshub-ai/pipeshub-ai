"""Both /mcp operations answer 500 to a malformed JSON body.

The JSON body parser runs before the router, so the failure comes ahead of the token
check and is reported as an internal error.
"""

from __future__ import annotations

import pytest
from mcp_endpoint_audit_support import JSON_MEDIA_TYPE, McpEndpointClient
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/mcp"


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_malformed_json_body_is_an_internal_error_before_auth(
    mcp_endpoint_client: McpEndpointClient, method: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    send = getattr(mcp_endpoint_client, method.lower())
    resp = send("", auth=False, data="{not json", headers={"Content-Type": JSON_MEDIA_TYPE})
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
