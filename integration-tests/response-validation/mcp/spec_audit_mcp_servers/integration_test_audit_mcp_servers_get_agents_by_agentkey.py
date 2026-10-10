"""Strict OpenAPI audit of GET /api/v1/mcp-servers/agents/:agentKey."""

from __future__ import annotations

import pytest
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    FIXTURE_TOOL,
    MISSING_AGENT_KEY,
    UNSAFE_PATH_ID,
    McpServersClient,
    SeedAgent,
    SeedMcpInstance,
    agent_path,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/agents/:agentKey"
# Tool discovery dials every authenticated instance in the org; keep it off.
NO_TOOLS = {"includeTools": "false"}


def _entry(resp_json: dict, instance_id: str) -> dict:
    mine = [i for i in resp_json["instances"] if i["_id"] == instance_id]
    assert len(mine) == 1, resp_json
    return mine[0]


def test_owner_lists_agent_mcp_servers(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    seeded = seed_mcp_instance()
    agent_key = seed_agent()

    resp = mcp_servers_client.get(agent_path(agent_key), params=NO_TOOLS)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    entry = _entry(resp.json(), seeded["_id"])
    assert entry["name"] == seeded["name"]
    assert entry["hasOAuthClientConfig"] is False
    # authMode "none" needs no credential, so it counts as authenticated for any owner.
    assert entry["isAuthenticated"] is True
    assert entry["tools"] == []
    assert entry["toolsError"] is None


def test_auth_status_is_the_agents_own(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    instance_id = seed_mcp_instance(authMode="api_token")["_id"]
    agent_key = seed_agent()
    # The admin's own credential does not count for the agent.
    mine = mcp_servers_client.post(f"/instances/{instance_id}/authenticate", json={"apiToken": "spec-audit"})
    assert mine.status_code == 200, mine.text[:500]

    before = mcp_servers_client.get(agent_path(agent_key), params=NO_TOOLS)
    assert _entry(before.json(), instance_id)["isAuthenticated"] is False

    stored = mcp_servers_client.post(agent_path(agent_key, instance_id, "/authenticate"), json={"apiToken": "spec-audit"})
    assert stored.status_code == 200, stored.text[:500]
    after = mcp_servers_client.get(agent_path(agent_key), params=NO_TOOLS)
    assert after.status_code == 200, after.text[:500]
    assert_strict_openapi_exchange(after, ROUTE)
    assert _entry(after.json(), instance_id)["isAuthenticated"] is True


def test_include_tools_lists_live_tools(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
    mcp_fixture_url: str,
) -> None:
    instance_id = seed_mcp_instance(url=mcp_fixture_url)["_id"]
    agent_key = seed_agent()

    resp = mcp_servers_client.get(agent_path(agent_key), params={"includeTools": "true"}, timeout=120)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    entry = _entry(resp.json(), instance_id)
    assert [t["name"] for t in entry["tools"]] == [FIXTURE_TOOL]
    assert entry["toolsError"] is None


@pytest.mark.parametrize("spelling", ["0", "no", "off"])
def test_include_tools_also_reads_other_false_spellings(
    mcp_servers_client: McpServersClient,
    seed_agent: SeedAgent,
    seed_mcp_instance: SeedMcpInstance,
    mcp_fixture_url: str,
    spelling: str,
) -> None:
    instance_id = seed_mcp_instance(url=mcp_fixture_url)["_id"]
    agent_key = seed_agent()
    with outside_request_contract(f"includeTools={spelling} is not a JSON boolean but FastAPI reads it as one"):
        resp = mcp_servers_client.get(agent_path(agent_key), params={"includeTools": spelling})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _entry(resp.json(), instance_id)["tools"] == []


def test_unknown_query_parameter_is_dropped(
    mcp_servers_client: McpServersClient, seed_agent: SeedAgent
) -> None:
    agent_key = seed_agent()
    with outside_request_contract("sends a query parameter the route does not define"):
        resp = mcp_servers_client.get(agent_path(agent_key), params={**NO_TOOLS, "page": "2"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_member_who_can_view_a_shared_agent_lists_it(
    second_user: SecondUser,
    seed_agent: SeedAgent,
) -> None:
    # A service-account agent is shared with the whole org, so view access is enough.
    agent_key = seed_agent()
    resp = request_as(second_user, "GET", agent_path(agent_key), params=NO_TOOLS)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_agent_is_not_found(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(agent_path(MISSING_AGENT_KEY), params=NO_TOOLS)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_without_agent_access_gets_not_found(
    second_user: SecondUser,
    seed_agent: SeedAgent,
) -> None:
    # Not admin-gated: the gate is view access to the agent, and no access is reported as 404.
    agent_key = seed_agent(False)

    resp = request_as(second_user, "GET", agent_path(agent_key), params=NO_TOOLS)
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_non_boolean_include_tools_is_unprocessable(
    mcp_servers_client: McpServersClient,
) -> None:
    # FastAPI validates the query before the handler looks the agent up.
    resp = mcp_servers_client.get(
        agent_path(MISSING_AGENT_KEY), params={"includeTools": "not-a-bool"}
    )
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


def test_unsafe_agent_key_is_rejected_before_auth(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(agent_path(UNSAFE_PATH_ID), auth=False)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_an_agent_read_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(agent_path(MISSING_AGENT_KEY), auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(agent_path(MISSING_AGENT_KEY), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
