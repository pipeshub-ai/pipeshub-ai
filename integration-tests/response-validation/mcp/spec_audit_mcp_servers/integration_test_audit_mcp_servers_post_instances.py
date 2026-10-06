"""Strict OpenAPI audit of POST /api/v1/mcp-servers/instances."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
import requests
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_TYPE_ID,
    UNREACHABLE_MCP_URL,
    JsonObject,
    McpServersClient,
    instance_body,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/instances"

Create = Callable[[JsonObject], requests.Response]


@pytest.fixture
def create(mcp_servers_client: McpServersClient) -> Iterator[Create]:
    """``create(body)`` posts the body; any instance it creates is deleted on teardown."""
    created: list[str] = []

    def _create(body: JsonObject) -> requests.Response:
        resp = mcp_servers_client.create_instance(body)
        if resp.status_code == 201:
            created.append(resp.json()["_id"])
        return resp

    try:
        yield _create
    finally:
        for instance_id in created:
            mcp_servers_client.delete_instance(instance_id)


def test_admin_creates_custom_instance(create: Create) -> None:
    body = instance_body()

    resp = create(body)
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    record: JsonObject = resp.json()
    assert record["_id"]
    assert record["name"] == body["name"]
    assert record["transport"] == "streamable_http"
    assert record["authMode"] == "none"
    assert record["url"] == UNREACHABLE_MCP_URL
    assert record["typeId"] is None
    assert record["isCustom"] is True
    assert record["useAdminAuth"] is False


def test_admin_creates_instance_from_a_catalog_template(
    create: Create, mcp_servers_client: McpServersClient
) -> None:
    template = mcp_servers_client.get("/catalog/github").json()
    body = {"name": instance_body()["name"], "typeId": "github", "transport": "streamable_http", "authMode": "api_token"}

    resp = create(body)
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    record = resp.json()
    assert record["typeId"] == "github"
    assert record["isCustom"] is False
    assert record["url"] == template["defaultUrl"]
    assert record["scopes"] == template["defaultScopes"]


def test_admin_creates_custom_stdio_instance_with_allowed_env(create: Create) -> None:
    # Stored only: the command is never run by this call.
    body = instance_body(
        transport="stdio",
        url=None,
        command="spec-audit-not-a-binary",
        args=["--flag"],
        requiredEnv=["SPEC_AUDIT_TOKEN"],
        env={"SPEC_AUDIT_TOKEN": "x"},
        authMode="api_token",
        useAdminAuth=True,
    )
    resp = create(body)
    assert resp.status_code == 201, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    record = resp.json()
    assert record["command"] == "spec-audit-not-a-binary"
    assert record["args"] == ["--flag"]
    assert record["requiredEnv"] == ["SPEC_AUDIT_TOKEN"]
    assert record["useAdminAuth"] is True
    # env is only checked against the allowlist, never stored on the instance.
    assert "env" not in record


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"typeId": MISSING_TYPE_ID}, id="unknown-catalog-type"),
        pytest.param({"url": None}, id="custom-http-without-url"),
        pytest.param({"transport": "stdio", "url": None}, id="custom-stdio-without-command"),
        pytest.param(
            {"transport": "stdio", "command": "x", "requiredEnv": ["A"], "env": {"B": "x"}},
            id="env-not-allowed",
        ),
    ],
)
def test_create_semantically_invalid_instance_is_a_bad_request(create: Create, overrides: JsonObject) -> None:
    resp = create(instance_body(**overrides))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("overrides", "omit"),
    [
        pytest.param({"transport": "carrier_pigeon"}, None, id="unknown-transport"),
        pytest.param({"authMode": "kerberos"}, None, id="unknown-auth-mode"),
        pytest.param({}, "name", id="missing-name"),
        pytest.param({}, "transport", id="missing-transport"),
        pytest.param({}, "authMode", id="missing-auth-mode"),
        pytest.param({"name": 123}, None, id="name-not-a-string"),
        pytest.param({"args": "--flag"}, None, id="args-not-a-list"),
        pytest.param({"env": {"A": 1}}, None, id="env-value-not-a-string"),
        pytest.param({"useAdminAuth": None}, None, id="use-admin-auth-null"),
    ],
)
def test_create_with_invalid_body_is_unprocessable(
    create: Create, overrides: JsonObject, omit: str | None
) -> None:
    payload = instance_body(**overrides)
    if omit:
        del payload[omit]
    resp = create(payload)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_create_ignores_unknown_fields(create: Create) -> None:
    with outside_request_contract("sends a body field the route does not define"):
        resp = create(instance_body(isCustom=False, colour="blue"))
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["isCustom"] is True
    assert "colour" not in resp.json()


def test_create_also_reads_snake_case_field_names(create: Create) -> None:
    body = instance_body()
    body["auth_mode"] = body.pop("authMode")
    with outside_request_contract("authMode is sent under its snake_case name"):
        resp = create(body)
        assert resp.status_code == 201, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["authMode"] == "none"


def test_member_cannot_create_instance(
    mcp_servers_client: McpServersClient, second_user: SecondUser
) -> None:
    resp = request_as(second_user, "POST", "/instances", json=instance_body())
    try:
        assert resp.status_code == 403, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        if resp.status_code == 201:
            mcp_servers_client.delete_instance(resp.json()["_id"])


def test_create_without_an_mcp_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.post("/instances", auth=False, headers=narrow_scope_headers, json=instance_body())
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.create_instance(instance_body(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
