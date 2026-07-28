"""Tests for app.agent_loop_lib.modules.pipeline.planner.plan_ahead.PlanAheadPlanner.

Covers the planner's plain-text passthrough (`complete()`, zero parsing)
plus the `tool_names` steering added so the upfront plan can reference
real tools (e.g. `run_code`) instead of producing tool-agnostic phases.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.responses import ModelResponse
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.base import Plan
from app.agent_loop_lib.modules.pipeline.planner.plan_ahead import PlanAheadPlanner

_PLAN_TEXT = (
    "1. **Research**: Gather data\n"
    "2. **Implement**: Write code"
)


@pytest.fixture
def mock_transport():
    t = AsyncMock()
    t.complete = AsyncMock(return_value=ModelResponse(message=AssistantMessage(content=_PLAN_TEXT)))
    return t


def test_plan_ahead_planner_instantiates() -> None:
    p = PlanAheadPlanner()
    assert p is not None


async def test_plan_ahead_without_transport_returns_empty_plan() -> None:
    p = PlanAheadPlanner(model=None)
    goal = Goal(description="complex task")
    plan = await p.plan(goal)
    assert isinstance(plan, Plan)
    assert plan.text == ""


async def test_plan_ahead_returns_raw_text_verbatim(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    plan = await p.plan(Goal(description="build a feature"))
    assert plan.text == _PLAN_TEXT


async def test_plan_ahead_goal_preserved(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    goal = Goal(description="my special goal")
    plan = await p.plan(goal)
    assert plan.goal.description == "my special goal"


async def test_plan_ahead_calls_complete(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    await p.plan(Goal(description="task"))
    mock_transport.complete.assert_called_once()


async def test_plan_ahead_includes_requirements_in_prompt(mock_transport) -> None:
    p = PlanAheadPlanner(model=mock_transport)
    goal = Goal(description="task", requirements=["Must be fast"])
    await p.plan(goal)
    call_kwargs = mock_transport.complete.call_args.kwargs
    prompt = call_kwargs["messages"][0].content
    assert "Must be fast" in prompt


class TestOddFormatNeverRaises:
    """Any `complete()` response is passed through verbatim — there is no
    schema (or parsing) to fail against anymore."""

    async def test_free_prose_response_passed_through(self, mock_transport) -> None:
        mock_transport.complete = AsyncMock(
            return_value=ModelResponse(message=AssistantMessage(content="Just do the thing carefully."))
        )
        p = PlanAheadPlanner(model=mock_transport)
        plan = await p.plan(Goal(description="task"))
        assert plan.text == "Just do the thing carefully."

    async def test_empty_response_yields_empty_text(self, mock_transport) -> None:
        mock_transport.complete = AsyncMock(
            return_value=ModelResponse(message=AssistantMessage(content=""))
        )
        p = PlanAheadPlanner(model=mock_transport)
        plan = await p.plan(Goal(description="task"))
        assert plan.text == ""


class TestToolNamesSteering:
    """`tool_names` makes the planner reference real tools (e.g. `run_code`)
    in its system prompt instead of producing tool-agnostic phases."""

    async def test_no_tool_names_leaves_system_prompt_unchanged(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport)
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete.call_args.kwargs
        assert "Available tools for execution" not in call_kwargs["system"]

    async def test_tool_names_appended_to_system_prompt(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["run_code", "web_search"])
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete.call_args.kwargs
        system = call_kwargs["system"]
        assert "run_code" in system
        assert "web_search" in system

    async def test_empty_tool_names_list_leaves_system_prompt_unchanged(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=[])
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete.call_args.kwargs
        assert "Available tools for execution" not in call_kwargs["system"]

    async def test_run_code_mentioned_as_mandatory_for_file_generation(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["run_code"])
        await p.plan(Goal(description="task"))
        call_kwargs = mock_transport.complete.call_args.kwargs
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
        call_kwargs = mock_transport.complete.call_args.kwargs
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
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "run_code` has NO network access" in system

    async def test_sandbox_has_network_true_steers_toward_calling_apis_from_run_code(self, mock_transport) -> None:
        p = PlanAheadPlanner(
            model=mock_transport, tool_names=["run_code", "web_search"], sandbox_has_network=True,
        )
        await p.plan(Goal(description="task"))
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "run_code` CAN reach the network" in system
        assert "run_code` has NO network access" not in system

    async def test_sandbox_has_network_true_without_tool_names_leaves_prompt_unchanged(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, sandbox_has_network=True)
        await p.plan(Goal(description="task"))
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "Available tools for execution" not in system


class TestComposedDelegateSteering:
    """When the caller composed the top-level agent's tools (see
    `app.agents.agent_loop.domain_agents`), `run_code`/`web_search`/
    `fetch_url` are no longer directly callable — the planner must
    reference the `coding_agent`/`web_agent` delegate instead, or the
    executing ReAct loop has no tool matching the phase's own work."""

    async def test_coding_agent_referenced_instead_of_run_code(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["coding_agent", "web_agent"])
        await p.plan(Goal(description="task"))
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "MUST reference `coding_agent`" in system
        assert "run_code" not in system

    async def test_web_agent_referenced_instead_of_web_search_fetch_url(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["coding_agent", "web_agent"])
        await p.plan(Goal(description="task"))
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "`web_agent`" in system
        assert "web_search" not in system
        assert "fetch_url" not in system

    async def test_coding_agent_network_steering_still_applies(self, mock_transport) -> None:
        p = PlanAheadPlanner(
            model=mock_transport, tool_names=["coding_agent", "web_agent"], sandbox_has_network=True,
        )
        await p.plan(Goal(description="task"))
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "coding_agent` CAN reach the network" in system

    async def test_coding_agent_without_network_still_warns(self, mock_transport) -> None:
        p = PlanAheadPlanner(model=mock_transport, tool_names=["coding_agent"])
        await p.plan(Goal(description="task"))
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "coding_agent` has NO network access" in system

    async def test_flat_names_still_use_singular_run_code_and_plural_web_tools(self, mock_transport) -> None:
        """Regression guard: when composition is off (flat names), the
        original phrasing is unchanged, including "are" (plural) for the
        two-tool `web_search`/`fetch_url` pair."""
        p = PlanAheadPlanner(model=mock_transport, tool_names=["run_code", "web_search", "fetch_url"], sandbox_has_network=True)
        await p.plan(Goal(description="task"))
        system = mock_transport.complete.call_args.kwargs["system"]
        assert "`web_search`/`fetch_url` (if in this list) are still" in system
