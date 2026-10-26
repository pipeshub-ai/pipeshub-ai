"""Tell people when a newly connected source is ready to search.

A connector's first sync sets ``awaitingFirstSync`` (``sync_runner``). Reading
the source ends long before indexing does, so readiness is checked here, once
a minute: the first successful sync has ended and none of the connector's
records is still waiting or in progress. The flag is cleared with a
compare-and-set, so with several connector processes exactly one sends the
notice. Until then, search and chat tell people results may be incomplete.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import AppStatus, CollectionNames, ProgressStatus
from app.connectors.core.base.connector.connector_service import (
    DEFAULT_CONNECTOR_NOTIFICATION_LINK,
)
from app.connectors.core.constants import ConnectorStateKeys, SyncRunOutcome
from app.services.notification.types import (
    NotificationOrigin,
    NotificationSeverity,
    NotificationType,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.notification.notification_service import NotificationService

CHECK_INTERVAL_S = 60.0

_NOT_YET_INDEXED = [
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.PAUSED.value,
    ProgressStatus.IN_PROGRESS.value,
]


async def watch_first_syncs_forever(
    graph_provider: IGraphDBProvider, notification_service: "NotificationService | None", logger: logging.Logger
) -> None:
    while True:
        try:
            await check_first_syncs(graph_provider, notification_service, logger)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"First-sync readiness check failed: {e}")
        await asyncio.sleep(CHECK_INTERVAL_S)


async def check_first_syncs(
    graph_provider: IGraphDBProvider, notification_service: "NotificationService | None", logger: logging.Logger
) -> list[str]:
    """Mark ready, and announce, every connector whose first sync is now searchable; their ids."""
    ready: list[str] = []
    for org in (await graph_provider.get_all_orgs(active=True)) or []:
        org_id = org.get("_key") or org.get("id")
        if not org_id:
            continue
        for app in (await graph_provider.get_org_apps(org_id)) or []:
            if not app.get(ConnectorStateKeys.AWAITING_FIRST_SYNC):
                continue
            connector_id = app.get("_key") or app.get("id")
            if connector_id and await _mark_ready(graph_provider, notification_service, logger, org_id, connector_id, app):
                ready.append(connector_id)
    return ready


async def _mark_ready(
    graph_provider: IGraphDBProvider,
    notification_service: "NotificationService | None",
    logger: logging.Logger,
    org_id: str,
    connector_id: str,
    app: dict[str, Any],
) -> bool:
    if not app.get("isActive") or app.get("status") not in (None, AppStatus.IDLE.value):
        return False
    if app.get(ConnectorStateKeys.LAST_SYNC_OUTCOME) != SyncRunOutcome.SUCCEEDED.value:
        return False
    pending = await graph_provider.count_connector_records_by_status(connector_id, _NOT_YET_INDEXED)
    if sum(pending.values()):
        return False
    claimed = await graph_provider.update_node_fields_if_match(
        connector_id,
        CollectionNames.APPS.value,
        {ConnectorStateKeys.AWAITING_FIRST_SYNC: False, ConnectorStateKeys.READY_AT: get_epoch_timestamp_in_ms()},
        {ConnectorStateKeys.AWAITING_FIRST_SYNC: True},
    )
    if not claimed:
        return False
    searchable = (
        await graph_provider.count_connector_records_by_status(connector_id, [ProgressStatus.COMPLETED.value])
    ).get(ProgressStatus.COMPLETED.value, 0)
    logger.info(f"Connector {connector_id} finished its first sync: {searchable} record(s) searchable")
    recipients = [uid for uid in dict.fromkeys([app.get("createdBy"), app.get("lastSyncedBy")]) if uid]
    if notification_service is None or not recipients:
        return True
    name = app.get("name") or app.get("type") or "Your connector"
    connector_type = app.get("type") or ""
    await notification_service.publish_notification(
        org_id=org_id,
        origin=NotificationOrigin.CONNECTOR,
        type=NotificationType.CONNECTOR_SUCCESS,
        severity=NotificationSeverity.SUCCESS,
        title=f"{name} is ready",
        message=(
            f"{searchable:,} items from {name} are now searchable."
            if searchable
            else f"{name} finished its first sync. It found nothing to make searchable."
        ),
        payload={"kind": "connector_ready", "connectorId": connector_id, "searchable": searchable},
        redirect_link=(
            DEFAULT_CONNECTOR_NOTIFICATION_LINK
            + f"{app.get('scope') or 'team'}/?connectorType={connector_type}"
        ),
        recipient_user_ids=recipients,
    )
    return True
