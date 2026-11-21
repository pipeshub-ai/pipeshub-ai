"""A Docling 429 holds only the records that call Docling (default admission mode).

Docling is reached only from a PDF-route record (``events.py`` ``Route.PDF``), so its ``429 + Retry-After`` must not
stop the light records a consumer reads beside them. ``policy.SERVICE_POOL`` names it as the one dependency only the
heavy index tier calls; on a consumer with the fair scheduler a Docling pause is ignored by the read stop and held per
record at dispatch instead. Every other service's 429, and any 429 on a broker-order consumer, still stops everything.

Driven with a real ``BackpressureCoordinator`` on a fake clock and real consumers with mocked broker I/O; a patched
``asyncio.sleep`` advances the clock instead of sleeping.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from aiokafka import TopicPartition

from app.services.docling.client import DoclingClient, DoclingUnavailableError
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.backpressure import BackpressureCoordinator
from app.services.messaging.config import RedisStreamsConfig, StreamMessage
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.redis_streams import indexing_consumer as redis_module
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor import DownstreamFeedback
from app.services.resource_governor.feedback import set_default_downstream_feedback
from app.services.resource_governor.models import Pool

if TYPE_CHECKING:
    from collections.abc import Iterator

DOCLING = "DoclingService"
PARSING = "ParsingService"
RETRY_AFTER = 30.0
START = 1_000.0


class _Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now

    def at(self, seconds: float) -> None:
        self.now = START + seconds


@pytest.fixture(autouse=True)
def _fresh_feedback() -> Iterator[None]:
    """``signal`` reports a throttle to the process-wide feedback; keep it from leaking into other tests."""
    set_default_downstream_feedback(DownstreamFeedback())
    yield
    set_default_downstream_feedback(None)


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_docling_429_scope")


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def coordinator(clock: _Clock) -> BackpressureCoordinator:
    return BackpressureCoordinator(clock=clock)


def _fair(enabled: bool = True) -> FairSchedulerConfig:
    return FairSchedulerConfig(enabled=enabled, key_fields=("orgId", "connectorId"), default_quantum=1)


def _payload(index: int, extension: str, mime: str | None = None, connector: str | None = None) -> dict[str, str]:
    return {
        "recordId": f"r-{index}", "orgId": "org-1", "connectorId": connector or f"c{index}", "extension": extension,
        "mimeType": mime or ("application/pdf" if extension == "pdf" else "text/plain"),
    }


def _message(**payload: str) -> StreamMessage:
    return StreamMessage(eventType="newRecord", payload=payload)


def _starts(consumer, parsed_of, on_start=None) -> tuple[AsyncMock, list[str]]:
    """Start-processing stand-in: records each started record's id and leaves a gate-waiter token like the real one."""
    started: list[str] = []

    def start(*a, **k) -> None:
        parsed = parsed_of(a, k)
        started.append(parsed.payload["recordId"])
        concurrency.GateWaiterToken(consumer, concurrency.dispatch_tier(consumer, parsed))
        if on_start is not None:
            on_start()

    return AsyncMock(side_effect=start), started


def _redis(logger, coordinator, *, fair: bool = True, on_start=None, **kwargs) -> tuple[IndexingRedisStreamsConsumer, list[str]]:
    consumer = IndexingRedisStreamsConsumer(
        logger, RedisStreamsConfig(host="h", port=6379, group_id="g", topics=["record-events"], batch_size=10),
        fair_scheduler_config=_fair(fair), backpressure_coordinator=coordinator, **kwargs,
    )
    consumer.redis = AsyncMock()
    consumer.running = True
    consumer._start_processing_task, started = _starts(
        consumer, lambda a, k: a[3] if len(a) > 3 else k.get("parsed_message"), on_start
    )
    return consumer, started


def _kafka(logger, coordinator, *, fair: bool = True, on_start=None) -> tuple[IndexingKafkaConsumer, list[str]]:
    consumer = IndexingKafkaConsumer(
        logger,
        KafkaConsumerConfig(topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
                            enable_auto_commit=False, bootstrap_servers=["b:9092"]),
        backpressure_coordinator=coordinator, fair_scheduler_config=_fair(fair),
    )
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.consumer.assignment.return_value = {TopicPartition("record-events", 0)}
    consumer.consumer.paused.return_value = set()
    consumer.running = True
    consumer._IndexingKafkaConsumer__start_processing_task, started = _starts(consumer, lambda a, k: a[1], on_start)
    return consumer, started


