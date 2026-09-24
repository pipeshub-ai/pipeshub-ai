"""Process-wide thread pool shared by every connector, with per-connector caps.

Connectors run blocking vendor SDK calls (googleapiclient, python-gitlab) off the
event loop. Giving each connector its own ``ThreadPoolExecutor`` made the total
thread count scale with the number of connector instances and never come back
down: CPython pool workers park on ``work_queue.get(block=True)`` forever and are
only ever ended by ``shutdown()``, so a connector's *peak* concurrency became its
*permanent* thread count.

Here one bounded pool is shared by everyone, and each connector holds a
``ThreadPoolLease`` capping how much of it that connector may occupy at once. A
lease borrows a thread only for the duration of one call, so idle capacity is
reclaimed for other connectors, and a connector dropped without ``cleanup()``
leaks a small object rather than parked OS threads.

Two constraints shape the implementation:

- The lease is a ``concurrent.futures.Executor`` so ``loop.run_in_executor(lease, ...)``
  works unchanged — that call is just ``wrap_future(executor.submit(...))``.
- The cap is enforced with a ``threading.Lock``, never an ``asyncio.Semaphore``.
  This process runs several event loops (see ``app/utils/concurrency.py``), and a
  semaphore binds to whichever one first awaits it; more decisively, the dispatch
  pump re-enters ``submit()`` from pool worker threads, where no loop is running.
"""

from __future__ import annotations

import asyncio
import functools
import os
import threading
from collections import deque
from concurrent.futures import (
    CancelledError,
    Executor,
    Future,
    InvalidStateError,
    ThreadPoolExecutor,
)
from typing import TYPE_CHECKING, Any, TypeVar, override

from app.utils.logger import create_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = create_logger("connector_service")

T = TypeVar("T")

# Maximum concurrent blocking connector calls process-wide. Override with
# CONNECTOR_THREAD_POOL_MAX_WORKERS; defaults to 4 to bound startup bursts.
CONNECTOR_THREAD_POOL_MAX_WORKERS = max(
    1, int(os.getenv("CONNECTOR_THREAD_POOL_MAX_WORKERS", "4"))
)

# Wall-clock budget for draining one lease during connector cleanup. A worker
# cannot be interrupted, so on expiry we stop waiting and let it unwind on its
# own; cleanup() returning therefore does not imply quiescence.
CONNECTOR_LEASE_DRAIN_TIMEOUT_SECONDS = max(
    0.0, float(os.getenv("CONNECTOR_LEASE_DRAIN_TIMEOUT_SECONDS", "30"))
)

# Trampoline state for lease dispatch. add_done_callback fires inline when the
# future is already done, so a run of fast calls would otherwise recurse
# _finish -> _start -> submit_raw -> callback -> _finish without bound.
_dispatch = threading.local()


