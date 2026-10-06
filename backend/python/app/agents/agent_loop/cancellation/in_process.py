"""`InProcessRunCancellationRegistry`: dict-backed `RunCancellationRegistry`
for a single worker. Sufficient whenever every request that could cancel
a run lands on the same process running it (`QUERY_UVICORN_WORKERS=1`, no
Helm replicas) — `KVBackedRunCancellationRegistry` composes this for the
same-process fast path and adds cross-process fan-out on top.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.agents.agent_loop.cancellation.policy import CancelRequester, may_cancel

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import CancellationToken
    from app.agents.agent_loop.cancellation.registry import CancelOutcome, RunOwner

__all__ = ["InProcessRunCancellationRegistry"]

logger = logging.getLogger(__name__)


class InProcessRunCancellationRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, tuple["CancellationToken", RunOwner]] = {}
        # Guards the dict against concurrent register()/cancel()/unregister()
        # calls from different requests' event-loop tasks — no actual
        # blocking I/O happens under it, so contention is a non-issue.
        self._lock = asyncio.Lock()

    async def is_active(self, run_id: str) -> bool:
        return run_id in self._entries

    async def register(self, run_id: str, token: "CancellationToken", owner: RunOwner) -> None:
        async with self._lock:
            # Last-write-wins by design (see test_in_process.py) — the route
            # layer's is_active() 409 check is what's actually meant to stop
            # this in practice. A live entry still being here means that
            # check raced (or was skipped, e.g. an older Node build not
            # sending runId), so the FIRST run's token becomes uncancellable
            # through this run_id. Loud because it's otherwise a silent
            # "stop did nothing" report from a user.
            if run_id in self._entries:
                logger.warning(
                    "InProcessRunCancellationRegistry.register: run_id=%s is already "
                    "registered and still active; overwriting — the prior run's "
                    "token is now unreachable via this run_id", run_id,
                )
            self._entries[run_id] = (token, owner)

    async def cancel(self, run_id: str, requester: CancelRequester) -> CancelOutcome:
        async with self._lock:
            entry = self._entries.get(run_id)
        if entry is None:
            return "not_found"
        token, owner = entry
        if not may_cancel(owner, requester):
            return "forbidden"
        token.cancel()
        return "cancelled"

    async def unregister(self, run_id: str) -> None:
        async with self._lock:
            self._entries.pop(run_id, None)
