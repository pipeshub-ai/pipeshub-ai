"""Bounded thread-pool fan-out with a circuit breaker.

The work is I/O-bound and every reused helper is synchronous `requests`, so
threads (not asyncio) are the simplest correct model. Workers must return a
result for per-item failures; an exception escaping a worker is a bug and
aborts the stage.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TypeVar

from benchmarks.frames.errors import CircuitOpenError

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class CircuitBreaker:
    def __init__(self, max_error_rate: float, min_items: int) -> None:
        self._max_error_rate = max_error_rate
        self._min_items = min_items
        self._total = 0
        self._failed = 0
        self._lock = threading.Lock()

    @property
    def failed(self) -> int:
        return self._failed

    def record(self, ok: bool) -> None:
        with self._lock:
            self._total += 1
            self._failed += 0 if ok else 1
            if self._total >= self._min_items and self._failed / self._total > self._max_error_rate:
                raise CircuitOpenError(
                    f"{self._failed}/{self._total} items failed "
                    f"(limit {self._max_error_rate:.0%}); aborting stage",
                )


def run_parallel(
    items: Iterable[T],
    worker: Callable[[T], R],
    *,
    workers: int,
    on_result: Callable[[T, R], None],
    is_failure: Callable[[R], bool] = lambda _result: False,
    breaker: CircuitBreaker | None = None,
) -> int:
    """Run `worker` over `items`; return how many completed."""
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures: dict[Future[R], T] = {pool.submit(worker, item): item for item in items}
        try:
            # Handled as they finish, not once the whole batch is done: results
            # are persisted (and the breaker fires) while the stage runs, so a
            # long stage shows progress and a crash loses only what is in flight.
            for future in as_completed(futures):
                result = future.result()
                on_result(futures[future], result)
                completed += 1
                if breaker is not None:
                    breaker.record(not is_failure(result))
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    return completed
