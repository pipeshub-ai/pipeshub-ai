"""Strict OpenAPI audit of GET /api/v1/toolsets/registry."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    TOOLSET_TYPE,
    JsonObject,
    ToolsetsClient,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/registry"


def _by_name(resp: requests.Response) -> dict[str, JsonObject]:
    return {toolset["name"]: toolset for toolset in resp.json()["toolsets"]}


def test_registry_lists_toolsets_with_python_defaults(toolsets_client: ToolsetsClient) -> None:
    # Node forwards only page/limit/search, so Python answers with its own defaults:
    # tools included, grouped by category.
    resp = toolsets_client.get("/registry")
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success"
    by_name = _by_name(resp)
    assert TOOLSET_TYPE in by_name
    assert by_name[TOOLSET_TYPE]["tools"]
    assert by_name[TOOLSET_TYPE]["toolCount"] == len(by_name[TOOLSET_TYPE]["tools"])
    grouped = [toolset["name"] for group in body["categorizedToolsets"].values() for toolset in group]
    assert sorted(grouped) == sorted(by_name)
    assert body["pagination"] == {
        "page": 1,
        "limit": 20,
        "total": len(by_name),
        "totalPages": (len(by_name) + 19) // 20,
    }


@pytest.mark.parametrize("value", ["false", "true"])
def test_registry_returns_tool_details_whatever_include_tools_says(
    toolsets_client: ToolsetsClient, value: str
) -> None:
    # API bug: Node validates include_tools and never forwards it, so Python always
    # applies its own default and include_tools=false still returns every tool.
    resp = toolsets_client.get("/registry", params={"include_tools": value})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    jira = _by_name(resp)[TOOLSET_TYPE]
    assert jira["tools"], jira
    assert jira["toolCount"] == len(jira["tools"])


@pytest.mark.parametrize(
    "params",
    [
        {"include_tools": "banana"},
        {"include_tools": ""},
        [("include_tools", "true"), ("include_tools", "false")],
    ],
    ids=["not-a-boolean", "empty", "repeated"],
)
def test_registry_refuses_no_include_tools_value(toolsets_client: ToolsetsClient, params: Any) -> None:
    with outside_request_contract("include_tools is read as `value === 'true'`, so nothing is refused"):
        resp = toolsets_client.get("/registry", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _by_name(resp)[TOOLSET_TYPE]["tools"]


def test_member_searches_registry_and_gets_the_unpaged_list(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "GET", "/registry", params={"search": "a", "page": 2, "limit": 1}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    pagination = body["pagination"]
    assert (pagination["page"], pagination["limit"]) == (2, 1)
    assert pagination["totalPages"] == pagination["total"]
    # Grouping by category is always on through Node, and it turns the slicing off.
    assert len(body["toolsets"]) == pagination["total"] > 1


def test_registry_search_that_matches_nothing_is_an_empty_list(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/registry", params={"search": "spec-audit-matches-no-toolset"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["toolsets"] == []
    assert body["categorizedToolsets"] == {}
    assert body["pagination"] == {"page": 1, "limit": 20, "total": 0, "totalPages": 0}


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "query.limit"),
        ({"limit": 201}, "query.limit"),
        ({"limit": ""}, "query.limit"),
        ({"page": 0}, "query.page"),
        ({"page": "abc"}, "query.page"),
        ({"page": "1.5"}, "query.page"),
        ({"page": ""}, "query.page"),
        ([("search", "a"), ("search", "b")], "query.search"),
    ],
    ids=[
        "limit-zero",
        "limit-above-max",
        "limit-empty",
        "page-zero",
        "page-not-a-number",
        "page-fraction",
        "page-empty",
        "search-repeated",
    ],
)
def test_registry_rejects_a_query_that_fails_validation(
    toolsets_client: ToolsetsClient, params: Any, field: str
) -> None:
    resp = toolsets_client.get("/registry", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_registry_reads_page_and_limit_as_javascript_numbers(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("page and limit go through Number(), which also reads hex and exponents"):
        resp = toolsets_client.get("/registry", params={"page": "0x2", "limit": "2e1"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (2, 20)


def test_registry_ignores_unknown_query_parameters(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("unknown query parameters are dropped by the validator"):
        resp = toolsets_client.get("/registry", params={"specAuditUnknown": "1", "group_by_category": "false"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    # group_by_category is a Python parameter Node never forwards: grouping stays on.
    assert resp.json()["categorizedToolsets"]


def test_registry_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/registry", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
