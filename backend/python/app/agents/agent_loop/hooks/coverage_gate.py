"""POST_MODEL coverage gate: a text answer is final only once every part of the
question has supporting evidence, or the loop's budget is spent.

For a draft with gaps (a part the evidence read does not support, a named
reference not read, a near miss the draft concedes, a conflicting part resting
on the wrong record) it runs targeted searches in code and hands the results
back with a request to revise, at most `gate_round_budget()` times. A draft the
deterministic checks find covered passes without any model call.

With `ANSWER_OPENING_CHECK`, a cited draft that opens with a limitation or a
status ahead of its substance, or disputes wording the question uses, is also
sent back, once, to lead with the answer: in the same request as any gaps, or
alone without a search when there are none. With
`ANSWER_OPENING_CHECK_DISPUTE`, a draft the patterns pass is also read by
the dispute checker, beside the gate's own check.

Registered before `completion_gate` and `ask_in_text_recovery`, so it acts
after them and stays out of the way when one of them sends the reply back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.agent_loop.coverage.dispute import disputed_quotes
from app.agents.agent_loop.coverage.evidence import Evidence, collect_evidence
from app.agents.agent_loop.coverage.gate import (
    Gap,
    GateDecision,
    adjudicate_found,
    assess_draft,
)
from app.agents.agent_loop.coverage.llm import coverage_llm
from app.agents.agent_loop.coverage.opening import (
    FramingHit,
    framing_exempt,
    framing_hits,
)
from app.agents.agent_loop.coverage.prefetch import COVERAGE_USAGE_SOURCE
from app.agents.agent_loop.coverage.state import CoverageState, coverage_state
from app.agents.agent_loop.coverage.targeted import (
    render_results,
    run_targeted_searches,
)
from app.agents.agent_loop.protocol.run_usage import llm_usage_reporter
from app.modules.agents.context.tool_surface import ToolSurfaces
from app.utils.chat_helpers import get_record_id_shortener_if_enabled
from app.utils.citations import cited_blocks, cited_record_ids
from app.utils.coverage_switches import (
    answer_dispute_checker_enabled,
    answer_opening_check_enabled,
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

__all__ = ["RESTATE_THE_ANSWER", "coverage_gate", "render_gap_request", "render_opening_request"]

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
    "facet": "Your draft may lack: {label}",
    "reference": "Named but not read: {label}",
    "admission": 'Your draft says: "{label}"',
}


_FRAMING_FOUND = {
    "opening": 'your draft opens with "{quote}"',
    "dispute": 'your draft says "{quote}"',
    "checker": 'it disputes a detail the question states or the source plainly supports: "{quote}"',
}
_MAX_QUOTE_CHARS = 240
_FRAMING_ASK = (
    "Lead with the answer the records you read support. Keep a caveat only if it changes the answer, in one "
    "sentence after it. Do not dispute wording the question uses for a value the record states."
)
_KEEP_CITED = (
    "Keep every fact, value and citation link of the draft that answers the question, with the citation "
    "links exactly as written."
)


def _framing_found(hits: list[FramingHit]) -> list[str]:
    return list(dict.fromkeys(
        _FRAMING_FOUND[hit.kind].format(quote=" ".join(hit.quote.split())[:_MAX_QUOTE_CHARS]) for hit in hits
    ))


def render_opening_request(hits: list[FramingHit]) -> str:
    found = "\n".join(f"- {line}" for line in _framing_found(hits))
    return (
        f"Before this answer is sent, revise it once. A review of the draft found:\n{found}\n\n"
        f"{_FRAMING_ASK} Do not search again. {_KEEP_CITED} {RESTATE_THE_ANSWER}"
    )


def render_gap_request(
    decision: GateDecision, results_text: str, framing: list[FramingHit] | None = None,
) -> str:
    lines = ["Before you finalise, check your draft against these points:"]
    lines += [f"- {_GAP_LINES[gap.kind].format(label=gap.label)}" for gap in decision.gaps]
    lines += [f"- {ruling}" for ruling in decision.rulings]
    if framing:
        lines += [f"- {line[0].upper()}{line[1:]}" for line in _framing_found(framing)]
        lines.append(f"- {_FRAMING_ASK}")
    if decision.gaps:
        lines.append(
            "Searches for them were run for you; the results follow. Use a record here to fill a point "
            f"your draft lacks (read it in full with {FETCH_RECORD_TOOL_NAME} if its blocks are not enough). "
            "Keep every value your draft already cites: do not replace it with a different value from a "
            "record here unless that record explicitly revises it, meaning it is about the same scope "
            "(customer, product, version, team, period) and says it updates, corrects or supersedes the "
            "cited one, or is a later version of the same document. If records of the same scope give "
            "different values and neither revises the other, give both, each with its source, and say "
            "which is which. Where nothing here covers a point, keep your answer for it as it was, "
            "without adding new caveats."
        )
    lines.append(RESTATE_THE_ANSWER)
    text = "\n".join(lines)
    return f"{text}\n\n{results_text}" if results_text else text


@dataclass
class _Draft:
    text: str
    evidence: Evidence
    cited_record_ids: list[str] = field(default_factory=list[str])
    cited_blocks: dict[str, list[int]] = field(default_factory=dict[str, list[int]])


def _read_draft(context: "AgentContext", text: str) -> _Draft:
    ref_mapper = context.tool_state.get("citation_ref_mapper")
    ref_to_url = getattr(ref_mapper, "ref_to_url", None)
    return _Draft(
        text=text,
        evidence=collect_evidence(context.tool_state),
        cited_record_ids=cited_record_ids(text, ref_to_url),
        cited_blocks=cited_blocks(text, ref_to_url),
    )


def _agent_queries(ctx: "ModelResponseContext") -> list[str]:
    if ctx.scope is None:
        return []
    queries: list[str] = []
    for turn in ctx.scope.run.turns:
        for call in turn.tool_calls:
            query = call.arguments.get("query")
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
        framing = _framing_hits(context, text)
        try:
            draft = _read_draft(context, text)
            if _wants_dispute_check(context, text, framing):
                decision, framing = await asyncio.gather(_decide(context, ctx, draft), _dispute_hits(context, text))
            else:
                decision = await _decide(context, ctx, draft)
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
            [gap.kind for gap in decision.gaps], len(decision.rulings),
            int((time.monotonic() - started) * 1000),
        )
        if decision.covered:
            if framing:
                state.gate_rounds += 1
                _ask_for_opening(context, ctx, framing)
            return
        try:
            await _send_back(context, ctx, draft, decision, framing)
        except Exception:
            logger.warning("coverage_loop: gate send-back failed", exc_info=True)

    return _middleware


def _framing_hits(context: "AgentContext", text: str) -> list[FramingHit]:
    """Never raises: a failure here skips the framing check, not the gate."""
    if not answer_opening_check_enabled() or coverage_state(context.tool_state).opening_rewrite_sent:
        return []
    try:
        return framing_hits(context.query, text)
    except Exception:
        logger.warning("coverage_loop: framing check failed", exc_info=True)
        return []


def _wants_dispute_check(context: "AgentContext", text: str, framing: list[FramingHit]) -> bool:
    return (
        not framing
        and answer_dispute_checker_enabled()
        and not coverage_state(context.tool_state).opening_rewrite_sent
        and not framing_exempt(text)
    )


async def _dispute_hits(context: "AgentContext", text: str) -> list[FramingHit]:
    """Never raises: on a timeout or an error the draft has no dispute."""
    coverage_state(context.tool_state).bump("gate_dispute_checks")
    try:
        llm = await coverage_llm(context.config_service, context.org_id, fallback=context.llm)
        quotes = await disputed_quotes(
            context.query, text, llm, timeout=CHECK_TIMEOUT_S,
            on_usage=llm_usage_reporter(context.tool_state, COVERAGE_USAGE_SOURCE),
        )
    except Exception:
        logger.warning("coverage_loop: dispute check failed", exc_info=True)
        return []
    return [FramingHit("checker", quote) for quote in quotes]


def _note_opening(context: "AgentContext", framing: list[FramingHit]) -> None:
    state = coverage_state(context.tool_state)
    state.opening_rewrite_sent = True
    state.bump("gate_opening_rewrites")
    logger.info(
        "coverage_loop: answer opening conversation=%s hits=%r",
        context.conversation_id, [hit.kind for hit in framing],
    )


def _ask_for_opening(context: "AgentContext", ctx: "ModelResponseContext", framing: list[FramingHit]) -> None:
    """A rewrite-only round: no search, and the draft's citations stand."""
    _note_opening(context, framing)
    coverage_state(context.tool_state).bump("gate_rounds_sent")
    ctx.recovery_message = UserMessage(content=render_opening_request(framing), injected=True)


