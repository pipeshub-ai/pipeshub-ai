"""Strict OpenAPI audit of PUT /api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials."""

from __future__ import annotations

import pytest
from strict_openapi import assert_strict_openapi_response
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    JsonObject,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_path,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials"
# Unlike the user route, Node's zod schema here is an open record, so every field
# Jira declares (baseUrl included) reaches Python.
VALID_BODY: JsonObject = {"auth": {**API_TOKEN_AUTH, "apiToken": "spec-audit-token-2"}}


def test_update_agent_credentials_replaces_the_agents_saved_credentials(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()
    # The route only updates: the agent's credentials must already be saved.
    # Deleting the seeded instance on teardown removes them again.
    saved = toolsets_client.post(
        agent_path(agent_key, instance_id, "/authenticate"), json={"auth": dict(API_TOKEN_AUTH)}
    )
    assert saved.status_code == 200, saved.text[:500]

    resp = toolsets_client.put(agent_path(agent_key, instance_id, "/credentials"), json=VALID_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_agent_credentials_refuses_a_regular_agent(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent(is_service_account=False)
    # Python checks the agent before it looks up the instance.
    resp = toolsets_client.put(
        agent_path(agent_key, MISSING_INSTANCE_ID, "/credentials"), json=VALID_BODY
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_agent_credentials_is_not_found_for_an_unknown_agent(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/credentials"), json=VALID_BODY
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_agent_credentials_rejects_a_body_without_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    # Refused by Node's zod schema, so the body is Node's validation error, not Python's.
    resp = toolsets_client.put(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/credentials"),
        json={"apiToken": "spec-audit-token-2"},
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_agent_credentials_rejects_a_call_without_a_token(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.put(
        agent_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID, "/credentials"),
        auth=False,
        json=VALID_BODY,
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
