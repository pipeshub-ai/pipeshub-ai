"""The Kafka consumer binds the org for the handler only and dead-letters bad envelopes."""

import json
from collections.abc import Iterator
from unittest.mock import MagicMock, Mock

import pytest

from app.core import envelope, org_context
from app.exceptions.non_retryable import NonRetryableError
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer

KEY = envelope.derive_key("kafka-consumer-test")


@pytest.fixture(autouse=True)
def _keys() -> Iterator[None]:
    async def keys() -> tuple[bytes, ...]:
        return (KEY,)

    envelope.configure(keys)
    yield
    envelope.configure(None)


def _consumer(seen) -> KafkaMessagingConsumer:
    config = KafkaConsumerConfig(
        topics=["t"], client_id="c", group_id="g", auto_offset_reset="earliest",
        enable_auto_commit=False, bootstrap_servers=["localhost:9092"],
    )
    consumer = KafkaMessagingConsumer(Mock(), config)

    async def handler(msg) -> bool:
        seen.append(org_context.current().org_id)
        return True

    consumer.message_handler = handler
    return consumer


def _message(body) -> MagicMock:
    m = MagicMock()
    m.value, m.topic, m.partition, m.offset = json.dumps(body).encode(), "t", 0, 1
    return m


async def test_signed_and_legacy_messages_bind_their_org() -> None:
    seen = []
    consumer = _consumer(seen)
    ctx = org_context.OrgContext("org_a", "u", "user", "jwt", "r")
    signed = envelope.sign(KEY, "t", {"k": 1}, ctx, event_type="T")
    assert await consumer._KafkaMessagingConsumer__process_message(_message(signed)) == (True, None)
    legacy = {"eventType": "T", "payload": {"orgId": "org_z"}}
    assert await consumer._KafkaMessagingConsumer__process_message(_message(legacy)) == (True, None)
    assert seen == ["org_a", "org_z"]
    with pytest.raises(org_context.MissingOrgContext):
        org_context.current()


async def test_org_mismatch_is_terminal_and_never_reaches_the_handler() -> None:
    seen = []
    ctx = org_context.OrgContext("org_a", "u", "user", "jwt", "r")
    bad = {"eventType": "T", **envelope.sign(KEY, "t", {"orgId": "org_b"}, ctx)}
    ok, exc = await _consumer(seen)._KafkaMessagingConsumer__process_message(_message(bad))
    assert ok is False and isinstance(exc, NonRetryableError) and seen == []
    assert MessageErrorClassifier.classify_by_exception(exc) == MessageErrorType.TERMINAL
