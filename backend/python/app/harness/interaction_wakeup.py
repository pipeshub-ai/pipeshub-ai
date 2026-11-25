"""``InteractionWakeup`` on Redis pub/sub: Node publishes on a run's channel when it
resolves an interaction (or cancels) while the run is running.

A hint only: a holder subscribes for one wait at a time, and a message sent
between its journal read and its subscribe is lost, which costs one poll
interval (the bridge re-reads the journal at least every ``POLL_S``). Without
Redis, or after a Redis error, the wait just sleeps.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from harness_core.logs import HarnessLog

WAKE_PREFIX = "harness:interaction-notify:"


def wake_channel(run_id: str, key_namespace: str = "") -> str:
    name = f"{WAKE_PREFIX}{run_id}"
    return f"{key_namespace}:{name}" if key_namespace else name


class PubSub(Protocol):
    """The part of redis-py's asyncio ``PubSub`` a wait uses."""

    async def subscribe(self, *channels: str) -> object: ...

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float
    ) -> object | None: ...

    async def aclose(self) -> None: ...


class PubSubClient(Protocol):
    def pubsub(self) -> PubSub: ...


class RedisInteractionWakeup:
    def __init__(self, client: PubSubClient, *, key_namespace: str = "") -> None:
        self._client = client
        self._namespace = key_namespace
        self._log = HarnessLog.named("app.harness.interactions")
        self._failed = False

    async def wait(self, run_id: str, timeout_s: float) -> bool:
        started = time.monotonic()
        channel = wake_channel(run_id, self._namespace)
        try:
            pubsub = self._client.pubsub()
            try:
                _ = await pubsub.subscribe(channel)
                while (remaining := timeout_s - (time.monotonic() - started)) > 0:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=remaining
                    )
                    if message is not None:
                        return True
                return False
            finally:
                # Closing drops the subscription with the connection; an UNSUBSCRIBE first would wait on a down
                # Redis, and a cancelled wait (the Stop watcher of a call that returned) held its run (QA17-F4).
                await pubsub.aclose()
        except Exception as exc:
            if not self._failed:
                self._failed = True
                self._log.warning(
                    "interaction.wakeup_unavailable", errorType=type(exc).__name__
                )
            await asyncio.sleep(max(0.0, timeout_s - (time.monotonic() - started)))
            return False


__all__ = ["WAKE_PREFIX", "RedisInteractionWakeup", "wake_channel"]
