"""Knowing when the thread work a parse started has really ended.

Cancelling an ``await`` on ``asyncio.to_thread`` returns at once and leaves the thread running. The parsing route
answers its own deadline by cancelling the parse, but the conversion may still hold the memory its permit and
reservation stand for, so those are held until every thread the parse started has returned.

``ThreadWorkExecutor`` is the loop's default executor: each job submitted from inside a ``track()`` block is added
to that block's tracker (the submit runs on the loop thread, in the submitting task's context).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_current: ContextVar[ThreadWork | None] = ContextVar("parse_thread_work", default=None)
_INSTALLED = "_parse_thread_work_installed"


class ThreadWork:
    """The executor jobs one parse started."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[Future[Any]] = set()

    def add(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.add(future)
        future.add_done_callback(self._discard)

    def _discard(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.discard(future)

    def running(self) -> int:
        with self._lock:
            return len(self._pending)

    def when_idle(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` (from the thread that finished last) once no job is pending; at once if none is."""
        with self._lock:
            waiting = list(self._pending)
        if not waiting:
            callback()
            return
        remaining = [len(waiting)]
        guard = threading.Lock()

        def one_done(_future: Future[Any]) -> None:
            with guard:
                remaining[0] -= 1
                last = remaining[0] == 0
            if last:
                callback()

        for future in waiting:
            future.add_done_callback(one_done)


class ThreadWorkExecutor(ThreadPoolExecutor):
    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:  # noqa: ANN401
        future = super().submit(fn, *args, **kwargs)
        work = _current.get()
        if work is not None:
            work.add(future)
        return future


def install(loop: asyncio.AbstractEventLoop) -> None:
    """Record the jobs submitted to the loop's default executor (once per loop).

    The executor the service installed (sized to the governor's ceilings, and shut down at shutdown) is kept: its
    ``submit`` is wrapped, and its size and identity do not change. Only a loop with no default executor yet gets a
    tracking one, with asyncio's own default size.
    """
    if getattr(loop, _INSTALLED, False):
        return
    executor = getattr(loop, "_default_executor", None)
    if executor is None:
        loop.set_default_executor(ThreadWorkExecutor(max_workers=min(32, (os.cpu_count() or 1) + 4)))
    elif not isinstance(executor, ThreadWorkExecutor):
        original = executor.submit

        def submit(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:  # noqa: ANN401
            future = original(fn, *args, **kwargs)
            work = _current.get()
            if work is not None:
                work.add(future)
            return future

        executor.submit = submit
    setattr(loop, _INSTALLED, True)


@contextlib.contextmanager
def track(work: ThreadWork | None = None) -> Iterator[ThreadWork]:
    """Collect the executor jobs submitted inside the block (into ``work``, when the caller keeps one)."""
    install(asyncio.get_running_loop())
    work = work if work is not None else ThreadWork()
    token = _current.set(work)
    try:
        yield work
    finally:
        _current.reset(token)
