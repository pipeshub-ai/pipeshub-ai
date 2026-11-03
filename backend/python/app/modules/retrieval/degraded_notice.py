"""Whether a search of this request was answered with less than the full search (``SearchResult.degraded``).

The retrieval service notes it; the chat stream reads it when the run completes. A ContextVar holding a mutable list is shared by the
tasks the request spawns, so a search made by a tool in another task is seen too.
"""
from contextvars import ContextVar

_seen: ContextVar[list[bool] | None] = ContextVar("search_degraded", default=None)


def track() -> None:
    """Start tracking for this request (a no-op when it already is)."""
    if _seen.get() is None:
        _seen.set([])


def note() -> None:
    if (seen := _seen.get()) is not None:
        seen.append(True)


def was_degraded() -> bool:
    return bool(_seen.get())
