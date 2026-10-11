"""A record whose source rate-limits its content fetch waits, and is not failed.

The connectors service answers the indexing fetch with 429 and a Retry-After
when the source (GitHub, past its quota reserve) will not serve the record yet.
These tests run both indexing consumers' real consume loops and worker threads
against the in-memory brokers, with the real record handler, its real message
wrapper and the real stranded-record sweep over an in-memory graph. Only the
HTTP fetch is stubbed.

Before this change the record was retried a few times within a minute and then
dead-lettered FAILED, although the source would have served it after the reset.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fakeredis.aioredis")

from app import indexing_main
from app.config.constants.arangodb import ProgressStatus
from app.services.distributed.interface import IRetryTracker
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as kafka_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.utils.api_call import ApiCallError
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.support.fake_kafka import FakeKafkaBroker
from tests.support.fake_record_graph import FakeRecordGraph
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from tests.support.fake_cluster_redis import FakeClusterRedis

# The stored fields, spelled out: they are what the sweep and the UI read.
RETRY_NOT_BEFORE_FIELD = "retryNotBefore"
RATE_LIMITED_SINCE_FIELD = "rateLimitedSince"
TOPIC = "record-events"
GROUP = "records_consumer_group"
CONNECTOR = "conn-gh"
RETRY_AFTER_S = 1800
HOUR_MS = 3600 * 1000

logger = logging.getLogger("rate-limited-records")


class CountingRetryTracker(IRetryTracker):
    """The consumers' failure counters, recording every failure they count."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counted: list[str] = []
        self.failures: dict[str, int] = {}

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        with self._lock:
            self.counted.append(message_id)
            self.failures[message_id] = self.failures.get(message_id, 0) + 1
            count = self.failures[message_id]
        return count, count >= max_attempts

    async def record_delivery(self, message_id: str) -> int:
        return 1

    async def get_count(self, message_id: str) -> int:
        with self._lock:
            return self.failures.get(message_id, 0)

    async def clear(self, message_id: str) -> None:
        with self._lock:
            self.failures.pop(message_id, None)

    async def clear_batch(self, message_ids: list[str]) -> int:
        for message_id in message_ids:
            await self.clear(message_id)
        return len(message_ids)

    async def has_pending_retries(self, message_ids: list[str]) -> bool:
        return False


class SentEvents:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        self.sent.append({"topic": topic, "eventType": event_type, "payload": payload, "key": key})
        return True


class AbandonSink:
    def __init__(self) -> None:
        self.abandoned: list[str | None] = []

    async def on_message_abandoned(self, message: StreamMessage | None, *, reason: str, attempts: int) -> None:
        self.abandoned.append(message.payload.get("recordId") if message else None)


def _envelope(record_id: str) -> dict:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": "org-1",
            "connectorId": CONNECTOR,
            "extension": "md",
            "mimeType": "text/markdown",
            "recordName": "README.md",
        },
        "timestamp": 1,
    }


def _fair() -> FairSchedulerConfig:
    return FairSchedulerConfig(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=100,
        max_per_entity_messages=50,
        max_dwell_seconds=900.0,
    )


class _NonBlockingReadProvider(FakeRedisConnectionProvider):
    """fakeredis can deadlock when one client blocks in XREADGROUP while another
    runs a command; reads go out without BLOCK and an empty one waits instead."""

    def create_client(self, options=None) -> FakeClusterRedis:
        client = super().create_client(options)
        read = client.xreadgroup

        async def xreadgroup(*args, block=None, **kwargs):  # noqa: ANN202
            result = await read(*args, **kwargs)
            if not result and block:
                await asyncio.sleep(block / 1000)
            return result

        client.xreadgroup = xreadgroup
        return client


async def _until(predicate, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.02)


class Setup:
    """The handler and everything around it that the broker does not provide."""

    def __init__(self) -> None:
        self.graph = FakeRecordGraph()
        self.graph.connector_names[CONNECTOR] = "Engineering GitHub"
        self.graph.find_next_queued_duplicate = AsyncMock(return_value=None)
        self.retries = CountingRetryTracker()
        self.retry_producer = SentEvents()
        self.sink = AbandonSink()
        rate_limited = ApiCallError(
            "API call failed with status 429: Too many requests to GitHub.", status_code=429
        )
        # Set as make_api_call sets it from the Retry-After header. Assigned
        # rather than passed so this module also runs, and fails on its
        # assertions, against a version that ignored the header.
        rate_limited.retry_after_seconds = RETRY_AFTER_S
        self.fetch = AsyncMock(side_effect=rate_limited)
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"connectors": {"endpoint": "http://connectors:8088"}})
        self.handler = RecordEventHandler(
            logger=logger,
            config_service=config_service,
            event_processor=SimpleNamespace(graph_provider=self.graph, processor=MagicMock()),
        )

    async def message_handler(self):  # noqa: ANN201
        return await KafkaUtils.create_record_message_handler(
            SimpleNamespace(logger=lambda: logger), record_event_service=self.handler
        )

    async def sweep(self, *, at_ms: int) -> SentEvents:
        sent = SentEvents()

        async def run_coordination(coro):  # noqa: ANN202
            return await coro

        with patch.object(indexing_main, "get_epoch_timestamp_in_ms", lambda: at_ms):
            await indexing_main._republish_stranded_records(
                graph_provider=self.graph,
                logger=logger,
                producer=sent,
                run_coordination=run_coordination,
                concurrency_manager=None,
                page_size=100,
            )
        return sent


