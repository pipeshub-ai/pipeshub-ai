"""Strict OpenAPI audit of GET /api/v1/mcp-servers/my-mcp-servers."""

from __future__ import annotations

import pytest
import requests
from helper.second_user import SecondUser
from mcp_servers_audit_support import (
    FIXTURE_TOOL,
    JsonObject,
    McpServersClient,
    SeedMcpInstance,
    request_as,
)
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/mcp-servers/my-mcp-servers"
PATH = "/my-mcp-servers"
# Without this Python dials every authenticated instance in the org, other suites' included.
NO_TOOLS = {"includeTools": "false"}


def _own_entry(resp: requests.Response, instance_id: str) -> JsonObject:
    body = resp.json()
    assert set(body) == {"instances"}, body.keys()
    matches = [entry for entry in body["instances"] if entry.get("_id") == instance_id]
    assert len(matches) == 1, f"seeded instance {instance_id} listed {len(matches)} times"
    return matches[0]


def test_admin_gets_merged_view_of_instances(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    open_instance = seed_mcp_instance()
    token_instance = seed_mcp_instance(authMode="api_token")

    resp = mcp_servers_client.get(PATH, params=NO_TOOLS)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    open_entry = _own_entry(resp, open_instance["_id"])
    assert open_entry["name"] == open_instance["name"]
    assert open_entry["hasOAuthClientConfig"] is False
    assert open_entry["isAuthenticated"] is True
    assert open_entry["tools"] == []
    assert open_entry["toolsError"] is None

    # Nobody stored a token for it, so the caller is not authenticated against it.
    token_entry = _own_entry(resp, token_instance["_id"])
    assert token_entry["isAuthenticated"] is False
    assert token_entry["tools"] == []


def test_default_lists_live_tools_and_reports_unreachable_servers(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    mcp_fixture_url: str,
) -> None:
    reachable = seed_mcp_instance(url=mcp_fixture_url)
    unreachable = seed_mcp_instance()

    # includeTools defaults to true; each instance gets at most 8 s.
    resp = mcp_servers_client.get(PATH, timeout=120)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    live = _own_entry(resp, reachable["_id"])
    assert live["toolsError"] is None, live
    assert [t["name"] for t in live["tools"]] == [FIXTURE_TOOL]
    assert live["tools"][0]["inputSchema"]["required"] == ["order_id"]

    down = _own_entry(resp, unreachable["_id"])
    assert down["tools"] == []
    assert down["toolsError"], down


def test_member_gets_the_same_view(
    second_user: SecondUser,
    seed_mcp_instance: SeedMcpInstance,
) -> None:
    # Unlike GET /instances, Python does not admin-gate this route.
    instance = seed_mcp_instance()

    resp = request_as(second_user, "GET", PATH, params=NO_TOOLS)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _own_entry(resp, instance["_id"])["tools"] == []


@pytest.mark.parametrize("value", ["0", "no", "off"])
def test_include_tools_also_reads_other_false_spellings(
    mcp_servers_client: McpServersClient,
    seed_mcp_instance: SeedMcpInstance,
    mcp_fixture_url: str,
    value: str,
) -> None:
    instance = seed_mcp_instance(url=mcp_fixture_url)
    with outside_request_contract("FastAPI's bool parser also reads 0/1, yes/no and on/off"):
        resp = mcp_servers_client.get(PATH, params={"includeTools": value})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert _own_entry(resp, instance["_id"])["tools"] == []


def test_without_an_mcp_scope_is_forbidden(
    mcp_servers_client: McpServersClient, narrow_scope_headers: dict[str, str]
) -> None:
    resp = mcp_servers_client.get(PATH, params=NO_TOOLS, auth=False, headers=narrow_scope_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(mcp_servers_client: McpServersClient) -> None:
    resp = mcp_servers_client.get(PATH, params=NO_TOOLS, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_non_boolean_include_tools_is_unprocessable(
    mcp_servers_client: McpServersClient,
) -> None:
    # Node has no validator here; the value reaches FastAPI's bool query parser.
    resp = mcp_servers_client.get(PATH, params={"includeTools": "not-a-bool"})
    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
