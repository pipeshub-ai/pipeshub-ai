"""The LiveBus over Redis Streams (REPORT 6.2.4, D3): one capped, expiring stream per run.

Node's projector reads the same stream (``projection/livebus`` in the Node
``agent-harness`` module); the wire shape is pinned by
``contracts/harness/vectors/livebus-frames.json``. Frames carry their fence,
model attempt and number; consumers drop frames from a lower fence.

Each frame is followed by an empty message on the run's notify channel, so a
reader subscribed to it reads at once instead of at its next poll; the stream
stays the only carrier of frames.

Publishing is best effort and bounded: each ``XADD`` has a short timeout, and
after a failure the bus stays quiet for a backoff period instead of paying a
timeout per frame. A run's frames past its rate are dropped before they reach
Redis (SEC-DOS-02), so one run's flood costs the bus nothing. Without Redis, runs still stream at message granularity
from the journal (L-5).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, cast

from harness_core.journal.live_rate import LiveRateCap
from harness_core.journal.types import LIVE_BUFFER_FRAMES, LIVE_TTL_S, LiveFrame
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.harness.engines.signin_relay import RedisSignInRelay, RelayClient
    from app.harness.interaction_wakeup import PubSubClient, RedisInteractionWakeup
    from app.harness.run_ready import RedisRunReadySignal
    from app.services.redis.connection_provider import RedisClient

WIRE_VERSION = "1"
PUBLISH_TIMEOUT_S = 0.25
DOWN_BACKOFF_S = 5.0
KEY_PREFIX = "harness:live:"
NOTIFY_PREFIX = "harness:live-notify:"
# The activity bus (UX5): sandbox command output on its own stream, so it never evicts the chat's deltas.
ACTIVITY_KEY_PREFIX = "harness:activity:"
ACTIVITY_NOTIFY_PREFIX = "harness:activity-notify:"
ACTIVITY_BUFFER_FRAMES = 4_000


def _namespaced(name: str, key_namespace: str) -> str:
    return f"{key_namespace}:{name}" if key_namespace else name


def stream_key(run_id: str, key_namespace: str = "", prefix: str = KEY_PREFIX) -> str:
    return _namespaced(f"{prefix}{run_id}", key_namespace)


def notify_channel(run_id: str, key_namespace: str = "", prefix: str = NOTIFY_PREFIX) -> str:
    return _namespaced(f"{prefix}{run_id}", key_namespace)


def encode_frame(frame: LiveFrame) -> dict[str, str]:
    return {
        "v": WIRE_VERSION,
        "f": str(frame.fence),
        "a": str(frame.model_attempt),
        "n": str(frame.n),
        "k": frame.kind,
        "d": json.dumps(frame.data, ensure_ascii=False, separators=(",", ":")),
    }


def decode_frame(run_id: str, fields: dict[str, str]) -> LiveFrame | None:
    if fields.get("v") != WIRE_VERSION:
        return None
    return LiveFrame(
        run_id,
        int(fields["f"]),
        int(fields["a"]),
        int(fields["n"]),
        cast("Any", fields["k"]),
        json.loads(fields["d"]),
    )


class RedisLiveBus:
    """``LiveBusPort`` on Redis Streams; passes ``LiveBusContract``."""

    def __init__(
        self,
        client: RedisClient,
        *,
        key_namespace: str = "",
        maxlen: int = LIVE_BUFFER_FRAMES,
        ttl_s: int = LIVE_TTL_S,
        timeout_s: float = PUBLISH_TIMEOUT_S,
        backoff_s: float = DOWN_BACKOFF_S,
        key_prefix: str = KEY_PREFIX,
        notify_prefix: str = NOTIFY_PREFIX,
        cap: LiveRateCap | None = None,
    ) -> None:
        self._client = client
        self.cap = cap or LiveRateCap()
        self._key_prefix = key_prefix
        self._notify_prefix = notify_prefix
        self._namespace = key_namespace
        self._maxlen = maxlen
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._backoff_s = backoff_s
        self._down_until = 0.0
        self._log = HarnessLog.named("app.harness.livebus")

    @classmethod
    async def create(cls, config_service: ConfigurationService) -> RedisLiveBus | None:
        """The deployment's Redis, or ``None`` (journal-only streaming) when it has none."""
        from app.services.redis.config import ClientOptions, RedisConnectionConfig
        from app.services.redis.connection_provider_factory import get_redis_provider

        log = HarnessLog.named("app.harness.livebus")
        client = None
        try:
            redis_config = await config_service.get_redis_config()
            provider = get_redis_provider(
                RedisConnectionConfig.from_redis_config(redis_config)
            )
            client = provider.create_client(
                ClientOptions(
                    decode_responses=True,
                    socket_timeout_seconds=PUBLISH_TIMEOUT_S * 4,
                    socket_connect_timeout_seconds=PUBLISH_TIMEOUT_S * 4,
                )
            )
            _ = await client.ping()
        except Exception as exc:
            log.warning(
                "live.unavailable",
                reason="redis_unreachable",
                errorType=type(exc).__name__,
            )
            if client is not None:
                try:
                    await client.aclose()
                except Exception as close_error:
                    log.debug("live.close_failed", errorType=type(close_error).__name__)
            return None
        log.info(
            "live.ready",
            transport="redis_streams",
            namespaced=bool(provider.key_namespace),
        )
        return cls(client, key_namespace=provider.key_namespace)

    def activity_bus(self) -> RedisLiveBus:
        """The activity bus on the same connection (UX5); closing this bus closes both."""
        return RedisLiveBus(
            self._client,
            key_namespace=self._namespace,
            maxlen=ACTIVITY_BUFFER_FRAMES,
            ttl_s=self._ttl_s,
            timeout_s=self._timeout_s,
            backoff_s=self._backoff_s,
            key_prefix=ACTIVITY_KEY_PREFIX,
            notify_prefix=ACTIVITY_NOTIFY_PREFIX,
        )

    def _key(self, run_id: str) -> str:
        return stream_key(run_id, self._namespace, self._key_prefix)

    async def publish(self, frame: LiveFrame) -> None:
        if not self.cap.admit(frame.run_id):
            return
        if time.monotonic() < self._down_until:
            raise ConnectionError("live bus backing off after a failure")
        key = self._key(frame.run_id)
        try:
            pipe = self._client.pipeline(transaction=False)
            pipe.xadd(key, encode_frame(frame), maxlen=self._maxlen, approximate=True)
            pipe.expire(key, self._ttl_s)
            _ = await asyncio.wait_for(pipe.execute(), timeout=self._timeout_s)
            # Its own command: a cluster pipeline carries keyed commands only.
            _ = await asyncio.wait_for(
                self._client.publish(notify_channel(frame.run_id, self._namespace, self._notify_prefix), ""),
                timeout=self._timeout_s,
            )
        except Exception:
            self._down_until = time.monotonic() + self._backoff_s
            raise

    async def read(self, run_id: str) -> list[LiveFrame]:
        entries = await self._client.xrange(self._key(run_id))
        frames: list[LiveFrame] = []
        for _entry_id, fields in entries:
            decoded = decode_frame(run_id, dict(fields))
            if decoded is not None:
                frames.append(decoded)
        return frames

    def run_ready_signal(self) -> RedisRunReadySignal:
        """Hears runs Node made claimable, on the same Redis (LAT1)."""
        from app.harness.run_ready import RedisRunReadySignal

        return RedisRunReadySignal(cast("PubSubClient", self._client), key_namespace=self._namespace)

    def interaction_wakeup(self) -> RedisInteractionWakeup:
        """Wakes held approvals on the same Redis (P2.6c)."""
        from app.harness.interaction_wakeup import RedisInteractionWakeup

        return RedisInteractionWakeup(
            cast("PubSubClient", self._client), key_namespace=self._namespace
        )

    def signin_relay(self) -> RedisSignInRelay:
        """A person's own engine sign-in (SIGNIN): their browser's requests reach this worker, on the same Redis."""
        from app.harness.engines.signin_relay import RedisSignInRelay

        return RedisSignInRelay(cast("RelayClient", self._client), key_namespace=self._namespace)

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = [
    "ACTIVITY_BUFFER_FRAMES",
    "ACTIVITY_KEY_PREFIX",
    "ACTIVITY_NOTIFY_PREFIX",
    "KEY_PREFIX",
    "NOTIFY_PREFIX",
    "WIRE_VERSION",
    "RedisLiveBus",
    "decode_frame",
    "encode_frame",
    "notify_channel",
    "stream_key",
]
