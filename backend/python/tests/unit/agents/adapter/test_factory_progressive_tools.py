"""`PipesHubAgentFactory.create()` entity-tool grant: `search_entities` is
granted from the first turn, `find_records_by_entity` starts hidden until
`search_entities` runs (or an earlier turn used an entity tool), and both are
hidden when no entity vector store is wired. The unlock hook itself is
covered in `test_progressive_entity_tools.py`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks.progressive_tools import (
    PROGRESSIVE_FIND_RECORDS_TOOL_NAME,
    SEARCH_ENTITIES_TOOL_NAME,
)
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
    mode: str = "quick", *, with_store: bool = True, history: list | None = None,
) -> tuple[Any, Any, Any]:
    context = make_context(
        llm=FakeChatModel(), has_knowledge=True, previous_conversations=history or [],
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
    async def test_search_entities_granted_find_records_deferred(self) -> None:
        _context, agent, runtime = await _create()

        assert SEARCH_ENTITIES_TOOL_NAME in agent.spec.tool_names
        assert runtime.tool_registry.has(PROGRESSIVE_FIND_RECORDS_TOOL_NAME)
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME not in agent.spec.tool_names
        assert "knowledgegraph__search" in agent.spec.tool_names

    async def test_entity_tools_hidden_without_entity_store(self) -> None:
        _context, agent, _runtime = await _create(with_store=False)

        assert SEARCH_ENTITIES_TOOL_NAME not in agent.spec.tool_names
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME not in agent.spec.tool_names
        assert "knowledgegraph__search" in agent.spec.tool_names

    async def test_find_records_granted_when_history_used_entity_tools(self) -> None:
        history = [
            {"role": "user_query", "content": "what's tagged legal?"},
            {"role": "bot_response", "content": "…", "tool_results": [
                {"tool_name": SEARCH_ENTITIES_TOOL_NAME, "result": "{}"},
            ]},
        ]
        _context, agent, _runtime = await _create(history=history)

        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME in agent.spec.tool_names

    async def test_deep_mode_orchestrator_gets_no_entity_tools(self) -> None:
        _context, agent, _runtime = await _create("deep")

        assert SEARCH_ENTITIES_TOOL_NAME not in agent.spec.tool_names
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME not in agent.spec.tool_names

    async def test_root_agent_spec_is_the_mutable_spec_the_hook_targets(self) -> None:
        context, agent, _runtime = await _create()

        assert context.root_agent_spec is agent.spec

    async def test_progressive_entity_tools_hook_registered_on_post_tool_use(self) -> None:
        _context, _agent, runtime = await _create()

        # No public "is X registered" query on Pipeline — same approach
        # test_factory_wiring.py uses for its own hook-count assertions.
        assert len(runtime.hooks.on(HookEvent.POST_TOOL_USE)._stack) >= 5
