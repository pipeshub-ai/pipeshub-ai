"""The consumer side of reclassifying by inspection, driven through ``_process_message_wrapper`` with a fake handler: the START_PARSING
that follows START_INSPECT moves the record's index permit to the class its page count says, in place, and a big record
that waits out its turn is requeued carrying the time it first queued.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.capacity.classes import CostClass
from app.modules.parse_engine import wiring
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("test-inspection-reclass")
MIB = 1 << 20


@pytest.fixture(autouse=True)
def _classes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.delenv("PARSE_LANES", raising=False)
    monkeypatch.setattr(
        "app.services.resource_governor.controller.start_rate_limiter_params", lambda _ceiling: (0.001, 100)
    )
    wiring.reset_for_tests()


def _consumer(heavy_parse: int = 2) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        LOGGER,
        RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"]),
        retry_manager=None,
        producer=None,
        governor=make_test_governor(logger_name="test-inspection-reclass", env_parse=heavy_parse),
    )
    registry = consumer.governor._registry
    registry.set(Pool.HEAVY_PARSE, heavy_parse)
    registry.set(Pool.INDEX_HEAVY, 8)
    consumer.running = True
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    consumer._ack_message = AsyncMock()
    consumer._requeue_message = AsyncMock()
    return consumer


def _fields(record_id: str, extension: str = "pdf") -> dict[str, str]:
    # No size in the event: the dispatch prior is small.
    mime = "application/pdf" if extension == "pdf" else ""
    payload = {"recordId": record_id, "extension": extension, "mimeType": mime, "orgId": "o1"}
    return {"value": json.dumps({"eventType": "newRecord", "payload": payload})}


class _Fleet:
    def __init__(self, pages: int | None, cells: int | None = None) -> None:
        self.pages = pages
        self.cells = cells
        self.seen: list[tuple[int, int, int]] = []  # (small, large, huge) running at the index gate while parsing
        self.release = asyncio.Event()
        self.gate: object = None

    async def handler(self, message) -> AsyncGenerator[PipelineEvent, None]:
        rid = message.payload["recordId"]
        yield PipelineEvent(event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id=rid))
        yield PipelineEvent(event=IndexingEvent.START_INSPECT, data=PipelineEventData(record_id=rid, size_bytes=80 * MIB))
        extra = {} if self.pages is None else {"pages": self.pages, "scanned": False}
        if self.cells is not None:
            extra = {"cells": self.cells}
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(record_id=rid, tier=ParseTier.HEAVY, size_bytes=80 * MIB, **extra),
        )
        gate = self.gate() if callable(self.gate) else None
        if gate is not None:
            self.seen.append(tuple(gate.running(c) for c in (CostClass.SMALL, CostClass.LARGE, CostClass.HUGE)))  # type: ignore[arg-type]
        await self.release.wait()
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=rid))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=rid))


async def _run_one(consumer: IndexingRedisStreamsConsumer, fleet: _Fleet, extension: str = "pdf") -> bool:
    consumer.message_handler = fleet.handler
    fleet.gate = lambda: wiring.index_gate(consumer.governor)
    task = asyncio.create_task(consumer._process_message_wrapper("t", "1-0", _fields("r1", extension)))
    await asyncio.sleep(0.4)
    fleet.release.set()
    return await task


async def test_an_inspected_thousand_page_pdf_runs_as_huge_without_a_requeue() -> None:
    consumer = _consumer()
    fleet = _Fleet(pages=1000)
    assert await _run_one(consumer, fleet) is True
    assert fleet.seen == [(0, 0, 1)]  # the index permit moved from small (its prior) to huge, in place
    consumer._requeue_message.assert_not_awaited()


async def test_without_inspected_pages_the_record_stays_on_its_prior() -> None:
    consumer = _consumer()
    fleet = _Fleet(pages=None)
    assert await _run_one(consumer, fleet) is True
    assert fleet.seen == [(1, 0, 0)]


async def test_a_big_record_that_waits_out_its_turn_is_requeued_with_its_first_admission_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = _consumer(heavy_parse=1)
    monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.3)
    fleet = _Fleet(pages=1000)
    consumer.message_handler = fleet.handler
    first = asyncio.create_task(consumer._process_message_wrapper("t", "1-0", _fields("r1")))
    await asyncio.sleep(0.3)  # r1 holds the one huge CPU parse
    second = await consumer._process_message_wrapper("t", "2-0", _fields("r2"))
    fleet.release.set()
    await first
    assert second is False
    (call,) = [c for c in consumer._requeue_message.await_args_list if c.args[2:3] != ()][-1:]
    assert call.kwargs["parse_enqueued_at_ms"] > 0


async def test_an_xlsx_cell_area_moves_a_sizeless_workbook_out_of_small_in_place() -> None:
    """The START_PARSING of a workbook carries the cell area; the consumer folds it into the class context."""
    consumer = _consumer()
    fleet = _Fleet(pages=None, cells=26 * 400_000)  # 10.4 million cells in an 80 MiB workbook
    assert await _run_one(consumer, fleet, "xlsx") is True
    assert fleet.seen == [(0, 1, 0)]
    consumer._requeue_message.assert_not_awaited()


async def test_an_xlsx_without_a_cell_area_stays_on_its_prior() -> None:
    consumer = _consumer()
    fleet = _Fleet(pages=None)
    assert await _run_one(consumer, fleet, "xlsx") is True
    assert fleet.seen == [(1, 0, 0)]


def _kafka_consumer():  # noqa: ANN202
    from unittest.mock import MagicMock

    from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
    from app.services.messaging.kafka.consumer.indexing_consumer import (
        IndexingKafkaConsumer,
    )

    cfg = KafkaConsumerConfig(
        topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest", enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
    )
    c = IndexingKafkaConsumer(LOGGER, cfg, governor=make_test_governor(logger_name="test-kafka-reclass", env_parse=2))
    c.governor._registry.set(Pool.HEAVY_PARSE, 2)
    c.governor._registry.set(Pool.INDEX_HEAVY, 8)
    c.consumer = MagicMock()
    c.consumer.commit = AsyncMock()
    c.running = True
    return c


async def _kafka_run(cells: int | None) -> list[tuple[int, int, int]]:
    from unittest.mock import MagicMock

    consumer = _kafka_consumer()
    seen: list[tuple[int, int, int]] = []

    async def handler(message) -> AsyncGenerator[PipelineEvent, None]:
        rid = message.payload["recordId"]
        yield PipelineEvent(event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id=rid))
        yield PipelineEvent(event=IndexingEvent.START_INSPECT, data=PipelineEventData(record_id=rid, size_bytes=80 * MIB))
        extra = {} if cells is None else {"cells": cells}
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(record_id=rid, tier=ParseTier.HEAVY, size_bytes=80 * MIB, **extra),
        )
        gate = wiring.index_gate(consumer.governor)
        seen.append(tuple(gate.running(k) for k in (CostClass.SMALL, CostClass.LARGE, CostClass.HUGE)))  # type: ignore[arg-type]
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=rid))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=rid))

    consumer.message_handler = handler
    payload = {"recordId": "r1", "extension": "xlsx", "mimeType": "", "orgId": "o1"}
    message = MagicMock()
    message.topic, message.partition, message.offset = "record-events", 0, 3
    message.value = json.dumps({"eventType": "newRecord", "payload": payload}).encode()
    await consumer._IndexingKafkaConsumer__process_message_wrapper(message)
    return seen


async def test_the_kafka_consumer_folds_an_xlsx_cell_area_into_the_class_too() -> None:
    assert await _kafka_run(26 * 400_000) == [(0, 1, 0)]  # moved out of small in place
    assert await _kafka_run(None) == [(1, 0, 0)]  # no cell area: it stays on its prior


def _download_use() -> dict[str, int]:
    return {n: a.stats()["in_use"] for n, a in wiring._Registry.ahead.get(asyncio.get_running_loop(), {}).items()}


async def _until(condition, timeout: float = 10.0) -> bool:
    """Poll a condition on observable state (bounded): readiness is what the system reports, not elapsed time."""
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


def _reclassed(use: dict[str, int]) -> bool:
    return use.get("small", 0) == 0 and use.get("big") == 1


async def test_the_redis_consumer_moves_the_download_place_when_inspection_makes_a_record_huge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second record waits for the one parse slot holding a download place: after inspection it is a huge record's."""
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD", "2")
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", "2")
    wiring.reset_for_tests()
    consumer = _consumer(heavy_parse=1)
    fleet = _Fleet(pages=1000)
    consumer.message_handler = fleet.handler
    first = asyncio.create_task(consumer._process_message_wrapper("t", "1-0", _fields("r1")))
    second = None
    try:
        gate = consumer.governor.gate(Pool.HEAVY_PARSE)
        assert await _until(lambda: gate.in_use >= 1), "r1 never took the one parse slot"
        second = asyncio.create_task(consumer._process_message_wrapper("t", "2-0", _fields("r2")))
        # r2 is inspected and waits for the parse slot, holding its download place: wait for what that looks like
        await _until(lambda: _reclassed(_download_use()))
        use = _download_use()
    finally:
        fleet.release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
    assert _reclassed(use), use  # a small place held by a huge record blocks small PDFs


