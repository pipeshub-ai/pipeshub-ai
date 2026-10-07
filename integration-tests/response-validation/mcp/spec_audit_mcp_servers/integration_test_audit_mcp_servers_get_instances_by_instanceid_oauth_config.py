"""Strict OpenAPI audit of GET /api/v1/mcp-servers/instances/:instanceId/oauth-config."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/oauth-config"

CLIENT_ID = "spec-audit-client-id"
CLIENT_SECRET = "spec-audit-client-secret"
MASK = "•" * 8


def _path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth-config"


def test_admin_reads_masked_client_config(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    stored = mcp_servers_client.put(
        _path(instance_id),
        json={"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET},
    )
    if stored.status_code != 200:
        pytest.fail(f"storing the OAuth client config: {stored.status_code} {stored.text[:500]}")

    resp = mcp_servers_client.get(_path(instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.json() == {
        "configured": True,
        "clientId": f"{CLIENT_ID[:4]}{MASK}{CLIENT_ID[-4:]}",
        "clientSecret": f"{CLIENT_SECRET[:4]}{MASK}{CLIENT_SECRET[-4:]}",
    }
    assert CLIENT_SECRET not in resp.text


def test_unknown_instance_reports_not_configured(
    mcp_servers_client: McpServersClient,
) -> None:
    # The handler never looks the instance up, so an unknown id is a 200, not a 404.
    resp = mcp_servers_client.get(_path(MISSING_INSTANCE_ID))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"configured": False}


def test_short_values_are_masked_entirely(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]
    stored = mcp_servers_client.put(_path(instance_id), json={"clientId": "short", "clientSecret": "12345678"})
    assert stored.status_code == 200, stored.text[:500]

    resp = mcp_servers_client.get(_path(instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"configured": True, "clientId": "•" * 5, "clientSecret": "•" * 8}


def test_unknown_query_parameter_is_ignored(mcp_servers_client: McpServersClient) -> None:
    with outside_request_contract("sends a query parameter the route does not define"):
        resp = mcp_servers_client.get(_path(MISSING_INSTANCE_ID), params={"reveal": "true"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"configured": False}


def test_without_an_mcp_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(_path(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", _path(MISSING_INSTANCE_ID))
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_path(MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.get(_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
