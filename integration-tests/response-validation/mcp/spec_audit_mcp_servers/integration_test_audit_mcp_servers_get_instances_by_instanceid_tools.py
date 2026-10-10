"""Strict OpenAPI audit of GET /api/v1/mcp-servers/instances/:instanceId/tools."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    FIXTURE_TOOL,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedMcpInstance,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/tools"


def _tools(instance_id: str) -> str:
    return f"/instances/{instance_id}/tools"


def test_tools_of_a_reachable_server_are_listed(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    mcp_fixture_url: str,
) -> None:
    instance = seed_mcp_instance(url=mcp_fixture_url)

    resp = mcp_servers_client.get(_tools(instance["_id"]), timeout=120)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    tools = resp.json()["tools"]
    assert [t["name"] for t in tools] == [FIXTURE_TOOL]
    assert tools[0]["namespacedName"].endswith(FIXTURE_TOOL)
    assert tools[0]["inputSchema"]["required"] == ["order_id"]


def test_member_lists_tools_with_their_own_access(
    second_user: SecondUser,
    seed_mcp_instance: SeedMcpInstance,
    mcp_fixture_url: str,
) -> None:
    # Not admin-gated: a server with no auth is usable by every member.
    instance = seed_mcp_instance(url=mcp_fixture_url)
    resp = request_as(second_user, "GET", _tools(instance["_id"]), timeout=120)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [t["name"] for t in resp.json()["tools"]] == [FIXTURE_TOOL]


def test_unreachable_server_is_an_internal_error(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    # Python answers 502; the gateway turns every upstream 5xx into a 500.
    instance = seed_mcp_instance()
    resp = mcp_servers_client.get(_tools(instance["_id"]), timeout=120)
    assert resp.status_code == 500, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_stored_credentials_is_a_conflict(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    mcp_fixture_url: str,
) -> None:
    instance = seed_mcp_instance(url=mcp_fixture_url, authMode="api_token")
    resp = mcp_servers_client.get(_tools(instance["_id"]))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_tools(MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_tools(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_mcp_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(_tools(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_tools(MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
