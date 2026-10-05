"""Strict OpenAPI audit of POST /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/reauthenticate."""

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

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/reauthenticate"


def _path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/reauthenticate")


def test_service_account_agent_credentials_are_cleared(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    agent_key = seed_agent()
    instance_id = seed_mcp_instance()["_id"]

    # Nothing is stored for the agent yet; the handler deletes unconditionally and still succeeds.
    resp = mcp_servers_client.post(_path(agent_key, instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True}


def test_unknown_agent_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance()["_id"]

    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, instance_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_regular_agent_is_refused(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    agent_key = seed_agent(is_service_account=False)
    instance_id = seed_mcp_instance()["_id"]

    # Per-agent credentials exist only for service-account agents.
    resp = mcp_servers_client.post(_path(agent_key, instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent()

    resp = mcp_servers_client.post(_path(agent_key, MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