def _record_label(context: "AgentContext") -> "Callable[[str | None, str], str]":
    shortener = get_record_id_shortener_if_enabled(context.tool_state)

    def _name(record_id: str | None, name: str) -> str:
        shown_id = shortener.shorten_if_known(record_id) if shortener is not None and record_id else record_id
        return f"{name} (record {shown_id})" if name and shown_id else name or shown_id or "a record"

    return _name


async def _decide(context: "AgentContext", ctx: "ModelResponseContext", draft: _Draft) -> GateDecision:
    state = coverage_state(context.tool_state)
    llm = await coverage_llm(context.config_service, context.org_id, fallback=context.llm)
    return await assess_draft(
        question=context.query,
        draft=draft.text,
        plan=state.plan,
        evidence=draft.evidence,
        cited_record_ids=draft.cited_record_ids,
        cited_blocks=draft.cited_blocks,
        searched=[*state.searched, *_agent_queries(ctx)],
        llm=llm,
        timeout=CHECK_TIMEOUT_S,
        max_gaps=gate_search_budget(),
        adjudication=coverage_adjudication_enabled(),
        name_of=_record_label(context),
        on_usage=llm_usage_reporter(context.tool_state, COVERAGE_USAGE_SOURCE),
    )


async def _adjudicate_found(context: "AgentContext", draft: _Draft, evidence: Evidence, new: set[str]) -> list[str]:
    llm = await coverage_llm(context.config_service, context.org_id, fallback=context.llm)
    return await adjudicate_found(
        question=context.query,
        draft=draft.text,
        plan=coverage_state(context.tool_state).plan,
        evidence=evidence,
        new_virtual_record_ids=new,
        cited_record_ids=draft.cited_record_ids,
        cited_blocks=draft.cited_blocks,
        llm=llm,
        timeout=CHECK_TIMEOUT_S,
        name_of=_record_label(context),
        on_usage=llm_usage_reporter(context.tool_state, COVERAGE_USAGE_SOURCE),
    )