@pytest.fixture
def setup() -> Iterator[Setup]:
    s = Setup()
    with patch("app.services.messaging.kafka.handlers.record.make_api_call", s.fetch), \
            patch("app.services.messaging.kafka.handlers.record.generate_jwt", AsyncMock(return_value="jwt")):
        yield s


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    monkeypatch.setenv("SHUTDOWN_TASK_TIMEOUT", "5")
    monkeypatch.setenv("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", "3600")
    monkeypatch.delenv("INDEXING_RATE_LIMIT_MAX_DEFER_SECONDS", raising=False)


class RedisBroker:
    def __init__(self, setup: Setup) -> None:
        self.setup = setup
        self.provider = _NonBlockingReadProvider(is_cluster=False)
        self.consumer: IndexingRedisStreamsConsumer | None = None

    @property
    def client(self) -> FakeClusterRedis:
        return self.provider.get_client()

    async def deliver(self, envelope: dict) -> None:
        await self.client.xadd(TOPIC, {"value": json.dumps(envelope)})
        self.consumer = IndexingRedisStreamsConsumer(
            logger,
            RedisStreamsConfig(
                topics=[TOPIC], group_id=GROUP, client_id="indexing-1",
                batch_size=10, block_ms=20, claim_min_idle_ms=60_000,
            ),
            retry_manager=self.setup.retries,
            producer=self.setup.retry_producer,
            fair_scheduler_config=_fair(),
            disposition_sink=self.setup.sink,
            provider=self.provider,
        )
        await self.consumer.start(await self.setup.message_handler())
        await _until(self._settled)
        await self.consumer.stop()
        self.consumer = None

    async def _settled(self) -> bool:
        stream = await self.client.xinfo_stream(TOPIC)
        groups = await self.client.xinfo_groups(TOPIC)
        group = next((g for g in groups if g["name"] == GROUP), None)
        return (
            group is not None
            and group["last-delivered-id"] == stream["last-generated-id"]
            and int(group["pending"]) == 0
        )

    async def stop(self) -> None:
        if self.consumer is not None:
            await self.consumer.stop()


class KafkaBroker:
    def __init__(self, setup: Setup, broker: FakeKafkaBroker) -> None:
        self.setup = setup
        self.broker = broker
        self.consumer: IndexingKafkaConsumer | None = None

    async def deliver(self, envelope: dict) -> None:
        self.broker.produce(TOPIC, envelope)
        target = len(self.broker.logs[next(iter(self.broker.logs))])
        self.consumer = IndexingKafkaConsumer(
            logger,
            KafkaConsumerConfig(
                topics=[TOPIC], client_id="indexing-test", group_id=GROUP,
                auto_offset_reset="earliest", enable_auto_commit=False,
                bootstrap_servers=["kafka:9092"],
            ),
            retry_manager=self.setup.retries,
            producer=self.setup.retry_producer,
            fair_scheduler_config=_fair(),
            disposition_sink=self.setup.sink,
        )
        await self.consumer.start(await self.setup.message_handler())
        await _until(lambda: (self.broker.committed_offset(GROUP, TOPIC) or 0) == target)
        await self.consumer.stop()
        self.consumer = None

    async def stop(self) -> None:
        if self.consumer is not None:
            await self.consumer.stop()


@pytest.fixture(params=["redis", "kafka"])
async def broker(request: pytest.FixtureRequest, setup: Setup) -> AsyncIterator[RedisBroker | KafkaBroker]:
    if request.param == "redis":
        harness: RedisBroker | KafkaBroker = RedisBroker(setup)
        yield harness
        await harness.stop()
        return
    fake = FakeKafkaBroker()
    with patch.object(kafka_module, "AIOKafkaConsumer", fake.consumer_factory()):
        harness = KafkaBroker(setup, fake)
        yield harness
        await harness.stop()


