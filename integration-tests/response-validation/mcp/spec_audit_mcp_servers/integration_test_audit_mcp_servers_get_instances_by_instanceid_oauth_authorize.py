"""Strict OpenAPI audit of GET /api/v1/mcp-servers/instances/:instanceId/oauth/authorize."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/oauth/authorize"
CLIENT_ID = "spec-audit-client"


def _authorize_path(instance_id: str) -> str:
    return f"/instances/{instance_id}/oauth/authorize"


def test_admin_gets_authorization_url_for_configured_oauth_instance(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance(**oauth_instance_body())
    instance_id = seeded["_id"]
    configured = mcp_servers_client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": CLIENT_ID, "clientSecret": "spec-audit-secret"},
    )
    if configured.status_code != 200:
        pytest.fail(f"storing the OAuth client: {configured.status_code} {configured.text[:500]}")

    # Nothing is dialled at the provider: this only builds the URL and stores a 10-minute state.
    resp = mcp_servers_client.get(_authorize_path(instance_id))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    url = urlparse(resp.json()["authorizationUrl"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == seeded["authorizationUrl"]
    assert query["client_id"] == [CLIENT_ID]
    assert query["state"][0]
    assert query["redirect_uri"][0].endswith("/mcp-servers/oauth/callback/")


def test_non_oauth_instance_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]

    resp = mcp_servers_client.get(_authorize_path(instance_id))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_oauth_instance_without_client_config_is_conflict(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    # The unreachable url publishes no metadata, so there is no DCR fallback either.
    instance_id = seed_mcp_instance(**oauth_instance_body())["_id"]

    resp = mcp_servers_client.get(_authorize_path(instance_id))
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_INSTANCE_ID))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(_authorize_path(MISSING_INSTANCE_ID), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
