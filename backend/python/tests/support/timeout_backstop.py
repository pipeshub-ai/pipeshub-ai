"""A hard deadline behind pytest-timeout's signal method (TESTINFRA).

The signal method raises the timeout's failure once, from SIGALRM, in whatever Python code the main thread is running
at that moment. In an async test that is often not the test: a background task's step (a poll loop, a lease renewer,
an engine tick), and an asyncio task keeps any exception raised in its step as its own result. The test then awaits on,
the one-shot alarm is spent, and nothing ends it: an xdist worker hung ~48 min in the SYNCMAIN gate that way. A main
thread blocked in native code never runs the handler at all.

So every signal-method timeout gets a thread backstop ``GRACE_S`` later: if the test is still running then, every
thread's stack goes to the process's stderr and the process exits, as pytest-timeout's thread method does; xdist
reports a crashed worker naming the test, and replaces it.

Either way out is ``os._exit``, which skips ``atexit``: the process's exit handlers (``docker_session.at_exit``: the
Docker objects it made) run first. pytest-timeout's own ``thread`` method is set here for the same reason (SBXLEAK:
the G4 and P5.9a lanes' sandboxes and networks outlived their timed-out tests).
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
from typing import Any

import pytest
import pytest_timeout

GRACE_S = float(os.environ.get("PXH_TIMEOUT_GRACE_S", "15"))
_TIMER = pytest.StashKey[threading.Timer]()


def _hard_stop(item: pytest.Item, settings: Any) -> None:  # noqa: ANN401 - pytest-timeout's Settings tuple
    if not settings.disable_debugger_detection and pytest_timeout.is_debugging():
        return
    # Off pytest's fd capture, onto the process's own stderr: an xdist worker's captured output dies with it.
    capman = item.config.pluginmanager.getplugin("capturemanager")
    if capman is not None:
        capman.suspend_global_capture(in_=True)
    err = sys.__stderr__
    try:
        err.write(
            f"\nTimeout: {item.nodeid} is still running {GRACE_S:g} s after its {settings.timeout:g} s timeout "
            "fired: the timeout's failure did not end it (an asyncio task kept it, or the main thread is blocked in "
            "native code). Every thread's stack follows; this test process ends.\n"
        )
        err.flush()
        faulthandler.dump_traceback(file=err, all_threads=True)
        err.flush()
    finally:
        _run_exit_handlers()
        os._exit(1)


def _run_exit_handlers() -> None:
    """Never raises: the ``os._exit`` after it must happen whatever it meets."""
    try:
        from tests.support import docker_session

        docker_session.run_exit_handlers()
    except Exception:
        pass


def _thread_timeout(item: pytest.Item, settings: Any) -> None:  # noqa: ANN401 - pytest-timeout's Settings tuple
    """pytest-timeout's ``thread`` method (stacks, then ``os._exit``), the exit handlers first."""
    if not settings.disable_debugger_detection and pytest_timeout.is_debugging():
        return
    _run_exit_handlers()
    pytest_timeout.timeout_timer(item, settings)


class TimeoutBackstop:
    """Registered by ``tests/conftest.py``. For the signal method it returns None, so pytest-timeout still sets and
    cancels its own timer; the thread method's timer is set here (pytest-timeout's cancel hook cancels it)."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_timeout_set_timer(self, item: pytest.Item, settings: Any) -> bool | None:  # noqa: ANN401
        if settings.method != "signal" or threading.current_thread() is not threading.main_thread():
            # pytest-timeout's thread method, as it sets it, ending in ``_thread_timeout``.
            thread_timer = threading.Timer(settings.timeout, _thread_timeout, (item, settings))
            thread_timer.name = f"pytest_timeout {item.nodeid}"

            def cancel() -> None:
                thread_timer.cancel()
                thread_timer.join()

            item.cancel_timeout = cancel  # type: ignore[attr-defined]
            thread_timer.start()
            return True
        timer = threading.Timer(settings.timeout + GRACE_S, _hard_stop, (item, settings))
        timer.name = f"timeout backstop {item.nodeid}"
        timer.daemon = True
        item.stash[_TIMER] = timer
        timer.start()
        return None

    @pytest.hookimpl(tryfirst=True)
    def pytest_timeout_cancel_timer(self, item: pytest.Item) -> None:
        timer = item.stash.get(_TIMER, None)
        if timer is not None:
            del item.stash[_TIMER]
            timer.cancel()
