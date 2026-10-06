"""Strict OpenAPI audit of GET /api/v1/toolsets/my-toolsets."""

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
    API_TOKEN_AUTH,
    TOOLSET_TYPE,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/my-toolsets"

REGISTRY_ENTRY_FIELDS = {
    "instanceId",
    "instanceName",
    "toolsetType",
    "authType",
    "oauthConfigId",
    "displayName",
    "description",
    "iconPath",
    "category",
    "supportedAuthTypes",
    "documentationLinks",
    "toolCount",
    "tools",
    "isConfigured",
    "isAuthenticated",
    "isFromRegistry",
}
INSTANCE_ENTRY_FIELDS = REGISTRY_ENTRY_FIELDS | {
    "createdBy",
    "createdAtTimestamp",
    "updatedAtTimestamp",
    "auth",
}
EMPTY_LIST: JsonObject = {
    "status": "success",
    "toolsets": [],
    "pagination": {"page": 1, "limit": 20, "total": 0, "totalPages": 0, "hasNext": False, "hasPrev": False},
    "filterCounts": {"all": 0, "authenticated": 0, "notAuthenticated": 0},
}


def _only_entry(resp: requests.Response, instance_id: str) -> JsonObject:
    assert resp.status_code == 200, resp.text[:500]
    toolsets = resp.json()["toolsets"]
    assert [entry["instanceId"] for entry in toolsets] == [instance_id], resp.text[:500]
    return toolsets[0]


