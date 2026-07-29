"""POST_MODEL full-record gate and POST_TOOL_USE fetch tracker.

Uses the same `recovery_message` mechanism as `completion_gate.py`.

Two hooks, wired together in factory.py:

- `full_record_fetch_tracking` (POST_TOOL_USE) records the record IDs
  that were actually fetched into `context.full_records_fetched`, so
  `build_candidates` in the gate can exclude them from the next candidate
  computation.

- `full_record_gate` (POST_MODEL) is the gate itself. It runs only when:
  - the model produced a text answer with no tool calls, AND
  - the response is not truncated, AND
  - `dynamic_fetch_full_record` is in this agent's spec.tool_names, AND
  - `context.needs_whole_document` is True, AND
  - there are still un-fetched, incomplete records, AND
  - the nudge budget has not been exhausted.

  Only then does it invoke the IFetchJudge (once per request). The judge
  receives the query, the draft answer, and the candidate list, and returns
  the record IDs it thinks are necessary. If the verdict is empty, judged=False,
  or no candidates remain, the gate is silent and the answer stands.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import (
        ModelResponseContext,
        ToolResultContext,
    )
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext
    from app.modules.agents.record_escalation.judge import IFetchJudge

__all__ = ["full_record_fetch_tracking", "full_record_gate"]

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME as _FETCH_FULL_RECORD_TOOL_NAME
_FETCH_FULL_RECORD_TOOL_NAME_LEGACY = "dynamic_fetch_full_record"
_DEFAULT_MAX_NUDGES = 1
logger = logging.getLogger(__name__)


def _response_text(message: object) -> str:
    if isinstance(message, AssistantMessage):
        return message.text
    return ""


def full_record_fetch_tracking(context: "AgentContext") -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE middleware factory.

    Records the record IDs returned by dynamic_fetch_full_record into
    context.full_records_fetched so build_candidates can exclude them.
    """

    _fetch_names = frozenset({_FETCH_FULL_RECORD_TOOL_NAME, _FETCH_FULL_RECORD_TOOL_NAME_LEGACY})

    async def _middleware(ctx: "ToolResultContext", next_fn: "Next") -> None:
        await next_fn()
        if resolve_tool_name(ctx) not in _fetch_names:
            return
        # Extract record_ids from the tool call arguments (via ToolScope.call).
        scope = ctx.scope
        call = scope.call if scope is not None else None
        if call is None:
            return
        args = call.arguments or {}
        record_ids = args.get("record_ids") or []
        if isinstance(record_ids, list):
            for rid in record_ids:
                if isinstance(rid, str) and rid:
                    context.full_records_fetched.add(rid)
                    context.tool_state.setdefault("full_records_fetched", set()).add(rid)

    return _middleware


def full_record_gate(
    context: "AgentContext",
    judge: "IFetchJudge",
    *,
    max_nudges: int = _DEFAULT_MAX_NUDGES,
) -> "Middleware[ModelResponseContext]":
    """POST_MODEL middleware factory.

    `context` is the same `AgentContext` threaded through the whole request
    (top-level agent + every spawned child), so `needs_whole_document` and
    `full_record_gate_nudges` are tracked tree-wide.
    """
    # Guard: the judge runs at most once per request across all POST_MODEL calls.
    _judge_called = [False]

    async def _middleware(ctx: "ModelResponseContext", next_fn: "Next") -> None:
        await next_fn()

        if ctx.tool_calls or getattr(ctx.response, "truncated", False):
            return

        run_scope = ctx.scope.run if ctx.scope is not None else None
        if run_scope is None:
            return

        tool_names = set(run_scope.spec.tool_names or [])
        if not (tool_names & frozenset({_FETCH_FULL_RECORD_TOOL_NAME, _FETCH_FULL_RECORD_TOOL_NAME_LEGACY})):
            return

        # Bounds the judge to requests already flagged as whole-document; the
        # model's own fetch decision (tool description + injected policy +
        # candidate list) is the primary mechanism, not this gate.
        if not context.needs_whole_document:
            return

        draft = _response_text(ctx.response).strip()
        if not draft:
            return

        if context.full_record_gate_nudges >= max_nudges:
            return

        if _judge_called[0]:
            return

        # Recompute candidates from accumulated state — the retrieval tool
        # may have been called multiple times since the stashed plan was built.
        from app.modules.agents.record_escalation import (
            analyze_coverage,
            build_candidates,
        )

        all_final = context.tool_state.get("final_results") or []
        vr_map = context.tool_state.get("virtual_record_id_to_result") or {}
        already_fetched: set[str] = set(context.full_records_fetched)
        coverage = analyze_coverage(all_final, vr_map)

        seen_rids: set[str] = set()
        records_in_order: list[dict] = []
        for entry in all_final:
            vrid = entry.get("virtual_record_id")
            if not vrid:
                continue
            rec = vr_map.get(vrid)
            if not rec:
                continue
            rid = rec.get("id")
            if rid and rid not in seen_rids:
                seen_rids.add(rid)
                records_in_order.append(rec)

        plan = build_candidates(
            coverage=coverage,
            records_in_relevance_order=records_in_order,
            already_fetched_ids=already_fetched,
        )

        if not plan.has_candidates:
            return

        query = context.tool_state.get("query") or ""
        _judge_called[0] = True

        try:
            verdict = await judge.judge(
                query=query,
                draft_answer=draft,
                plan=plan,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("full_record_gate: judge raised unexpectedly — silent: %s", exc)
            return

        if not verdict.judged or not verdict.needed:
            return

        context.full_record_gate_nudges += 1
        needed_ids = [rid for rid, _ in verdict.needed]
        reasons = {rid: reason for rid, reason in verdict.needed}

        id_lines = "\n".join(
            f"- {rid}" + (f" ({reasons.get(rid, '')})" if reasons.get(rid) else "")
            for rid in needed_ids
        )
        nudge = (
            f"[System: the draft answer above is likely incomplete. "
            f"The following record(s) contain content required to answer correctly — "
            f"call `{_FETCH_FULL_RECORD_TOOL_NAME}` with these record_ids before answering:\n"
            f"{id_lines}]"
        )
        ctx.recovery_message = UserMessage(content=nudge, injected=True)

    return _middleware
