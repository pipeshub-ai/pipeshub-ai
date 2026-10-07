"""Strict OpenAPI audit of GET /api/v1/toolsets/agents/:agentKey."""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_AGENT_KEY,
    TOOLSET_TYPE,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_not_found,
    agent_path,
    assert_not_found,
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey"


def _entry(resp: Any, instance_id: str) -> JsonObject:
    assert resp.status_code == 200, resp.text[:500]
    entry: JsonObject | None = next((t for t in resp.json()["toolsets"] if t["instanceId"] == instance_id), None)
    assert entry is not None, resp.text[:500]
    return entry


def test_owner_lists_an_instance_with_the_agents_auth_status(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    seeded = seed_toolset_instance()
    agent_key = seed_agent()

    # The list is paginated over every instance in the shared org; narrow it to the seeded one.
    resp = toolsets_client.get(
        agent_path(agent_key),
        params={"search": seeded["instanceName"], "toolsetType": TOOLSET_TYPE, "limit": 200},
    )
    entry = _entry(resp, seeded["_id"])
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success", body
    assert body["pagination"]["page"] == 1, body["pagination"]
    assert body["pagination"]["limit"] == 200, body["pagination"]
    assert body["filterCounts"] == {"all": 1, "authenticated": 0, "notAuthenticated": 1}, body
    assert entry["toolsetType"] == TOOLSET_TYPE, entry
    assert entry["authType"] == "API_TOKEN", entry
    assert entry["isConfigured"] is True, entry
    assert entry["isFromRegistry"] is False, entry
    assert entry["isAuthenticated"] is False, entry
    assert entry["hasCredentials"] is False, entry
    # The owner can edit the agent, so the credential key is present; nothing is saved yet.
    assert "auth" in entry and entry["auth"] is None, entry


def test_owner_sees_the_agents_saved_credentials_and_a_reader_does_not(
    toolsets_client: ToolsetsClient,
    second_user: SecondUser,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    seeded = seed_toolset_instance()
    agent_key = seed_agent()
    saved = toolsets_client.post(
        agent_path(agent_key, seeded["_id"], "/authenticate"), json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]
    params = {"search": seeded["instanceName"]}

    resp = toolsets_client.get(agent_path(agent_key), params=params)
    entry = _entry(resp, seeded["_id"])
    assert_strict_openapi_exchange(resp, ROUTE)
    assert (entry["isAuthenticated"], entry["hasCredentials"]) == (True, True)
    assert entry["auth"] == API_TOKEN_AUTH
    assert resp.json()["filterCounts"] == {"all": 1, "authenticated": 1, "notAuthenticated": 0}

    # A service-account agent is shared with the whole org as reader: listed, but no credentials.
    as_member = request_as(second_user, "GET", agent_path(agent_key), params=params)
    member_entry = _entry(as_member, seeded["_id"])
    assert_strict_openapi_exchange(as_member, ROUTE)
    assert (member_entry["isAuthenticated"], member_entry["hasCredentials"]) == (True, True)
    assert "auth" not in member_entry, member_entry


def test_include_registry_adds_toolset_types_without_an_instance(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent()

    resp = toolsets_client.get(agent_path(agent_key), params={"includeRegistry": "true", "limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    from_registry = [t for t in resp.json()["toolsets"] if t["isFromRegistry"]]
    assert from_registry, resp.text[:500]
    for entry in from_registry:
        assert (entry["instanceId"], entry["isConfigured"], entry["isAuthenticated"]) == ("", False, False)


@pytest.mark.parametrize(
    "params",
    [{"includeRegistry": "banana"}, {"includeRegistry": ""}],
    ids=["not-a-boolean", "empty"],
)
def test_include_registry_reads_any_value_but_true_as_false(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent, params: JsonObject
) -> None:
    agent_key = seed_agent()

    with outside_request_contract("includeRegistry is read as `value === 'true'`, so nothing is refused"):
        resp = toolsets_client.get(agent_path(agent_key), params={**params, "limit": 200})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert not any(t["isFromRegistry"] for t in resp.json()["toolsets"])


def test_page_and_limit_are_read_as_javascript_numbers(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent()

    with outside_request_contract("page and limit go through Number(), which also reads hex and exponents"):
        resp = toolsets_client.get(
            agent_path(agent_key), params={"includeRegistry": "true", "page": "0x2", "limit": "2e0"}
        )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    pagination = resp.json()["pagination"]
    assert (pagination["page"], pagination["limit"]) == (2, 2)
    assert len(resp.json()["toolsets"]) == 2


def test_unknown_query_parameters_are_dropped(toolsets_client: ToolsetsClient, seed_agent: SeedAgent) -> None:
    agent_key = seed_agent()

    with outside_request_contract("unknown query parameters (authStatus included) are dropped by the validator"):
        resp = toolsets_client.get(
            agent_path(agent_key),
            params={"search": "spec-audit-matches-nothing", "authStatus": "authenticated", "specAuditUnknown": "1"},
        )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["toolsets"] == []


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "query.limit"),
        ({"limit": 201}, "query.limit"),
        ({"limit": ""}, "query.limit"),
        ({"page": 0}, "query.page"),
        ({"page": "abc"}, "query.page"),
        ({"page": ""}, "query.page"),
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
        "search-repeated",
        "toolsetType-repeated",
    ],
)
def test_get_agent_toolsets_rejects_a_query_that_fails_validation(
    toolsets_client: ToolsetsClient, params: Any, field: str
) -> None:
    resp = toolsets_client.get(agent_path(MISSING_AGENT_KEY), params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == {field}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_an_unknown_agent_key_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(agent_path(MISSING_AGENT_KEY))
    assert_not_found(resp, agent_not_found(MISSING_AGENT_KEY))
    assert_strict_openapi_exchange(resp, ROUTE)


def test_a_member_without_access_to_the_agent_gets_not_found(
    second_user: SecondUser,
    seed_agent: SeedAgent,
) -> None:
    # A regular agent is not shared with the org, so the member has no permission edge.
    agent_key = seed_agent(is_service_account=False)

    resp = request_as(second_user, "GET", agent_path(agent_key))
    assert_not_found(resp, agent_not_found(agent_key))
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_agent_toolsets_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(agent_path(MISSING_AGENT_KEY), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_agent_toolsets_refuses_an_unsafe_agent_key_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(agent_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
