from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.types import Confidence
from app.agent_loop_lib.modules.pipeline.planner.base import (
    extract_trailing_confidence,
    parse_confidence,
)
from app.agent_loop_lib.tools.tags import TAG_PLANNING_CREATE_PLAN

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
exists mid-loop. `hooks/middleware/builtin/turn_guards.py::
install_supervisor_confidence_gate()` wires it onto a kernel — an opt-in
via `AgentSpec.middleware` (or `ControlPlaneConfig.hooks`) for roles that
call `create_plan` and want LOW-confidence plans blocked automatically,
never left to LLM judgment for those roles.

`create_plan`'s result comes back in one of two shapes (see that module's
own docstring for why neither forces the model into structured JSON just
to carry a confidence value):
  - a dict payload (`{"plan": ..., "confidence": "low|medium|high"}`) from
    the structured `steps` path, where `confidence` is a plain tool-call
    argument;
  - the model's own free-form markdown plan text, ending with a
    `Confidence: low|medium|high` line, from the `DefaultPlanner` path —
    extracted via the exact same `extract_trailing_confidence()` that
    populated `Plan.confidence` when the text was produced.
"""

__all__ = ["confidence_allows_execution", "confidence_from_tool_response", "supervisor_confidence_gate"]


def confidence_from_tool_response(data: object) -> Confidence | None:
    """Extract a `Confidence` from a successful `create_plan` result's
    `data`, regardless of which of the two output shapes produced it.
    Returns `None` for anything unrecognizable (e.g. a different tool's
    result) — a caller should treat that as "nothing to evaluate", never
    as LOW."""
    if isinstance(data, dict):
        raw = data.get("confidence")
        return parse_confidence(raw) if raw is not None else None
    if isinstance(data, str):
        return extract_trailing_confidence(data)
    return None


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
        if TAG_PLANNING_CREATE_PLAN in ctx.tags and ctx.tool_response.success:
            confidence = confidence_from_tool_response(ctx.tool_response.data)
            if confidence is not None and not confidence_allows_execution(confidence):
                ctx.block(
                    f"Supervisor blocked: plan confidence is {confidence.value}. "
                    "Revise the plan or call request_review before proceeding."
                )
        await next_fn()

    return _middleware
