"""The scan for this consumer's own unheld pending entries reaches every one
of them in turn, whatever keeps failing ahead of it.

Each scan is bounded (a page budget and a limit on what it returns). Entries
at the head of the pending list that keep failing -- a handoff that raises, or
a trimmed entry whose acknowledgement fails -- used to fill every scan, which
started again from the head, so an entry behind them was never reached.
"""
from __future__ import annotations

import asyncio
import json
import math
from logging import getLogger

import pytest

pytest.importorskip("fakeredis.aioredis")

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams import indexing_consumer as module
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

GROUP = "records_consumer_group"
LANE = "record-events.0"


def _fields(record_id: str) -> dict[str, str]:
    payload = {"recordId": record_id, "orgId": "org-1", "connectorId": f"conn-{record_id}"}
    return {"value": json.dumps({"eventType": "newRecord", "payload": payload})}


async def _consumer() -> IndexingRedisStreamsConsumer:
    provider = FakeRedisConnectionProvider(is_cluster=False)
    consumer = IndexingRedisStreamsConsumer(
        getLogger("test_own_pending_scan"),
        RedisStreamsConfig(topics=[LANE], group_id=GROUP, client_id="live", batch_size=1),
        fair_scheduler_config=FairSchedulerConfig(
            enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=1,
            max_per_entity_messages=1,
        ),
        provider=provider,
    )
    consumer.redis = provider.create_client()
    consumer.running = True
    await consumer.redis.xgroup_create(LANE, GROUP, id="0", mkstream=True)
    return consumer


async def _pending_here(consumer, kinds: list[str]) -> list[str]:
    """Entries pending under this consumer, held by no one; "gone" ones trimmed."""
    for i in range(len(kinds)):
        await consumer.redis.xadd(LANE, _fields(f"r-{i}"))
    read = await consumer.redis.xreadgroup(GROUP, consumer.consumer_name, {LANE: ">"})
    ids = [message_id for _s, entries in read for message_id, _f in entries]
    for message_id, kind in zip(ids, kinds, strict=True):
        if kind == "gone":
            await consumer.redis.xdel(LANE, message_id)
    return ids


def _acks_fail_for(consumer, ids: set[str]) -> None:
    real = consumer.redis.xack

    async def xack(stream, group, *message_ids):  # noqa: ANN202
        if set(message_ids) & ids:
            raise ConnectionError("lost the connection")
        return await real(stream, group, *message_ids)

    consumer.redis.xack = xack


async def _scans_until_reached(consumer, wanted: set[str], ok: set[str], limit: int, scans: int) -> set[str]:
    """Run ``scans`` scans; entries in ``ok`` are handed on (acknowledged here),
    any other entry returned stays pending, failing again next time."""
    reached: set[str] = set()
    scan = consumer._IndexingRedisStreamsConsumer__own_unheld_pending_payloads
    for _ in range(scans):
        for message_id, _payload in await scan(LANE, limit=limit, ignore_budget=True):
            if message_id in wanted:
                reached.add(message_id)
            if message_id in ok:
                await consumer.redis.xack(LANE, GROUP, message_id)
    return reached


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(
    kinds=st.lists(st.sampled_from(["ok", "fails", "gone"]), min_size=1, max_size=40),
    page=st.integers(min_value=1, max_value=6),
    pages=st.integers(min_value=1, max_value=4),
    limit=st.integers(min_value=1, max_value=4),
)
def test_every_recoverable_entry_is_reached_while_the_failures_persist(
    kinds: list[str], page: int, pages: int, limit: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Random pending lists of recoverable entries, entries whose handoff keeps
    failing, and trimmed entries whose acknowledgement keeps failing, with
    random page sizes, page budgets and return limits."""
    monkeypatch.setattr(module, "_PENDING_SCAN_PAGE", page)
    monkeypatch.setattr(module, "_PENDING_SCAN_MAX_PAGES", pages)

    async def run() -> None:
        consumer = await _consumer()
        ids = await _pending_here(consumer, kinds)
        by_kind = {kind: {m for m, k in zip(ids, kinds, strict=True) if k == kind} for kind in kinds}
        _acks_fail_for(consumer, by_kind.get("gone", set()))
        budget = consumer._IndexingRedisStreamsConsumer__pending_scan_pages(LANE) * page
        # A scan examines at least min(its page budget, `limit` returned
        # entries) unless it reaches the end; one more for the wrap.
        scans = math.ceil(len(ids) / min(budget, limit)) + 1
        wanted = by_kind.get("ok", set())

        reached = await _scans_until_reached(consumer, wanted, wanted, limit, scans)

        assert reached == wanted

    asyncio.run(run())


async def test_more_than_a_budget_of_trimmed_entries_failing_their_ack_with_the_retry_list_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry list is full, more than one scan budget of trimmed entries at
    the head keeps failing its acknowledgement, and a recoverable entry sits
    behind them. Driven through the read pass beside a live peer."""
    monkeypatch.setattr(module, "_PENDING_SCAN_PAGE", 2)
    monkeypatch.setattr(module, "_PENDING_SCAN_MAX_PAGES", 2)
    monkeypatch.setattr(module, "_UNHANDED_LIMIT", 1)
    consumer = await _consumer()
    consumer.fair_scheduler_config = FairSchedulerConfig(
        enabled=True, key_fields=("orgId", "connectorId"), max_buffered_messages=8,
        max_per_entity_messages=8,
    )
    consumer._IndexingRedisStreamsConsumer__pending_scan_pages = lambda _topic: 2
    await consumer.redis.xreadgroup(GROUP, "live-peer", {LANE: ">"}, count=1)
    ids = await _pending_here(consumer, ["gone"] * 9 + ["ok"])
    _acks_fail_for(consumer, set(ids[:9]))
    consumer._unhanded = {(LANE, "0-1"): None}

    for _ in range(8):
        await consumer.redis.xreadgroup(GROUP, "live-peer", {LANE: ">"}, count=1)
        consumer._last_recovery = 0.0
        consumer._last_ownership_refresh = 0.0
        await consumer._IndexingRedisStreamsConsumer__read_phase()
        if any(m == ids[9] for _s, m in consumer._held_entries):
            break

    assert any(m == ids[9] for _s, m in consumer._held_entries)
