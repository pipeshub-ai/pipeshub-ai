"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/instances/:instanceId."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    McpServersClient,
    SeedMcpInstance,
    instance_body,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId"


def test_update_replaces_config_and_keeps_identity(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()
    body = instance_body(
        authMode="api_token",
        headerName="X-Api-Key",
        description="Updated by the MCP servers spec audit",
    )

    resp = mcp_servers_client.update_instance(seeded["_id"], body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    record = resp.json()
    assert record["_id"] == seeded["_id"]
    assert record["createdAt"] == seeded["createdAt"]
    assert record["createdBy"] == seeded["createdBy"]
    assert record["updatedAt"] >= seeded["updatedAt"]
    assert record["name"] == body["name"]
    assert record["authMode"] == "api_token"
    assert record["headerName"] == "X-Api-Key"
    assert record["description"] == body["description"]
    assert record["isCustom"] is True


def test_update_body_missing_required_field_is_unprocessable(
    mcp_servers_client: McpServersClient,
) -> None:
    body = instance_body()
    del body["transport"]

    # Pydantic rejects the body before the handler looks the instance up.
    resp = mcp_servers_client.update_instance(MISSING_INSTANCE_ID, body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_custom_http_server_without_url_is_rejected(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()

    resp = mcp_servers_client.update_instance(seeded["_id"], instance_body(url=None))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    stored = mcp_servers_client.get_instance(seeded["_id"])
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["url"] == seeded["url"]


def test_update_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.update_instance(MISSING_INSTANCE_ID, instance_body())
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.update_instance(
        MISSING_INSTANCE_ID, instance_body(), auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
