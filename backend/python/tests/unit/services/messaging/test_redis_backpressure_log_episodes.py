"""The Redis Streams indexing consumer logs one "Backpressure engaged" WARNING and one
"Backpressure cleared" INFO per backpressure episode, as the Kafka consumer does, in
both its fair read phase and its FIFO loop.

Waking on every finish (#5939) makes the pause condition flip on almost every finish
under sustained load. #5939 throttled the line with a process-wide
``log_once(..., 60)`` per group, which hides a genuine second episode that starts
within a minute of the first, logs an "engaged/cleared" pair a few ms apart once a
minute through one long episode, and lets one consumer of a group silence another.

The clock is faked in the consumer module and in ``log_once``'s, so a time-based throttle
coming back would fail here as it did on main; a flip lasts ``FLIP_GAP``, a genuine clear
``REAL_CLEAR`` (past the settle time, inside the old quiet minute). Boundary times are
binary-exact so ``now - since`` is exact.
"""
from __future__ import annotations

import logging
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.backpressure import BACKPRESSURE_CLEAR_SETTLE_SECONDS
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams import indexing_consumer as redis_module
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.utils import logging_fields
from tests.unit.services.messaging.test_redis_fair_finish_wakes_loop import (
    UNIT_LOGGER,
    _held_by_budget,
    _read_phase,
    _unit_consumer,
)

ENGAGED = "Backpressure engaged"
CLEARED = "Backpressure cleared"
FLIP_GAP = 0.25
TICK = 0.0625
REAL_CLEAR = 10.0
# Flips that keep one episode going for 70 s, past the old quiet minute.
LONG_FLIPS = 140


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    proxy = types.SimpleNamespace(**{n: getattr(time, n) for n in dir(time) if not n.startswith("_")})
    proxy.monotonic = lambda: fake.now
    for module in (redis_module, logging_fields):
        monkeypatch.setattr(module, "time", proxy)
    return fake


class _Load:
    """One consumer whose dispatch budget is blocked or not, turn by turn."""

    def __init__(self, consumer: IndexingRedisStreamsConsumer, clock: _Clock) -> None:
        self.consumer = consumer
        self.clock = clock
        self.blocked = {"now": False}
        _held_by_budget(consumer, self.blocked)
        consumer._finished.set()  # the backpressure wait returns at once; nothing here clears it

    async def turn(self, blocked: bool, advance: float = 0.0) -> None:
        self.clock.now += advance
        self.blocked["now"] = blocked
        await _read_phase(self.consumer)

    async def episode(self, flips: int) -> None:
        await self.turn(True)
        for _ in range(flips):
            await self.turn(False, FLIP_GAP)
            await self.turn(True, FLIP_GAP)

    async def settle(self) -> None:
        await self.turn(False)
        await self.turn(False, REAL_CLEAR)


@pytest.fixture
def load(clock: _Clock) -> _Load:
    consumer = _unit_consumer(group="g")
    return _Load(consumer, clock)


@pytest.fixture
def caplog_info(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger=UNIT_LOGGER)
    return caplog


def _count(caplog: pytest.LogCaptureFixture, level: int, prefix: str) -> int:
    return sum(
        1 for r in caplog.records
        if r.name == UNIT_LOGGER and r.levelno == level and r.getMessage().startswith(prefix)
    )


