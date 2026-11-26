"""Every Kafka client the indexing consumer builds long-polls 50 ms, not aiokafka's 500 ms default.

aiokafka keeps one fetch per broker in flight, and a partition paused for its record is left out of it. A partition
resumed beside an idle one waits for that idle fetch, which the broker holds for fetch_max_wait_ms. At 500 ms a busy
partition next to an idle one ran at 2 records/s (FIFO). 0 or 1 ms would turn every idle consumer into a busy fetch
loop, so the value is pinned exactly.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka import AIOKafkaConsumer

from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import indexing_consumer as kafka_module
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig

_LOGGER = logging.getLogger("test_kafka_fetch_long_poll")
_EXPECTED_MS = 50


def _config(**overrides: object) -> KafkaConsumerConfig:
    fields: dict = {
        "topics": ["idx-topic"], "client_id": "idx", "group_id": "idx-group", "auto_offset_reset": "earliest",
        "enable_auto_commit": False, "bootstrap_servers": ["broker:9092"],
    }
    fields.update(overrides)
    return KafkaConsumerConfig(**fields)


def _consumer(fair: bool) -> IndexingKafkaConsumer:
    return IndexingKafkaConsumer(_LOGGER, _config(), fair_scheduler_config=FairSchedulerConfig(enabled=fair))


@pytest.mark.parametrize(
    "overrides",
    [{}, {"ssl": True}, {"ssl": True, "sasl": {"mechanism": "SCRAM-SHA-512", "username": "u", "password": "p"}}],
    ids=["plain", "ssl", "sasl"],
)
def test_config_long_polls_50_ms_beside_the_security_settings(overrides: dict) -> None:
    config = IndexingKafkaConsumer.kafka_config_to_dict(_config(**overrides))

    assert config["fetch_max_wait_ms"] == _EXPECTED_MS
    # `50.0 == 50` passes, and aiokafka then fails every fetch it sends.
    assert type(config["fetch_max_wait_ms"]) is int


@pytest.mark.asyncio
async def test_aiokafka_takes_the_setting() -> None:
    """A misspelt key would raise TypeError here; the consumer is never started, so no broker is needed."""
    config = IndexingKafkaConsumer.kafka_config_to_dict(_config())
    topics = config.pop("topics")

    consumer = AIOKafkaConsumer(*topics, **config)

    assert consumer._fetch_max_wait_ms == _EXPECTED_MS


@pytest.mark.asyncio
@pytest.mark.parametrize("fair", [True, False], ids=["fair", "fifo"])
async def test_initialize_builds_the_consumer_with_it_on_both_paths(fair: bool) -> None:
    consumer = _consumer(fair)
    consumer.worker_loop_ready.set()
    consumer.worker_loop = MagicMock(is_running=MagicMock(return_value=True))

    with (
        patch.object(consumer, "_IndexingKafkaConsumer__start_worker_thread"),
        patch.object(kafka_module, "AIOKafkaConsumer") as kafka_cls,
    ):
        kafka_cls.return_value.start = AsyncMock()
        await consumer.initialize()

    args, kwargs = kafka_cls.call_args
    assert kwargs["fetch_max_wait_ms"] == _EXPECTED_MS
    assert args == (() if fair else ("idx-topic",))


@pytest.mark.parametrize(
    "build",
    ["_IndexingKafkaConsumer__waiting_fetcher", "_IndexingKafkaConsumer__offset_fetcher"],
    ids=["record-fetch-back", "fetch-back"],
)
def test_fetch_back_readers_long_poll_50_ms(build: str) -> None:
    consumer = _consumer(fair=True)

    with patch.object(kafka_module, "OffsetFetcher") as fetcher_cls:
        getattr(consumer, build)()

    client_config = fetcher_cls.call_args.args[0]
    assert client_config["fetch_max_wait_ms"] == _EXPECTED_MS
    assert not {"topics", "group_id", "enable_auto_commit", "auto_offset_reset"} & client_config.keys()


@pytest.mark.asyncio
async def test_lane_backlog_probe_long_polls_50_ms() -> None:
    consumer = _consumer(fair=True)
    consumer.consumer = MagicMock(partitions_for_topic=MagicMock(return_value={0, 1}))

    with patch.object(kafka_module, "read_partition_backlog", AsyncMock(return_value={})) as read:
        await consumer._read_lane_backlog("idx-topic")

    client_config = read.call_args.args[0]
    assert client_config["fetch_max_wait_ms"] == _EXPECTED_MS
    assert not {"topics", "enable_auto_commit", "auto_offset_reset"} & client_config.keys()
