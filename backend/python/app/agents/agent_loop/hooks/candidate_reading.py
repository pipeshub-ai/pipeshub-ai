"""POST_MODEL: before a draft is final, ask the agent to read the records that ranked at the top of its own
searches but that it never opened (``ANSWER_CANDIDATE_READING``).

The candidates come from the searches' own order (``app.agents.agent_loop.candidate_reading``), not from a
model's guess, and the request keeps the draft unless a candidate states what the question asks for its specific
case more directly. One round per request.

Registered after ``record_completeness_pass`` (inside it), so the completeness pass checks the answer written after
the reading, and before ``coverage_gate``, so it acts on a draft that gate let stand.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.agent_loop.answer_rules import answer_scope_guard_enabled
from app.agents.agent_loop.candidate_reading import TopCandidate, unread_top_candidates
from app.agents.agent_loop.coverage.evidence import collect_evidence, record_ref
from app.agents.agent_loop.coverage.opening import framing_exempt
from app.agents.agent_loop.hooks.coverage_gate import RESTATE_THE_ANSWER
from app.modules.agents.context.tool_surface import ToolSurfaces
from app.utils.chat_helpers import get_record_id_shortener_if_enabled
from app.utils.citations import cited_record_ids

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ModelResponseContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

__all__ = ["CANDIDATE_READING_SENT", "read_top_candidates", "render_reading_request"]

logger = logging.getLogger(__name__)

CANDIDATE_READING_SENT = "candidate_reading_sent"
# The fetch takes one turn, the restated answer another, and the completeness pass may take a third.
_MIN_TURNS_LEFT = 3


# ANSWER_SCOPE_GUARD: a candidate that only shared the question's words took the lead from the record the question
# describes, and another tier's runbook merged its steps into the answer.
_SCOPE_GUARD = (
    "Answer from a record here only if it states the question's own details about which case it means (who, "
    "which customer, scope, window or version) at least as fully as the record your draft relies on. A record "
    "about another scope, tier, version line or process adds at most one attributed sentence; do not merge its "
    "steps, thresholds, approvals or schedules into the answer. "
)


def render_reading_request(labels: list[str]) -> str:
    found = "\n".join(f"- {label}" for label in labels)
    which = "this record" if len(labels) == 1 else "these records"
    guard = _SCOPE_GUARD if answer_scope_guard_enabled() else ""
    return (
        "Before this answer is final: your searches ranked "
        f"{which} at the top of their results, and you have not read {'it' if len(labels) == 1 else 'them'}:\n"
        f"{found}\n\n"
        f"Read {which} with {FETCH_RECORD_TOOL_NAME}, in one call. If one of them states what the question asks, "
        "for the specific case it names (its names, identifiers, dates and scope), more directly than the records "
        f"your draft relies on, answer from it and cite it. Otherwise keep your draft's answer. {guard}Keep every "
        "fact, value and citation link of the draft that still answers the question, with the links exactly as "
        f"written. {RESTATE_THE_ANSWER}"
    )


def _turns_left(ctx: "ModelResponseContext") -> int:
    if ctx.scope is None:
        return 0
    return ctx.scope.run.spec.max_turns - (ctx.turn_index + 1)


def _eligible(ctx: "ModelResponseContext") -> bool:
    scope = ctx.scope
    return (
        scope is not None
        and scope.run.identity.spawn_depth == 0
        and _turns_left(ctx) >= _MIN_TURNS_LEFT
        and ToolSurfaces.resolve(scope.run.spec.tool_names, {}).can_fetch_full_record
    )


def _label(context: "AgentContext", candidate: TopCandidate) -> str:
    shortener = get_record_id_shortener_if_enabled(context.tool_state)
    shown_id = shortener.shorten_if_known(candidate.record_id) if shortener is not None else candidate.record_id
    return record_ref(shown_id, candidate.name)


def read_top_candidates(context: "AgentContext") -> "Middleware[ModelResponseContext]":
    """POST_MODEL middleware factory over the request's ``AgentContext``."""

    async def _middleware(ctx: "ModelResponseContext", next_fn: "Next") -> None:
        await next_fn()
        if ctx.tool_calls or getattr(ctx.response, "truncated", False) or ctx.recovery_message is not None:
            return
        if context.tool_state.get(CANDIDATE_READING_SENT):
            return
        text = ctx.response.text if isinstance(ctx.response, AssistantMessage) else ""
        if not text.strip() or not _eligible(ctx) or framing_exempt(text):
            return
        try:
            ref_mapper = context.tool_state.get("citation_ref_mapper")
            cited = cited_record_ids(text, getattr(ref_mapper, "ref_to_url", None))
            if not cited:
                return
            evidence = collect_evidence(context.tool_state)
            opened = {record.record_id for record in evidence.records if record.record_id and record.fetched}
            opened |= set(context.tool_state.get("full_records_fetched") or ())
            candidates = unread_top_candidates(context.tool_state, cited, opened)
        except Exception:
            logger.warning("candidate_reading: check failed; the draft stands", exc_info=True)
            return
        if not candidates:
            return
        context.tool_state[CANDIDATE_READING_SENT] = True
        logger.info(
            "candidate_reading: sent back conversation=%s ranks=%r", context.conversation_id,
            [candidate.rank for candidate in candidates],
        )
        ctx.recovery_message = UserMessage(
            content=render_reading_request([_label(context, candidate) for candidate in candidates]), injected=True,
        )

    return _middleware
