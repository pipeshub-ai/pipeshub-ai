"""`decomposition_harness.py` (p4-orchestrator-evals) — the harness's OWN
plumbing, entirely offline:

- `Phase1OnlyLoop` + `plan_for_query()` driven end-to-end through a
  `ScriptedTransport` (no real model, no network) to prove the loop
  actually retrieves the `Plan` `create_plan` stores.
- `run_decomposition_eval()`'s aggregation, driven by a fake
  `plan_for_query_fn` — proves the harness scores/aggregates correctly
  without needing ANY of the 20 real queries to hit a real model. A real
  eval run (live model, real cost/latency) is a separate, deliberately
  manual invocation — see the module docstring.
"""

from __future__ import annotations

from app.agent_loop_lib.agent.spec import ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.base import Plan, PlanStep
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.evals.decomposition_harness import (
    default_domain_block,
    plan_for_query,
    run_decomposition_eval,
)
from app.agents.agent_loop.evals.decomposition_queries import DecompositionEvalQuery
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_GOAL = Goal(description="x")


def _transport_registry(transport: ScriptedTransport) -> TransportRegistry:
    registry = TransportRegistry()
    registry.register("scripted", lambda: transport)
    return registry


class TestPlanForQuery:
    async def test_retrieves_the_structured_plan_create_plan_stored(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="c-plan", name="create_plan", arguments={
                "steps": [
                    {"id": "fetch", "description": "fetch the data", "domain": "internal_exploration_agent"},
                ],
                "confidence": "high",
            },
        ))
        transport.add_tool_call(ToolCall(
            id="c-critique", name="critique_plan", arguments={"plan": "1. fetch — fetch the data"},
        ))
        transport.add_text("critique-structured-response-placeholder")  # consumed by critique_plan's own complete_structured call

        plan = await plan_for_query(
            "fetch the data",
            transport_registry=_transport_registry(transport),
            model=ModelSpec(provider="scripted", model="scripted-model"),
        )

        assert plan is not None
        assert plan.steps is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].id == "fetch"

    async def test_returns_none_when_create_plan_never_called(self) -> None:
        transport = ScriptedTransport()
        transport.add_text("I don't think this needs a plan.")

        plan = await plan_for_query(
            "trivial question",
            transport_registry=_transport_registry(transport),
            model=ModelSpec(provider="scripted", model="scripted-model"),
            max_turns=2,
        )

        assert plan is None

    async def test_uses_the_default_domain_block_when_none_given(self) -> None:
        """Smoke check that `default_domain_block()` itself doesn't blow
        up and produces non-empty, catalog-derived content — the actual
        prompt-injection path is exercised implicitly by the test above
        (which passes no `domain_block` override)."""
        block = default_domain_block()
        assert "## Available Domains" in block
        assert len(block.splitlines()) > 1


class TestRunDecompositionEval:
    def _query(self, query_id: str) -> DecompositionEvalQuery:
        return DecompositionEvalQuery(
            id=query_id, query="q", min_steps=1, max_steps=1,
            expected_domains=frozenset(), requires_dependency=False,
        )

    async def test_aggregates_pass_and_fail_across_queries(self) -> None:
        queries = (self._query("good"), self._query("bad"))
        plans = {
            "good": Plan(goal=_GOAL, text="t", steps=[
                PlanStep(id="s1", description="d", domain="x"),
            ]),
            "bad": None,  # create_plan never called -> hard failure
        }

        async def fake_plan_for_query(query: DecompositionEvalQuery) -> Plan | None:
            return plans[query.id]

        report = await run_decomposition_eval(queries, plan_for_query_fn=fake_plan_for_query)

        assert report.pass_count == 1
        assert report.fail_count == 1
        assert report.pass_rate == 0.5
        by_id = {s.query_id: s for s in report.scores}
        assert by_id["good"].passed is True
        assert by_id["bad"].passed is False

    async def test_render_text_includes_every_query_and_its_issues(self) -> None:
        queries = (self._query("q1"),)

        async def fake_plan_for_query(query: DecompositionEvalQuery) -> Plan | None:
            return None

        report = await run_decomposition_eval(queries, plan_for_query_fn=fake_plan_for_query)
        text = report.render_text()

        assert "q1" in text
        assert "FAIL" in text
        assert "0/1" in text or "0.0" not in text  # aggregate line renders without crashing

    async def test_empty_query_set_has_zero_pass_rate_not_a_crash(self) -> None:
        async def fake_plan_for_query(query: DecompositionEvalQuery) -> Plan | None:
            return None

        report = await run_decomposition_eval((), plan_for_query_fn=fake_plan_for_query)

        assert report.pass_rate == 0.0
        assert report.scores == []
