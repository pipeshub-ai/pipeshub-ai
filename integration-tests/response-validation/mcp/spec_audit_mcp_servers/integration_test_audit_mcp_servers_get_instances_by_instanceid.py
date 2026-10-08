"""Strict OpenAPI audit of GET /api/v1/mcp-servers/instances/:instanceId."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    CATALOG_STDIO_TYPE_ID,
    MISSING_INSTANCE_ID,
    UNREACHABLE_MCP_URL,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId"


def test_admin_reads_seeded_instance(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()

    resp = mcp_servers_client.get_instance(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["_id"] == seeded["_id"]
    assert body["name"] == seeded["name"]
    assert body["url"] == UNREACHABLE_MCP_URL
    assert body["authMode"] == "none"
    # Added by this handler only; the create response does not carry them.
    assert body["hasOAuthClientConfig"] is False
    assert body["disabledReason"] is None
    assert "disabledReason" not in seeded


def test_catalog_stdio_instance_is_not_disabled(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    # Only custom STDIO servers depend on MCP_ALLOW_CUSTOM_STDIO; catalog ones always run.
    seeded = seed_mcp_instance(typeId=CATALOG_STDIO_TYPE_ID, transport="stdio", url=None, authMode="api_token")

    resp = mcp_servers_client.get_instance(seeded["_id"])
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["transport"] == "stdio"
    assert resp.json()["disabledReason"] is None


def test_admin_sees_a_stored_oauth_client_flagged(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    configured = mcp_servers_client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": "spec-audit-client", "clientSecret": "spec-audit-secret"},
    )
    assert configured.status_code == 200, configured.text[:500]

    resp = mcp_servers_client.get_instance(instance_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["hasOAuthClientConfig"] is True
    assert "spec-audit-secret" not in resp.text


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get_instance(MISSING_INSTANCE_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(
    second_user: SecondUser,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance()["_id"]

    resp = request_as(second_user, "GET", f"/instances/{instance_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get_instance(MISSING_INSTANCE_ID, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.get_instance(UNSAFE_PATH_ID, auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
