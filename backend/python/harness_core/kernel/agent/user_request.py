"""A child run's goal with the person's request ahead of its delegated task.

The parent model's goal is a paraphrase; a child given only that carries any drift in it with no way
to notice. Here rather than beside ``parent_results`` because the kernel's spawn path applies it.
"""

from __future__ import annotations

from harness_core.kernel.core.types import Goal

__all__ = ["with_user_request"]

_USER_REQUEST_HEADER = "## The user's request (verbatim, for context: work only on your delegated task)"
# The request can carry attachment text sized for the parent's window; every child gets a copy.
_USER_REQUEST_MAX_CHARS = 4_000
_USER_REQUEST_CUT = "\n[… the rest of the user's message, such as attached files, is not shown here]"
_DELEGATED_TASK_HEADER = "## Your delegated task"


def with_user_request(goal: Goal, request: str) -> Goal:
    """``goal`` with the person's request, verbatim, ahead of the delegated
    task. The parent model's goal is a paraphrase; the child needs the
    person's own words to resolve what the task refers to and to notice a
    task that drifted from them."""
    if request in goal.description or goal.description.startswith(_USER_REQUEST_HEADER):
        return goal
    shown = request
    if len(shown) > _USER_REQUEST_MAX_CHARS:
        shown = shown[:_USER_REQUEST_MAX_CHARS] + _USER_REQUEST_CUT
    return goal.model_copy(update={
        "description": (
            f"{_USER_REQUEST_HEADER}\n\n{shown}\n\n{_DELEGATED_TASK_HEADER}\n\n{goal.description}"
        ),
    })
