"""`OrchestratorLoop` (Phase 10 — `app/agents/agent_loop/loops/orchestrator.py`):
drives a scripted end-to-end Decompose -> Critique -> Dispatch -> Verify run
through a real `Agent`/`AgentRuntime`, using `ScriptedTransport` for both the
top-level orchestrator AND its spawned child (both resolve the same
"scripted" provider, per `ScriptedTransport`'s module docstring) instead of
a real LLM.

`ScriptedTransport.complete_structured()` always returns `data={}` (see its
implementation) — `critique_plan`/`verify_result`'s critics default
`passed=True` when `raw.get("passed", True)` sees an empty dict, so their
filler script slots (still consumed via `complete_structured()`, which never
appends to `transport.calls`) can be any harmless value.

`create_plan`'s `DefaultPlanner`, unlike those critics, now calls
`complete()` (markdown output, no JSON schema — see `planner/default.py`)
— its filler slot IS a real `complete()` call and DOES land in
`transport.calls`, one index earlier than every orchestrator turn after it;
`_scripted_full_run()`'s comments and the index-based assertions below
account for that extra call.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.loops.orchestrator import (
    COORDINATION_TOOL_NAMES,
    OrchestratorLoop,
    domain_spec_factory,
    register_coordination_tools,
)
from app.agents.agent_loop.tool_adapter import PipesHubToolAdapter
from app.agents.tools.models import Tool as RegistryTool
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_JIRA_TOOL_NAME = "jira_search_issues"


def _search_issues(query: str) -> str:
    """Module-level (see `test_agent_run_contract.py`'s `_add`) so
    `RegistryToolWrapper._is_class_method()` doesn't misroute this as a
    bound method needing `ClientFactoryRegistry` instantiation."""
    return f"3 open issues matching {query!r}"


def _build_orchestrator(context, transport: ScriptedTransport, *, max_turns: int = 15) -> tuple[Agent, ToolRegistry]:
    registry = ToolRegistry()
    registry.register_tool(
        PipesHubToolAdapter(
            RegistryTool(app_name="jira", tool_name="search_issues", description="Search Jira issues", function=_search_issues),
            "jira",
            "search_issues",
            context_ref=lambda: context,
        )
    )
    register_coordination_tools(registry)

    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)

    runtime = AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=registry,
        spec_factory=domain_spec_factory(
            provider="scripted",
            model_name="scripted-model",
            default_tool_names=[_JIRA_TOOL_NAME],
        ),
    )
    spec = AgentSpec(
        name="pipeshub-orchestrator",
        system_prompt="You are a deep-agent orchestrator.",
        tool_names=list(COORDINATION_TOOL_NAMES),
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=OrchestratorLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime), registry


def _scripted_full_run() -> ScriptedTransport:
    """9 scripted steps consumed in exactly this order (see module
    docstring): parent turn -> tool's own planner/critic call (for
    `create_plan`/`critique_plan`/`verify_result`) -> next parent turn, with
    the spawned child's single terminal turn interleaved between the
    dispatch and verify phases."""
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="c1", name="create_plan", arguments={}))
    transport.add_text("_")  # consumed by create_plan's DefaultPlanner.complete() call
    transport.add_tool_call(ToolCall(
        id="c2", name="critique_plan",
        arguments={"plan": "1. jira: Look up open Jira issues"},
    ))
    transport.add_text("_")  # consumed by critique_plan's complete_structured()
    transport.add_tool_call(ToolCall(
        id="c3", name="spawn_agent",
        arguments={
            "role": "jira", "goal": "Find open Jira issues assigned to the user",
            "reasoning": "Isolated Jira workstream", "tools": [_JIRA_TOOL_NAME],
        },
    ))
    transport.add_text("Found 3 open issues.")  # child's terminal turn
    transport.add_tool_call(ToolCall(
        id="c4", name="verify_result",
        arguments={"output": "There are 3 open Jira issues assigned to you."},
    ))
    transport.add_text("_")  # consumed by verify_result's complete_structured()
    transport.add_text("There are 3 open Jira issues assigned to you.")  # parent's final answer
    return transport


class TestOrchestratorLoopFullRun:
    async def test_completes_decompose_critique_dispatch_verify(self) -> None:
        context = make_context()
        transport = _scripted_full_run()
        agent, _registry = _build_orchestrator(context, transport)

        result = await agent.run(Goal(description="How many open Jira issues do I have?"))

        assert result.success is True
        assert result.output == "There are 3 open Jira issues assigned to you."

    async def test_orchestrator_never_sees_domain_tool_schemas(self) -> None:
        """The parent's OWN turns must only ever be offered the four
        coordination tool schemas — connector tools are reachable only
        through a spawned child's separately-scoped `tool_names`."""
        context = make_context()
        transport = _scripted_full_run()
        agent, _registry = _build_orchestrator(context, transport)

        await agent.run(Goal(description="How many open Jira issues do I have?"))

        # Call order: plan (orchestrator turn) -> create_plan's own
        # DefaultPlanner.complete() call -> critique, dispatch (orchestrator
        # turns) -> the spawned child's one turn -> verify, final
        # (orchestrator again).
        assert len(transport.calls) == 7
        orchestrator_calls = [transport.calls[i] for i in (0, 2, 3, 5, 6)]
        for call in orchestrator_calls:
            names = {schema.name for schema in (call["tools"] or [])}
            assert names <= set(COORDINATION_TOOL_NAMES)
            assert _JIRA_TOOL_NAME not in names

    async def test_spawned_child_only_sees_its_own_domain_tool(self) -> None:
        context = make_context()
        transport = _scripted_full_run()
        agent, _registry = _build_orchestrator(context, transport)

        await agent.run(Goal(description="How many open Jira issues do I have?"))

        # The 5th complete() call (index 4) is the spawned child's only
        # turn — index 1 is create_plan's own DefaultPlanner.complete() call.
        child_call = transport.calls[4]
        names = {schema.name for schema in (child_call["tools"] or [])}
        assert names == {_JIRA_TOOL_NAME}

    async def test_fails_if_dispatch_phase_exhausts_max_turns(self) -> None:
        """If the model never calls `spawn_agent` during Phase 2 (calling
        some other tool instead, so each turn keeps `step()` returning
        "continue" rather than terminating early on a plain-text reply),
        the loop must fail cleanly once `max_turns` is exhausted rather
        than looping forever or crashing."""
        context = make_context()
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="c1", name="create_plan", arguments={}))
        transport.add_text("_")  # create_plan's complete_structured() filler
        transport.add_tool_call(ToolCall(
            id="c2", name="critique_plan",
            arguments={"plan": "1. jira: Look up open Jira issues"},
        ))
        transport.add_text("_")  # critique_plan's complete_structured() filler
        # Phase 2 (turn_index 2, 3 with max_turns=4 below): model keeps
        # calling create_plan again instead of spawn_agent.
        for i in range(2):
            transport.add_tool_call(ToolCall(id=f"stall-{i}", name="create_plan", arguments={}))
            transport.add_text("_")

        agent, _registry = _build_orchestrator(context, transport, max_turns=4)
        result = await agent.run(Goal(description="How many open Jira issues do I have?"))

        assert result.success is False
        assert "max_turns" in (result.error or "")

    async def test_spawned_child_receives_user_and_time_context(self) -> None:
        """The child's system prompt must include user identity and time
        context from `AgentContext` so it can resolve user-relative /
        time-relative queries ('my tickets', 'last 2 months')."""
        context = make_context(
            user_email="alice@example.com",
            user_info={"fullName": "Alice Smith", "userId": "user-1", "orgId": "org-1"},
            timezone="America/New_York",
            current_time="2026-07-04T22:00:00Z",
        )
        transport = _scripted_full_run()
        agent, _registry = _build_orchestrator_with_context(context, transport)

        await agent.run(Goal(description="Show my Jira issues from last 2 months"))

        # The 5th complete() call (index 4) is the child's turn — its
        # system prompt should contain the user/time context. Index 1 is
        # create_plan's own DefaultPlanner.complete() call.
        child_call = transport.calls[4]
        system = child_call.get("system") or ""
        assert "Alice Smith" in system
        assert "alice@example.com" in system
        assert "America/New_York" in system or "New_York" in system


def _build_orchestrator_with_context(
    context, transport: ScriptedTransport, *, max_turns: int = 15
) -> tuple[Agent, ToolRegistry]:
    """Like `_build_orchestrator` but passes `context` to
    `domain_spec_factory` so the child inherits user/time context."""
    registry = ToolRegistry()
    registry.register_tool(
        PipesHubToolAdapter(
            RegistryTool(app_name="jira", tool_name="search_issues", description="Search Jira issues", function=_search_issues),
            "jira",
            "search_issues",
            context_ref=lambda: context,
        )
    )
    register_coordination_tools(registry)

    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)

    runtime = AgentRuntime(
        transport_registry=transport_registry,
        tool_registry=registry,
        spec_factory=domain_spec_factory(
            provider="scripted",
            model_name="scripted-model",
            default_tool_names=[_JIRA_TOOL_NAME],
            context=context,
        ),
    )
    spec = AgentSpec(
        name="pipeshub-orchestrator",
        system_prompt="You are a deep-agent orchestrator.",
        tool_names=list(COORDINATION_TOOL_NAMES),
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=OrchestratorLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime), registry
