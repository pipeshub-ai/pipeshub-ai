"""Strict OpenAPI audit of GET /api/v1/mcp-servers/agents/:agentKey."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_AGENT_KEY,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey"
# Tool discovery dials every authenticated instance in the org; keep it off.
NO_TOOLS = {"includeTools": "false"}


def test_owner_lists_agent_mcp_servers(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()
    agent_key = seed_agent()

    resp = mcp_servers_client.get(agent_path(agent_key), params=NO_TOOLS)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    mine = [i for i in resp.json()["instances"] if i["_id"] == seeded["_id"]]
    assert len(mine) == 1, resp.text[:500]
    entry = mine[0]
    assert entry["name"] == seeded["name"]
    assert entry["hasOAuthClientConfig"] is False
    # authMode "none" needs no credential, so it counts as authenticated for any owner.
    assert entry["isAuthenticated"] is True
    assert entry["tools"] == []
    assert entry["toolsError"] is None


def test_unknown_agent_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(agent_path(MISSING_AGENT_KEY), params=NO_TOOLS)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_without_agent_access_gets_not_found(
    second_user: SecondUser,
    seed_agent: SeedAgent,
) -> None:
    # Not admin-gated: the gate is view access to the agent, and no access is reported as 404.
    # A regular agent, because a service-account agent can be force-shared with the whole org.
    agent_key = seed_agent(False)

    resp = request_as(second_user, "GET", agent_path(agent_key), params=NO_TOOLS)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_non_boolean_include_tools_is_unprocessable(
    mcp_servers_client: McpServersClient,
) -> None:
    # FastAPI validates the query before the handler looks the agent up.
    resp = mcp_servers_client.get(
        agent_path(MISSING_AGENT_KEY), params={"includeTools": "not-a-bool"}
    )
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(agent_path(MISSING_AGENT_KEY), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
