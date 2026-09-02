"""E2E: fair scheduling gives a small producer a fair share of indexing
against a large one, and documents exactly how far that goes on one lane.

Drives the real ``IndexingKafkaConsumer`` read/dispatch split -- parsing,
key extraction, offset tracking, worker-thread dispatch, DRR ordering --
against a fake broker. Only the Kafka transport (``AIOKafkaConsumer``) is
faked.

Two scenarios, because they have genuinely different outcomes:

- **Two users, one org** (``TestTwoUsersInOneOrg``): the single-tenant OSS
  case. Both users share an ``orgId``, so fairness depends entirely on the
  second key level, ``connectorId``.
- **Concurrent producers** (``TestConcurrentProducers``): two orgs syncing at
  once, so the log interleaves and DRR gives the small one a fair share.
- **Segregated backlog** (``TestSegregatedBacklogIsStillHeadOfLineBlocked``):
  one producer's entire backlog is published *before* the other's first
  record. On a single FIFO lane the small producer's records are physically
  behind the whole backlog, and no consumer-side reordering can reach them
  early -- the consumer cannot schedule what it has not read. That test
  pins the limitation rather than pretending it is solved; per-key broker
  lanes are what remove it.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier

_TOPIC = "record-events"
_ORG_A_COUNT = 1000
_ORG_B_COUNT = 10


class _FakeKafkaConsumer:
    """Single-partition in-memory stand-in for ``AIOKafkaConsumer``.

    Only the surface ``__read_phase``/``__dispatch_phase`` touch: ``getmany``
    (FIFO, respecting ``max_records``), ``commit`` (recorded, not enforced --
    the watermark's correctness is covered by
    ``test_partition_offset_tracker.py`` and ``test_fair_scheduling_kafka.py``),
    ``seek`` (a real rewind, since a full buffer now stops and rewinds the
    partition), and no-op ``pause``/``resume``.

    Offsets are positions in the log, so ``seek`` is just an index assignment.
    """

    def __init__(self, records: list) -> None:
        self._records = records
        self._position = 0
        self.commits: list[dict] = []
        self.seeks: list[tuple] = []

    @property
    def exhausted(self) -> bool:
        return self._position >= len(self._records)

    async def getmany(self, timeout_ms: int = 0, max_records: int = 1) -> dict:
        batch = self._records[self._position : self._position + max_records]
        if not batch:
            return {}
        self._position += len(batch)
        tp = TopicPartition(batch[0].topic, batch[0].partition)
        return {tp: batch}

    async def commit(self, offsets: dict) -> None:
        self.commits.append(dict(offsets))

    def pause(self, *_tps) -> None:
        pass

    def resume(self, *_tps) -> None:
        pass

    def seek(self, tp: TopicPartition, offset: int) -> None:
        """A full buffer rewinds the partition instead of re-publishing, so
        the fake has to honour the rewind for the read loop to make
        progress once the buffer drains."""
        self._position = offset
        self.seeks.append((tp, offset))


def _make_message(
    offset: int, org_id: str, record_id: str, connector_id: str = "conn-1"
):
    payload = {
        "recordId": record_id,
        "orgId": org_id,
        "connectorId": connector_id,
        "virtualRecordId": f"vr-{record_id}",
        "extension": "txt",
        "mimeType": "text/plain",
    }
    envelope = {"eventType": "newRecord", "payload": payload, "requestId": record_id}
    msg = MagicMock()
    msg.topic = _TOPIC
    msg.partition = 0
    msg.offset = offset
    msg.value = json.dumps(envelope).encode()
    return msg


def _build_segregated_log() -> list:
    """Org A's full backlog, published first, then Org B's -- physically
    behind it in the one partition."""
    records = [_make_message(i, "org-a", f"a-{i}") for i in range(_ORG_A_COUNT)]
    records += [
        _make_message(_ORG_A_COUNT + i, "org-b", f"b-{i}")
        for i in range(_ORG_B_COUNT)
    ]
    return records


def _build_concurrent_log() -> list:
    """Both connectors syncing at once. Org A produces 100x faster, so the
    log is Org-A-dominated but Org B's records are sprinkled through it --
    the realistic shape when two users start syncs around the same time."""
    records = []
    offset = 0
    b_index = 0
    for a_index in range(_ORG_A_COUNT):
        records.append(_make_message(offset, "org-a", f"a-{a_index}"))
        offset += 1
        if a_index % 100 == 99 and b_index < _ORG_B_COUNT:
            records.append(_make_message(offset, "org-b", f"b-{b_index}"))
            offset += 1
            b_index += 1
    return records


def _fair(max_buffered: int, max_per_entity: int) -> FairSchedulerConfig:
    return FairSchedulerConfig(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=max_buffered,
        max_per_entity_messages=max_per_entity,
        max_dwell_seconds=900.0,
    )


def _build_two_users_one_org_log() -> list:
    """The single-org OSS case: two users of the *same* org syncing at once.

    Both records carry the same ``orgId``, so a scheduler keyed on org alone
    sees one queue and gives no fairness whatsoever. ``connectorId`` -- one
    per individually-configured connector -- is what separates them.
    """
    records = []
    offset = 0
    small_index = 0
    for big_index in range(_ORG_A_COUNT):
        records.append(
            _make_message(offset, "org-1", f"big-{big_index}", connector_id="user-a")
        )
        offset += 1
        if big_index % 100 == 99 and small_index < _ORG_B_COUNT:
            records.append(
                _make_message(
                    offset, "org-1", f"small-{small_index}", connector_id="user-b"
                )
            )
            offset += 1
            small_index += 1
    return records


def _make_handler(completion_order: list):
    async def handler(parsed_message):
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(tier=ParseTier.LIGHT),
        )
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        completion_order.append(
            (
                parsed_message.payload["orgId"],
                parsed_message.payload["connectorId"],
            )
        )
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handler


async def _wait_until_idle(consumer: IndexingKafkaConsumer, max_iters: int = 500) -> None:
    """Block until no processing task is in flight and the (single)
    partition has been released, so the next dispatch call sees it free.
    """
    for _ in range(max_iters):
        with consumer._futures_lock:
            futures = list(consumer._active_futures)
        if futures:
            await asyncio.wait([asyncio.wrap_future(f) for f in futures], timeout=5.0)
        with consumer._partition_lock:
            in_flight = bool(consumer._in_flight_partitions)
        if not in_flight:
            return
        await asyncio.sleep(0)
    raise AssertionError("consumer did not release its partition in time")


@pytest.mark.asyncio
class TestConcurrentProducers:
    """The common case: two connectors syncing at the same time."""

    async def test_small_producer_finishes_far_earlier_than_fifo(self) -> None:
        """Every Org B record completes ahead of its FIFO position.

        Fairness on one lane is bounded by the read-ahead window: the
        consumer can only reorder among records it has already buffered, so
        a record 1000 entries deep cannot be pulled forward further than the
        buffer is deep. The gain is real and large, but it is the window --
        not unlimited.
        """
        log = _build_concurrent_log()
        fifo_positions = [
            i
            for i, msg in enumerate(log)
            if json.loads(msg.value)["payload"]["orgId"] == "org-b"
        ]
        completion_order, _consumer, _broker = await _drive(
            log, fair_config=_fair(max_buffered=400, max_per_entity=200)
        )
        fair_positions = [
            i for i, org in enumerate(completion_order) if org[0] == "org-b"
        ]

        assert len(fair_positions) == _ORG_B_COUNT
        assert all(
            fair < fifo
            for fair, fifo in zip(fair_positions, fifo_positions, strict=True)
        ), f"fair={fair_positions} fifo={fifo_positions}"

        gains = [
            fifo - fair
            for fair, fifo in zip(fair_positions, fifo_positions, strict=True)
        ]
        assert sum(gains) / len(gains) > 150, (
            "Org B should be pulled forward by roughly the read-ahead "
            f"window, got mean gain {sum(gains) / len(gains):.0f}"
        )
        # The first Org B record is served on the very next DRR turn after
        # it is read, rather than behind the 100 Org A records ahead of it.
        # It sits at log index 100 and the consumer reads MESSAGE_BATCH_SIZE
        # entries per dispatch turn, so ~10 turns pass before it is even
        # visible -- that read cadence, not the scheduler, is the floor.
        assert fair_positions[0] < 15

    async def test_fairness_is_near_perfect_once_the_buffer_spans_the_backlog(
        self,
    ) -> None:
        """With buffer room for the whole log the per-key cap stops biting,
        and Org B's completions collapse onto the read cadence: the consumer
        reads MESSAGE_BATCH_SIZE_INDEXING (10) entries per dispatch turn, so
        a record at log index N is served at roughly N/10 instead of N.

        That ratio -- the read batch size -- is the ceiling of consumer-side
        fairness on a single lane, and it is why per-key broker lanes are the
        next phase: with a lane per key there is no backlog to read past at
        all.
        """
        completion_order, _consumer, _broker = await _drive(
            _build_concurrent_log(),
            fair_config=_fair(max_buffered=2000, max_per_entity=1500),
        )
        fair_positions = [
            i for i, org in enumerate(completion_order) if org[0] == "org-b"
        ]
        # FIFO would put the last one at 1009; the read cadence puts it at
        # ~101. Anything near 1009 means DRR stopped interleaving.
        assert fair_positions[-1] < 150, (
            "with the whole backlog buffered every Org B record should track "
            f"the read cadence, got {fair_positions}"
        )

    async def test_every_record_is_indexed_exactly_once(self) -> None:
        """Fair ordering must not drop or duplicate work: reordering the
        dispatch is the whole feature, losing a record is not."""
        completion_order, consumer, broker = await _drive(
            _build_concurrent_log(),
            fair_config=_fair(max_buffered=400, max_per_entity=200),
        )
        assert len(completion_order) == _ORG_A_COUNT + _ORG_B_COUNT
        assert [o for o, _c in completion_order].count("org-b") == _ORG_B_COUNT
        assert [o for o, _c in completion_order].count("org-a") == _ORG_A_COUNT

    async def test_commit_watermark_reaches_the_end_of_the_log(self) -> None:
        """Every offset resolved means the watermark clears the whole log.
        A pinned watermark shows up here as a final commit far below the
        log length -- the exact symptom of an unresolved offset."""
        _order, consumer, broker = await _drive(
            _build_concurrent_log(),
            fair_config=_fair(max_buffered=400, max_per_entity=200),
        )
        tp = TopicPartition(_TOPIC, 0)
        assert broker.commits, "consumer never committed"
        assert broker.commits[-1][tp] == _ORG_A_COUNT + _ORG_B_COUNT
        assert consumer._offset_tracker.watermark_lag(tp) == 0


@pytest.mark.asyncio
class TestTwoUsersInOneOrg:
    """The single-tenant OSS case, which is the whole reason the default key
    is two levels.

    Both users belong to the same org, so every record carries an identical
    ``orgId``. A scheduler keyed on org alone puts all of it in one queue and
    delivers exactly zero fairness -- ``test_org_only_key_gives_no_fairness``
    pins that, so the default can never silently regress to it.
    """

    async def test_second_user_is_not_stuck_behind_the_first_users_sync(
        self,
    ) -> None:
        log = _build_two_users_one_org_log()
        fifo_positions = [
            i
            for i, msg in enumerate(log)
            if json.loads(msg.value)["payload"]["connectorId"] == "user-b"
        ]
        completion_order, _consumer, _broker = await _drive(
            log, fair_config=_fair(max_buffered=400, max_per_entity=200)
        )
        user_b = [
            i for i, (_org, conn) in enumerate(completion_order) if conn == "user-b"
        ]

        assert len(user_b) == _ORG_B_COUNT
        # Same read-ahead-window bound as the cross-org case: the consumer can
        # only reorder among records it has already buffered, so a record deep
        # in the log is pulled forward by the buffer depth, not to the front.
        assert all(
            fair < fifo
            for fair, fifo in zip(user_b, fifo_positions, strict=True)
        ), f"fair={user_b} fifo={fifo_positions}"
        gains = [
            fifo - fair for fair, fifo in zip(user_b, fifo_positions, strict=True)
        ]
        assert sum(gains) / len(gains) > 150, (
            f"mean gain {sum(gains) / len(gains):.0f} is too small -- User B "
            "is still tracking its FIFO position"
        )
        assert user_b[0] < 15

    async def test_org_only_key_gives_no_fairness(self) -> None:
        """The regression guard for the key choice itself: with ``orgId`` as
        the only level, both users collapse into one queue and User B's
        records complete exactly where FIFO would put them."""
        log = _build_two_users_one_org_log()
        fifo_positions = [
            i
            for i, msg in enumerate(log)
            if json.loads(msg.value)["payload"]["connectorId"] == "user-b"
        ]
        completion_order, _consumer, _broker = await _drive(
            log,
            fair_config=FairSchedulerConfig(
                enabled=True,
                key_fields=("orgId",),
                default_quantum=1,
                max_buffered_messages=400,
                max_per_entity_messages=200,
                max_dwell_seconds=900.0,
            ),
        )
        user_b = [
            i for i, (_org, conn) in enumerate(completion_order) if conn == "user-b"
        ]
        assert user_b == fifo_positions, (
            "keying on orgId alone must degenerate to FIFO for two users in "
            "one org -- if this now differs, the default key changed"
        )

    async def test_every_record_is_indexed_exactly_once(self) -> None:
        completion_order, consumer, _broker = await _drive(
            _build_two_users_one_org_log(),
            fair_config=_fair(max_buffered=400, max_per_entity=200),
        )
        connectors = [conn for _org, conn in completion_order]
        assert connectors.count("user-a") == _ORG_A_COUNT
        assert connectors.count("user-b") == _ORG_B_COUNT
        assert consumer._offset_tracker.watermark_lag(TopicPartition(_TOPIC, 0)) == 0


@pytest.mark.asyncio
class TestSegregatedBacklogIsStillHeadOfLineBlocked:
    """Pins the known limit of consumer-side fairness on a single lane.

    When Org A's entire backlog is published before Org B's first record,
    Org B's records sit physically behind 1000 entries in one FIFO log. A
    consumer cannot schedule what it has not read, so no amount of DRR
    reordering reaches them early. This is not a bug in the scheduler -- it
    is why per-key broker lanes are the next phase. If this test ever starts
    failing because Org B finishes early, lanes have landed and it should be
    replaced with the fairness assertion.
    """

    async def test_small_producer_still_waits_behind_a_segregated_backlog(
        self,
    ) -> None:
        completion_order, _consumer, _broker = await _drive(
            _build_segregated_log(),
            fair_config=_fair(max_buffered=200, max_per_entity=50),
        )

        assert len(completion_order) == _ORG_A_COUNT + _ORG_B_COUNT
        first_b = min(
            i for i, org in enumerate(completion_order) if org[0] == "org-b"
        )
        # Org B is reached only once Org A's backlog has largely drained.
        assert first_b > _ORG_A_COUNT / 2

    async def test_nothing_is_lost_even_when_the_buffer_keeps_filling(
        self,
    ) -> None:
        """The per-key cap makes the read phase stop and rewind repeatedly
        here. Every record must still be indexed exactly once."""
        completion_order, consumer, broker = await _drive(
            _build_segregated_log(),
            fair_config=_fair(max_buffered=200, max_per_entity=50),
        )
        # No rewind is asserted: a capped key is parked in memory while the
        # buffer as a whole still has room, and only rewinds the partition
        # once nothing more can be held. What matters either way is that
        # every record is indexed exactly once.
        assert [o for o, _c in completion_order].count("org-a") == _ORG_A_COUNT
        assert [o for o, _c in completion_order].count("org-b") == _ORG_B_COUNT
        assert consumer._offset_tracker.watermark_lag(TopicPartition(_TOPIC, 0)) == 0


async def _drive(
    log: list, fair_config: FairSchedulerConfig
) -> tuple[list[str], IndexingKafkaConsumer, _FakeKafkaConsumer]:
    """Run the real read/dispatch loop over ``log`` until it is fully
    indexed, returning the completion order in org terms."""
    logger = logging.getLogger("test_fair_scheduling_e2e")
    kafka_config = KafkaConsumerConfig(
        topics=[_TOPIC],
        client_id="e2e-consumer",
        group_id="e2e-group",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
    )
    completion_order: list[str] = []
    consumer = IndexingKafkaConsumer(
        logger,
        kafka_config,
        retry_manager=None,
        producer=AsyncMock(),
        fair_scheduler_config=fair_config,
    )
    broker = _FakeKafkaConsumer(log)
    consumer.consumer = broker
    consumer.message_handler = _make_handler(completion_order)
    consumer.running = True

    consumer._IndexingKafkaConsumer__start_worker_thread()
    assert consumer.worker_loop_ready.wait(timeout=5.0)
    consumer.main_loop = asyncio.get_running_loop()

    try:
        # One in-flight record per partition means the loop turns once per
        # record; the bound is generous but finite so a stall fails loudly
        # instead of hanging.
        for _ in range((len(log) + 1) * 4):
            if broker.exhausted and consumer._scheduler.is_empty:
                break
            await consumer._IndexingKafkaConsumer__read_phase()
            await consumer._IndexingKafkaConsumer__dispatch_phase()
            await _wait_until_idle(consumer)
        else:
            raise AssertionError(
                f"log not drained: {len(completion_order)} of {len(log)} "
                f"indexed, {consumer._scheduler.pending_count} still buffered"
            )
    finally:
        consumer._IndexingKafkaConsumer__stop_worker_thread()

    return completion_order, consumer, broker
