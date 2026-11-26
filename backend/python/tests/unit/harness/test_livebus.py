"""The Redis Streams LiveBus: contract suite, the shared wire vector, bounded failure (L-5, L-17)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fakeredis
import pytest

from app.harness.livebus import (
    RedisLiveBus,
    decode_frame,
    encode_frame,
    notify_channel,
    stream_key,
)
from harness_core.journal.types import LiveFrame
from harness_core.testing.contracts.livebus import LiveBusContract, frame

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

VECTOR = (
    Path(__file__).resolve().parents[5]
    / "contracts"
    / "harness"
    / "vectors"
    / "livebus-frames.json"
)


class TestRedisLiveBusContract(LiveBusContract):
    @pytest.fixture
    def bus(self) -> RedisLiveBus:
        return RedisLiveBus(
            fakeredis.FakeAsyncRedis(decode_responses=True), maxlen=self.MAXLEN
        )


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("REDIS_IT_URL"), reason="needs a Redis (REDIS_IT_URL)")
class TestRealRedisLiveBusContract(LiveBusContract):
    """The same contract on a real Redis: approximate trimming, TTL, XADD pipelines."""

    @pytest.fixture
    async def bus(self) -> AsyncIterator[RedisLiveBus]:
        from redis.asyncio import Redis

        client = Redis.from_url(os.environ["REDIS_IT_URL"], decode_responses=True)
        namespace = f"it-{uuid.uuid4().hex[:8]}"
        yield RedisLiveBus(client, key_namespace=namespace, maxlen=self.MAXLEN)
        async for key in client.scan_iter(f"{namespace}:*"):
            _ = await client.delete(key)
        await client.aclose()


def _vector() -> dict[str, Any]:
    return json.loads(VECTOR.read_text(encoding="utf-8"))


def test_the_wire_shape_matches_the_shared_vector() -> None:
    doc = _vector()
    for case in doc["keys"]:
        assert stream_key(case["runId"], case["namespace"]) == case["key"]
        assert notify_channel(case["runId"], case["namespace"]) == case["notifyChannel"]
    for case in doc["frames"]:
        raw = case["frame"]
        live = LiveFrame(
            raw["runId"],
            raw["fence"],
            raw["modelAttempt"],
            raw["n"],
            raw["kind"],
            raw["data"],
        )
        assert encode_frame(live) == case["fields"]
        assert live.frame_id == case["frameId"]
        assert decode_frame(raw["runId"], case["fields"]) == live
    for case in doc["rejected"]:
        assert decode_frame("run_1", case["fields"]) is None


async def test_every_publish_refreshes_the_ttl_and_uses_the_namespace() -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    bus = RedisLiveBus(client, key_namespace="tenant-a", ttl_s=120)
    await bus.publish(frame())
    assert await client.exists("tenant-a:harness:live:run-1") == 1
    assert 0 < await client.ttl("tenant-a:harness:live:run-1") <= 120


async def test_each_frame_wakes_the_run_readers_on_its_notify_channel() -> None:
    """A reader subscribed to the run's channel reads the stream at once
    instead of at its next poll; the message itself carries nothing."""
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe("tenant-a:harness:live-notify:run-1")
    assert (await pubsub.get_message(timeout=1))["type"] == "subscribe"
    bus = RedisLiveBus(client, key_namespace="tenant-a")
    await bus.publish(frame())
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
    assert message is not None
    assert (message["channel"], message["data"]) == ("tenant-a:harness:live-notify:run-1", "")
    await pubsub.aclose()


class _DownClient:
    def __init__(self) -> None:
        self.pipelines = 0

    def pipeline(self, transaction: bool = False) -> _DownClient:
        self.pipelines += 1
        return self

    def xadd(self, *args: object, **kwargs: object) -> None:
        return None

    def expire(self, *args: object, **kwargs: object) -> None:
        return None

    async def execute(self) -> None:
        raise ConnectionError("connection refused")


async def test_l5_redis_down_raises_once_then_backs_off_without_paying_a_timeout_per_frame() -> (
    None
):
    client = _DownClient()
    bus = RedisLiveBus(client, backoff_s=60)  # type: ignore[arg-type]
    with pytest.raises(ConnectionError):
        await bus.publish(frame(n=1))
    with pytest.raises(ConnectionError):
        await bus.publish(frame(n=2))
    assert client.pipelines == 1


async def test_create_returns_none_when_redis_is_unreachable() -> None:
    class _Config:
        async def get_redis_config(self) -> object:
            raise ConnectionError("no redis configured")

    assert await RedisLiveBus.create(_Config()) is None  # type: ignore[arg-type]
