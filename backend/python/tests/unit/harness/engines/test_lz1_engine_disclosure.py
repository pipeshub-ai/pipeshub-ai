"""LZ1: which of an engine's granted tools are found with ``search_tools`` instead of listed, and when."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.harness.engines.durable import engine_disclosure
from tests.unit.agent_loop_lib.tools.test_registry import _SimpleTool

MCP = [f"jira_mcp_tool_{i}" for i in range(60)]


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("search_knowledge", "create_artifact", "jira_create_issue", "send_message", *MCP):
        registry.register_tool(_SimpleTool(name))
    registry.register_toolset("retrieval", "Knowledge.", ["search_knowledge"])
    registry.register_toolset("jira", "Jira.", ["jira_create_issue"])
    registry.register_toolset("mcp", "MCP servers.", [])
    registry.register_toolset("mcp_jira", "Jira MCP.", MCP, parent="mcp")
    return registry


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PIPESHUB_ENABLE_LAZY_TOOLS", "PIPESHUB_ENGINE_LAZY_TOOLS_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)


async def test_a_large_grant_defers_its_connector_and_mcp_tools_and_ranks_only_the_grant() -> None:
    registry = _registry()
    granted = ["search_knowledge", "create_artifact", "jira_create_issue", *MCP[:55]]
    disclosure = engine_disclosure(registry, granted, ["retrieval"])
    assert disclosure is not None
    assert disclosure.deferred == frozenset({"jira_create_issue", *MCP[:55]})
    ranked = (await disclosure.rank("jira mcp tool", 100)).names
    assert ranked and set(ranked) <= set(granted) and MCP[59] not in ranked


def test_at_or_below_the_threshold_or_switched_off_every_tool_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry()
    assert engine_disclosure(registry, ["search_knowledge", *MCP[:49]], ["retrieval"]) is None
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    assert engine_disclosure(registry, ["search_knowledge", *MCP], ["retrieval"]) is None


def test_a_large_grant_of_essential_and_ungrouped_tools_only_is_listed_whole() -> None:
    registry = _registry()
    for i in range(60):
        registry.register_tool(_SimpleTool(f"builtin_{i}"))
    assert engine_disclosure(registry, ["search_knowledge", *(f"builtin_{i}" for i in range(60))], ["retrieval"]) is None
