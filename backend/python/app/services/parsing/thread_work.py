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
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar
from typing import Any

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
    """Make the loop's default executor the tracking one (once per loop)."""
    if getattr(loop, _INSTALLED, False):
        return
    loop.set_default_executor(ThreadWorkExecutor(max_workers=min(32, (os.cpu_count() or 1) + 4)))
    setattr(loop, _INSTALLED, True)


@contextlib.contextmanager
def track() -> Iterator[ThreadWork]:
    """Collect the executor jobs submitted inside the block."""
    install(asyncio.get_running_loop())
    work = ThreadWork()
    token = _current.set(work)
    try:
        yield work
    finally:
        _current.reset(token)
