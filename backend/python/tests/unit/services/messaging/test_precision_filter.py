"""Each consumer hands back, as it reads them, the events that would write vectors of the wrong precision."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka.structs import TopicPartition

from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.precision_check import HANDBACKS_KEY
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)

log = logging.getLogger("filter-test")


class _Filter:
    def __init__(self, delays: dict[int, float]) -> None:
        self.delays = delays
        self.seen: list = []

    def hand_back(self, messages):  # noqa: ANN202
        self.seen.append(list(messages))
        return self.delays


def _message(event="newRecord", **payload):  # noqa: ANN202
    return SimpleNamespace(eventType=event, payload=dict(payload))


class TestKafka:
    def _consumer(self, delays):  # noqa: ANN202
        consumer = object.__new__(IndexingKafkaConsumer)
        consumer.logger = log
        consumer.precision_filter = _Filter(delays)
        consumer.consumer = MagicMock()
        consumer._requeue_message = AsyncMock()
        return consumer

    def test_the_connector_off_settled_messages_are_not_offered_to_the_filter(self) -> None:
        consumer = self._consumer({0: 60.0})
        tp = TopicPartition("t", 0)
        pre_parsed = {(tp, 1): _message(), (tp, 2): _message()}
        handed = consumer._IndexingKafkaConsumer__precision_hand_backs(pre_parsed, {(tp, 2): pre_parsed[(tp, 2)]})
        assert list(handed) == [(tp, 1)] and handed[(tp, 1)][1] == 60.0
        assert len(consumer.precision_filter.seen[0]) == 1

    def test_no_filter_or_a_filter_that_raises_hands_nothing_back(self) -> None:
        consumer = self._consumer({})
        consumer.precision_filter = None
        assert consumer._IndexingKafkaConsumer__precision_hand_backs({("x", 1): _message()}, {}) == {}
        consumer.precision_filter = MagicMock(hand_back=MagicMock(side_effect=RuntimeError("x")))
        assert consumer._IndexingKafkaConsumer__precision_hand_backs({("x", 1): _message()}, {}) == {}

    @pytest.mark.asyncio
    async def test_only_the_positions_the_read_reached_are_published_and_counted_once(self) -> None:
        consumer = self._consumer({})
        tp = TopicPartition("t", 0)
        first, second = _message(**{HANDBACKS_KEY: 1}), _message()
        handed = {(tp, 1): (first, 600.0), (tp, 2): (second, 60.0)}
        resolvable = await consumer._IndexingKafkaConsumer__publish_hand_backs([(tp, 1)], handed)  # the loop never reached 2
        assert resolvable == [(tp, 1)]
        consumer._requeue_message.assert_awaited_once()
        kwargs = consumer._requeue_message.await_args.kwargs
        assert kwargs["delay_s"] == 600.0 and kwargs["retry_count"] == 0 and kwargs["extra_payload"] == {HANDBACKS_KEY: 2}

    @pytest.mark.asyncio
    async def test_a_failed_publish_leaves_the_position_unresolved_and_reads_it_again(self) -> None:
        consumer = self._consumer({})
        consumer._requeue_message = AsyncMock(side_effect=[RuntimeError("broker down"), None])
        tp = TopicPartition("t", 0)
        handed = {(tp, 1): (_message(), 60.0), (tp, 2): (_message(), 60.0)}
        resolvable = await consumer._IndexingKafkaConsumer__publish_hand_backs([(tp, 1), (tp, 2)], handed)
        assert resolvable == [(tp, 2)]
        consumer.consumer.seek.assert_called_once_with(tp, 1)

    @pytest.mark.asyncio
    async def test_positions_that_are_not_handed_back_pass_through(self) -> None:
        consumer = self._consumer({})
        tp = TopicPartition("t", 0)
        assert await consumer._IndexingKafkaConsumer__publish_hand_backs([(tp, 7)], {}) == [(tp, 7)]
        consumer._requeue_message.assert_not_awaited()


class TestRedis:
    def _consumer(self):  # noqa: ANN202
        consumer = object.__new__(IndexingRedisStreamsConsumer)
        consumer.logger = log
        consumer.redis = MagicMock(xack=AsyncMock())
        consumer.config = SimpleNamespace(group_id="g")
        consumer._requeue_message = AsyncMock()
        return consumer

    @pytest.mark.asyncio
    async def test_an_entry_is_republished_and_then_acknowledged(self) -> None:
        consumer = self._consumer()
        order: list[str] = []
        consumer._requeue_message = AsyncMock(side_effect=lambda *a, **k: order.append("requeue"))
        consumer.redis.xack = AsyncMock(side_effect=lambda *a, **k: order.append("ack"))
        await consumer._IndexingRedisStreamsConsumer__hand_back_entry("s", "1-0", _message(), 60.0)
        assert order == ["requeue", "ack"]

    @pytest.mark.asyncio
    async def test_a_failed_publish_leaves_the_entry_pending(self) -> None:
        consumer = self._consumer()
        consumer._requeue_message = AsyncMock(side_effect=RuntimeError("down"))
        await consumer._IndexingRedisStreamsConsumer__hand_back_entry("s", "1-0", _message(), 60.0)
        consumer.redis.xack.assert_not_awaited()

    def test_events_already_settled_or_unparsed_are_not_offered(self) -> None:
        consumer = self._consumer()
        consumer.precision_filter = _Filter({0: 60.0})
        parsed = [_message(), _message(), None]
        assert consumer._IndexingRedisStreamsConsumer__precision_hand_backs(parsed, {1}) == {0: 60.0}
        assert consumer.precision_filter.seen[0][1] is None and consumer.precision_filter.seen[0][2] is None


class TestPostgres:
    def _consumer(self, delays, message="m"):  # noqa: ANN202
        consumer = object.__new__(PgMessagingConsumer)
        consumer.logger = log
        consumer.indexing = True
        consumer.config = SimpleNamespace(group_id="g")
        consumer.precision_filter = _Filter(delays)
        consumer.queue = MagicMock(nack=AsyncMock())
        consumer._handbacks = {}
        consumer._handback_log = {}
        consumer._parse_for_sink = AsyncMock(return_value=_message() if message else None)
        return consumer

    @pytest.mark.asyncio
    async def test_a_vector_writing_event_is_nacked_with_a_refund_before_anything_runs(self) -> None:
        consumer = self._consumer({0: 60.0})
        d = SimpleNamespace(topic="t", message_id=5)
        assert await consumer._hand_back_for_precision(d) is True
        consumer.queue.nack.assert_awaited_once()
        args, kwargs = consumer.queue.nack.await_args
        assert args[2] == 60.0 and kwargs["refund"] is True

    @pytest.mark.asyncio
    async def test_the_second_hand_back_of_a_message_waits_ten_times_as_long(self) -> None:
        consumer = self._consumer({0: 60.0})
        d = SimpleNamespace(topic="t", message_id=5)
        await consumer._hand_back_for_precision(d)
        await consumer._hand_back_for_precision(d)
        assert [c.args[2] for c in consumer.queue.nack.await_args_list] == [60.0, 600.0]

    @pytest.mark.asyncio
    async def test_other_events_and_non_indexing_consumers_take_the_normal_path(self) -> None:
        consumer = self._consumer({})
        assert await consumer._hand_back_for_precision(SimpleNamespace(topic="t", message_id=1)) is False
        consumer.indexing = False
        assert await consumer._hand_back_for_precision(SimpleNamespace(topic="t", message_id=1)) is False
        consumer.queue.nack.assert_not_awaited()


class TestPostgresFlipHandBack:
    @pytest.mark.asyncio
    async def test_a_flip_in_a_running_record_is_nacked_with_a_refund_and_never_dead_lettered(self) -> None:
        from app.services.messaging.precision_flip import PrecisionFlip

        consumer = object.__new__(PgMessagingConsumer)
        consumer.logger = log
        consumer.indexing = True
        consumer.precision_filter = None
        consumer.config = SimpleNamespace(group_id="g")
        consumer.stage_admission = None
        consumer._paused_until = 0.0
        consumer._handbacks = {}
        consumer._handback_log = {}
        consumer.queue = MagicMock(nack=AsyncMock(), ack=AsyncMock())
        consumer._process = AsyncMock(side_effect=PrecisionFlip("the index holds int8-w", delay_s=60.0))
        consumer._abandon = AsyncMock()
        d = SimpleNamespace(topic="t", message_id=5, attempts=1)
        await consumer._settle(d)
        consumer._abandon.assert_not_awaited()
        args, kwargs = consumer.queue.nack.await_args
        assert args[2] >= 60.0 and kwargs["refund"] is True
        assert consumer._paused_until == 0.0  # only this message waits
