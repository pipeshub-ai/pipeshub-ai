"""Process pools that survive worker crashes.

A ProcessPoolExecutor whose child process dies (e.g. OOM-killed) raises
BrokenProcessPool on every subsequent submit and never recovers on its own.
The indexing service holds long-lived singleton pools, so a single crash
would disable PDF processing until the service restarts. RecoverableProcessPool
discards the broken executor, creates a fresh one, and retries the failed
call once.
"""

import asyncio
import atexit
import logging
import multiprocessing
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import TypeVar

R = TypeVar("R")

logger = logging.getLogger(__name__)


class RecoverableProcessPool:
    def __init__(
        self, max_workers: int, name: str, mp_start_method: str = "spawn"
    ) -> None:
        self._max_workers = max_workers
        self._name = name
        self._mp_start_method = mp_start_method
        self._lock = threading.Lock()
        self._pool: ProcessPoolExecutor | None = None
        self._generation = 0

    def _get(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(
                    max_workers=self._max_workers,
                    mp_context=multiprocessing.get_context(self._mp_start_method),
                )
                self._generation += 1
                # Safety net for unclean exits; the owning service's lifespan
                # shutdown is the primary cleanup path.
                atexit.register(self._pool.shutdown, wait=False, cancel_futures=True)
                logger.info(
                    f"Process pool '{self._name}' executor created "
                    f"(generation={self._generation}, workers={self._max_workers})"
                )
            return self._pool

    @staticmethod
    def _task_name(fn: Callable[..., R]) -> str:
        return getattr(fn, "__qualname__", repr(fn))

    def _log_inflight_loss(self, fn: Callable[..., R]) -> None:
        logger.warning(
            f"Task '{self._task_name(fn)}' on pool '{self._name}' aborted: worker "
            "process died mid-execution (likely OOM-killed). Partial work is "
            "discarded; retrying once on a fresh executor"
        )

    def _log_abandoned(self, fn: Callable[..., R]) -> None:
        logger.error(
            f"Task '{self._task_name(fn)}' on pool '{self._name}' failed again after "
            "executor replacement; giving up. Partial work discarded — the caller's "
            "operation is left incomplete and must be retried upstream"
        )

    def _discard(self, broken: ProcessPoolExecutor) -> None:
        # Instance check so a concurrent caller that already replaced the pool
        # doesn't get its fresh pool torn down underneath it.
        with self._lock:
            replaced = self._pool is broken
            if replaced:
                self._pool = None
        if replaced:
            logger.warning(
                f"Process pool '{self._name}' (generation={self._generation}) "
                "destroyed: worker process died abruptly (likely OOM-killed or "
                "segfaulted); a fresh executor will be created on next use"
            )
        atexit.unregister(broken.shutdown)
        broken.shutdown(wait=False, cancel_futures=True)

    async def run(self, fn: Callable[..., R], *args: object) -> R:
        """Run fn(*args) in the pool, retrying once on a fresh pool if it broke."""
        loop = asyncio.get_running_loop()
        pool = self._get()
        try:
            return await loop.run_in_executor(pool, fn, *args)
        except BrokenProcessPool:
            self._log_inflight_loss(fn)
            self._discard(pool)
            pool = self._get()
            try:
                return await loop.run_in_executor(pool, fn, *args)
            except BrokenProcessPool:
                self._log_abandoned(fn)
                self._discard(pool)
                raise

    def submit_and_wait(self, fn: Callable[..., R], *args: object) -> R:
        """Blocking counterpart of run(), for callers already off the event loop."""
        pool = self._get()
        try:
            return pool.submit(fn, *args).result()
        except BrokenProcessPool:
            self._log_inflight_loss(fn)
            self._discard(pool)
            pool = self._get()
            try:
                return pool.submit(fn, *args).result()
            except BrokenProcessPool:
                self._log_abandoned(fn)
                self._discard(pool)
                raise

    def shutdown(self) -> bool:
        """Shut down the pool if it was initialised. Returns True if one existed."""
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is None:
            return False
        atexit.unregister(pool.shutdown)
        pool.shutdown(wait=False, cancel_futures=True)
        logger.info(
            f"Process pool '{self._name}' shut down "
            f"(generation={self._generation}, workers={self._max_workers})"
        )
        return True
