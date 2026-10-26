"""Retrieval-side accuracy switches leave the cached request prefix alone.

A switch that changes how search ranks or cuts hits must not reach the system
prompt or the tool list: either would split every deployment's provider cache
by switch state, or worse, by question. For each chat mode, the stable prompt
block and the tool schemas (those shown on the first turn, every registered
one, and the knowledge search tools') must be identical across two different
questions, with each switch off and on.
"""

from __future__ import annotations

import inspect

import pytest

from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.retrieval.retrieval import Retrieval
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.modules.retrieval.record_cut import FUSION_RECORD_CUT_ENV_VAR
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

_RETRIEVAL_SWITCHES = (FUSION_RECORD_CUT_ENV_VAR,)
_MODES = ("quick", "react", "planExecute", "deep")
_QUESTIONS = (
    Goal(description="Who owns HarborBank?"),
    Goal(
        description="Compare Q3 revenue across regions",
        requirements=["Use the finance reports"],
        success_criteria=["Every region named"],
    ),
)


@pytest.fixture(autouse=True)
def _quiet_subsystems(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)


def _knowledge_tool_schemas() -> list[dict]:
    """The search tools' own schemas, which the factory loads lazily."""
    registry = ToolRegistry()
    for toolset in (KnowledgeGraph, Retrieval):
        for _, fn in inspect.getmembers(toolset, predicate=inspect.isfunction):
            meta = getattr(fn, TOOL_META_ATTR, None)
            if meta is not None:
                registry.register_tool(BoundMethodTool(fn, meta))
    return [schema.model_dump() for schema in registry.schemas(None)]


async def _prefix(mode: str, goal: Goal) -> tuple[str, list[dict], list[dict], list[dict]]:
    context = make_context(llm=FakeChatModel(), timezone="Asia/Kolkata", has_knowledge=True)
    agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
        context, context.llm, mode, query=goal.description
    )
    stable, _volatile = agent.spec.system_prompt.build_blocks(agent.spec, runtime, goal, [], {})
    shown = [schema.model_dump() for schema in tool_schemas_for_turn(agent, agent.spec, runtime)]
    registered = [schema.model_dump() for schema in runtime.tool_registry.schemas(None)]
    return stable, shown, registered, _knowledge_tool_schemas()


@pytest.mark.parametrize("switch", _RETRIEVAL_SWITCHES)
@pytest.mark.parametrize("mode", _MODES)
async def test_the_prefix_is_the_same_for_every_question_and_switch_state(
    monkeypatch: pytest.MonkeyPatch, mode: str, switch: str
) -> None:
    prefixes = []
    for state in ("false", "true"):
        monkeypatch.setenv(switch, state)
        prefixes.extend([await _prefix(mode, goal) for goal in _QUESTIONS])

    first = prefixes[0]
    assert all(first)
    assert "knowledgegraph__search" in {schema["name"] for schema in first[3]}
    for other in prefixes[1:]:
        assert other == first


@pytest.mark.parametrize("mode", _MODES)
async def test_high_accuracy_mode_does_not_move_the_prefix_through_these_switches(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """HIGH_ACCURACY_MODE turns the switches on when they are unset."""
    for switch in _RETRIEVAL_SWITCHES:
        monkeypatch.setenv(switch, "")
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
    on = await _prefix(mode, _QUESTIONS[0])
    for switch in _RETRIEVAL_SWITCHES:
        monkeypatch.setenv(switch, "false")
    off = await _prefix(mode, _QUESTIONS[0])

    assert on == off
