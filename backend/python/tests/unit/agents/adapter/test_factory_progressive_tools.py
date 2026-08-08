"""Bug 4 (progressive tool injection): `PipesHubAgentFactory.create()` must
exclude `knowledgegraph__resolve_entity_filters`,
`knowledgegraph__find_records_by_entity`, `knowledgegraph__expand_neighbors`,
and `knowledgegraph__get_relationships` from the top-level agent's initial
tool grant even though the toolset they belong to is essential, while
leaving every other tool on that same toolset (e.g. `search`,
`search_entities`) granted as usual. The `progressive_entity_tools`
POST_TOOL_USE hook (re-granting them after first knowledge use) is covered
separately in `test_progressive_entity_tools.py`; this file only pins the
factory-side exclusion + hook-wiring contract. See
`hooks/progressive_tools.py`'s module docstring for the full rationale.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks.progressive_tools import (
    PROGRESSIVE_ENTITY_TOOL_NAME,
    PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME,
    PROGRESSIVE_FIND_RECORDS_TOOL_NAME,
    PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME,
)
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


class _FakeKnowledgeGraphToolset:
    """Minimal stand-in for the real `KnowledgeGraph` action class (see
    `knowledge_graph.py`) — just enough `@tool`-decorated methods for
    `ToolsetBuilder`/`PipesHubToolLoader` to discover, registered essential
    like the real toolset."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = state

    @tool(
        path="/tools/knowledgegraph/search",
        short_description="Search",
        description="Semantic search over indexed knowledge",
    )
    async def search(self, query: str) -> str:
        return "ok"

    @tool(
        path="/tools/knowledgegraph/resolve_entity_filters",
        short_description="Resolve entities",
        description="Resolve a department/topic/category mention to entity IDs",
    )
    async def resolve_entity_filters(self, query: str) -> str:
        return "ok"

    @tool(
        path="/tools/knowledgegraph/search_entities",
        short_description="Search entities",
        description="Essential-tier semantic entity lookup",
    )
    async def search_entities(self, query: str) -> str:
        return "ok"

    @tool(
        path="/tools/knowledgegraph/find_records_by_entity",
        short_description="Find records by entity",
        description="Progressive-tier entity-to-record traversal",
    )
    async def find_records_by_entity(self, entity_id: str, entity_type: str) -> str:
        return "ok"

    @tool(
        path="/tools/knowledgegraph/expand_neighbors",
        short_description="Expand entity neighbors",
        description="Progressive-tier full 1-hop entity neighborhood",
    )
    async def expand_neighbors(self, entity_id: str, entity_type: str) -> str:
        return "ok"

    @tool(
        path="/tools/knowledgegraph/get_relationships",
        short_description="Get entity relationships",
        description="Progressive-tier pairwise entity connection",
    )
    async def get_relationships(
        self, source_entity_id: str, source_entity_type: str,
        target_entity_id: str, target_entity_type: str,
    ) -> str:
        return "ok"


def _patch_knowledgegraph_toolset_registry() -> tuple[Any, Any]:
    fake_registry = MagicMock()
    fake_registry.get_all_toolsets.return_value = {
        "knowledgegraph": {
            "class": _FakeKnowledgeGraphToolset,
            "isInternal": True,
            "description": "Knowledge graph",
            "essential": True,
        },
    }
    return (
        patch(
            "app.agents.registry.toolset_registry.get_toolset_registry",
            return_value=fake_registry,
        ),
        patch(
            "app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory",
            return_value=None,
        ),
    )


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same rationale as `test_factory_wiring.py`'s fixture of the same
    name: these tests assert grant/wiring shape only, and skills would
    otherwise await real graph calls on the mocked context."""
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


@pytest.mark.asyncio
class TestProgressiveEntityToolExclusion:
    async def test_resolve_entity_filters_excluded_from_initial_grant(self) -> None:
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello",
            )

        assert runtime.tool_registry.has(PROGRESSIVE_ENTITY_TOOL_NAME)
        assert PROGRESSIVE_ENTITY_TOOL_NAME not in agent.spec.tool_names
        assert "knowledgegraph__search" in agent.spec.tool_names

    async def test_find_records_by_entity_excluded_from_initial_grant(self) -> None:
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello",
            )

        assert runtime.tool_registry.has(PROGRESSIVE_FIND_RECORDS_TOOL_NAME)
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME not in agent.spec.tool_names

    async def test_expand_neighbors_excluded_from_initial_grant(self) -> None:
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello",
            )

        assert runtime.tool_registry.has(PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME)
        assert PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME not in agent.spec.tool_names

    async def test_get_relationships_excluded_from_initial_grant(self) -> None:
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello",
            )

        assert runtime.tool_registry.has(PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME)
        assert PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME not in agent.spec.tool_names

    async def test_essential_entity_tools_are_not_excluded(self) -> None:
        """search_entities is essential (turn-0) — only
        resolve_entity_filters/find_records_by_entity are deferred."""
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            agent, _runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello",
            )

        assert "knowledgegraph__search_entities" in agent.spec.tool_names

    async def test_deep_mode_orchestrator_top_level_unaffected(self) -> None:
        """Deep mode's own grant is always just the four coordination
        tools — the exclusion is a no-op there (nothing to exclude), not a
        crash or an accidental narrowing of the coordination set."""
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            agent, _runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "deep", query="hello",
            )

        assert PROGRESSIVE_ENTITY_TOOL_NAME not in agent.spec.tool_names
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME not in agent.spec.tool_names
        assert PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME not in agent.spec.tool_names
        assert PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME not in agent.spec.tool_names

    async def test_root_agent_spec_is_the_mutable_spec_the_hook_targets(self) -> None:
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            agent, _runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello",
            )

        assert context.root_agent_spec is agent.spec

    async def test_progressive_entity_tools_hook_registered_on_post_tool_use(self) -> None:
        context = make_context(llm=FakeChatModel(), has_knowledge=True)
        registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
        with registry_patch, factory_patch:
            _agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello",
            )

        # No public "is X registered" query on Pipeline — same approach
        # test_factory_wiring.py uses for its own hook-count assertions.
        assert len(runtime.hooks.on(HookEvent.POST_TOOL_USE)._stack) >= 5
