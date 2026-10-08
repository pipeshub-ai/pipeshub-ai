"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/instances/:instanceId/credentials."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
    oauth_instance_body,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


def test_put_credentials_replaces_a_stored_api_token(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    first = mcp_servers_client.post(f"/instances/{instance_id}/authenticate", json={"apiToken": "first"})
    assert first.status_code == 200, first.text[:500]

    resp = mcp_servers_client.put(_credentials(instance_id), json={"apiToken": "second"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


@pytest.mark.parametrize(
    ("instance", "body"),
    [
        # Every body field is optional for pydantic; the per-authMode requirement is a 400.
        pytest.param({"authMode": "api_token"}, {}, id="missing-api-token"),
        pytest.param({"authMode": "headers"}, {}, id="missing-header-value"),
        pytest.param({}, {"apiToken": "x"}, id="instance-needs-no-auth"),
        pytest.param(oauth_instance_body(), {"apiToken": "x"}, id="instance-uses-oauth"),
    ],
)
def test_put_credentials_the_auth_mode_does_not_take_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    instance: JsonObject,
    body: JsonObject,
) -> None:
    instance_id = seed_mcp_instance(**instance)["_id"]
    resp = mcp_servers_client.put(_credentials(instance_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"apiToken": {"value": "x"}}, id="api-token-not-a-string"),
        pytest.param({"headerValue": 7}, id="header-value-not-a-string"),
    ],
)
def test_put_credentials_with_a_wrong_type_is_unprocessable(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance, body: JsonObject
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = mcp_servers_client.put(_credentials(instance_id), json=body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_put_credentials_ignores_unknown_fields(
    mcp_servers_client: McpServersClient, seed_mcp_instance: SeedMcpInstance
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    with outside_request_contract("sends a body field the route does not define"):
        resp = mcp_servers_client.put(_credentials(instance_id), json={"apiToken": "x", "expiresAt": 1})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_replace_a_shared_admin_credential(
    seed_mcp_instance: SeedMcpInstance, second_user: SecondUser
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token", useAdminAuth=True)["_id"]
    resp = request_as(second_user, "PUT", _credentials(instance_id), json={"apiToken": "x"})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_credentials_for_an_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(_credentials(MISSING_INSTANCE_ID), json={"apiToken": "spec-audit"})
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_credentials_with_an_unsafe_id_is_rejected(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.put(_credentials(UNSAFE_PATH_ID), json={"apiToken": "spec-audit"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_credentials_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers, json={"apiToken": "x"}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_credentials_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_INSTANCE_ID), auth=False, json={"apiToken": "spec-audit"}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
