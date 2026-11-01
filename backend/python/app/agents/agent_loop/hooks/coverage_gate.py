"""POST_MODEL coverage gate: a text answer is final only once every part of the
question has supporting evidence, or the loop's budget is spent.

For a draft with gaps (a part the evidence read does not support, a named
reference not read, a near miss the draft concedes, a conflicting part resting
on the wrong record) it runs targeted searches in code and hands the results
back with a request to revise, at most `gate_round_budget()` times. A draft the
deterministic checks find covered passes without any model call.

Registered before `completion_gate` and `ask_in_text_recovery`, so it acts
after them and stays out of the way when one of them sends the reply back.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.agent_loop.coverage.evidence import collect_evidence
from app.agents.agent_loop.coverage.gate import Gap, GateDecision, assess_draft
from app.agents.agent_loop.coverage.llm import coverage_llm
from app.agents.agent_loop.coverage.prefetch import COVERAGE_USAGE_SOURCE
from app.agents.agent_loop.coverage.state import CoverageState, coverage_state
from app.agents.agent_loop.coverage.targeted import (
    render_results,
    run_targeted_searches,
)
from app.agents.agent_loop.protocol.run_usage import llm_usage_reporter
from app.modules.agents.context.tool_surface import ToolSurfaces
from app.utils.chat_helpers import get_record_id_shortener_if_enabled
from app.utils.citations import cited_record_ids
from app.utils.coverage_switches import (
    coverage_adjudication_enabled,
    gate_round_budget,
    gate_search_budget,
    gate_search_timeout_seconds,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["RESTATE_THE_ANSWER", "coverage_gate", "render_gap_request"]

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 20.0
# A round costs the searches plus a revision turn; with fewer turns left the
# draft stands.
_MIN_TURNS_LEFT = 3
_SEARCH_TOOL_SUFFIX = "search"

# The reply to a send-back replaces the draft, so it must be the whole answer.
RESTATE_THE_ANSWER = (
    "Then please give your complete answer again in full, since your next reply "
    "replaces the draft above as the answer; if what you read changes nothing, repeat "
    "the same answer rather than a note about it."
)

_GAP_LINES = {
    "facet": "Not supported by what you read: {label}",
    "reference": "Named but not read: {label}",
    "admission": 'Your draft says: "{label}"',
}


def render_gap_request(decision: GateDecision, results_text: str) -> str:
    lines = ["Before you finalise, check your draft against these points:"]
    lines += [f"- {_GAP_LINES[gap.kind].format(label=gap.label)}" for gap in decision.gaps]
    lines += [f"- {ruling}" for ruling in decision.rulings]
    if decision.gaps:
        lines.append(
            "Searches for them were run for you; the results follow. Where a record here covers a "
            f"point, use it (read it in full with {FETCH_RECORD_TOOL_NAME} if its blocks are not enough), and keep "
            "what your draft already supported. Where nothing here covers a point, keep your answer "
            "for it as it was, without adding new caveats."
        )
    lines.append(RESTATE_THE_ANSWER)
    text = "\n".join(lines)
    return f"{text}\n\n{results_text}" if results_text else text


def _agent_queries(ctx: "ModelResponseContext") -> list[str]:
    if ctx.scope is None:
        return []
    queries = []
    for turn in ctx.scope.run.turns:
        for call in turn.tool_calls:
            query = call.arguments.get("query") if isinstance(call.arguments, dict) else None
            if call.name.endswith(_SEARCH_TOOL_SUFFIX) and isinstance(query, str) and query.strip():
                queries.append(query.strip())
    return queries


def _can_look_further(ctx: "ModelResponseContext") -> bool:
    if ctx.scope is None:
        return False
    spec = ctx.scope.run.spec
    if spec.max_turns - (ctx.turn_index + 1) < _MIN_TURNS_LEFT:
        return False
    return ToolSurfaces.resolve(spec.tool_names, {}).retrieval is not None


def _is_top_level(ctx: "ModelResponseContext") -> bool:
    return ctx.scope is not None and ctx.scope.run.identity.spawn_depth == 0


def coverage_gate(context: "AgentContext") -> "Middleware[ModelResponseContext]":
    """POST_MODEL middleware factory over the request's `AgentContext`."""

    async def _middleware(ctx: "ModelResponseContext", next_fn: "Next") -> None:
        await next_fn()
        if ctx.tool_calls or getattr(ctx.response, "truncated", False) or ctx.recovery_message is not None:
            return
        text = ctx.response.text if isinstance(ctx.response, AssistantMessage) else ""
        if not text.strip() or not _is_top_level(ctx):
            return
        state = coverage_state(context.tool_state)
        if state.gate_rounds >= gate_round_budget() or not _can_look_further(ctx):
            return
        started = time.monotonic()
        try:
            decision = await _decide(context, ctx, text)
        except Exception:
            # The draft stands; this gate only ever adds a look.
            logger.warning("coverage_loop: gate check failed", exc_info=True)
            return
        state.bump("gate_checks")
        if decision.checked:
            state.bump("gate_checker_calls")
        logger.info(
            "coverage_loop: gate conversation=%s round=%d signals=%s gaps=%s rulings=%d ms=%d",
            context.conversation_id, state.gate_rounds, decision.signals,
            [(gap.kind, gap.query) for gap in decision.gaps], len(decision.rulings),
            int((time.monotonic() - started) * 1000),
        )
        if decision.covered:
            return
        await _send_back(context, ctx, decision)

    return _middleware


