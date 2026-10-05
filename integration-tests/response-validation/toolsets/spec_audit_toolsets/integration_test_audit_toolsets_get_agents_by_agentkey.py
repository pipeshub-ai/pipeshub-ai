"""Strict OpenAPI audit of GET /api/v1/toolsets/agents/:agentKey."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_AGENT_KEY,
    TOOLSET_TYPE,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_path,
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey"


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
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    body = resp.json()
    assert body["status"] == "success", body
    assert body["pagination"]["page"] == 1, body["pagination"]
    assert body["pagination"]["limit"] == 200, body["pagination"]
    assert set(body["filterCounts"]) == {"all", "authenticated", "notAuthenticated"}, body
    entry = next((t for t in body["toolsets"] if t["instanceId"] == seeded["_id"]), None)
    assert entry is not None, body["toolsets"]
    assert entry["toolsetType"] == TOOLSET_TYPE, entry
    assert entry["authType"] == "API_TOKEN", entry
    assert entry["isConfigured"] is True, entry
    assert entry["isFromRegistry"] is False, entry
    assert entry["isAuthenticated"] is False, entry
    assert entry["hasCredentials"] is False, entry
    # The owner can edit the agent, so the credential key is present; nothing is saved yet.
    assert "auth" in entry and entry["auth"] is None, entry


def test_an_unknown_agent_key_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(agent_path(MISSING_AGENT_KEY))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_a_member_without_access_to_the_agent_gets_not_found(
    second_user: SecondUser,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent()

    resp = request_as(second_user, "GET", agent_path(agent_key))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_agent_toolsets_rejects_a_limit_below_one(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.get(agent_path(MISSING_AGENT_KEY), params={"limit": 0})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_get_agent_toolsets_rejects_a_call_without_a_token(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.get(agent_path(MISSING_AGENT_KEY), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
