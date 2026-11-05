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

from app.core.org_context import spawn_in_request

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        Awaitable,
        Callable,
        Coroutine,
        Hashable,
        Iterable,
        Mapping,
    )

P = ParamSpec("P")

_turn: ContextVar[dict[Hashable, asyncio.Task] | None] = ContextVar("pipeshub_turn_memo", default=None)


def begin_turn() -> dict[Hashable, asyncio.Task]:
    tasks: dict[Hashable, asyncio.Task] = {}
    _turn.set(tasks)
    return tasks


def in_turn() -> bool:
    return _turn.get() is not None


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


async def memo_each(
    namespace: Hashable,
    keys: Iterable[Hashable],
    compute: Callable[[list[Hashable]], Coroutine[Any, Any, Mapping[Hashable, Any]]],
) -> dict[Hashable, Any]:
    """``{key: value}`` for *keys*, each looked up once per turn.

    Keys nobody in the turn has asked about go to one ``compute(missing)``;
    keys another caller's batch is fetching wait on that batch. A key the
    batch leaves out maps to None. A failed batch raises to its callers and
    is asked again next time.
    """
    wanted = list(dict.fromkeys(keys))
    cache = _turn.get()
    if cache is None:
        found = await compute(wanted)
        return {k: found.get(k) for k in wanted}
    owner: dict[Hashable, asyncio.Task] = {}
    missing = []
    for k in wanted:
        task = cache.get((namespace, k))
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            missing.append(k)
        else:
            owner[k] = task
    if missing:
        task = spawn_in_request(compute(missing))
        for k in missing:
            cache[(namespace, k)] = owner[k] = task
    batches = {task: await asyncio.shield(task) for task in dict.fromkeys(owner.values())}
    return {k: batches[owner[k]].get(k) for k in wanted}


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
