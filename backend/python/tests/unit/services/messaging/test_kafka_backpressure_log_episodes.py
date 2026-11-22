"""The Kafka indexing consumer logs one "Backpressure engaged" WARNING and one
"Backpressure cleared" INFO per backpressure episode, not per loop turn.

With both index pools saturated, a finishing record frees one permit for a
loop turn and the next dispatch takes it again, so the saturation condition
flips on almost every finish. Those flips are not episodes: logging each of
them gave about 190 WARNING + 190 INFO lines per 10 s on a saturated node,
while partitions must still pause and resume on every flip.

The clock is the module's ``time.monotonic``, faked per test: a flip lasts one
busy poll (``FLIP_GAP``); a genuine clear lasts far longer than the settle
time (``LONG_CLEAR``). Boundary times are binary-exact so ``now - since`` is
exact.
"""
from __future__ import annotations

import logging
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka import TopicPartition

from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.backpressure import BACKPRESSURE_CLEAR_SETTLE_SECONDS
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as ic
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

ENGAGED = "Backpressure engaged: both index pools saturated"
CLEARED = "Backpressure cleared: "
TP = TopicPartition("record-events", 0)
FLIP_GAP = 0.05  # one busy poll (_BUSY_POLL_TIMEOUT_MS)
LONG_CLEAR = 3600.0
TICK = 0.0625
# Enough flips that the episode outlasts the settle time.
FLIPS = 100


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(ic, "time", types.SimpleNamespace(monotonic=lambda: fake.now, time=time.time))
    return fake


def _consumer(name: str) -> IndexingKafkaConsumer:
    consumer = IndexingKafkaConsumer(
        logging.getLogger(name),
        KafkaConsumerConfig(
            topics=["record-events"],
            client_id="c",
            group_id="g",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            bootstrap_servers=["b:9092"],
        ),
        fair_scheduler_config=FairSchedulerConfig(enabled=True, key_fields=("orgId", "connectorId")),
    )
    consumer.governor = make_test_governor()
    # A broker that remembers what is paused, so resume has something to act on.
    paused: set[TopicPartition] = set()
    consumer.consumer = MagicMock()
    consumer.consumer.commit = AsyncMock()
    consumer.consumer.assignment.return_value = {TP}
    consumer.consumer.paused.side_effect = lambda: set(paused)
    consumer.consumer.pause.side_effect = lambda *tps: paused.update(tps)
    consumer.consumer.resume.side_effect = lambda *tps: paused.difference_update(tps)
    consumer.running = True
    return consumer


@pytest.fixture
def consumer() -> IndexingKafkaConsumer:
    return _consumer("test_kafka_backpressure_log_episodes")


@pytest.fixture
def caplog_info(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO)
    return caplog


def _turn(consumer: IndexingKafkaConsumer) -> None:
    consumer._IndexingKafkaConsumer__apply_backpressure()


async def _saturate(consumer: IndexingKafkaConsumer) -> None:
    for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT):
        gate = consumer.governor.gate(pool)
        while gate.in_use < gate.limit:
            assert await gate.acquire()
    assert concurrency.index_gates_saturated(consumer)


def _drain(consumer: IndexingKafkaConsumer) -> None:
    for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT):
        gate = consumer.governor.gate(pool)
        while gate.in_use:
            gate.release()
    assert not concurrency.index_gates_saturated(consumer)


async def _flip(consumer: IndexingKafkaConsumer, clock: _Clock) -> None:
    """One finish under sustained load: a permit frees for a loop turn, then
    the next dispatch takes it again."""
    gate = consumer.governor.gate(Pool.INDEX_LIGHT)
    gate.release()
    clock.advance(FLIP_GAP)
    _turn(consumer)
    assert not concurrency.index_gates_saturated(consumer)
    assert await gate.acquire()
    clock.advance(FLIP_GAP)
    _turn(consumer)


async def _episode(consumer: IndexingKafkaConsumer, clock: _Clock, flips: int) -> None:
    await _saturate(consumer)
    _turn(consumer)
    for _ in range(flips):
        await _flip(consumer, clock)


def _settle(consumer: IndexingKafkaConsumer, clock: _Clock) -> None:
    """The load drains and stays drained for a long time."""
    _drain(consumer)
    _turn(consumer)
    clock.advance(LONG_CLEAR)
    _turn(consumer)
    _turn(consumer)


