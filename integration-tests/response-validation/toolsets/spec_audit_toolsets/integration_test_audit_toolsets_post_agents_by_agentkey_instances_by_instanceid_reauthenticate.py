"""Strict OpenAPI audit of POST /api/v1/toolsets/agents/:agentKey/instances/:instanceId/reauthenticate."""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_path,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey/instances/:instanceId/reauthenticate"
SUFFIX = "/reauthenticate"


def test_reauthenticate_clears_service_account_agent_credentials(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    # Clearing credentials that were never saved is still a success.
    agent_key = seed_agent()
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.post(agent_path(agent_key, instance_id, SUFFIX))

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Agent credentials cleared. Please re-authenticate.",
    }


def test_reauthenticate_regular_agent_is_bad_request(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    agent_key = seed_agent(is_service_account=False)
    instance_id = seed_toolset_instance()["_id"]

    resp = toolsets_client.post(agent_path(agent_key, instance_id, SUFFIX))

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_unknown_instance_is_not_found(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    # A real service-account agent, so the 404 comes from the instance lookup, not the agent one.
    agent_key = seed_agent()

    resp = toolsets_client.post(agent_path(agent_key, MISSING_INSTANCE_ID, SUFFIX))

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, SUFFIX), auth=False
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_reauthenticate_with_unsafe_agent_key_is_refused_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers before authenticate runs.
    resp = toolsets_client.post(
        agent_path(UNSAFE_PATH_ID, MISSING_INSTANCE_ID, SUFFIX), auth=False
    )

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
