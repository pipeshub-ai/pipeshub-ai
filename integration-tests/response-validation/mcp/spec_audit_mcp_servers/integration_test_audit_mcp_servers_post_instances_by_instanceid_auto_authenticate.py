"""Strict OpenAPI audit of POST /api/v1/mcp-servers/instances/:instanceId/auto-authenticate."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/auto-authenticate"


def _path(instance_id: str) -> str:
    return f"/instances/{instance_id}/auto-authenticate"


def _shared_credential_instance(client: McpServersClient, seed: SeedMcpInstance) -> str:
    instance_id = seed(authMode="api_token", useAdminAuth=True)["_id"]
    # Storing a token never dials the server; it is the admin record the route looks up.
    stored = client.post(f"/instances/{instance_id}/authenticate", json={"apiToken": "spec-audit-token"})
    assert stored.status_code == 200, stored.text[:500]
    return instance_id


def test_member_adopts_the_shared_admin_credential(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    instance_id = _shared_credential_instance(mcp_servers_client, seed_mcp_instance)

    resp = request_as(second_user, "POST", _path(instance_id))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


def test_a_body_is_ignored(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    instance_id = _shared_credential_instance(mcp_servers_client, seed_mcp_instance)
    with outside_request_contract("the route takes no body; one is sent to show it is ignored"):
        resp = mcp_servers_client.post(_path(instance_id), json={"apiToken": "ignored"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        pytest.param({}, 400, id="instance-without-admin-auth"),
        pytest.param(
            {"authMode": "api_token", "useAdminAuth": True}, 409, id="admin-credential-not-set"
        ),
    ],
)
def test_instance_with_nothing_to_adopt_is_refused(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    overrides: JsonObject,
    expected_status: int,
) -> None:
    instance_id = seed_mcp_instance(**overrides)["_id"]

    resp = mcp_servers_client.post(_path(instance_id))

    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(MISSING_INSTANCE_ID))

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.post(_path(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(MISSING_INSTANCE_ID), auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
