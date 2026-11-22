"""QA: adversarial cases for the record title consumer that the main suites do not pin."""
from __future__ import annotations

from typing import Any

import pytest

from app.modules.indexing.record_title_consumer import MAX_REQUEUES
from app.services.messaging.config import StreamMessage
from app.services.messaging.record_titles import clean_record_title
from tests.unit.modules.indexing.test_record_title_consumer import (
    FakeGraph,
    FakeStore,
    _feed,
    _msg,
    _record,
    _writer,
)

pytestmark = pytest.mark.asyncio


async def test_a_permanently_failing_embedder_gives_up_after_the_requeue_cap() -> None:
    """Re-queue loop: the event comes back MAX_REQUEUES times, then is dropped (no infinite loop)."""
    graph, store = FakeGraph(), FakeStore()
    graph.records["r1"] = _record()
    store.failed_next = [1] * 1000
    topic: list[dict[str, Any]] = []

    async def requeue(messages: list[dict[str, Any]]) -> bool:
        topic.extend(messages)
        return True

    writer = _writer(graph, store, requeue=requeue, max_attempts=2)
    await _feed(writer, _msg("r1"))
    rounds = 1
    while topic and rounds < 50:
        batch, topic[:] = list(topic), []
        await _feed(writer, *(StreamMessage(eventType=m["eventType"], payload=m["payload"]) for m in batch))
        rounds += 1
    assert rounds == MAX_REQUEUES + 1
    assert not topic


async def test_a_forged_future_upsert_for_a_trashed_record_writes_nothing() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.records["r1"] = _record(deleted=True)
    await _feed(_writer(graph, store), _msg("r1", ts=2**62))
    assert store.written() == []


async def test_a_forged_huge_attempt_is_dropped_not_requeued_forever() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.records["r1"] = _record()
    store.failed_next = [1] * 100
    sent: list[Any] = []

    async def requeue(messages: list[dict[str, Any]]) -> bool:
        sent.extend(messages)
        return True

    msg = _msg("r1")
    msg.payload["attempt"] = 10**9
    await _feed(_writer(graph, store, requeue=requeue, max_attempts=1), msg)
    assert sent == []


@pytest.mark.parametrize("name", ["​", "​​", "\x00", "﻿"])
async def test_invisible_only_names_are_not_blank_to_the_cleaner(name: str) -> None:
    # Documents current behaviour: zero-width-only names are embedded (not str.strip()-blank).
    assert clean_record_title(name) is not None


async def test_a_name_of_10k_chars_with_a_lone_surrogate_does_not_crash_the_write() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.records["r1"] = _record(name=("a" * 5000) + "\ud800" + ("b" * 5000))
    await _feed(_writer(graph, store), _msg("r1"))
    assert len(store.written()) == 1
