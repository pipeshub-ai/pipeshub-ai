"""Strict OpenAPI audit of POST /api/v1/mcp-servers/oauth/discover.

No success case: a 200 means Python dialled the probed host, and every host this
stack can reach without leaving the test network is refused by the SSRF guard.
"""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    UNREACHABLE_MCP_URL,
    JsonObject,
    McpServersClient,
    request_as,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/oauth/discover"
PATH = "/oauth/discover"


def test_discover_without_a_token_is_401(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(PATH, auth=False, json={"url": UNREACHABLE_MCP_URL})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_discover_as_a_member_is_403(second_user: SecondUser) -> None:
    # The admin check runs before the SSRF guard, so the loopback url is never judged.
    resp = request_as(second_user, "POST", PATH, json={"url": UNREACHABLE_MCP_URL})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "administrators" in resp.text, resp.text[:500]


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        pytest.param({}, 422, id="missing-url"),
        pytest.param({"url": UNREACHABLE_MCP_URL}, 400, id="loopback-url"),
        pytest.param({"url": "ftp://mcp.example.com/mcp"}, 400, id="non-http-scheme"),
    ],
)
def test_discover_as_admin_refuses_a_bad_target(
    mcp_servers_client: McpServersClient, body: JsonObject, expected_status: int
) -> None:
    resp = mcp_servers_client.post(PATH, json=body)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
