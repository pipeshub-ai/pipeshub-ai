"""One request at a time on a googleapiclient service.

A built service shares one ``httplib2.Http``, which is not thread-safe, and the agent runs a
turn's tool calls concurrently. Same scheme as ``GoogleDriveDataSource.execute``.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")


class SerializedExecute:
    def __init__(self) -> None:
        self._execute_lock = asyncio.Lock()
        # Cancelling the awaiting task frees the asyncio lock but not the worker thread,
        # so the worker also holds this one until its request returns.
        self._transport_lock = threading.Lock()

    def _run_exclusively(self, operation: Callable[[], T]) -> T:
        with self._transport_lock:
            return operation()

    async def run(self, operation: Callable[[], T]) -> T:
        """Run a blocking call off the event loop, never overlapping another on this service."""
        async with self._execute_lock:
            return await asyncio.to_thread(self._run_exclusively, operation)