async def _send_back(
    context: "AgentContext",
    ctx: "ModelResponseContext",
    draft: _Draft,
    decision: GateDecision,
    framing: list[FramingHit] | None = None,
) -> None:
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
    evidence = collect_evidence(context.tool_state) if found else draft.evidence
    shown = draft.evidence.virtual_record_ids()
    new = {record.virtual_record_id for record in evidence.read_records()} - shown
    state.bump("gate_new_records", len(new))
    rulings = list(decision.rulings)
    if new and coverage_adjudication_enabled():
        for ruling in await _adjudicate_found(context, draft, evidence, new):
            if ruling not in rulings:
                rulings.append(ruling)
                state.bump("gate_found_rulings")
    logger.info(
        "coverage_loop: gate searches conversation=%s results=%d new_records=%d rulings=%d",
        context.conversation_id, len(found), len(new), len(rulings),
    )
    if not new and not rulings:
        # Nothing the draft has not seen: another turn would restate it, or
        # trade a cited value for a lookalike's.
        state.bump("gate_nothing_new" if found else "gate_nothing_found")
        if framing:
            _ask_for_opening(context, ctx, framing)
        return
    state.bump("gate_rounds_sent")
    state.bump("gate_rulings", len(rulings))
    request = GateDecision(gaps=decision.gaps if new else [], rulings=rulings, checked=decision.checked)
    if framing:
        _note_opening(context, framing)
    ctx.recovery_message = UserMessage(
        content=render_gap_request(request, render_results(found) if new else "", framing), injected=True,
    )


def _count_gap_kinds(state: CoverageState, gaps: list[Gap]) -> None:
    for gap in gaps:
        state.bump(f"gap_{gap.kind}")
