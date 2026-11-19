"""A busy record's later events wait for it, not for its connector, on a real Kafka broker.

With parallel dispatch, a long-running record R used to stall every later
event of its connector behind R's next event at the head of the scheduler's
queue. Here R is held for as long as the test likes: the other records of the
same connector must finish meanwhile, R's queued events must run after it one
at a time in offset order (most of them kept by offset only and read back
from the broker), and the committed offset must never pass an event that has
not run.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

import pytest

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    committed_offsets,
    create_kafka_topic,
    delete_kafka_topic,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# One partition, so offsets give one order to every event.
_R_EVENTS = 12
_OTHERS = 10


@pytest.fixture
async def topic(kafka_available: str, unique_suffix: str) -> AsyncIterator[str]:
    name = f"record-busy-{unique_suffix}"
    await create_kafka_topic(kafka_available, name, 1)
    yield name
    await delete_kafka_topic(kafka_available, name)


async def _publish(bootstrap: str, topic: str, events: list[tuple[str, int]]) -> None:
    producer = KafkaMessagingProducer(
        logging.getLogger("it-producer"),
        KafkaProducerConfig(bootstrap_servers=[bootstrap], client_id="it-busy-producer"),
    )
    await producer.initialize()
    try:
        for record_id, seq in events:
            await producer.send_event(
                topic=topic,
                event_type="newRecord",
                payload={
                    "recordId": record_id,
                    "orgId": "org-1",
                    "connectorId": "c1",
                    "extension": "txt",
                    "mimeType": "text/plain",
                    "seq": seq,
                },
            )
    finally:
        await producer.cleanup()


def _handler(
    done: list[tuple[str, int]],
    holds: dict[int, threading.Event],
    r_running: list[int],
    overlaps: list[int],
    timed_out: list[int],
) -> Callable[..., AsyncIterator]:
    """Hold each event in ``holds`` until its gate opens, never less.

    A gate that is still shut when the wait times out is recorded and waited
    on again, so a slow broker query cannot let an event finish early and the
    commit pass it before the test has looked.
    """
    lock = threading.Lock()

    async def handle(parsed_message) -> AsyncIterator:
        record_id = parsed_message.payload["recordId"]
        seq = parsed_message.payload["seq"]
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        if record_id == "R":
            with lock:
                r_running.append(seq)
                if len(r_running) > 1:
                    overlaps.append(seq)
            try:
                hold = holds.get(seq)
                while hold is not None and not await asyncio.to_thread(hold.wait, DRAIN_TIMEOUT_SECONDS):
                    timed_out.append(seq)
            finally:
                with lock:
                    r_running.remove(seq)
        done.append((record_id, seq))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handle


async def _wait_for(condition: Callable[[], bool], what: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DRAIN_TIMEOUT_SECONDS
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)


async def test_a_long_running_record_holds_back_only_its_own_events(
    kafka_available: str, topic: str, unique_suffix: str
) -> None:
    # R first, then R's later events interleaved with other records of the same connector.
    events: list[tuple[str, int]] = [("R", 0)]
    others = iter(range(_OTHERS))
    for seq in range(1, _R_EVENTS + 1):
        events.append(("R", seq))
        other = next(others, None)
        if other is not None:
            events.append((f"A{other}", 100 + other))
    await _publish(kafka_available, topic, events)
    total = len(events)
    r_offset = 0  # R's first event is the first message on the only partition.

    group = f"it-busy-{unique_suffix}"
    consumer = IndexingKafkaConsumer(
        logging.getLogger("it-consumer"),
        KafkaConsumerConfig(
            topics=[topic],
            client_id=f"{group}-client",
            group_id=group,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=[kafka_available],
        ),
        fair_scheduler_config=FairSchedulerConfig(
            enabled=True,
            key_fields=("orgId", "connectorId"),
            # Smaller than R's burst: most of R's waiting events are kept by offset only.
            max_buffered_messages=8,
            max_per_entity_messages=50,
            parallel_partitions=True,
        ),
    )
    done: list[tuple[str, int]] = []
    gate, second_gate = threading.Event(), threading.Event()
    r_running: list[int] = []
    overlaps: list[int] = []
    timed_out: list[int] = []
    await consumer.start(_handler(done, {0: gate, 1: second_gate}, r_running, overlaps, timed_out))
    try:
        await _wait_for(
            lambda: sum(1 for record, _ in done if record != "R") == _OTHERS,
            "the other records to finish while R runs",
        )
        assert [seq for record, seq in done if record == "R"] == []
        assert r_running == [0]
        # Give the consumer time to commit what it can, then check it stopped at R.
        await asyncio.sleep(2.0)
        committed = await committed_offsets(kafka_available, group, topic)
        assert committed.get(0, 0) <= r_offset, committed

        # R@0 done, R@1 (offset 1) running: the commit reaches offset 1 and must stop there,
        # though every other record of the connector finished long ago.
        gate.set()
        await _wait_for(lambda: r_running == [1], "R's next event to start")
        committed_at: list[int] = []

        async def reached_r1() -> bool:
            committed_at.append((await committed_offsets(kafka_available, group, topic)).get(0, 0))
            return committed_at[-1] >= r_offset + 1

        loop = asyncio.get_running_loop()
        deadline = loop.time() + DRAIN_TIMEOUT_SECONDS
        while not await reached_r1():
            assert loop.time() < deadline, "the commit never reached R's next event"
            await asyncio.sleep(0.2)
        await asyncio.sleep(2.0)
        committed = await committed_offsets(kafka_available, group, topic)
        assert committed.get(0, 0) == r_offset + 1, committed
        assert timed_out == [], f"a gate outlasted its wait while the test was still checking: {timed_out}"

        second_gate.set()
        await _wait_for(lambda: len(done) == total, "R's queued events to run")
        await asyncio.sleep(2.0)
    finally:
        gate.set()
        second_gate.set()
        await consumer.stop()

    assert [seq for record, seq in done if record == "R"] == list(range(_R_EVENTS + 1))
    assert overlaps == []
    assert timed_out == []
    committed = await committed_offsets(kafka_available, group, topic)
    assert committed.get(0) == total
