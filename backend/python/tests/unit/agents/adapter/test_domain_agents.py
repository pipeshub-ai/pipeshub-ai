"""`domain_agents.py` — the domain-agent catalog and `compose_domain_agents()`
builder, plus `AgentTool.handle()`'s parent-context propagation: verifies tool
claiming, per-request availability, agent-to-agent delegation wiring, and a
full parent -> child -> tool run driven by `ScriptedTransport`."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.exceptions import AgentError
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.special_route import RouteContext
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.domain_agents import (
    compose_domain_agents,
    plan_domain_agents,
    register_domain_agents,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class FakeTool(Tool):
    """Minimal registrable tool; `app_name` mimics the PipesHub adapters'
    domain-grouping attribute."""

    def __init__(self, name: str, app_name: str | None = None, result: str = "ok") -> None:
        self._name = name
        self._app_name = app_name
        self._result = result
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(kwargs)
        return ToolOutput(success=True, data=self._result)


def _full_registry() -> ToolRegistry:
    """Every catalog domain has at least one tool, plus an unclaimed
    connector tool that must stay at the top level."""
    registry = ToolRegistry()
    for tool in (
        FakeTool("run_code"),
        FakeTool("install_packages"),
        FakeTool("read_sandbox_file"),
        FakeTool("dynamic_web_search", app_name="dynamic"),
        FakeTool("dynamic_fetch_url", app_name="dynamic"),
        FakeTool("retrieval_search", app_name="retrieval"),
        FakeTool("knowledgehub_list", app_name="knowledgehub"),
        FakeTool("dynamic_fetch_full_record", app_name="dynamic"),
        FakeTool("calculator_evaluate", app_name="calculator"),
        FakeTool("date_calculator_get_exclusion_dates", app_name="date_calculator"),
        FakeTool("google_calendar_list_events", app_name="google_calendar"),
        FakeTool("jira_search_issues", app_name="jira"),
    ):
        registry.register_tool(tool)
    return registry


def _compose(registry: ToolRegistry, runtime: AgentRuntime | None = None) -> list[str]:
    return compose_domain_agents(
        registry,
        runtime or AgentRuntime(tool_registry=registry),
        make_context(),
        provider="scripted",
        model_name="scripted-model",
    )


class TestComposition:
    def test_all_domains_built_and_residual_kept(self) -> None:
        registry = _full_registry()
        top_names = _compose(registry)

        for agent_name in (
            "web_agent", "coding_agent", "internal_search_agent",
            "calculator_agent", "calendar_agent",
        ):
            assert agent_name in top_names
            assert registry.has(agent_name)
            assert isinstance(registry.resolve_by_name(agent_name), AgentTool)

        # Unclaimed connector tool stays a direct top-level tool...
        assert "jira_search_issues" in top_names
        # ...while claimed domain tools leave the top level entirely.
        for claimed in ("run_code", "dynamic_web_search", "retrieval_search",
                        "calculator_evaluate", "date_calculator_get_exclusion_dates",
                        "google_calendar_list_events"):
            assert claimed not in top_names

    def test_calculator_agent_claims_date_calculator_tools_too(self) -> None:
        """`date_calculator` is a separate registry `app_name` from
        `calculator` (see `app/agents/actions/calculator/date_calculator.py`)
        — both must be claimed by the SAME `calculator_agent`, not left
        stranded at the top level or split across two agents."""
        registry = _full_registry()
        _compose(registry)

        calculator = registry.resolve_by_name("calculator_agent")._spec
        assert "date_calculator_get_exclusion_dates" in calculator.tool_names
        assert "calculator_evaluate" in calculator.tool_names

    def test_child_specs_are_react_loops_scoped_to_their_domain(self) -> None:
        registry = _full_registry()
        _compose(registry)

        internal = registry.resolve_by_name("internal_search_agent")._spec
        assert isinstance(internal.loop, ReActLoop)
        assert set(internal.tool_names) == {
            "retrieval_search", "knowledgehub_list", "dynamic_fetch_full_record",
        }

    def test_coding_agent_delegates_to_web_agent_when_both_exist(self) -> None:
        registry = _full_registry()
        _compose(registry)

        coding = registry.resolve_by_name("coding_agent")._spec
        assert "web_agent" in coding.tool_names

    def test_coding_agent_has_no_web_delegate_when_web_unavailable(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(FakeTool("run_code"))
        top_names = _compose(registry)

        assert "web_agent" not in top_names
        assert "web_agent" not in registry.resolve_by_name("coding_agent")._spec.tool_names

    def test_no_claims_degenerates_to_flat_tool_list(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(FakeTool("jira_search_issues", app_name="jira"))
        top_names = _compose(registry)

        assert top_names == ["jira_search_issues"]
        assert not any(registry.has(n) for n in ("web_agent", "coding_agent"))


class TestPlanRegisterSplit:
    """`plan_domain_agents()` must be pure (no registry mutation, callable
    before an `AgentRuntime` exists) and `register_domain_agents()` must
    replay that SAME plan to produce an identical top-level grant — this
    is what lets `factory.py` steer the quick-mode planner with the exact
    names the executing agent later ends up with."""

    def test_plan_does_not_mutate_the_registry(self) -> None:
        registry = _full_registry()
        names_before = set(registry.names())

        plan = plan_domain_agents(registry)

        assert set(registry.names()) == names_before
        assert not any(registry.has(n) for n in plan.agent_names)

    def test_plan_top_level_names_matches_post_registration_grant(self) -> None:
        registry = _full_registry()
        plan = plan_domain_agents(registry)
        planned_names = plan.top_level_names

        runtime = AgentRuntime(tool_registry=registry)
        registered_names = register_domain_agents(
            plan, registry, runtime, make_context(), provider="scripted", model_name="scripted-model",
        )

        assert set(registered_names) == set(planned_names)

    def test_plan_is_reusable_across_multiple_registration_calls(self) -> None:
        """The plan itself carries no registry reference — computing it
        once and registering later (as `factory.py` does, with loop
        routing in between) must not depend on registry state that could
        have changed in the meantime."""
        registry = _full_registry()
        plan = plan_domain_agents(registry)

        # Simulate work happening between planning and registration (e.g.
        # loop routing) — the registry gains an unrelated tool.
        registry.register_tool(FakeTool("slack_post_message", app_name="slack"))

        runtime = AgentRuntime(tool_registry=registry)
        top_names = register_domain_agents(
            plan, registry, runtime, make_context(), provider="scripted", model_name="scripted-model",
        )

        # The plan's residual is frozen at planning time — a tool added
        # afterward is neither claimed nor granted via this call.
        assert "slack_post_message" not in top_names

    def test_compose_domain_agents_is_plan_then_register(self) -> None:
        registry = _full_registry()
        runtime = AgentRuntime(tool_registry=registry)

        composed_names = compose_domain_agents(
            registry, runtime, make_context(), provider="scripted", model_name="scripted-model",
        )

        registry2 = _full_registry()
        runtime2 = AgentRuntime(tool_registry=registry2)
        plan = plan_domain_agents(registry2)
        split_names = register_domain_agents(
            plan, registry2, runtime2, make_context(), provider="scripted", model_name="scripted-model",
        )

        assert composed_names == split_names


class TestEndToEndDelegation:
    async def test_parent_delegates_to_child_which_runs_its_tool(self) -> None:
        """Parent -> calculator_agent (AgentTool special route) -> child ReAct
        run -> calculator tool -> child's final text becomes the parent's
        tool result. One shared ScriptedTransport scripts both runs in call
        order, proving parent and child use the same Agent loop."""
        registry = _full_registry()
        calc_tool = registry.resolve_by_name("calculator_evaluate")

        transport = ScriptedTransport()
        transport_registry = TransportRegistry()
        transport_registry.register("scripted", lambda: transport)
        runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)

        top_names = _compose(registry, runtime)

        # 1: parent delegates; 2: child calls its tool; 3: child answers;
        # 4: parent answers.
        transport.add_tool_call(ToolCall(id="c1", name="calculator_agent", arguments={"goal": "what is 3 + 4?"}))
        transport.add_tool_call(ToolCall(id="c2", name="calculator_evaluate", arguments={}))
        transport.add_text("The sum is 7.")
        transport.add_text("Answer: 7.")

        spec = AgentSpec(
            name="top-agent",
            system_prompt="You are a helpful assistant.",
            tool_names=top_names,
            model=ModelSpec(provider="scripted", model="scripted-model"),
            loop=ReActLoop(),
            max_turns=5,
        )
        result = await Agent(spec, runtime, session_id="sess-1").run(Goal(description="what is 3 + 4?"))

        assert result.success is True
        assert result.output == "Answer: 7."
        assert calc_tool.calls, "child agent never executed the calculator tool"
        delegate_result = result.turns[0].tool_results[0]
        assert delegate_result.name == "calculator_agent"
        assert delegate_result.is_error is False
        assert delegate_result.content == "The sum is 7."


def _route_context(runtime: AgentRuntime, call: ToolCall, *, session_id: str | None = "sess-1") -> RouteContext:
    parent_spec = AgentSpec(name="parent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
    run_scope = RunScope(
        identity=RunContext(role_name="parent", model="m"),
        spec=parent_spec, runtime=runtime, goal=Goal(description="g"),
        session_id=session_id,
    )
    tool_scope = ToolScope(turn=TurnScope(run=run_scope, turn_index=0), call=call, tool_path="")
    return RouteContext(agent=MagicMock(), scope=tool_scope)


class TestAgentToolHandle:
    async def test_handle_propagates_parent_run_ctx_and_session_id(self) -> None:
        runtime = AgentRuntime()
        captured: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            captured.update(spec=spec, goal=goal, parent_run_ctx=parent_run_ctx, **kwargs)
            return AgentResult(goal=goal, output="child output", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="child", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime)
        call = ToolCall(id="c1", name="child", arguments={"goal": "do it", "context": "extra"})

        result = await tool.handle(call, _route_context(runtime, call))

        assert result.is_error is False
        assert result.content == "child output"
        assert captured["spec"] is child_spec
        assert captured["goal"].description == "do it\n\nContext: extra"
        assert captured["parent_run_ctx"].role_name == "parent"
        assert captured["session_id"] == "sess-1"

    async def test_handle_surfaces_depth_guard_as_error_result(self) -> None:
        runtime = AgentRuntime()

        async def _raise(*_args: Any, **_kwargs: Any) -> AgentResult:
            raise AgentError("Maximum spawn depth (3) reached")

        runtime.run_child = _raise

        child_spec = AgentSpec(name="child", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime)
        call = ToolCall(id="c1", name="child", arguments={"goal": "do it"})

        result = await tool.handle(call, _route_context(runtime, call))

        assert result.is_error is True
        assert "spawn depth" in str(result.content)
