from __future__ import annotations

import logging

import pytest
from pydantic import JsonValue

from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.record_ordering_producer import (
    RECORD_ORDER_BIT_FIELD,
    RecordOrderingProducer,
)
from app.services.messaging.record_ordering_store import RecordOrderingStore


class _FakeOrderingStore(RecordOrderingStore):
    def __init__(self, bits: dict[str, bool] | None = None) -> None:
        self.bits = bits or {}
        self.lookups: list[str] = []
        self.initialized = False
        self.cleaned_up = False

    async def initialize(self) -> None:
        self.initialized = True

    async def cleanup(self) -> None:
        self.cleaned_up = True

    async def get_or_initialize(self, record_id: str) -> bool:
        self.lookups.append(record_id)
        return self.bits.setdefault(record_id, False)


class _FakeProducer(IMessagingProducer):
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, JsonValue], str | None]] = []
        self.events: list[
            tuple[str, str, dict[str, JsonValue], str | None]
        ] = []
        self.batches: list[
            tuple[str, list[tuple[str | None, dict[str, JsonValue]]]]
        ] = []

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(
        self, topic: str, message: dict[str, JsonValue], key: str | None = None
    ) -> bool:
        self.messages.append((topic, message, key))
        return True

    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, JsonValue],
        key: str | None = None,
    ) -> bool:
        self.events.append((topic, event_type, payload, key))
        return True

    async def send_messages(
        self, topic: str, messages: list[tuple[str | None, dict[str, JsonValue]]]
    ) -> list[bool]:
        self.batches.append((topic, messages))
        return [True] * len(messages)


@pytest.fixture
def producer_parts() -> tuple[_FakeProducer, _FakeOrderingStore, RecordOrderingProducer]:
    inner = _FakeProducer()
    store = _FakeOrderingStore({"record-1": True})
    producer = RecordOrderingProducer(logging.getLogger("test"), inner, store)
    return inner, store, producer


class TestSendEvent:
    async def test_adds_the_current_bit_to_new_record_event(
        self, producer_parts: tuple[_FakeProducer, _FakeOrderingStore, RecordOrderingProducer]
    ) -> None:
        inner, store, producer = producer_parts

        await producer.send_event(
            "record-events",
            "newRecord",
            {"recordId": "record-1"},
        )

        assert inner.events[0][2][RECORD_ORDER_BIT_FIELD] is True
        assert store.lookups == ["record-1"]

    async def test_uses_connector_id_as_record_event_key(
        self,
        producer_parts: tuple[_FakeProducer, _FakeOrderingStore, RecordOrderingProducer],
    ) -> None:
        inner, _store, producer = producer_parts

        await producer.send_event(
            "record-events",
            "newRecord",
            {"recordId": "record-1", "connectorId": "connector-1"},
            key="record-1",
        )

        assert inner.events[0][3] == "connector-1"

    async def test_uses_record_id_when_connector_id_is_missing(
        self,
        producer_parts: tuple[_FakeProducer, _FakeOrderingStore, RecordOrderingProducer],
    ) -> None:
        inner, _store, producer = producer_parts

        await producer.send_event(
            "record-events",
            "newRecord",
            {"recordId": "record-1"},
            key="batch-1",
        )

        assert inner.events[0][3] == "record-1"

    async def test_preserves_an_existing_event_bit(
        self, producer_parts: tuple[_FakeProducer, _FakeOrderingStore, RecordOrderingProducer]
    ) -> None:
        inner, store, producer = producer_parts

        await producer.send_event(
            "record-events",
            "newRecord",
            {"recordId": "record-1", RECORD_ORDER_BIT_FIELD: False},
        )

        assert inner.events[0][2][RECORD_ORDER_BIT_FIELD] is False
        assert store.lookups == []

    async def test_rejects_non_boolean_existing_bit(
        self, producer_parts: tuple[_FakeProducer, _FakeOrderingStore, RecordOrderingProducer]
    ) -> None:
        _inner, _store, producer = producer_parts

        with pytest.raises(ValueError, match="must be a boolean"):
            await producer.send_event(
                "record-events",
                "newRecord",
                {"recordId": "record-1", RECORD_ORDER_BIT_FIELD: 1},
            )

    async def test_does_not_change_non_record_or_recordless_events(
        self, producer_parts: tuple[_FakeProducer, _FakeOrderingStore, RecordOrderingProducer]
    ) -> None:
        inner, store, producer = producer_parts
        payload = {"orgId": "org-1"}

        await producer.send_event("entity-events", "userAdded", payload)
        await producer.send_event("record-events", "bulkDeleteRecords", payload)

        assert inner.events[0][2] == payload
        assert inner.events[1][2] == payload
        assert store.lookups == []


class TestSendMessage:
    async def test_adds_bit_to_enveloped_record_message(self, producer_parts) -> None:
        inner, _store, producer = producer_parts

        await producer.send_message(
            "record-events",
            {"eventType": "updateRecord", "payload": {"recordId": "record-1"}},
            key="record-1",
        )

        assert inner.messages[0][1]["payload"][RECORD_ORDER_BIT_FIELD] is True
        assert inner.messages[0][2] == "record-1"

    async def test_adds_bit_to_flat_record_message(self, producer_parts) -> None:
        inner, _store, producer = producer_parts

        await producer.send_message(
            "record-events",
            {"eventType": "newRecord", "recordId": "record-1"},
        )

        assert inner.messages[0][1][RECORD_ORDER_BIT_FIELD] is True


class TestSendMessages:
    async def test_adds_bits_to_batched_record_messages_in_input_order(
        self, producer_parts
    ) -> None:
        inner, store, producer = producer_parts

        await producer.send_messages(
            "record-events",
            [
                ("key-1", {"eventType": "newRecord", "payload": {"recordId": "record-1"}}),
                ("key-2", {"eventType": "newRecord", "payload": {"recordId": "record-2"}}),
            ],
        )

        topic, messages = inner.batches[0]
        assert topic == "record-events"
        assert [key for key, _ in messages] == ["record-1", "record-2"]
        assert [message["payload"][RECORD_ORDER_BIT_FIELD] for _, message in messages] == [
            True,
            False,
        ]
        assert store.lookups == ["record-1", "record-2"]

    async def test_batch_does_not_modify_non_record_messages(self, producer_parts) -> None:
        inner, store, producer = producer_parts
        message = {"eventType": "userAdded", "payload": {"orgId": "org-1"}}

        await producer.send_messages("entity-events", [(None, message)])

        assert inner.batches == [("entity-events", [(None, message)])]
        assert store.lookups == []


class TestLifecycle:
    async def test_initializes_and_cleans_up_store_with_producer(self, producer_parts) -> None:
        _inner, store, producer = producer_parts

        await producer.initialize()
        await producer.cleanup()

        assert store.initialized
        assert store.cleaned_up
