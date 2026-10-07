"""Strict OpenAPI audit of POST /api/v1/toolsets/agents/:agentKey/instances/:instanceId/authenticate.

Node has no validator here. Python checks, in this order: edit access to the agent, that
it is a service account, the instance, and only then the body.
"""

from __future__ import annotations

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
    REGULAR_AGENT_REFUSAL,
    UNSAFE_PATH_ID,
    JsonObject,
    SeedAgent,
    SeedToolsetInstance,
    ToolsetsClient,
    agent_not_found,
    agent_path,
    assert_backend_failure,
    assert_bad_request,
    assert_forbidden,
    assert_not_found,
    request_as,
)

from helper.second_user import SecondUser

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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "status": "success",
        "message": "Agent toolset authenticated successfully.",
        "isAuthenticated": True,
    }


def test_authenticate_ignores_unknown_top_level_fields(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()

    with outside_request_contract("only `auth` is read from the body"):
        resp = toolsets_client.post(_path(agent_key, instance_id), json={**VALID_BODY, "specAuditUnknown": 1})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(None, CREDENTIALS_REQUIRED, id="no-body"),
        pytest.param({}, CREDENTIALS_REQUIRED, id="no-auth"),
        pytest.param({"auth": {}}, CREDENTIALS_REQUIRED, id="auth-empty"),
        pytest.param({"auth": "spec-audit"}, "auth must be an object.", id="auth-text"),
    ],
)
def test_authenticate_refuses_a_body_without_credentials(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
    body: JsonObject | None,
    message: str,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.post(_path(agent_key, instance_id), json=body)
    assert_bad_request(resp, message)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        pytest.param(
            {"email": "spec-audit@example.com"},
            "Invalid authentication configuration: apiToken is required for API_TOKEN auth type",
            id="apiToken-missing",
        ),
        pytest.param(
            {**API_TOKEN_AUTH, "specAuditUndeclared": "x"},
            "Unexpected credential fields for this toolset: specAuditUndeclared",
            id="undeclared-field",
        ),
    ],
)
def test_authenticate_refuses_credentials_the_toolset_does_not_accept(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
    auth: JsonObject,
    message: str,
) -> None:
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.post(_path(agent_key, instance_id), json={"auth": auth})
    assert_bad_request(resp, message)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_crashes_on_a_body_that_is_not_an_object(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    # API bug: a JSON list reaches Python, which calls .get() on it.
    instance_id = seed_toolset_instance()["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.post(_path(agent_key, instance_id), json=[VALID_BODY])
    assert_backend_failure(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_authenticate_oauth_instance_is_refused(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
    seed_toolset_instance: SeedToolsetInstance,
) -> None:
    instance_id = seed_toolset_instance(oauth=True)["_id"]
    agent_key = seed_agent()

    resp = toolsets_client.post(_path(agent_key, instance_id), json=VALID_BODY)
    assert_bad_request(
        resp, f"For OAuth toolsets, use the /agents/{agent_key}/instances/{instance_id}/oauth/authorize endpoint."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_regular_agent_is_refused(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
) -> None:
    # The service-account check runs before the instance lookup, so no instance is needed.
    agent_key = seed_agent(is_service_account=False)

    resp = toolsets_client.post(_path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_bad_request(resp, REGULAR_AGENT_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_can_only_read_the_agent_is_forbidden(
    second_user: SecondUser, seed_agent: SeedAgent
) -> None:
    # A service-account agent is shared with the org as reader, which is not edit access.
    agent_key = seed_agent()

    resp = request_as(second_user, "POST", _path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_forbidden(resp, AGENT_EDIT_REFUSAL)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_agent_not_shared_with_member_is_not_found(
    second_user: SecondUser,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent(is_service_account=False)

    resp = request_as(second_user, "POST", _path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_not_found(resp, agent_not_found(agent_key))
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_unknown_agent_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_not_found(resp, agent_not_found(MISSING_AGENT_KEY))
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_unknown_instance_is_not_found(
    toolsets_client: ToolsetsClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent()

    resp = toolsets_client.post(_path(agent_key, MISSING_INSTANCE_ID), json=VALID_BODY)
    assert_not_found(resp, INSTANCE_NOT_FOUND)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_refuses_an_unsafe_instance_id_before_auth(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.post(_path(MISSING_AGENT_KEY, UNSAFE_PATH_ID), auth=False, json=VALID_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
