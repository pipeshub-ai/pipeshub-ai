"""A consumer process killed mid-message: the message comes back, and one that keeps killing its consumer is given up on."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from logging import getLogger
from pathlib import Path

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue

HERE = Path(__file__).parent
pytestmark = [pytest.mark.integration, pytest.mark.chaos]
CLAIM_S = 1.5


def _spawn(runtime, topic: str, group: str, name: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "PG_CORE_URL": runtime.cfg.dsn,
        "PG_POOLER": "none",
        "PG_RUNTIME_SERVICE": "connectors",
        "PYTHONPATH": os.getcwd(),
    }
    return subprocess.Popen(
        [sys.executable, str(HERE / "hanging_consumer_child.py"), topic, group, name, str(CLAIM_S)],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )


def _kill_after_claim(proc: subprocess.Popen) -> list[int]:
    try:
        attempts = json.loads(proc.stdout.readline())["claimed"]
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    return attempts


async def test_a_message_held_by_a_killed_consumer_is_delivered_again_and_the_old_claim_cannot_ack(runtime, uid) -> None:
    queue, topic = PgQueue(runtime), f"kill-{uid}"
    await queue.ensure_group(topic, "g", from_start=True)
    await PgMessagingProducer(getLogger("t"), queue=queue).send_event(topic, "e", {"i": 0})
    assert _kill_after_claim(_spawn(runtime, topic, "g", "victim")) == [1]
    assert await queue.claim("g", [topic], "other", 10, CLAIM_S) == []  # still held until the claim runs out
    await asyncio.sleep(CLAIM_S + 0.3)
    again = await queue.claim("g", [topic], "other", 10, CLAIM_S)
    assert [d.attempts for d in again] == [2]
    assert await queue.ack("g", again[0])


async def test_a_message_that_kills_every_consumer_is_dead_lettered_and_the_next_message_proceeds(runtime, uid, monkeypatch) -> None:
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    queue, topic = PgQueue(runtime), f"kill-{uid}"
    told: list[int] = []
    handled: list[int] = []

    class Sink:
        async def on_message_abandoned(self, message, *, reason, attempts) -> None:
            told.append(message.payload["i"] if message is not None else -1)

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        handled.append(m.payload["i"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    await queue.ensure_group(topic, "idx", from_start=True)
    producer = PgMessagingProducer(getLogger("t"), queue=queue)
    await producer.send_event(topic, "e", {"i": 0})
    for n in (1, 2, 3):
        assert _kill_after_claim(_spawn(runtime, topic, "idx", f"victim-{n}")) == [n]
        await asyncio.sleep(CLAIM_S + 0.3)
    await producer.send_event(topic, "e", {"i": 1})
    cfg = RedisStreamsConfig(topics=[topic], group_id="idx", client_id="live", block_ms=1000, batch_size=5)
    consumer = PgMessagingConsumer(getLogger("t"), cfg, indexing=True, disposition_sink=Sink())
    consumer.queue = queue
    await consumer.start(handler)
    try:
        for _ in range(300):
            if told and 1 in handled:
                break
            await asyncio.sleep(0.05)
        assert told == [0] and handled == [1]  # the message that killed three consumers was never handed to a handler
    finally:
        await consumer.stop()