def _record_label(context: "AgentContext") -> "Callable[[str | None, str], str]":
    shortener = get_record_id_shortener_if_enabled(context.tool_state)

    def _name(record_id: str | None, name: str) -> str:
        shown_id = shortener.shorten_if_known(record_id) if shortener is not None and record_id else record_id
        return f"{name} (record {shown_id})" if name and shown_id else name or shown_id or "a record"

    return _name


async def _decide(context: "AgentContext", ctx: "ModelResponseContext", text: str) -> GateDecision:
    state = coverage_state(context.tool_state)
    ref_mapper = context.tool_state.get("citation_ref_mapper")
    llm = await coverage_llm(context.config_service, context.org_id, fallback=context.llm)
    return await assess_draft(
        question=context.query,
        draft=text,
        plan=state.plan,
        evidence=collect_evidence(context.tool_state),
        cited_record_ids=cited_record_ids(text, getattr(ref_mapper, "ref_to_url", None)),
        searched=[*state.searched, *_agent_queries(ctx)],
        llm=llm,
        timeout=CHECK_TIMEOUT_S,
        max_gaps=gate_search_budget(),
        adjudication=coverage_adjudication_enabled(),
        name_of=_record_label(context),
        on_usage=llm_usage_reporter(context.tool_state, COVERAGE_USAGE_SOURCE),
    )


async def _send_back(context: "AgentContext", ctx: "ModelResponseContext", decision: GateDecision) -> None:
    state = coverage_state(context.tool_state)
    queries = [gap.query for gap in decision.gaps]
    for query in queries:
        state.note_searched(query)
    results = await run_targeted_searches(
        context, queries, purpose="gate", timeout=gate_search_timeout_seconds(),
    ) if queries else []
    found = [result for result in results if result.text]
    state.gate_rounds += 1
    state.bump("gate_searches", len(queries))
    state.bump("gate_searches_with_results", len(found))
    _count_gap_kinds(state, decision.gaps)
    if not found and not decision.rulings:
        # Nothing new to read: another turn would restate the same draft.
        state.bump("gate_nothing_found")
        return
    state.bump("gate_rounds_sent")
    state.bump("gate_rulings", len(decision.rulings))
    ctx.recovery_message = UserMessage(
        content=render_gap_request(decision, render_results(found) if found else ""), injected=True,
    )


def _count_gap_kinds(state: CoverageState, gaps: list[Gap]) -> None:
    for gap in gaps:
        state.bump(f"gap_{gap.kind}")
