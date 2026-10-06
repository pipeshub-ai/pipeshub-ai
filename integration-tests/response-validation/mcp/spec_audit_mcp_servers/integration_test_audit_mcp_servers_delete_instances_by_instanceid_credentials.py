"""Strict OpenAPI audit of DELETE /api/v1/mcp-servers/instances/:instanceId/credentials."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/credentials"


def test_admin_removes_own_stored_credentials(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    stored = mcp_servers_client.post(
        f"/instances/{instance_id}/authenticate", json={"apiToken": "spec-audit-token"}
    )
    assert stored.status_code == 200, stored.text[:500]

    resp = mcp_servers_client.delete(f"/instances/{instance_id}/credentials")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True}

    listed = mcp_servers_client.get("/my-mcp-servers", params={"includeTools": "false"})
    entry = next(i for i in listed.json()["instances"] if i["_id"] == instance_id)
    assert entry["isAuthenticated"] is False


@pytest.mark.parametrize(
    "instance",
    [
        pytest.param({"authMode": "api_token"}, id="api-token"),
        pytest.param(oauth_instance_body(), id="oauth"),
        pytest.param({}, id="no-auth"),
    ],
)
def test_removing_nothing_stored_still_succeeds(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    instance: JsonObject,
) -> None:
    instance_id = seed_mcp_instance(**instance)["_id"]
    resp = mcp_servers_client.delete(f"/instances/{instance_id}/credentials")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True}


def test_member_removes_their_own_credentials(
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    stored = request_as(second_user, "POST", f"/instances/{instance_id}/authenticate", json={"apiToken": "x"})
    assert stored.status_code == 200, stored.text[:500]
    resp = request_as(second_user, "DELETE", f"/instances/{instance_id}/credentials")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_remove_shared_admin_credentials(
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    # Only the shared-credential case is admin-gated; a member may clear their own otherwise.
    instance_id = seed_mcp_instance(authMode="api_token", useAdminAuth=True)["_id"]
    resp = request_as(second_user, "DELETE", f"/instances/{instance_id}/credentials")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.delete(f"/instances/{MISSING_INSTANCE_ID}/credentials")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.delete(f"/instances/{UNSAFE_PATH_ID}/credentials")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.delete(f"/instances/{MISSING_INSTANCE_ID}/credentials", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_mcp_delete_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.delete(
        f"/instances/{MISSING_INSTANCE_ID}/credentials", auth=False, headers=narrow_scope_headers
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
