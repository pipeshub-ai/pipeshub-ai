"""Strict OpenAPI audit of POST /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/reauthenticate."""

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
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/reauthenticate"


def _path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/reauthenticate")


def _agent_is_authenticated(client: McpServersClient, agent_key: str, instance_id: str) -> bool:
    listed = client.get(agent_path(agent_key), params={"includeTools": "false"})
    assert listed.status_code == 200, listed.text[:500]
    return next(i for i in listed.json()["instances"] if i["_id"] == instance_id)["isAuthenticated"]


def test_stored_agent_credential_is_cleared(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    agent_key = seed_agent()
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    stored = mcp_servers_client.post(agent_path(agent_key, instance_id, "/authenticate"), json={"apiToken": "spec-audit"})
    assert stored.status_code == 200, stored.text[:500]
    assert _agent_is_authenticated(mcp_servers_client, agent_key, instance_id) is True

    resp = mcp_servers_client.post(_path(agent_key, instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True}
    assert _agent_is_authenticated(mcp_servers_client, agent_key, instance_id) is False


def test_nothing_stored_still_succeeds(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    resp = mcp_servers_client.post(_path(seed_agent(), seed_mcp_instance()["_id"]))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True}


def test_a_body_is_ignored(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    with outside_request_contract("the route takes no body; one is sent to show it is ignored"):
        resp = mcp_servers_client.post(_path(seed_agent(), seed_mcp_instance()["_id"]), json={"force": True})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_agent_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance()["_id"]

    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, instance_id))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_cannot_edit_the_agent_is_forbidden(
    seed_agent: SeedAgent, seed_mcp_instance: SeedMcpInstance, second_user: SecondUser
) -> None:
    resp = request_as(second_user, "POST", _path(seed_agent(), seed_mcp_instance()["_id"]))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent()

    resp = mcp_servers_client.post(_path(agent_key, MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_agent_key_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(UNSAFE_PATH_ID, MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_agent_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