def test_my_toolsets_lists_an_instance_the_caller_has_not_authenticated(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.get("/my-toolsets", params={"search": seeded["instanceName"].upper()})
    entry = _only_entry(resp, seeded["_id"])
    assert_strict_openapi_exchange(resp, ROUTE)

    assert set(entry) == INSTANCE_ENTRY_FIELDS, sorted(entry)
    assert entry["instanceName"] == seeded["instanceName"]
    assert (entry["toolsetType"], entry["authType"]) == (TOOLSET_TYPE, "API_TOKEN")
    assert entry["oauthConfigId"] is None
    assert (entry["isConfigured"], entry["isAuthenticated"], entry["isFromRegistry"]) == (True, False, False)
    assert entry["auth"] is None
    assert entry["createdBy"] == seeded["createdBy"]
    assert entry["toolCount"] == len(entry["tools"]) > 0
    body = resp.json()
    assert body["pagination"] == {
        "page": 1,
        "limit": 20,
        "total": 1,
        "totalPages": 1,
        "hasNext": False,
        "hasPrev": False,
    }
    assert body["filterCounts"] == {"all": 1, "authenticated": 0, "notAuthenticated": 1}


def test_my_toolsets_returns_the_callers_own_saved_credentials(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    seeded = seed_toolset_instance()
    saved = toolsets_client.post(f"/instances/{seeded['_id']}/authenticate", json={"auth": dict(API_TOKEN_AUTH)})
    assert saved.status_code == 200, saved.text[:500]
    search = {"search": seeded["instanceName"]}

    resp = toolsets_client.get("/my-toolsets", params=search)
    entry = _only_entry(resp, seeded["_id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    assert entry["isAuthenticated"] is True
    # The saved credential fields come back as stored, the API token included.
    assert entry["auth"] == API_TOKEN_AUTH
    assert resp.json()["filterCounts"] == {"all": 1, "authenticated": 1, "notAuthenticated": 0}

    # Authentication is per user: the member sees the same instance as not authenticated.
    as_member = request_as(second_user, "GET", "/my-toolsets", params=search)
    member_entry = _only_entry(as_member, seeded["_id"])
    assert_strict_openapi_exchange(as_member, ROUTE)
    assert (member_entry["isAuthenticated"], member_entry["auth"]) == (False, None)


@pytest.mark.parametrize(
    ("auth_status", "listed"),
    [("authenticated", True), ("not-authenticated", False)],
)
def test_my_toolsets_filters_by_auth_status_and_counts_before_the_filter(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    auth_status: str,
    listed: bool,
) -> None:
    seeded = seed_toolset_instance()
    saved = toolsets_client.post(f"/instances/{seeded['_id']}/authenticate", json={"auth": dict(API_TOKEN_AUTH)})
    assert saved.status_code == 200, saved.text[:500]

    resp = toolsets_client.get(
        "/my-toolsets", params={"search": seeded["instanceName"], "authStatus": auth_status}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert [entry["instanceId"] for entry in body["toolsets"]] == ([seeded["_id"]] if listed else [])
    assert body["pagination"]["total"] == (1 if listed else 0)
    assert body["filterCounts"] == {"all": 1, "authenticated": 1, "notAuthenticated": 0}


def test_my_toolsets_lists_an_oauth_instance_with_its_oauth_config_id(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance(oauth=True)

    resp = toolsets_client.get("/my-toolsets", params={"search": seeded["instanceName"]})
    entry = _only_entry(resp, seeded["_id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    assert (entry["authType"], entry["oauthConfigId"]) == ("OAUTH", seeded["oauthConfigId"])
    assert entry["auth"] is None


def test_my_toolsets_adds_registry_entries_for_types_without_an_instance(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.get("/my-toolsets", params={"includeRegistry": "true", "limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    toolsets = resp.json()["toolsets"]
    from_registry = [entry for entry in toolsets if entry["isFromRegistry"]]
    assert from_registry, "the registry holds more types than the stack has instances"
    for entry in from_registry:
        assert set(entry) == REGISTRY_ENTRY_FIELDS, sorted(entry)
        assert (entry["instanceId"], entry["oauthConfigId"]) == ("", None)
        assert (entry["isConfigured"], entry["isAuthenticated"]) == (False, False)
        # The first auth type the toolset supports stands in for the instance's.
        assert entry["authType"] == entry["supportedAuthTypes"][0]
    # A type that has an instance is not repeated as a registry entry.
    assert seeded["_id"] in {entry["instanceId"] for entry in toolsets}
    assert TOOLSET_TYPE not in {entry["toolsetType"] for entry in from_registry}


def test_my_toolsets_toolset_type_filter_is_case_insensitive_and_scopes_the_registry(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    seeded = seed_toolset_instance()

    resp = toolsets_client.get(
        "/my-toolsets",
        params={"toolsetType": TOOLSET_TYPE.upper(), "includeRegistry": "true", "limit": 200},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert seeded["_id"] in {entry["instanceId"] for entry in body["toolsets"]}
    assert {entry["toolsetType"] for entry in body["toolsets"]} == {TOOLSET_TYPE}
    assert not any(entry["isFromRegistry"] for entry in body["toolsets"])
    assert body["filterCounts"]["all"] == len(body["toolsets"])


def test_my_toolsets_empty_toolset_type_is_no_filter(toolsets_client: ToolsetsClient) -> None:
    everything = toolsets_client.get("/my-toolsets", params={"includeRegistry": "true", "limit": 200})
    assert everything.status_code == 200, everything.text[:500]

    resp = toolsets_client.get(
        "/my-toolsets", params={"includeRegistry": "true", "limit": 200, "toolsetType": ""}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    types = {entry["toolsetType"] for entry in resp.json()["toolsets"]}
    assert len(types) > 1
    assert types == {entry["toolsetType"] for entry in everything.json()["toolsets"]}


def test_my_toolsets_pages_through_the_merged_list(toolsets_client: ToolsetsClient) -> None:
    first = toolsets_client.get("/my-toolsets", params={"includeRegistry": "true", "limit": 200})
    assert first.status_code == 200, first.text[:500]
    total = first.json()["pagination"]["total"]
    assert total > 2

    resp = toolsets_client.get("/my-toolsets", params={"includeRegistry": "true", "limit": 1, "page": 2})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert len(body["toolsets"]) == 1
    pagination = body["pagination"]
    assert (pagination["page"], pagination["limit"]) == (2, 1)
    assert (pagination["hasNext"], pagination["hasPrev"]) == (True, True)
    assert pagination["totalPages"] == pagination["total"]

    past_the_end = toolsets_client.get(
        "/my-toolsets", params={"includeRegistry": "true", "limit": 200, "page": 99}
    )
    assert past_the_end.status_code == 200, past_the_end.text[:500]
    assert_strict_openapi_exchange(past_the_end, ROUTE)
    assert past_the_end.json()["toolsets"] == []
    assert past_the_end.json()["pagination"]["hasNext"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"search": "spec-audit-matches-no-toolset"},
        {"search": "spec-audit-matches-no-toolset", "includeRegistry": "true", "toolsetType": ""},
    ],
    ids=["instances-only", "with-registry"],
)
def test_my_toolsets_search_that_matches_nothing_is_an_empty_list(
    second_user: SecondUser, params: dict[str, str]
) -> None:
    resp = request_as(second_user, "GET", "/my-toolsets", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == EMPTY_LIST


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "query.limit"),
        ({"limit": 201}, "query.limit"),
        ({"limit": ""}, "query.limit"),
        ({"page": 0}, "query.page"),
        ({"page": "abc"}, "query.page"),
        ({"page": ""}, "query.page"),
        ({"authStatus": "pending"}, "query.authStatus"),
        ({"authStatus": ""}, "query.authStatus"),
        ([("search", "a"), ("search", "b")], "query.search"),
        ([("toolsetType", "jira"), ("toolsetType", "slack")], "query.toolsetType"),
    ],
    ids=[
        "limit-zero",
        "limit-above-max",
        "limit-empty",
        "page-zero",
        "page-not-a-number",
        "page-empty",
        "authStatus-unknown",
        "authStatus-empty",
        "search-repeated",
        "toolsetType-repeated",
    ],
)
def test_my_toolsets_rejects_a_query_that_fails_validation(
    toolsets_client: ToolsetsClient, params: Any, field: str
) -> None:
    resp = toolsets_client.get("/my-toolsets", params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        {"includeRegistry": "banana"},
        {"includeRegistry": ""},
        [("includeRegistry", "true"), ("includeRegistry", "true")],
    ],
    ids=["not-a-boolean", "empty", "repeated"],
)
def test_my_toolsets_reads_any_include_registry_value_but_true_as_false(
    toolsets_client: ToolsetsClient, params: Any
) -> None:
    with outside_request_contract("includeRegistry is read as `value === 'true'`, so nothing is refused"):
        resp = toolsets_client.get("/my-toolsets", params=params)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert not any(entry["isFromRegistry"] for entry in resp.json()["toolsets"])


def test_my_toolsets_reads_page_and_limit_as_javascript_numbers(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("page and limit go through Number(), which also reads hex and exponents"):
        resp = toolsets_client.get("/my-toolsets", params={"page": "0x2", "limit": "2e0"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (2, 2)


def test_my_toolsets_ignores_unknown_query_parameters(toolsets_client: ToolsetsClient) -> None:
    with outside_request_contract("unknown query parameters are dropped by the validator"):
        resp = toolsets_client.get(
            "/my-toolsets", params={"search": "spec-audit-matches-no-toolset", "specAuditUnknown": "1"}
        )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == EMPTY_LIST


def test_my_toolsets_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get("/my-toolsets", auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
