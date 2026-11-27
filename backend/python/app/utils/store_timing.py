"""Announce how long each storage port call took: ``db.query.completed`` with ``store`` and ``operation``.

``announce_store_calls`` is applied once per store in its factory (graph, vector,
kv), so provider code needs no per-method edits. ``operation`` is the port
method name, a set fixed by the interface.
"""

import asyncio
import inspect
import time
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from functools import wraps
from typing import Any, TypeVar

from app import platform_events

# (task, store instance) pairs inside an outer port call. A port method calling
# another port method on the same store is timed once, as the outer operation; a
# call to a different store, or from a task it spawned, is timed on its own.
_in_call: ContextVar[frozenset] = ContextVar("pipeshub_db_in_call", default=frozenset())

# Lifecycle and long-wait operations (connect, listeners, watches) would skew a percentile.
_UNTIMED_OPS = frozenset({"connect", "disconnect", "close", "wait_for_connection", "watch_key"})
_UNTIMED_PREFIXES = ("subscribe", "unsubscribe", "watch")

T = TypeVar("T")


def announce_store_calls(store: T, name: str) -> T:
    """Time every public coroutine method of ``store`` in place and return it.

    Wraps per instance (not via a proxy) so ``type(store)`` and ``isinstance``
    stay exact. Decided when the store is built: with nothing listening for the
    event (plug-ins are loaded before any store exists) the store is left as it is.
    """
    if not platform_events.has_listeners("db.query.completed"):
        return store
    for op in dir(type(store)):
        if op.startswith("_") or op in _UNTIMED_OPS or op.startswith(_UNTIMED_PREFIXES) or not inspect.iscoroutinefunction(
            inspect.getattr_static(type(store), op, None)
        ):
            continue
        setattr(store, op, _timed(getattr(store, op), name, op))
    return store


def _timed(fn: Callable[..., Coroutine[Any, Any, Any]], store: str, op: str) -> Callable[..., Coroutine[Any, Any, Any]]:
    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        key = (asyncio.current_task(), id(getattr(fn, "__self__", fn)))
        active = _in_call.get()
        if key in active:
            return await fn(*args, **kwargs)
        token = _in_call.set(active | {key})
        start = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            _in_call.reset(token)
            platform_events.emit("db.query.completed", store=store, operation=op, duration_seconds=time.perf_counter() - start)

    return wrapper
