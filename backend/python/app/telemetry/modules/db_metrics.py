"""Storage-port latency: ``pipeshub_db_query_seconds{store,op}``.

``instrument_store`` is applied once per store in its factory (graph, vector,
kv), so provider code needs no per-method edits. ``op`` is the port method
name, a set fixed by the interface, so cardinality is bounded by code.
"""

import asyncio
import inspect
import time
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from functools import wraps
from typing import Any, TypeVar

from app.telemetry.backend import METRICS_BACKEND, NoopBackend

DB_QUERY_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_db_query_seconds",
    "Latency of storage port calls (graph, vector, kv) in seconds",
    ["store", "op"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# (task, store instance) pairs inside an outer port call. A port method calling
# another port method on the same store is timed once, as the outer op; a call
# to a different store, or from a task it spawned, is timed on its own.
_in_call: ContextVar[frozenset] = ContextVar("pipeshub_db_in_call", default=frozenset())

# Lifecycle and long-wait ops (connect, listeners, watches) would skew p95.
_UNTIMED_OPS = frozenset({"connect", "disconnect", "close", "wait_for_connection", "watch_key"})
_UNTIMED_PREFIXES = ("subscribe", "unsubscribe", "watch")

T = TypeVar("T")


def instrument_store(store: T, name: str) -> T:
    """Time every public coroutine method of ``store`` in place and return it.

    Wraps per instance (not via a proxy) so ``type(store)`` and ``isinstance``
    stay exact. No-op when telemetry is off.
    """
    if isinstance(METRICS_BACKEND, NoopBackend):
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
            DB_QUERY_SECONDS.observe(store, op, value=time.perf_counter() - start)

    return wrapper
