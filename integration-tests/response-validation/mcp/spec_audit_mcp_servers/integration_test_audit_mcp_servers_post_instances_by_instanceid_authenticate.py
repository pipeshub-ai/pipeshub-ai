"""Strict OpenAPI audit of POST /api/v1/mcp-servers/instances/:instanceId/authenticate."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/authenticate"

# Never sent anywhere: authenticate only stores the credential, it does not dial the server.
API_TOKEN_BODY: JsonObject = {"apiToken": "spec-audit-not-a-real-token"}


def test_authenticate_stores_api_token(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]

    resp = mcp_servers_client.post(f"/instances/{instance_id}/authenticate", json=API_TOKEN_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        # Every field is optional in the pydantic model; the handler refuses the missing token.
        pytest.param({}, 400, id="missing-api-token"),
        pytest.param({"apiToken": 12345}, 422, id="api-token-not-a-string"),
    ],
)
def test_bad_credential_body_is_rejected(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    body: JsonObject,
    expected_status: int,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]

    resp = mcp_servers_client.post(f"/instances/{instance_id}/authenticate", json=body)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(
        f"/instances/{MISSING_INSTANCE_ID}/authenticate", json=API_TOKEN_BODY
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(
        f"/instances/{MISSING_INSTANCE_ID}/authenticate", json=API_TOKEN_BODY, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
