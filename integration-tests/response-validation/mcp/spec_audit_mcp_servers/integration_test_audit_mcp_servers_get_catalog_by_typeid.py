"""Strict OpenAPI audit of GET /api/v1/mcp-servers/catalog/:typeId."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_TYPE_ID,
    UNSAFE_PATH_ID,
    McpServersClient,
    request_as,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/catalog/:typeId"

# A template registered in backend/python/app/agents/mcp/servers/github.py.
KNOWN_TYPE_ID = "github"


def test_get_catalog_template_returns_the_template(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.get(f"/catalog/{KNOWN_TYPE_ID}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    template = resp.json()
    assert template["typeId"] == KNOWN_TYPE_ID
    assert template["transport"] == "streamable_http"
    assert template["defaultAuthMode"] in template["supportedAuthModes"]


def test_every_listed_template_is_served_by_its_type_id(
    mcp_servers_client: McpServersClient,
) -> None:
    listed = mcp_servers_client.get("/catalog", params={"limit": 200})
    assert listed.status_code == 200, listed.text[:500]
    for entry in listed.json()["templates"]:
        resp = mcp_servers_client.get(f"/catalog/{entry['typeId']}")
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == entry


def test_member_can_read_catalog_template(second_user: SecondUser) -> None:
    # The catalog is not admin-gated in Python, unlike the instance routes.
    resp = request_as(second_user, "GET", f"/catalog/{KNOWN_TYPE_ID}")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["typeId"] == KNOWN_TYPE_ID


def test_unknown_type_id_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(f"/catalog/{MISSING_TYPE_ID}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unsafe_type_id_is_rejected(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(f"/catalog/{UNSAFE_PATH_ID}")
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_mcp_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(f"/catalog/{KNOWN_TYPE_ID}", auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(f"/catalog/{KNOWN_TYPE_ID}", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
