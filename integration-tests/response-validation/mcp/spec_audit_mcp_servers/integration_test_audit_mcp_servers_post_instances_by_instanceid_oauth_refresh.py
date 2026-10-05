"""Strict OpenAPI audit of POST /api/v1/mcp-servers/instances/:instanceId/oauth/refresh.

No success case: a 200 needs a stored refresh token and a live OAuth token endpoint.
"""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/oauth/refresh"


def _refresh_path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth/refresh"


def test_oauth_instance_without_credential_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = mcp_servers_client.post(_refresh_path(instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_instance_is_bad_request_not_404(
    mcp_servers_client: McpServersClient,
) -> None:
    # The handler never loads the instance: it only looks up the caller's credential
    # record, so an unknown id fails the same way as an unauthenticated one.
    resp = mcp_servers_client.post(_refresh_path(MISSING_INSTANCE_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_not_admin_gated(
    second_user: SecondUser,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    # No admin check in Python: a member gets past auth and fails on its own missing credential.
    resp = request_as(second_user, "POST", _refresh_path(instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_refresh_path(MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unsafe_instance_id_is_rejected_before_auth(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.post(_refresh_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
