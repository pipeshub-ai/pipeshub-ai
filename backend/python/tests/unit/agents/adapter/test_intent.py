"""`parse_intent_and_route` (`app/agents/agent_loop/intent.py`) — the merged
intent-understanding + tier-routing structured call every chat mode goes
through before `Agent.run()` (see `router.py::select_loop_and_goal`).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.agent_loop.intent import IntentRouteDecision, parse_intent_and_route

_LOGGER = logging.getLogger("test.agents.agent_loop.intent")


class _FakeStructuredModel:
    """Minimal double for `llm.with_structured_output(Schema).ainvoke(...)`
    — real LangChain returns the parsed pydantic instance directly (not an
    `AIMessage`), which is what this fake mimics; pass an `Exception`
    instance as `result` to simulate a failed structured call."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.last_messages: list[Any] = []

    def with_structured_output(self, schema: Any) -> "_FakeStructuredModel":  # noqa: ANN401
        return self

    async def ainvoke(self, messages: list, config: dict | None = None) -> Any:  # noqa: ANN401
        self.last_messages = messages
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _query_info(query: str = "hello", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query": query,
        "knowledge": [],
        "connector_configs": {},
        "filters": {},
        "toolsets": [],
        "previous_conversations": [],
        "attachments": [],
    }
    base.update(overrides)
    return base


class TestParseIntentAndRoute:
    async def test_empty_query_short_circuits_without_llm_call(self) -> None:
        llm = _FakeStructuredModel(RuntimeError("must not be called"))

        decision = await parse_intent_and_route(
            _query_info(query=""), _LOGGER, llm, include_routing=True,
        )

        assert decision.rewritten_query == ""
        assert decision.route == "react"

    async def test_empty_query_without_routing_leaves_route_none(self) -> None:
        llm = _FakeStructuredModel(RuntimeError("must not be called"))

        decision = await parse_intent_and_route(
            _query_info(query=""), _LOGGER, llm, include_routing=False,
        )

        assert decision.route is None

    async def test_happy_path_returns_rewritten_query_and_route(self) -> None:
        expected = IntentRouteDecision(
            reasoning="user wants X",
            rewritten_query="Clear self-contained restatement of X",
            requirements=["req1"],
            success_criteria=["done when x happens"],
            route="quick",
        )
        llm = _FakeStructuredModel(expected)

        decision = await parse_intent_and_route(
            _query_info(query="do x"), _LOGGER, llm, include_routing=True,
        )

        assert decision.rewritten_query == "Clear self-contained restatement of X"
        assert decision.route == "quick"
        assert decision.requirements == ["req1"]
        assert decision.success_criteria == ["done when x happens"]

    async def test_include_routing_false_leaves_llm_returned_route_as_is(self) -> None:
        expected = IntentRouteDecision(reasoning="r", rewritten_query="q", route=None)
        llm = _FakeStructuredModel(expected)

        decision = await parse_intent_and_route(
            _query_info(), _LOGGER, llm, include_routing=False,
        )

        assert decision.route is None
        system_message = llm.last_messages[0]
        assert "Leave `route` null" in system_message.content

    async def test_include_routing_true_embeds_tier_rubric_in_system_prompt(self) -> None:
        expected = IntentRouteDecision(reasoning="r", rewritten_query="q", route="react")
        llm = _FakeStructuredModel(expected)

        await parse_intent_and_route(_query_info(), _LOGGER, llm, include_routing=True)

        system_message = llm.last_messages[0]
        assert "## quick" in system_message.content
        assert "## react" in system_message.content
        assert "## deep" in system_message.content

    async def test_llm_failure_falls_back_to_raw_query_and_react(self) -> None:
        llm = _FakeStructuredModel(RuntimeError("boom"))

        decision = await parse_intent_and_route(
            _query_info(query="raw text"), _LOGGER, llm, include_routing=True,
        )

        assert decision.rewritten_query == "raw text"
        assert decision.route == "react"

    async def test_llm_failure_without_routing_leaves_route_none(self) -> None:
        llm = _FakeStructuredModel(RuntimeError("boom"))

        decision = await parse_intent_and_route(
            _query_info(query="raw text"), _LOGGER, llm, include_routing=False,
        )

        assert decision.rewritten_query == "raw text"
        assert decision.route is None

    async def test_blank_rewritten_query_falls_back_to_raw_query(self) -> None:
        expected = IntentRouteDecision(reasoning="r", rewritten_query="   ", route="react")
        llm = _FakeStructuredModel(expected)

        decision = await parse_intent_and_route(
            _query_info(query="raw text"), _LOGGER, llm, include_routing=True,
        )

        assert decision.rewritten_query == "raw text"

    async def test_gaps_and_clarifying_questions_default_empty(self) -> None:
        expected = IntentRouteDecision(reasoning="r", rewritten_query="q", route="react")
        llm = _FakeStructuredModel(expected)

        decision = await parse_intent_and_route(
            _query_info(query="hello"), _LOGGER, llm, include_routing=True,
        )

        assert decision.gaps == []
        assert decision.clarifying_questions == []

    async def test_clarifying_questions_threaded_through_on_ambiguous_request(self) -> None:
        from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput

        question = AskUserQuestionItemInput(
            question="Which channel should I post in?",
            options=[{"label": "general"}, {"label": "engineering"}, {"label": "random"}],
            multiSelect=False,
        )
        expected = IntentRouteDecision(
            reasoning="ambiguous — missing channel",
            rewritten_query="Post the update in a channel",
            gaps=["missing target channel"],
            clarifying_questions=[question],
            route="react",
        )
        llm = _FakeStructuredModel(expected)

        decision = await parse_intent_and_route(
            _query_info(query="post the update"), _LOGGER, llm, include_routing=True,
        )

        assert decision.gaps == ["missing target channel"]
        assert decision.clarifying_questions == [question]

    async def test_empty_query_leaves_gaps_and_clarifying_questions_empty(self) -> None:
        llm = _FakeStructuredModel(RuntimeError("must not be called"))

        decision = await parse_intent_and_route(
            _query_info(query=""), _LOGGER, llm, include_routing=True,
        )

        assert decision.gaps == []
        assert decision.clarifying_questions == []

    async def test_llm_failure_leaves_gaps_and_clarifying_questions_empty(self) -> None:
        llm = _FakeStructuredModel(RuntimeError("boom"))

        decision = await parse_intent_and_route(
            _query_info(query="raw text"), _LOGGER, llm, include_routing=True,
        )

        assert decision.gaps == []
        assert decision.clarifying_questions == []
