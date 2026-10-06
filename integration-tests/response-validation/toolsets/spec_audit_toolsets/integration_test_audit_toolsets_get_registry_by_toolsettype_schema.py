"""Strict OpenAPI audit of GET /api/v1/toolsets/registry/:toolsetType/schema."""

from __future__ import annotations

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    MISSING_TOOLSET_TYPE,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    ToolsetsClient,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/registry/:toolsetType/schema"
TOOLSET_FIELDS = {
    "name",
    "displayName",
    "description",
    "category",
    "supportedAuthTypes",
    "documentationLinks",
    "config",
    "oauthConfig",
    "tools",
}


def _schema_path(toolset_type: str) -> str:
    return f"/registry/{toolset_type}/schema"


def test_schema_returns_the_registry_entry_for_a_known_toolset(
    toolsets_client: ToolsetsClient,
) -> None:
    # The registry normalises the name, so an upper-case type resolves to the same entry.
    resp = toolsets_client.get(_schema_path(TOOLSET_TYPE.upper()))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success", body
    toolset = body["toolset"]
    assert set(toolset) == TOOLSET_FIELDS, sorted(toolset)
    assert toolset["name"].lower() == TOOLSET_TYPE, toolset["name"]
    assert {"OAUTH", "API_TOKEN"} <= set(toolset["supportedAuthTypes"]), toolset
    assert isinstance(toolset["tools"], list) and toolset["tools"], toolset["tools"]
    assert isinstance(toolset["config"], dict), toolset["config"]


def test_schema_is_readable_by_a_non_admin_member(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", _schema_path(TOOLSET_TYPE))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(resp.json()["toolset"]) == TOOLSET_FIELDS, resp.text[:500]


def test_schema_does_not_read_the_query_string(toolsets_client: ToolsetsClient) -> None:
    plain = toolsets_client.get(_schema_path(TOOLSET_TYPE))
    with outside_request_contract("the route reads no query parameter"):
        resp = toolsets_client.get(
            _schema_path(TOOLSET_TYPE), params={"include_tools": "false", "specAuditUnknown": "1"}
        )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == plain.json()


def test_schema_of_an_unknown_toolset_type_is_not_found(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(_schema_path(MISSING_TOOLSET_TYPE))
    assert_not_found(resp, f"Toolset '{MISSING_TOOLSET_TYPE}' not found in registry")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_schema_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(_schema_path(TOOLSET_TYPE), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_schema_refuses_an_unsafe_toolset_type_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(_schema_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
