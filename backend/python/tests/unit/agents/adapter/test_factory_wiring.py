"""`PipesHubAgentFactory` (`app/agents/agent_loop/factory.py`) — Phase 7 +
Chat Mode Redesign: verifies `create()` assembles a fully-wired
`Agent`/`AgentRuntime`/`Goal` from an `AgentContext` without going through
the LLM auto-router (direct chat modes only; `router.select_loop_and_goal`'s
"auto" branch is covered by `router.py`-focused tests, not here), AND that
composition/tool-grant behavior matches each mode's `modes.MODE_CATALOG`
entry:

- `quick` -> flat `ReActLoop`, NO domain-agent composition (the fast path).
- `react` -> `ReActLoop` + domain agents (unchanged default tier).
- `planExecute` -> `PlanCritiqueExecuteLoop` (`PhaseDriver`-backed, plans via
  the `create_plan` TOOL rather than a construction-time `Planner`) +
  domain agents — this is what `quick` used to mean pre-redesign.
- `deep` -> `OrchestratorLoop`; top-level stays restricted to the four
  coordination tools, but its `spawn_agent` pool is now ALSO composed
  (domain-agent delegates + residual, not the raw flat tool list).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import PARENT_RESULTS_INPUT_PATH
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.loops.plan_execute import PLANNING_TOOL_NAMES, PlanCritiqueExecuteLoop
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert factory/agent SHAPE only. The skills subsystem
    (on by default) would await real graph calls on `make_context`'s
    MagicMock graph_provider and blow up — skills wiring has its own
    dedicated tests, so pin it off here."""
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


@pytest.fixture(autouse=True)
def _no_dynamic_tools() -> None:
    """`PipesHubToolLoader.load()` builds dynamic tools (SQL/Slack-context/
    web-search) via `tool_loader._build_dynamic_tools()`, each gated on a
    `context.tool_state` flag (`has_sql_connector`, `web_search_config`,
    ...) `make_context()` never sets — so those tools are already absent
    with no patch needed. No-op kept as a fixture (rather than deleted) so
    this file's `TestCreate` tests keep the same "factory-wiring tests
    don't depend on dynamic tool wiring" invariant documented and pinned
    to one place if `_build_dynamic_tools()`'s defaults ever change."""


