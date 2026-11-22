"""Producer for ``MESSAGE_BROKER=postgres``: signs the envelope like the Redis one and appends to ``platform.queue_messages``."""

import json
from logging import Logger
from typing import override

from pydantic import JsonValue

from app.core.envelope import sign_message
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.postgres.queue import PgQueue
from app.utils.request_context import inject_envelope
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class PgMessagingProducer(IMessagingProducer):
    def __init__(
        self,
        logger: Logger,
        config: RedisStreamsConfig | None = None,
        queue: PgQueue | None = None,
    ) -> None:
        self.logger = logger
        self.config = config
        self.queue = queue or PgQueue()

    async def has_dead_delivery(self, topic: str, key: str) -> bool:
        """A delivery of this key was dead-lettered: stale recovery must not hand it out again."""
        return await self.queue.has_dead(topic, key)

    @override
    async def initialize(self) -> None:
        return None

    @override
    async def cleanup(self) -> None:
        return None

    @override
    async def start(self) -> None:
        return None

    @override
    async def stop(self) -> None:
        return None

    @override
    async def send_message(
        self, topic: str, message: dict[str, JsonValue], key: str | None = None
    ) -> bool:
        # Stamped here, in the caller's context, as the Redis producer does.
        signed = sign_message(topic, inject_envelope(message))
        await self.queue.publish(topic, [(key, json.dumps(signed))])
        self.logger.debug("Message published to queue topic %s", topic)
        return True

    @override
    async def send_messages(
        self, topic: str, messages: list[tuple[str | None, dict[str, JsonValue]]]
    ) -> list[bool]:
        if not messages:
            return []
        try:
            stamped = [
                (key, json.dumps(sign_message(topic, inject_envelope(dict(m)))))
                for key, m in messages
            ]
            await self.queue.publish(topic, stamped)  # one transaction: all or none
            return [True] * len(messages)
        except Exception as e:
            self.logger.error(
                "Failed to publish %d messages to queue topic %s: %s",
                len(messages),
                topic,
                e,
            )
            return [False] * len(messages)

    @override
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, JsonValue],
        key: str | None = None,
    ) -> bool:
        message: dict[str, JsonValue] = {
            "eventType": event_type,
            "payload": payload,
            "timestamp": get_epoch_timestamp_in_ms(),
        }
        await self.send_message(topic=topic, message=message, key=key)
        self.logger.info(
            "Successfully sent event with type: %s to topic: %s", event_type, topic
        )
        return True
