"""Strict OpenAPI audit of POST /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/authenticate."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    JsonObject,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/authenticate"

# Never sent anywhere: authenticate only stores the credential, it does not dial the server.
API_TOKEN_BODY: JsonObject = {"apiToken": "spec-audit-not-a-real-token"}


def test_authenticate_stores_agent_api_token(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    # Deleting the seeded instance on teardown also removes the agent's credential.
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    agent_key = seed_agent()

    resp = mcp_servers_client.post(
        agent_path(agent_key, instance_id, "/authenticate"), json=API_TOKEN_BODY
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


def test_regular_agent_is_rejected(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent(is_service_account=False)

    # The service-account check runs before the instance lookup, so the id need not exist.
    resp = mcp_servers_client.post(
        agent_path(agent_key, MISSING_INSTANCE_ID, "/authenticate"), json=API_TOKEN_BODY
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_agent_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/authenticate"), json=API_TOKEN_BODY
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_api_token_not_a_string_is_unprocessable(mcp_servers_client: McpServersClient) -> None:
    # Pydantic validates the body before the handler looks anything up.
    resp = mcp_servers_client.post(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/authenticate"),
        json={"apiToken": 12345},
    )
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/authenticate"),
        json=API_TOKEN_BODY,
        auth=False,
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
