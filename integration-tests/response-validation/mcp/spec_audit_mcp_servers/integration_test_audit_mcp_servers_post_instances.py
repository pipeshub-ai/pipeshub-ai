"""Strict OpenAPI audit of POST /api/v1/mcp-servers/instances."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_TYPE_ID,
    UNREACHABLE_MCP_URL,
    JsonObject,
    McpServersClient,
    instance_body,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances"


def test_admin_creates_custom_instance(mcp_servers_client: McpServersClient) -> None:
    body = instance_body()

    resp = mcp_servers_client.create_instance(body)
    try:
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
        record: JsonObject = resp.json()
        assert record["_id"]
        assert record["name"] == body["name"]
        assert record["transport"] == "streamable_http"
        assert record["authMode"] == "none"
        assert record["url"] == UNREACHABLE_MCP_URL
        assert record["typeId"] is None
    finally:
        if resp.status_code == 201:
            mcp_servers_client.delete_instance(resp.json()["_id"])


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        # Not an MCPTransport member: pydantic refuses the body before the handler runs.
        pytest.param({"transport": "carrier_pigeon"}, 422, id="unknown-transport"),
        pytest.param({"typeId": MISSING_TYPE_ID}, 400, id="unknown-catalog-type"),
    ],
)
def test_create_with_invalid_body_is_rejected(
    mcp_servers_client: McpServersClient,
    overrides: JsonObject,
    expected_status: int,
) -> None:
    resp = mcp_servers_client.create_instance(instance_body(**overrides))
    try:
        assert resp.status_code == expected_status, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        if resp.status_code == 201:
            mcp_servers_client.delete_instance(resp.json()["_id"])


def test_member_cannot_create_instance(
    mcp_servers_client: McpServersClient, second_user: SecondUser
) -> None:
    resp = request_as(second_user, "POST", "/instances", json=instance_body())
    try:
        assert resp.status_code == 403, resp.text[:500]
        assert_strict_openapi_response(resp, ROUTE)
    finally:
        if resp.status_code == 201:
            mcp_servers_client.delete_instance(resp.json()["_id"])


def test_create_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.create_instance(instance_body(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
