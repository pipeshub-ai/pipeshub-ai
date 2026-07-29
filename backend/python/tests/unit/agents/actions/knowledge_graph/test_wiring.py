"""Wiring tests for knowledge graph tools.

Verifies:
1. knowledgegraph is in _KNOWLEDGE_TOOLSETS (skipped when no knowledge)
2. knowledgegraph__* tools are NOT claimed by internal_exploration_agent
3. knowledgegraph__* tools remain in the top-level residual grant
4. knowledgegraph__* appear in every child agent's tool grant via shared_tool_names
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.domain_agents import (
    DOMAIN_AGENT_DEFINITIONS,
    register_domain_agents,
    plan_domain_agents,
    compose_domain_agents,
)
from app.agents.agent_loop.tool_loader import _KNOWLEDGE_TOOLSETS


# ---------------------------------------------------------------------------
# Verify _KNOWLEDGE_TOOLSETS contains knowledgegraph
# ---------------------------------------------------------------------------


class TestKnowledgeToolsetsGate:
    def test_knowledgegraph_in_knowledge_toolsets(self):
        assert "knowledgegraph" in _KNOWLEDGE_TOOLSETS

    def test_knowledgehub_still_in_knowledge_toolsets(self):
        assert "knowledgehub" in _KNOWLEDGE_TOOLSETS

    def test_retrieval_still_in_knowledge_toolsets(self):
        assert "retrieval" in _KNOWLEDGE_TOOLSETS


class TestKnowledgeToolsetsAreEssential:
    """The loader skips these entirely when no knowledge is attached, so when
    they DO load the knowledge is attached — and then they must survive lazy
    tool disclosure. `essential` is what `factory.py` turns into
    `AgentSpec.pinned_toolsets`; without it navigation and lookup sit behind a
    `fetch_tools` round trip the model has no reason to make, and nothing ever
    surfaces the Record IDs that make `dynamic_fetch_full_record` available."""

    def _toolset_class(self, toolset_name: str) -> type:
        from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
        from app.agents.actions.knowledge_hub.knowledge_hub import KnowledgeHub
        from app.agents.actions.retrieval.retrieval import Retrieval

        classes = {
            "knowledgegraph": KnowledgeGraph,
            "knowledgehub": KnowledgeHub,
            "retrieval": Retrieval,
        }
        assert toolset_name in classes, (
            f"{toolset_name} is gated by _KNOWLEDGE_TOOLSETS but this test does "
            "not know its class — add it so its essential flag stays covered"
        )
        return classes[toolset_name]

    @pytest.mark.parametrize("toolset", sorted(_KNOWLEDGE_TOOLSETS))
    def test_every_gated_knowledge_toolset_is_essential(self, toolset: str):
        metadata = self._toolset_class(toolset)._toolset_metadata
        assert metadata["essential"] is True


# ---------------------------------------------------------------------------
# Fake tool helpers
# ---------------------------------------------------------------------------


class FakeTool(Tool):
    def __init__(self, name: str, app_name: str | None = None) -> None:
        self._name = name
        self._app_name = app_name

    @property
    def app_name(self) -> str | None:
        return self._app_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return f"fake {self._name}"

    @property
    def description(self) -> str:
        return f"fake {self._name}"

    @property
    def path(self) -> str:
        return f"/fake/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    def validate(self, kwargs: dict[str, Any]) -> None:
        return

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data="ok")


def _registry_with_nav_tools() -> ToolRegistry:
    """Registry that includes the knowledge graph nav tools plus all standard domain tools."""
    registry = ToolRegistry()
    for tool in (
        FakeTool("run_code"),
        FakeTool("install_packages"),
        FakeTool("read_sandbox_file"),
        FakeTool("dynamic__web_search", app_name="dynamic"),
        FakeTool("dynamic__fetch_url", app_name="dynamic"),
        FakeTool("retrieval_search", app_name="retrieval"),
        FakeTool("knowledgehub_list", app_name="knowledgehub"),
        FakeTool("dynamic_fetch_full_record", app_name="dynamic"),
        FakeTool("calculator_evaluate", app_name="calculator"),
        FakeTool("date_calculator_get_exclusion_dates", app_name="date_calculator"),
        FakeTool("google_calendar_list_events", app_name="google_calendar"),
        FakeTool("jira_search_issues", app_name="jira"),
        # navigation tools — app_name=knowledgegraph, NOT in any domain's app_names set
        FakeTool("knowledgegraph__navigate", app_name="knowledgegraph"),
        FakeTool("knowledgegraph__lookup_record", app_name="knowledgegraph"),
    ):
        registry.register_tool(tool)
    return registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNavigationToolsNotClaimedByExplorationAgent:
    def test_nav_tools_not_claimed_by_exploration_agent(self):
        registry = _registry_with_nav_tools()
        plan = plan_domain_agents(registry, DOMAIN_AGENT_DEFINITIONS)

        exploration_claims = plan.claims.get("internal_exploration_agent", [])
        assert "knowledgegraph__navigate" not in exploration_claims
        assert "knowledgegraph__lookup_record" not in exploration_claims

    def test_nav_tools_not_claimed_by_any_domain_agent(self):
        registry = _registry_with_nav_tools()
        plan = plan_domain_agents(registry, DOMAIN_AGENT_DEFINITIONS)

        all_claimed = {n for names in plan.claims.values() for n in names}
        assert "knowledgegraph__navigate" not in all_claimed
        assert "knowledgegraph__lookup_record" not in all_claimed


class TestNavigationToolsInTopLevelResidual:
    def test_nav_tools_remain_at_top_level(self):
        registry = _registry_with_nav_tools()
        runtime = AgentRuntime(tool_registry=registry)
        context = MagicMock()
        context.agent_knowledge = []
        context.connector_configs = {}

        top_names = compose_domain_agents(
            registry, runtime, context,
            provider="scripted",
            model_name="scripted-model",
        )

        assert "knowledgegraph__navigate" in top_names
        assert "knowledgegraph__lookup_record" in top_names


class TestNavigationToolsSharedWithChildren:
    def test_nav_tools_in_every_child_via_shared_tool_names(self):
        registry = _registry_with_nav_tools()
        runtime = AgentRuntime(tool_registry=registry)
        context = MagicMock()
        context.agent_knowledge = []
        context.connector_configs = {}

        nav_tool_names = frozenset({
            "knowledgegraph__navigate",
            "knowledgegraph__lookup_record",
        })

        register_domain_agents(
            plan_domain_agents(registry, DOMAIN_AGENT_DEFINITIONS),
            registry,
            runtime,
            context,
            provider="scripted",
            model_name="scripted-model",
            shared_tool_names=nav_tool_names,
        )

        # Every domain agent that got built should have the nav tools
        for domain_def in DOMAIN_AGENT_DEFINITIONS:
            agent_tool = registry.resolve_by_name(domain_def.name) if registry.has(domain_def.name) else None
            if agent_tool is None:
                continue
            spec = agent_tool._spec
            for nav_name in nav_tool_names:
                if nav_name in registry.names():
                    assert nav_name in spec.tool_names, (
                        f"{domain_def.name} should have {nav_name} in its tool_names via shared_tool_names"
                    )
