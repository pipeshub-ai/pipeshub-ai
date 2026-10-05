"""Strict OpenAPI audit of GET /api/v1/mcp-servers/catalog."""

from __future__ import annotations

import uuid

import pytest
from mcp_servers_audit_support import McpServersClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/catalog"


def test_catalog_pages_and_filters_templates(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get("/catalog", params={"page": 1, "limit": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["page"] == 1
    assert body["limit"] == 1
    assert isinstance(body["total"], int)
    assert len(body["templates"]) == min(1, body["total"])
    for template in body["templates"]:
        assert template["typeId"]
        assert template["displayName"]

    miss = mcp_servers_client.get("/catalog", params={"search": f"no-such-{uuid.uuid4().hex}"})
    assert miss.status_code == 200, miss.text[:500]
    assert_strict_openapi_response(miss, ROUTE)
    assert miss.json() == {"templates": [], "total": 0, "page": 1, "limit": 50}


def test_catalog_is_open_to_a_non_admin_member(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", "/catalog")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["page"] == 1
    assert body["limit"] == 50
    assert len(body["templates"]) == min(50, body["total"])


@pytest.mark.parametrize(
    "params",
    [{"limit": 201}, {"page": "abc"}],
    ids=["limit-above-max", "page-not-a-number"],
)
def test_catalog_invalid_paging_is_unprocessable(
    mcp_servers_client: McpServersClient, params: dict[str, object]
) -> None:
    # Node forwards page/limit untouched; the bounds live in FastAPI's Query().
    resp = mcp_servers_client.get("/catalog", params=params)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_catalog_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get("/catalog", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
