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


@pytest.mark.parametrize("module", [
    "app.services.messaging.redis_streams.indexing_consumer",
    "app.services.messaging.kafka.consumer.indexing_consumer",
])
def test_both_consumers_fold_every_inspected_fact_into_the_class_context(module: str) -> None:
    """The Kafka consumer's loop is too heavy to drive here; this pins that it passes pages, the scan flag and the
    cell area on, exactly as the Redis consumer does (the behavioural tests above)."""
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(module))
    assert "class_ctx.inspected(event.data.pages, scanned=event.data.scanned, cells=event.data.cells)" in source
