"""`MCPToolProvider` (`app/agents/agent_loop/mcp_tool_loader.py`) — the MCP
analog of `PipesHubToolLoader.load()`: live discovery per attached instance,
fallback to the graph node's stored tool list on a discovery failure, one
`register_toolset` group per instance nested under `MCP_PARENT`, and
soft-skip failures recorded on `context.mcp_tool_load_failures`."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import mcp_tool_loader as mcp_tool_loader_module
from app.agents.agent_loop.lazy_tools_wiring import MCP_PARENT
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp.models import MCPToolInfo
from tests.unit.agents.adapter.conftest import make_context


def _instance(instance_id: str = "inst-1", *, type_id: str = "jira_mcp") -> dict[str, Any]:
    return {
        "_id": instance_id, "orgId": "org-1", "createdBy": "user-1",
        "name": "JiraMCP", "typeId": type_id, "transport": "sse", "authMode": "none",
        "createdAt": 0, "updatedAt": 0,
    }


def _context_with_server(
    *, instance_id: str = "inst-1", name: str = "JiraMCP", attached_tools: list[dict] | None = None,
    type_id: str = "jira_mcp",
) -> Any:
    mcp_server: dict[str, Any] = {"instanceId": instance_id, "name": name, "displayName": name, "typeId": type_id}
    if attached_tools is not None:
        mcp_server["tools"] = attached_tools
    return make_context(
        mcp_servers=[mcp_server],
        mcp_server_configs={
            instance_id: {"instance": _instance(instance_id, type_id=type_id), "auth": {}, "ownerId": "user-1"},
        },
    )


async def _discover_ok(config: Any, credentials: dict, timeout_seconds: float = 10.0) -> list[MCPToolInfo]:
    return [MCPToolInfo(
        name="search", namespaced_name=f"mcp_{config.name.lower()}_search",
        description="Search", input_schema={},
    )]


async def _discover_fails(config: Any, credentials: dict, timeout_seconds: float = 10.0) -> list[MCPToolInfo]:
    raise RuntimeError("connection refused")


class TestLoadInto:
    async def test_registers_discovered_tool_and_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover_ok)
        registry = ToolRegistry()
        context = _context_with_server()

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jiramcp_search")
        assert registry.tools_in_toolset(MCP_PARENT) == ["mcp_jiramcp_search"]
        group = next(g for g in registry.toolsets() if g.name == "mcp_jiramcp")
        assert group.parent == MCP_PARENT
        assert context.mcp_tool_load_failures == []

    async def test_no_attached_servers_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(
            mcp_tool_loader_module, "discover_tools",
            lambda *a, **k: calls.append(1) or _discover_ok(*a, **k),
        )
        registry = ToolRegistry()
        context = make_context()

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == []
        assert calls == []
        assert not any(g.name == MCP_PARENT for g in registry.toolsets())

    async def test_discovery_failure_falls_back_to_attached_tool_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover_fails)
        registry = ToolRegistry()
        context = _context_with_server(attached_tools=[
            {"name": "search", "fullName": "mcp_jira_mcp_search", "description": "Search Jira"},
        ])

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jira_mcp_search")
        assert context.mcp_tool_load_failures == []

    async def test_discovery_failure_with_no_fallback_records_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover_fails)
        registry = ToolRegistry()
        context = _context_with_server()  # attached_tools is None — no stored selection to fall back to

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == []
        assert context.mcp_tool_load_failures == [
            {"instanceId": "inst-1", "name": "JiraMCP", "reason": "discovery_failed"},
        ]
        assert not any(g.name == MCP_PARENT for g in registry.toolsets())

    async def test_empty_attached_tool_list_is_also_no_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover_fails)
        registry = ToolRegistry()
        context = _context_with_server(attached_tools=[])

        await MCPToolProvider().load_into(registry, context)

        assert registry.names() == []
        assert context.mcp_tool_load_failures[0]["reason"] == "discovery_failed"

    async def test_two_instances_load_independently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0) -> list[MCPToolInfo]:
            if config.id == "inst-1":
                return [MCPToolInfo(name="search", namespaced_name="mcp_jira_search", input_schema={})]
            raise RuntimeError("down")

        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover)
        registry = ToolRegistry()
        context = make_context(
            mcp_servers=[
                {"instanceId": "inst-1", "name": "JiraMCP", "typeId": "jira_mcp"},
                {"instanceId": "inst-2", "name": "SlackMCP", "typeId": "slack_mcp"},
            ],
            mcp_server_configs={
                "inst-1": {"instance": _instance("inst-1", type_id="jira_mcp"), "auth": {}, "ownerId": "user-1"},
                "inst-2": {"instance": _instance("inst-2", type_id="slack_mcp"), "auth": {}, "ownerId": "user-1"},
            },
        )

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jira_search")
        assert context.mcp_tool_load_failures == [
            {"instanceId": "inst-2", "name": "SlackMCP", "reason": "discovery_failed"},
        ]
        assert any(g.name == MCP_PARENT for g in registry.toolsets())

    async def test_colliding_tool_name_is_skipped_but_group_still_registers_survivors(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(name="search", namespaced_name="mcp_jira_search", input_schema={}),
                MCPToolInfo(name="create", namespaced_name="mcp_jira_create", input_schema={}),
            ]

        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover)
        registry = ToolRegistry()
        # Pre-register a colliding name to force one of the two discovered
        # tools to be skipped as a duplicate.
        from app.agent_loop_lib.tools.base import ToolOutput, ToolParameter

        class _Existing:
            name = "mcp_jira_search"
            short_description = "existing"
            description = "existing"
            path = "/existing/mcp_jira_search"
            parameters: list[ToolParameter] = []

            async def execute(self, **kwargs: Any) -> ToolOutput:
                return ToolOutput(success=True, data="existing")

        registry.register_tool(_Existing())
        context = _context_with_server()

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jira_create")
        assert registry.path_for_name("mcp_jira_search") == "/existing/mcp_jira_search"
        assert context.mcp_tool_load_failures == []

    async def test_all_discovered_tools_colliding_records_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0) -> list[MCPToolInfo]:
            return [MCPToolInfo(name="search", namespaced_name="mcp_jira_search", input_schema={})]

        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover)
        registry = ToolRegistry()

        from app.agent_loop_lib.tools.base import ToolOutput, ToolParameter

        class _Existing:
            name = "mcp_jira_search"
            short_description = "existing"
            description = "existing"
            path = "/existing/mcp_jira_search"
            parameters: list[ToolParameter] = []

            async def execute(self, **kwargs: Any) -> ToolOutput:
                return ToolOutput(success=True, data="existing")

        registry.register_tool(_Existing())
        context = _context_with_server()

        await MCPToolProvider().load_into(registry, context)

        assert context.mcp_tool_load_failures
        assert context.mcp_tool_load_failures[0]["instanceId"] == "inst-1"
        assert context.mcp_tool_load_failures[0]["reason"] == "error"

    async def test_discovery_respects_attached_tools_selection(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _discover(config: Any, credentials: dict, timeout_seconds: float = 10.0) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(name="search", namespaced_name="mcp_jira_mcp_search", input_schema={}),
                MCPToolInfo(name="create", namespaced_name="mcp_jira_mcp_create", input_schema={}),
            ]

        monkeypatch.setattr(mcp_tool_loader_module, "discover_tools", _discover)
        registry = ToolRegistry()
        context = _context_with_server(attached_tools=[
            {"name": "search", "fullName": "mcp_jira_mcp_search"},
        ])

        await MCPToolProvider().load_into(registry, context)

        assert registry.has("mcp_jira_mcp_search")
        assert not registry.has("mcp_jira_mcp_create")
        assert context.mcp_tool_load_failures == []


class TestFilterByAttached:
    def test_none_attached_tools_keeps_all_discovered(self) -> None:
        from app.agents.agent_loop.mcp_access import ResolvedMCPServer

        server = ResolvedMCPServer(
            instance_id="inst-1", name="JiraMCP", display_name="JiraMCP",
            instance=_instance(type_id="jira_mcp"), auth={}, owner_id="user-1",
            attached_tools=None,
        )
        discovered = [
            MCPToolInfo(name="search", namespaced_name="mcp_jira_mcp_search", input_schema={}),
            MCPToolInfo(name="create", namespaced_name="mcp_jira_mcp_create", input_schema={}),
        ]
        assert MCPToolProvider._filter_by_attached(server, discovered) == discovered

    def test_attached_tools_keeps_only_selected(self) -> None:
        from app.agents.agent_loop.mcp_access import ResolvedMCPServer

        server = ResolvedMCPServer(
            instance_id="inst-1", name="JiraMCP", display_name="JiraMCP",
            instance=_instance(type_id="jira_mcp"), auth={}, owner_id="user-1",
            attached_tools=[{"name": "search", "fullName": "mcp_jira_mcp_search"}],
        )
        discovered = [
            MCPToolInfo(name="search", namespaced_name="mcp_jira_mcp_search", input_schema={}),
            MCPToolInfo(name="create", namespaced_name="mcp_jira_mcp_create", input_schema={}),
        ]
        filtered = MCPToolProvider._filter_by_attached(server, discovered)
        assert [t.namespaced_name for t in filtered] == ["mcp_jira_mcp_search"]
