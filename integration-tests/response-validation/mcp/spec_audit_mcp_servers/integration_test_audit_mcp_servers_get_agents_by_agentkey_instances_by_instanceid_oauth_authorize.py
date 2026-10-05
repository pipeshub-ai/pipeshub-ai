"""Strict OpenAPI audit of GET /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/oauth/authorize.

The success case only builds the authorization URL: the instance url is unreachable, so
discovery finds nothing and the stored endpoints plus a static client are used.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from mcp_servers_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
    oauth_instance_body,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/oauth/authorize"
CLIENT_ID = "spec-audit-client-id"


def _authorize_path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/oauth/authorize")


def test_service_account_agent_gets_authorization_url(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    agent_key = seed_agent()
    configured = mcp_servers_client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": CLIENT_ID, "clientSecret": "spec-audit-client-secret"},
    )
    assert configured.status_code == 200, configured.text[:500]

    resp = mcp_servers_client.get(_authorize_path(agent_key, instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"authorizationUrl"}
    url = urlparse(body["authorizationUrl"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://auth.invalid/authorize"
    query = parse_qs(url.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query.get("state"), "the authorization URL must carry the state the callback looks up"


def test_oauth_instance_without_client_config_is_conflict(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    # No static client, and nothing to discover at the unreachable url, so no DCR either.
    resp = mcp_servers_client.get(_authorize_path(seed_agent(), instance_id))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_regular_agent_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent(is_service_account=False)

    # The service-account check runs before the instance lookup, so the unknown id is never read.
    resp = mcp_servers_client.get(_authorize_path(agent_key, MISSING_INSTANCE_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_agent_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(
        _authorize_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
