"""A deadline for one request, readable by anything that runs under it.

A search answers callers that stop waiting: the MCP server, for one, gives up
on a request that has not started answering within ``PIPESHUB_MCP_TIMEOUT_MS``
(60 s by default) and tells the agent PipesHub did not answer. The steps of a
search each have their own limit, and the vector search's alone is 60 s
(``VECTOR_SEARCH_RETRY_BUDGET_SECONDS``), so a slow search used to run past the
caller and never return the partial results or the clear error it would have.

A route sets the deadline with ``deadline_after``; a step that can wait long
asks ``seconds_left`` and fits its own limit inside it. The deadline lives in a
context variable, so the searches a timeline runs side by side share it, and a
caller that sets none (the chat path, indexing) keeps every limit as it was.
"""

from __future__ import annotations

import contextlib
import math
import os
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["deadline_after", "search_deadline_seconds", "seconds_left"]

_DEADLINE: ContextVar[float | None] = ContextVar("request_deadline", default=None)

# Inside the MCP server's default 60 s, with room for the gateway to save the
# search and send it on.
_DEFAULT_SEARCH_DEADLINE_SECONDS = 50.0


def search_deadline_seconds() -> float | None:
    """How long a search may take, from ``SEARCH_DEADLINE_SECONDS``; ``None``
    (no deadline) when it is ``0`` or less. A value that isn't a finite number
    (unreadable, ``nan``, ``inf``) gives the default."""
    raw = os.getenv("SEARCH_DEADLINE_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_SEARCH_DEADLINE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_SEARCH_DEADLINE_SECONDS
    if not math.isfinite(value):
        return _DEFAULT_SEARCH_DEADLINE_SECONDS
    return value if value > 0 else None


@contextlib.contextmanager
def deadline_after(seconds: float | None) -> Iterator[None]:
    """Everything run inside has until ``seconds`` from now; ``None`` sets no
    deadline. An earlier deadline already set is kept."""
    if seconds is None:
        yield
        return
    deadline = time.monotonic() + seconds
    outer = _DEADLINE.get()
    token = _DEADLINE.set(deadline if outer is None else min(outer, deadline))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def seconds_left() -> float | None:
    """Seconds until the current deadline (negative once it has passed), or
    ``None`` when no deadline is set."""
    deadline = _DEADLINE.get()
    return None if deadline is None else deadline - time.monotonic()