async def _redis_buffer(consumer, *payloads: dict[str, str]) -> None:
    for index, payload in enumerate(payloads):
        fields = {"value": json.dumps({"eventType": "newRecord", "payload": payload})}
        await consumer._IndexingRedisStreamsConsumer__enqueue_message("record-events", f"{index}-0", fields)


async def _kafka_buffer(consumer, *payloads: dict[str, str]) -> None:
    for index, payload in enumerate(payloads):
        msg = MagicMock(topic="record-events", partition=index, offset=index)
        msg.value = json.dumps({"eventType": "newRecord", "payload": payload}).encode()
        await consumer._IndexingKafkaConsumer__enqueue_message(TopicPartition("record-events", index), msg)


def _advance_on_sleep(monkeypatch, clock: _Clock) -> list[float]:
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake(seconds: float, *a, **k) -> None:
        slept.append(seconds)
        clock.now += seconds
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake)
    return slept


LIGHT = _payload(0, "txt")
HEAVY = _payload(1, "pdf")


# --- the coordinator ---------------------------------------------------------------------------------------------------


class TestPauseRemainingIgnoring:
    def test_a_docling_only_pause_is_zero_when_docling_is_ignored(self, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        assert coordinator.pause_remaining(ignoring={DOCLING}) == 0.0
        assert coordinator.pause_remaining() == RETRY_AFTER

    def test_with_both_paused_it_is_the_other_services_wait(self, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        coordinator.signal(PARSING, 5.0)
        assert coordinator.pause_remaining(ignoring={DOCLING}) == 5.0
        assert coordinator.pause_remaining() == RETRY_AFTER

    def test_ignoring_a_service_that_is_not_paused_changes_nothing(self, coordinator) -> None:
        coordinator.signal(PARSING, 5.0)
        assert coordinator.pause_remaining(ignoring={DOCLING}) == 5.0

    def test_expiry_is_pruned_and_the_ignored_service_is_still_named(self, coordinator, clock) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        coordinator.signal(PARSING, 5.0)
        assert coordinator.paused_services == {DOCLING, PARSING}
        clock.at(5.0)
        assert coordinator.pause_remaining(ignoring={DOCLING}) == 0.0
        assert coordinator.paused_services == {DOCLING}
        clock.at(RETRY_AFTER)
        assert coordinator.paused_services == frozenset()

    def test_the_signal_log_does_not_claim_a_scope(self, coordinator, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="app.services.messaging.backpressure"):
            coordinator.signal(DOCLING, RETRY_AFTER)
        assert "Backpressure signalled by DoclingService for 30.0s" in caplog.text


# --- the helpers -------------------------------------------------------------------------------------------------------


def _host(coordinator=None, *, light_ceiling: int = 64, stage=None) -> SimpleNamespace:
    governor = SimpleNamespace(ceilings=SimpleNamespace(index_light=light_ceiling))
    return SimpleNamespace(backpressure_coordinator=coordinator, governor=governor, stage_admission=stage)


class TestDownstreamReadPause:
    def test_scoped_ignores_docling_and_waits_for_parsing(self, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        assert concurrency.downstream_read_pause(_host(coordinator), scoped=True) == 0.0
        coordinator.signal(PARSING, 5.0)
        assert concurrency.downstream_read_pause(_host(coordinator), scoped=True) == 5.0

    def test_unscoped_waits_for_docling(self, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        assert concurrency.downstream_read_pause(_host(coordinator), scoped=False) == RETRY_AFTER

    def test_no_coordinator_never_pauses(self) -> None:
        assert concurrency.downstream_read_pause(_host(None), scoped=False) == 0.0
        assert concurrency.held_pools(_host(None)) == frozenset()
        assert concurrency.read_pausing_services(_host(None), scoped=True) == []

    def test_the_log_names_only_the_services_holding_reads(self, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        coordinator.signal(PARSING, 5.0)
        assert concurrency.read_pausing_services(_host(coordinator), scoped=True) == [PARSING]
        assert concurrency.read_pausing_services(_host(coordinator), scoped=False) == [DOCLING, PARSING]


class TestHeldDownstream:
    @pytest.mark.parametrize(
        ("extension", "mime"),
        [
            ("pdf", "application/pdf"),
            ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("unknown", "application/octet-stream"),
            ("", ""),
            ("txt", "application/pdf"),
            ("md", "application/pdf"),
            ("json", "application/pdf"),
            ("", "text/x-pdf"),
            ("htm", "text/x-pdf"),  # htm reads as HTML only when the mime names nothing, so any PDF spelling routes to Docling
        ],
    )
    def test_heavy_and_pdf_route_records_are_held(self, extension: str, mime: str) -> None:
        held = concurrency.held_downstream(_host(), _message(extension=extension, mimeType=mime), frozenset({Pool.INDEX_HEAVY}))
        assert held, (extension, mime)

    @pytest.mark.parametrize(
        ("extension", "mime"),
        [
            ("txt", "text/plain"), ("", "application/blocks"), ("", "text/gmail_content"), ("md", "text/markdown"),
            ("py", "text/x-python"),
            ("txt", "text/x-pdf"),  # a PDF mime alias does not outrank a light extension: the handler reads it as text
        ],
    )
    def test_light_records_that_never_call_docling_are_not_held(self, extension: str, mime: str) -> None:
        held = concurrency.held_downstream(_host(), _message(extension=extension, mimeType=mime), frozenset({Pool.INDEX_HEAVY}))
        assert not held, (extension, mime)

    def test_a_name_that_names_a_pdf_holds_an_unlabelled_record(self) -> None:
        message = _message(extension="", mimeType="", recordName="report.pdf")
        assert concurrency.held_downstream(_host(), message, frozenset({Pool.INDEX_HEAVY}))

    @pytest.mark.parametrize("mime", [None, "", "unknown"])
    @pytest.mark.parametrize("extension", ["txt", "md", "html", "htm"])
    def test_a_light_event_with_no_mime_is_not_held(self, extension: str, mime: str | None) -> None:
        """Known limit: the hold reads the event's own labels. The record handler (#6233) routes such an event by the
        record's stored mime, so a stored PDF sent this way still reaches Docling inside its 429 window (its client waits
        the 429 out; nothing is lost). Holding every mime-less light record would hold connector records that never call
        Docling. Follow-up F23g makes admission follow the resolved mime."""
        payload = {"extension": extension} if mime is None else {"extension": extension, "mimeType": mime}
        assert not concurrency.held_downstream(_host(), _message(**payload), frozenset({Pool.INDEX_HEAVY}))

    def test_nothing_is_held_without_a_held_pool(self) -> None:
        assert not concurrency.held_downstream(_host(), _message(extension="pdf", mimeType="application/pdf"), frozenset())

    def test_a_collapsed_light_tier_holds_its_light_records_in_the_heavy_pool(self) -> None:
        message = _message(extension="txt", mimeType="text/plain")
        assert concurrency.held_downstream(_host(light_ceiling=0), message, frozenset({Pool.INDEX_HEAVY}))


class TestHeldPools:
    def test_a_docling_pause_holds_the_heavy_pool(self, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        assert concurrency.held_pools(_host(coordinator)) == {Pool.INDEX_HEAVY}

    def test_a_shared_service_holds_no_pool(self, coordinator) -> None:
        coordinator.signal(PARSING, RETRY_AFTER)
        assert concurrency.held_pools(_host(coordinator)) == frozenset()

    def test_a_stage_consumer_holds_nothing(self, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        assert concurrency.held_pools(_host(coordinator, stage=concurrency.StageAdmission("classify", 4))) == frozenset()


# --- Redis Streams -----------------------------------------------------------------------------------------------------


async def _docling_429(coordinator: BackpressureCoordinator) -> None:
    """A real DoclingClient meets one ``429 Retry-After: 30`` and gives up (its own wait is not under test)."""
    client = DoclingClient(service_url="http://docling.test", timeout=5.0, max_retries=3, retry_delay=0.0,
                           backpressure_coordinator=coordinator)
    client.max_backpressure_attempts = 0
    client._make_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": str(int(RETRY_AFTER))})),
    )
    with patch("app.services.docling.client.get_pdf_page_count", return_value=1), pytest.raises(DoclingUnavailableError):
        await client.parse_pdf_batched("a.pdf", b"%PDF-1.4 x", batch_size=10)


class TestRedisStreams:
    async def test_reads_go_on_through_a_docling_429(self, logger, coordinator, clock, monkeypatch, caplog) -> None:
        consumer, _ = _redis(logger, coordinator)
        await _docling_429(coordinator)
        slept = _advance_on_sleep(monkeypatch, clock)
        with caplog.at_level(logging.WARNING, logger=logger.name):
            await consumer._wait_out_backpressure()
        assert sum(slept) == 0.0
        assert "Downstream backpressure" not in caplog.text

    async def test_light_starts_at_once_and_heavy_only_when_the_pause_ends(self, logger, coordinator, clock) -> None:
        consumer, started = _redis(logger, coordinator)
        await _redis_buffer(consumer, LIGHT, HEAVY)
        coordinator.signal(DOCLING, RETRY_AFTER)
        timeline = []
        for at in (0.0, 29.9, 30.0):
            clock.at(at)
            await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
            timeline.append(list(started))
        assert timeline == [["r-0"], ["r-0"], ["r-0", "r-1"]]

    async def test_a_pdf_route_light_record_is_held(self, logger, coordinator) -> None:
        consumer, started = _redis(logger, coordinator)
        await _redis_buffer(consumer, LIGHT, _payload(1, "txt", "application/pdf"))
        coordinator.signal(DOCLING, RETRY_AFTER)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == ["r-0"]

    async def test_a_docling_429_mid_pass_holds_the_rest_of_the_pass(self, logger, coordinator) -> None:
        consumer, started = _redis(logger, coordinator, on_start=lambda: coordinator.signal(DOCLING, RETRY_AFTER))
        await _redis_buffer(consumer, _payload(0, "txt"), _payload(1, "pdf"), _payload(2, "txt", "application/pdf"),
                            _payload(3, "txt"))
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == ["r-0", "r-3"]

    async def test_a_parsing_429_still_stops_every_read_and_dispatch(self, logger, coordinator, clock, monkeypatch) -> None:
        consumer, started = _redis(logger, coordinator)
        await _redis_buffer(consumer, LIGHT, HEAVY)
        coordinator.signal(PARSING, RETRY_AFTER)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == []
        slept = _advance_on_sleep(monkeypatch, clock)
        await consumer._wait_out_backpressure()
        assert sum(slept) == RETRY_AFTER

    async def test_docling_and_parsing_stop_everything_then_hold_docling_only(self, logger, coordinator, clock) -> None:
        consumer, started = _redis(logger, coordinator)
        await _redis_buffer(consumer, LIGHT, HEAVY)
        coordinator.signal(DOCLING, RETRY_AFTER)
        coordinator.signal(PARSING, 5.0)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == []
        clock.at(5.0)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == ["r-0"]
        clock.at(RETRY_AFTER)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == ["r-0", "r-1"]

    async def test_a_broker_order_consumer_still_waits_out_a_docling_429(self, logger, coordinator, clock, monkeypatch) -> None:
        consumer, _ = _redis(logger, coordinator, fair=False)
        coordinator.signal(DOCLING, RETRY_AFTER)
        slept = _advance_on_sleep(monkeypatch, clock)
        await consumer._wait_out_backpressure()
        assert sum(slept) == RETRY_AFTER

    async def test_a_held_pdf_route_head_holds_its_connectors_light_records(self, logger, coordinator) -> None:
        """Known limit: DRR looks at a leaf's head only, so light records queued behind a held PDF-route one of the same
        connector wait for the pause too. Other connectors flow."""
        consumer, started = _redis(logger, coordinator)
        await _redis_buffer(
            consumer, _payload(0, "txt", "application/pdf", connector="c1"), _payload(1, "txt", connector="c1"),
            _payload(2, "txt", connector="c2"),
        )
        coordinator.signal(DOCLING, RETRY_AFTER)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == ["r-2"]

    async def test_only_held_work_buffered_reads_with_the_short_block(self, logger, coordinator) -> None:
        consumer, _ = _redis(logger, coordinator)
        await _redis_buffer(consumer, HEAVY)
        coordinator.signal(DOCLING, RETRY_AFTER)
        consumer._xreadgroup_grouped = AsyncMock(return_value=[])
        await consumer._IndexingRedisStreamsConsumer__read_phase()
        assert consumer._xreadgroup_grouped.await_args.kwargs["block_ms"] == redis_module._BUSY_BLOCK_MS

    async def test_a_stage_consumer_dispatches_a_pdf_job_through_a_docling_429(self, logger, coordinator) -> None:
        consumer, started = _redis(logger, coordinator, stage_admission=concurrency.StageAdmission("classify", 4))
        await _redis_buffer(consumer, HEAVY)
        coordinator.signal(DOCLING, RETRY_AFTER)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == ["r-1"]

    @pytest.mark.parametrize("service", [PARSING, "ExtractionService"])
    async def test_a_stage_consumer_still_stops_for_a_shared_service(self, logger, coordinator, service) -> None:
        consumer, started = _redis(logger, coordinator, stage_admission=concurrency.StageAdmission("classify", 4))
        await _redis_buffer(consumer, HEAVY)
        coordinator.signal(service, RETRY_AFTER)
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()
        assert started == []


# --- Kafka -------------------------------------------------------------------------------------------------------------


class TestKafka:
    async def test_partitions_stay_read_through_a_docling_429(self, logger, coordinator, caplog) -> None:
        consumer, _ = _kafka(logger, coordinator)
        await _docling_429(coordinator)
        with caplog.at_level(logging.WARNING, logger=logger.name):
            consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_not_called()
        assert "Downstream backpressure" not in caplog.text

    async def test_light_starts_at_once_and_heavy_only_when_the_pause_ends(self, logger, coordinator, clock) -> None:
        consumer, started = _kafka(logger, coordinator)
        await _kafka_buffer(consumer, LIGHT, HEAVY)
        coordinator.signal(DOCLING, RETRY_AFTER)
        timeline = []
        for at in (0.0, 29.9, 30.0):
            clock.at(at)
            await consumer._IndexingKafkaConsumer__dispatch_phase()
            timeline.append(list(started))
        assert timeline == [["r-0"], ["r-0"], ["r-0", "r-1"]]

    async def test_a_docling_429_mid_pass_holds_the_rest_of_the_pass(self, logger, coordinator) -> None:
        consumer, started = _kafka(logger, coordinator, on_start=lambda: coordinator.signal(DOCLING, RETRY_AFTER))
        await _kafka_buffer(consumer, _payload(0, "txt"), _payload(1, "pdf"), _payload(2, "txt", "application/pdf"),
                            _payload(3, "txt"))
        await consumer._IndexingKafkaConsumer__dispatch_phase()
        assert started == ["r-0", "r-3"]

    async def test_a_finished_partition_resumes_through_a_docling_429(self, logger, coordinator) -> None:
        consumer, _ = _kafka(logger, coordinator)
        coordinator.signal(DOCLING, RETRY_AFTER)
        message = MagicMock(topic="record-events", partition=0, offset=0)
        tp = TopicPartition("record-events", 0)
        consumer._in_flight_partitions.add(tp)
        consumer._IndexingKafkaConsumer__finish_partition(message, retry_current=False)
        consumer.consumer.resume.assert_called_once_with(tp)

    async def test_a_parsing_429_still_pauses_every_partition(self, logger, coordinator, caplog) -> None:
        consumer, _ = _kafka(logger, coordinator)
        coordinator.signal(DOCLING, RETRY_AFTER)
        coordinator.signal(PARSING, 5.0)
        with caplog.at_level(logging.WARNING, logger=logger.name):
            consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_called_once()
        assert "Downstream backpressure from ParsingService: pausing Kafka partition reads for 5.0s" in caplog.text

    async def test_a_broker_order_consumer_still_pauses_for_docling(self, logger, coordinator) -> None:
        consumer, _ = _kafka(logger, coordinator, fair=False)
        coordinator.signal(DOCLING, RETRY_AFTER)
        consumer._IndexingKafkaConsumer__apply_backpressure()
        consumer.consumer.pause.assert_called_once()


# --- PostgreSQL queue and the health endpoint --------------------------------------------------------------------------


def test_the_pg_consumer_has_no_downstream_pause(logger) -> None:
    """MESSAGE_BROKER=postgres: the factory hands it no coordinator, so no 429 pauses its claims."""
    consumer = PgMessagingConsumer(logger, RedisStreamsConfig(host="h", port=6379, group_id="g", topics=["t"]), indexing=True)
    assert getattr(consumer, "backpressure_coordinator", None) is None


class TestAdmissionExplain:
    async def test_a_docling_hold_is_not_a_read_pause(self, logger, coordinator) -> None:
        coordinator.signal(DOCLING, RETRY_AFTER)
        for consumer, _ in (_redis(logger, coordinator), _kafka(logger, coordinator)):
            pause = consumer.admission_explain()["readPause"]
            assert pause["downstream"] is False, pause
            assert pause["pausedServices"] == [DOCLING]
            assert pause["pauseRemainingSec"] == RETRY_AFTER
            assert pause["heldPools"] == [Pool.INDEX_HEAVY.value]

    async def test_a_parsing_pause_is_a_read_pause_with_nothing_held(self, logger, coordinator) -> None:
        coordinator.signal(PARSING, RETRY_AFTER)
        pause = _redis(logger, coordinator)[0].admission_explain()["readPause"]
        assert pause["downstream"] is True and pause["heldPools"] == [], pause
