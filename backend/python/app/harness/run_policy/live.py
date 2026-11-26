"""A worker process's live layer: Node's live route behind a cache, kept current by ``control-plane-events``.

Only a pinned run reads it, so a deployment that pins nothing (``CONTROL_PLANE_PIN`` not ``on``) never joins the
topic or calls the route: the subscriber starts with the first pinned run. A broker that cannot be joined leaves the
cache on its time-to-live poll (NFR-37) and the join is tried again later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Protocol

from app.control_plane.runtime.live_layer import ReloadingLiveLayerCache
from app.control_plane.runtime.notifier import BrokerChangeNotifier
from app.core.org_context import spawn_detached
from app.harness.journal_node.live_layer import NodeLiveLayerSource
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

    from app.control_plane.contracts.generated.change_event import ChangeEvent
    from app.control_plane.runtime.live_layer import LiveLayer
    from app.control_plane.runtime.ports import LiveLayerCache
    from app.harness.journal_node.live_layer import LiveCredentialSource
    from harness_core.clock import Clock

# How long a broker that could not be joined is left alone before the next pinned run tries again.
JOIN_RETRY_S: Final = 30.0


class ChangeSubscription(Protocol):
    """The process's own consumer of ``control-plane-events``."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class WorkerLiveLayer:
    """``LiveLayerCache`` that joins the change topic when the first pinned run reads it."""

    def __init__(
        self,
        cache: LiveLayerCache,
        subscribe: Callable[[Callable[[ChangeEvent], None]], Awaitable[ChangeSubscription]],
        clock: Clock,
        *,
        log: HarnessLog | None = None,
        retry_s: float = JOIN_RETRY_S,
        close_source: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._close_source = close_source
        self._cache = cache
        self._subscribe = subscribe
        self._clock = clock
        self._log = log if log is not None else HarnessLog.named("app.harness.run_policy.live")
        self._retry_s = retry_s
        self._subscription: ChangeSubscription | None = None
        self._joining: asyncio.Future[None] | None = None
        self._failed_at: float | None = None

    async def current(self, org_id: str) -> LiveLayer:
        self._join()
        return await self._cache.current(org_id)

    def on_change(self, event: ChangeEvent) -> None:
        self._cache.on_change(event)

    def _join(self) -> None:
        """Starts joining the topic, once, without making the run wait for it: joining a Kafka group takes seconds,
        and until it has joined the cache's own reloads are what the run reads."""
        if self._subscription is not None or self._joining is not None:
            return
        if self._failed_at is not None and self._clock.monotonic() - self._failed_at < self._retry_s:
            return
        self._joining = spawn_detached(self._start())

    async def _start(self) -> None:
        try:
            self._subscription = await self._subscribe(self._cache.on_change)
        except Exception as exc:  # the poll covers a broker that is down; a pinned run must not fail on it
            self._failed_at = self._clock.monotonic()
            self._log.warning(
                "live_layer.notifier_unavailable",
                reasonCode="change_notifier_unavailable",
                errorType=type(exc).__name__,
                choice="poll",
            )
        else:
            self._failed_at = None
            self._log.info("live_layer.notifier_joined")
        finally:
            self._joining = None

    async def joined(self) -> bool:
        """Whether the topic is joined, once a join in progress has settled (tests, shutdown)."""
        if self._joining is not None:
            await self._joining
        return self._subscription is not None

    async def aclose(self) -> None:
        _ = await self.joined()
        subscription, self._subscription = self._subscription, None
        if subscription is not None:
            await subscription.stop()
        if self._close_source is not None:
            await self._close_source()


def worker_live_layer(
    container: Any,  # noqa: ANN401 - the process's DI container (logger, config_service)
    *,
    base_url: str,
    credentials: LiveCredentialSource,
    clock: Clock,
) -> WorkerLiveLayer:
    """The live layer of a worker or the inline claimer: its own consumer group on the deployment's broker."""
    source = NodeLiveLayerSource(base_url=base_url, credentials=credentials)

    async def subscribe(handler: Callable[[ChangeEvent], None]) -> ChangeSubscription:
        from app.services.messaging.config import ConsumerType, get_message_broker_type
        from app.services.messaging.messaging_factory import MessagingFactory
        from app.services.messaging.utils import MessagingUtils

        config = await MessagingUtils.create_control_plane_events_consumer_config(container)
        consumer = MessagingFactory.create_consumer(
            logger=container.logger(),
            config=config,
            broker_type=get_message_broker_type(),
            consumer_type=ConsumerType.SIMPLE,
        )
        notifier = BrokerChangeNotifier(consumer)
        _ = notifier.subscribe(handler)
        await notifier.start()
        return notifier

    return WorkerLiveLayer(ReloadingLiveLayerCache(source, clock), subscribe, clock, close_source=source.aclose)


__all__ = ["ChangeSubscription", "WorkerLiveLayer", "worker_live_layer"]
