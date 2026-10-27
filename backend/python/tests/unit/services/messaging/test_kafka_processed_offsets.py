"""The settled-offset dedup record must stay bounded and O(1).

It exists to skip a message that is redelivered after it was settled (a
rebalance handing back an offset, a seek, a commit that failed). Only recent
offsets can be redelivered, so a bounded window per partition keeps that
guarantee without growing for the life of the process.
"""

import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import (
    SETTLED_OFFSET_WINDOW,
    KafkaMessagingConsumer,
    _SettledOffsets,
)

TP = "topic-a-0"


def _consumer(commit=None) -> KafkaMessagingConsumer:
    cfg = KafkaConsumerConfig(
        topics=["topic-a"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["b:9092"], ssl=False, sasl=None,
    )
    c = KafkaMessagingConsumer(logging.getLogger("t"), cfg)
    c.consumer = MagicMock()
    c.consumer.commit = commit or AsyncMock()
    return c


def _msg(offset: int) -> SimpleNamespace:
    return SimpleNamespace(topic="topic-a", partition=0, offset=offset,
                           value=json.dumps({"eventType": "e", "payload": {}}).encode())


async def _settle(c: KafkaMessagingConsumer, offset: int) -> None:
    await c._KafkaMessagingConsumer__commit_settled(
        "topic-a-0-tp", _msg(offset), f"{TP}-{offset}"
    )


def _is_processed(c, offset: int) -> bool:
    return c._KafkaMessagingConsumer__is_message_processed(f"{TP}-{offset}")


def _tracked(c) -> int:
    return sum(len(v) for v in c.processed_messages.values())


@pytest.mark.asyncio
async def test_structure_stays_bounded_after_many_messages() -> None:
    c = _consumer()
    for offset in range(SETTLED_OFFSET_WINDOW * 5):
        await _settle(c, offset)
    assert _tracked(c) == SETTLED_OFFSET_WINDOW


@pytest.mark.asyncio
async def test_recent_offsets_are_still_skipped_on_redelivery() -> None:
    c = _consumer()
    for offset in range(SETTLED_OFFSET_WINDOW * 3):
        await _settle(c, offset)
    last = SETTLED_OFFSET_WINDOW * 3 - 1
    assert _is_processed(c, last)
    assert _is_processed(c, last - SETTLED_OFFSET_WINDOW + 1)
    assert not _is_processed(c, 0)  # aged out of the window


@pytest.mark.asyncio
async def test_uncommitted_settled_offset_is_still_skipped_on_redelivery() -> None:
    c = _consumer(commit=AsyncMock(side_effect=RuntimeError("broker down")))
    await _settle(c, 7)
    assert _is_processed(c, 7)


@pytest.mark.asyncio
async def test_marking_the_same_offset_twice_does_not_use_two_slots() -> None:
    c = _consumer()
    for _ in range(3):
        c._KafkaMessagingConsumer__mark_message_processed(f"{TP}-5")
    assert _tracked(c) == 1


@pytest.mark.asyncio
async def test_partitions_are_tracked_independently() -> None:
    c = _consumer()
    c._KafkaMessagingConsumer__mark_message_processed("topic-a-1-5")
    for offset in range(SETTLED_OFFSET_WINDOW * 2):
        await _settle(c, offset)
    assert c._KafkaMessagingConsumer__is_message_processed("topic-a-1-5")


@pytest.mark.asyncio
async def test_lookup_cost_does_not_grow_with_messages_seen() -> None:
    def lookup_cost(c) -> float:
        start = time.process_time()
        for i in range(3000):
            _is_processed(c, 10**9 + i)  # absent offset: worst case for a scan
        return time.process_time() - start

    fresh = _consumer()
    await _settle(fresh, 0)
    busy = _consumer()
    for offset in range(60_000):
        await _settle(busy, offset)
    base = min(lookup_cost(fresh) for _ in range(3))
    grown = min(lookup_cost(busy) for _ in range(3))
    # A linear scan of 60k entries is ~1000x slower than one entry.
    assert grown < max(base * 5, 0.01)


def test_a_replay_from_beyond_the_window_reprocesses_only_what_the_window_lost() -> None:
    """Re-adding offsets older than the window must not evict the ones about to be replayed."""
    window = _SettledOffsets(SETTLED_OFFSET_WINDOW)
    total = 10_000
    for offset in range(total):
        window.add(offset)
    start = total - SETTLED_OFFSET_WINDOW - 1000

    handled = 0
    for offset in range(start, total):
        if offset not in window:
            handled += 1
            window.add(offset)

    assert handled == 1000
    assert len(window) == SETTLED_OFFSET_WINDOW
