"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/instances/:instanceId/credentials."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    McpServersClient,
    SeedMcpInstance,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/credentials"


def _credentials(instance_id: str) -> str:
    return f"/instances/{instance_id}/credentials"


def test_put_credentials_stores_a_header_credential(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    # Nothing is dialled: the record is only written to the config store, and deleting
    # the seeded instance removes it again.
    instance_id = seed_mcp_instance(authMode="headers")["_id"]
    resp = mcp_servers_client.put(
        _credentials(instance_id),
        json={"headerName": "X-Spec-Audit", "headerValue": "spec-audit-value"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


def test_put_credentials_without_the_required_token_is_bad_request(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    # Every body field is optional for pydantic; the per-authMode requirement is a 400.
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = mcp_servers_client.put(_credentials(instance_id), json={})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_put_credentials_with_a_non_string_token_is_unprocessable(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = mcp_servers_client.put(_credentials(instance_id), json={"apiToken": {"value": "x"}})
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_put_credentials_for_an_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(_credentials(MISSING_INSTANCE_ID), json={"apiToken": "spec-audit"})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_put_credentials_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_INSTANCE_ID), auth=False, json={"apiToken": "spec-audit"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
