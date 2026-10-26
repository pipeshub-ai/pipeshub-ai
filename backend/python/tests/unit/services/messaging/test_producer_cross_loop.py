"""Producers started on the main loop and sent to from the indexing worker loop.

The record handler runs on the consumer's worker loop, while the producer it
was given is started on the main loop. On Redis Streams that made a send from
the handler fail with "Future attached to a different loop" (main integration
run 37229085638: a stored-documents reschedule failed and its message was
re-queued). Each test starts the producer on the test's loop and sends from a
second loop running in its own thread.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.utils.request_context import ENVELOPE_REQUEST_ID, reset_context, set_context
from tests.support.loop_topology import on_loop, redis_tcp_server, worker_loop

TOPIC = "record-events"


def _event_types(entries: list[tuple[str, dict[str, str]]]) -> list[str]:
    return [json.loads(fields["value"])["eventType"] for _id, fields in entries]


class TestRedisStreamsProducerAcrossLoops:
    async def test_a_send_from_the_worker_loop_lands(self) -> None:
        with redis_tcp_server() as (host, port), worker_loop() as worker:
            producer = RedisStreamsProducer(
                logging.getLogger("test"), RedisStreamsConfig(host=host, port=port)
            )
            await producer.initialize()
            try:
                await on_loop(
                    worker,
                    producer.send_event(TOPIC, "deleteStoredDocuments", {"connectorId": "c-1"}),
                )
                # Back on the main loop, then the worker again: each side reuses
                # the connection the other one used last.
                await producer.send_event(TOPIC, "newRecord", {"recordId": "r-1"})
                results = await on_loop(
                    worker, producer.send_messages(TOPIC, [(None, {"eventType": "reindexRecord"})])
                )

                assert results == [True]
                assert _event_types(await producer.redis.xrange(TOPIC)) == [
                    "deleteStoredDocuments",
                    "newRecord",
                    "reindexRecord",
                ]
            finally:
                await producer.cleanup()

    async def test_the_request_id_survives_the_hop(self) -> None:
        """The send runs on the main loop, whose context has no request id;
        the envelope must still carry the id of the request that caused it."""
        with redis_tcp_server() as (host, port), worker_loop() as worker:
            producer = RedisStreamsProducer(
                logging.getLogger("test"), RedisStreamsConfig(host=host, port=port)
            )
            await producer.initialize()

            async def send_within_a_request() -> None:
                token = set_context("req-cross-loop-1")
                try:
                    await producer.send_event(TOPIC, "newRecord", {"recordId": "r-1"})
                finally:
                    reset_context(token)

            try:
                await on_loop(worker, send_within_a_request())
                [(_id, fields)] = await producer.redis.xrange(TOPIC)
                assert json.loads(fields["value"])[ENVELOPE_REQUEST_ID] == "req-cross-loop-1"
            finally:
                await producer.cleanup()


class _LoopBoundAIOKafkaProducer:
    """Binds to loops the way aiokafka does: to the running loop at
    construction, with each send acked by a future that the sender task on
    that loop resolves. Awaiting that ack from another loop raises the same
    "attached to a different loop" error a real producer does."""

    instances: list["_LoopBoundAIOKafkaProducer"] = []

    def __init__(self, **_config: object) -> None:
        self._loop = asyncio.get_running_loop()
        self.sent: list[tuple[str, bytes | None, bytes]] = []
        _LoopBoundAIOKafkaProducer.instances.append(self)

    async def start(self) -> None:
        assert self._loop is asyncio.get_running_loop()

    async def stop(self) -> None:
        return None

    async def send(
        self, topic: str, key: bytes | None = None, value: bytes = b""
    ) -> "asyncio.Future[SimpleNamespace]":
        ack = self._loop.create_future()
        self._loop.call_soon_threadsafe(self._deliver, ack, topic, key, value)
        return ack

    async def send_and_wait(
        self, topic: str, key: bytes | None = None, value: bytes = b""
    ) -> SimpleNamespace:
        return await (await self.send(topic, key=key, value=value))

    def _deliver(
        self, ack: "asyncio.Future[SimpleNamespace]", topic: str, key: bytes | None, value: bytes
    ) -> None:
        self.sent.append((topic, key, value))
        ack.set_result(SimpleNamespace(topic=topic, partition=0, offset=len(self.sent) - 1))


class TestKafkaProducerAcrossLoops:
    async def test_a_send_from_the_worker_loop_lands(self) -> None:
        _LoopBoundAIOKafkaProducer.instances.clear()
        with patch(
            "app.services.messaging.kafka.producer.producer.AIOKafkaProducer",
            _LoopBoundAIOKafkaProducer,
        ), worker_loop() as worker:
            producer = KafkaMessagingProducer(
                logging.getLogger("test"),
                KafkaProducerConfig(bootstrap_servers=["kafka:9092"], client_id="test"),
            )
            await producer.initialize()
            try:
                await on_loop(
                    worker,
                    producer.send_event(TOPIC, "deleteStoredDocuments", {"connectorId": "c-1"}),
                )
                results = await on_loop(
                    worker, producer.send_messages(TOPIC, [("r-2", {"eventType": "newRecord"})])
                )

                assert results == [True]
                [inner] = _LoopBoundAIOKafkaProducer.instances
                assert [json.loads(value)["eventType"] for _t, _k, value in inner.sent] == [
                    "deleteStoredDocuments",
                    "newRecord",
                ]
            finally:
                await producer.cleanup()
