"""`ask_act_harness.py` (Ask User Tool Improvement Plan, Testing Plan item
6) — the harness's OWN plumbing, entirely offline:

- `decision_for_query()` driven through a `ScriptedTransport` (no real
  model, no network) to prove it actually reaches `parse_intent_and_route`
  and returns its `IntentRouteDecision`.
- `run_ask_act_eval()`'s aggregation, driven by a fake
  `decision_for_query_fn` — proves the harness scores/aggregates correctly
  without needing ANY of the real queries to hit a real model. A real eval
  run (live model, real cost/latency) is a separate, deliberately manual
  invocation — see the module docstring.
"""

from __future__ import annotations

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.evals.ask_act_harness import decision_for_query, run_ask_act_eval
from app.agents.agent_loop.evals.ask_act_queries import AskActEvalQuery
from app.agents.agent_loop.intent import IntentRouteDecision
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _model():  # noqa: ANN202
    from app.agent_loop_lib.agent.spec import ModelSpec

    return ModelSpec(provider="langchain", model="scripted-model")


def _transport_registry(transport: ScriptedTransport) -> TransportRegistry:
    registry = TransportRegistry()
    registry.register("langchain", lambda: transport)
    return registry


def _clarify_output() -> str:
    return (
        '```clarify\n'
        '{"user_intent": "unclear which ticket", "severity": "moderate", '
        '"questions": [{"question": "Which ticket?", "multiSelect": false, '
        '"options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]}]}\n'
        '```'
    )


class TestDecisionForQuery:
    async def test_reaches_parse_intent_and_route_and_returns_its_decision(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={"output": "## Request\nFind the Q3 report."}),
        )

        decision = await decision_for_query(
            "Find the Q3 report.", transport_registry=_transport_registry(transport), model=_model(),
        )

        assert isinstance(decision, IntentRouteDecision)
        assert decision.clarifying_questions == []
        assert decision.route is None  # include_routing=False for this eval

    async def test_clarify_block_surfaces_as_clarifying_questions(self) -> None:
        transport = ScriptedTransport().add_tool_call(
            ToolCall(id="tc1", name="task_complete", arguments={"output": _clarify_output()}),
        )

        decision = await decision_for_query(
            "Update the ticket.", transport_registry=_transport_registry(transport), model=_model(),
        )

        assert len(decision.clarifying_questions) == 1
        assert decision.clarification_severity == "moderate"


class TestRunAskActEval:
    def _query(self, query_id: str, expect_ask: bool) -> AskActEvalQuery:
        return AskActEvalQuery(id=query_id, query="q", expect_ask=expect_ask)

    async def test_aggregates_pass_and_fail_across_queries(self) -> None:
        queries = (self._query("should_ask", expect_ask=True), self._query("should_act", expect_ask=False))
        decisions = {
            "should_ask": IntentRouteDecision(rewritten_query="unclear what they want", clarification_severity="fatal"),
            "should_act": IntentRouteDecision(rewritten_query="x"),
        }
        # The "should_ask" fixture above has no clarifying_questions, so the
        # harness should score it as a FAILED ask (model didn't actually ask).
        async def fake_decision_for_query(query: AskActEvalQuery) -> IntentRouteDecision:
            return decisions[query.id]

        report = await run_ask_act_eval(queries, decision_for_query_fn=fake_decision_for_query)

        by_id = {s.query_id: s for s in report.scores}
        assert by_id["should_ask"].passed is False  # expected ask, got none
        assert by_id["should_act"].passed is True

    async def test_render_text_includes_every_query(self) -> None:
        queries = (self._query("q1", expect_ask=False),)

        async def fake_decision_for_query(query: AskActEvalQuery) -> IntentRouteDecision:
            return IntentRouteDecision(rewritten_query="x")

        report = await run_ask_act_eval(queries, decision_for_query_fn=fake_decision_for_query)
        text = report.render_text()

        assert "q1" in text
        assert "PASS" in text

    async def test_empty_query_set_has_zero_pass_rate_not_a_crash(self) -> None:
        async def fake_decision_for_query(query: AskActEvalQuery) -> IntentRouteDecision:
            return IntentRouteDecision(rewritten_query="x")

        report = await run_ask_act_eval((), decision_for_query_fn=fake_decision_for_query)

        assert report.pass_rate == 0.0
        assert report.scores == []
