"""Strict OpenAPI audit of GET /api/v1/mcp-servers/instances."""

from __future__ import annotations

import pytest
from mcp_servers_audit_support import (
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances"


def _listed(body: JsonObject, instance_id: str) -> JsonObject:
    # The org is shared, so the list also carries other suites' instances.
    matches = [i for i in body["instances"] if i.get("_id") == instance_id]
    assert len(matches) == 1, f"instance {instance_id} listed {len(matches)} times"
    return matches[0]


def test_list_returns_seeded_instance_as_stored(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    record = seed_mcp_instance()

    resp = mcp_servers_client.list_instances()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert set(body) == {"instances"}
    assert _listed(body, record["_id"]) == {**record, "hasOAuthClientConfig": False}


def test_list_flags_instance_with_oauth_client_config(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    record = seed_mcp_instance(**oauth_instance_body())
    instance_id = record["_id"]
    configured = mcp_servers_client.put(
        f"/instances/{instance_id}/oauth-config",
        json={"clientId": "spec-audit-client", "clientSecret": "spec-audit-secret"},
    )
    assert configured.status_code == 200, configured.text[:500]

    resp = mcp_servers_client.list_instances()
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    listed = _listed(resp.json(), instance_id)
    assert listed["authMode"] == "oauth"
    assert listed["hasOAuthClientConfig"] is True
    assert "spec-audit-secret" not in resp.text


def test_list_ignores_query_parameters(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    record = seed_mcp_instance()
    # Node forwards no query string for this route, so even a nonsense page is dropped.
    with outside_request_contract("the route defines no query parameters"):
        resp = mcp_servers_client.get("/instances", params={"page": "not-a-number"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    _listed(resp.json(), record["_id"])


def test_list_without_an_mcp_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get("/instances", auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/instances")
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.list_instances(auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
