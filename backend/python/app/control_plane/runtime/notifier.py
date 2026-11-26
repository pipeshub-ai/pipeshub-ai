"""``ChangeNotifier`` over the deployment's message broker (03-interfaces §5.4): Kafka or Redis Streams through
``MessagingFactory``, one code path.

Topic ``control-plane-events``, key ``orgId`` (one org's events stay in order), value the broker envelope every
PipesHub topic uses, ``{eventType: "cp.changed", payload: <ChangeEvent>}``. Each process consumes in a group of its
own that starts at the topic's tail, so every process sees every event and a late one gets no replay: it recovers by
the revision gap rule and the live layer's time to live. In PipesHub only Node publishes (its outbox dispatcher);
``publish`` serves the port's contract and a Loom host.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final, cast

from pydantic import ValidationError

from app.control_plane.contracts.generated.change_event import ChangeEvent
from app.services.messaging.config import Topic
from harness_core.contracts import dump_contract
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from app.control_plane.runtime.types import Unsubscribe
    from app.services.messaging.config import StreamMessage
    from app.services.messaging.interface.consumer import IMessagingConsumer
    from app.services.messaging.interface.producer import IMessagingProducer

CHANGE_TOPIC: Final = Topic.CONTROL_PLANE_EVENTS.value
CHANGE_EVENT_TYPE: Final = "cp.changed"


class BrokerChangeNotifier:
    """``consumer`` must be this process's own group on ``CHANGE_TOPIC``
    (``MessagingUtils.create_control_plane_events_consumer_config``); ``producer`` only where the process
    publishes."""

    def __init__(
        self,
        consumer: IMessagingConsumer,
        producer: IMessagingProducer | None = None,
        *,
        log: HarnessLog | None = None,
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._log = log if log is not None else HarnessLog.named("app.control_plane.notifier")
        self._handlers: list[Callable[[ChangeEvent], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Joins the topic; events published before this are never delivered."""
        self._loop = asyncio.get_running_loop()
        await self._consumer.start(self._deliver)

    async def stop(self) -> None:
        await self._consumer.stop()

    async def publish(self, event: ChangeEvent) -> None:
        if self._producer is None:
            raise RuntimeError("this process does not publish control-plane changes")
        envelope: dict[str, JsonValue] = {
            "eventType": CHANGE_EVENT_TYPE,
            "payload": cast("dict[str, JsonValue]", dump_contract(event)),
        }
        _ = await self._producer.send_message(CHANGE_TOPIC, envelope, key=event.org_id)

    def subscribe(self, handler: Callable[[ChangeEvent], None]) -> Unsubscribe:
        def own(event: ChangeEvent) -> None:
            handler(event)

        self._handlers.append(own)

        def unsubscribe() -> None:
            if own in self._handlers:
                self._handlers.remove(own)

        return unsubscribe

    async def _deliver(self, message: StreamMessage) -> bool:
        """Always settles the message: a change event is a hint, and redelivering one that could not be read or
        handled helps nobody (the holder's reload does)."""
        if message.eventType != CHANGE_EVENT_TYPE:
            self._log.warning("cp.change_event_skipped", reasonCode="change_event_type_unknown")
            return True
        try:
            event = ChangeEvent.model_validate(message.payload)
        except ValidationError as invalid:
            self._log.warning(
                "cp.change_event_skipped", reasonCode="change_event_invalid", invalidMembers=invalid.error_count()
            )
            return True
        loop = self._loop
        if loop is not None and loop is not asyncio.get_running_loop():
            # A consumer that polls on a loop of its own: handlers touch state owned by the loop that subscribed.
            _ = loop.call_soon_threadsafe(self._dispatch, event)
        else:
            self._dispatch(event)
        return True

    def _dispatch(self, event: ChangeEvent) -> None:
        for handler in list(self._handlers):
            try:
                handler(event.model_copy(deep=True))
            except Exception as exc:  # one subscriber must not cost the others the event
                self._log.warning(
                    "cp.change_handler_failed",
                    reasonCode="change_handler_failed",
                    orgId=event.org_id,
                    revision=event.revision,
                    errorType=type(exc).__name__,
                )


__all__ = ["CHANGE_EVENT_TYPE", "CHANGE_TOPIC", "BrokerChangeNotifier"]
