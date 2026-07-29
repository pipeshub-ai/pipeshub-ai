"""Ask-vs-act decision regression harness (Ask User Tool Improvement Plan,
Testing Plan item 6). Answers one question, repeatedly, across
`ASK_ACT_EVAL_QUERIES`: for this query, does `intent.parse_intent_and_route()`
(under the SAME `_CLARIFY_INSTRUCTIONS` every real request uses) choose to
ask a clarifying question, or answer/act directly — and does that choice
match the query's `expect_ask`?

Much smaller than `decomposition_harness.py` because `parse_intent_and_route`
is a single free function, not something requiring an `Agent`/`ToolRegistry`/
`LoopStrategy` to drive — `decision_for_query()` below just calls it directly
against a real model.

Usage (needs a real model — this is NOT part of the default fast test suite;
see `tests/unit/agents/adapter/test_ask_act_harness.py` for the offline-safe
subset that IS):

    from app.agent_loop_lib.agent.spec import ModelSpec
    from app.agent_loop_lib.transport.registry import TransportRegistry

    transport_registry = TransportRegistry()
    transport_registry.register("langchain", ...)  # real transport factory
    report = await run_ask_act_eval(
        decision_for_query_fn=lambda q: decision_for_query(
            q.query, transport_registry=transport_registry,
            model=ModelSpec(provider="langchain", model="gpt-4o-mini"),
        ),
    )
    print(report.render_text())
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agents.agent_loop.evals.ask_act_queries import ASK_ACT_EVAL_QUERIES, AskActEvalQuery
from app.agents.agent_loop.evals.ask_act_scorer import AskActEvalReport, score_decision
from app.agents.agent_loop.intent import IntentRouteDecision, parse_intent_and_route

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.agent.spec import ModelSpec
    from app.agent_loop_lib.transport.registry import TransportRegistry

__all__ = ["decision_for_query", "run_ask_act_eval"]

logger = logging.getLogger(__name__)


def _query_info(query: str) -> dict:
    """The minimal `query_info` shape `parse_intent_and_route()` reads —
    same fields `router.py::_context_to_query_info` derives from a real
    `AgentContext`, populated empty here since this eval isolates the
    ask-vs-act decision from retrieval/connector context."""
    return {
        "query": query,
        "knowledge": [],
        "connector_configs": {},
        "filters": {},
        "toolsets": [],
        "previous_conversations": [],
        "attachments": [],
    }


async def decision_for_query(
    query: str,
    *,
    transport_registry: "TransportRegistry",
    model: "ModelSpec",
) -> IntentRouteDecision:
    """Runs the intent call for one query against a REAL model.
    `include_routing=False` — this eval is scoped to the ask-vs-act
    decision, not the quick/react/deep tier classification, which is an
    orthogonal concern the ```clarify escape hatch takes regardless of
    `include_routing` (see `_build_intent_prompt`)."""
    return await parse_intent_and_route(
        _query_info(query),
        logger,
        llm=None,  # unused: `transport_registry` already has "langchain" registered
        include_routing=False,
        transport_registry=transport_registry,
        model_name=model.model,
    )


async def run_ask_act_eval(
    queries: tuple[AskActEvalQuery, ...] = ASK_ACT_EVAL_QUERIES,
    *,
    decision_for_query_fn: "Callable[[AskActEvalQuery], Awaitable[IntentRouteDecision]]",
) -> AskActEvalReport:
    """Runs `decision_for_query_fn` over every query and scores the result.

    Takes the decision-producing step as an injected callable (same shape
    as `decomposition_harness.run_decomposition_eval`) so the harness's OWN
    aggregation logic is testable with a fake, zero-cost callable — see
    `test_ask_act_harness.py`."""
    scores = [score_decision(await decision_for_query_fn(query), query) for query in queries]
    return AskActEvalReport(scores=scores)
