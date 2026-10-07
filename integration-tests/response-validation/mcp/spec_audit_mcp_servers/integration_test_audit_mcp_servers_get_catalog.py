"""Strict OpenAPI audit of GET /api/v1/mcp-servers/catalog."""

from __future__ import annotations

import uuid

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import CATALOG_STDIO_TYPE_ID, McpServersClient, request_as
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/catalog"


def test_catalog_pages_and_filters_templates(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get("/catalog", params={"page": 1, "limit": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["page"] == 1
    assert body["limit"] == 1
    assert isinstance(body["total"], int)
    assert len(body["templates"]) == min(1, body["total"])

    miss = mcp_servers_client.get("/catalog", params={"search": f"no-such-{uuid.uuid4().hex}"})
    assert miss.status_code == 200, miss.text[:500]
    assert_strict_openapi_exchange(miss, ROUTE)
    assert miss.json() == {"templates": [], "total": 0, "page": 1, "limit": 50, "customStdioAllowed": False}


def test_catalog_reports_custom_stdio_servers_are_off_by_default(mcp_servers_client: McpServersClient) -> None:
    # MCP_ALLOW_CUSTOM_STDIO is an operator opt-in in the service environment; this stack keeps the default.
    resp = mcp_servers_client.get("/catalog", params={"search": CATALOG_STDIO_TYPE_ID})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["customStdioAllowed"] is False
    template = next(t for t in body["templates"] if t["typeId"] == CATALOG_STDIO_TYPE_ID)
    assert template["transport"] == "stdio"
    assert template["command"]


def test_catalog_at_the_largest_page_holds_every_template(mcp_servers_client: McpServersClient) -> None:
    # One page of 200 is the whole catalog, so every template is checked against the schema.
    resp = mcp_servers_client.get("/catalog", params={"limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert len(body["templates"]) == body["total"] > 0
    assert "github" in {t["typeId"] for t in body["templates"]}

    found = mcp_servers_client.get("/catalog", params={"search": "github"})
    assert found.status_code == 200, found.text[:500]
    assert_strict_openapi_exchange(found, ROUTE)
    assert "github" in {t["typeId"] for t in found.json()["templates"]}


def test_catalog_is_open_to_a_non_admin_member(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/catalog")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["page"] == 1
    assert body["limit"] == 50
    assert len(body["templates"]) == min(50, body["total"])


@pytest.mark.parametrize(
    "params",
    [{"limit": 201}, {"limit": 0}, {"page": 0}, {"page": "abc"}, {"limit": "1.5"}],
    ids=["limit-above-max", "limit-zero", "page-zero", "page-not-a-number", "limit-not-an-integer"],
)
def test_catalog_invalid_paging_is_unprocessable(
    mcp_servers_client: McpServersClient, params: dict[str, object]
) -> None:
    # Node forwards page/limit untouched; the bounds live in FastAPI's Query().
    resp = mcp_servers_client.get("/catalog", params=params)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_catalog_empty_page_value_falls_back_to_the_default(mcp_servers_client: McpServersClient) -> None:
    # Node drops empty query values before forwarding, so Python never sees page=.
    with outside_request_contract("an empty page value is not an integer"):
        resp = mcp_servers_client.get("/catalog", params={"page": "", "limit": 1})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["page"] == 1


def test_catalog_drops_an_unknown_query_parameter(mcp_servers_client: McpServersClient) -> None:
    with outside_request_contract("sends a query parameter the route does not define"):
        resp = mcp_servers_client.get("/catalog", params={"limit": 1, "sort": "name"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["limit"] == 1


def test_catalog_without_an_mcp_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get("/catalog", auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_catalog_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get("/catalog", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
