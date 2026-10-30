"""`PipesHubAgentFactory.create()` entity-tool grant: `search_entities` and
`find_records_by_entity` are granted together from the first turn when an
entity vector store is wired, and both are hidden when it is not.
`find_records_by_entity` takes the ids `search_by_entities` prints too, so it
is never held back for `search_entities`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.knowledge_graph.entity_search_surface import (
    FIND_RECORDS_BY_ENTITY_TOOL_NAME,
    SEARCH_ENTITIES_TOOL_NAME,
)
from app.agents.agent_loop.factory import PipesHubAgentFactory
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


class _FakeKnowledgeGraphToolset:
    """Minimal stand-in for the real `KnowledgeGraph` action class."""

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
        path="/tools/knowledgegraph/search_entities",
        short_description="Search entities",
        description="Entity lookup",
    )
    async def search_entities(self, query: str) -> str:
        return "ok"

    @tool(
        path="/tools/knowledgegraph/find_records_by_entity",
        short_description="Find records by entity",
        description="Entity-to-record listing",
    )
    async def find_records_by_entity(self, entity_id: str) -> str:
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
    name: these tests assert grant/wiring shape only."""
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


async def _create(
    mode: str = "quick", *, with_store: bool = True,
) -> tuple[Any, Any, Any]:
    context = make_context(
        llm=FakeChatModel(), has_knowledge=True,
    )
    if with_store:
        context.tool_state["entity_vector_store"] = MagicMock()
    registry_patch, factory_patch = _patch_knowledgegraph_toolset_registry()
    with registry_patch, factory_patch:
        agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
            context, context.llm, mode, query="hello",
        )
    return context, agent, runtime


@pytest.mark.asyncio
class TestEntityToolGrant:
    async def test_both_entity_tools_are_granted_on_the_first_turn(self) -> None:
        _context, agent, runtime = await _create()

        assert SEARCH_ENTITIES_TOOL_NAME in agent.spec.tool_names
        assert FIND_RECORDS_BY_ENTITY_TOOL_NAME in agent.spec.tool_names

    async def test_entity_tools_hidden_without_entity_store(self) -> None:
        _context, agent, _runtime = await _create(with_store=False)

        assert SEARCH_ENTITIES_TOOL_NAME not in agent.spec.tool_names
        assert FIND_RECORDS_BY_ENTITY_TOOL_NAME not in agent.spec.tool_names
        assert "knowledgegraph__search" in agent.spec.tool_names

    async def test_deep_mode_orchestrator_gets_no_entity_tools(self) -> None:
        _context, agent, _runtime = await _create("deep")

        assert SEARCH_ENTITIES_TOOL_NAME not in agent.spec.tool_names
        assert FIND_RECORDS_BY_ENTITY_TOOL_NAME not in agent.spec.tool_names

    async def test_deep_mode_spawn_pool_gets_both_entity_tools(self) -> None:
        """Sub-agents it spawns used to get find_records_by_entity only from the removed hook."""
        with patch("app.agents.agent_loop.factory.domain_spec_factory") as spec_factory:
            await _create("deep")

        granted = spec_factory.call_args.kwargs["default_tool_names"]
        assert SEARCH_ENTITIES_TOOL_NAME in granted and FIND_RECORDS_BY_ENTITY_TOOL_NAME in granted
