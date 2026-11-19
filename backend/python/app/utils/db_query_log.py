"""One record per database client call that failed or was slow (contract: docs/logging.md, "Database queries (Python)").

The ArangoDB and Neo4j clients decorate their query methods with :func:`query_logged`; the Postgres runtime calls
:func:`query_ended` per statement. The record is an operation record: it does not mark the exception as logged, so the layer
that handles the failure still writes its own line, with the one stack. It never carries the driver's text or the query.
"""

import functools
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from types import FrameType
from typing import Any, TypeVar

from app.utils.error_class import is_stamped, level_for, mark_error
from app.utils.logging_fields import event_fields

DEFAULT_SLOW_QUERY_MS = 500
Describe = Callable[[Exception], tuple[str | int | None, str, Mapping[str, object], bool]]
"""(code, error_class, extra fields, hide_text) of a driver error."""

_T = TypeVar("_T")


def _threshold(environ: Mapping[str, str]) -> int:
    """``DB_SLOW_QUERY_MS``: milliseconds, ``0`` turns slow records off; anything unusable means the default."""
    try:
        value = int(environ.get("DB_SLOW_QUERY_MS", ""))
    except ValueError:
        return DEFAULT_SLOW_QUERY_MS
    return value if value >= 0 else DEFAULT_SLOW_QUERY_MS


def slow_query_ms() -> int:
    """The current ``DB_SLOW_QUERY_MS``, read per call: some services import this module before they load ``.env``."""
    return _threshold(os.environ)


CALLER_FRAME: ContextVar[FrameType | None] = ContextVar("db_query_caller_frame", default=None)
"""The caller of a call that runs in another task (a Postgres provider transaction): found while that caller's stack was live."""


# Between a caller and the client, walked through: `async with` around a unit of work, asyncio.wait_for / timeouts.
_PLUMBING = ("contextlib", "asyncio.")
_LOOP = ("asyncio.events", "asyncio.base_events")  # the event loop running a task: nothing past it called this task


def _first_outside(frame: FrameType | None, skip: tuple[str, ...]) -> FrameType | None:
    """The first live frame outside ``skip`` and this module, or None when the walk reaches the task's root (the loop) first."""
    while frame is not None:
        name = frame.f_globals.get("__name__", "")
        if name in _LOOP:
            return None
        if name != __name__ and not name.startswith(skip + _PLUMBING):
            return frame
        frame = frame.f_back
    return None


def caller_origin(skip: tuple[str, ...]) -> FrameType | None:
    """The caller of the function that calls this, for work that will run in another task (a Postgres provider transaction).
    Found now, while the caller's stack is live: once it awaits the other task its frame is suspended (``f_back`` is None) and
    cannot be walked to. Else the origin this context inherited. Set it in :data:`CALLER_FRAME` around the work."""
    return _first_outside(sys._getframe(2), skip) or CALLER_FRAME.get()


def _described(describe: Describe, exc: Exception) -> tuple[str | int | None, str, Mapping[str, object], bool]:
    try:
        return describe(exc)
    except Exception:
        return None, "internal", {}, True


def query_ended(
    logger: logging.Logger, backend: str, op: str, started: float, exc: Exception | None = None, /, *,
    describe: Describe, skip: tuple[str, ...], **fields: object,
) -> None:
    """``db.query.failed`` when ``exc`` is set, ``db.query.slow`` when the call took at least :func:`slow_query_ms`.

    ``[file:line]`` and ``caller`` are the first frame outside ``skip`` (the provider line that called the client), else
    :data:`CALLER_FRAME`. A failure is stamped on ``exc`` (class, hidden text) for the handler's ``log_failure``; an exception an
    inner call already recorded writes nothing. Never raises.
    """
    try:
        if exc is not None and is_stamped(exc):
            return
        duration_ms = int((time.perf_counter() - started) * 1000)
        if exc is None:
            threshold = slow_query_ms()
            if not threshold or duration_ms < threshold:
                return
            event, level = "db.query.slow", logging.WARNING
            fields["threshold_ms"] = threshold
        else:
            code, cls, extra, hide_text = _described(describe, exc)
            mark_error(exc, cls, hide_text=hide_text)
            event, level = "db.query.failed", level_for(cls)
            fields.update(extra)
            fields.update(code=code, error_class=cls, error_type=type(exc).__name__, outcome="failure")
        if not logger.isEnabledFor(level):
            return
        here = sys._getframe(1)
        caller = _first_outside(here, skip) or CALLER_FRAME.get()
        where = caller or _first_outside(here, ()) or here  # no caller: the client's own line, not this module's
        name = f"{caller.f_globals.get('__name__', '').rsplit('.', 1)[-1]}.{caller.f_code.co_qualname}" if caller else "unknown"
        record = logger.makeRecord(
            logger.name, level, where.f_code.co_filename, where.f_lineno, event, (), None, where.f_code.co_name,
            event_fields(event, {"backend": backend, "op": op, **fields, "caller": name, "duration_ms": duration_ms}),
        )
        logger.handle(record)
    except Exception:
        pass  # a log record never breaks the query


def query_logged(
    backend: str, op: str, *, describe: Describe, skip: tuple[str, ...],
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Decorate an async client method (``self.logger``): one :func:`query_ended` when it raises or is slow.

    Placed outside the client's own retries, so a retry it wins writes nothing. A cancellation is not a query failure.
    """
    def wrap(method: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @functools.wraps(method)
        async def run(self: Any, *args: object, **kwargs: object) -> _T:  # noqa: ANN401
            started = time.perf_counter()
            try:
                result = await method(self, *args, **kwargs)
            except Exception as e:
                query_ended(self.logger, backend, op, started, e, describe=describe, skip=skip)
                raise
            threshold = slow_query_ms()
            if threshold and (time.perf_counter() - started) * 1000 >= threshold:
                query_ended(self.logger, backend, op, started, describe=describe, skip=skip)
            return result
        return run
    return wrap
