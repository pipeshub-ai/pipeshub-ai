"""`PipesHubAgentFactory.create()` — MCP server wiring (`MCPToolProvider`,
invoked right after `PipesHubToolLoader().load()` — see `factory.py`).
Verifies attached, authenticated MCP instances actually end up registered on
the shared `ToolRegistry`, grouped under `lazy_tools_wiring.MCP_PARENT`
("mcp") rather than `CONNECTORS_PARENT` ("connectors"), and counted toward
the lazy-tool-disclosure threshold like any other tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.lazy_tools_wiring import CONNECTORS_PARENT, MCP_PARENT
from app.agents.mcp.models import MCPToolInfo
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """See `test_factory_wiring.py`'s identical fixture for why: skills
    wiring would await real graph calls on `make_context`'s MagicMock
    `graph_provider` otherwise.

    MCP tool loading is gated on the `ENABLE_MCP` platform feature flag
    (defaults disabled, read via `config_service` — no env override exists
    anymore) — `_mcp_context()` below stubs `config_service.get_config` to
    report it enabled for these wiring tests.
    """
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


def _mcp_instance(instance_id: str = "inst-1", *, name: str = "JiraMCP", type_id: str = "jira_mcp") -> dict[str, Any]:
    return {
        "_id": instance_id, "orgId": "org-1", "createdBy": "user-1",
        "name": name, "typeId": type_id, "transport": "sse", "authMode": "none",
        "createdAt": 0, "updatedAt": 0,
    }


def _mcp_enabled_config_service() -> Any:
    """Stubs the `/services/platform/settings` read `is_mcp_enabled()` does
    via `read_platform_feature_flag()` so ENABLE_MCP resolves to True."""
    cfg = MagicMock()
    cfg.get_config = AsyncMock(return_value={"featureFlags": {"ENABLE_MCP": True}})
    return cfg


def _mcp_context(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "llm": FakeChatModel(),
        "config_service": _mcp_enabled_config_service(),
        "mcp_servers": [{"instanceId": "inst-1", "name": "JiraMCP", "displayName": "Jira MCP", "typeId": "jira_mcp"}],
        "mcp_server_configs": {
            "inst-1": {"instance": _mcp_instance(), "auth": {}, "ownerId": "user-1"},
        },
    }
    defaults.update(overrides)
    return make_context(**defaults)


async def _fake_discover_tools(config: Any, credentials: dict, timeout_seconds: float = 10.0) -> list[MCPToolInfo]:
    return [MCPToolInfo(
        name="search", namespaced_name="mcp_jira_mcp_search", description="Search Jira", input_schema={},
    )]


class TestMcpServerWiring:
    async def test_mcp_tool_registered_and_grouped_under_mcp_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.agents.agent_loop.mcp_tool_loader.discover_tools", _fake_discover_tools)
        context = _mcp_context()
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert runtime.tool_registry.has("mcp_jira_mcp_search")
        assert "mcp_jira_mcp_search" in agent.spec.tool_names

        mcp_children = {g.name for g in runtime.tool_registry.children_of(MCP_PARENT)}
        assert any(g.startswith("mcp_") for g in mcp_children)
        assert "mcp_jira_mcp_search" in runtime.tool_registry.tools_in_toolset(MCP_PARENT)

    async def test_mcp_group_never_nested_under_connectors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "0")
        monkeypatch.setattr("app.agents.agent_loop.mcp_tool_loader.discover_tools", _fake_discover_tools)
        context = _mcp_context()
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        connectors_children = {g.name for g in runtime.tool_registry.children_of(CONNECTORS_PARENT)}
        assert not any(g.startswith("mcp_") for g in connectors_children)

    async def test_no_attached_mcp_servers_is_a_noop(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert not any(g.name == MCP_PARENT for g in runtime.tool_registry.toolsets())

    async def test_discovery_failure_falls_back_to_attached_tool_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_args: Any, **_kwargs: Any) -> list[MCPToolInfo]:
            raise RuntimeError("connection refused")

        monkeypatch.setattr("app.agents.agent_loop.mcp_tool_loader.discover_tools", _boom)
        context = _mcp_context(mcp_servers=[{
            "instanceId": "inst-1", "name": "JiraMCP", "displayName": "Jira MCP", "typeId": "jira_mcp",
            "tools": [{"name": "search", "fullName": "mcp_jira_mcp_search", "description": "Search Jira"}],
        }])
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert runtime.tool_registry.has("mcp_jira_mcp_search")

    async def test_discovery_failure_with_no_fallback_records_load_failure(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The assistant/placeholder agent's auto-attached instances carry no
        stored `tools` selection (`attached_tools is None`) — a live
        discovery failure there has nothing to fall back to."""
        async def _boom(*_args: Any, **_kwargs: Any) -> list[MCPToolInfo]:
            raise RuntimeError("connection refused")

        monkeypatch.setattr("app.agents.agent_loop.mcp_tool_loader.discover_tools", _boom)
        context = _mcp_context()
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert not runtime.tool_registry.has("mcp_jira_mcp_search")
        assert context.mcp_tool_load_failures
        assert context.mcp_tool_load_failures[0]["instanceId"] == "inst-1"
