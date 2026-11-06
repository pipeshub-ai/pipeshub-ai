"""The coverage loop's first step: plan the question's parts beside the
prefetch, then search in code for the parts the prefetched context does not
cover, before the model's first turn."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from app.agents.agent_loop.coverage.evidence import collect_evidence
from app.agents.agent_loop.coverage.facets import (
    FacetPlan,
    plan_facets,
    uncovered_facets,
)
from app.agents.agent_loop.coverage.llm import coverage_llm
from app.agents.agent_loop.coverage.state import coverage_state
from app.agents.agent_loop.coverage.targeted import (
    delimit_results,
    render_results,
    run_targeted_searches,
)
from app.agents.agent_loop.protocol.run_usage import llm_usage_reporter
from app.core.org_context import spawn_in_request
from app.utils.coverage_switches import facet_search_budget

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

COVERAGE_USAGE_SOURCE = "coverage_loop"
PLAN_TIMEOUT_S = 20.0
# The plan runs beside the prefetch; once the prefetch is back, it gets this
# long more before the turn starts without it.
_PLAN_GRACE_S = 5.0
# These searches run before the first turn, like the plan.
FACET_SEARCH_TIMEOUT_S = 20.0

FACET_CONTEXT_INTRO = (
    "More internal knowledge base context, from searches for parts of the question the context above "
    "does not cover (cite using the Citation IDs shown):"
)


def start_facet_plan(context: "AgentContext", question: str) -> "asyncio.Future[FacetPlan | None]":
    """Start planning the question's parts as a task of this request, beside the prefetch."""
    async def _plan() -> FacetPlan | None:
        llm = await coverage_llm(context.config_service, context.org_id, fallback=context.llm)
        return await plan_facets(
            llm, question, timeout=PLAN_TIMEOUT_S,
            on_usage=llm_usage_reporter(context.tool_state, COVERAGE_USAGE_SOURCE),
        )

    return cast("asyncio.Task[FacetPlan | None]", spawn_in_request(_plan(), name="coverage_facet_plan"))


async def finish_facet_plan(
    context: "AgentContext", task: "asyncio.Future[FacetPlan | None] | None",
) -> FacetPlan | None:
    """The plan, stored for the gate; None (and the task cancelled) when it is
    not ready in time or failed."""
    state = coverage_state(context.tool_state)
    if task is None:
        return state.plan
    try:
        plan = await asyncio.wait_for(asyncio.shield(task), timeout=_PLAN_GRACE_S)
    except Exception as exc:
        task.cancel()
        logger.info("coverage_loop: facet plan not ready, going on without it: %r", exc)
        plan = None
    state.plan, state.planned = plan, True
    state.bump("plan_ok" if plan else "plan_missing")
    if plan is not None:
        logger.info(
            "coverage_loop: plan conversation=%s facets=%s",
            context.conversation_id, [facet.kind for facet in plan.facets],
        )
    return plan


async def search_uncovered_facets(context: "AgentContext", plan: FacetPlan | None) -> str | None:
    """Context for the facets the prefetch does not cover, or None."""
    budget = facet_search_budget()
    if plan is None or budget <= 0:
        return None
    state = coverage_state(context.tool_state)
    missing = uncovered_facets(plan, collect_evidence(context.tool_state).texts())
    queries = [facet.query for facet in missing if facet.query and not state.was_searched(facet.query)][:budget]
    logger.info(
        "coverage_loop: prefetch coverage conversation=%s facets=%d uncovered=%d searches=%d",
        context.conversation_id, len(plan.facets), len(missing), len(queries),
    )
    if not queries:
        return None
    for query in queries:
        state.note_searched(query)
    state.bump("facet_searches", len(queries))
    results = await run_targeted_searches(context, queries, purpose="facet", timeout=FACET_SEARCH_TIMEOUT_S)
    if not any(result.text for result in results):
        return None
    return f"{FACET_CONTEXT_INTRO}\n{delimit_results(render_results([r for r in results if r.text]))}"


__all__ = [
    "COVERAGE_USAGE_SOURCE",
    "FACET_CONTEXT_INTRO",
    "FACET_SEARCH_TIMEOUT_S",
    "finish_facet_plan",
    "search_uncovered_facets",
    "start_facet_plan",
]
