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
import contextlib
import functools
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, ParamSpec

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Hashable

P = ParamSpec("P")

_turn: ContextVar[dict[Hashable, asyncio.Task] | None] = ContextVar("pipeshub_turn_memo", default=None)


def begin_turn() -> dict[Hashable, asyncio.Task]:
    tasks: dict[Hashable, asyncio.Task] = {}
    _turn.set(tasks)
    return tasks


async def end_turn(tasks: dict[Hashable, asyncio.Task]) -> None:
    """Cancel lookups still running, so a closed stream does not leave a graph query holding a connection."""
    pending = [t for t in tasks.values() if not t.done()]
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


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


def per_turn_lookups(stream: Callable[P, AsyncGenerator[str, None]]) -> Callable[P, AsyncGenerator[str, None]]:
    """Give each run of a streaming turn its own memo, and end the lookups it left running.

    The inner stream is closed first, so a live turn is not cut off; closing the
    outer stream at any point, setup included, still reaches the cleanup.
    """

    @functools.wraps(stream)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[str, None]:
        lookups = begin_turn()
        try:
            async with contextlib.aclosing(stream(*args, **kwargs)) as chunks:
                async for chunk in chunks:
                    yield chunk
        finally:
            await end_turn(lookups)

    return wrapper
