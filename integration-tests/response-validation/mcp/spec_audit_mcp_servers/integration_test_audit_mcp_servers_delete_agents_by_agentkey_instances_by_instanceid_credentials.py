"""Strict OpenAPI audit of DELETE /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials"


def test_admin_removes_service_account_agent_credentials(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    path = agent_path(seed_agent(), instance_id, "/credentials")
    stored = mcp_servers_client.put(path, json={"apiToken": "spec-audit-token"})
    assert stored.status_code == 200, stored.text[:500]

    resp = mcp_servers_client.delete(path)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True}


def test_unknown_agent_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance()["_id"]
    resp = mcp_servers_client.delete(agent_path(MISSING_AGENT_KEY, instance_id, "/credentials"))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_regular_agent_is_rejected(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    # Per-agent credentials exist only for service-account agents; checked before the instance.
    instance_id = seed_mcp_instance()["_id"]
    agent_key = seed_agent(is_service_account=False)
    resp = mcp_servers_client.delete(agent_path(agent_key, instance_id, "/credentials"))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    resp = mcp_servers_client.delete(
        agent_path(seed_agent(), MISSING_INSTANCE_ID, "/credentials")
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.delete(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/credentials"), auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
