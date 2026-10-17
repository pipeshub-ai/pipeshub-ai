"""A turned-off connector's queued events are settled as they are read, on real brokers.

The events of one turned-off connector sit ahead of another connector's on the
same lane, as in the report this guards. They must be acknowledged (Redis) or
committed (Kafka) on the broker itself without ever reaching the handler, with
one status write per read pass.

Requires:
  docker compose -f deployment/docker-compose/docker-compose.integration.messaging.yml up -d
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.indexing.connector_off_events import GraphConnectorOffFilter
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.resource_governor.models import ParseTier
from tests.integration.messaging.conftest import (
    DRAIN_TIMEOUT_SECONDS,
    committed_offsets,
    create_kafka_topic,
    delete_kafka_topic,
)
from tests.support.fake_connector_graph import FakeConnectorGraph

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_OFF_BACKLOG = 2_000
_ON = 5


def _fair() -> FairSchedulerConfig:
    return FairSchedulerConfig(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        default_quantum=1,
        max_buffered_messages=200,
        max_per_entity_messages=50,
        max_dwell_seconds=900.0,
    )


def _envelope(record_id: str, connector_id: str) -> dict:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": "org-1",
            "connectorId": connector_id,
            "extension": "txt",
            "mimeType": "text/plain",
        },
        "timestamp": 1,
    }


def _backlog() -> tuple[FakeConnectorGraph, list[dict]]:
    graph = FakeConnectorGraph()
    graph.add_connector("conn-off", active=False)
    graph.add_connector("conn-on", active=True)
    envelopes = []
    for i in range(_OFF_BACKLOG):
        graph.add_record(f"off-{i}", "conn-off")
        envelopes.append(_envelope(f"off-{i}", "conn-off"))
    for i in range(_ON):
        graph.add_record(f"on-{i}", "conn-on")
        envelopes.append(_envelope(f"on-{i}", "conn-on"))
    return graph, envelopes


def _handler(seen: list[str]):  # noqa: ANN202
    async def handle(parsed_message):  # noqa: ANN202
        yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(tier=ParseTier.LIGHT))
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        seen.append(parsed_message.payload["recordId"])
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    return handle


async def _until(predicate) -> None:
    deadline = asyncio.get_running_loop().time() + DRAIN_TIMEOUT_SECONDS
    while True:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.1)


def _assert_settled(graph: FakeConnectorGraph, seen: list[str]) -> None:
    assert sorted(seen) == [f"on-{i}" for i in range(_ON)]
    off = {r["indexingStatus"] for k, r in graph.records.items() if k.startswith("off-")}
    assert off == {ProgressStatus.AUTO_INDEX_OFF.value}
    assert graph.calls["update_nodes_fields_if_match:records"] < _OFF_BACKLOG // 5


async def test_redis_settles_a_turned_off_connectors_backlog_on_the_broker(
    redis_available, unique_suffix
) -> None:
    from redis.asyncio import Redis

    host, port = redis_available
    stream = f"record-events-off-{unique_suffix}"
    group = f"it-off-{unique_suffix}"
    graph, envelopes = _backlog()
    client = Redis(host=host, port=port, decode_responses=True)
    consumer = None
    try:
        pipe = client.pipeline(transaction=False)
        for envelope in envelopes:
            pipe.xadd(stream, {"value": json.dumps(envelope)})
        await pipe.execute()

        consumer = IndexingRedisStreamsConsumer(
            logging.getLogger("it-consumer"),
            RedisStreamsConfig(
                host=host, port=port, client_id=f"{group}-client", group_id=group,
                topics=[stream], batch_size=10, block_ms=200,
            ),
            fair_scheduler_config=_fair(),
            connector_off_filter=GraphConnectorOffFilter(graph, logging.getLogger("it"), 15.0),
        )
        seen: list[str] = []
        await consumer.start(_handler(seen))

        async def drained() -> bool:
            info = await client.xinfo_groups(stream)
            mine = next(g for g in info if g["name"] == group)
            return int(mine["pending"]) == 0 and mine["last-delivered-id"] == (
                await client.xinfo_stream(stream)
            )["last-generated-id"]

        await _until(drained)
        await _until(lambda: len(seen) == _ON)
    finally:
        if consumer is not None:
            await consumer.stop()
        await client.delete(stream)
        await client.aclose()

    _assert_settled(graph, seen)


async def test_kafka_settles_a_turned_off_connectors_backlog_on_the_broker(
    kafka_available, unique_suffix
) -> None:
    from aiokafka import AIOKafkaProducer

    bootstrap = kafka_available
    topic = f"record-events-off-{unique_suffix}"
    group = f"it-off-{unique_suffix}"
    graph, envelopes = _backlog()
    await create_kafka_topic(bootstrap, topic, 1)
    consumer = None
    try:
        producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
        await producer.start()
        try:
            for envelope in envelopes:
                await producer.send(topic, json.dumps(envelope).encode())
            await producer.flush()
        finally:
            await producer.stop()

        consumer = IndexingKafkaConsumer(
            logging.getLogger("it-consumer"),
            KafkaConsumerConfig(
                topics=[topic], client_id=f"{group}-client", group_id=group,
                auto_offset_reset="earliest", enable_auto_commit=False,
                bootstrap_servers=[bootstrap],
            ),
            fair_scheduler_config=_fair(),
            connector_off_filter=GraphConnectorOffFilter(graph, logging.getLogger("it"), 15.0),
        )
        seen: list[str] = []
        await consumer.start(_handler(seen))
        await _until(lambda: len(seen) == _ON)

        async def committed_all() -> bool:
            return (await committed_offsets(bootstrap, group, topic)).get(0) == len(envelopes)

        await _until(committed_all)
    finally:
        if consumer is not None:
            await consumer.stop()
        await delete_kafka_topic(bootstrap, topic)

    _assert_settled(graph, seen)