class _QueuedCall:
    """One submitted callable and the future handed back to the caller."""

    __slots__ = ("args", "fn", "future", "kwargs", "pool_future")

    def __init__(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.future: Future[Any] = Future()
        self.pool_future: Future[Any] | None = None


class ThreadPoolLease(Executor):
    """A connector's capped share of the shared pool.

    ``submit()`` never blocks: work beyond ``max_concurrency`` queues here and is
    dispatched as earlier calls finish, so one connector cannot occupy more than
    its share of the pool no matter how wide it fans out.
    """

    def __init__(
        self,
        pool: SharedConnectorThreadPool,
        max_concurrency: int,
        label: str,
    ) -> None:
        self._pool = pool
        self._max = max(1, max_concurrency)
        self._label = label
        self._lock = threading.Lock()
        self._queue: deque[_QueuedCall] = deque()
        self._active: set[_QueuedCall] = set()
        self._closed = False

    @property
    def label(self) -> str:
        return self._label

    @property
    def max_concurrency(self) -> int:
        return self._max

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def inflight(self) -> int:
        """Calls dispatched to the pool and not yet finished."""
        with self._lock:
            return len(self._active)

    @property
    def queued(self) -> int:
        """Calls held back by the cap — this lease's share of the backpressure."""
        with self._lock:
            return len(self._queue)

    @override
    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        call = _QueuedCall(fn, args, kwargs)
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    f"connector thread lease {self._label!r} is closed"
                )
            start = len(self._active) < self._max
            if start:
                self._active.add(call)
            else:
                self._queue.append(call)
            active_count = len(self._active)
            queued_count = len(self._queue)
        if start:
            logger.debug(
                "Lease %s: acquired slot (active=%d/%d)",
                self._label, active_count, self._max,
            )
            self._start(call)
        else:
            logger.debug(
                "Lease %s: at capacity (%d/%d active), call queued (queue depth=%d)",
                self._label, self._max, self._max, queued_count,
            )
        return call.future

    @override
    def shutdown(
        self,
        wait: bool = True,
        *,
        cancel_futures: bool = False,
    ) -> None:
        """Close this lease, leaving the shared pool running.

        Overridden so the inherited ``Executor.__exit__`` cannot reach the shared
        pool. ``wait`` is not honoured — blocking a caller here could be the event
        loop thread; use ``shutdown_and_drain`` when in-flight work must be awaited.
        """
        self._close()

    async def shutdown_and_drain(self, timeout: float | None = None) -> None:
        """Cancel queued work, await in-flight work, and leave the pool alone."""
        active = self._close()
        outstanding = [call.future for call in active]
        if not outstanding:
            logger.debug(
                "Lease %s: closed with no in-flight calls to drain", self._label
            )
            return
        budget = (
            CONNECTOR_LEASE_DRAIN_TIMEOUT_SECONDS if timeout is None else timeout
        )
        logger.debug(
            "Lease %s: draining %d in-flight call(s) (timeout=%.0fs)",
            self._label, len(outstanding), budget,
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(asyncio.wrap_future(f) for f in outstanding),
                    return_exceptions=True,
                ),
                timeout=budget,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "Connector thread lease %s: %d call(s) still in flight after %.0fs; "
                "abandoning them (they keep their pool slot until they unwind)",
                self._label,
                len(outstanding),
                budget,
            )

    def _close(self) -> list[_QueuedCall]:
        """Mark closed, drop queued work, and return the calls still in flight."""
        with self._lock:
            self._closed = True
            queued = list(self._queue)
            self._queue.clear()
            active = list(self._active)
        logger.debug(
            "Lease %s: closing (cancelling %d queued call(s), %d still in flight)",
            self._label, len(queued), len(active),
        )
        for call in queued:
            call.future.cancel()
            call.future.set_running_or_notify_cancel()
        for call in active:
            # Succeeds only for calls the pool has not picked up yet.
            if call.pool_future is not None:
                call.pool_future.cancel()
        return active

    def _start(self, call: _QueuedCall) -> None:
        pending: deque[tuple[ThreadPoolLease, _QueuedCall]] | None = getattr(
            _dispatch, "pending", None
        )
        if pending is not None:
            pending.append((self, call))
            return
        pending = deque()
        _dispatch.pending = pending
        try:
            self._start_now(call)
            while pending:
                lease, queued_call = pending.popleft()
                lease._start_now(queued_call)
        finally:
            _dispatch.pending = None

    def _start_now(self, call: _QueuedCall) -> None:
        if not call.future.set_running_or_notify_cancel():
            logger.debug(
                "Lease %s: call was cancelled before dispatch, skipping pool submit",
                self._label,
            )
            self._finish(call)
            return
        try:
            logger.debug(
                "Lease %s: handing call to the shared pool", self._label
            )
            call.pool_future = self._pool.submit_raw(
                call.fn, *call.args, **call.kwargs
            )
        except Exception as exc:
            logger.exception(
                "Lease %s: shared pool rejected call", self._label
            )
            call.future.set_exception(exc)
            self._finish(call)
            return
        call.pool_future.add_done_callback(
            functools.partial(self._on_pool_done, call)
        )

    def _on_pool_done(self, call: _QueuedCall, pool_future: Future[Any]) -> None:
        logger.debug(
            "Lease %s: call finished on pool worker %s",
            self._label, threading.current_thread().name,
        )
        try:
            if pool_future.cancelled():
                call.future.set_exception(CancelledError())
            else:
                exc = pool_future.exception()
                if exc is not None:
                    call.future.set_exception(exc)
                else:
                    call.future.set_result(pool_future.result())
        except InvalidStateError:
            pass
        finally:
            # Released after the result is set, so the awaiting coroutine is woken
            # before another call can take the slot.
            self._finish(call)

    def _finish(self, call: _QueuedCall) -> None:
        with self._lock:
            self._active.discard(call)
            successor: _QueuedCall | None = None
            while self._queue:
                candidate = self._queue.popleft()
                if candidate.future.cancelled():
                    continue
                successor = candidate
                self._active.add(candidate)
                break
            active_count = len(self._active)
            queued_count = len(self._queue)
        # Handing the slot straight to the queue head, rather than releasing it and
        # letting a racing submit() win, is what keeps this lease FIFO-fair.
        if successor is not None:
            logger.debug(
                "Lease %s: slot freed, handing it to next queued call "
                "(active=%d/%d, queue depth=%d)",
                self._label, active_count, self._max, queued_count,
            )
            self._start(successor)
        else:
            logger.debug(
                "Lease %s: slot freed, nothing queued (active=%d/%d)",
                self._label, active_count, self._max,
            )


