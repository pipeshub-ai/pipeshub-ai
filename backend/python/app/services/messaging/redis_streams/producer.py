import asyncio
import json
from logging import Logger
from typing import override

from pydantic import JsonValue
from redis.asyncio import Redis

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.interface.producer import IMessagingProducer
from app.utils.request_context import inject_envelope
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RedisStreamsProducer(IMessagingProducer):
    """Redis Streams implementation of messaging producer"""

    def __init__(self, logger: Logger, config: RedisStreamsConfig) -> None:
        self.logger = logger
        self.config = config
        self.redis: Redis | None = None
        self._lock = asyncio.Lock()

    @override
    async def initialize(self) -> None:
        if self.redis is not None:
            return

        async with self._lock:
            if self.redis is not None:
                return

            try:
                self.redis = Redis(
                    host=self.config.host,
                    port=self.config.port,
                    password=self.config.password,
                    db=self.config.db,
                    decode_responses=True,
                )
                await self.redis.ping()
                self.logger.info(
                    "Redis Streams producer initialized at %s:%s",
                    self.config.host,
                    self.config.port,
                )
            except Exception as e:
                self.redis = None
                self.logger.error("Failed to initialize Redis Streams producer: %s", e)
                raise

    @override
    async def cleanup(self) -> None:
        async with self._lock:
            if self.redis:
                try:
                    await self.redis.aclose()
                    self.redis = None
                    self.logger.info("Redis Streams producer stopped successfully")
                except Exception as e:
                    self.logger.error("Error stopping Redis Streams producer: %s", e)

    @override
    async def start(self) -> None:
        if self.redis is None:
            await self.initialize()

    @override
    async def stop(self) -> None:
        await self.cleanup()

    @override
    async def send_message(
        self,
        topic: str,
        message: dict[str, JsonValue],
        key: str | None = None,
    ) -> bool:
        try:
            if self.redis is None:
                await self.initialize()

            message = inject_envelope(message)
            fields: dict[str, str] = {
                "value": json.dumps(message),
            }
            if key:
                fields["key"] = key

            await self.redis.xadd(  # type: ignore
                topic,
                fields,
                maxlen=self.config.max_len,
                approximate=True,
            )

            self.logger.debug("Message successfully published to Redis stream %s", topic)
            return True

        except Exception as e:
            self.logger.error("Failed to send message to Redis stream: %s", e)
            raise

    @override
    async def send_messages(
        self,
        topic: str,
        messages: list[tuple[str | None, dict[str, JsonValue]]],
    ) -> list[bool]:
        """Pipeline the XADDs instead of awaiting one round trip per message.

        A connector sync flushes batches of 50-100; the base implementation
        sends them one at a time, paying a full Redis round trip each. The
        Kafka producer already overrides this for the same reason.
        """
        if not messages:
            return []
        try:
            if self.redis is None:
                await self.initialize()

            pipeline = self.redis.pipeline(transaction=False)  # type: ignore
            for key, message in messages:
                fields: dict[str, str] = {
                    "value": json.dumps(inject_envelope(dict(message)))
                }
                if key:
                    fields["key"] = key
                pipeline.xadd(
                    topic,
                    fields,
                    maxlen=self.config.max_len,
                    approximate=True,
                )
            # Per-message results, not one all-or-nothing raise: callers use
            # them to record exactly which records were accepted.
            outcomes = await pipeline.execute(raise_on_error=False)
        except Exception as e:
            # The batch outcome is genuinely unknown here: the connection can
            # drop after Redis has already applied some of the XADDs. Report
            # all of them as unsent anyway, because the two errors are not
            # symmetric. A record reported unsent that did land is republished
            # by stale-record recovery and de-duplicated downstream by the
            # `record:<id>` lease and the COMPLETED short-circuit -- the
            # pipeline is at-least-once by design. A record reported sent that
            # did *not* land is marked QUEUED with no event behind it and is
            # never indexed. Prefer the recoverable error.
            self.logger.error(
                "Failed to publish %d message(s) to Redis stream %s; treating "
                "the whole batch as unsent: %s",
                len(messages),
                topic,
                e,
            )
            return [False] * len(messages)

        results = [not isinstance(outcome, Exception) for outcome in outcomes]
        failed = results.count(False)
        if failed:
            first_error = next(
                (o for o in outcomes if isinstance(o, Exception)), None
            )
            self.logger.error(
                "%d/%d messages failed to publish to %s; first error: %s",
                failed,
                len(messages),
                topic,
                first_error,
            )
        return results

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
            "Successfully sent event with type: %s to topic: %s",
            event_type,
            topic,
        )
        return True
