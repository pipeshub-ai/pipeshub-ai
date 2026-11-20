"""The record consumers with nothing extra supplied behave exactly as they did before the planner hook existed.

The goldens in ``goldens/record_consumer_seam_off`` were written by ``tests/support/regen_record_consumer_goldens.py``
on the commit before the hook. Each test replays the same generated stream through the real read, dispatch and
claim code on the in-memory brokers of ``tests/support/record_consumer_world.py``, with an injected clock, and
compares dispatch order, the commit or ack sequence, the buffer, remembered-position and parked-entry counters
step by step, and the claim statements of the queue.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.support import record_consumer_world as world

if TYPE_CHECKING:
    from collections.abc import Iterator

GOLDENS = Path(__file__).parent / "goldens" / "record_consumer_seam_off"

pytestmark = pytest.mark.timeout(240)


@pytest.fixture(autouse=True)
def _world_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in world.GOLDEN_ENV.items():
        monkeypatch.setenv(key, value)
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def golden(kind: str) -> dict:
    return json.loads((GOLDENS / f"{kind}.json").read_text())


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
def test_constructor_state_is_the_default(kind: str) -> None:
    assert world.constructor_state(kind) == golden(kind)["constructor"]


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
async def test_replay_equals_the_golden(kind: str) -> None:
    assert await world.collect(kind) == golden(kind)["run"]


def test_the_queue_module_is_unchanged() -> None:
    import hashlib

    source = Path(world.__file__).parents[2] / "app" / "services" / "messaging" / "postgres" / "queue.py"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == golden("pg")["queue_sha256"]


# -- a default consumer made through the factory ---------------------------------------------------------------------
def make_default(kind: str) -> object:
    from app.services.messaging.config import (
        ConsumerType,
        MessageBrokerType,
        RedisStreamsConfig,
    )
    from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
    from app.services.messaging.messaging_factory import MessagingFactory

    log = logging.getLogger("factory-defaults")
    if kind == "kafka":
        config = KafkaConsumerConfig(
            topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["k:1"],
        )
        broker = MessageBrokerType.KAFKA
    else:
        config = RedisStreamsConfig(host="h", port=1, client_id="c", group_id="g", topics=["record-events"])
        broker = MessageBrokerType.REDIS if kind == "redis" else MessageBrokerType.POSTGRES
    return MessagingFactory.create_consumer(log, config, broker_type=broker, consumer_type=ConsumerType.INDEXING)


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
def test_the_factory_gives_a_default_record_consumer_its_own_topics_and_extractor(kind: str) -> None:
    consumer = make_default(kind)
    cfg = consumer.kafka_config if kind == "kafka" else consumer.config
    assert list(cfg.topics) == ["record-events"]
    if kind != "pg":
        assert type(consumer.key_extractor.inner).__name__ == "CompositeKeyExtractor"
        assert consumer.key_extractor.inner.extract(world_message()) == ("o", "c")
        assert consumer.admission_backlog.observer is None
        assert consumer._fairness is None
    else:
        assert consumer._fairness is None


def world_message() -> object:
    from app.services.messaging.config import StreamMessage

    return StreamMessage(
        eventType="newRecord", payload={"recordId": "r", "orgId": "o", "connectorId": "c"}, timestamp=1
    )


# -- mutation controls: each wrong edit of the default path must be noticed by the checks above ------------------------
class StubPlanner:
    """Lets every key through; stands in for SlotShare in the planner mutation."""

    def __init__(self, *_args: object) -> None:
        pass

    def started(self, *_args: object) -> None:
        pass

    admitted = finished = started

    def plan(self, *_args: object) -> object:
        class Plan:
            def allows(self, _key: object) -> bool:
                return True

        return Plan()


def apply(monkeypatch: pytest.MonkeyPatch, cls: type, method: str, replacements: list[tuple[str, str]]) -> None:
    from tests.support.mutated_method import mutate

    monkeypatch.setattr(cls, method, getattr(cls, method))  # put back when the test ends
    mutate(cls, method, replacements)


def kafka_class() -> type:
    from app.services.messaging.kafka.consumer.indexing_consumer import (
        IndexingKafkaConsumer,
    )

    return IndexingKafkaConsumer


async def detected(kind: str) -> bool:
    # a mutated Kafka run that stalls would otherwise run to the step limit; the golden ends in 252 steps
    world_args = {"max_steps": 300} if kind == "kafka" else {}
    try:
        return (
            world.constructor_state(kind) != golden(kind)["constructor"]
            or await world.collect(kind, **world_args) != golden(kind)["run"]
        )
    except Exception:  # a mutation that breaks the consumer outright is noticed too
        return True


async def test_m1_a_default_factory_that_appends_a_topic_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.messaging_factory import MessagingFactory

    apply(monkeypatch, MessagingFactory, "create_consumer", [(
        "    if broker_type == MessageBrokerType.REDIS:\n        _reject_namespaced_redis_streams()",
        "    if config is not None:\n"
        "        config = (dataclasses.replace(config, topics=[*config.topics, 'extra']) if dataclasses.is_dataclass(config)\n"
        "                  else config.model_copy(update={'topics': [*config.topics, 'extra']}))\n"
        "    if broker_type == MessageBrokerType.REDIS:\n        _reject_namespaced_redis_streams()",
    )])
    with pytest.raises(AssertionError):
        test_the_factory_gives_a_default_record_consumer_its_own_topics_and_extractor("kafka")


async def test_m2_a_room_that_is_zero_for_one_key_with_no_rule_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.scheduling.drr_scheduler import DRRScheduler

    apply(monkeypatch, DRRScheduler, "enqueue", [(
        "    if self._room is not None:\n        left = self._room(key)\n",
        "    if True:\n        left = self._room(key) if self._room is not None else ('b', 0 if key[1:2] == ('org-0-conn-0',) else 1)\n",
    )])
    assert await detected("kafka")


async def test_m3_a_default_planner_replaced_by_a_stub_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.kafka.consumer import indexing_consumer as module

    monkeypatch.setattr(module, "StubPlanner", StubPlanner, raising=False)
    apply(monkeypatch, kafka_class(), "__init__", [("else SlotShare)(", "else StubPlanner)(")])
    assert await detected("kafka")


async def test_m4_claim_steps_that_reorder_the_default_topics_are_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.postgres.consumer import PgMessagingConsumer

    apply(monkeypatch, PgMessagingConsumer, "_consume_loop", [(
        "self.config.topics,\n                self.consumer_name,", "self.config.topics[::-1],\n                self.consumer_name,",
    )])
    assert await detected("pg")


async def test_m5_a_key_depth_changed_by_one_level_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    apply(monkeypatch, kafka_class(), "__init__", [(
        "self.fair_scheduler_config.entity_depth, self.weight_provider\n", "self.fair_scheduler_config.entity_depth - 1, self.weight_provider\n",
    )])
    assert await detected("kafka")


async def test_m6_a_fetch_back_room_clamp_with_no_rule_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    apply(monkeypatch, kafka_class(), "_IndexingKafkaConsumer__fetch_back_remembered", [(
        "if buffer_room else None", "if buffer_room else ('bucket', 2)",
    )])
    assert await detected("kafka")


async def test_m7_an_observer_wired_with_no_bundle_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    apply(monkeypatch, kafka_class(), "__init__", [(
        "if fairness is not None and fairness.observer is not None:\n        self.admission_backlog.observer = fairness.observer",
        "if True:\n        self.admission_backlog.observer = object()",
    )])
    assert await detected("kafka")


@pytest.mark.parametrize("kind", ["kafka", "redis"])
def test_the_factory_leaves_operator_key_fields_and_the_off_switch_alone_with_no_bundle(kind: str) -> None:
    from app.services.messaging.scheduling.interface import FairSchedulerConfig

    narrowed = make_with(kind, FairSchedulerConfig(enabled=True, key_fields=("orgId",)))
    assert narrowed.fair_scheduler_config.key_fields == ("orgId",)
    assert narrowed.key_extractor.inner.extract(world_message()) == ("o",)
    off = make_with(kind, FairSchedulerConfig(enabled=False))
    assert off._scheduler is None
    assert list((off.kafka_config if kind == "kafka" else off.config).topics) == ["record-events"]


def make_with(kind: str, fair: object) -> object:
    from app.services.messaging.config import (
        ConsumerType,
        MessageBrokerType,
        RedisStreamsConfig,
    )
    from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
    from app.services.messaging.messaging_factory import MessagingFactory

    if kind == "kafka":
        config = KafkaConsumerConfig(
            topics=["record-events"], client_id="c", group_id="g", auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["k:1"],
        )
    else:
        config = RedisStreamsConfig(host="h", port=1, client_id="c", group_id="g", topics=["record-events"])
    broker = MessageBrokerType.KAFKA if kind == "kafka" else MessageBrokerType.REDIS
    return MessagingFactory.create_consumer(
        logging.getLogger("factory-operator"), config, broker_type=broker, consumer_type=ConsumerType.INDEXING,
        fair_scheduler_config=fair,
    )


async def test_q1_a_factory_that_resets_operator_key_fields_with_no_bundle_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.messaging_factory import MessagingFactory

    apply(monkeypatch, MessagingFactory, "create_consumer", [(
        "    effective_fair_config = fair_scheduler_config or _fair_scheduler_config_from_env()\n",
        "    effective_fair_config = fair_scheduler_config or _fair_scheduler_config_from_env()\n"
        "    effective_fair_config = dataclasses.replace(effective_fair_config, key_fields=('orgId', 'connectorId'))\n",
    )])
    with pytest.raises(AssertionError):
        test_the_factory_leaves_operator_key_fields_and_the_off_switch_alone_with_no_bundle("kafka")


async def test_q3_a_queue_claim_that_reverses_each_batch_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.postgres.queue import PgQueue

    apply(monkeypatch, PgQueue, "claim", [("key=lambda d: d.message_id", "key=lambda d: -d.message_id")])
    assert await detected("pg")
