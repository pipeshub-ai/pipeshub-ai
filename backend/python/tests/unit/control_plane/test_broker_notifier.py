"""CN-1..3, NTF-03/04 (CPD-6): ``BrokerChangeNotifier`` on the real Redis Streams and Kafka consumers of
``MessagingFactory``; only the broker is in memory (``fakeredis`` streams, ``tests.support.fake_kafka``)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

pytest.importorskip("fakeredis.aioredis")

from app.control_plane.runtime.notifier import (
    CHANGE_EVENT_TYPE,
    CHANGE_TOPIC,
    BrokerChangeNotifier,
)
from app.control_plane.testing.contracts import (
    ORG,
    ChangeNotifierContract,
    change_event,
)
from app.services.messaging.config import (
    MessageBrokerType,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import consumer as kafka_consumer_module
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.redis_streams.broadcast_consumer import (
    RedisStreamsBroadcastConsumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from harness_core.contracts import dump_contract
from tests.support.fake_kafka import FakeKafkaBroker
from tests.unit.services.messaging.test_redis_streams_broadcast_consumer import (
    NonBlockingXreadProvider,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from pydantic import JsonValue

    from app.control_plane.contracts.generated.change_event import ChangeEvent
    from app.services.messaging.interface.consumer import IMessagingConsumer

LOGGER = logging.getLogger("tests.cpd6.notifier")


async def _until(predicate: Any, timeout: float = 5.0) -> None:  # noqa: ANN401
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("the consumer did not settle")
        await asyncio.sleep(0.005)


def _redis_config(group: str) -> RedisStreamsConfig:
    return RedisStreamsConfig(
        topics=[CHANGE_TOPIC], group_id=group, client_id=group, batch_size=10, block_ms=5, broadcast=True,
    )


@dataclass
class _RedisHarness:
    provider: NonBlockingXreadProvider
    notifier: BrokerChangeNotifier
    consumer: RedisStreamsBroadcastConsumer

    async def settled(self) -> None:
        client = self.provider.get_client()

        async def handled_everything() -> bool:
            if not await client.exists(CHANGE_TOPIC):
                return True
            last = (await client.xinfo_stream(CHANGE_TOPIC))["last-generated-id"]
            return self.consumer._positions[CHANGE_TOPIC] == last  # pyright: ignore[reportPrivateUsage]

        await _until(handled_everything)


async def _redis_notifier(provider: NonBlockingXreadProvider, group: str) -> _RedisHarness:
    consumer = RedisStreamsBroadcastConsumer(LOGGER, _redis_config(group), provider=provider)
    producer = RedisStreamsProducer(LOGGER, _redis_config(group), provider=provider)
    await producer.initialize()
    notifier = BrokerChangeNotifier(consumer, producer)
    await notifier.start()
    return _RedisHarness(provider, notifier, consumer)


class TestRedisStreamsChangeNotifierContract(ChangeNotifierContract):
    @pytest.fixture
    async def notifier_harness(self) -> AsyncIterator[_RedisHarness]:
        provider = NonBlockingXreadProvider(is_cluster=False)
        harness = await _redis_notifier(provider, "cp-process-1")
        yield harness
        await harness.notifier.stop()

    async def test_every_process_sees_every_event_and_one_started_later_gets_no_replay(
        self, notifier_harness: _RedisHarness
    ) -> None:
        h = notifier_harness
        first: list[ChangeEvent] = []
        _ = h.notifier.subscribe(first.append)
        await h.notifier.publish(change_event(ORG, 1))
        await h.settled()

        other = await _redis_notifier(h.provider, "cp-process-2")
        second: list[ChangeEvent] = []
        _ = other.notifier.subscribe(second.append)
        await h.notifier.publish(change_event(ORG, 2))
        await h.settled()
        await other.settled()
        groups = {g["name"] for g in await h.provider.get_client().xinfo_groups(CHANGE_TOPIC)}
        await other.notifier.stop()

        assert [e.revision for e in first] == [1, 2]
        assert [e.revision for e in second] == [2]
        assert groups == set(), "a subscriber holds no consumer group, so a process that dies leaves none"

    async def test_the_message_is_the_broker_envelope_keyed_by_org(self, notifier_harness: _RedisHarness) -> None:
        import json

        h = notifier_harness
        event = change_event(ORG, 4)
        await h.notifier.publish(event)

        [(_, fields)] = await h.provider.get_client().xrange(CHANGE_TOPIC)
        envelope = json.loads(fields["value"])
        assert fields["key"] == ORG
        assert (envelope["eventType"], envelope["payload"]) == (CHANGE_EVENT_TYPE, dump_contract(event))


class _LogProducer:
    """A producer onto the fake Kafka log: what ``KafkaMessagingProducer.send_message`` leaves on the topic."""

    def __init__(self, broker: FakeKafkaBroker) -> None:
        self._broker = broker
        self.keys: list[str | None] = []

    async def send_message(self, topic: str, message: dict[str, JsonValue], key: str | None = None) -> bool:
        self.keys.append(key)
        _ = self._broker.produce(topic, message)
        return True


@dataclass
class _KafkaHarness:
    broker: FakeKafkaBroker
    notifier: BrokerChangeNotifier
    consumer: IMessagingConsumer

    async def settled(self) -> None:
        async def committed() -> bool:
            produced = sum(len(log) for tp, log in self.broker.logs.items() if tp.topic == CHANGE_TOPIC)
            return (self.broker.committed_offset("cp-process-1", CHANGE_TOPIC) or 0) == produced

        await _until(committed)


class TestKafkaChangeNotifierContract(ChangeNotifierContract):
    @pytest.fixture
    def broker(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKafkaBroker]:
        monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "5")
        broker = FakeKafkaBroker()
        with patch.object(kafka_consumer_module, "AIOKafkaConsumer", broker.consumer_factory()):
            yield broker

    @pytest.fixture
    async def notifier_harness(self, broker: FakeKafkaBroker) -> AsyncIterator[_KafkaHarness]:
        config = KafkaConsumerConfig(
            topics=[CHANGE_TOPIC], client_id="cp-process-1", group_id="cp-process-1", auto_offset_reset="latest",
            enable_auto_commit=False, bootstrap_servers=["kafka:9092"],
        )
        consumer = MessagingFactory.create_consumer(LOGGER, config, MessageBrokerType.KAFKA)
        notifier = BrokerChangeNotifier(consumer, _LogProducer(broker))  # type: ignore[arg-type]
        await notifier.start()
        yield _KafkaHarness(broker, notifier, consumer)
        await notifier.stop()


class _HandedConsumer:
    """A consumer the test plays the broker for: ``deliver`` is one message arriving."""

    def __init__(self) -> None:
        self.handler: Any = None
        self.stopped = False

    async def start(self, message_handler: Any) -> None:  # noqa: ANN401
        self.handler = message_handler

    async def stop(self, message_handler: Any = None) -> None:  # noqa: ANN401
        self.stopped = True

    async def deliver(self, event_type: str, payload: dict[str, JsonValue]) -> bool:
        return await self.handler(StreamMessage(eventType=event_type, payload=payload))


async def _started() -> tuple[_HandedConsumer, BrokerChangeNotifier, list[ChangeEvent]]:
    consumer = _HandedConsumer()
    notifier = BrokerChangeNotifier(consumer)  # type: ignore[arg-type]
    await notifier.start()
    got: list[ChangeEvent] = []
    _ = notifier.subscribe(got.append)
    return consumer, notifier, got


async def test_ntf13_a_message_that_is_not_a_change_event_is_settled_skipped_and_logged_without_its_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    consumer, _, got = await _started()
    valid = dump_contract(change_event(ORG, 3))

    with caplog.at_level(logging.DEBUG):
        results = [
            await consumer.deliver(CHANGE_EVENT_TYPE, {**valid, "apiKey": "sk-canary-0123456789abcdef"}),
            await consumer.deliver(CHANGE_EVENT_TYPE, {**valid, "live": {"org": {"approvals": {"writes": "deny"}}}}),
            await consumer.deliver("userAdded", valid),
            await consumer.deliver(CHANGE_EVENT_TYPE, valid),
        ]

    assert results == [True, True, True, True]
    assert [e.revision for e in got] == [3]
    assert caplog.text.count("change_event_invalid") == 2
    assert caplog.text.count("change_event_type_unknown") == 1
    assert "canary" not in caplog.text


async def test_a_subscriber_that_raises_does_not_cost_the_others_the_event(caplog: pytest.LogCaptureFixture) -> None:
    consumer = _HandedConsumer()
    notifier = BrokerChangeNotifier(consumer)  # type: ignore[arg-type]
    await notifier.start()
    got: list[ChangeEvent] = []

    def broken(_event: ChangeEvent) -> None:
        raise RuntimeError("subscriber bug")

    _ = notifier.subscribe(broken)
    _ = notifier.subscribe(got.append)
    with caplog.at_level(logging.WARNING):
        settled = await consumer.deliver(CHANGE_EVENT_TYPE, dump_contract(change_event(ORG, 9)))

    assert settled
    assert [e.revision for e in got] == [9]
    assert "change_handler_failed" in caplog.text
    assert ORG in caplog.text


async def test_handlers_run_on_the_loop_that_started_the_notifier() -> None:
    import threading

    consumer = _HandedConsumer()
    notifier = BrokerChangeNotifier(consumer)  # type: ignore[arg-type]
    await notifier.start()
    handled_on: list[int] = []
    _ = notifier.subscribe(lambda _event: handled_on.append(threading.get_ident()))
    payload = dump_contract(change_event(ORG, 5))

    # The consumer polls on a loop of its own, in another thread.
    await asyncio.to_thread(lambda: asyncio.run(consumer.deliver(CHANGE_EVENT_TYPE, payload)))
    await asyncio.sleep(0)

    assert handled_on == [threading.get_ident()]


async def test_a_process_that_does_not_publish_refuses_to() -> None:
    _, notifier, _ = await _started()
    with pytest.raises(RuntimeError):
        await notifier.publish(change_event(ORG, 1))


async def test_stop_leaves_the_topic() -> None:
    consumer, notifier, _ = await _started()
    await notifier.stop()
    assert consumer.stopped


class _Container:
    """The part of an app container the consumer config reads: the configuration service."""

    def __init__(self) -> None:
        self._config = _ConfigService()

    def config_service(self) -> _ConfigService:
        return self._config


class _ConfigService:
    async def get_config(self, _key: str) -> dict[str, Any]:
        return {"brokers": ["kafka:9092"]}

    async def get_redis_config(self) -> Any:  # noqa: ANN401
        from app.services.messaging.config import RedisConfig

        return RedisConfig(host="redis", port=6379)


@pytest.mark.parametrize("broker_type", ["kafka", "redis"])
async def test_the_consumer_config_is_a_group_of_this_process_starting_at_the_tail(
    broker_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.messaging import utils as messaging_utils
    from app.services.messaging.utils import MessagingUtils

    monkeypatch.setenv("MESSAGE_BROKER", broker_type)
    config = await MessagingUtils.create_control_plane_events_consumer_config(_Container())  # type: ignore[arg-type]
    monkeypatch.setattr(messaging_utils, "_process_id", "another-process")
    other = await MessagingUtils.create_control_plane_events_consumer_config(_Container())  # type: ignore[arg-type]

    assert config.topics == [CHANGE_TOPIC] == ["control-plane-events"]
    assert config.group_id != other.group_id
    if isinstance(config, KafkaConsumerConfig):
        assert config.auto_offset_reset == "latest"
    else:
        assert config.broadcast
