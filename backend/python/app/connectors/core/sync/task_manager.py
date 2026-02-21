"""
Sync Task Manager
Centralized manager for asyncio tasks running connector sync processes.
Ensures at most one sync task per connector is active at any time.
"""

import asyncio
import logging
from typing import Coroutine, Dict


class SyncTaskManager:
    """
    Manages asyncio tasks for connector sync processes.

    Guarantees:
    - At most one sync task per connector_id at a time.
    - Starting a sync when one is already running cancels the old one first.
    - Tasks are automatically removed from the registry when they finish.
    - All running tasks can be cancelled at shutdown via cancel_all().
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._tasks: Dict[str, asyncio.Task] = {}

    async def start_sync(self, connector_id: str, coro: Coroutine) -> asyncio.Task:
        """
        Start a sync task for the given connector.

        If a task is already running for this connector_id, it is cancelled
        and awaited before the new task is created.

        Args:
            connector_id: Unique identifier for the connector instance.
            coro: The coroutine to run (e.g. connector.run_sync()).

        Returns:
            The newly created asyncio.Task.
        """
        # Cancel any existing task for this connector
        await self.cancel_sync(connector_id)

        # Create and register the new task
        task = asyncio.create_task(coro, name=f"sync_{connector_id}")
        self._tasks[connector_id] = task

        # Auto-remove the task from the registry once it finishes
        task.add_done_callback(lambda t: self._on_task_done(connector_id, t))

        self.logger.info(f"Sync task started for connector {connector_id}")
        return task

    async def cancel_sync(self, connector_id: str) -> None:
        """
        Cancel and await the sync task for the given connector, if one is running.

        Args:
            connector_id: Unique identifier for the connector instance.
        """
        task = self._tasks.get(connector_id)
        if task is None or task.done():
            return

        self.logger.info(f"Cancelling existing sync task for connector {connector_id}")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            # CancelledError is expected; any other exception was already
            # handled inside the task itself.
            pass

        self.logger.info(f"Sync task cancelled for connector {connector_id}")

    async def cancel_all(self) -> None:
        """
        Cancel all running sync tasks. Intended to be called at application shutdown.
        """
        connector_ids = list(self._tasks.keys())
        if not connector_ids:
            return

        self.logger.info(f"Cancelling all sync tasks ({len(connector_ids)} connectors): {connector_ids}")

        await asyncio.gather(
            *(self.cancel_sync(cid) for cid in connector_ids),
            return_exceptions=True
        )

        self.logger.info("All sync tasks cancelled")

    def is_running(self, connector_id: str) -> bool:
        """
        Return True if a sync task is currently active for the given connector.

        Args:
            connector_id: Unique identifier for the connector instance.
        """
        task = self._tasks.get(connector_id)
        return task is not None and not task.done()

    def _on_task_done(self, connector_id: str, task: asyncio.Task) -> None:
        """
        Callback invoked automatically when a task finishes (completed, cancelled,
        or raised an exception). Removes the task from the internal registry and
        logs any unexpected exception.
        """
        # Remove from registry only if this is still the registered task
        # (a newer task may have already replaced it)
        if self._tasks.get(connector_id) is task:
            del self._tasks[connector_id]

        if task.cancelled():
            self.logger.debug(f"Sync task for connector {connector_id} was cancelled")
        elif task.exception():
            self.logger.error(
                f"Sync task for connector {connector_id} raised an exception",
                exc_info=task.exception(),
            )
        else:
            self.logger.info(f"Sync task for connector {connector_id} completed successfully")


# Module-level singleton — import and use this everywhere
sync_task_manager = SyncTaskManager()

