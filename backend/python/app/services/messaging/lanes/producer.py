"""Producer decorator that places each message into its lane.

Wrapping the producer once, in ``MessagingFactory.create_producer``, routes
every existing publish site -- the connector data processor, the KB service,
the connector-instance delete path, vector-store rebuild, startup recovery,
and the indexing consumer's own reindex re-publish -- without editing any of
them. That matters beyond convenience: two of those sites publish with no key
at all today, and a lane scheme that depended on each caller remembering to
pass one would silently leak those messages into the wrong lane.
"""
from __future__ import annotations

from logging import Logger
from typing import TYPE_CHECKING, override

from pydantic import JsonValue

from app.services.messaging.interface.producer import IMessagingProducer

if TYPE_CHECKING:
    from app.services.messaging.lanes.interface import LaneConfig, LaneRouter

__all__ = ["LaneAwareProducer"]


class LaneAwareProducer(IMessagingProducer):
    """Routes laned topics through a :class:`LaneRouter`, passes everything
    else through untouched.

    Delegation is to the *inner* producer's methods, never back through this
    one, so a message is routed exactly once even though the inner
    ``send_event`` is itself implemented in terms of its own ``send_message``.
    """

    def __init__(
        self,
        logger: Logger,
        inner: IMessagingProducer,
        router: "LaneRouter",
        config: "LaneConfig",
    ) -> None:
        self.logger = logger
        self._inner = inner
        self._router = router
        self._config = config
        self._laned = frozenset(config.laned_topics)
        self._warned_missing_key = False

    @property
    def inner(self) -> IMessagingProducer:
        return self._inner

    def _lane_key(self, message: dict[str, JsonValue]) -> str | None:
        """Read the fairness key from an envelope.

        Accepts both shapes in use: the standard ``{eventType, payload, ...}``
        envelope, and the flat dict that a couple of call sites hand straight
        to ``send_message``.
        """
        field = self._config.lane_key_field
        payload = message.get("payload")
        value = None
        if isinstance(payload, dict):
            value = payload.get(field)
        if value is None:
            value = message.get(field)
        if value is None or value == "":
            self._warn_missing_key(message)
            return None
        return str(value)

    def _warn_missing_key(self, message: dict[str, JsonValue]) -> None:
        # Once per producer: these all share one lane, which is correct but
        # worth knowing about, and a per-message log on a bulk sync is noise.
        if self._warned_missing_key:
            return
        self._warned_missing_key = True
        self.logger.warning(
            "Message has no '%s'; routing it and any like it to the shared "
            "default lane (eventType=%s)",
            self._config.lane_key_field,
            message.get("eventType"),
        )

    def _route(
        self, topic: str, message: dict[str, JsonValue], key: str | None
    ) -> tuple[str, str | None]:
        if topic not in self._laned:
            return topic, key
        return self._router.route(topic, self._lane_key(message))

    @override
    async def initialize(self) -> None:
        await self._inner.initialize()

    @override
    async def cleanup(self) -> None:
        await self._inner.cleanup()

    @override
    async def start(self) -> None:
        await self._inner.start()

    @override
    async def stop(self) -> None:
        await self._inner.stop()

    @override
    async def send_message(
        self,
        topic: str,
        message: dict[str, JsonValue],
        key: str | None = None,
    ) -> bool:
        routed_topic, routed_key = self._route(topic, message, key)
        return await self._inner.send_message(routed_topic, message, key=routed_key)

    @override
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, JsonValue],
        key: str | None = None,
    ) -> bool:
        envelope: dict[str, JsonValue] = {"eventType": event_type, "payload": payload}
        routed_topic, routed_key = self._route(topic, envelope, key)
        return await self._inner.send_event(
            topic=routed_topic,
            event_type=event_type,
            payload=payload,
            key=routed_key,
        )

    @override
    async def send_messages(
        self,
        topic: str,
        messages: list[tuple[str | None, dict[str, JsonValue]]],
    ) -> list[bool]:
        """Group by destination lane before delegating.

        One delegated call per lane rather than per message, so the Kafka
        producer's accumulator still coalesces a connector sync's batch into
        real broker batches -- sending them one at a time would undo the
        batching ``KafkaProducer.send_messages`` exists for. Results are
        reassembled into the caller's original order, which callers rely on to
        know which records were accepted.
        """
        if topic not in self._laned:
            return await self._inner.send_messages(topic, messages)

        grouped: dict[str, list[tuple[int, str | None, dict[str, JsonValue]]]] = {}
        for index, (key, message) in enumerate(messages):
            routed_topic, routed_key = self._route(topic, message, key)
            grouped.setdefault(routed_topic, []).append((index, routed_key, message))

        results: list[bool] = [False] * len(messages)
        for routed_topic, entries in grouped.items():
            lane_results = await self._inner.send_messages(
                routed_topic, [(key, message) for _i, key, message in entries]
            )
            for (index, _key, _message), ok in zip(entries, lane_results, strict=False):
                results[index] = ok
        return results
