"""Strict OpenAPI audit of DELETE /api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials.

Node only authenticates and forwards to Python `remove_agent_toolset_credentials`, which
checks edit access to a service-account agent and then deletes the agent's credential key
without looking the instance up.
"""

from __future__ import annotations

import pytest
from toolsets_audit_support import (
    API_TOKEN_AUTH,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_path,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials"


def _is_authenticated(toolsets_client: ToolsetsClient, agent_key: str, instance: JsonObject) -> bool:
    listed = toolsets_client.get(
        agent_path(agent_key), params={"search": instance["instanceName"], "limit": 200}
    )
    assert listed.status_code == 200, listed.text[:500]
    entries = [t for t in listed.json()["toolsets"] if t.get("instanceId") == instance["_id"]]
    assert entries, f"instance {instance['_id']} missing from the agent's toolsets: {listed.text[:500]}"
    return bool(entries[0].get("isAuthenticated"))


def test_remove_saved_agent_credentials(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance = seed_toolset_instance()
    agent_key = seed_agent()
    saved = toolsets_client.post(
        agent_path(agent_key, instance["_id"], "/authenticate"),
        json={"auth": dict(API_TOKEN_AUTH)},
    )
    assert saved.status_code == 200, saved.text[:500]
    assert _is_authenticated(toolsets_client, agent_key, instance)

    resp = toolsets_client.delete(agent_path(agent_key, instance["_id"], "/credentials"))
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)

    assert not _is_authenticated(toolsets_client, agent_key, instance)


def test_remove_credentials_of_regular_agent_is_rejected(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent(is_service_account=False)
    resp = toolsets_client.delete(agent_path(agent_key, MISSING_INSTANCE_ID, "/credentials"))
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_without_access_to_agent_gets_not_found(
    seed_agent: SeedAgent,
    second_user: SecondUser,
) -> None:
    # An unshared agent is invisible to the member, so Python answers 404 rather than 403.
    agent_key = seed_agent()
    resp = request_as(
        second_user, "DELETE", agent_path(agent_key, MISSING_INSTANCE_ID, "/credentials")
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_remove_agent_credentials_without_token_is_unauthorized(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent()
    resp = toolsets_client.delete(
        agent_path(agent_key, MISSING_INSTANCE_ID, "/credentials"), auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_remove_agent_credentials_with_unsafe_agent_key_is_rejected_before_auth(
    toolsets_client: ToolsetsClient,
) -> None:
    resp = toolsets_client.delete(
        agent_path(UNSAFE_PATH_ID, MISSING_INSTANCE_ID, "/credentials"), auth=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
