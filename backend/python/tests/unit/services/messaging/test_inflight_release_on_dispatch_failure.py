"""A message must not stay marked in flight when dispatch fails.

`_start_processing_task` marks a message in flight and then does several things
that can raise before the work is handed to the worker loop: parsing the entry,
deriving its tier, building the gate-waiter token, and creating the wrapper
coroutine.

A message id left behind in `_in_flight_message_ids` is not merely wasted
memory. Nothing ever clears it, and while it is there:

  * the read and dispatch phases skip that entry (`_is_in_flight`), so it is
    never retried, and
  * `__already_held` reports it as held, so the stranded-entry sweep will not
    dead-letter it either.

The record it carries is then never indexed and nothing is logged to say so.
These tests pin the release on each failure point.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)

MESSAGE_ID = "1700000000000-0"


def _consumer() -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        logging.getLogger("inflight-test"),
        RedisStreamsConfig(
            host="h", port=6379, group_id="g", topics=["record-events"], batch_size=10
        ),
    )
    consumer.redis = AsyncMock()
    consumer.running = True
    # Truthy so the early return does not fire; nothing here reaches it.
    consumer.worker_loop = MagicMock()
    return consumer


@pytest.mark.asyncio
async def test_parse_failure_releases_the_message() -> None:
    """_parse_message raising must not stand the entry up permanently."""
    consumer = _consumer()
    consumer._parse_message = AsyncMock(side_effect=RuntimeError("unparseable"))

    with pytest.raises(RuntimeError, match="unparseable"):
        await consumer._start_processing_task("record-events.0", MESSAGE_ID, {})

    assert not consumer._is_in_flight(MESSAGE_ID), (
        "message stayed in flight after a parse failure; it would be skipped by "
        "every later read and never dead-lettered"
    )


@pytest.mark.asyncio
async def test_tier_failure_releases_the_message(monkeypatch) -> None:
    """Deriving the tier sits between the mark and the hand-off too."""
    consumer = _consumer()
    consumer._parse_message = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        concurrency,
        "dispatch_tier",
        MagicMock(side_effect=RuntimeError("no tier")),
    )

    with pytest.raises(RuntimeError, match="no tier"):
        await consumer._start_processing_task("record-events.0", MESSAGE_ID, {})

    assert not consumer._is_in_flight(MESSAGE_ID)


@pytest.mark.asyncio
async def test_scheduling_failure_releases_the_message(monkeypatch) -> None:
    """The original guarded case still holds: scheduling onto the loop fails."""
    consumer = _consumer()
    consumer._parse_message = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "asyncio.run_coroutine_threadsafe",
        MagicMock(side_effect=RuntimeError("loop closed")),
    )

    with pytest.raises(RuntimeError, match="loop closed"):
        await consumer._start_processing_task("record-events.0", MESSAGE_ID, {})

    assert not consumer._is_in_flight(MESSAGE_ID)


@pytest.mark.asyncio
async def test_a_released_message_is_eligible_again() -> None:
    """Releasing is only worth anything if the entry can be picked up again.

    `__already_held` is what the read phase and the stranded-entry sweep
    consult; an id still marked in flight reads as held, so it is skipped on
    every later pass and never dead-lettered.
    """
    consumer = _consumer()
    consumer._parse_message = AsyncMock(side_effect=RuntimeError("unparseable"))

    with pytest.raises(RuntimeError):
        await consumer._start_processing_task("record-events.0", MESSAGE_ID, {})

    already_held = consumer._IndexingRedisStreamsConsumer__already_held
    assert not already_held(MESSAGE_ID), (
        "a failed dispatch left the entry reading as held, so later passes would "
        "skip it and the stranded sweep would never reclaim it"
    )
