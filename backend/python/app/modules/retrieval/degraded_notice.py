"""Whether a search of this request was answered from a partial search.

Provider contract: a vector store that answers a query from less than its full search calls ``note(reason)`` (it needs no hits to do so,
so an empty answer can be flagged), or sets ``SearchResult.degraded`` on the hits it returns; the retrieval service turns the latter into
the former. The chat stream reads ``was_degraded`` when the run completes. A ContextVar holding a mutable list is shared by the tasks the
request spawns, so a search made by a tool in another task is seen too; outside a tracked request ``note`` does nothing.
"""
from contextvars import ContextVar
from typing import Any

_seen: ContextVar[list[str] | None] = ContextVar("search_degraded", default=None)


def track() -> None:
    """Start tracking for this request (a no-op when it already is)."""
    if _seen.get() is None:
        _seen.set([])


def note(reason: str = "partial") -> None:
    if (seen := _seen.get()) is not None:
        seen.append(reason)


def count() -> int:
    """How many times this request has been noted, so a caller can tell whether its own search was."""
    return len(_seen.get() or ())


def was_degraded() -> bool:
    return bool(_seen.get())


def flag(completion_data: dict[str, Any]) -> dict[str, Any]:
    """Mark a final answer whose request had a degraded search (the UI shows a note)."""
    if was_degraded():
        completion_data["searchDegraded"] = True
    return completion_data
