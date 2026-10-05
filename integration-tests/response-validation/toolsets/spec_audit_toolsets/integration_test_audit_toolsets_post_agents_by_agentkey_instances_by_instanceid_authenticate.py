"""Strict OpenAPI audit of POST /api/v1/toolsets/agents/:agentKey/instances/:instanceId/authenticate."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
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
    request_as,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey/instances/:instanceId/authenticate"

VALID_BODY: JsonObject = {"auth": dict(API_TOKEN_AUTH)}


def _path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/authenticate")


def test_authenticate_stores_credentials_for_service_account_agent(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    # The agent's credentials are removed when the seeded instance is deleted.
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.post(_path(agent_key, instance_id), json=VALID_BODY)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Agent toolset authenticated successfully.",
        "isAuthenticated": True,
    }


def test_authenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(
        _path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, json=VALID_BODY
    )

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authenticate_regular_agent_is_refused(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
) -> None:
    # The service-account check runs before the instance lookup, so no instance is needed.
    agent_key = seed_agent(is_service_account=False)

    resp = toolsets_client.post(_path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authenticate_agent_not_shared_with_member_is_not_found(
    second_user: SecondUser,
    seed_agent: SeedAgent,
) -> None:
    # A regular agent is never org-shared by default, so the member has no permission
    # edge and Python hides the agent with a 404 rather than a 403.
    agent_key = seed_agent(is_service_account=False)

    resp = request_as(
        second_user, "POST", _path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY
    )

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_authenticate_unknown_instance_is_not_found(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent()

    resp = toolsets_client.post(_path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
