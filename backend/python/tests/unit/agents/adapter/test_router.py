"""`select_loop_and_goal` (`app/agents/agent_loop/router.py`) — covers
`chatMode` -> `LoopStrategy` resolution AND the intent-parsed `Goal` every
mode now receives, focused on:

- `tool_names` threading into `PlanAheadPlanner` for the "quick" route
  (unchanged from the pre-intent `select_loop()` behavior it replaces).
- `include_routing` only being `True` for `auto`/unrecognized modes — direct
  modes (`react`/`verification`) and explicit modes (`quick`/`deep`) never
  pay for a routing classification, only intent parsing.
- `_build_goal`'s contract: the rewritten query becomes `Goal.description`,
  falling back to the raw query when blank, with the raw query always
  preserved verbatim as a constraint, and `decision.gaps` landing on
  `Goal.gaps` unchanged.
- The third return value (`clarifying_questions`) passing through verbatim
  from `decision.clarifying_questions` regardless of which mode/route was
  selected.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent.loops import PlanExecuteLoop, ReActLoop
from app.agent_loop_lib.modules.pipeline.planner.plan_ahead import PlanAheadPlanner
from app.agent_loop_lib.transport.opik_tracing import OpikTracingTransport
from app.agents.agent_loop.intent import IntentRouteDecision
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.agents.agent_loop.loops.orchestrator import OrchestratorLoop
from app.agents.agent_loop.router import (
    _build_goal,
    _loop_for_route,
    select_loop_and_goal,
)
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context


class TestLoopForRoute:
    def test_quick_route_uses_plan_execute_loop(self) -> None:
        loop = _loop_for_route("quick", FakeChatModel())
        assert isinstance(loop, PlanExecuteLoop)

    def test_deep_route_uses_orchestrator_loop(self) -> None:
        loop = _loop_for_route("deep", FakeChatModel())
        assert isinstance(loop, OrchestratorLoop)

    def test_unrecognized_route_falls_back_to_react(self) -> None:
        loop = _loop_for_route("something-else", FakeChatModel())
        assert isinstance(loop, ReActLoop)

    def test_quick_route_seeds_planner_with_given_tool_names(self) -> None:
        loop = _loop_for_route("quick", FakeChatModel(), tool_names=["run_code", "web_search"])
        assert isinstance(loop, PlanExecuteLoop)
        planner = loop._planner
        assert isinstance(planner, PlanAheadPlanner)
        assert planner._tool_names == ["run_code", "web_search"]

    def test_quick_route_without_tool_names_leaves_planner_tool_list_empty(self) -> None:
        loop = _loop_for_route("quick", FakeChatModel())
        assert loop._planner._tool_names == []

    def test_quick_route_defaults_sandbox_has_network_to_false(self) -> None:
        loop = _loop_for_route("quick", FakeChatModel())
        assert loop._planner._sandbox_has_network is False

    def test_quick_route_threads_sandbox_has_network_into_planner(self) -> None:
        loop = _loop_for_route("quick", FakeChatModel(), sandbox_has_network=True)
        assert loop._planner._sandbox_has_network is True

    def test_quick_route_wraps_planner_transport_with_opik_when_active(self) -> None:
        loop = _loop_for_route(
            "quick", FakeChatModel(), opik_active=True, opik_project_name="proj",
        )
        transport = loop._planner._model.transport
        assert isinstance(transport, OpikTracingTransport)

    def test_quick_route_leaves_planner_transport_unwrapped_when_opik_inactive(self) -> None:
        loop = _loop_for_route("quick", FakeChatModel(), opik_active=False)
        transport = loop._planner._model.transport
        assert isinstance(transport, LangChainTransport)
        assert not isinstance(transport, OpikTracingTransport)


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
    async def test_react_mode_returns_react_loop_without_routing_classification(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, _clarifying = await select_loop_and_goal(
            chat_mode="react", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)
        assert captured["include_routing"] is False
        assert goal.description == "hello"

    async def test_verification_mode_skips_routing_classification(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying = await select_loop_and_goal(
            chat_mode="verification", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)
        assert captured["include_routing"] is False

    async def test_quick_mode_threads_tool_names_into_planner_and_skips_routing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="Create a PDF of GitHub repos")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, goal, _clarifying = await select_loop_and_goal(
            chat_mode="quick",
            query="Create a PDF of GitHub repos",
            llm=context.llm,
            context=context,
            tool_names=["run_code", "retrieval.search_internal_knowledge"],
        )

        assert isinstance(loop, PlanExecuteLoop)
        assert loop._planner._tool_names == ["run_code", "retrieval.search_internal_knowledge"]
        assert goal.description == "Create a PDF of GitHub repos"
        assert captured["include_routing"] is False

    async def test_quick_mode_threads_sandbox_has_network_into_planner(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="q")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying = await select_loop_and_goal(
            chat_mode="quick",
            query="q",
            llm=context.llm,
            context=context,
            tool_names=["run_code"],
            sandbox_has_network=True,
        )

        assert isinstance(loop, PlanExecuteLoop)
        assert loop._planner._sandbox_has_network is True

    async def test_quick_mode_threads_opik_flags_into_planner_transport(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="q")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying = await select_loop_and_goal(
            chat_mode="quick",
            query="q",
            llm=context.llm,
            context=context,
            opik_active=True,
            opik_project_name="proj",
        )

        assert isinstance(loop, PlanExecuteLoop)
        assert isinstance(loop._planner._model.transport, OpikTracingTransport)

    async def test_deep_mode_returns_orchestrator_loop_and_skips_routing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying = await select_loop_and_goal(
            chat_mode="deep", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, OrchestratorLoop)
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

        loop, goal, _clarifying = await select_loop_and_goal(
            chat_mode="auto",
            query="Create a PDF of GitHub repos",
            llm=context.llm,
            context=context,
            tool_names=["run_code"],
        )

        assert isinstance(loop, PlanExecuteLoop)
        assert loop._planner._tool_names == ["run_code"]
        assert goal.description == "Create a PDF of GitHub repos"
        assert captured["include_routing"] is True

    async def test_auto_mode_threads_sandbox_has_network_into_planner(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="q", route="quick")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying = await select_loop_and_goal(
            chat_mode="auto",
            query="q",
            llm=context.llm,
            context=context,
            tool_names=["run_code"],
            sandbox_has_network=True,
        )

        assert isinstance(loop, PlanExecuteLoop)
        assert loop._planner._sandbox_has_network is True

    async def test_unrecognized_mode_also_requests_routing_classification(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _fake_parse_intent(*_args: Any, **kwargs: Any) -> IntentRouteDecision:
            captured.update(kwargs)
            return IntentRouteDecision(reasoning="r", rewritten_query="hello", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying = await select_loop_and_goal(
            chat_mode="some-unknown-mode", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)
        assert captured["include_routing"] is True

    async def test_auto_mode_falls_back_to_react_when_route_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="hello", route=None)

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        loop, _goal, _clarifying = await select_loop_and_goal(
            chat_mode="auto", query="hello", llm=context.llm, context=context,
        )

        assert isinstance(loop, ReActLoop)

    async def test_clarifying_questions_empty_by_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _fake_parse_intent(*_args: Any, **_kwargs: Any) -> IntentRouteDecision:
            return IntentRouteDecision(reasoning="r", rewritten_query="hello", route="react")

        monkeypatch.setattr("app.agents.agent_loop.router.parse_intent_and_route", _fake_parse_intent)
        context = make_context(llm=FakeChatModel())

        _loop, _goal, clarifying = await select_loop_and_goal(
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

        loop, goal, clarifying = await select_loop_and_goal(
            chat_mode="auto", query="do the thing", llm=context.llm, context=context,
        )

        # Loop/Goal are still built normally -- callers gate on `clarifying`.
        assert isinstance(loop, ReActLoop)
        assert goal.gaps == ["missing project name"]
        assert clarifying == [question]
