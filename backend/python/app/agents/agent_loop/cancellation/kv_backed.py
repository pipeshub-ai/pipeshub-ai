"""`KVBackedRunCancellationRegistry`: cross-process cancellation fan-out
over a `KeyValueStore` (Redis or etcd — see `factory.py`), for a
multi-worker/multi-replica query service where a cancel POST can land on
a process that doesn't own the run.

Composes `InProcessRunCancellationRegistry` for the common same-process
case (the owning worker's own `cancel()` call sets the token directly, no
KV round trip) and layers cross-process delivery on top: a `cancel()` that
misses locally publishes the requester's identity to
`chat:run:{run_id}:cancel`; `register()`'s per-run watcher task polls that
key on the OWNING worker and, once it appears, re-runs it through the
SAME `InProcessRunCancellationRegistry.cancel()` owner check before
touching the local token — a non-owning worker has no `RunOwner` to
validate against, so publishing must never be mistaken for an
authoritative decision; only the owning worker's watcher can make one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)
from app.agents.agent_loop.cancellation.registry import CancelOutcome, RunOwner

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import CancellationToken
    from app.config.key_value_store import KeyValueStore

__all__ = ["KVBackedRunCancellationRegistry"]

_KEY_PREFIX = "chat:run:"
_KEY_SUFFIX = ":cancel"
# Bounds a leaked key if a producer's `finally` never runs (process killed
# mid-run) — not the normal cleanup path, `unregister()` deletes it.
_TTL_SECONDS = 600
_POLL_INTERVAL_SECONDS = 1.0


def _key(run_id: str) -> str:
    return f"{_KEY_PREFIX}{run_id}{_KEY_SUFFIX}"


class KVBackedRunCancellationRegistry:
    def __init__(self, store: "KeyValueStore[str]", logger_: logging.Logger | None = None) -> None:
        self._store = store
        self._local = InProcessRunCancellationRegistry()
        self._watchers: dict[str, asyncio.Task] = {}
        self._log = logger_ or logging.getLogger(__name__)

    async def is_active(self, run_id: str) -> bool:
        return await self._local.is_active(run_id)

    async def register(self, run_id: str, token: "CancellationToken", owner: RunOwner) -> None:
        await self._local.register(run_id, token, owner)
        self._watchers[run_id] = asyncio.create_task(self._watch(run_id, token))

    async def cancel(self, run_id: str, requester: RunOwner) -> CancelOutcome:
        outcome = await self._local.cancel(run_id, requester)
        if outcome != "not_found":
            # This worker owns the run — "cancelled"/"forbidden" are both
            # authoritative; no cross-process write needed either way.
            return outcome
        try:
            await self._store.create_key(
                _key(run_id), requester.model_dump_json(), overwrite=True, ttl=_TTL_SECONDS,
            )
        except Exception:
            self._log.warning(
                "KVBackedRunCancellationRegistry: failed to publish cancel for run_id=%s",
                run_id, exc_info=True,
            )
            return "not_found"
        # "Delivered", not "confirmed" — the owning worker's watcher (if any
        # actually owns this run_id) re-validates `requester` itself before
        # touching its local token. A run_id nobody owns anywhere leaves an
        # inert key that expires via TTL.
        return "cancelled"

    async def unregister(self, run_id: str) -> None:
        await self._local.unregister(run_id)
        watcher = self._watchers.pop(run_id, None)
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        try:
            await self._store.delete_key(_key(run_id))
        except Exception:
            self._log.debug(
                "KVBackedRunCancellationRegistry: failed to delete cancel key for run_id=%s",
                run_id, exc_info=True,
            )

    async def _watch(self, run_id: str, token: "CancellationToken") -> None:
        try:
            while not token.is_cancelled:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                try:
                    raw = await self._store.get_key(_key(run_id))
                except Exception:
                    self._log.debug(
                        "KVBackedRunCancellationRegistry: poll failed for run_id=%s",
                        run_id, exc_info=True,
                    )
                    continue
                if not raw:
                    continue
                try:
                    requester = RunOwner.model_validate_json(raw)
                except (ValidationError, ValueError):
                    self._log.warning(
                        "KVBackedRunCancellationRegistry: malformed cancel payload for run_id=%s",
                        run_id,
                    )
                    continue
                outcome = await self._local.cancel(run_id, requester)
                if outcome == "cancelled":
                    return
                if outcome == "forbidden":
                    # Keep watching — a mismatched requester published now
                    # does not preclude the RIGHT requester publishing a
                    # later cancel to the same key (`overwrite=True`), which
                    # a later poll tick would then see and accept.
                    self._log.warning(
                        "KVBackedRunCancellationRegistry: rejected cross-process cancel for "
                        "run_id=%s (requester org/user mismatch)", run_id,
                    )
                    continue
                # "not_found": the local entry is already gone — unregister()
                # raced this poll tick, or the run ended between the KV read
                # and this check. Nothing left to watch for.
                return
        except asyncio.CancelledError:
            pass