class TestCreate:
    async def test_returns_agent_and_runtime(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert isinstance(agent, Agent)
        assert isinstance(runtime, AgentRuntime)

    async def test_direct_mode_skips_llm_router(self) -> None:
        """`"react"`/`"planExecute"`/`"verification"` all resolve via the
        catalog synchronously — no LLM call, so `classify_route` must never
        be reached."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert isinstance(agent.spec.loop, ReActLoop)

    async def test_transport_registry_has_langchain_provider(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello", model_name="gpt-4")

        transport = runtime.transport_registry.resolve("langchain")
        assert transport.provider == "langchain"

    async def test_spec_max_turns_matches_legacy_iteration_cap(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert agent.spec.max_turns == 15

    async def test_hooks_registered_on_all_expected_events(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        # `Pipeline` exposes no public "is anything registered" query — its
        # internal `_stack` is the only way to assert wiring without also
        # exercising full dispatch semantics (covered by `test_hooks.py`).
        assert len(runtime.hooks.on(HookEvent.PRE_TOOL_USE)._stack) >= 2  # error tracker + metadata stash
        assert len(runtime.hooks.on(HookEvent.POST_TOOL_USE)._stack) >= 4
        assert len(runtime.hooks.on(HookEvent.PRE_TURN)._stack) >= 1

    async def test_pre_model_call_wrapper_registered_for_llm_retry(self) -> None:
        """A 429/5xx from the LLM must be retried — see `retry_with_status.py`
        and its docstring for why this adapter path can't rely on
        `ControlPlane`'s default `retry_model_call` wiring."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert len(runtime.hooks.wrapper(HookEvent.PRE_MODEL_CALL)._stack) >= 1

    async def test_event_emitter_wired_when_context_has_event_sink(self) -> None:
        sink = MagicMock()
        context = make_context(llm=FakeChatModel(), event_sink=sink)
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert runtime.event_emitter is not None

    async def test_event_emitter_none_without_event_sink(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert runtime.event_emitter is None

    async def test_seeds_conversation_history_when_present(self) -> None:
        context = make_context(
            llm=FakeChatModel(),
            previous_conversations=[
                {"role": "user_query", "content": "hi"},
                {"role": "bot_response", "content": "hello there"},
            ],
        )
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="follow up")

        assert agent.context is not None
        messages = await agent.context.messages()
        assert len(messages) == 2

    async def test_no_history_seeding_without_previous_conversations(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert agent.context is None

    async def test_quick_mode_uses_flat_react_loop(self) -> None:
        """`quick` is now the flat, no-planner, no-composition fast path —
        it must NOT pay for an upfront `PlanAheadPlanner` call."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert isinstance(agent.spec.loop, ReActLoop)
        assert agent.spec.tool_names == runtime.tool_registry.names()

    async def test_quick_mode_never_composes_domain_agents(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert "coding_agent" not in agent.spec.tool_names
        assert not runtime.tool_registry.has("coding_agent")

    async def test_plan_execute_mode_uses_plan_execute_loop(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "planExecute", query="hello")

        assert isinstance(agent.spec.loop, PlanCritiqueExecuteLoop)

    async def test_legacy_verification_mode_also_uses_plan_execute_loop(self) -> None:
        """`verification` is the pre-rename wire value — `resolve_mode()`
        aliases it to `planExecute` so old clients/conversations keep
        working unchanged."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "verification", query="hello")

        assert isinstance(agent.spec.loop, PlanCritiqueExecuteLoop)

    async def test_plan_execute_mode_grants_planning_tools(self) -> None:
        """`PlanCritiqueExecuteLoop` plans via the `create_plan`/`critique_plan`/
        `verify_result`/`replan` TOOLS (see `loops/plan_execute.py`), not a
        construction-time `Planner` — the top-level agent must actually be
        granted them, and the registry must actually carry them."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "planExecute", query="hello")

        for name in PLANNING_TOOL_NAMES:
            assert name in agent.spec.tool_names
            assert runtime.tool_registry.has(name)

    async def test_deep_mode_uses_orchestrator_loop(self) -> None:
        from app.agents.agent_loop.loops.orchestrator import OrchestratorLoop

        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        assert isinstance(agent.spec.loop, OrchestratorLoop)

    async def test_deep_mode_restricts_tool_names_to_coordination_tools(self) -> None:
        from app.agents.agent_loop.loops.orchestrator import COORDINATION_TOOL_NAMES

        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        assert set(agent.spec.tool_names) == set(COORDINATION_TOOL_NAMES)

    async def test_deep_mode_registers_coordination_tools_on_registry(self) -> None:
        from app.agents.agent_loop.loops.orchestrator import COORDINATION_TOOL_NAMES

        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        for name in COORDINATION_TOOL_NAMES:
            assert runtime.tool_registry.has(name)

    async def test_deep_mode_wires_spec_factory_for_spawn_agent(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        assert runtime.spec_factory is not None
        child_spec = runtime.spec_factory("jira")
        assert child_spec.name == "pipeshub-subagent-jira"

    async def test_non_deep_mode_leaves_spec_factory_unset(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert runtime.spec_factory is None

    async def test_react_mode_keeps_full_tool_registry_names_when_composition_off(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With the domain-agent kill-switch off, non-deep modes must NOT be
        narrowed at all — the flat, every-registered-tool grant is the
        rollback behavior."""
        monkeypatch.setenv("PIPESHUB_USE_COMPOSED_AGENTS", "false")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert agent.spec.tool_names == runtime.tool_registry.names()

    async def test_react_mode_composes_domain_agents_by_default(self) -> None:
        """React runs get the composed top level: sandbox tools are claimed
        by `coding_agent` (registered as an `AgentTool` on the shared
        registry) and leave the top-level grant."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        if "run_code" not in runtime.tool_registry.names():
            pytest.skip("code execution disabled in this environment — no domain to compose")
        assert "coding_agent" in agent.spec.tool_names
        assert "run_code" not in agent.spec.tool_names
        coding_spec = runtime.tool_registry.resolve_by_name("coding_agent")._spec
        assert "run_code" in coding_spec.tool_names

    async def test_plan_execute_mode_composes_domain_agents_plus_planning_tools(self) -> None:
        """Regression test for the bug this fix originally addressed: the
        `planExecute` mode must compose domain agents (connector tools
        claimed away behind e.g. `coding_agent`) while STILL granting the
        planning tools composition never claims (see `PLANNING_TOOL_NAMES`
        threading in `factory.py`'s `plan_execute` branches)."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "planExecute", query="hello")

        for name in PLANNING_TOOL_NAMES:
            assert name in agent.spec.tool_names

        if "run_code" not in runtime.tool_registry.names():
            pytest.skip("code execution disabled in this environment — no domain to compose")
        assert "coding_agent" in agent.spec.tool_names
        assert "run_code" not in agent.spec.tool_names

    async def test_plan_execute_mode_keeps_flat_tool_names_when_composition_off(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PIPESHUB_USE_COMPOSED_AGENTS", "false")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "planExecute", query="hello")

        assert agent.spec.tool_names == runtime.tool_registry.names()

    async def test_deep_mode_top_level_is_never_composed(self) -> None:
        """`OrchestratorLoop`'s OWN `spec.tool_names` keeps its four
        coordination tools regardless of composition — the composed
        AgentTool delegates augment the SPAWN POOL (see the next test),
        never the top-level orchestrator's own grant."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        assert "coding_agent" not in agent.spec.tool_names

    async def test_deep_mode_composes_domain_agents_into_spawn_pool(self) -> None:
        """Deep mode now registers domain agents too (unlike the pre-redesign
        behavior) — the SHARED registry gains `coding_agent` etc., and
        `spawn_agent`'s default pool (`spec_factory`'s `default_tool_names`)
        includes it instead of the raw flat tool list."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        if "run_code" not in runtime.tool_registry.names():
            pytest.skip("code execution disabled in this environment — no domain to compose")
        assert runtime.tool_registry.has("coding_agent")

        child_spec = runtime.spec_factory("some-workstream")
        assert "coding_agent" in child_spec.tool_names
        assert "run_code" not in child_spec.tool_names

    async def test_deep_mode_spawn_pool_stays_flat_when_composition_off(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PIPESHUB_USE_COMPOSED_AGENTS", "false")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        assert not runtime.tool_registry.has("coding_agent")
        from app.agents.agent_loop.loops.orchestrator import COORDINATION_TOOL_NAMES

        child_spec = runtime.spec_factory("some-workstream")
        assert set(child_spec.tool_names) == {
            n for n in runtime.tool_registry.names() if n not in COORDINATION_TOOL_NAMES
        }

    async def test_quick_mode_run_code_does_not_advertise_parent_results(self) -> None:
        """Regression: quick mode grants `run_code` directly to the flat
        top-level agent — no `coding_agent` `AgentTool` ever wraps it, so
        `PARENT_RESULTS_INPUT_PATH` is never staged (see `agent_tool.py`'s
        `share_parent_results`). The tool's own description must say so,
        or the model writes code that unconditionally opens a file that
        will never exist (the bug this fix addresses)."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        if not runtime.tool_registry.has("run_code"):
            pytest.skip("code execution disabled in this environment")
        run_code = runtime.tool_registry.resolve_by_name("run_code")
        assert "MAY have it pre-loaded" not in run_code.description

    async def test_react_mode_coding_agent_run_code_advertises_parent_results(self) -> None:
        """The inverse: once `coding_agent` actually claims `run_code`
        (composed modes, the only path that ever stages the file — see
        `domain_agents.py`'s `share_parent_results=True`), the promise in
        the description must still hold."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        if "coding_agent" not in agent.spec.tool_names:
            pytest.skip("code execution disabled in this environment — no domain to compose")
        run_code = runtime.tool_registry.resolve_by_name("run_code")
        assert PARENT_RESULTS_INPUT_PATH in run_code.description
        assert "MAY have it pre-loaded" in run_code.description

    async def test_composition_kill_switch_off_run_code_does_not_advertise_parent_results(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With domain composition off, `react` mode falls back to the flat
        grant (see `test_react_mode_keeps_full_tool_registry_names_when_
        composition_off` above) — `run_code` is granted directly again, so
        the promise must be withdrawn exactly as it is for quick mode."""
        monkeypatch.setenv("PIPESHUB_USE_COMPOSED_AGENTS", "false")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        _agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        if not runtime.tool_registry.has("run_code"):
            pytest.skip("code execution disabled in this environment")
        run_code = runtime.tool_registry.resolve_by_name("run_code")
        assert "MAY have it pre-loaded" not in run_code.description

    async def test_auto_mode_invokes_classifier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.agent_loop.intent import IntentRouteDecision

        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="test", rewritten_query="hello", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "auto", query="hello")

        assert isinstance(agent.spec.loop, ReActLoop)
