import asyncio
from typing import cast, override

from app.services.messaging import message_log
from app.services.messaging.config import messaging_env
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer

_STREAM_START = "0-0"

# What XREAD answers: per stream, the entries after the position asked for.
StreamRead = list[tuple[str, list[tuple[str, dict[str, str]]]]]


class RedisStreamsBroadcastConsumer(RedisStreamsConsumer):
    """A fan-out subscriber: every process that subscribes sees every message.

    A consumer group hands each message to one member and remembers what each
    has not acknowledged; a subscriber that must see everything wants neither.
    It reads with XREAD from a position it keeps in memory, so the server holds
    nothing for it and a process that dies leaves nothing behind (Redis never
    expires a group). It starts after the last entry of the stream: what was
    published while the process was not running is not replayed.
    """

    _positions: dict[str, str]

    @override
    async def _join(self) -> None:
        """Read once: ``$`` in every XREAD would skip what arrives between two reads."""
        self._positions = {}
        for topic in self.config.topics:
            last = cast(
                "list[tuple[str, dict[str, str]]]",
                await self.redis.xrevrange(topic, count=1),  # type: ignore[union-attr]
            )
            self._positions[topic] = last[0][0] if last else _STREAM_START

    @override
    async def _consume_loop(self) -> None:
        try:
            while self.running:
                try:
                    for topics in self._planner.group(list(self._positions)):
                        await self._deliver(await self._read(topics))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error("Error in broadcast loop: %s: %s", type(e).__name__, e)
                    await asyncio.sleep(1)
        finally:
            await self.cleanup()

    async def _read(self, topics: list[str]) -> StreamRead:
        read = await self.redis.xread(  # type: ignore[union-attr]
            streams={topic: self._positions[topic] for topic in topics},
            count=self.config.batch_size,
            block=self.config.block_ms,
        )
        return cast("StreamRead", read or [])

    async def _deliver(self, read: StreamRead) -> None:
        for topic, messages in read:
            for message_id, fields in messages:
                await self._handle(topic, message_id, fields)
                self._positions[topic] = message_id

    async def _handle(self, topic: str, message_id: str, fields: dict[str, str]) -> None:
        """Nothing redelivers a subscriber's copy, so a handler that fails for a passing reason is tried again
        here, up to the delivery limit the group consumers use."""
        attempts = messaging_env.max_delivery_attempts
        for attempt in range(1, attempts + 1):
            # Each try is one delivery to message_log: it binds the envelope's context and writes the one end line.
            with message_log.delivery(self.logger, topic=topic, message_id=message_id, attempt=attempt):
                success, is_terminal = await self._process_message_with_classification(topic, message_id, fields)
                if success or is_terminal:
                    message_log.end("ack" if success else "dead_letter")
                    return
                if attempt == attempts:
                    message_log.end("dead_letter", reason="attempts_exhausted")
                    return
                message_log.end("retry")
            await asyncio.sleep(self.config.block_ms / 1000)
