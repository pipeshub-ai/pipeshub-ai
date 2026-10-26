"""Housekeeping for the workers of a ``ProcessPoolExecutor``.

Pass ``start_housekeeping`` as the pool's initializer, with the creating
process's pid as its argument, and submit every task through
``run_and_release``.
"""
from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, TypeVar

from app.utils.memory_release import release_freed_memory

if TYPE_CHECKING:
    from collections.abc import Callable

_T = TypeVar("_T")
_HOUSEKEEPING_SECONDS = 2.0
# Set in a pool worker when a task ends; its housekeeping thread then trims.
_task_ended = threading.Event()


def start_housekeeping(parent_pid: int) -> None:
    """Pool initializer: a thread that keeps an idle worker from holding memory.

    glibc keeps freed heap for reuse, so a PDF raster worker held ~0.6 GB more
    than it needed after rendering 32 pages of a 300 MB PDF; it trims after
    each task. A SIGKILLed parent (OOM kill, an idle parse worker being
    stopped) never shuts the pool down, and its workers would block on their
    call queue forever, so a worker exits once it is reparented.
    """

    def housekeep() -> None:
        trims_left = 0
        while os.getppid() == parent_pid:
            time.sleep(_HOUSEKEEPING_SECONDS)
            if _task_ended.is_set():
                _task_ended.clear()
                # The pool sends the result after the task returns, so the
                # first trim can run while the worker still holds it.
                trims_left = 2
            if trims_left:
                trims_left -= 1
                release_freed_memory()
        os._exit(0)

    threading.Thread(target=housekeep, name="pool-housekeeping", daemon=True).start()


def run_and_release(fn: Callable[..., _T], *args: object) -> _T:
    try:
        return fn(*args)
    finally:
        _task_ended.set()
