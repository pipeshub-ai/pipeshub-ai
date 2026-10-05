"""Strict OpenAPI audit of GET /api/v1/toolsets/registry."""

from __future__ import annotations

import pytest
from toolsets_audit_support import TOOLSET_TYPE, ToolsetsClient, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/registry"


def test_registry_lists_toolsets_with_python_defaults(toolsets_client: ToolsetsClient) -> None:
    # Node forwards only page/limit/search, so Python answers with its own defaults:
    # tools included, grouped by category.
    resp = toolsets_client.get("/registry")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success"
    by_name = {toolset["name"]: toolset for toolset in body["toolsets"]}
    assert TOOLSET_TYPE in by_name
    assert "tools" in by_name[TOOLSET_TYPE]
    assert by_name[TOOLSET_TYPE]["toolCount"] == len(by_name[TOOLSET_TYPE]["tools"])
    assert isinstance(body["categorizedToolsets"], dict)
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 20
    assert body["pagination"]["total"] == len(by_name)


@pytest.mark.xfail(
    strict=True,
    reason="API bug: GET /toolsets/registry validates include_tools but never forwards it, "
    "so include_tools=false still returns every tool",
)
def test_registry_omits_tool_details_when_include_tools_is_false(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get("/registry", params={"include_tools": "false"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    # Python answers include_tools=false with an empty tools list next to the tool count.
    by_name = {toolset["name"]: toolset for toolset in resp.json()["toolsets"]}
    assert by_name[TOOLSET_TYPE]["tools"] == []
    assert by_name[TOOLSET_TYPE]["toolCount"] > 0


def test_member_searches_registry_and_gets_the_unpaged_list(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "GET", "/registry", params={"search": "a", "page": 2, "limit": 1}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    pagination = body["pagination"]
    assert (pagination["page"], pagination["limit"]) == (2, 1)
    assert pagination["totalPages"] == pagination["total"]
    # Grouping by category is always on through Node, and it turns the slicing off.
    assert len(body["toolsets"]) == pagination["total"]


def test_registry_rejects_a_limit_above_the_maximum(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/registry", params={"limit": 201})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_registry_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/registry", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
