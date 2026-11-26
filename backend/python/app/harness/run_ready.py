"""``RunReadySignal`` on Redis pub/sub: Node publishes on ``harness:run-ready``
after the transaction that made a run claimable commits (LAT1).

One subscription for the claimer's life, opened at the first wait, so notices
that arrive between waits are buffered on the connection and coalesce. A
notice published before that first subscription, or while Redis is away, is
lost: the claimer's poll finds the run. After a Redis error the signal reports
itself unhealthy, sleeps out the wait, and subscribes again on the next one.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.harness.interaction_wakeup import PubSub, PubSubClient

RUN_READY_CHANNEL = "harness:run-ready"


def run_ready_channel(key_namespace: str = "") -> str:
    return f"{key_namespace}:{RUN_READY_CHANNEL}" if key_namespace else RUN_READY_CHANNEL


class RedisRunReadySignal:
    def __init__(self, client: PubSubClient, *, key_namespace: str = "") -> None:
        self._client = client
        self._channel = run_ready_channel(key_namespace)
        self._pubsub: PubSub | None = None
        self._healthy = True
        self._log = HarnessLog.named("app.harness.worker")

    @property
    def healthy(self) -> bool:
        return self._healthy

    async def wait(self, timeout_s: float) -> bool:
        started = time.monotonic()
        try:
            pubsub = await self._subscribed()
            # A read that only consumed the subscribe confirmation answers None early.
            while True:
                remaining = max(0.0, timeout_s - (time.monotonic() - started))
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining)
                if message is not None or remaining == 0:
                    break
            if message is None:
                return False
            while await pubsub.get_message(ignore_subscribe_messages=True, timeout=0) is not None:
                pass
        except Exception as exc:
            await self._lost(exc)
            await asyncio.sleep(max(0.0, timeout_s - (time.monotonic() - started)))
            return False
        return True

    async def aclose(self) -> None:
        if self._pubsub is not None:
            pubsub, self._pubsub = self._pubsub, None
            try:
                await pubsub.aclose()
            except Exception as exc:
                self._log.debug("slots.ready_close_failed", errorType=type(exc).__name__)

    async def _subscribed(self) -> PubSub:
        if self._pubsub is None:
            pubsub = self._client.pubsub()
            _ = await pubsub.subscribe(self._channel)
            self._pubsub = pubsub
            if not self._healthy:
                self._healthy = True
                self._log.info("slots.ready_restored")
        return self._pubsub

    async def _lost(self, exc: Exception) -> None:
        if self._healthy:
            self._healthy = False
            self._log.warning("slots.ready_unavailable", reason="redis_error", errorType=type(exc).__name__)
        await self.aclose()


__all__ = ["RUN_READY_CHANNEL", "RedisRunReadySignal", "run_ready_channel"]