class SharedConnectorThreadPool:
    """The one pool. Only process shutdown may shut it down."""

    def __init__(
        self,
        max_workers: int = CONNECTOR_THREAD_POOL_MAX_WORKERS,
        thread_name_prefix: str = "conn-pool",
    ) -> None:
        self._max_workers = max(1, max_workers)
        self._tpe = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        # ThreadPoolExecutor exposes no "workers busy right now" query, so this
        # tracks it ourselves purely to log contention -- best-effort, not used
        # for any scheduling decision.
        self._busy_lock = threading.Lock()
        self._busy = 0

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def lease(
        self,
        *,
        max_concurrency: int,
        label: str,
    ) -> ThreadPoolLease:
        return ThreadPoolLease(self, max_concurrency, label)

    def submit_raw(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,  # noqa: ANN401 - forwards an arbitrary connector call
        **kwargs: Any,  # noqa: ANN401 - forwards an arbitrary connector call
    ) -> Future[Any]:
        """Submit straight to the pool, bypassing every cap. Leases only."""
        # A lease handing a call here does not mean an OS thread is free -- other
        # leases can fill max_workers first. Wrapping fn logs the moment a worker
        # actually picks the call up, which is the only place that wait is visible.
        with self._busy_lock:
            busy = self._busy
        if busy >= self._max_workers:
            logger.debug(
                "Shared pool: all %d worker(s) busy, call waiting for a free worker",
                self._max_workers,
            )
        return self._tpe.submit(functools.partial(self._run_on_worker, fn, args, kwargs))

    def _run_on_worker(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:  # noqa: ANN401 - forwards an arbitrary connector call's return value
        thread_name = threading.current_thread().name
        with self._busy_lock:
            self._busy += 1
            busy = self._busy
        logger.debug(
            "Shared pool: worker %s picked up call (busy=%d/%d)",
            thread_name, busy, self._max_workers,
        )
        try:
            return fn(*args, **kwargs)
        finally:
            with self._busy_lock:
                self._busy -= 1
                busy = self._busy
            logger.debug(
                "Shared pool: worker %s freed (busy=%d/%d)",
                thread_name, busy, self._max_workers,
            )

    def shutdown(self, *, wait: bool = False) -> None:
        self._tpe.shutdown(wait=wait, cancel_futures=not wait)


_shared_pool: SharedConnectorThreadPool | None = None
_shared_pool_lock = threading.Lock()


def get_shared_connector_thread_pool() -> SharedConnectorThreadPool:
    """The process-wide pool, created on first use."""
    global _shared_pool  # noqa: PLW0603 - double-checked process singleton
    pool = _shared_pool
    if pool is None:
        with _shared_pool_lock:
            pool = _shared_pool
            if pool is None:
                pool = SharedConnectorThreadPool()
                _shared_pool = pool
                logger.info(
                    "Shared connector thread pool created with max_workers=%d",
                    pool.max_workers,
                )
    return pool


def acquire_connector_lease(
    owner: object,
    max_concurrency: int,
    *,
    label: str,
) -> ThreadPoolLease:
    """Lease from the pool injected onto ``owner``, or the process-wide one.

    The isinstance check matters for tests: connectors are often mocks, and a
    MagicMock attribute would otherwise be treated as a pool and produce futures
    ``asyncio.wrap_future`` rejects.
    """
    pool = getattr(owner, "_shared_thread_pool", None)
    if not isinstance(pool, SharedConnectorThreadPool):
        pool = get_shared_connector_thread_pool()
    return pool.lease(max_concurrency=max_concurrency, label=label)
