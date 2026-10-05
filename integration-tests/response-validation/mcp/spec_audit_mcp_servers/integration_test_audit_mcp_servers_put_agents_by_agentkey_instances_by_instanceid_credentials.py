"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials."""

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
HEADER_CREDENTIAL = {"headerName": "X-Spec-Audit", "headerValue": "spec-audit-value"}


def _credentials(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/credentials")


def test_put_agent_credentials_stores_a_header_credential(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    # Nothing is dialled: the record is only written to the config store.
    instance_id = seed_mcp_instance(authMode="headers")["_id"]
    path = _credentials(seed_agent(), instance_id)
    try:
        resp = mcp_servers_client.put(path, json=HEADER_CREDENTIAL)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        assert resp.json() == {"success": True, "isAuthenticated": True}
    finally:
        mcp_servers_client.delete(path)


def test_put_agent_credentials_for_a_regular_agent_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    # Per-agent credentials exist only for service-account agents.
    instance_id = seed_mcp_instance(authMode="headers")["_id"]
    agent_key = seed_agent(is_service_account=False)
    resp = mcp_servers_client.put(_credentials(agent_key, instance_id), json=HEADER_CREDENTIAL)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_put_agent_credentials_for_an_unknown_agent_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=HEADER_CREDENTIAL
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_put_agent_credentials_with_a_non_string_token_is_unprocessable(
    mcp_servers_client: McpServersClient,
) -> None:
    # Pydantic rejects the body before the agent or the instance is looked up.
    resp = mcp_servers_client.put(
        _credentials(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json={"apiToken": {"value": "x"}}
    )
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_put_agent_credentials_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, json=HEADER_CREDENTIAL
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
