"""`domain_agents.py` — the domain-agent catalog and `compose_domain_agents()`
builder, plus `AgentTool.handle()`'s parent-context propagation: verifies tool
claiming, per-request availability, agent-to-agent delegation wiring, and a
full parent -> child -> tool run driven by `ScriptedTransport`."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.exceptions import AgentError
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, ToolMessage, UserMessage
from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import peek_staged_input_files
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.special_route import RouteContext
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.domain_agents import (
    DOMAIN_AGENT_DEFINITIONS,
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
        FakeTool("dynamic__web_search", app_name="dynamic"),
        FakeTool("dynamic__fetch_url", app_name="dynamic"),
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


def _compose(
    registry: ToolRegistry, runtime: AgentRuntime | None = None, context: Any | None = None,
) -> list[str]:
    return compose_domain_agents(
        registry,
        runtime or AgentRuntime(tool_registry=registry),
        context if context is not None else make_context(),
        provider="scripted",
        model_name="scripted-model",
    )


class TestComposition:
    def test_all_domains_built_and_residual_kept(self) -> None:
        registry = _full_registry()
        top_names = _compose(registry)

        for agent_name in (
            "web_agent", "coding_agent", "internal_exploration_agent",
            "calculator_agent", "calendar_agent",
        ):
            assert agent_name in top_names
            assert registry.has(agent_name)
            assert isinstance(registry.resolve_by_name(agent_name), AgentTool)

        # Unclaimed connector tool stays a direct top-level tool...
        assert "jira_search_issues" in top_names
        # ...while claimed domain tools leave the top level entirely.
        for claimed in ("run_code", "dynamic__web_search", "retrieval_search",
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

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
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


class TestAvailabilityGating:
    """Availability is claim-based, not a separate flag: no `retrieval`/
    `knowledgehub` tools registered means nothing for
    `internal_exploration_agent` to claim, so it is silently never built
    (see `domain_agents.py`'s module docstring). Same mechanism gates
    `web_agent` on whether any `dynamic__web_search`/`dynamic__fetch_url`
    tool was loaded. These tests pin down that contract so a future change
    can't quietly grant either delegate when its underlying tools aren't
    actually available this request."""

    def test_no_internal_exploration_agent_without_knowledge_tools(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(FakeTool("jira_search_issues", app_name="jira"))
        top_names = _compose(registry)

        assert "internal_exploration_agent" not in top_names
        assert not registry.has("internal_exploration_agent")

    def test_no_web_agent_without_web_search_tools(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(FakeTool("retrieval_search", app_name="retrieval"))
        top_names = _compose(registry)

        assert "web_agent" not in top_names
        assert not registry.has("web_agent")

    def test_both_available_when_both_tool_sets_are_loaded(self) -> None:
        registry = _full_registry()
        top_names = _compose(registry)

        assert "internal_exploration_agent" in top_names
        assert "web_agent" in top_names


class TestInternalExplorationAgent:
    """Covers what's specific to `internal_exploration_agent` beyond the
    generic composition mechanics already covered by `TestComposition`:
    its larger turn budget, and the per-request connector inventory/
    playbook `instructions_factory` appends to its system prompt."""

    def test_max_turns_exceeds_the_generic_child_default(self) -> None:
        registry = _full_registry()
        _compose(registry)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        calculator = registry.resolve_by_name("calculator_agent")._spec
        assert internal.max_turns == 12
        assert internal.max_turns > calculator.max_turns

    def test_connector_inventory_and_playbook_included_when_knowledge_configured(self) -> None:
        registry = _full_registry()
        context = make_context(
            agent_knowledge=[
                {"displayName": "Confluence", "type": "confluence", "connectorId": "conn-1"},
            ],
        )
        _compose(registry, context=context)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "Exploration Playbook" in internal.system_prompt
        assert "conn-1" in internal.system_prompt

    def test_playbook_still_injected_without_agent_knowledge(self) -> None:
        """The generic fan-out/iteration/fetch-policy playbook must be
        present even when there is no per-request connector inventory to
        append — returning `None` here would silently drop ALL exploration
        guidance, not just the connector list (see `_internal_exploration_instructions`)."""
        registry = _full_registry()
        _compose(registry)  # default make_context() has no agent_knowledge

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "Exploration Playbook" in internal.system_prompt
        # No connector inventory was configured, so no routing-rules block.
        assert "Retrieval Routing" not in internal.system_prompt

    def test_full_record_fetch_policy_is_mandatory_and_names_the_dynamic_tool(self) -> None:
        """Regression guard for the weak, easily-skipped phrasing this
        policy used to have — it must reference the actual dynamic tool
        name and use MUST-call language, not just a soft suggestion."""
        registry = _full_registry()
        context = make_context(
            agent_knowledge=[
                {"displayName": "Confluence", "type": "confluence", "connectorId": "conn-1"},
            ],
        )
        _compose(registry, context=context)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "dynamic_fetch_full_record" in internal.system_prompt
        assert "MUST call it" in internal.system_prompt
        assert "Record ID :" in internal.system_prompt

    def test_playbook_instructs_reporting_record_ids_for_caller_follow_up(self) -> None:
        registry = _full_registry()
        context = make_context(
            agent_knowledge=[
                {"displayName": "Confluence", "type": "confluence", "connectorId": "conn-1"},
            ],
        )
        _compose(registry, context=context)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "Reporting Record IDs" in internal.system_prompt

    def test_playbook_has_relevance_gate_on_fetch_policy(self) -> None:
        """Issue 5 fix: the fetch policy must tell the agent to fetch only
        records relevant to the goal, not every result from a broad search."""
        registry = _full_registry()
        _compose(registry)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "Relevance gate" in internal.system_prompt
        assert "only fetch" in internal.system_prompt.lower()

    def test_playbook_softens_when_in_doubt_default_to_relevant_records_only(self) -> None:
        registry = _full_registry()
        _compose(registry)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "when in doubt about a record that is clearly relevant" in internal.system_prompt

    def test_playbook_fan_out_mentions_single_connector_broad_search(self) -> None:
        """Issue 2 fix: a single-connector inventory should not stop the
        agent from also trying a broad, unscoped search in the same turn."""
        registry = _full_registry()
        _compose(registry)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "only ONE connector" in internal.system_prompt

    def test_playbook_never_mentions_collection_ids(self) -> None:
        """connector_ids/collection_ids are unified — the playbook must
        never teach the agent about a separate collection_ids parameter."""
        registry = _full_registry()
        context = make_context(
            agent_knowledge=[
                {"displayName": "Confluence", "type": "confluence", "connectorId": "conn-1"},
            ],
        )
        _compose(registry, context=context)

        internal = registry.resolve_by_name("internal_exploration_agent")._spec
        assert "collection_ids" not in internal.system_prompt


class TestSharedToolNames:
    """`register_domain_agents(..., shared_tool_names=...)` — meta-tools
    (today: the read-only skill tools) granted to EVERY built domain in
    addition to its own claimed set, without being marked "claimed" (so
    the top level keeps them too, as before)."""

    def test_shared_tool_names_granted_to_every_built_domain(self) -> None:
        registry = _full_registry()
        registry.register_tool(FakeTool("load_skill"))
        registry.register_tool(FakeTool("skill_search"))
        runtime = AgentRuntime(tool_registry=registry)

        top_names = compose_domain_agents(
            registry, runtime, make_context(), provider="scripted", model_name="scripted-model",
            shared_tool_names=frozenset({"load_skill", "skill_search"}),
        )

        coding = registry.resolve_by_name("coding_agent")._spec
        calculator = registry.resolve_by_name("calculator_agent")._spec
        assert "load_skill" in coding.tool_names
        assert "skill_search" in coding.tool_names
        assert "load_skill" in calculator.tool_names

        # Not "claimed" — the top level keeps them too, same as before
        # this feature existed.
        assert "load_skill" in top_names
        assert "skill_search" in top_names

    def test_shared_tool_name_not_registered_this_request_is_skipped(self) -> None:
        """A name in `shared_tool_names` that isn't actually on the
        registry (skills disabled, or any other future caller) must not
        be granted as a phantom tool a child can never resolve."""
        registry = _full_registry()
        runtime = AgentRuntime(tool_registry=registry)

        compose_domain_agents(
            registry, runtime, make_context(), provider="scripted", model_name="scripted-model",
            shared_tool_names=frozenset({"load_skill"}),
        )

        coding = registry.resolve_by_name("coding_agent")._spec
        assert "load_skill" not in coding.tool_names

    def test_defaults_to_empty_and_changes_nothing(self) -> None:
        registry = _full_registry()
        runtime = AgentRuntime(tool_registry=registry)

        top_names = compose_domain_agents(
            registry, runtime, make_context(), provider="scripted", model_name="scripted-model",
        )

        coding = registry.resolve_by_name("coding_agent")._spec
        assert set(coding.tool_names) == {
            "run_code", "install_packages", "read_sandbox_file", "web_agent",
        }
        assert "coding_agent" in top_names


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

    async def test_jira_search_result_reaches_coding_agent_child_deterministically(self) -> None:
        """Reproduces (and verifies the fix for) the incident this module
        exists for: the parent fetches Jira tickets with its OWN tool,
        then delegates "build a PDF from them" to `coding_agent` — which
        cannot reach Jira itself. Asserts BOTH handoff channels: the
        child's goal contains the ticket data inline, AND its sandbox
        received the full data as `input/parent_tool_results.json` before
        it ran any code — not merely relying on the calling model to have
        pasted the data into the goal text."""
        from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
        from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import CodingSandboxTool
        from tests.unit.agent_loop_lib.tools.builtin.sandbox.test_coding_sandbox import (
            _UploadCapturingBackend,
        )

        registry = ToolRegistry()
        registry.register_tool(
            FakeTool("jira_search_issues", app_name="jira", result={"tickets": ["A-1", "A-2"]})
        )
        created_backends: list[_UploadCapturingBackend] = []

        def _backend_factory() -> _UploadCapturingBackend:
            backend = _UploadCapturingBackend()
            created_backends.append(backend)
            return backend

        sandbox_manager = SandboxManager()
        sandbox_manager.register_backend_factory(SandboxType.CODING, _backend_factory)
        registry.register_tool(CodingSandboxTool(sandbox_manager))

        transport = ScriptedTransport()
        transport_registry = TransportRegistry()
        transport_registry.register("scripted", lambda: transport)
        runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)

        top_names = _compose(registry, runtime)
        assert "coding_agent" in top_names

        transport.add_tool_call(ToolCall(id="c-jira", name="jira_search_issues", arguments={}))
        transport.add_tool_call(ToolCall(
            id="c-code", name="coding_agent",
            arguments={"goal": "Build a PDF report of the tickets."},
        ))
        transport.add_tool_call(ToolCall(
            id="c-run", name="run_code", arguments={"code": "print('pdf built')", "language": "python"},
        ))
        transport.add_text("Built the PDF from the 2 tickets.")  # child's final answer
        transport.add_text("Done — PDF report built.")  # parent's final answer

        spec = AgentSpec(
            name="top-agent",
            system_prompt="You are a helpful assistant.",
            tool_names=top_names,
            model=ModelSpec(provider="scripted", model="scripted-model"),
            loop=ReActLoop(),
            max_turns=5,
        )
        result = await Agent(spec, runtime, session_id="sess-1").run(
            Goal(description="Fetch my Jira tickets and build a PDF report of them"),
        )

        assert result.success is True
        assert result.output == "Done — PDF report built."

        # calls[0]=parent dispatches jira, [1]=parent dispatches coding_agent,
        # [2]=child's first (and only) completion call, [3]=child's final
        # text, [4]=parent's final text.
        child_first_call_messages = transport.calls[2]["messages"]
        child_goal_text = " ".join(
            m.content if isinstance(m.content, str) else str(m.content) for m in child_first_call_messages
        )
        assert "jira_search_issues" in child_goal_text
        assert "A-1" in child_goal_text and "A-2" in child_goal_text
        assert "Build a PDF report of the tickets." in child_goal_text

        assert len(created_backends) == 1
        uploaded = created_backends[0].uploaded
        assert "input/parent_tool_results.json" in uploaded
        payload = json.loads(uploaded["input/parent_tool_results.json"])
        # `parent_results_as_json`'s budget-aware envelope (see
        # `coordination/parent_results.py`): results wrapped alongside a
        # `_meta` block describing any truncation (none here).
        assert payload["results"] == [{"tool": "jira_search_issues", "content": {"tickets": ["A-1", "A-2"]}}]
        assert payload["_meta"]["truncated"] is False


def _route_context(
    runtime: AgentRuntime, call: ToolCall, *, session_id: str | None = "sess-1",
    messages: list | None = None, parent_extra_prompt_sections: dict[str, str] | None = None,
) -> RouteContext:
    parent_spec = AgentSpec(name="parent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
    run_scope = RunScope(
        identity=RunContext(role_name="parent", model="m"),
        spec=parent_spec, runtime=runtime, goal=Goal(description="g"),
        session_id=session_id,
    )
    if parent_extra_prompt_sections:
        run_scope.extra_prompt_sections.update(parent_extra_prompt_sections)
    tool_scope = ToolScope(
        turn=TurnScope(run=run_scope, turn_index=0), call=call, tool_path="", messages=messages or [],
    )
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


class TestAgentToolInheritsParentSkills:
    """`AgentTool.handle()` carries forward whatever skill body the
    calling agent already had preloaded (`preloaded_skills`, written by
    `skill_preloading`/`load_skill` — see `RunScope.extra_prompt_sections`)
    into the child's own goal text, since `run_child()` always hands a
    statically-composed child a brand-new, empty `RunScope`."""

    async def test_parent_preloaded_skill_reaches_child_goal(self) -> None:
        runtime = AgentRuntime()
        captured: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            captured["goal"] = goal
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime)
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "generate the pdf report"})

        ctx = _route_context(
            runtime, call,
            parent_extra_prompt_sections={"preloaded_skills": "### Skill: pdf\nFull pdf instructions."},
        )
        result = await tool.handle(call, ctx)

        assert result.is_error is False
        assert "generate the pdf report" in captured["goal"].description
        assert "Full pdf instructions." in captured["goal"].description

    async def test_no_op_when_parent_has_nothing_preloaded(self) -> None:
        runtime = AgentRuntime()
        captured: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            captured["goal"] = goal
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime)
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "compute 2 + 2"})

        await tool.handle(call, _route_context(runtime, call))

        assert captured["goal"].description == "compute 2 + 2"

    async def test_combines_with_share_parent_results_digest(self) -> None:
        """Both handoff channels are additive: the parent's already-loaded
        skill guidance AND its shared tool-result digest can land in the
        same child goal without either clobbering the other."""
        runtime = AgentRuntime()
        captured: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            captured["goal"] = goal
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime, share_parent_results=True)
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "build a PDF report of the tickets"})

        ctx = _route_context(
            runtime, call,
            messages=_parent_messages_with_jira_result(),
            parent_extra_prompt_sections={"preloaded_skills": "### Skill: pdf\nFull pdf instructions."},
        )
        await tool.handle(call, ctx)

        assert "Full pdf instructions." in captured["goal"].description
        assert "A-1" in captured["goal"].description


class TestAgentToolResultNote:
    """`result_note=...` — the presentation rule appended to the child's
    successful output so it sits directly adjacent to the data it governs
    (instruction proximity), instead of relying on the calling agent's
    system prompt hundreds of thousands of tokens away."""

    _NOTE = "[SYSTEM NOTE] present in full; do not summarize."

    @staticmethod
    def _tool_and_call(runtime: AgentRuntime, *, note: str | None) -> tuple[AgentTool, ToolCall]:
        spec = AgentSpec(name="child", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(spec, runtime, result_note=note)
        return tool, ToolCall(id="c1", name="child", arguments={"goal": "explore"})

    async def test_handle_appends_note_to_successful_string_output(self) -> None:
        runtime = AgentRuntime()

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            return AgentResult(goal=goal, output="detailed findings", success=True)

        runtime.run_child = _fake_run_child
        tool, call = self._tool_and_call(runtime, note=self._NOTE)

        result = await tool.handle(call, _route_context(runtime, call))

        assert result.is_error is False
        assert result.content == f"detailed findings\n\n{self._NOTE}"

    async def test_handle_leaves_error_results_untouched(self) -> None:
        runtime = AgentRuntime()

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            return AgentResult(goal=goal, success=False, error="child failed")

        runtime.run_child = _fake_run_child
        tool, call = self._tool_and_call(runtime, note=self._NOTE)

        result = await tool.handle(call, _route_context(runtime, call))

        assert result.is_error is True
        assert self._NOTE not in str(result.content)

    async def test_handle_without_note_returns_output_verbatim(self) -> None:
        runtime = AgentRuntime()

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            return AgentResult(goal=goal, output="detailed findings", success=True)

        runtime.run_child = _fake_run_child
        tool, call = self._tool_and_call(runtime, note=None)

        result = await tool.handle(call, _route_context(runtime, call))

        assert result.content == "detailed findings"

    async def test_handle_leaves_non_string_output_untouched(self) -> None:
        runtime = AgentRuntime()
        payload = {"findings": ["a", "b"]}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            return AgentResult(goal=goal, output=payload, success=True)

        runtime.run_child = _fake_run_child
        tool, call = self._tool_and_call(runtime, note=self._NOTE)

        result = await tool.handle(call, _route_context(runtime, call))

        assert result.content == payload

    async def test_execute_appends_note_too(self) -> None:
        runtime = AgentRuntime()

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            return AgentResult(goal=goal, output="detailed findings", success=True)

        runtime.run_child = _fake_run_child
        tool, _call = self._tool_and_call(runtime, note=self._NOTE)

        output = await tool.execute(goal="explore")

        assert output.success is True
        assert output.data == f"detailed findings\n\n{self._NOTE}"

    def test_internal_exploration_agent_definition_carries_the_note(self) -> None:
        definition = next(
            d for d in DOMAIN_AGENT_DEFINITIONS if d.name == "internal_exploration_agent"
        )
        assert definition.result_note
        assert "About these findings:" in definition.result_note
        assert "specific facts" in definition.result_note
        assert "Citation markers" in definition.result_note


def _parent_messages_with_jira_result() -> list:
    return [
        UserMessage(content="build a report of my tickets"),
        AssistantMessage(tool_calls=[ToolCall(id="c-jira", name="jira_search_issues", arguments={})]),
        ToolMessage(content='{"tickets": ["A-1", "A-2"]}', tool_call_id="c-jira"),
    ]


class TestAgentToolShareParentResults:
    """`share_parent_results=True` — the deterministic parent -> child data
    handoff (see `parent_results.py`/`input_staging.py`): the calling
    agent's own recent tool results reach the child both inline (in its
    goal) and as a staged file, without the calling MODEL having to paste
    data into the goal text itself."""

    async def test_child_goal_contains_a_digest_of_parent_results(self) -> None:
        runtime = AgentRuntime()
        captured: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            captured["goal"] = goal
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime, share_parent_results=True)
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "build a PDF report of the tickets"})

        result = await tool.handle(call, _route_context(runtime, call, messages=_parent_messages_with_jira_result()))

        assert result.is_error is False
        assert "jira_search_issues" in captured["goal"].description
        assert "A-1" in captured["goal"].description
        assert "A-2" in captured["goal"].description
        assert "build a PDF report of the tickets" in captured["goal"].description

    async def test_full_data_is_staged_as_a_file_visible_inside_run_child(self) -> None:
        runtime = AgentRuntime()
        seen_staged: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            seen_staged["files"] = peek_staged_input_files()
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime, share_parent_results=True)
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "build a PDF report"})

        await tool.handle(call, _route_context(runtime, call, messages=_parent_messages_with_jira_result()))

        staged = seen_staged["files"]
        assert staged is not None
        assert "input/parent_tool_results.json" in staged
        payload = json.loads(staged["input/parent_tool_results.json"])
        # `parent_results_as_json`'s budget-aware envelope — see the
        # end-to-end delegation test above for the same shape.
        assert payload["results"] == [{"tool": "jira_search_issues", "content": {"tickets": ["A-1", "A-2"]}}]
        assert payload["_meta"]["truncated"] is False

    async def test_staging_is_cleared_once_handle_returns(self) -> None:
        runtime = AgentRuntime()

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime, share_parent_results=True)
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "build a PDF report"})

        await tool.handle(call, _route_context(runtime, call, messages=_parent_messages_with_jira_result()))

        assert peek_staged_input_files() is None

    async def test_flag_off_behaves_identically_to_today(self) -> None:
        runtime = AgentRuntime()
        captured: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            captured["goal"] = goal
            captured["staged"] = peek_staged_input_files()
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime)  # share_parent_results defaults to False
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "build a PDF report"})

        await tool.handle(call, _route_context(runtime, call, messages=_parent_messages_with_jira_result()))

        assert captured["goal"].description == "build a PDF report"
        assert captured["staged"] is None

    async def test_no_relevant_parent_results_leaves_goal_untouched(self) -> None:
        runtime = AgentRuntime()
        captured: dict[str, Any] = {}

        async def _fake_run_child(spec, goal, parent_run_ctx, **kwargs):
            captured["goal"] = goal
            captured["staged"] = peek_staged_input_files()
            return AgentResult(goal=goal, output="ok", success=True)

        runtime.run_child = _fake_run_child

        child_spec = AgentSpec(name="coding_agent", system_prompt="x", model=ModelSpec(provider="scripted", model="m"))
        tool = AgentTool(child_spec, runtime, share_parent_results=True)
        call = ToolCall(id="c1", name="coding_agent", arguments={"goal": "compute 2 + 2"})

        await tool.handle(call, _route_context(runtime, call, messages=[UserMessage(content="compute 2 + 2")]))

        assert captured["goal"].description == "compute 2 + 2"
        assert captured["staged"] is None
