"""Strict OpenAPI audit of POST /api/v1/toolsets/agents/:agentKey/instances/:instanceId/reauthenticate."""

from __future__ import annotations

import pytest
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    AGENT_EDIT_REFUSAL,
    API_TOKEN_AUTH,
    INSTANCE_NOT_FOUND,
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    REGULAR_AGENT_REFUSAL,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_not_found,
    agent_path,
    assert_bad_request,
    assert_forbidden,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey/instances/:instanceId/reauthenticate"
SUFFIX = "/reauthenticate"
CLEARED: JsonObject = {"status": "success", "message": "Agent credentials cleared. Please re-authenticate."}


def _is_authenticated(toolsets_client: ToolsetsClient, agent_key: str, instance: JsonObject) -> bool:
    listed = toolsets_client.get(agent_path(agent_key), params={"search": instance["instanceName"]})
    assert listed.status_code == 200, listed.text[:500]
    return bool(next(t for t in listed.json()["toolsets"] if t["instanceId"] == instance["_id"])["isAuthenticated"])


def test_reauthenticate_clears_service_account_agent_credentials(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    agent_key = seed_agent()
    instance = seed_toolset_instance()
    saved = toolsets_client.post(
        agent_path(agent_key, instance["_id"], "/authenticate"), json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]
    assert _is_authenticated(toolsets_client, agent_key, instance)

    resp = toolsets_client.post(agent_path(agent_key, instance["_id"], SUFFIX))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == CLEARED
    assert not _is_authenticated(toolsets_client, agent_key, instance)


def test_reauthenticate_without_saved_credentials_still_succeeds(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    agent_key = seed_agent()
    instance_id = seed_toolset_instance()["_id"]

    with outside_request_contract("Node forwards no body to the backend for this route"):
        resp = toolsets_client.post(agent_path(agent_key, instance_id, SUFFIX), json={"specAuditUnknown": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == CLEARED


def test_reauthenticate_regular_agent_is_bad_request(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent(is_service_account=False)

    resp = toolsets_client.post(agent_path(agent_key, MISSING_INSTANCE_ID, SUFFIX))
    assert_bad_request(resp, REGULAR_AGENT_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_can_only_read_the_agent_is_forbidden(
    second_user: SecondUser, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent()
    resp = request_as(second_user, "POST", agent_path(agent_key, MISSING_INSTANCE_ID, SUFFIX))
    assert_forbidden(resp, AGENT_EDIT_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_unknown_instance_is_not_found(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    # A real service-account agent, so the 404 comes from the instance lookup, not the agent one.
    agent_key = seed_agent()

    resp = toolsets_client.post(agent_path(agent_key, MISSING_INSTANCE_ID, SUFFIX))
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_unknown_agent_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, SUFFIX))
    assert_not_found(resp, agent_not_found(MISSING_AGENT_KEY))
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, SUFFIX), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_reauthenticate_with_unsafe_agent_key_is_refused_before_auth(toolsets_client: ToolsetsClient) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.post(agent_path(UNSAFE_PATH_ID, MISSING_INSTANCE_ID, SUFFIX), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
