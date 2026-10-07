"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/instances/:instanceId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    CATALOG_STDIO_TYPE_ID,
    MISSING_INSTANCE_ID,
    MISSING_TYPE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
    custom_stdio_allowed,
    instance_body,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances/:instanceId"


def test_update_replaces_config_and_keeps_identity(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()
    body = instance_body(
        authMode="api_token",
        headerName="X-Api-Key",
        description="Updated by the MCP servers spec audit",
    )

    resp = mcp_servers_client.update_instance(seeded["_id"], body)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    record = resp.json()
    assert record["_id"] == seeded["_id"]
    assert record["createdAt"] == seeded["createdAt"]
    assert record["createdBy"] == seeded["createdBy"]
    assert record["updatedAt"] >= seeded["updatedAt"]
    assert record["name"] == body["name"]
    assert record["authMode"] == "api_token"
    assert record["headerName"] == "X-Api-Key"
    assert record["description"] == body["description"]
    assert record["isCustom"] is True


@pytest.mark.parametrize(
    ("overrides", "omit"),
    [
        pytest.param({}, "transport", id="missing-transport"),
        pytest.param({}, "name", id="missing-name"),
        pytest.param({"authMode": "kerberos"}, None, id="unknown-auth-mode"),
        pytest.param({"scopes": "read"}, None, id="scopes-not-a-list"),
    ],
)
def test_update_with_invalid_body_is_unprocessable(
    mcp_servers_client: McpServersClient, overrides: JsonObject, omit: str | None
) -> None:
    body = instance_body(**overrides)
    if omit:
        del body[omit]

    # Pydantic rejects the body before the handler looks the instance up.
    resp = mcp_servers_client.update_instance(MISSING_INSTANCE_ID, body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_ignores_unknown_fields_and_keeps_identity(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()
    with outside_request_contract("sends body fields the route does not define"):
        resp = mcp_servers_client.update_instance(
            seeded["_id"], instance_body(_id="spec-audit-other-id", createdBy="someone-else")
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["_id"] == seeded["_id"]
    assert resp.json()["createdBy"] == seeded["createdBy"]


def test_update_to_an_unknown_catalog_type_is_rejected(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()
    resp = mcp_servers_client.update_instance(seeded["_id"], instance_body(typeId=MISSING_TYPE_ID))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_custom_http_server_without_url_is_rejected(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()

    resp = mcp_servers_client.update_instance(seeded["_id"], instance_body(url=None))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    stored = mcp_servers_client.get_instance(seeded["_id"])
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["url"] == seeded["url"]


def test_update_to_a_custom_stdio_server_is_refused_without_the_operator_opt_in(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    if custom_stdio_allowed(mcp_servers_client):
        pytest.fail("this deployment sets MCP_ALLOW_CUSTOM_STDIO=true; the suite expects the default (off)")
    seeded = seed_mcp_instance()

    resp = mcp_servers_client.update_instance(
        seeded["_id"], instance_body(transport="stdio", url=None, command="spec-audit-not-a-binary")
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "MCP_ALLOW_CUSTOM_STDIO=true" in resp.json()["error"]["message"]

    stored = mcp_servers_client.get_instance(seeded["_id"])
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["transport"] == "streamable_http"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"transport": "streamable_http"}, id="catalog-transport-changed"),
        pytest.param({"command": "/bin/sh"}, id="catalog-command-overridden"),
    ],
)
def test_update_cannot_change_what_a_catalog_stdio_server_runs(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    overrides: JsonObject,
) -> None:
    catalog = {"typeId": CATALOG_STDIO_TYPE_ID, "transport": "stdio", "url": None, "authMode": "api_token"}
    seeded = seed_mcp_instance(**catalog)

    resp = mcp_servers_client.update_instance(seeded["_id"], instance_body(**{**catalog, **overrides}))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    stored = mcp_servers_client.get_instance(seeded["_id"])
    assert stored.status_code == 200, stored.text[:500]
    assert stored.json()["command"] == seeded["command"]
    assert stored.json()["transport"] == "stdio"


def test_update_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.update_instance(MISSING_INSTANCE_ID, instance_body())
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.update_instance(
        MISSING_INSTANCE_ID, instance_body(), auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_as_member_is_forbidden(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    second_user: SecondUser,
) -> None:
    seeded = seed_mcp_instance()
    resp = request_as(second_user, "PUT", f"/instances/{seeded['_id']}", json=instance_body())
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert mcp_servers_client.get_instance(seeded["_id"]).json()["name"] == seeded["name"]


def test_update_unsafe_instance_id_is_rejected_before_auth(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.update_instance(UNSAFE_PATH_ID, instance_body(), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
