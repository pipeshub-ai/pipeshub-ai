"""Strict OpenAPI audit of PUT /api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials."""

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

ROUTE = "/api/v1/mcp-servers/agents/:agentKey/instances/:instanceId/credentials"
HEADER_CREDENTIAL = {"headerName": "X-Spec-Audit", "headerValue": "spec-audit-value"}


def _credentials(agent_key: str, instance_id: str) -> str:
    return agent_path(agent_key, instance_id, "/credentials")


def test_put_agent_credentials_stores_a_header_credential(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    # Nothing is dialled: the record is only written to the config store.
    instance_id = seed_mcp_instance(authMode="headers")["_id"]
    path = _credentials(seed_agent(), instance_id)
    try:
        resp = mcp_servers_client.put(path, json=HEADER_CREDENTIAL)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json() == {"success": True, "isAuthenticated": True}
    finally:
        mcp_servers_client.delete(path)


def test_put_replaces_a_stored_api_token(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    path = _credentials(seed_agent(), instance_id)
    first = mcp_servers_client.put(path, json={"apiToken": "spec-audit-first"})
    assert first.status_code == 200, first.text[:500]
    resp = mcp_servers_client.put(path, json={"apiToken": "spec-audit-second"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("instance", "body"),
    [
        pytest.param({"authMode": "headers"}, {"headerName": "X-Only-A-Name"}, id="missing-header-value"),
        pytest.param({}, HEADER_CREDENTIAL, id="instance-needs-no-auth"),
        pytest.param(oauth_instance_body(), HEADER_CREDENTIAL, id="instance-uses-oauth"),
    ],
)
def test_put_refuses_what_the_auth_mode_does_not_take(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
    instance: JsonObject,
    body: JsonObject,
) -> None:
    instance_id = seed_mcp_instance(**instance)["_id"]
    resp = mcp_servers_client.put(_credentials(seed_agent(), instance_id), json=body)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_reads_snake_case_and_ignores_unknown_fields(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    instance_id = seed_mcp_instance(authMode="headers")["_id"]
    with outside_request_contract("the header credential is sent under snake_case names next to an unknown field"):
        resp = mcp_servers_client.put(
            _credentials(seed_agent(), instance_id),
            json={"header_name": "X-Spec", "header_value": "v", "remember": True},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_put_agent_credentials_for_a_regular_agent_is_bad_request(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    seed_agent: SeedAgent,
) -> None:
    # Per-agent credentials exist only for service-account agents.
    instance_id = seed_mcp_instance(authMode="headers")["_id"]
    agent_key = seed_agent(is_service_account=False)
    resp = mcp_servers_client.put(_credentials(agent_key, instance_id), json=HEADER_CREDENTIAL)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_cannot_edit_the_agent_is_forbidden(
    seed_mcp_instance: SeedMcpInstance, seed_agent: SeedAgent, second_user: SecondUser
) -> None:
    instance_id = seed_mcp_instance(authMode="headers")["_id"]
    resp = request_as(second_user, "PUT", _credentials(seed_agent(), instance_id), json=HEADER_CREDENTIAL)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_agent_credentials_for_an_unknown_agent_is_not_found(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=HEADER_CREDENTIAL
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_agent_credentials_for_an_unknown_instance_is_not_found(
    mcp_servers_client: McpServersClient, seed_agent: SeedAgent
) -> None:
    resp = mcp_servers_client.put(_credentials(seed_agent(), MISSING_INSTANCE_ID), json=HEADER_CREDENTIAL)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"apiToken": {"value": "x"}}, id="api-token-not-a-string"),
        pytest.param({"headerValue": ["v"]}, id="header-value-not-a-string"),
        pytest.param({"env": "SPEC_AUDIT_TOKEN=x"}, id="env-not-an-object"),
    ],
)
def test_put_agent_credentials_with_a_bad_body_is_unprocessable(
    mcp_servers_client: McpServersClient, body: JsonObject
) -> None:
    # Pydantic rejects the body before the agent or the instance is looked up.
    resp = mcp_servers_client.put(_credentials(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), json=body)
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_put_unsafe_agent_key_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.put(
        _credentials(UNSAFE_PATH_ID, MISSING_INSTANCE_ID), auth=False, json=HEADER_CREDENTIAL
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_without_an_agent_write_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_AGENT_KEY, MISSING_INSTANCE_ID),
        auth=False,
        headers=narrow_scope_headers,
        json=HEADER_CREDENTIAL,
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_put_agent_credentials_without_token_is_unauthorized(
    mcp_servers_client: McpServersClient,
) -> None:
    resp = mcp_servers_client.put(
        _credentials(MISSING_AGENT_KEY, MISSING_INSTANCE_ID), auth=False, json=HEADER_CREDENTIAL
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
