"""Integration-style tests for fair scheduling in ``IndexingRedisStreamsConsumer``.

Covers the consumer-level behavior the scheduler unit tests
(``test_drr_scheduler.py``) can't see on their own: key extraction from a
real Redis Streams entry, a capped key parking its overflow un-ACKed in the
PEL rather than re-publishing it, a buffered entry whose PEL ownership is
lost by dispatch time being skipped safely (not double-processed),
``_drain_pending``'s PEL-recovered entries flowing through the scheduler
instead of being dispatched directly, and the scheduler staying off
entirely when fair scheduling is disabled.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig


@pytest.fixture
def logger():
    return logging.getLogger("test_fair_scheduling_redis")


@pytest.fixture
def config():
    return RedisStreamsConfig(
        host="localhost",
        port=6379,
        password="secret",
        db=0,
        max_len=10000,
        block_ms=100,
        batch_size=10,
        client_id="test-consumer",
        group_id="test-group",
        topics=["record-events"],
    )


def _fair_config(**overrides) -> FairSchedulerConfig:
    defaults = {
        "enabled": True,
        "key_fields": ("orgId", "connectorId"),
        "default_quantum": 1,
        "max_buffered_messages": 100,
        "max_per_entity_messages": 50,
        "max_dwell_seconds": 900.0,
    }
    defaults.update(overrides)
    return FairSchedulerConfig(**defaults)


def _fields(
    org_id: str = "org-a",
    record_id: str = "rec-1",
    tracking_id: str | None = None,
    connector_id: str = "conn-1",
) -> dict:
    payload: dict = {
        "recordId": record_id,
        "orgId": org_id,
        "connectorId": connector_id,
        "virtualRecordId": f"vr-{record_id}",
        "extension": "txt",
        "mimeType": "text/plain",
    }
    if tracking_id is not None:
        payload["_retry_tracking_id"] = tracking_id
    envelope = {"eventType": "newRecord", "payload": payload, "requestId": record_id}
    return {"value": json.dumps(envelope)}


def _make_consumer(logger, config, fair_scheduler_config=None, producer=None, retry_manager=None):
    consumer = IndexingRedisStreamsConsumer(
        logger,
        config,
        retry_manager=retry_manager,
        producer=producer,
        fair_scheduler_config=fair_scheduler_config,
    )
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    return consumer


class TestMultiOrgInterleaving:
    async def test_two_orgs_interleave_round_robin(self, logger, config):
        consumer = _make_consumer(logger, config, _fair_config())
        stream = "record-events"

        for i in range(5):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                stream, f"{i}-0", _fields(org_id="org-a", record_id=f"a-{i}")
            )
        for i in range(5):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                stream, f"{5 + i}-0", _fields(org_id="org-b", record_id=f"b-{i}")
            )

        assert consumer._scheduler.pending_count == 10

        order = []
        while not consumer._scheduler.is_empty:
            key, _item = consumer._scheduler.dequeue()
            order.append(key)

        # Quantum 1 for both keys: strict alternation, not "all of org-a's
        # backlog first" -- the exact failure mode this feature fixes.
        assert order == [("org-a", "conn-1"), ("org-b", "conn-1")] * 5


class TestFullBufferParksInsteadOfRepublishing:
    """A capped key must not bounce its overflow back to the stream tail.

    XACK does not delete a stream entry, so every bounce *adds* one while the
    original stays until MAXLEN trims it -- and MAXLEN trimming is not
    consumer-group aware, so it discards entries nobody has consumed. The
    entry is parked un-ACKed in this consumer's own PEL instead.
    """

    async def test_entity_full_parks_the_entry_and_never_publishes(
        self, logger, config
    ):
        producer = AsyncMock()
        retry_manager = MagicMock()
        retry_manager.increment_and_check = AsyncMock()
        consumer = _make_consumer(
            logger,
            config,
            _fair_config(max_per_entity_messages=1),
            producer=producer,
            retry_manager=retry_manager,
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        stream = "record-events"

        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            stream, "0-0", _fields(org_id="org-a", record_id="rec-1")
        )
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            stream, "1-0", _fields(org_id="org-a", record_id="rec-2")
        )

        assert consumer._scheduler.pending_count == 1
        assert len(consumer._deferred_entries) == 1
        producer.send_event.assert_not_awaited()
        retry_manager.increment_and_check.assert_not_awaited()
        # Never ACKed: the entry stays in this consumer's PEL, which is what
        # makes parking it safe across a crash.
        consumer.redis.xack.assert_not_awaited()

    async def test_parked_entry_is_re_offered_before_reading_more(
        self, logger, config
    ):
        """_drain_pending only runs after several consecutive *empty* polls,
        which never happens during a sustained backlog -- so a parked entry
        has to be retried by the read phase itself or it strands."""
        consumer = _make_consumer(
            logger, config, _fair_config(max_per_entity_messages=1)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        stream = "record-events"

        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            stream, "0-0", _fields(org_id="org-a", record_id="rec-1")
        )
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            stream, "1-0", _fields(org_id="org-a", record_id="rec-2")
        )
        assert len(consumer._deferred_entries) == 1

        # Dispatching the buffered entry frees the key's only slot.
        consumer._scheduler.dequeue()
        await consumer._IndexingRedisStreamsConsumer__drain_deferred()

        assert consumer._deferred_entries == deque()
        assert consumer._scheduler.pending_count == 1

    async def test_read_batch_shrinks_as_parked_entries_use_the_budget(
        self, logger, config
    ):
        """Parked entries are held in memory, so they consume the same
        budget as buffered ones and shrink the next read accordingly."""
        consumer = _make_consumer(
            logger,
            config,
            _fair_config(max_per_entity_messages=1, max_buffered_messages=5),
        )
        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = AsyncMock(return_value=[])
        consumer.main_loop = asyncio.get_running_loop()
        stream = "record-events"

        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            stream, "0-0", _fields(org_id="org-a", record_id="rec-1")
        )
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            stream, "1-0", _fields(org_id="org-a", record_id="rec-2")
        )
        consumer.redis.xreadgroup.reset_mock()

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        _args, kwargs = consumer.redis.xreadgroup.call_args
        # 5 total - 1 buffered - 1 parked = 3
        assert kwargs["count"] == 3

    async def test_dwell_sweep_releases_entries_held_past_the_budget(
        self, logger, config
    ):
        """A buffered entry is un-ACKed, so Redis counts it as idle and a peer
        will XAUTOCLAIM it. Releasing our stale copy keeps the in-memory view
        honest instead of dispatching an entry we no longer own."""
        consumer = _make_consumer(
            logger, config, _fair_config(max_dwell_seconds=0.0)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()

        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields(org_id="org-a", record_id="rec-1")
        )
        assert consumer._scheduler.pending_count == 1

        consumer._IndexingRedisStreamsConsumer__sweep_stale_buffered()

        assert consumer._scheduler.pending_count == 0
        # Released, not ACKed: the PEL entry is untouched so it is re-read.
        consumer.redis.xack.assert_not_awaited()

    async def test_dwell_sweep_leaves_fresh_entries_buffered(
        self, logger, config
    ):
        consumer = _make_consumer(
            logger, config, _fair_config(max_dwell_seconds=900.0)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()

        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields(org_id="org-a", record_id="rec-1")
        )
        consumer._IndexingRedisStreamsConsumer__sweep_stale_buffered()

        assert consumer._scheduler.pending_count == 1


class TestHeldEntriesAreNotReclaimed:
    """Buffered and parked entries stay un-ACKed in the pending list by
    design. Every re-claim of them bumps Redis's ``times_delivered``, which
    the dead-letter backstop reads as a failed attempt -- so a record that
    merely waited its turn gets discarded.
    """

    def _consumer(self, logger, config, **overrides):
        consumer = _make_consumer(logger, config, _fair_config(**overrides))
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        return consumer

    async def test_buffered_entries_are_reported_as_held(self, logger, config):
        consumer = self._consumer(logger, config)
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )
        assert consumer._IndexingRedisStreamsConsumer__already_held("0-0")

    async def test_parked_entries_are_reported_as_held(self, logger, config):
        consumer = self._consumer(logger, config, max_per_entity_messages=1)
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", message_id, _fields(connector_id="busy")
            )
        assert consumer._IndexingRedisStreamsConsumer__already_held("1-0")

    async def test_dispatch_hands_ownership_to_the_in_flight_set(
        self, logger, config
    ):
        consumer = self._consumer(logger, config)
        consumer.running = True
        consumer._IndexingRedisStreamsConsumer__start_processing_task = AsyncMock()
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )
        await consumer._IndexingRedisStreamsConsumer__dispatch_phase()

        assert "0-0" not in consumer._held_entries

    async def test_dwell_release_stops_reporting_the_entry_as_held(
        self, logger, config
    ):
        """Released back to the pending list, so recovery *should* pick it
        up again."""
        consumer = self._consumer(logger, config, max_dwell_seconds=0.0)
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )
        consumer._IndexingRedisStreamsConsumer__sweep_stale_buffered()

        assert not consumer._IndexingRedisStreamsConsumer__already_held("0-0")

    async def test_ownership_refresh_uses_justid(self, logger, config):
        """JUSTID resets the idle timer without incrementing the delivery
        counter -- the whole point of refreshing rather than re-claiming."""
        consumer = self._consumer(logger, config)
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )
        consumer._last_ownership_refresh = 0.0

        await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()

        _args, kwargs = consumer.redis.xclaim.call_args
        assert kwargs["justid"] is True
        assert kwargs["message_ids"] == ["0-0"]
        assert kwargs["min_idle_time"] == 0

    async def test_refresh_is_rate_limited(self, logger, config):
        consumer = self._consumer(logger, config)
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )
        consumer._last_ownership_refresh = 0.0
        await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()
        consumer.redis.xclaim.reset_mock()

        await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()

        consumer.redis.xclaim.assert_not_called()

    async def test_a_failed_refresh_does_not_propagate(self, logger, config):
        consumer = self._consumer(logger, config)
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )
        consumer.redis.xclaim = AsyncMock(side_effect=RuntimeError("down"))
        consumer._last_ownership_refresh = 0.0

        await consumer._IndexingRedisStreamsConsumer__refresh_held_ownership()


class TestReadBudgetIsSharedAcrossStreams:
    """XREADGROUP applies COUNT to *each* stream in the request.

    Reading N lanes with count=C returns up to N*C entries. Anything past the
    buffer budget lands in this consumer's pending list with times_delivered
    incremented and is then neither buffered nor parked -- burning delivery
    attempts against the dead-letter backstop on work it cannot hold.
    """

    async def test_count_is_divided_between_the_streams_being_read(
        self, logger, config
    ):
        lanes = [f"record-events.{i}" for i in range(8)]
        consumer = _make_consumer(
            logger,
            config.model_copy(update={"topics": lanes, "batch_size": 100}),
            _fair_config(max_buffered_messages=16),
        )
        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = AsyncMock(return_value=[])
        consumer.main_loop = asyncio.get_running_loop()
        consumer.running = True

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        _args, kwargs = consumer.redis.xreadgroup.call_args
        streams_read = len(kwargs["streams"])
        assert kwargs["count"] * streams_read <= 16, (
            f"count {kwargs['count']} across {streams_read} streams can "
            "return more than the buffer budget of 16"
        )

    async def test_a_single_stream_still_gets_the_whole_budget(
        self, logger, config
    ):
        consumer = _make_consumer(
            logger,
            config.model_copy(
                update={"topics": ["record-events"], "batch_size": 100}
            ),
            _fair_config(max_buffered_messages=16),
        )
        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = AsyncMock(return_value=[])
        consumer.main_loop = asyncio.get_running_loop()
        consumer.running = True

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        _args, kwargs = consumer.redis.xreadgroup.call_args
        assert kwargs["count"] == 16


class TestParkedEntriesAgeOut:
    """A key stuck at its cap would otherwise hold parked entries forever:
    ownership refresh keeps resetting their idle time, so no peer claims them
    either, and they never reach the dwell metric."""

    async def test_parked_entries_past_the_dwell_budget_are_released(
        self, logger, config
    ):
        consumer = _make_consumer(
            logger,
            config,
            _fair_config(max_per_entity_messages=1, max_dwell_seconds=0.0),
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", message_id, _fields(connector_id="busy")
            )
        assert len(consumer._deferred_entries) == 1

        consumer._IndexingRedisStreamsConsumer__sweep_stale_buffered()

        assert consumer._deferred_entries == deque()
        # Released, not ACKed: the pending entry is untouched so it is re-read.
        consumer.redis.xack.assert_not_awaited()
        assert not consumer._IndexingRedisStreamsConsumer__already_held("1-0")

    async def test_fresh_parked_entries_are_kept(self, logger, config):
        consumer = _make_consumer(
            logger,
            config,
            _fair_config(max_per_entity_messages=1, max_dwell_seconds=900.0),
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", message_id, _fields(connector_id="busy")
            )

        consumer._IndexingRedisStreamsConsumer__sweep_stale_buffered()

        assert len(consumer._deferred_entries) == 1


class TestPendingScanPagesPastHeldEntries:
    """Held entries stay in the pending list by design and have the lowest
    ids, so they sit at its head. A one-shot window the size of a read batch
    would see nothing else and conclude there is nothing to recover."""

    async def test_finds_an_unheld_entry_behind_a_page_of_held_ones(
        self, logger, config
    ):
        consumer = _make_consumer(
            logger, config.model_copy(update={"batch_size": 2}), _fair_config()
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        # 150 held entries, then one that is not held.
        for index in range(150):
            consumer._held_entries[f"{index}-0"] = "record-events"
        pages = [
            [{"message_id": f"{i}-0"} for i in range(100)],
            [{"message_id": f"{i}-0"} for i in range(100, 150)]
            + [{"message_id": "unheld-0"}],
        ]
        consumer.redis.xpending_range = AsyncMock(side_effect=pages)

        found = await consumer._IndexingRedisStreamsConsumer__has_unheld_pending(
            "record-events"
        )

        assert found is True
        assert consumer.redis.xpending_range.await_count == 2

    async def test_reports_nothing_when_every_pending_entry_is_held(
        self, logger, config
    ):
        consumer = _make_consumer(logger, config, _fair_config())
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        for index in range(5):
            consumer._held_entries[f"{index}-0"] = "record-events"
        consumer.redis.xpending_range = AsyncMock(
            return_value=[{"message_id": f"{i}-0"} for i in range(5)]
        )

        found = await consumer._IndexingRedisStreamsConsumer__has_unheld_pending(
            "record-events"
        )

        assert found is False

    async def test_a_failed_scan_fails_open(self, logger, config):
        consumer = _make_consumer(logger, config, _fair_config())
        consumer.redis = AsyncMock()
        consumer.redis.xpending_range = AsyncMock(side_effect=RuntimeError("down"))

        assert (
            await consumer._IndexingRedisStreamsConsumer__has_unheld_pending(
                "record-events"
            )
            is True
        )


class TestRecoveryClaimBudget:
    """XAUTOCLAIM bumps times_delivered on every entry it returns, so
    claiming more than there is room to take responsibility for burns
    delivery attempts on work nothing tries to process."""

    async def test_budget_is_capped_by_remaining_buffer_room(
        self, logger, config
    ):
        consumer = _make_consumer(
            logger, config, _fair_config(max_buffered_messages=4)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )

        budget = consumer._IndexingRedisStreamsConsumer__recovery_claim_budget(100)

        assert budget == 3

    async def test_budget_is_zero_when_the_buffer_is_full(self, logger, config):
        consumer = _make_consumer(
            logger, config, _fair_config(max_buffered_messages=1)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "0-0", _fields()
        )

        assert (
            consumer._IndexingRedisStreamsConsumer__recovery_claim_budget(100) == 0
        )

    def test_budget_still_respects_pipeline_capacity(self, logger, config):
        consumer = _make_consumer(logger, config, _fair_config())
        assert (
            consumer._IndexingRedisStreamsConsumer__recovery_claim_budget(2) == 2
        )


class TestLaneStreamAdoption:
    """Lowering the lane count must not orphan the lanes that drop out.

    Lane count is a deployment setting. Without this the consumer would stop
    subscribing to `record-events.4..7` the moment someone set the count back
    to 4, leaving whatever is still in them unread until the count went up
    again.
    """

    def _consumer(self, logger, config, topics):
        consumer = _make_consumer(
            logger, config.model_copy(update={"topics": topics}), _fair_config()
        )
        consumer.redis = AsyncMock()
        return consumer

    def _scan(self, keys):
        async def scan_iter(match=None):
            for key in keys:
                yield key

        return scan_iter

    async def test_adopts_lane_streams_outside_the_configured_range(
        self, logger, config
    ):
        consumer = self._consumer(
            logger, config, ["record-events", "record-events.0"]
        )
        consumer.redis.scan_iter = self._scan(
            ["record-events.0", "record-events.1", "record-events.2"]
        )
        consumer.redis.type = AsyncMock(return_value="stream")

        await consumer._IndexingRedisStreamsConsumer__adopt_existing_lane_streams()

        assert consumer.config.topics == [
            "record-events",
            "record-events.0",
            "record-events.1",
            "record-events.2",
        ]

    async def test_ignores_keys_that_are_not_lane_streams(self, logger, config):
        consumer = self._consumer(logger, config, ["record-events"])
        consumer.redis.scan_iter = self._scan(
            ["record-events.meta", "record-events.0"]
        )
        consumer.redis.type = AsyncMock(return_value="stream")

        await consumer._IndexingRedisStreamsConsumer__adopt_existing_lane_streams()

        assert consumer.config.topics == ["record-events", "record-events.0"]

    async def test_ignores_keys_of_the_wrong_redis_type(self, logger, config):
        consumer = self._consumer(logger, config, ["record-events"])
        consumer.redis.scan_iter = self._scan(["record-events.0"])
        consumer.redis.type = AsyncMock(return_value="string")

        await consumer._IndexingRedisStreamsConsumer__adopt_existing_lane_streams()

        assert consumer.config.topics == ["record-events"]

    async def test_handles_byte_keys(self, logger, config):
        consumer = self._consumer(logger, config, ["record-events"])
        consumer.redis.scan_iter = self._scan([b"record-events.3"])
        consumer.redis.type = AsyncMock(return_value="stream")

        await consumer._IndexingRedisStreamsConsumer__adopt_existing_lane_streams()

        assert consumer.config.topics == ["record-events", "record-events.3"]

    async def test_a_scan_failure_leaves_the_configured_subscription_alone(
        self, logger, config
    ):
        """Discovery is an operational convenience; it must never keep the
        consumer from starting."""
        consumer = self._consumer(logger, config, ["record-events"])

        def boom(match=None):
            raise RuntimeError("scan unavailable")

        consumer.redis.scan_iter = boom

        await consumer._IndexingRedisStreamsConsumer__adopt_existing_lane_streams()

        assert consumer.config.topics == ["record-events"]


class TestPerLaneReadExclusion:
    """A lane is a stream here, so a blocked key excludes only its own stream
    from the next read -- every other lane keeps flowing.

    Excluding rather than claiming matters: an entry left unread is not in
    this consumer's pending list, so it is not ageing toward another
    replica's idle-claim window while it waits.
    """

    def _consumer(self, logger, config, topics, **overrides):
        config = config.model_copy(update={"topics": topics})
        consumer = _make_consumer(logger, config, _fair_config(**overrides))
        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = AsyncMock(return_value=[])
        consumer.main_loop = asyncio.get_running_loop()
        consumer.running = True
        return consumer

    async def test_only_the_blocked_lane_is_excluded_from_the_read(
        self, logger, config
    ):
        lanes = ["record-events.0", "record-events.1"]
        consumer = self._consumer(
            logger, config, lanes, max_per_entity_messages=1
        )
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                lanes[0], message_id, _fields(connector_id="busy")
            )
        assert len(consumer._deferred_entries) == 1

        # The other lane produced work on the last poll, so it is a genuine
        # alternative source and the blocked one can be skipped.
        consumer._lanes_with_data = {lanes[1]}

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        _args, kwargs = consumer.redis.xreadgroup.call_args
        assert set(kwargs["streams"]) == {lanes[1]}

    async def test_an_idle_lane_does_not_count_as_an_alternative(
        self, logger, config
    ):
        """The failure seen in production: eight lanes configured, traffic on
        one. The seven idle lanes looked readable forever, so the busy lane
        was never read again and the second key on it never got picked up."""
        lanes = [f"record-events.{i}" for i in range(8)]
        consumer = self._consumer(
            logger, config, lanes, max_per_entity_messages=1
        )
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                lanes[6], message_id, _fields(connector_id="busy")
            )
        assert len(consumer._deferred_entries) == 1
        consumer._lanes_with_data = set()  # nothing else has produced
        consumer.redis.xreadgroup.reset_mock()

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        _args, kwargs = consumer.redis.xreadgroup.call_args
        assert lanes[6] in kwargs["streams"], (
            "the busy lane must keep being read when no other lane is "
            "actually producing"
        )

    async def test_the_only_lane_keeps_being_read_even_when_blocked(
        self, logger, config
    ):
        """Reading is the only way to discover a key that is *not* backed up.

        Skipping every lane would cap read-ahead at one key's share of the
        buffer, so a large backlog at the head of the stream would never be
        read past and every key behind it would starve -- the exact problem
        fair scheduling exists to solve.
        """
        lanes = ["record-events.0"]
        consumer = self._consumer(
            logger, config, lanes, max_per_entity_messages=1
        )
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                lanes[0], message_id, _fields(connector_id="busy")
            )
        consumer.redis.xreadgroup.reset_mock()

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        consumer.redis.xreadgroup.assert_awaited_once()

    async def test_reads_stop_once_the_total_buffer_is_full(
        self, logger, config
    ):
        """The total buffer, not one key's share of it, is what bounds
        memory and therefore what stops reads."""
        lanes = ["record-events.0"]
        consumer = self._consumer(
            logger,
            config,
            lanes,
            max_per_entity_messages=1,
            max_buffered_messages=2,
        )
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                lanes[0], message_id, _fields(connector_id="busy")
            )
        # One buffered, one parked: two held, which is the whole budget.
        assert consumer._scheduler.pending_count == 1
        assert len(consumer._deferred_entries) == 1
        consumer.redis.xreadgroup.reset_mock()

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        consumer.redis.xreadgroup.assert_not_awaited()

    async def test_a_blocked_lane_is_skipped_when_another_lane_can_progress(
        self, logger, config
    ):
        lanes = ["record-events.0", "record-events.1"]
        consumer = self._consumer(
            logger, config, lanes, max_per_entity_messages=1
        )
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                lanes[0], message_id, _fields(connector_id="busy")
            )
        consumer.redis.xreadgroup.reset_mock()

        # The other lane produced work on the last poll, so it is a genuine
        # alternative source and the blocked one can be skipped.
        consumer._lanes_with_data = {lanes[1]}

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        _args, kwargs = consumer.redis.xreadgroup.call_args
        assert set(kwargs["streams"]) == {lanes[1]}

    async def test_lane_is_read_again_once_its_key_drains(self, logger, config):
        lanes = ["record-events.0"]
        consumer = self._consumer(
            logger, config, lanes, max_per_entity_messages=1
        )
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                lanes[0], message_id, _fields(connector_id="busy")
            )
        consumer._scheduler.dequeue()
        consumer.redis.xreadgroup.reset_mock()

        await consumer._IndexingRedisStreamsConsumer__read_phase()

        # The parked entry was re-offered, freeing the lane to be read.
        assert consumer._deferred_entries == deque()
        consumer.redis.xreadgroup.assert_awaited_once()


class TestMeteringIsNotInflatedByRetries:
    """A parked entry is re-offered on every read iteration. Counting the
    deferral or the missing key there would inflate both without bound while
    an entry waits -- roughly twice a second, forever."""

    async def test_a_parked_entry_is_counted_once_not_once_per_retry(
        self, logger, config
    ):
        from app.telemetry.backend import METRICS_BACKEND

        def deferred_total() -> float:
            for line in METRICS_BACKEND.serialize().splitlines():
                if line.startswith(
                    'pipeshub_indexing_scheduler_deferred_total{broker="redis"'
                ):
                    return float(line.rsplit(" ", 1)[1])
            return 0.0

        consumer = _make_consumer(
            logger, config, _fair_config(max_per_entity_messages=1)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", message_id, _fields(connector_id="busy")
            )
        assert len(consumer._deferred_entries) == 1
        after_first = deferred_total()

        for _ in range(5):
            await consumer._IndexingRedisStreamsConsumer__drain_deferred()

        assert deferred_total() == after_first

    async def test_missing_key_is_counted_once_per_message(self, logger, config):
        from app.telemetry.backend import METRICS_BACKEND

        def missing_total() -> float:
            for line in METRICS_BACKEND.serialize().splitlines():
                if line.startswith(
                    "pipeshub_indexing_scheduler_missing_key_total"
                ) and 'broker="redis"' in line:
                    return float(line.rsplit(" ", 1)[1])
            return 0.0

        consumer = _make_consumer(
            logger, config, _fair_config(max_per_entity_messages=1)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        before = missing_total()
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", message_id, _fields(connector_id="")
            )
        after_reads = missing_total()

        for _ in range(5):
            await consumer._IndexingRedisStreamsConsumer__drain_deferred()

        assert after_reads == before + 2
        assert missing_total() == after_reads


class TestDeferredDrainIsPerKey:
    async def test_one_full_key_does_not_hold_back_another_keys_entries(
        self, logger, config
    ):
        """Otherwise the parking area re-creates, inside the consumer,
        exactly the head-of-line blocking lanes exist to remove."""
        consumer = _make_consumer(
            logger, config, _fair_config(max_per_entity_messages=1)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()

        # "busy" fills its cap and parks one; "quiet" parks behind it only
        # because the buffer had no room at that instant.
        for message_id in ("0-0", "1-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", message_id, _fields(connector_id="busy")
            )
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            "record-events", "2-0", _fields(connector_id="quiet")
        )
        assert len(consumer._deferred_entries) == 1

        await consumer._IndexingRedisStreamsConsumer__drain_deferred()

        # "busy" is still capped, so its entry stays parked; nothing else is
        # stuck behind it.
        parked_keys = [entry[4] for entry in consumer._deferred_entries]
        assert parked_keys == [("org-a", "busy")]

    async def test_a_keys_own_entries_keep_arrival_order(self, logger, config):
        consumer = _make_consumer(
            logger, config, _fair_config(max_per_entity_messages=1)
        )
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        for message_id in ("0-0", "1-0", "2-0"):
            await consumer._IndexingRedisStreamsConsumer__enqueue_message(
                "record-events", message_id, _fields(connector_id="busy")
            )
        assert [e[1] for e in consumer._deferred_entries] == ["1-0", "2-0"]

        consumer._scheduler.dequeue()
        await consumer._IndexingRedisStreamsConsumer__drain_deferred()

        # Only the head fits; the tail stays parked, still in order.
        assert [e[1] for e in consumer._deferred_entries] == ["2-0"]


class TestPelOwnershipLostAfterBuffering:
    async def test_dispatch_skips_entry_whose_ownership_was_lost_while_buffered(
        self, logger, config
    ):
        """A message that sat in the fair-scheduling buffer can have its PEL
        entry stolen (XAUTOCLAIM) or ACKed by another consumer before this
        consumer gets around to dispatching it. The ownership check at
        dispatch time must still catch that -- the buffering delay must not
        create a window for double processing."""
        consumer = _make_consumer(logger, config, _fair_config())
        consumer.redis = AsyncMock()
        consumer.main_loop = asyncio.get_running_loop()
        handler_calls = []

        async def handler(_parsed_message):
            handler_calls.append(1)
            return
            yield  # pragma: no cover - never reached, keeps this an async generator

        consumer.message_handler = handler
        consumer.governor = None
        consumer.parsing_semaphore = asyncio.Semaphore(1)
        consumer.indexing_semaphore = asyncio.Semaphore(1)

        stream = "record-events"
        await consumer._IndexingRedisStreamsConsumer__enqueue_message(
            stream, "0-0", _fields(org_id="org-a", record_id="rec-1")
        )
        assert consumer._scheduler.pending_count == 1

        # Ownership was lost while this entry sat buffered.
        consumer._pending_message_is_owned = AsyncMock(return_value=False)

        dispatched = consumer._scheduler.dequeue()
        assert dispatched is not None
        _key, (stream_name, message_id, fields, parsed, _buffered_at) = dispatched
        result = await consumer._process_message_wrapper(
            stream_name, message_id, fields, parsed_message=parsed
        )

        assert result is False
        assert handler_calls == []
        consumer.redis.xack.assert_not_awaited()


class TestDrainPendingRoutesThroughScheduler:
    async def test_pel_recovered_entries_are_buffered_not_dispatched_directly(
        self, logger, config
    ):
        consumer = _make_consumer(logger, config, _fair_config())
        consumer.running = True
        consumer.redis = AsyncMock()
        consumer.redis.xautoclaim = AsyncMock(
            side_effect=[
                (
                    "0-0",
                    [
                        ("1-0", _fields(org_id="org-a", record_id="rec-1")),
                        ("2-0", _fields(org_id="org-b", record_id="rec-2")),
                    ],
                    [],
                ),
                ("0-0", [], []),
            ]
        )
        consumer.redis.xreadgroup = AsyncMock(return_value=None)

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_start:
            await consumer._drain_pending()

        # Recovered entries went into the scheduler, not straight to a task.
        mock_start.assert_not_called()
        assert consumer._scheduler.pending_count == 2
        keys = set()
        while not consumer._scheduler.is_empty:
            key, _item = consumer._scheduler.dequeue()
            keys.add(key)
        assert keys == {("org-a", "conn-1"), ("org-b", "conn-1")}


class TestDisabledIsCurrentFifo:
    def test_no_fair_scheduler_config_disables_scheduler(self, logger, config):
        consumer = IndexingRedisStreamsConsumer(logger, config, retry_manager=None, producer=None)
        assert consumer.fair_scheduler_config.enabled is False
        assert consumer._scheduler is None

    async def test_dispatch_or_enqueue_calls_start_processing_task_directly(
        self, logger, config
    ):
        consumer = _make_consumer(logger, config, _fair_config(enabled=False))
        assert consumer._scheduler is None

        with patch.object(
            consumer, "_start_processing_task", new_callable=AsyncMock
        ) as mock_start:
            await consumer._IndexingRedisStreamsConsumer__dispatch_or_enqueue(
                "record-events", "0-0", _fields(org_id="org-a")
            )

        mock_start.assert_awaited_once_with("record-events", "0-0", _fields(org_id="org-a"))