class TestOneLinePairPerEpisode:
    async def test_a_second_episode_after_a_genuine_clear_is_logged(
        self, load: _Load, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await load.episode(4)
        await load.settle()
        await load.episode(4)
        await load.settle()

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 2
        assert _count(caplog_info, logging.INFO, CLEARED) == 2

    async def test_flips_inside_an_episode_log_no_clear(
        self, load: _Load, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await load.episode(20)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 1
        assert _count(caplog_info, logging.INFO, CLEARED) == 0

    async def test_a_flapping_episode_longer_than_a_minute_logs_one_pair(
        self, load: _Load, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await load.episode(LONG_FLIPS)
        await load.settle()

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 1
        assert _count(caplog_info, logging.INFO, CLEARED) == 1


class TestSettleTime:
    async def test_a_gap_just_shorter_than_the_settle_time_stays_in_the_episode(
        self, load: _Load, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await load.turn(True)
        load.clock.now = 2048.0
        await load.turn(False)
        load.clock.now = 2048.0 + BACKPRESSURE_CLEAR_SETTLE_SECONDS - TICK
        await load.turn(False)
        await load.turn(True)

        assert _count(caplog_info, logging.INFO, CLEARED) == 0
        assert _count(caplog_info, logging.WARNING, ENGAGED) == 1

    async def test_a_gap_of_exactly_the_settle_time_ends_the_episode(
        self, load: _Load, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        await load.turn(True)
        load.clock.now = 2048.0
        await load.turn(False)
        assert _count(caplog_info, logging.INFO, CLEARED) == 0
        reads = load.consumer.redis.xreadgroup.await_count
        load.clock.now = 2048.0 + BACKPRESSURE_CLEAR_SETTLE_SECONDS
        await load.turn(False)

        assert _count(caplog_info, logging.INFO, CLEARED) == 1
        # The turn that ends the episode still reads, like every clear turn.
        assert load.consumer.redis.xreadgroup.await_count > reads
        assert not load.consumer.read_paused_for_admission()


class TestBehaviourTheLogChangeMustKeep:
    async def test_the_health_flag_follows_every_flip(self, load: _Load) -> None:
        await load.turn(True)
        assert load.consumer.read_paused_for_admission()
        await load.turn(False, FLIP_GAP)
        assert not load.consumer.read_paused_for_admission()
        await load.turn(True, FLIP_GAP)
        assert load.consumer.read_paused_for_admission()

    async def test_each_consumer_of_a_group_logs_its_own_episode(
        self, clock: _Clock, caplog_info: pytest.LogCaptureFixture
    ) -> None:
        """No process-wide throttle state: a second consumer of the same group still logs."""
        first, second = _Load(_unit_consumer(group="g"), clock), _Load(_unit_consumer(group="g"), clock)

        await first.turn(True)
        await second.turn(True)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 2

    async def test_a_restart_during_the_settle_time_logs_the_next_episode(
        self, load: _Load, caplog_info: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(redis_module, "spawn_detached", lambda coro: coro.close())
        await load.turn(True)
        await load.turn(False)

        await load.consumer.start(MagicMock())
        load.consumer._finished.set()
        await load.turn(True)

        assert _count(caplog_info, logging.WARNING, ENGAGED) == 2


FIFO_LOGGER = "test_redis_backpressure_fifo"


class TestFifoLoop:
    """Fair scheduling off: the FIFO branch of ``_consume_loop`` follows the same episode rule."""

    async def test_flips_then_a_settled_clear_log_one_pair(
        self, clock: _Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger(FIFO_LOGGER),
            RedisStreamsConfig(host="localhost", port=6379, block_ms=100, client_id="fifo", group_id="g",
                               topics=["record-events"]),
            retry_manager=None,
            producer=None,
        )
        assert consumer._scheduler is None
        consumer.redis = AsyncMock()
        consumer.redis.xreadgroup = AsyncMock(return_value=[])
        consumer.redis.xpending = AsyncMock(return_value={"pending": 0})
        consumer.redis.xautoclaim = AsyncMock(return_value=["0-0", [], []])
        consumer.running = True
        # (admission open?, seconds since the previous turn): a flip, then a clear that lasts the settle time.
        turns = iter([(False, 0.0), (True, FLIP_GAP), (False, FLIP_GAP), (True, FLIP_GAP),
                      (True, BACKPRESSURE_CLEAR_SETTLE_SECONDS)])

        def admission_open(**_: object) -> bool:
            step = next(turns, None)
            if step is None:
                consumer.running = False
                return True
            clock.now += step[1]
            return step[0]

        caplog.set_level(logging.INFO, logger=FIFO_LOGGER)
        with (
            patch.object(consumer, "_IndexingRedisStreamsConsumer__fifo_admission_open", side_effect=admission_open),
            patch.object(consumer, "_drain_pending", new_callable=AsyncMock),
            patch.object(consumer, "_IndexingRedisStreamsConsumer__wait_for_finish", AsyncMock(return_value=False)),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await consumer._consume_loop()

        lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == FIFO_LOGGER]
        assert [lvl for lvl, msg in lines if msg.startswith(ENGAGED)] == [logging.WARNING]
        assert [lvl for lvl, msg in lines if msg.startswith(CLEARED)] == [logging.INFO]
        assert not consumer.read_paused_for_admission()
