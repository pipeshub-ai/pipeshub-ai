"""The coordinator's broker publisher, bound to the retry producer once the consumers start."""

import asyncio
from typing import Any

from app.services.messaging.consumer_concurrency import run_on_loop
from app.services.messaging.interface.producer import IMessagingProducer


class PublisherNotBoundError(RuntimeError):
    pass


class DeferredPublisher:
    """``EventPublisher`` created with the pipeline runtime and bound at consumer startup.

    The producer's connections belong to the loop it was started on, while stage workers
    and recovery run on their own loops, so every publish is marshalled onto that loop.
    """

    def __init__(self) -> None:
        super().__init__()
        self._producer: IMessagingProducer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, producer: IMessagingProducer, loop: asyncio.AbstractEventLoop) -> None:
        self._producer = producer
        self._loop = loop

    @property
    def bound(self) -> bool:
        return self._producer is not None

    async def send_event(self, topic: str, event_type: str, payload: dict[str, Any], key: str | None = None) -> bool:
        if self._producer is None:
            raise PublisherNotBoundError("the stage publisher is not bound to a producer yet")
        return bool(await run_on_loop(self._loop, self._producer.send_event(topic, event_type, payload, key)))
