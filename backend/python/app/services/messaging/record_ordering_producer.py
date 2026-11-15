"""Producer decorator that adds ordering state to record events."""

from __future__ import annotations

import asyncio
from logging import Logger

from pydantic import JsonValue

from app.services.messaging.config import Topic
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.record_ordering_store import (
    RECORD_ORDER_BIT_FIELD,
    RecordOrderingStore,
)


class RecordOrderingProducer(IMessagingProducer):
    """Adds ordering bits and stable partition keys to record events."""

    def __init__(
        self,
        logger: Logger,
        inner: IMessagingProducer,
        ordering_store: RecordOrderingStore,
    ) -> None:
        self.logger = logger
        self._inner = inner
        self._ordering_store = ordering_store

    @staticmethod
    def _record_event_key(
        topic: str,
        payload: dict[str, JsonValue],
        key: str | None,
    ) -> str | None:
        if topic != Topic.RECORD_EVENTS.value:
            return key

        for field in ("connectorId", "recordId"):
            value = payload.get(field)
            if isinstance(value, str) and value:
                return value
        return key

    @property
    def inner(self) -> IMessagingProducer:
        return self._inner

    async def initialize(self) -> None:
        await self._ordering_store.initialize()
        await self._inner.initialize()

    async def cleanup(self) -> None:
        try:
            await self._inner.cleanup()
        finally:
            await self._ordering_store.cleanup()

    async def start(self) -> None:
        await self._ordering_store.initialize()
        await self._inner.start()

    async def stop(self) -> None:
        await self._inner.stop()

    async def _prepare_payload(
        self,
        topic: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if topic != Topic.RECORD_EVENTS.value:
            return payload

        record_id = payload.get("recordId")
        if not isinstance(record_id, str) or not record_id:
            return payload

        prepared = dict(payload)
        if RECORD_ORDER_BIT_FIELD in prepared:
            bit = prepared[RECORD_ORDER_BIT_FIELD]
            if type(bit) is not bool:
                raise ValueError(
                    f"{RECORD_ORDER_BIT_FIELD} must be a boolean, got {bit!r}"
                )
            return prepared

        prepared[RECORD_ORDER_BIT_FIELD] = (
            await self._ordering_store.get_or_initialize(record_id)
        )
        return prepared

    async def _prepare_message(
        self,
        topic: str,
        message: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if topic != Topic.RECORD_EVENTS.value:
            return message

        payload = message.get("payload")
        if isinstance(payload, dict):
            prepared = dict(message)
            prepared["payload"] = await self._prepare_payload(topic, payload)
            return prepared

        return await self._prepare_payload(topic, message)

    async def send_message(
        self,
        topic: str,
        message: dict[str, JsonValue],
        key: str | None = None,
    ) -> bool:
        prepared = await self._prepare_message(topic, message)
        payload = prepared.get("payload")
        key_payload = payload if isinstance(payload, dict) else prepared
        record_event_key = self._record_event_key(topic, key_payload, key)
        return await self._inner.send_message(
            topic, prepared, key=record_event_key
        )

    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, JsonValue],
        key: str | None = None,
    ) -> bool:
        prepared_payload = await self._prepare_payload(topic, payload)
        record_event_key = self._record_event_key(topic, prepared_payload, key)
        return await self._inner.send_event(
            topic,
            event_type,
            prepared_payload,
            key=record_event_key,
        )

    async def send_messages(
        self,
        topic: str,
        messages: list[tuple[str | None, dict[str, JsonValue]]],
    ) -> list[bool]:
        prepared_messages = await asyncio.gather(
            *(
                self._prepare_message(topic, message)
                for _, message in messages
            )
        )
        return await self._inner.send_messages(
            topic,
            [
                (
                    self._record_event_key(
                        topic,
                        message.get("payload")
                        if isinstance(message.get("payload"), dict)
                        else message,
                        key,
                    ),
                    message,
                )
                for (key, _), message in zip(
                    messages,
                    prepared_messages,
                    strict=True,
                )
            ],
        )
