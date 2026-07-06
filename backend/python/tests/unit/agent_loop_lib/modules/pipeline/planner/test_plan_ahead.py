"""Tests for app.agent_loop_lib.modules.pipeline.planner.plan_ahead.PlanAheadPlanner.

Covers the base plan/phase/confidence parsing behavior plus the
`tool_names` steering added so the upfront plan can reference real tools
(e.g. `run_code`) instead of producing tool-agnostic phases.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.responses import StructuredResponse
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.base import Plan
from app.agent_loop_lib.modules.pipeline.planner.plan_ahead import PlanAheadPlanner


@pytest.fixture
def mock_transport():
    t = AsyncMock()
    t.complete_structured = AsyncMock(return_value=StructuredResponse(data={
        "phases": [
            {"name": "Research", "description": "Gather data", "tools": ["knowledge_query"]},
            {"name": "Implement", "description": "Write code", "tools": []},
        ],
        "confidence": "high",
    }))
    return t


def test_plan_ahead_planner_instantiates() -> None:
    p = PlanAheadPlanner()
    assert p is not None


async def test_plan_ahead_without_transport_returns_empty_plan() -> None:
    p = PlanAheadPlanner(model=None)
    goal = Goal(description="complex task")
    plan = await p.plan(goal)
    assert isinstance(plan, Plan)
    assert plan.phases == []


async def test_plan_ahead_returns_plan_with_phases(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    goal = Goal(description="build a feature")
    plan = await p.plan(goal)
    assert len(plan.phases) == 2
    assert plan.phases[0].name == "Research"
    assert plan.phases[1].name == "Implement"


async def test_plan_ahead_phase_tools_parsed(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    goal = Goal(description="task")
    plan = await p.plan(goal)
    assert plan.phases[0].tools == ["knowledge_query"]


async def test_plan_ahead_confidence_parsed(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    goal = Goal(description="task")
    plan = await p.plan(goal)
    assert plan.confidence.value == "high"


async def test_plan_ahead_goal_preserved(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    goal = Goal(description="my special goal")
    plan = await p.plan(goal)
    assert plan.goal.description == "my special goal"


async def test_plan_ahead_calls_complete_structured(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    await p.plan(Goal(description="task"))
    mock_transport.complete_structured.assert_called_once()


async def test_plan_ahead_includes_requirements_in_prompt(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    goal = Goal(description="task", requirements=["Must be fast"])
    await p.plan(goal)
    call_kwargs = mock_transport.complete_structured.call_args.kwargs
    prompt = call_kwargs["messages"][0].content
    assert "Must be fast" in prompt


class TestToolNamesSteering:
    """`tool_names` makes the planner reference real tools (e.g. `run_code`)
    in its system prompt instead of producing tool-agnostic phases."""

    async def test_no_tool_names_leaves_system_prompt_unchanged(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport)
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete_structured.call_args.kwargs
        assert "Available tools for execution" not in call_kwargs["system"]

    async def test_tool_names_appended_to_system_prompt(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["run_code", "web_search"])
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete_structured.call_args.kwargs
        system = call_kwargs["system"]
        assert "run_code" in system
        assert "web_search" in system

    async def test_empty_tool_names_list_leaves_system_prompt_unchanged(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=[])
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete_structured.call_args.kwargs
        assert "Available tools for execution" not in call_kwargs["system"]

    async def test_run_code_mentioned_as_mandatory_for_file_generation(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["run_code"])
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete_structured.call_args.kwargs
        system = call_kwargs["system"]
        assert "MUST reference `run_code`" in system

    async def test_hint_clarifies_run_code_has_no_network_access(self, mock_transport) -> None:
        """`run_code` can never reach an external host (see
        `docker.py`'s `network_mode="none"`) — the planner must be told to
        route external-data phases through `web_search`/`fetch_url`
        instead, or it produces a plan that sends `run_code` on a task it
        can never actually complete."""
        p = PlanAheadPlanner(model=mock_transport, tool_names=["run_code", "web_search", "fetch_url"])
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete_structured.call_args.kwargs
        system = call_kwargs["system"]
        assert "NO network access" in system
        assert "web_search" in system
        assert "fetch_url" in system


class TestSandboxHasNetworkSteering:
    """When `sandbox_has_network=True` (the sandbox CAN reach the network —
    see `sandbox_bridge.sandbox_network_enabled()`), the planner's steering
    must flip: `run_code` becomes a valid way to fetch AND analyze live
    public data in one phase, instead of being forbidden from network use."""

    async def test_default_still_says_no_network_access(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["run_code"])
        await p.plan(Goal(description="task"))
        system = mock_transport.complete_structured.call_args.kwargs["system"]
        assert "run_code` has NO network access" in system

    async def test_sandbox_has_network_true_steers_toward_calling_apis_from_run_code(self, mock_transport) -> None:
        p = PlanAheadPlanner(
            model=mock_transport, tool_names=["run_code", "web_search"], sandbox_has_network=True,
        )
        await p.plan(Goal(description="task"))
        system = mock_transport.complete_structured.call_args.kwargs["system"]
        assert "run_code` CAN reach the network" in system
        assert "run_code` has NO network access" not in system

    async def test_sandbox_has_network_true_without_tool_names_leaves_prompt_unchanged(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, sandbox_has_network=True)
        await p.plan(Goal(description="task"))
        system = mock_transport.complete_structured.call_args.kwargs["system"]
        assert "Available tools for execution" not in system
