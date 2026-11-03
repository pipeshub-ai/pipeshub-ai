"""What a loop note may tell the model to do next.

Notes the loop writes into the model's context (deadline, stall, duplicate
call, truncation) must only name moves the run actually has. Agents built
without `task_complete` finish with a plain-text reply; telling one to call
a tool it was never granted leaves it no valid move.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_loop_lib.core.scope import RunScope, TurnScope

__all__ = ["TASK_COMPLETE", "can_call", "finish_move"]

TASK_COMPLETE = "task_complete"


def can_call(scope: "TurnScope | RunScope | Any | None", tool_name: str) -> bool:
    """Whether this run may call `tool_name`: registered, and inside the
    spec's grant (an empty grant means every registered tool)."""
    if scope is None:
        return False
    run = getattr(scope, "run", scope)
    registry = getattr(getattr(run, "runtime", None), "tool_registry", None)
    if registry is None or not registry.has(tool_name):
        return False
    granted = getattr(getattr(run, "spec", None), "tool_names", None)
    return not granted or tool_name in granted


def finish_move(scope: "TurnScope | RunScope | Any | None", *, answer: str = "final answer") -> str:
    """The way this run finishes, phrased to follow "…, or" / ";" in a note."""
    if can_call(scope, TASK_COMPLETE):
        return f"call {TASK_COMPLETE} with your {answer}"
    return f"reply with your {answer} as plain text, without calling tools"
