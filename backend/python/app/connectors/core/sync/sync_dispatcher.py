"""Publishing a sync request.

Submit publishes to ``sync-events`` and lets whichever process owns the
consumer pick it up. Here that is always this process.

`submit` takes identifiers, never a live connector. A `BaseConnector` holds
aiohttp sessions and DI-resolved clients; it cannot cross a process boundary, so
the consumer rebuilds it from the ids.

Admission is not decided here. `SyncCoordinator.begin` owns that, and this asks
it only whether publishing would be pointless because the connector is already
syncing.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.connectors.core.sync.sync_coordinator import get_coordinator
from app.services.messaging.config import Topic
from app.utils.time_conversion import get_epoch_timestamp_in_ms

@dataclass(frozen=True)
class SyncSpec:
    connector_id: str
    connector_name: str
    org_id: str
    full_sync: bool = False


class SubmitResult(Enum):
    """Three outcomes, not a bool.

    `process_event` reads False as "transient failure, redeliver", so collapsing
    "already running" into the same value as "publish failed" would redeliver
    the event forever and stall the partition behind a healthy connector.
    """

    ACCEPTED = "accepted"
    DECLINED_RUNNING = "declined_running"
    FAILED = "failed"


class SyncDispatcher(Protocol):
    async def submit(self, spec: SyncSpec) -> SubmitResult: ...
    async def is_running(self, connector_id: str) -> bool: ...
    async def request_stop(self, connector_id: str) -> bool: ...


class SyncEventDispatcher:
    def __init__(self, logger: logging.Logger, producer: object) -> None:
        self.logger = logger
        self._producer = producer

    async def submit(self, spec: SyncSpec) -> SubmitResult:
        if await self.is_running(spec.connector_id):
            return SubmitResult.DECLINED_RUNNING

        event_type = f"{spec.connector_name.replace(' ', '').lower()}.resync"
        now = str(get_epoch_timestamp_in_ms())
        try:
            await self._producer.send_message(
                topic=Topic.SYNC_EVENTS.value,
                message={
                    "eventType": event_type,
                    "payload": {
                        "orgId": spec.org_id,
                        "connector": spec.connector_name,
                        "connectorId": spec.connector_id,
                        "fullSync": spec.full_sync,
                        "origin": "CONNECTOR",
                        "createdAtTimestamp": now,
                        "updatedAtTimestamp": now,
                        "sourceCreatedAtTimestamp": now,
                    },
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
                key=spec.connector_id,
            )
        except Exception as e:
            self.logger.error(
                f"Failed to publish sync event for {spec.connector_id}: {e}"
            )
            return SubmitResult.FAILED
        return SubmitResult.ACCEPTED

    async def is_running(self, connector_id: str) -> bool:
        coordinator = get_coordinator()
        if coordinator is None:
            return False
        try:
            return await coordinator.is_running(connector_id)
        except Exception as e:
            self.logger.error(f"Could not read sync state for {connector_id}: {e}")
            return False

    async def request_stop(self, connector_id: str) -> bool:
        coordinator = get_coordinator()
        if coordinator is None:
            return False
        try:
            return await coordinator.request_stop(connector_id)
        except Exception as e:
            self.logger.error(f"Could not request stop for {connector_id}: {e}")
            return False


class _Registry:
    dispatcher: SyncDispatcher | None = None


def set_dispatcher(dispatcher: SyncDispatcher) -> None:
    _Registry.dispatcher = dispatcher


def get_dispatcher() -> SyncDispatcher | None:
    return _Registry.dispatcher
