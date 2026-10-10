"""Strict OpenAPI audit of PUT /api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials.

Node's validator requires `auth` as an object of any keys (unlike the user route, it keeps
every field) and drops other top-level fields; Python replaces the agent's saved `auth`.
"""

from __future__ import annotations

from typing import Any

import pytest
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    AGENT_EDIT_REFUSAL,
    API_TOKEN_AUTH,
    CREDENTIALS_REQUIRED,
    INSTANCE_NOT_FOUND,
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    NO_SAVED_CREDENTIALS,
    OAUTH_INSTANCE_CREDENTIALS_REFUSAL,
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
    rejected_fields,
    request_as,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/agents/:agentKey/instances/:instanceId/credentials"
VALID_BODY: JsonObject = {"auth": {**API_TOKEN_AUTH, "apiToken": "spec-audit-token-2"}}
UPDATED: JsonObject = {"status": "success", "message": "Agent credentials updated successfully."}


def _path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/credentials")


def _authenticate(client: ToolsetsClient, agent_key: str, instance_id: str) -> None:
    saved = client.post(agent_path(agent_key, instance_id, "/authenticate"), json={"auth": dict(API_TOKEN_AUTH)})
    assert saved.status_code == 200, saved.text[:500]


def _saved_auth(client: ToolsetsClient, agent_key: str, instance: JsonObject) -> Any:
    listed = client.get(agent_path(agent_key), params={"search": instance["instanceName"]})
    assert listed.status_code == 200, listed.text[:500]
    return next(t for t in listed.json()["toolsets"] if t["instanceId"] == instance["_id"])["auth"]


def test_update_agent_credentials_replaces_the_agents_saved_credentials(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance = seed_toolset_instance()
    agent_key = seed_agent()
    # The route only updates: the agent's credentials must already be saved.
    _authenticate(toolsets_client, agent_key, instance["_id"])

    resp = toolsets_client.put(_path(agent_key, instance["_id"]), json=VALID_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == UPDATED
    assert _saved_auth(toolsets_client, agent_key, instance) == VALID_BODY["auth"]

    # Replaced whole, not merged.
    partial = toolsets_client.put(_path(agent_key, instance["_id"]), json={"auth": {"apiToken": "spec-audit-token-3"}})
    assert partial.status_code == 200, partial.text[:500]
    assert_strict_openapi_exchange(partial, ROUTE)
    assert _saved_auth(toolsets_client, agent_key, instance) == {"apiToken": "spec-audit-token-3"}


def test_update_agent_credentials_drops_unknown_top_level_fields(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance = seed_toolset_instance()
    agent_key = seed_agent()
    _authenticate(toolsets_client, agent_key, instance["_id"])

    with outside_request_contract("the validator drops top-level fields other than auth"):
        resp = toolsets_client.put(_path(agent_key, instance["_id"]), json={**VALID_BODY, "specAuditUnknown": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_update_agent_credentials_is_not_found_before_the_agent_authenticated(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.put(_path(agent_key, instance_id), json=VALID_BODY)
    assert_not_found(resp, NO_SAVED_CREDENTIALS)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_agent_credentials_of_an_unknown_instance_is_not_found(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent()
    resp = toolsets_client.put(_path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_agent_credentials_refuses_an_oauth_instance(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_toolset_instance(oauth=True)["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.put(_path(agent_key, instance_id), json=VALID_BODY)
    assert_bad_request(resp, OAUTH_INSTANCE_CREDENTIALS_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_agent_credentials_refuses_a_field_the_toolset_does_not_declare(
    toolsets_client: ToolsetsClient,
    seed_toolset_instance: SeedToolsetInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.put(_path(agent_key, instance_id), json={"auth": {"username": "spec-audit"}})
    assert_bad_request(resp, "Unexpected credential fields for this toolset: username")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_agent_credentials_refuses_an_empty_auth_before_the_instance_is_looked_up(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent()
    resp = toolsets_client.put(_path(agent_key, MISSING_INSTANCE_ID), json={"auth": {}})
    assert_bad_request(resp, CREDENTIALS_REQUIRED)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_update_agent_credentials_refuses_a_regular_agent(
    toolsets_client: ToolsetsClient, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent(is_service_account=False)
    # Python checks the agent before it looks up the instance.
    resp = toolsets_client.put(_path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_bad_request(resp, REGULAR_AGENT_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_can_only_read_the_agent_is_forbidden(
    second_user: SecondUser, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent()
    resp = request_as(second_user, "PUT", _path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_forbidden(resp, AGENT_EDIT_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_agent_credentials_is_not_found_for_an_unknown_agent(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_not_found(resp, agent_not_found(MISSING_AGENT_KEY))
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(None, id="no-body"),
        pytest.param({"apiToken": "spec-audit-token-2"}, id="auth-missing"),
        pytest.param({"auth": "spec-audit"}, id="auth-text"),
        pytest.param({"auth": ["spec-audit"]}, id="auth-list"),
    ],
)
def test_update_agent_credentials_rejects_a_body_without_an_auth_object(
    toolsets_client: ToolsetsClient, body: JsonObject | None
) -> None:
    # Refused by Node's validator, before the agent is looked up.
    resp = toolsets_client.put(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == {"body.auth"}
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_agent_credentials_rejects_a_call_without_a_token(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_update_agent_credentials_refuses_an_unsafe_agent_key_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.put(_path(UNSAFE_PATH_ID, MISSING_INSTANCE_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
