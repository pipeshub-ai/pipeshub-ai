"""The tool calls a run left open: what Node closes when it ends a run itself (EV-06, C2.49).

A worker ends a run with an outcome for every call it opened (``effects.journal.closing_outcomes``). Node
ends a run with no worker holding it (a cancel of a queued or suspended run, the deadline sweeper): a call
whose intent a crashed attempt journaled is then still open, and Node journals its ``tool.outcome`` as
``cancelled`` ahead of the terminal event, so every call ends with exactly one outcome.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.journal.types import JournalEvent

# Node's own outcome: it read nothing of the call, so it carries no result and the platform's integrity.
_NODE_LABELS = {"integrity": "internal"}


def open_calls(events: Iterable[JournalEvent], run_id: str) -> list[str]:
    """Invocations of ``run_id`` with a ``tool.intent`` or ``tool.denied`` and no ``tool.outcome`` or
    ``tool.unknown`` after it, in the order they were opened."""
    opened: dict[str, bool] = {}
    outcomes: set[str] = set()
    for event in events:
        if event.run_id != run_id:
            continue
        invocation = event.data.get("invocationId")
        if not isinstance(invocation, str):
            continue
        if event.type in ("tool.intent", "tool.denied"):
            # A re-call after ``tool.reconciled`` not_applied opens the call again.
            opened[invocation] = invocation in outcomes
        elif event.type in ("tool.outcome", "tool.unknown"):
            opened[invocation] = True
            if event.type == "tool.outcome":
                outcomes.add(invocation)
    return [invocation for invocation, ended in opened.items() if not ended]


def node_closing_outcomes(events: Iterable[JournalEvent], run_id: str) -> list[NewEvent]:
    """The ``tool.outcome`` Node journals for each call ``run_id`` left open, before the run's terminal event."""
    return [
        NewEvent("tool.outcome", {"invocationId": invocation, "status": "cancelled", "labels": dict(_NODE_LABELS)})
        for invocation in open_calls(events, run_id)
    ]


__all__ = ["node_closing_outcomes", "open_calls"]
