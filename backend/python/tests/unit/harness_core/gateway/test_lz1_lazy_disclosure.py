"""LZ1 (MCP-E2E §9): an engine of a large grant is shown the grant less its deferred tools, plus ``search_tools``
and ``call_tool``; ``call_tool`` is the direct call of the named tool (grant, approval, journal by its name)."""

from __future__ import annotations

import json

from harness_core.gateway.disclosure import (
    CALL_TOOL,
    SEARCH_TOOLS,
    LazyDisclosure,
    shown_tool,
)
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    GRANTED,
    EndpointWorld,
    build_endpoint,
    claims_for,
)

DEFERRED = frozenset({"search", "create", "delete"})


async def _rank(query: str, limit: int) -> list[str]:
    return ["delete", "search", "create"][:limit]


def _lazy(**options: object) -> EndpointWorld:
    return build_endpoint(disclosure=LazyDisclosure(DEFERRED, _rank), **options)  # type: ignore[arg-type]


async def test_lz1_the_list_is_the_grant_less_the_deferred_plus_the_two_disclosure_tools() -> None:
    world = _lazy()
    names = [t.name for t in await world.endpoint.list_tools(claims_for(BINDING))]
    assert names[-2:] == [SEARCH_TOOLS, CALL_TOOL]
    assert sorted(names[:-2]) == sorted(t.name for t in GRANTED if t.name not in DEFERRED)


async def test_lz1_nothing_changes_when_no_granted_tool_is_deferred() -> None:
    world = build_endpoint(disclosure=LazyDisclosure(frozenset({"ghost"}), _rank))
    names = sorted(t.name for t in await world.endpoint.list_tools(claims_for(BINDING)))
    assert names == sorted(t.name for t in GRANTED)
    refused = await world.endpoint.call_tool(claims_for(BINDING), CALL_TOOL, {"name": "search", "arguments": {}})
    assert refused.reason_code == "not_granted"


async def test_lz1_search_answers_only_granted_tools_with_their_schemas() -> None:
    world = _lazy()
    result = await world.endpoint.call_tool(claims_for(BINDING), SEARCH_TOOLS, {"query": "find things"})
    found = json.loads(result.text)
    assert [f["name"] for f in found] == ["search", "create"]
    assert found[0]["inputSchema"]["properties"]["q"]["type"] == "string"
    assert world.runs == []


async def test_lz1_call_tool_runs_the_named_tool_as_a_direct_call() -> None:
    world = _lazy()
    result = await world.endpoint.call_tool(claims_for(BINDING), CALL_TOOL, {"name": "search", "arguments": {"q": "x"}})
    assert result.disposition == "executed" and not result.is_error
    assert world.runs == ["search"]


async def test_lz1_call_tool_of_an_ungranted_tool_is_not_granted() -> None:
    world = _lazy()
    result = await world.endpoint.call_tool(claims_for(BINDING), CALL_TOOL, {"name": "delete", "arguments": {"q": "x"}})
    assert (result.disposition, result.reason_code) == ("refused", "not_granted")
    assert world.runs == []


async def test_lz1_a_write_through_call_tool_is_decided_under_the_real_tools_name() -> None:
    world = _lazy(approvals={"create": "deny"})
    result = await world.endpoint.call_tool(claims_for(BINDING), CALL_TOOL, {"name": "create", "arguments": {"q": "x"}})
    assert result.disposition == "denied" and world.runs == []


async def test_lz1_a_malformed_call_tool_is_refused_before_any_effect() -> None:
    world = _lazy()
    for arguments in ({}, {"name": CALL_TOOL}, {"name": "search", "arguments": "q=x"}):
        result = await world.endpoint.call_tool(claims_for(BINDING), CALL_TOOL, arguments)
        assert (result.is_error, result.disposition, result.reason_code) == (True, "refused", "invalid_disclosure_call")
    assert world.runs == []


async def test_lz1_the_engine_limit_counts_what_is_listed() -> None:
    listed = len([t for t in GRANTED if t.name not in DEFERRED]) + 2
    world = _lazy(max_tools=listed)
    assert len(await world.endpoint.list_tools(claims_for(BINDING))) == listed


def test_lz1_activity_names_the_tool_call_tool_called() -> None:
    assert shown_tool("mcp__tools__call_tool", {"name": "jira_create"}, prefix="mcp__tools__") == "jira_create"
    assert shown_tool("call_tool", {"name": "jira_create"}) == "jira_create"
    assert shown_tool("tools_search", {"name": "x"}, prefix="tools_") == "tools_search"
    assert shown_tool("call_tool", {}) == "call_tool"