class TestARateLimitedRecordWaits:
    async def test_it_is_neither_failed_nor_counted_and_is_sent_again_only_once_its_time_has_passed(
        self, setup: Setup, broker: RedisBroker | KafkaBroker
    ) -> None:
        setup.graph.add_queued("gh-1", CONNECTOR, queued_at_ms=get_epoch_timestamp_in_ms() - 60_000)
        before = get_epoch_timestamp_in_ms()

        await broker.deliver(_envelope("gh-1"))

        # Acknowledged as handled: no failure counted, no retry copy, not dead-lettered.
        assert setup.retries.counted == []
        assert setup.retry_producer.sent == []
        assert setup.sink.abandoned == []
        assert setup.fetch.await_count == 1

        stored = setup.graph.records["gh-1"]
        assert stored["indexingStatus"] == ProgressStatus.QUEUED.value
        assert "Engineering GitHub is limiting" in stored["reason"]
        assert "retry automatically after" in stored["reason"]
        not_before = stored[RETRY_NOT_BEFORE_FIELD]
        assert before + RETRY_AFTER_S * 1000 <= not_before <= get_epoch_timestamp_in_ms() + RETRY_AFTER_S * 1000
        assert stored[RATE_LIMITED_SINCE_FIELD] >= before

        assert (await setup.sweep(at_ms=get_epoch_timestamp_in_ms())).sent == []
        assert (await setup.sweep(at_ms=not_before - 1000)).sent == []

        resent = await setup.sweep(at_ms=not_before + 60_000)
        assert [(e["eventType"], e["payload"]["recordId"], e["key"]) for e in resent.sent] == [
            ("newRecord", "gh-1", "gh-1")
        ]
        assert (await setup.sweep(at_ms=not_before + 120_000)).sent == [], "sent once, not every pass"

    async def test_a_record_still_refused_after_a_day_ends_failed_with_the_reason(
        self, setup: Setup, broker: RedisBroker | KafkaBroker
    ) -> None:
        now = get_epoch_timestamp_in_ms()
        setup.graph.add_queued(
            "gh-2", CONNECTOR, queued_at_ms=now - 2 * 60_000,
            **{RATE_LIMITED_SINCE_FIELD: now - 23 * HOUR_MS - 45 * 60_000, RETRY_NOT_BEFORE_FIELD: now - 60_000},
        )

        await broker.deliver(_envelope("gh-2"))

        stored = setup.graph.records["gh-2"]
        assert stored["indexingStatus"] == ProgressStatus.FAILED.value
        assert "Engineering GitHub kept limiting" in stored["reason"]
        assert "more than 24 hours" in stored["reason"]
        assert RETRY_NOT_BEFORE_FIELD not in stored
        assert RATE_LIMITED_SINCE_FIELD not in stored
        assert setup.retries.counted == []
        assert setup.retry_producer.sent == []
        setup.graph.find_next_queued_duplicate.assert_awaited_once()

        assert (await setup.sweep(at_ms=now + 2 * HOUR_MS)).sent == [], "nothing re-sends a failed record"


async def _run(setup: Setup, record_id: str) -> list[str]:
    payload = dict(_envelope(record_id)["payload"], is_final_failure=False)
    return [event.event.value async for event in setup.handler.process_event("newRecord", payload)]


class TestWhatIsNotDeferred:
    """Anything else keeps the ordinary retry path, so nothing is left waiting
    on a sweep that will not come."""

    async def test_a_429_without_retry_after(self, setup: Setup) -> None:
        setup.fetch.side_effect = ApiCallError("slow down", status_code=429)
        setup.graph.add_queued("r", CONNECTOR, queued_at_ms=1)
        with pytest.raises(Exception, match="slow down"):
            await _run(setup, "r")
        assert RETRY_NOT_BEFORE_FIELD not in setup.graph.records["r"]

    @pytest.mark.parametrize(
        "env",
        [
            {"STRANDED_RECORD_REPUBLISH_AFTER_SECONDS": "0"},
            {"INDEXING_RATE_LIMIT_MAX_DEFER_SECONDS": "0"},
        ],
    )
    async def test_when_nothing_would_send_it_again(
        self, setup: Setup, env: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        setup.graph.add_queued("r", CONNECTOR, queued_at_ms=1)
        with pytest.raises(Exception, match="429"):
            await _run(setup, "r")
        assert RETRY_NOT_BEFORE_FIELD not in setup.graph.records["r"]

    async def test_a_resumed_enrichment(self, setup: Setup) -> None:
        """Its record is searchable; putting it back to QUEUED would re-index it."""
        setup.graph.add_queued(
            "r", CONNECTOR, queued_at_ms=1,
            indexingStatus=ProgressStatus.COMPLETED.value,
            extractionStatus=ProgressStatus.IN_PROGRESS.value,
        )
        with pytest.raises(Exception, match="429"):
            await _run(setup, "r")
        assert setup.graph.records["r"]["indexingStatus"] == ProgressStatus.COMPLETED.value


class TestAServedRecordEndsItsWait:
    async def test_the_wait_is_cleared_once_the_source_serves_it(self, setup: Setup) -> None:
        async def on_event(event_data):  # noqa: ANN202
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r"))

        setup.handler.event_processor.on_event = on_event
        setup.fetch.side_effect = None
        setup.fetch.return_value = {"is_json": False, "data": b"# readme"}
        now = get_epoch_timestamp_in_ms()
        setup.graph.add_queued(
            "r", CONNECTOR, queued_at_ms=now,
            **{RATE_LIMITED_SINCE_FIELD: now - HOUR_MS, RETRY_NOT_BEFORE_FIELD: now - 60_000},
        )

        assert await _run(setup, "r") == ["parsing_complete", "indexing_complete"]

        assert RETRY_NOT_BEFORE_FIELD not in setup.graph.records["r"]
        assert RATE_LIMITED_SINCE_FIELD not in setup.graph.records["r"]
