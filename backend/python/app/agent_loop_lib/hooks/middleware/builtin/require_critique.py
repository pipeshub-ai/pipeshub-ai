from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.core.types import Confidence

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import (
        ToolCallContext,
        ToolResultContext,
    )

"""`require_critique` — the exemplar of "forced critique via hooks, never
LLM judgment" (see `.claude/rules/principles.md` rule 2: "Agent is
probabilistic; everything else is programmatic"; `examples/
05_confidence_gated_critique.py` demonstrates this end to end).

Distinct from `supervisor_gate.py`'s `supervisor_confidence_gate()` (which
BLOCKS a single LOW-confidence `create_plan` result outright, always on):
this middleware instead DENIES every execution-phase tool call — via
PRE_TOOL_USE, not POST_TOOL_USE — until a plan whose confidence fell below
`confidence_threshold` has been explicitly re-reviewed by `critique_plan`.
It never decides the plan is unusable; it just makes "critique before you
act on an uncertain plan" a hard constraint instead of a prompt suggestion.

Also the exemplar for migrating state OFF a closure-scoped dict and onto a
`StateSlot` (see `core/scope.py`) — this used to be a `dict[str | None,
bool]` keyed by `session_id`, shared across every `Agent` on one kernel.
That had two problems: (1) concurrent runs sharing no `session_id` (`None`)
shared one bucket, a cross-run leak; (2) the state was invisible to
checkpoints. One semantic constraint had to survive the migration:
`run_child()` propagates the parent's scope so the WHOLE SPAWN TREE shares
one pending-critique bucket — a child spawned while a critique is pending
must also be blocked, and a `critique_plan` pass anywhere in the tree
clears it for everyone. A plain per-run bool with `inherit=False` would
silently drop that, so `PENDING_CRITIQUE` holds a small mutable object and
`inherit=True` copies it BY REFERENCE — the whole tree shares one holder
(today's semantics), while separate runs get separate holders (fixing the
leak). Note the inheritance caveat cuts both ways: a child spawned BEFORE
`create_plan` runs already holds the shared holder, so a later pending
flag applies to it too, matching the old session-keyed behavior.
"""

__all__ = ["require_critique", "PENDING_CRITIQUE"]


class _CritiqueState:
    """Mutable holder shared by reference across a spawn tree."""

    def __init__(self) -> None:
        self.pending: bool = False


# Not `persist=True` (yet): the closure-dict version never survived a
# process restart either. A future revision could persist `pending` as a
# plain bool once there's a resume test to pin the semantics.
PENDING_CRITIQUE: StateSlot[_CritiqueState] = StateSlot(
    key="require_critique.state",
    default_factory=_CritiqueState,
    inherit=True,
)

_CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}

# Tools allowed to run even while a critique is pending — planning/
# escalation/bookkeeping tools only. Deliberately excludes `task_complete`:
# declaring the run done on an unreviewed plan is exactly the outcome this
# middleware exists to prevent.
_ALWAYS_ALLOWED_SUFFIXES = (
    "/create_plan", "/critique_plan", "/replan", "/verify_result",
    "/request_review", "/write_todos", "/clarify",
)


def require_critique(confidence_threshold: Confidence = Confidence.HIGH):
    """Build a PRE_TOOL_USE + POST_TOOL_USE middleware pair (installed
    together via one `HookRegistry` install call) that denies
    execution-phase tools whenever the most recent `create_plan` result's
    confidence was below `confidence_threshold`, until `critique_plan` has
    run since.

    State lives on the run's `RunScope` via `PENDING_CRITIQUE` (see module
    docstring) rather than a closure dict — scoped per spawn tree via
    `inherit=True`, not per `session_id` string, so unrelated concurrent
    runs never share a bucket even when neither sets a `session_id`.
    """

    async def _post_tool_use(ctx: "ToolResultContext", next_fn) -> None:
        run = ctx.scope.turn.run if ctx.scope else None
        if run is None:
            await next_fn()
            return
        if ctx.tool_path.endswith("/create_plan") and ctx.tool_response.success:
            data = ctx.tool_response.data
            confidence_raw = data.get("confidence") if isinstance(data, dict) else None
            confidence = _parse_confidence(confidence_raw)
            if confidence is not None:
                run.get(PENDING_CRITIQUE).pending = (
                    _CONFIDENCE_RANK[confidence] < _CONFIDENCE_RANK[confidence_threshold]
                )
        elif ctx.tool_path.endswith("/critique_plan") and ctx.tool_response.success:
            data = ctx.tool_response.data
            if isinstance(data, dict) and data.get("passed"):
                run.get(PENDING_CRITIQUE).pending = False
        await next_fn()

    async def _pre_tool_use(ctx: "ToolCallContext", next_fn) -> None:
        run = ctx.scope.turn.run if ctx.scope else None
        if (
            run is not None
            and run.get(PENDING_CRITIQUE).pending
            and not ctx.tool_path.endswith(_ALWAYS_ALLOWED_SUFFIXES)
        ):
            ctx.deny(
                f"require_critique: plan confidence was below {confidence_threshold.value!r} — "
                "call critique_plan (and get passed=True) before using execution tools."
            )
        await next_fn()

    marker = object()

    def _install(kernel) -> None:
        # Idempotent per (kernel, this require_critique(...) call) — several
        # `Agent`s built from the same `AgentSpec` can share one runtime's
        # kernel (see `AgentSpec.middleware`'s docstring); re-installing the
        # identical closure would double-dispatch every tool call.
        installed = getattr(kernel, "_require_critique_installed", None)
        if installed is None:
            installed = set()
            kernel._require_critique_installed = installed
        if marker in installed:
            return
        installed.add(marker)

        from app.agent_loop_lib.hooks.events import HookEvent
        kernel.on(HookEvent.PRE_TOOL_USE).use(_pre_tool_use)
        kernel.on(HookEvent.POST_TOOL_USE).use(_post_tool_use)

    return _install


def _parse_confidence(raw: object) -> Confidence | None:
    if raw is None:
        return None
    try:
        return Confidence(raw)
    except ValueError:
        return None