async def test_the_kafka_consumer_moves_the_download_place_when_inspection_makes_a_record_huge(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD", "2")
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", "2")
    wiring.reset_for_tests()
    consumer = _kafka_consumer()
    consumer.governor._registry.set(Pool.HEAVY_PARSE, 1)
    release = asyncio.Event()

    async def handler(message) -> AsyncGenerator[PipelineEvent, None]:
        rid = message.payload["recordId"]
        yield PipelineEvent(event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id=rid))
        yield PipelineEvent(event=IndexingEvent.START_INSPECT, data=PipelineEventData(record_id=rid, size_bytes=80 * MIB))
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(record_id=rid, tier=ParseTier.HEAVY, size_bytes=80 * MIB, pages=1000, scanned=False),
        )
        await release.wait()
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=rid))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=rid))

    consumer.message_handler = handler

    def message(rid: str, offset: int) -> object:
        m = MagicMock()
        m.topic, m.partition, m.offset = "record-events", 0, offset
        payload = {"recordId": rid, "extension": "pdf", "mimeType": "application/pdf", "orgId": "o1"}
        m.value = json.dumps({"eventType": "newRecord", "payload": payload}).encode()
        return m

    wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper
    first = asyncio.create_task(wrapper(message("r1", 1)))
    second = None
    try:
        gate = consumer.governor.gate(Pool.HEAVY_PARSE)
        assert await _until(lambda: gate.in_use >= 1), "r1 never took the one parse slot"
        second = asyncio.create_task(wrapper(message("r2", 2)))
        await _until(lambda: _reclassed(_download_use()))
        use = _download_use()
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
    assert _reclassed(use), use
