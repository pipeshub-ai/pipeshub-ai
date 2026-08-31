"""Capacity gating — the mechanism that actually balances work across executors.

Distinct consumer names alone balance nothing: the sync handler returns as soon
as a task spawns, so an executor already running twenty syncs asks for the next
message just as fast as an idle one. Only refusing to ask moves work elsewhere.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer

_LOG = logging.getLogger("test")


def _redis_consumer(gate) -> RedisStreamsConsumer:
    c = RedisStreamsConsumer(
        _LOG, RedisStreamsConfig(host="h", port=1, block_ms=100, topics=["sync-events"])
    )
    c.capacity_gate = gate
    c.redis = AsyncMock()
    c.redis.xreadgroup = AsyncMock(return_value=[])
    c._drain_pending = AsyncMock()
    return c


async def _run_until_first_read(c: RedisStreamsConsumer) -> None:
    """Stop the loop the moment it asks for a message.

    The mock returns instantly, so letting the loop run on a timer would spin
    millions of times rather than measure anything.
    """

    async def _once(*_a, **_k):
        c.running = False
        return []

    c.redis.xreadgroup = AsyncMock(side_effect=_once)
    c.running = True
    await c._consume_loop()


class TestRedisGate:
    @pytest.mark.asyncio
    async def test_closed_gate_never_asks_for_a_message(self) -> None:
        """The message stays in the stream for a peer with room."""
        c = _redis_consumer(lambda: False)
        c.running = True

        loop = asyncio.get_running_loop()
        loop.call_later(0.25, lambda: setattr(c, "running", False))
        await c._consume_loop()

        c.redis.xreadgroup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_open_gate_reads_normally(self) -> None:
        c = _redis_consumer(lambda: True)
        await _run_until_first_read(c)
        assert c.redis.xreadgroup.await_count == 1

    @pytest.mark.asyncio
    async def test_no_gate_configured_reads_normally(self) -> None:
        """Absent a gate the consumer must behave exactly as before."""
        c = _redis_consumer(None)
        await _run_until_first_read(c)
        assert c.redis.xreadgroup.await_count == 1

    @pytest.mark.asyncio
    async def test_closed_gate_yields_instead_of_spinning(self) -> None:
        """A bare `continue` would burn a core on a box that is already
        CPU-bound — the loop normally idles inside XREADGROUP's block."""
        calls = 0

        def gate() -> bool:
            nonlocal calls
            calls += 1
            return False

        c = _redis_consumer(gate)
        c.running = True
        loop = asyncio.get_running_loop()
        loop.call_later(0.3, lambda: setattr(c, "running", False))
        await c._consume_loop()

        # block_ms=100 -> ~0.1s per iteration, so a third of a second is a
        # handful of passes, not thousands.
        assert calls < 50


class TestKafkaGate:
    def _consumer(self, gate) -> KafkaMessagingConsumer:
        c = KafkaMessagingConsumer(
            _LOG,
            KafkaConsumerConfig(
                client_id="x",
                group_id="g",
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                bootstrap_servers=["b:9092"],
                topics=["sync-events"],
            ),
        )
        c.capacity_gate = gate
        c.consumer = MagicMock()
        c.consumer.assignment.return_value = {"tp0", "tp1"}
        c.consumer.paused.return_value = set()
        return c

    def test_closed_gate_pauses_assigned_partitions(self) -> None:
        """Kafka cannot simply stop polling — the coordinator would evict the
        member on max_poll_interval_ms — so it pauses and keeps polling."""
        c = self._consumer(lambda: False)
        c._apply_backpressure()
        c.consumer.pause.assert_called_once()

    def test_open_gate_resumes_paused_partitions(self) -> None:
        c = self._consumer(lambda: True)
        c.consumer.paused.return_value = {"tp0"}
        c._apply_backpressure()
        c.consumer.resume.assert_called_once()

    def test_no_gate_is_a_no_op(self) -> None:
        c = self._consumer(None)
        c._apply_backpressure()
        c.consumer.pause.assert_not_called()
        c.consumer.resume.assert_not_called()

    def test_pause_is_not_repeated_while_still_full(self) -> None:
        c = self._consumer(lambda: False)
        c._apply_backpressure()
        c.consumer.paused.return_value = {"tp0", "tp1"}
        c._apply_backpressure()
        assert c.consumer.pause.call_count == 1
