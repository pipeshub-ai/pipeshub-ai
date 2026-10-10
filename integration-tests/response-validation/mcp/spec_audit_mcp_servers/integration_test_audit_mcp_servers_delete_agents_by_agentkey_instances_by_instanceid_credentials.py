"""Strict OpenAPI audit of DELETE /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials"


def _agent_is_authenticated(client: McpServersClient, agent_key: str, instance_id: str) -> bool:
    listed = client.get(agent_path(agent_key), params={"includeTools": "false"})
    assert listed.status_code == 200, listed.text[:500]
    return next(i for i in listed.json()["instances"] if i["_id"] == instance_id)["isAuthenticated"]


def test_admin_removes_service_account_agent_credentials(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    agent_key = seed_agent()
    path = agent_path(agent_key, instance_id, "/credentials")
    stored = mcp_servers_client.put(path, json={"apiToken": "spec-audit-token"})
    assert stored.status_code == 200, stored.text[:500]
    assert _agent_is_authenticated(mcp_servers_client, agent_key, instance_id) is True

    resp = mcp_servers_client.delete(path)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True}
    assert _agent_is_authenticated(mcp_servers_client, agent_key, instance_id) is False


def test_nothing_stored_still_succeeds(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = mcp_servers_client.delete(agent_path(seed_agent(), instance_id, "/credentials"))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_agent_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance()["_id"]
    resp = mcp_servers_client.delete(agent_path(MISSING_AGENT_KEY, instance_id, "/credentials"))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_cannot_edit_the_agent_is_forbidden(
    seed_mcp_instance: SeedMcpInstance, seed_agent: SeedAgent, second_user: SecondUser
) -> None:
    instance_id = seed_mcp_instance()["_id"]
    resp = request_as(second_user, "DELETE", agent_path(seed_agent(), instance_id, "/credentials"))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    resp = mcp_servers_client.delete(
        agent_path(seed_agent(), MISSING_INSTANCE_ID, "/credentials")
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.delete(agent_path(MISSING_AGENT_KEY, UNSAFE_PATH_ID, "/credentials"), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_agent_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.delete(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/credentials"), auth=False, headers=narrow_scope_headers
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.delete(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/credentials"), auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
