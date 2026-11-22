"""Every producer of record events gives each message an event id (a UUID at publish), whether or not signing is on.

The event id is the identity of an event; the replay guard keys on it. Signing adds integrity, not identity.
"""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import OrgContext, bind
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.redis_streams.producer import RedisStreamsProducer

PAYLOAD = {"orgId": "org-1", "recordId": "r1"}


@pytest.fixture(autouse=True)
def _unsigned_default_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVELOPE_SIGN", raising=False)


def _uuid(value: object) -> str:
    assert isinstance(value, str)
    uuid.UUID(value)
    return value


async def test_the_kafka_producer_stamps_an_event_id() -> None:
    producer = KafkaMessagingProducer(
        logging.getLogger("t"), KafkaProducerConfig(bootstrap_servers=["b:9092"], client_id="c", ssl=False, sasl=None)
    )
    sent: list[dict] = []

    async def capture(topic: str, message: dict, key: str | None) -> bool:
        sent.append(message)
        return True

    producer._send_message = capture  # type: ignore[method-assign]
    producer._owner_loop = None
    await producer.send_message("record-events", {"eventType": "newRecord", "payload": dict(PAYLOAD)}, "r1")
    await producer.send_message("record-events", {"eventType": "newRecord", "payload": dict(PAYLOAD)}, "r1")

    assert _uuid(sent[0]["eventId"]) != _uuid(sent[1]["eventId"])


async def test_the_redis_streams_producer_stamps_an_event_id() -> None:
    producer = RedisStreamsProducer(
        logging.getLogger("t"),
        RedisStreamsConfig(host="h", port=1, client_id="c", group_id="g", topics=["record-events"]),
    )
    sent: list[dict] = []

    async def capture(topic: str, message: dict, key: str | None) -> bool:
        sent.append(message)
        return True

    producer._send_message = capture  # type: ignore[method-assign]
    producer._owner_loop = None
    await producer.send_message("record-events", {"eventType": "newRecord", "payload": dict(PAYLOAD)}, "r1")

    _uuid(sent[0]["eventId"])


async def test_the_postgres_producer_stamps_an_event_id() -> None:
    queue = MagicMock()
    queue.publish = AsyncMock()
    producer = PgMessagingProducer(logging.getLogger("t"), queue=queue)

    await producer.send_message("record-events", {"eventType": "newRecord", "payload": dict(PAYLOAD)}, "r1")
    await producer.send_messages("record-events", [("r2", {"eventType": "newRecord", "payload": dict(PAYLOAD)})])

    single = json.loads(queue.publish.await_args_list[0].args[1][0][1])
    batch = json.loads(queue.publish.await_args_list[1].args[1][0][1])
    assert _uuid(single["eventId"]) != _uuid(batch["eventId"])


async def test_signing_keeps_the_event_id_in_the_signed_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import envelope

    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    key = envelope.derive_key("stamp-test")
    envelope.configure(None, None, key)
    try:
        with bind(OrgContext("org-1", None, "service", "job:test", "t")):
            signed = envelope.sign_message("record-events", {"eventType": "newRecord", "payload": dict(PAYLOAD)})
    finally:
        envelope.configure(None)

    _uuid(signed["ctx"]["eventId"])
