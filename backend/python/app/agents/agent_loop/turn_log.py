"""`agent_turn.end`: the one line that says how a chat turn ended (contract: docs/logging.md).

Both bridges (`stream_bridge.run_agent_loop_stream`, `chat_modes.bridge.run_chat_stream`)
count the turn with `AgentRunTiming` and end it with `AgentRunTiming.end`; route exits
that happen before a bridge exists call `log_turn_end` directly. Ids (org, user,
conversation, agent, run) come from the bound log context. Counts, sizes and enums
only: never the query, the answer or the filters.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.events.base import EventType
from app.agents.agent_loop.error_classification import classify_error as user_error_code
from app.utils import error_class
from app.utils.logging_fields import log_event

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import AgentResult
    from app.agent_loop_lib.events.base import AgentEvent
    from app.agents.agent_loop.protocol.run_usage import UsageLedger
    from app.utils.stage_timer import RunTimer

logger = logging.getLogger(__name__)

# Every `fail(event="agent_failed")` is a max_turns exit (agent/loops.py,
# loops/plan_execute.py, loops/orchestrator.py).
_STOP_REASON_BY_END_EVENT = {
    "agent_failed": "max_turns",
    "agent_complete_partial": "max_turns",
    "agent_complete_after_max_turns": "max_turns",
    "guardrail_blocked": "guardrail",
    "turn_blocked": "guardrail",
    "agent_blocked": "guardrail",
}


def stop_reason_of(result: AgentResult, *, awaiting_approval: bool = False) -> str:
    """done | awaiting_input | max_turns | guardrail | cancelled | error, from how the run ended."""
    if result.cancelled:
        return "cancelled"
    if result.needs_input or awaiting_approval:
        return "awaiting_input"
    reason = _STOP_REASON_BY_END_EVENT.get(result.end_event)
    if reason is not None:
        return reason
    return "done" if result.success else "error"


# Exits whose layer already logged the failure: `llm_call.failed`, or the context
# guards' ERROR before `ContextBudgetExceeded`.
LOGGED_BY_THE_LAYER = frozenset({"llm_call_failed", "context_budget_exceeded"})


def _end_level(stop_reason: str, fields: dict[str, Any]) -> int:
    # Never two WARN+ lines for one failure.
    if stop_reason == "max_turns":
        return logging.WARNING
    if stop_reason != "error" or fields.get("end_event") in LOGGED_BY_THE_LAYER:
        return logging.INFO
    cls = fields.get("error_class")
    return error_class.level_for(cls) if cls else logging.WARNING


def log_turn_end(log: logging.Logger, stop_reason: str, *, exc: BaseException | None = None, **fields: Any) -> None:  # noqa: ANN401
    """One `agent_turn.end`. With ``exc``, the failure is logged first as `agent_turn.failed`
    (its own level and stack), and the end line stays INFO."""
    if exc is not None:
        error_class.log_failure(
            log, "agent_turn.failed", exc, error_class=fields.get("error_class"),
            run_id=fields.get("run_id"), code=fields.get("code"),
        )
        fields["error_class"] = fields.get("error_class") or error_class.classify_error(exc)
        level = logging.INFO
    else:
        level = _end_level(stop_reason, fields)
    log_event(log, "agent_turn.end", level=level, stop_reason=stop_reason, **fields)


class AgentRunTiming:
    """Counts one chat turn from the top-level agent's events, and times it as `RunTimer` spans.

    A model turn runs from TURN_START until its reply has finished streaming
    (TEXT_MESSAGE_END), so it excludes the tool calls that follow; a turn
    that never streams falls back to TURN_COMPLETE. A turn whose model call
    raised (its TEXT_MESSAGE_END says `discarded`, and a retry may follow),
    or a tool still running when the client disconnects, never gets its end
    event; `RunTimer.summary` shows it as ``(open)``. Turns are numbered
    from 1 in the order they start (``llm_turn_1``, ``llm_turn_2``, ...).
    A tool span runs from TOOL_CALL to TOOL_RESULT or TOOL_BLOCKED, matched
    on ``tool_call_id`` and named ``tool:<name>``. A sub-agent's events are
    skipped: its whole run already sits inside the parent's ``spawn_agent``
    tool span. Counting happens with timing off too. Logging only — nothing
    here changes what the agent does.
    """

    __slots__ = ("_timer", "_run_id", "_model", "_mode", "_ended", "turns", "tool_calls", "tool_errors")

    def __init__(self, timer: RunTimer, *, run_id: str, model: str | None, mode: str | None) -> None:
        self._timer = timer
        self._run_id = run_id
        self._model = model
        self._mode = mode
        self._ended = False
        self.turns = 0
        self.tool_calls = 0
        self.tool_errors = 0

    def on_event(self, event: AgentEvent) -> None:
        run_context = getattr(event, "run_context", None)
        if run_context is not None and run_context.parent_run_id is not None:
            return
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", None) or {}
        if event_type == EventType.TURN_START:
            self.turns += 1
            self._timer.start(("turn", payload.get("turn_index")), f"llm_turn_{self.turns}")
        elif event_type in (EventType.TEXT_MESSAGE_END, EventType.TURN_COMPLETE):
            if not payload.get("discarded"):
                self._timer.stop(("turn", payload.get("turn_index")))
        elif event_type == EventType.TOOL_CALL:
            self.tool_calls += 1
            self._timer.start(
                ("tool", payload.get("tool_call_id")), f"tool:{payload.get('tool') or 'unknown'}",
            )
        elif event_type in (EventType.TOOL_RESULT, EventType.TOOL_BLOCKED):
            if event_type == EventType.TOOL_RESULT and payload.get("is_error"):
                self.tool_errors += 1
            self._timer.stop(("tool", payload.get("tool_call_id")))

    def end(
        self, log: logging.Logger, stop_reason: str | None, *, result: AgentResult | None = None,
        awaiting_approval: bool = False, ledger: UsageLedger | None = None, exc: BaseException | None = None,
        **fields: Any,  # noqa: ANN401
    ) -> None:
        """Log `agent_turn.end` for this turn; only the first call logs. ``stop_reason=None``
        derives it from ``result`` here, so a bad result costs the line, never the answer.
        Never raises: it runs in the `finally` that must still send the stream's last frame."""
        if self._ended:
            return
        self._ended = True
        try:
            if stop_reason is None:
                stop_reason = (
                    stop_reason_of(result, awaiting_approval=awaiting_approval) if result is not None else "error"
                )
            line: dict[str, Any] = {
                "run_id": self._run_id, "model": self._model, "mode": self._mode,
                "turns": self.turns, "tool_calls": self.tool_calls, "tool_errors": self.tool_errors,
                "duration_ms": round(self._timer.total_ms), "spans": self._timer.summary(),
            }
            if ledger is not None:
                usages = [usage for _source, usage in ledger.entries]
                line |= {
                    "llm_calls": len(usages),
                    "tokens_in": sum(u.input_tokens for u in usages),
                    "tokens_out": sum(u.output_tokens for u in usages),
                    "cache_read_tokens": sum(u.cache_read_tokens for u in usages),
                }
            if result is not None:
                line |= {"end_event": result.end_event or None, "error_class": result.error_class}
                if not result.success and not result.cancelled:
                    line["code"] = user_error_code(result.error or "")[0]  # the code the client got
            log_turn_end(log, stop_reason, exc=exc, **(line | fields))
        except Exception:
            logger.debug("agent_turn.end failed", exc_info=True)


__all__ = ["AgentRunTiming", "log_turn_end", "stop_reason_of"]
