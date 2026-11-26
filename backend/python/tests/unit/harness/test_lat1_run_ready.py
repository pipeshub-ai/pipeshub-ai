"""LAT1 NT-7: ``RunReadySignalContract`` on every adapter (in memory, Redis on
fakeredis, and a real Redis when ``REDIS_IT_URL`` is set); the channel Node
publishes on; a Redis failure degrades to the poll."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import fakeredis
import pytest

from app.harness.run_ready import RedisRunReadySignal, run_ready_channel
from harness_core.journal.ready import InMemoryRunReadySignal
from harness_core.testing.contracts.run_ready import (
    ReadyHarness,
    RunReadySignalContract,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass
class _Memory:
    signal: InMemoryRunReadySignal

    async def announce(self, org_id: str, run_id: str) -> None:
        self.signal.notify()


@dataclass
class _Redis:
    signal: RedisRunReadySignal
    client: Any
    namespace: str = ""

    async def announce(self, org_id: str, run_id: str) -> None:
        notice = {"orgId": org_id, "runId": run_id, "kind": "run_ready"}
        _ = await self.client.publish(run_ready_channel(self.namespace), json.dumps(notice))


class TestInMemoryRunReadySignal(RunReadySignalContract):
    @pytest.fixture
    def ready_harness(self) -> ReadyHarness:
        return _Memory(InMemoryRunReadySignal())


class TestRedisRunReadySignal(RunReadySignalContract):
    @pytest.fixture
    async def ready_harness(self) -> AsyncIterator[ReadyHarness]:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        harness = _Redis(RedisRunReadySignal(client), client)
        yield harness
        await harness.signal.aclose()


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("REDIS_IT_URL"), reason="needs a Redis (REDIS_IT_URL)")
class TestRealRedisRunReadySignal(RunReadySignalContract):
    @pytest.fixture
    async def ready_harness(self) -> AsyncIterator[ReadyHarness]:
        from redis.asyncio import Redis

        client = Redis.from_url(os.environ["REDIS_IT_URL"], decode_responses=True)
        namespace = f"it-{uuid.uuid4().hex[:8]}"
        harness = _Redis(RedisRunReadySignal(client, key_namespace=namespace), client, namespace)
        yield harness
        await harness.signal.aclose()
        await client.aclose()


def test_the_channel_is_the_one_node_publishes_on() -> None:
    assert run_ready_channel() == "harness:run-ready"
    assert run_ready_channel("tenant-a") == "tenant-a:harness:run-ready"


async def test_a_redis_failure_degrades_to_the_poll_and_recovers(caplog: pytest.LogCaptureFixture) -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.down = True
            self.real = fakeredis.FakeAsyncRedis(decode_responses=True)

        def pubsub(self) -> Any:  # noqa: ANN401
            if self.down:
                raise ConnectionError("redis away")
            return self.real.pubsub()

    client = _Flaky()
    signal = RedisRunReadySignal(client)
    assert await signal.wait(0) is False
    assert await signal.wait(0) is False
    assert signal.healthy is False
    assert sum(r.getMessage().startswith("slots.ready_unavailable") for r in caplog.records) == 1
    client.down = False
    assert await signal.wait(0) is False
    assert signal.healthy is True
    _ = await client.real.publish(run_ready_channel(), "{}")
    assert await signal.wait(5) is True
    await signal.aclose()


async def test_a_dropped_subscription_is_replaced_not_reused() -> None:
    class _Dropping:
        def __init__(self) -> None:
            self.real = fakeredis.FakeAsyncRedis(decode_responses=True)
            self.opened = 0

        def pubsub(self) -> Any:  # noqa: ANN401
            self.opened += 1
            pubsub = self.real.pubsub()
            if self.opened == 1:

                async def dropped(**_: Any) -> None:  # noqa: ANN401
                    raise ConnectionError("connection reset")

                pubsub.get_message = dropped
            return pubsub

    client = _Dropping()
    signal = RedisRunReadySignal(client)
    assert await signal.wait(0) is False
    assert await signal.wait(0) is False
    assert client.opened == 2
    _ = await client.real.publish(run_ready_channel(), "{}")
    assert await signal.wait(5) is True
    await signal.aclose()
