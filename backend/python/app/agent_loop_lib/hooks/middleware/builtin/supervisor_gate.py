from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Confidence

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext

"""The deterministic confidence-routing gate — see `.claude/rules/
principles.md`'s "confidence routing": LOW-confidence blocking is a hard,
checkable rule (belongs in a hook/pure-function gate); MEDIUM-confidence
escalation to a human or senior agent is inherently interactive/
probabilistic (belongs in a tool call — see
`tools/builtin/planning/request_review.py`).

`supervisor_confidence_gate()` below is the POST_TOOL_USE middleware that
applies this deterministic gate to plans produced via the `create_plan`
tool call — the moment a plan (and thus a confidence rating) actually
exists mid-loop. `hooks/middleware/builtin/turn_guards.py::install_turn_guards()`
installs it unconditionally on every `Agent`'s kernel, so critique/
confidence review is never left to LLM judgment regardless of role or loop
strategy.
"""

__all__ = ["confidence_allows_execution", "supervisor_confidence_gate"]


def confidence_allows_execution(confidence: Confidence) -> bool:
    """Pure, deterministic policy: LOW confidence never proceeds without
    review; HIGH/MEDIUM are allowed to continue (MEDIUM should still be
    routed through `request_review` — see `tools/builtin/planning/request_review.py`
    — before acting on it, but that escalation is the agent's/caller's own
    probabilistic decision, not something this gate can make for them)."""
    return confidence != Confidence.LOW


def supervisor_confidence_gate():
    """POST_TOOL_USE middleware: blocks the `create_plan` tool's result
    when the plan it produced comes back with LOW confidence, applying
    `confidence_allows_execution()` at the one point a confidence rating
    actually exists on this path — right after `create_plan` runs (see
    `tools/builtin/planning/create_plan.py`). Blocking the tool RESULT (rather than
    aborting the whole run, as `Supervisor.review()`'s preamble path does)
    lets the agent see why and route to `request_review`
    (`tools/builtin/planning/request_review.py`) or revise the plan itself, instead
    of a hook silently deciding the run is over.

    A no-op for every other tool call, and for `create_plan` calls that
    fail outright or don't return a recognizable `confidence` field — this
    only ever escalates a decision, never denies one when there's nothing
    to evaluate.
    """

    async def _middleware(ctx: "ToolResultContext", next_fn) -> None:
        if ctx.tool_path.endswith("/create_plan") and ctx.tool_response.success:
            data = ctx.tool_response.data
            confidence_raw = data.get("confidence") if isinstance(data, dict) else None
            if confidence_raw is not None:
                try:
                    confidence = Confidence(confidence_raw)
                except ValueError:
                    confidence = None
                if confidence is not None and not confidence_allows_execution(confidence):
                    ctx.block(
                        f"Supervisor blocked: plan confidence is {confidence.value}. "
                        "Revise the plan or call request_review before proceeding."
                    )
        await next_fn()

    return _middleware
