"""`select_loop_and_goal` (`app/agents/agent_loop/router.py`) — covers
`chatMode` -> `LoopStrategy` resolution (via `modes.MODE_CATALOG`) AND the
intent-parsed `Goal` every mode now receives, focused on:

- `_build_loop`'s `loop_kind` -> `LoopStrategy` mapping (`"react"` ->
  `ReActLoop`, `"plan_execute"` -> `PlanCritiqueExecuteLoop`,
  `"orchestrator"` -> `OrchestratorLoop`), independent of mode NAME.
  `tool_names`/`sandbox_has_network`/`opik_active`/`opik_project_name` are
  accepted but unused by every loop kind today (`plan_execute` and
  `orchestrator` both plan via the `create_plan` TOOL at run time, not a
  construction-time `Planner` — see `_build_loop`'s docstring) — this
  module used to have a whole block of tests asserting they reached a
  `PlanAheadPlanner`; that planner/that threading no longer exists.
- `include_routing` only being `True` for `auto`/unrecognized modes —
  every mode `resolve_mode()` recognizes (canonical name or alias) never
  pays for a routing classification, only intent parsing.
- `_build_goal`'s contract: the rewritten query becomes `Goal.description`,
  falling back to the raw query when blank, with the raw query always
  preserved verbatim as a constraint, and `decision.gaps` landing on
  `Goal.gaps` unchanged.
- The 3rd return value (`clarifying_questions`) passing through verbatim
  from `decision.clarifying_questions`, and the 4th (`ModeDefinition`)
  matching the resolved mode, regardless of which mode/route was selected.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent.loops import ReActLoop
from app.agents.agent_loop.intent import IntentRouteDecision
from app.agents.agent_loop.loops.orchestrator import OrchestratorLoop
from app.agents.agent_loop.loops.plan_execute import PlanCritiqueExecuteLoop
from app.agents.agent_loop.modes import resolve_mode
from app.agents.agent_loop.router import (
    _build_goal,
    _build_loop,
    select_loop_and_goal,
)
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


class TestBuildLoop:
    def test_react_loop_kind_uses_react_loop(self) -> None:
        loop = _build_loop("react", FakeChatModel())
        assert isinstance(loop, ReActLoop)

    def test_orchestrator_loop_kind_uses_orchestrator_loop(self) -> None:
        loop = _build_loop("orchestrator", FakeChatModel())
        assert isinstance(loop, OrchestratorLoop)

    def test_unrecognized_loop_kind_falls_back_to_react(self) -> None:
        loop = _build_loop("something-else", FakeChatModel())
        assert isinstance(loop, ReActLoop)

    def test_plan_execute_loop_kind_uses_plan_critique_execute_loop(self) -> None:
        loop = _build_loop("plan_execute", FakeChatModel())
        assert isinstance(loop, PlanCritiqueExecuteLoop)

    def test_plan_execute_loop_kind_ignores_tool_names_and_sandbox_flag(self) -> None:
        """Unlike the old `PlanAheadPlanner`-backed shape, nothing about
        the constructed loop depends on these — they're accepted for call-
        site symmetry only (see this module's docstring)."""
        loop = _build_loop(
            "plan_execute", FakeChatModel(),
            tool_names=["run_code", "web_search"], sandbox_has_network=True,
        )
        assert isinstance(loop, PlanCritiqueExecuteLoop)


class TestBuildGoal:
    def test_uses_rewritten_query_as_description(self) -> None:
        decision = IntentRouteDecision(reasoning="r", rewritten_query="clear restatement")
        goal = _build_goal("raw query", decision)
        assert goal.description == "clear restatement"

    def test_falls_back_to_raw_query_when_rewritten_is_blank(self) -> None:
        decision = IntentRouteDecision(reasoning="r", rewritten_query="   ")
        goal = _build_goal("raw query", decision)
        assert goal.description == "raw query"

    def test_preserves_raw_query_verbatim_as_constraint(self) -> None:
        decision = IntentRouteDecision(reasoning="r", rewritten_query="rewritten")
        goal = _build_goal("do the thing for Acme Corp", decision)
        assert 'Original user query: "do the thing for Acme Corp"' in goal.constraints

    def test_threads_requirements_and_success_criteria(self) -> None:
        decision = IntentRouteDecision(
            reasoning="r", rewritten_query="q",
            requirements=["req1"], success_criteria=["done when x"],
        )
        goal = _build_goal("q", decision)
        assert goal.requirements == ["req1"]
        assert goal.success_criteria == ["done when x"]

    def test_threads_gaps_onto_goal(self) -> None:
        decision = IntentRouteDecision(
            reasoning="r", rewritten_query="q", gaps=["missing destination city"],
        )
        goal = _build_goal("q", decision)
        assert goal.gaps == ["missing destination city"]

    def test_gaps_default_to_empty(self) -> None:
        decision = IntentRouteDecision(reasoning="r", rewritten_query="q")
        goal = _build_goal("q", decision)
        assert goal.gaps == []


class TestSelectLoopAndGoal:
    async def test_quick_mode_bypasses_intent_parsing_entirely(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`quick` has `skip_intent=True` on its catalog entry (`modes.py`) —
        `parse_intent_and_route()` must never be called, and `Goal` is built
        straight from the raw query (no rewrite, no requirements/success-
        criteria/gaps extraction — there was no model call to produce them)."""
        called = False

        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            nonlocal called
            called = True
            return IntentRouteDecision(reasoning="r", rewritten_query="a rewritten query")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, clarifying, mode = await select_loop_and_goal(
            chat_mode="quick", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)
        assert mode.name == "quick"
        assert mode.compose_domain_agents is False
        assert called is False
        assert goal.description == "hello"
        assert goal.requirements == []
        assert goal.success_criteria == []
        assert goal.gaps == []
        assert 'Original user query: "hello"' in goal.constraints
        assert clarifying == []

    async def test_react_mode_returns_react_loop_without_routing_classification(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="react", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)
        assert mode.name == "react"
        assert mode.compose_domain_agents is True
        assert captured["include_routing"] is False
        assert goal.description == "hello"

    async def test_plan_execute_mode_returns_plan_execute_loop_and_skips_routing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="planExecute", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, PlanCritiqueExecuteLoop)
        assert mode.name == "planExecute"
        assert captured["include_routing"] is False

    async def test_legacy_verification_mode_resolves_to_plan_execute_and_skips_routing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="verification", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, PlanCritiqueExecuteLoop)
        assert mode.name == "planExecute"
        assert captured["include_routing"] is False

    async def test_plan_execute_mode_tool_names_argument_is_accepted_but_does_not_affect_the_loop(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="Create a PDF of GitHub repos")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="planExecute",
            query="Create a PDF of GitHub repos",
            llm=context.llm,
            context=context,
            tool_names=["run_code", "retrieval.search_internal_knowledge"],
        )

        assert isinstance(loop, PlanCritiqueExecuteLoop)
        assert mode.name == "planExecute"
        assert goal.description == "Create a PDF of GitHub repos"
        assert captured["include_routing"] is False

    async def test_threads_transport_registry_into_intent_call(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`PipesHubAgentFactory.create()` builds one `TransportRegistry` with
        "langchain" already registered — it must reach `parse_intent_and_route`
        unchanged so the intent call resolves that SAME cached transport
        rather than `parse_intent_and_route` building a throwaway second one."""
        from app.agent_loop_lib.transport.registry import TransportRegistry

        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())
        sentinel_registry = TransportRegistry()

        await select_loop_and_goal(
            chat_mode="react", query="hello", llm=context.llm, context=context,
            transport_registry=sentinel_registry,
        )

        assert captured["transport_registry"] is sentinel_registry

    async def test_deep_mode_returns_orchestrator_loop_and_skips_routing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="deep", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, OrchestratorLoop)
        assert mode.name == "deep"
        assert mode.compose_domain_agents is True
        assert captured["include_routing"] is False

    async def test_auto_mode_requests_routing_classification_and_threads_tool_names(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(
                reasoning="r", rewritten_query="Create a PDF of GitHub repos", route="quick",
            )

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="auto",
            query="Create a PDF of GitHub repos",
            llm=context.llm,
            context=context,
            tool_names=["run_code"],
        )

        # Auto's "quick" tier verdict now resolves to the FLAT ReAct catalog
        # entry (no domain-agent composition) -- `tool_names` is accepted
        # but ignored by `_build_loop` for `loop_kind == "react"`.
        assert isinstance(loop, ReActLoop)
        assert mode.name == "quick"
        assert goal.description == "Create a PDF of GitHub repos"
        assert captured["include_routing"] is True

    async def test_auto_mode_react_tier_threads_tool_names_into_plan_execute_style_modes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto's "react" tier verdict resolves to the plain ReAct catalog
        entry too (composed, but still no planner) -- this pins that only
        `planExecute`'s `loop_kind` actually consumes `tool_names`."""
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="q", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="auto",
            query="q",
            llm=context.llm,
            context=context,
            tool_names=["run_code"],
        )

        assert isinstance(loop, ReActLoop)
        assert mode.name == "react"

    async def test_auto_mode_deep_tier_threads_sandbox_has_network_into_planner(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="q", route="deep")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="auto",
            query="q",
            llm=context.llm,
            context=context,
            tool_names=["run_code"],
            sandbox_has_network=True,
        )

        assert isinstance(loop, OrchestratorLoop)
        assert mode.name == "deep"

    async def test_unrecognized_mode_also_requests_routing_classification(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="some-unknown-mode", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)
        assert mode.name == "react"
        assert captured["include_routing"] is True

    async def test_auto_mode_falls_back_to_react_when_route_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="hello", route=None)

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying, mode = await select_loop_and_goal(
            chat_mode="auto", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)
        assert mode.name == "react"

    async def test_clarifying_questions_empty_by_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="hello", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        _loop, _goal, clarifying, _mode = await select_loop_and_goal(
            chat_mode="auto", query="hello", llm=context.llm, context=context,
        )

        assert clarifying == []

    async def test_clarifying_questions_passed_through_from_decision(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput

        question = AskUserQuestionItemInput(
            question="Which project should this apply to?",
            options=[{"label": "Project A"}, {"label": "Project B"}, {"label": "Project C"}],
            multiSelect=False,
        )

        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(
                reasoning="ambiguous",
                rewritten_query="do the thing",
                gaps=["missing project name"],
                clarifying_questions=[question],
            )

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, clarifying, _mode = await select_loop_and_goal(
            chat_mode="auto", query="do the thing", llm=context.llm, context=context,
        )

        # Loop/Goal are still built normally -- callers gate on `clarifying`.
        assert isinstance(loop, ReActLoop)
        assert goal.gaps == ["missing project name"]
        assert clarifying == [question]

    async def test_returned_mode_matches_resolve_mode_for_every_explicit_mode(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        for chat_mode in ("quick", "react", "planExecute", "verification", "deep"):
            _loop, _goal, _clarifying, mode = await select_loop_and_goal(
                chat_mode=chat_mode, query="hello", llm=context.llm, context=context,
            )
            assert mode is resolve_mode(chat_mode)
