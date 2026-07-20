"""
Connector Task Manager
Centralized manager for asyncio tasks running connector background processes.
Ensures at most one task per key is active at any time.
"""

import asyncio
import logging
from typing import Coroutine, Dict, Optional

from app.utils.request_context import (
    get_context,
    new_system_root,
    reset_context,
    set_context,
)


class SyncTaskManager:
    """
    Manages asyncio tasks for connector background processes, keyed by an
    arbitrary string (a connector id for syncs, a scoped request key for reindex).

    Guarantees:
    - At most one task per key at a time.
    - start_sync() cancels and awaits an existing task for the key first;
      start_if_idle() leaves it alone and declines instead.
    - Tasks are automatically removed from the registry when they finish.
    - All running tasks can be cancelled at shutdown via cancel_all().

    Instances hold strong references to their tasks in `_tasks`; the event loop
    keeps only weak ones, so an unreferenced task can be collected mid-flight.
    """

    def __init__(self, label: str = "Sync") -> None:
        self.logger = logging.getLogger(__name__)
        self._tasks: Dict[str, asyncio.Task] = {}
        self._label = label

    async def start_sync(self, key: str, coro: Coroutine) -> asyncio.Task:
        """
        Start a task for the given key.

        If a task is already running for this key, it is cancelled and awaited
        before the new task is created.

        Args:
            key: Unique identifier for the task (e.g. a connector id).
            coro: The coroutine to run (e.g. connector.run_sync()).

        Returns:
            The newly created asyncio.Task.
        """
        await self.cancel_sync(key)
        return self._spawn(key, coro)

    async def start_if_idle(self, key: str, coro: Coroutine) -> Optional[asyncio.Task]:
        """
        Start a task for the given key only if none is currently running for it.

        Unlike start_sync(), an in-flight task is left untouched and the new
        request is declined — used for reindex, where a redelivered Kafka event
        must not restart work that is already progressing.

        Returns the new task, or None if one was already live.
        """
        # No await between the check and the spawn, so this is atomic under a
        # single-threaded event loop.
        if self.is_running(key):
            # The caller already built the coroutine; closing it avoids
            # "coroutine was never awaited".
            coro.close()
            self.logger.info(
                f"{self._label} task already running for {key} - ignoring duplicate request"
            )
            return None
        return self._spawn(key, coro)

    def _spawn(self, key: str, coro: Coroutine) -> asyncio.Task:
        # Inherit the triggering request's context (create_task copies it); only
        # a pure background trigger with no context mints a fresh root.
        async def _traced() -> None:
            if get_context() is not None:
                await coro
                return
            token = set_context(new_system_root())
            try:
                await coro
            finally:
                reset_context(token)

        task = asyncio.create_task(_traced(), name=f"{self._label.lower()}_{key}")
        self._tasks[key] = task

        # Auto-remove the task from the registry once it finishes
        task.add_done_callback(lambda t: self._on_task_done(key, t))

        self.logger.info(f"{self._label} task started for {key}")
        return task

    async def cancel_sync(self, key: str) -> None:
        """
        Cancel and await the task for the given key, if one is running.

        Args:
            key: Unique identifier for the task.
        """
        task = self._tasks.get(key)
        if task is None or task.done():
            return

        self.logger.info(f"Cancelling existing {self._label.lower()} task for {key}")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            # CancelledError is expected; any other exception was already
            # handled inside the task itself.
            pass

        self.logger.info(f"{self._label} task cancelled for {key}")

    async def cancel_by_prefix(self, prefix: str) -> None:
        """
        Cancel every task whose key starts with `prefix`.

        Reindex keys are scoped per request (connector, target, filters), so a
        connector-wide cancel has to match on the connector id prefix rather
        than an exact key.
        """
        keys = [k for k in self._tasks if k.startswith(prefix)]
        if not keys:
            return

        self.logger.info(
            f"Cancelling {len(keys)} {self._label.lower()} task(s) matching prefix {prefix}"
        )
        await asyncio.gather(
            *(self.cancel_sync(k) for k in keys),
            return_exceptions=True,
        )

    async def cancel_all(self) -> None:
        """
        Cancel all running tasks. Intended to be called at application shutdown.
        """
        keys = list(self._tasks.keys())
        if not keys:
            return

        self.logger.info(f"Cancelling all {self._label.lower()} tasks ({len(keys)}): {keys}")

        await asyncio.gather(
            *(self.cancel_sync(k) for k in keys),
            return_exceptions=True
        )

        self.logger.info(f"All {self._label.lower()} tasks cancelled")

    def is_running(self, key: str) -> bool:
        """
        Return True if a task is currently active for the given key.

        Args:
            key: Unique identifier for the task.
        """
        task = self._tasks.get(key)
        return task is not None and not task.done()

    def _on_task_done(self, key: str, task: asyncio.Task) -> None:
        """
        Callback invoked automatically when a task finishes (completed, cancelled,
        or raised an exception). Removes the task from the internal registry and
        logs any unexpected exception.
        """
        # Remove from registry only if this is still the registered task
        # (a newer task may have already replaced it)
        if self._tasks.get(key) is task:
            del self._tasks[key]

        if task.cancelled():
            self.logger.debug(f"{self._label} task for {key} was cancelled")
        elif task.exception():
            self.logger.error(
                f"{self._label} task for {key} raised an exception",
                exc_info=task.exception(),
            )
        else:
            self.logger.info(f"{self._label} task for {key} completed successfully")


# Module-level singletons — import and use these everywhere.
# Separate registries: reindex must never cancel an in-flight sync.
sync_task_manager = SyncTaskManager(label="Sync")
reindex_task_manager = SyncTaskManager(label="Reindex")
