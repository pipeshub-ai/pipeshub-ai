"""Every /api/v1/mcp-servers route answers 500 to a malformed JSON body.

The JSON body parser runs before the router, so the failure comes ahead of the
path-id guard and the token check, and is reported as an internal error.
"""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    McpServersClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

_ID = MISSING_INSTANCE_ID
_AGENT = f"/agents/{MISSING_AGENT_KEY}"

OPERATIONS = [
    ("GET", "/catalog", "/catalog"),
    ("GET", "/catalog/github", "/catalog/:typeId"),
    ("GET", "/my-mcp-servers", "/my-mcp-servers"),
    ("GET", "/oauth/callback", "/oauth/callback"),
    ("POST", "/oauth/discover", "/oauth/discover"),
    ("GET", "/instances", "/instances"),
    ("POST", "/instances", "/instances"),
    ("GET", f"/instances/{_ID}", "/instances/:instanceId"),
    ("PUT", f"/instances/{_ID}", "/instances/:instanceId"),
    ("DELETE", f"/instances/{_ID}", "/instances/:instanceId"),
    ("GET", f"/instances/{_ID}/tools", "/instances/:instanceId/tools"),
    ("POST", f"/instances/{_ID}/authenticate", "/instances/:instanceId/authenticate"),
    ("PUT", f"/instances/{_ID}/credentials", "/instances/:instanceId/credentials"),
    ("DELETE", f"/instances/{_ID}/credentials", "/instances/:instanceId/credentials"),
    ("POST", f"/instances/{_ID}/auto-authenticate", "/instances/:instanceId/auto-authenticate"),
    ("POST", f"/instances/{_ID}/reauthenticate", "/instances/:instanceId/reauthenticate"),
    ("GET", f"/instances/{_ID}/oauth/authorize", "/instances/:instanceId/oauth/authorize"),
    ("POST", f"/instances/{_ID}/oauth/refresh", "/instances/:instanceId/oauth/refresh"),
    ("GET", f"/instances/{_ID}/oauth-config", "/instances/:instanceId/oauth-config"),
    ("PUT", f"/instances/{_ID}/oauth-config", "/instances/:instanceId/oauth-config"),
    ("GET", _AGENT, "/agents/:agentKey"),
    ("POST", f"{_AGENT}/instances/{_ID}/authenticate", "/agents/:agentKey/instances/:instanceId/authenticate"),
    ("PUT", f"{_AGENT}/instances/{_ID}/credentials", "/agents/:agentKey/instances/:instanceId/credentials"),
    ("DELETE", f"{_AGENT}/instances/{_ID}/credentials", "/agents/:agentKey/instances/:instanceId/credentials"),
    ("POST", f"{_AGENT}/instances/{_ID}/reauthenticate", "/agents/:agentKey/instances/:instanceId/reauthenticate"),
    (
        "GET",
        f"{_AGENT}/instances/{_ID}/oauth/authorize",
        "/agents/:agentKey/instances/:instanceId/oauth/authorize",
    ),
]


@pytest.mark.parametrize(
    ("method", "path", "route"),
    OPERATIONS,
    ids=[f"{method} {route}" for method, _, route in OPERATIONS],
)
def test_malformed_json_body_is_an_internal_error_before_auth(
    mcp_servers_client: McpServersClient, method: str, path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    send = getattr(mcp_servers_client, method.lower())
    resp = send(path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, f"/api/v1/mcp-servers{route}")
