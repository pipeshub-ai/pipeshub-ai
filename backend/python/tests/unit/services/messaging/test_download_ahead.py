"""A heavy record waiting for a parse slot has not downloaded its file.

The index pool admits about twice as many records as the parse pool, and a
record used to download before it asked for its parse permit, so every record
past the parse slots sat on a whole file for as long as the parses ahead of it
took. Each test here fails on the code before the fix.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

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
from app.services.resource_governor.download_ahead import DownloadAhead
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("test-download-ahead")


@pytest.fixture(autouse=True)
def _heavy_starts_are_not_paced(monkeypatch) -> None:
    """The governor spaces heavy parse starts 2 s apart; these tests are about who waits for a slot."""
    monkeypatch.setattr(
        "app.services.resource_governor.controller.start_rate_limiter_params", lambda _ceiling: (0.001, 100),
    )


def _consumer(*, heavy_parse: int = 2) -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        LOGGER,
        RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"]),
        retry_manager=None,
        producer=None,
        governor=make_test_governor(logger_name="test-download-ahead", env_parse=heavy_parse),
    )
    # The index pool admits more records than the parse pool, as it does in production.
    registry = consumer.governor._registry
    registry.set(Pool.HEAVY_PARSE, heavy_parse)
    registry.set(Pool.INDEX_HEAVY, 8)
    consumer.running = True
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    consumer._ack_message = AsyncMock()
    consumer._requeue_message = AsyncMock()
    return consumer


def _fields(record_id: str, extension: str = "pdf") -> dict[str, str]:
    return {"value": json.dumps({"eventType": "newRecord", "payload": {"recordId": record_id, "extension": extension}})}


class _Fleet:
    """Records that download, wait for a parse slot, then parse until released."""

    def __init__(self, tier: ParseTier = ParseTier.HEAVY) -> None:
        self.tier = tier
        self.downloaded: set[str] = set()
        self.parsing: set[str] = set()
        self.most_waiting_with_content = 0
        self.most_places_used = 0
        self.ahead = None
        self.release_parse = asyncio.Event()

    async def handler(self, message) -> AsyncGenerator[PipelineEvent, None]:
        record_id = message.payload["recordId"]
        yield PipelineEvent(event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id=record_id))
        self.downloaded.add(record_id)
        self._note()
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(record_id=record_id, tier=self.tier, size_bytes=1),
        )
        self.parsing.add(record_id)
        self._note()
        await self.release_parse.wait()
        self.downloaded.discard(record_id)
        self.parsing.discard(record_id)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))

    def _note(self) -> None:
        if self.ahead is not None:
            self.most_places_used = max(self.most_places_used, self.ahead.stats()["in_use"])
        waiting = len(self.downloaded - self.parsing)
        self.most_waiting_with_content = max(self.most_waiting_with_content, waiting)


async def _run(consumer, fleet: _Fleet, count: int, extension: str = "pdf") -> list[bool]:
    consumer.message_handler = fleet.handler
    fleet.ahead = consumer.governor.download_ahead
    tasks = [
        asyncio.create_task(consumer._process_message_wrapper("t", f"{i}-0", _fields(f"r{i}", extension)))
        for i in range(count)
    ]
    await asyncio.sleep(0.3)
    fleet.release_parse.set()
    return list(await asyncio.gather(*tasks))


class TestHeavyRecordsWaitingForAParseSlot:
    async def test_only_a_few_hold_a_file_while_the_rest_wait_for_a_slot(self) -> None:
        consumer = _consumer(heavy_parse=2)
        limit = consumer.governor.download_ahead.limit
        fleet = _Fleet()

        results = await _run(consumer, fleet, count=8)

        assert results == [True] * 8
        assert 0 < limit < 8 - 2
        assert fleet.most_waiting_with_content <= limit

    async def test_every_place_is_handed_back_whether_the_record_parsed_or_waited_too_long(self, monkeypatch) -> None:
        consumer = _consumer(heavy_parse=1)
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.05)
        fleet = _Fleet()

        await _run(consumer, fleet, count=6)

        assert fleet.most_places_used >= 1
        assert consumer.governor.download_ahead.stats()["in_use"] == 0

    async def test_a_record_that_waits_too_long_for_a_place_is_requeued_without_downloading(self, monkeypatch) -> None:
        consumer = _consumer(heavy_parse=2)
        ahead = consumer.governor.download_ahead
        for _ in range(ahead.limit):
            assert await ahead.acquire()
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.05)
        fleet = _Fleet()
        consumer.message_handler = fleet.handler

        assert await consumer._process_message_wrapper("t", "1-0", _fields("r1")) is False

        assert fleet.downloaded == set()
        consumer._requeue_message.assert_awaited_once()

    async def test_light_records_are_not_held_back(self) -> None:
        consumer = _consumer()
        fleet = _Fleet(tier=ParseTier.LIGHT)

        results = await _run(consumer, fleet, count=6, extension="txt")

        assert results == [True] * 6
        assert fleet.most_places_used == 0


class TestDownloadAhead:
    async def test_places_are_handed_out_in_arrival_order(self) -> None:
        ahead = DownloadAhead(1)
        assert await ahead.acquire()
        order: list[int] = []

        async def wait(n: int) -> None:
            assert await ahead.acquire()
            order.append(n)
            ahead.release()

        tasks = [asyncio.create_task(wait(n)) for n in range(3)]
        await asyncio.sleep(0)
        ahead.release()
        await asyncio.gather(*tasks)

        assert order == [0, 1, 2]

    async def test_a_wait_gives_up_after_its_timeout(self) -> None:
        ahead = DownloadAhead(1)
        assert await ahead.acquire()

        assert await ahead.acquire(timeout=0.01) is False
        assert ahead.stats() == {"limit": 1, "in_use": 1}

    async def test_a_limit_of_zero_turns_the_bound_off(self) -> None:
        ahead = DownloadAhead(0)

        assert [await ahead.acquire() for _ in range(5)] == [True] * 5
        ahead.release()