def _count(caplog: pytest.LogCaptureFixture, level: int, prefix: str) -> int:
    return sum(1 for r in caplog.records if r.levelno == level and r.getMessage().startswith(prefix))


class TestOneLinePairPerEpisode:
    async def test_finishes_under_sustained_saturation_log_one_warning_and_no_clear(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await _episode(consumer, clock, FLIPS)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 1
        assert _count(caplog_info, logging.INFO, CLEARED) == 0

    async def test_the_end_of_an_episode_logs_one_clear(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await _episode(consumer, clock, FLIPS)
        _settle(consumer, clock)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 1
        assert _count(caplog_info, logging.INFO, CLEARED) == 1

    async def test_engaging_again_after_a_genuine_clear_logs_again(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await _episode(consumer, clock, 10)
        _settle(consumer, clock)
        await _episode(consumer, clock, 10)
        _settle(consumer, clock)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 2
        assert _count(caplog_info, logging.INFO, CLEARED) == 2


class TestSettleTime:
    async def test_a_flip_long_after_the_last_one_is_still_the_same_episode(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await _episode(consumer, clock, 1)
        clock.advance(LONG_CLEAR)
        _turn(consumer)
        await _flip(consumer, clock)

        assert _count(caplog_info, logging.INFO, CLEARED) == 0

    async def test_a_gap_just_shorter_than_the_settle_time_stays_in_the_episode(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        assert BACKPRESSURE_CLEAR_SETTLE_SECONDS >= 1
        await _saturate(consumer)
        _turn(consumer)
        clock.now = 2048.0
        _drain(consumer)
        _turn(consumer)
        clock.now = 2048.0 + BACKPRESSURE_CLEAR_SETTLE_SECONDS - TICK
        _turn(consumer)
        await _saturate(consumer)
        _turn(consumer)

        assert _count(caplog_info, logging.INFO, CLEARED) == 0
        assert _count(caplog_info, logging.WARNING, ENGAGED) == 1

    async def test_two_long_clear_turns_stay_in_the_episode(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        """A clear turn can block up to MESSAGE_TIMEOUT_MS (2 s by default), so a
        gap of two such turns is still a flip, whatever the settle constant says."""
        await _saturate(consumer)
        _turn(consumer)
        _drain(consumer)
        for now in (2048.0, 2050.0, 2052.0):
            clock.now = now
            _turn(consumer)
        await _saturate(consumer)
        _turn(consumer)

        assert _count(caplog_info, logging.INFO, CLEARED) == 0

    async def test_a_gap_of_exactly_the_settle_time_ends_the_episode(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await _saturate(consumer)
        _turn(consumer)
        clock.now = 2048.0
        _drain(consumer)
        _turn(consumer)
        clock.now = 2048.0 + BACKPRESSURE_CLEAR_SETTLE_SECONDS
        _turn(consumer)

        assert _count(caplog_info, logging.INFO, CLEARED) == 1


class TestBehaviourTheLogChangeMustKeep:
    async def test_partitions_still_pause_and_resume_on_every_flip(
        self, consumer: IndexingKafkaConsumer, clock: _Clock
    ) -> None:
        await _episode(consumer, clock, FLIPS)

        assert consumer.consumer.pause.call_count == FLIPS + 1
        assert consumer.consumer.resume.call_count == FLIPS
        assert consumer.consumer.paused() == {TP}

    async def test_a_long_episode_logs_no_clear_while_still_saturated(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await _episode(consumer, clock, 1)
        clock.advance(LONG_CLEAR)
        _turn(consumer)

        assert _count(caplog_info, logging.INFO, CLEARED) == 0

    async def test_a_restart_during_the_settle_time_logs_the_next_episode(
        self, consumer: IndexingKafkaConsumer, clock: _Clock, caplog_info: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ic, "spawn_detached", lambda coro: coro.close())
        await _episode(consumer, clock, 0)
        _drain(consumer)
        _turn(consumer)

        await consumer.start(MagicMock())
        await _episode(consumer, clock, 0)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 2

    async def test_each_consumer_in_a_process_logs_its_own_episode(
        self, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        """No process-wide throttle state: a second consumer of the same group still logs."""
        first, second = _consumer("bp_episode_first"), _consumer("bp_episode_second")

        await _episode(first, clock, 0)
        await _episode(second, clock, 0)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 2
