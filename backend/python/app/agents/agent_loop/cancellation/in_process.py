"""`InProcessRunCancellationRegistry`: dict-backed `RunCancellationRegistry`
for a single worker. Sufficient whenever every request that could cancel
a run lands on the same process running it (`QUERY_UVICORN_WORKERS=1`, no
Helm replicas) — `KVBackedRunCancellationRegistry` composes this for the
same-process fast path and adds cross-process fan-out on top.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import CancellationToken
    from app.agents.agent_loop.cancellation.registry import CancelOutcome, RunOwner

__all__ = ["InProcessRunCancellationRegistry"]


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
            self._entries[run_id] = (token, owner)

    async def cancel(self, run_id: str, requester: RunOwner) -> CancelOutcome:
        async with self._lock:
            entry = self._entries.get(run_id)
        if entry is None:
            return "not_found"
        token, owner = entry
        if owner.user_id != requester.user_id or owner.org_id != requester.org_id:
            return "forbidden"
        token.cancel()
        return "cancelled"

    async def unregister(self, run_id: str) -> None:
        async with self._lock:
            self._entries.pop(run_id, None)
