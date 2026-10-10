"""Strict OpenAPI audit of POST /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/authenticate."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    MISSING_AGENT_KEY,
    MISSING_INSTANCE_ID,
    UNSAFE_PATH_ID,
    JsonObject,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
    oauth_instance_body,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/authenticate"

# Never sent anywhere: authenticate only stores the credential, it does not dial the server.
API_TOKEN_BODY: JsonObject = {"apiToken": "spec-audit-not-a-real-token"}


def _path(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/authenticate")


def test_authenticate_stores_agent_api_token(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    # Deleting the seeded instance on teardown also removes the agent's credential.
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    agent_key = seed_agent()

    resp = mcp_servers_client.post(_path(agent_key, instance_id), json=API_TOKEN_BODY)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": True, "isAuthenticated": True}


def test_authenticate_stores_a_header_credential(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="headers", headerName="X-Spec-Audit")["_id"]
    resp = mcp_servers_client.post(_path(seed_agent(), instance_id), json={"headerValue": "spec-audit-value"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("instance", "body"),
    [
        pytest.param({"authMode": "api_token"}, {}, id="missing-api-token"),
        pytest.param({"authMode": "headers"}, {"headerName": "X-Only-A-Name"}, id="missing-header-value"),
        pytest.param({}, API_TOKEN_BODY, id="instance-needs-no-auth"),
        pytest.param(oauth_instance_body(), API_TOKEN_BODY, id="instance-uses-oauth"),
    ],
)
def test_authenticate_refuses_what_the_auth_mode_does_not_take(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
    instance: JsonObject,
    body: JsonObject,
) -> None:
    instance_id = seed_mcp_instance(**instance)["_id"]
    resp = mcp_servers_client.post(_path(seed_agent(), instance_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_authenticate_ignores_unknown_fields_and_reads_snake_case(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    with outside_request_contract("the token is sent as api_token next to an unknown field"):
        resp = mcp_servers_client.post(
            _path(seed_agent(), instance_id), json={"api_token": "spec-audit-token", "remember": True}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_regular_agent_is_rejected(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
) -> None:
    agent_key = seed_agent(is_service_account=False)

    # The service-account check runs before the instance lookup, so the id need not exist.
    resp = mcp_servers_client.post(_path(agent_key, MISSING_INSTANCE_ID), json=API_TOKEN_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_cannot_edit_the_agent_is_forbidden(
    seed_mcp_instance: SeedMcpInstance, seed_agent: SeedAgent, second_user: SecondUser
) -> None:
    # A service-account agent is visible to the whole org, but only its owner may edit it.
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    resp = request_as(second_user, "POST", _path(seed_agent(), instance_id), json=API_TOKEN_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_agent_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=API_TOKEN_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_instance_is_not_found(mcp_servers_client: McpServersClient, seed_agent: SeedAgent) -> None:
    resp = mcp_servers_client.post(_path(seed_agent(), MISSING_INSTANCE_ID), json=API_TOKEN_BODY)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"apiToken": 12345}, id="api-token-not-a-string"),
        pytest.param({"env": {"SPEC_AUDIT_TOKEN": 1}}, id="env-value-not-a-string"),
        pytest.param({"env": ["SPEC_AUDIT_TOKEN"]}, id="env-not-an-object"),
    ],
)
def test_bad_credential_body_is_unprocessable(mcp_servers_client: McpServersClient, body: JsonObject) -> None:
    # Pydantic validates the body before the handler looks anything up.
    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize("which", ["agentKey", "instanceId"])
def test_unsafe_path_id_is_rejected_before_auth(mcp_servers_client: McpServersClient, which: str) -> None:
    path = (
        _path(UNSAFE_PATH_ID, MISSING_INSTANCE_ID) if which == "agentKey" else _path(MISSING_AGENT_KEY, UNSAFE_PATH_ID)
    )
    resp = mcp_servers_client.post(path, auth=False, json=API_TOKEN_BODY)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_agent_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.post(
        _path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, headers=narrow_scope_headers, json=API_TOKEN_BODY
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.post(_path(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=API_TOKEN_BODY, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
