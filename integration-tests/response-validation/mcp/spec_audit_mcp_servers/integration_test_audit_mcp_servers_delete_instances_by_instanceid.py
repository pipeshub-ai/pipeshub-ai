"""Strict OpenAPI audit of DELETE /api/v1/mcp-servers/instances/:instanceId."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedMcpInstance,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId"


def test_delete_removes_instance_then_reports_not_found(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance()["_id"]

    resp = mcp_servers_client.delete_instance(instance_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "_id": instance_id}

    assert mcp_servers_client.get_instance(instance_id).status_code == 404

    again = mcp_servers_client.delete_instance(instance_id)
    assert again.status_code == 404, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_delete_as_member_is_forbidden_and_keeps_instance(
    mcp_servers_client: McpServersClient,
    second_user: SecondUser,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance()["_id"]

    resp = request_as(second_user, "DELETE", f"/instances/{instance_id}")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    assert mcp_servers_client.get_instance(instance_id).status_code == 200


@pytest.mark.parametrize(
    ("instance_id", "auth", "expected_status"),
    [
        pytest.param(MISSING_INSTANCE_ID, False, 401, id="no-token"),
        pytest.param(MISSING_INSTANCE_ID, True, 404, id="unknown-id"),
        # guardPathParams runs as router.param, ahead of the auth middleware.
        pytest.param(UNSAFE_PATH_ID, False, 400, id="unsafe-id-without-token"),
    ],
)
def test_delete_is_refused(
    mcp_servers_client: McpServersClient,
    instance_id: str,
    auth: bool,
    expected_status: int,
) -> None:
    resp = mcp_servers_client.delete_instance(instance_id, auth=auth)
    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_delete_without_an_mcp_delete_scope_is_forbidden(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    narrow_scope_headers: dict[str, str],
) -> None:
    instance_id = seed_mcp_instance()["_id"]
    resp = mcp_servers_client.delete_instance(instance_id, auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert mcp_servers_client.get_instance(instance_id).status_code == 200
