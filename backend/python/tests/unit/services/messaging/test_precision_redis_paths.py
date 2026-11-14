"""The Redis Streams indexing consumer hands back vector-writing events in its real read loop, at dispatch and on recovery.

The real consumer runs against fakeredis, with the real filter and verdict; the handler is a recorded stub, so
"nothing was downloaded" is what the handler saw. Only the broker is a fake.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.precision_check import (
    HANDBACKS_KEY,
    PrecisionFilter,
    PrecisionVerdict,
    Verdict,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from tests.unit.services.messaging.test_connector_off_at_read import (
    GROUP,
    TOPIC,
    Handler,
    RedisHarness,
    _envelope,
    _fair,
    _until,
)

log = logging.getLogger("precision-redis")
ON = "conn-on"


def _verdict(mismatch: bool) -> PrecisionVerdict:
    verdict = PrecisionVerdict(fetch_states=AsyncMock(), indexes=AsyncMock(), logger=log)
    verdict._verdict = Verdict(mismatch, ["x"] if mismatch else [])
    return verdict


class _Producer:
    """The retry producer, writing to the fake broker the consumer reads from."""

    def __init__(self, harness: RedisHarness) -> None:
        self.harness = harness
        self.sent: list[dict] = []

    async def send_event(self, topic: str, event_type: str, payload: dict) -> None:
        self.sent.append(payload)
        if len(self.sent) > 20:  # a message sent round and round: stop instead of spinning
            raise AssertionError("an entry was handed back again while it was already waiting")
        envelope = {"eventType": event_type, "payload": payload, "timestamp": 1}
        await self.harness.client.xadd(topic, {"value": json.dumps(envelope)})


def _build(harness: RedisHarness, verdict: PrecisionVerdict, **fair: object) -> tuple[IndexingRedisStreamsConsumer, _Producer]:
    producer = _Producer(harness)
    consumer = IndexingRedisStreamsConsumer(
        log,
        RedisStreamsConfig(topics=[TOPIC], group_id=GROUP, client_id="indexing-1", batch_size=100, block_ms=20, claim_min_idle_ms=60_000),
        fair_scheduler_config=_fair(**fair),
        provider=harness.provider,
        precision_filter=PrecisionFilter(verdict, log, rng=random.Random(1)),
        producer=producer,
    )
    harness.consumer = consumer
    return consumer, producer


@pytest.fixture
async def harness():  # noqa: ANN201
    h = RedisHarness()
    yield h
    await h.stop()


class _Buffered:
    """Which entries took a slot in the fair-scheduling buffer."""

    def __init__(self, consumer: IndexingRedisStreamsConsumer) -> None:
        self.handbacks: list[int] = []
        enqueue = consumer._scheduler.enqueue

        def counted(key, item, not_before=None):  # noqa: ANN202
            self.handbacks.append(int(item[3].payload.get(HANDBACKS_KEY) or 0))
            return enqueue(key, item, not_before=not_before)

        consumer._scheduler.enqueue = counted


class TestReadLoop:
    async def test_a_vector_writing_event_is_handed_back_before_any_handler_and_a_delete_passes(self, harness) -> None:
        await harness.produce(_envelope("n1", ON), _envelope("n2", ON), _envelope("d1", ON, "deleteRecord"))
        consumer, producer = _build(harness, _verdict(True))
        buffered = _Buffered(consumer)
        handler = Handler()
        await consumer.start(handler)
        await _until(lambda: handler.seen == ["d1"] and len(producer.sent) == 2)
        assert handler.seen == ["d1"]  # nothing of the two new records was downloaded or parsed
        assert sorted(p["recordId"] for p in producer.sent) == ["n1", "n2"]
        assert all(p[HANDBACKS_KEY] == 1 and p["_retry_not_before"] > time.time() + 30 for p in producer.sent)
        # the copies waiting in the stream are not sent round again while they wait
        await asyncio.sleep(0.3)
        assert len(producer.sent) == 2
        assert int((await harness.client.xpending(TOPIC, GROUP))["pending"]) == 2  # only the two waiting copies
        assert sorted(buffered.handbacks) == [0, 1, 1]  # the delete and the two waiting copies; the originals took no slot

    async def test_no_mismatch_processes_everything(self, harness) -> None:
        await harness.produce(_envelope("n1", ON), _envelope("n2", ON))
        consumer, producer = _build(harness, _verdict(False))
        handler = Handler()
        await consumer.start(handler)
        await _until(lambda: sorted(handler.seen) == ["n1", "n2"])
        assert producer.sent == []

    async def test_without_a_filter_the_read_loop_is_as_before(self, harness) -> None:
        await harness.produce(_envelope("n1", ON))
        consumer, producer = _build(harness, _verdict(True))
        consumer.precision_filter = None
        handler = Handler()
        await consumer.start(handler)
        await _until(lambda: handler.seen == ["n1"])
        assert producer.sent == []


class TestLegacyIndexUnderAnInt8OptIn:
    async def test_the_first_message_is_handed_back_before_anything_is_downloaded_parsed_or_written(self, harness) -> None:
        from types import SimpleNamespace

        from app.services.messaging.precision_check import ServerState
        from app.services.messaging.precision_runtime import build_precision_verdict
        from app.services.vector_db.collection_pin import INT8, CollectionPinStore
        from tests.unit.services.vector_db.test_collection_pin import _FakeDb

        db = _FakeDb()
        db.add(None, n=5, org="org-1")  # an index from before weight precisions existed: points, no tags, no pin
        config = ConfigurationService(log, InMemoryKeyValueStore(log))
        registry = MagicMock()
        registry.list_managed_collections = AsyncMock(return_value=[SimpleNamespace(name="records", embedding_model="m")])
        graph = MagicMock()
        graph.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
        verdict = build_precision_verdict(config, registry, db, graph, log)
        verdict._fetch_states = AsyncMock(return_value={"m": ServerState(INT8, INT8, False)})
        await verdict.refresh()  # what starting the check does, before the first message is read
        assert verdict.mismatch

        await harness.produce(_envelope("n1", ON, orgId="org-1"))
        consumer, producer = _build(harness, verdict)
        handler = Handler()
        await consumer.start(handler)
        await _until(lambda: len(producer.sent) == 1)
        assert handler.seen == []  # nothing was downloaded or parsed
        assert len(db.points) == 5 and db.counts and await CollectionPinStore(config, log).get("records", "org-1", fresh=True) is None


class TestDispatchAndRecovery:
    async def test_an_entry_buffered_before_the_verdict_turned_is_handed_back_at_dispatch(self, harness) -> None:
        await harness.produce(_envelope("n1", ON))
        verdict = _verdict(False)
        consumer, producer = _build(harness, verdict)
        consumer.running = True
        consumer.redis = harness.client
        await harness.client.xgroup_create(TOPIC, GROUP, id="0", mkstream=True)
        entries = await harness.client.xreadgroup(GROUP, "indexing-1", {TOPIC: ">"}, count=10)
        (_, messages), = entries
        message_id, fields = messages[0]
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(TOPIC, message_id, fields)
        assert consumer._scheduler.pending_count == 1
        started = AsyncMock()
        consumer._start_processing_task = started
        verdict._verdict = Verdict(True, ["flip"])
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        started.assert_not_awaited()
        assert [p["recordId"] for p in producer.sent] == ["n1"]
        assert int((await harness.client.xpending(TOPIC, GROUP))["pending"]) == 0  # acknowledged after the publish

    async def test_an_entry_recovered_from_the_pending_list_is_handed_back(self, harness) -> None:
        await harness.produce(_envelope("n1", ON))
        consumer, producer = _build(harness, _verdict(True))
        consumer.running = True
        consumer.redis = harness.client
        await harness.client.xgroup_create(TOPIC, GROUP, id="0", mkstream=True)
        entries = await harness.client.xreadgroup(GROUP, "other-consumer", {TOPIC: ">"}, count=10)  # a crashed peer's
        (_, messages), = entries
        message_id, fields = messages[0]
        started = AsyncMock()
        consumer._start_processing_task = started
        consumer._should_dead_letter = AsyncMock(return_value=False)
        handed = await consumer._IndexingRedisStreamsConsumer__recover_entry(TOPIC, message_id, fields, owned=False)
        assert handed is True
        started.assert_not_awaited()
        assert [p["recordId"] for p in producer.sent] == ["n1"]
        assert int((await harness.client.xpending(TOPIC, GROUP))["pending"]) == 0

    async def test_a_failed_publish_leaves_the_recovered_entry_pending(self, harness) -> None:
        await harness.produce(_envelope("n1", ON))
        consumer, producer = _build(harness, _verdict(True))
        consumer.running = True
        consumer.redis = harness.client
        producer.send_event = AsyncMock(side_effect=RuntimeError("broker down"))
        await harness.client.xgroup_create(TOPIC, GROUP, id="0", mkstream=True)
        (_, messages), = await harness.client.xreadgroup(GROUP, "other-consumer", {TOPIC: ">"}, count=10)
        message_id, fields = messages[0]
        consumer._should_dead_letter = AsyncMock(return_value=False)
        assert await consumer._IndexingRedisStreamsConsumer__recover_entry(TOPIC, message_id, fields, owned=False) is False
        assert int((await harness.client.xpending(TOPIC, GROUP))["pending"]) == 1
