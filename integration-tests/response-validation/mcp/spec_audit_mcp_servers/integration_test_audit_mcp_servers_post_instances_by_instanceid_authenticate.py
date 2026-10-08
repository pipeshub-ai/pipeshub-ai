"""Strict OpenAPI audit of POST /api/v1/mcp-servers/instances/:instanceId/authenticate."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    CATALOG_STDIO_ENV,
    CATALOG_STDIO_TYPE_ID,
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

ROUTE = "/api/v1/mcp-servers/instances/:instanceId/authenticate"

# Never sent anywhere: authenticate only stores the credential, it does not dial the server.
API_TOKEN_BODY: JsonObject = {"apiToken": "spec-audit-not-a-real-token"}


def _authenticate(instance_id: str) -> str:
    return f"/instances/{instance_id}/authenticate"


def test_authenticate_stores_api_token(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]

    resp = mcp_servers_client.post(_authenticate(instance_id), json=API_TOKEN_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}

    listed = mcp_servers_client.get("/my-mcp-servers", params={"includeTools": "false"})
    entry = next(i for i in listed.json()["instances"] if i["_id"] == instance_id)
    assert entry["isAuthenticated"] is True


def test_authenticate_stores_a_header_credential(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(authMode="headers", headerName="X-Spec-Audit")["_id"]
    resp = mcp_servers_client.post(_authenticate(instance_id), json={"headerValue": "spec-audit-value"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


def test_authenticate_stdio_server_with_its_required_env(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(
        typeId=CATALOG_STDIO_TYPE_ID, transport="stdio", url=None, authMode="api_token",
    )["_id"]
    resp = mcp_servers_client.post(
        _authenticate(instance_id), json={"env": {CATALOG_STDIO_ENV: "x", "NOT_ALLOWED": "dropped"}}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    missing = mcp_servers_client.post(_authenticate(instance_id), json={"env": {"NOT_ALLOWED": "x"}})
    assert missing.status_code == 400, missing.text[:500]
    assert_strict_openapi_exchange(missing, ROUTE)


@pytest.mark.parametrize(
    ("instance", "body"),
    [
        # Every field is optional in the pydantic model; the handler refuses the missing token.
        pytest.param({"authMode": "api_token"}, {}, id="missing-api-token"),
        pytest.param({"authMode": "headers"}, {"headerName": "X-Only-A-Name"}, id="missing-header-value"),
        pytest.param({}, API_TOKEN_BODY, id="instance-needs-no-auth"),
        pytest.param(oauth_instance_body(), API_TOKEN_BODY, id="instance-uses-oauth"),
    ],
)
def test_authenticate_refuses_what_the_auth_mode_does_not_take(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    instance: JsonObject,
    body: JsonObject,
) -> None:
    instance_id = seed_mcp_instance(**instance)["_id"]
    resp = mcp_servers_client.post(_authenticate(instance_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"apiToken": 12345}, id="api-token-not-a-string"),
        pytest.param({"env": {"SPEC_AUDIT_TOKEN": 1}}, id="env-value-not-a-string"),
        pytest.param({"env": ["SPEC_AUDIT_TOKEN"]}, id="env-not-an-object"),
    ],
)
def test_bad_credential_body_is_unprocessable(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    body: JsonObject,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = mcp_servers_client.post(_authenticate(instance_id), json=body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("auth_mode", "body"),
    [
        pytest.param("api_token", {"api_token": "spec-audit-token", "remember": True}, id="api_token"),
        pytest.param("headers", {"header_name": "X-Spec", "header_value": "v", "remember": True}, id="header_value"),
    ],
)
def test_authenticate_ignores_unknown_fields_and_reads_snake_case(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    auth_mode: str,
    body: JsonObject,
) -> None:
    instance_id = seed_mcp_instance(authMode=auth_mode)["_id"]
    with outside_request_contract("credentials are sent under snake_case names next to an unknown field"):
        resp = mcp_servers_client.post(_authenticate(instance_id), json=body)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_set_a_shared_admin_credential(
    seed_mcp_instance: SeedMcpInstance, second_user: SecondUser
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token", useAdminAuth=True)["_id"]
    resp = request_as(second_user, "POST", _authenticate(instance_id), json=API_TOKEN_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_stores_their_own_credential(
    seed_mcp_instance: SeedMcpInstance, second_user: SecondUser
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = request_as(second_user, "POST", _authenticate(instance_id), json=API_TOKEN_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_authenticate(MISSING_INSTANCE_ID), json=API_TOKEN_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_instance_id_is_rejected(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_authenticate(UNSAFE_PATH_ID), json=API_TOKEN_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.post(
        _authenticate(MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers, json=API_TOKEN_BODY
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_authenticate(MISSING_INSTANCE_ID), json=API_TOKEN_BODY, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
