"""The event loop indexing consumers run their handlers on.

Service clients (graph, vector store, HTTP and LLM clients, asyncio locks) bind to the loop
that first uses them. Every indexing consumer in the process therefore runs its handler tasks
on one shared worker loop: a loop per consumer would put the same clients on two loops. Broker
I/O stays on the main loop.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

_START_TIMEOUT_S = 60.0


class WorkerLoop:
    def __init__(self, logger: logging.Logger, *, thread_name: str = "indexing-worker") -> None:
        super().__init__()
        self._logger = logger
        self._thread_name = thread_name
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Set by stop() until its thread exits: no second loop may start beside a dying one.
        self._stopping = False

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def start(self) -> asyncio.AbstractEventLoop:
        """Start the loop on its own thread unless it is already running; return it."""
        with self._lock:
            if self._stopping and self._thread is not None and self._thread.is_alive():
                # A second loop beside one still shutting down would split the shared clients.
                raise RuntimeError("the previous shared worker event loop is still shutting down")
            if not self._stopping and self._loop is not None and self._loop.is_running():
                return self._loop
            self._stopping = False
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                try:
                    loop.run_forever()
                finally:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.close()
                    self._logger.info("Shared worker event loop closed")

            thread = threading.Thread(target=run, name=self._thread_name, daemon=True)
            thread.start()
            if not ready.wait(_START_TIMEOUT_S):
                raise RuntimeError("the shared worker event loop did not start")
            self._loop, self._thread = loop, thread
            self._logger.info("Shared worker event loop started")
            return loop

    def call(self, fn: Callable[[], T], timeout: float = _START_TIMEOUT_S) -> T:
        """Run ``fn`` on the loop's thread and return its result, for setup that must run there."""
        loop, thread = self._loop, self._thread
        if loop is None or not loop.is_running() or self._stopping:
            raise RuntimeError("the shared worker event loop is not running")
        if threading.current_thread() is thread:
            return fn()
        future: concurrent.futures.Future[T] = concurrent.futures.Future()

        def invoke() -> None:
            try:
                future.set_result(fn())
            except BaseException as exc:
                future.set_exception(exc)

        loop.call_soon_threadsafe(invoke)
        return future.result(timeout=timeout)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float = _START_TIMEOUT_S) -> T:
        """Run ``coro`` on the loop and wait for its result, from any thread but the loop's."""
        loop, thread = self._loop, self._thread
        if loop is None or not loop.is_running() or self._stopping:
            coro.close()
            raise RuntimeError("the shared worker event loop is not running")
        if threading.current_thread() is thread:
            coro.close()
            raise RuntimeError("WorkerLoop.run would block its own loop; await the coroutine there")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise

    def stop(self, timeout: float = 30.0) -> None:
        """Cancel what still runs on the loop, close it, and wait for its thread.

        Call only after every consumer using the loop has stopped, and never from the loop's
        own thread.
        """
        with self._lock:
            loop, thread = self._loop, self._thread
            if loop is None or thread is None:
                return
            # Only the first stop() stops the loop: stopping it again would cut short the
            # thread's cleanup of what it cancelled, leaving the loop unclosed.
            first = not self._stopping
            self._stopping = True
        if first and loop.is_running():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        if thread.is_alive():
            # Kept, not forgotten: until this thread exits no second loop may start beside it.
            self._logger.warning(
                "Shared worker event loop did not stop within %.0fs; it cannot restart until it does",
                timeout,
            )
            return
        with self._lock:
            if self._thread is thread:
                self._loop = self._thread = None
                self._stopping = False

    async def aclose(self, timeout: float = 30.0) -> None:
        """``stop`` without blocking the calling loop."""
        await asyncio.get_running_loop().run_in_executor(None, self.stop, timeout)
