"""Results memoized for the length of one chat turn.

`begin_turn()` puts a fresh dict in a ContextVar at the start of a turn. Tasks
the turn spawns afterwards inherit the context, so they all see the same dict;
nothing survives into the next turn, which keeps permission answers exactly as
fresh as they were when every caller asked the graph itself.

`memo()` stores the in-flight task rather than the result, so concurrent callers
inside a turn wait on one query instead of each issuing their own.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Hashable

_turn: ContextVar[dict[Hashable, asyncio.Task] | None] = ContextVar("pipeshub_turn_memo", default=None)


def begin_turn() -> None:
    _turn.set({})


async def memo(key: Hashable, compute: Callable[[], Awaitable[Any]]) -> Any:  # noqa: ANN401
    cache = _turn.get()
    if cache is None:
        return await compute()
    task = cache.get(key)
    if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
        task = asyncio.ensure_future(compute())
        cache[key] = task
    # shield: one caller's cancellation must not cancel the query for the rest.
    return await asyncio.shield(task)
