"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/instances/:instanceId/oauth-config."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/oauth-config"

CLIENT_ID = "spec-audit-client-id"
CLIENT_SECRET = "spec-audit-client-secret"
CLIENT_CONFIG = {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}


def _path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth-config"


def test_update_stores_client_config_and_read_masks_it(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = mcp_servers_client.put(_path(instance_id), json=CLIENT_CONFIG)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True}

    stored = mcp_servers_client.get(_path(instance_id))
    assert stored.status_code == 200, stored.text[:500]
    config = stored.json()
    assert config["configured"] is True
    assert config["clientId"] == f"{CLIENT_ID[:4]}{'•' * 8}{CLIENT_ID[-4:]}"
    assert CLIENT_SECRET not in stored.text


def test_update_without_client_secret_is_unprocessable(
    mcp_servers_client: McpServersClient,
) -> None:
    # Pydantic rejects the body before the admin check and the instance lookup.
    resp = mcp_servers_client.put(
        _path(MISSING_INSTANCE_ID), json={"clientId": CLIENT_ID}
    )
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(_path(MISSING_INSTANCE_ID), json=CLIENT_CONFIG)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    # Nothing may be stored for an instance that does not exist.
    stored = mcp_servers_client.get(_path(MISSING_INSTANCE_ID))
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {"configured": False}


def test_update_as_member_is_forbidden(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = request_as(second_user, "PUT", _path(instance_id), json=CLIENT_CONFIG)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    stored = mcp_servers_client.get(_path(instance_id))
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json() == {"configured": False}


def test_update_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _path(MISSING_INSTANCE_ID), json=CLIENT_CONFIG, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
