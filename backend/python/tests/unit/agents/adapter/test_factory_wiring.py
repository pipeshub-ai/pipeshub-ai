"""`PipesHubAgentFactory` (`app/agents/agent_loop/factory.py`) — Phase 7:
verifies `create()` assembles a fully-wired `Agent`/`AgentRuntime`/`Goal`
from an `AgentContext` without going through the LLM auto-router (direct
chat modes only; `router.select_loop_and_goal`'s "auto" branch is covered
by `router.py`-focused tests, not here)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.factory import PipesHubAgentFactory
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


@pytest.fixture(autouse=True)
def _no_dynamic_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """`PipesHubToolLoader.load()` calls into `get_agent_tools_with_schemas`,
    which needs a fully-populated `ChatState` — factory-wiring tests only
    care about the resulting `Agent`/`AgentRuntime` shape, so stub it out
    to an empty tool list rather than constructing a realistic state."""
    monkeypatch.setattr(
        "app.agents.agent_loop.tool_loader.get_agent_tools_with_schemas",
        lambda state: [],
    )


class TestCreate:
    async def test_returns_agent_and_runtime(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert isinstance(agent, Agent)
        assert isinstance(runtime, AgentRuntime)

    async def test_direct_mode_skips_llm_router(self) -> None:
        """`"react"`/`"verification"` resolve to `ReActLoop()` synchronously
        — no LLM call, so `classify_route` must never be reached."""
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

        assert agent._context is not None
        messages = await agent._context.messages()
        assert len(messages) == 2

    async def test_no_history_seeding_without_previous_conversations(self) -> None:
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "react", query="hello")

        assert agent._context is None

    async def test_quick_mode_uses_plan_execute_loop(self) -> None:
        from app.agent_loop_lib.agent.loops import PlanExecuteLoop

        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert isinstance(agent.spec.loop, PlanExecuteLoop)

    async def test_quick_mode_threads_resolved_sandbox_network_flag_into_planner(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`create()` resolves `sandbox_network_enabled()` ONCE and threads
        the SAME value into the planner (via `select_loop_and_goal`) as it
        does into the sandbox manager/tool — this pins the planner side of
        that contract."""
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "false")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert agent.spec.loop._planner._sandbox_has_network is False

        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "true")
        context2 = make_context(llm=FakeChatModel())
        agent2, _runtime2, _goal2, _clarifying2 = await factory.create(context2, context2.llm, "quick", query="hello")

        assert agent2.spec.loop._planner._sandbox_has_network is True

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

    async def test_quick_mode_composes_domain_agents_and_steers_planner_with_same_names(self) -> None:
        """Regression test for the bug this fix addresses: quick mode
        (`PlanExecuteLoop`) was previously exempt from composition, AND its
        `PlanAheadPlanner` was seeded with the flat tool names even when
        composition was applied elsewhere — the planner and the executing
        agent must agree on the SAME composed top-level names."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        if "run_code" not in runtime.tool_registry.names():
            pytest.skip("code execution disabled in this environment — no domain to compose")
        assert "coding_agent" in agent.spec.tool_names
        assert "run_code" not in agent.spec.tool_names

        planner_tool_names = agent.spec.loop._planner._tool_names
        assert "coding_agent" in planner_tool_names
        assert "run_code" not in planner_tool_names

    async def test_quick_mode_keeps_flat_tool_names_when_composition_off(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PIPESHUB_USE_COMPOSED_AGENTS", "false")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "quick", query="hello")

        assert agent.spec.tool_names == runtime.tool_registry.names()
        assert agent.spec.loop._planner._tool_names == runtime.tool_registry.names()

    async def test_deep_mode_is_never_composed(self) -> None:
        """OrchestratorLoop keeps its own spawn_agent dispatch — the
        composed AgentTool delegates must not be registered for it."""
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(context, context.llm, "deep", query="hello")

        assert "coding_agent" not in agent.spec.tool_names
        assert not runtime.tool_registry.has("coding_agent")

    async def test_auto_mode_invokes_classifier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agents.agent_loop.intent import IntentRouteDecision

        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="test", rewritten_query="hello", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(context, context.llm, "auto", query="hello")

        assert isinstance(agent.spec.loop, ReActLoop)
