"""KH-11 transport: ``InteractionWakeupContract`` on Redis pub/sub (fakeredis, and a real
Redis when ``REDIS_IT_URL`` is set); the channel name Node publishes on."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import fakeredis
import pytest

from app.harness.interaction_wakeup import RedisInteractionWakeup, wake_channel
from harness_core.testing.contracts.interactions import (
    InteractionWakeupContract,
    WakeupHarness,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class _Harness:
    wakeup: RedisInteractionWakeup
    client: Any
    namespace: str = ""

    async def wake(self, run_id: str) -> None:
        _ = await self.client.publish(wake_channel(run_id, self.namespace), "")


class TestRedisInteractionWakeup(InteractionWakeupContract):
    @pytest.fixture
    def wakeup_harness(self) -> WakeupHarness:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        return _Harness(RedisInteractionWakeup(client), client)


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("REDIS_IT_URL"), reason="needs a Redis (REDIS_IT_URL)")
class TestRealRedisInteractionWakeup(InteractionWakeupContract):
    @pytest.fixture
    async def wakeup_harness(self) -> AsyncIterator[WakeupHarness]:
        from redis.asyncio import Redis

        client = Redis.from_url(os.environ["REDIS_IT_URL"], decode_responses=True)
        namespace = f"it-{uuid.uuid4().hex[:8]}"
        yield _Harness(RedisInteractionWakeup(client, key_namespace=namespace), client, namespace)
        await client.aclose()


def test_the_channel_is_the_one_node_publishes_on() -> None:
    assert wake_channel("run-1") == "harness:interaction-notify:run-1"
    assert wake_channel("run-1", "tenant-a") == "tenant-a:harness:interaction-notify:run-1"


async def test_a_redis_failure_degrades_to_a_sleep(caplog: pytest.LogCaptureFixture) -> None:
    class _Down:
        def pubsub(self) -> Any:  # noqa: ANN401
            raise ConnectionError("redis down")

    wakeup = RedisInteractionWakeup(_Down())  # type: ignore[arg-type]
    assert await wakeup.wait("run-1", 0.01) is False
    assert await wakeup.wait("run-1", 0.01) is False
    assert sum("interaction.wakeup_unavailable" in r.getMessage() for r in caplog.records) == 1


async def test_qa17_f4_a_cancelled_wait_ends_while_redis_is_unreachable() -> None:
    """The run's Stop watcher is cancelled once its call returns; a cleanup that waits on a down Redis held the
    whole run (QA17-F4: the fault suite's Redis-down run never finished)."""
    import asyncio

    listening = asyncio.Event()

    class _Unreachable:
        closed = False

        async def subscribe(self, *channels: str) -> object:
            return None

        async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float) -> object | None:
            listening.set()
            await asyncio.Event().wait()
            return None

        async def unsubscribe(self, *channels: str) -> object:
            await asyncio.Event().wait()
            return None

        async def aclose(self) -> None:
            self.closed = True

    pubsub = _Unreachable()

    class _Client:
        def pubsub(self) -> Any:  # noqa: ANN401
            return pubsub

    task = asyncio.create_task(RedisInteractionWakeup(_Client()).wait("run-1", 3600))
    await listening.wait()
    _ = task.cancel()
    for _ in range(10):
        await asyncio.sleep(0)

    assert task.done() and task.cancelled()
    assert pubsub.closed
