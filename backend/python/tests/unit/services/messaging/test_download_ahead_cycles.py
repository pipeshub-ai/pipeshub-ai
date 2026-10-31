"""Bounded download-ahead on both indexing consumers: random interleavings, and no
wait cycle between the memory reservation and the download place.

Independent sampler: the handler itself counts records holding file bytes (from
the moment the consumer lets START_DOWNLOAD through until PARSING_COMPLETE / end).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("test-download-ahead-interleavings")
SEEDS = [9101, 9102, 9103]


@pytest.fixture(autouse=True)
def _unpaced(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.resource_governor.controller.start_rate_limiter_params", lambda _c: (0.001, 100),
    )


class Counting:
    def __init__(self, ahead) -> None:
        self.takes = 0
        self.returns = 0
        orig_acq, orig_rel = ahead.acquire, ahead.release

        async def acquire(timeout: float | None = None) -> bool:
            ok = await orig_acq(timeout)
            if ok:
                self.takes += 1
            return ok

        def release() -> None:
            self.returns += 1
            orig_rel()
        ahead.acquire, ahead.release = acquire, release


def build(kind: str, parse: int = 2, ahead: int | None = None) -> Any:  # noqa: ANN401
    gov = make_test_governor(logger_name="qa", env_parse=parse)
    if ahead is not None:
        from app.services.resource_governor.download_ahead import DownloadAhead
        gov.download_ahead = DownloadAhead(ahead)
    reg = gov._registry
    reg.set(Pool.HEAVY_PARSE, parse)
    reg.set(Pool.INDEX_HEAVY, 8)
    if kind == "redis":
        c = IndexingRedisStreamsConsumer(
            LOGGER, RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"]),
            retry_manager=None, producer=None, governor=gov)
        c._pending_message_is_owned = AsyncMock(return_value=True)
        c._ack_message = AsyncMock()
        c._requeue_message = AsyncMock()
    else:
        c = IndexingKafkaConsumer(
            LOGGER, KafkaConsumerConfig(topics=["t"], client_id="c", group_id="g", bootstrap_servers=["k:9092"], auto_offset_reset="earliest", enable_auto_commit=False),
            retry_manager=None, producer=None, governor=gov)
        c._IndexingKafkaConsumer__resolve_offset = AsyncMock()
        c._IndexingKafkaConsumer__commit_if_appropriate = AsyncMock()
    c.running = True
    return c


def call(kind: str, c: Any, rid: str, ext: str) -> Any:  # noqa: ANN401
    env = {"eventType": "newRecord", "payload": {"recordId": rid, "extension": ext}}
    if kind == "redis":
        return c._process_message_wrapper("t", f"{rid}-0", {"value": json.dumps(env)})
    m = MagicMock()
    m.topic, m.partition, m.offset = "t", 0, int(rid[1:] or 0) if rid[1:].isdigit() else 0
    m.value = json.dumps(env).encode()
    return c._IndexingKafkaConsumer__process_message_wrapper(m)


class World:
    def __init__(self, rng, release_parse) -> None:
        self.rng = rng
        self.holding: set[str] = set()
        self.max_holding = 0
        self.release_parse = release_parse

    def note(self) -> None:
        self.max_holding = max(self.max_holding, len(self.holding))

    async def handler(self, message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
        rid = message.payload["recordId"]
        mode = self.rng.choice(["ok", "ok", "ok", "dl_fail", "dedup", "parse_fail", "end_silent", "slow"])
        light = message.payload.get("extension") == "txt"
        yield PipelineEvent(event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id=rid))
        try:
            if mode == "slow":
                await asyncio.sleep(self.rng.random() * 0.05)
            if mode == "dl_fail":
                raise RuntimeError("download failed")
            if not light:
                self.holding.add(rid)
            self.note()
            if mode == "dedup":
                self.holding.discard(rid)
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=rid))
                return
            if mode == "end_silent":
                return
            yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(
                record_id=rid, tier=ParseTier.LIGHT if light else ParseTier.HEAVY, size_bytes=1))
            if mode == "parse_fail":
                raise RuntimeError("parse failed")
            await asyncio.sleep(self.rng.random() * 0.03)
            self.holding.discard(rid)
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=rid))
            await asyncio.sleep(0.005)
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=rid))
        finally:
            self.holding.discard(rid)


@pytest.mark.parametrize("kind", ["redis", "kafka"])
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("ahead", [None, 1, 0])
async def test_random_interleavings_do_not_leak_or_deadlock(kind: str, seed: int, ahead: int | None) -> None:
    rng = random.Random(seed)
    parse = 2
    c = build(kind, parse=parse, ahead=ahead)
    counting = Counting(c.governor.download_ahead)
    limit = c.governor.download_ahead.limit
    world = World(rng, None)
    c.message_handler = world.handler
    n = 24
    tasks = []
    for i in range(n):
        ext = rng.choice(["pdf", "pdf", "pdf", "docx", "txt"])
        t = asyncio.create_task(call(kind, c, f"r{i}", ext))
        tasks.append(t)
        await asyncio.sleep(rng.random() * 0.003)
    # cancel a random few mid-flight
    await asyncio.sleep(0.02)
    for t in rng.sample(tasks, 5):
        t.cancel()
    done, pending = await asyncio.wait(tasks, timeout=30)
    for p in pending:
        p.cancel()
    assert not pending, f"{len(pending)} records stuck (deadlock/starvation)"
    sem = c.governor.download_ahead._semaphore
    assert counting.takes == counting.returns, (counting.takes, counting.returns)
    assert c.governor.download_ahead.stats()["in_use"] == 0
    if sem is not None:
        assert sem._value == limit, "double return" if sem._value > limit else "leak"
    if limit:
        assert world.max_holding <= parse + limit, (world.max_holding, parse, limit)


GIB = 1024 ** 3


@pytest.mark.parametrize("kind", ["redis", "kafka"])
@pytest.mark.parametrize("ahead", [1, 0])
async def test_memory_reservation_and_download_place_do_not_wait_on_each_other(
    kind: str, ahead: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails with a place limit of 1 if the memory is reserved before the place is taken.

    A (size unknown before the download) holds the only place and needs memory at START_PARSING;
    B (size known, big) reserved that memory at START_DOWNLOAD and now waits for A's place.
    Each holds what the other needs; progress must not wait for the admission timeout."""
    c = build(kind, parse=2, ahead=ahead)
    c.governor.memory_budget.limit = 20 * GIB
    monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 3.0)
    order: list[str] = []

    async def handler(message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
        rid = message.payload["recordId"]
        known = rid == "B"
        yield PipelineEvent(event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(
            record_id=rid, size_bytes=3 * GIB if known else None))
        await asyncio.sleep(0.2)
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(
            record_id=rid, tier=ParseTier.HEAVY, size_bytes=5 * GIB if rid == "A" else 3 * GIB))
        order.append(rid)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=rid))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=rid))

    c.message_handler = handler
    ta = asyncio.create_task(call(kind, c, "A", "pdf"))
    await asyncio.sleep(0.05)
    tb = asyncio.create_task(call(kind, c, "B", "pdf"))
    await asyncio.sleep(1.0)
    stuck = sorted(set("AB") - set(order))
    await asyncio.wait([ta, tb], timeout=10)
    assert not stuck, f"{stuck} had made no progress after 1s of an idle host (waited for the 3s admission timeout)"
