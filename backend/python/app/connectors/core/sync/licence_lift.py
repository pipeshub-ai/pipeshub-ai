"""When a licence key is entered, start the full sync that lifts each connector's access limit.

A connector limited while the install was unlicensed (``accessLimit``) is lifted only by a full sync with a licence
(``DataSourceEntitiesProcessor.lift_access_limit``). Without this, a MANUAL connector stayed limited until someone ran
it. ``watch_licence`` notices the licence becoming valid and queues those full syncs through the queue the concurrency
limit already uses (``EventService._mark_queued``'s fields, released by ``drain_queued_syncs``).
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.base.data_processor.permission_sync_gate import (
    LICENCE_MAX_AGE_SECONDS,
    gated,
    permission_sync_licensed,
)
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_runner import drain_queued_syncs
from app.core.org_jobs import for_each_org
from app.utils import error_class
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


def _owed_full_sync(app: dict[str, Any]) -> dict[str, Any] | None:
    """The write that gets this limited connector its full sync, or None when it needs none.

    Running: flagged as a declined resync, so its finalizer re-issues it as a full sync. Queued: the queued sync runs
    full. Disabled or signed out: only owed, so the sync that enabling it starts runs full. Otherwise queued, as at the
    concurrency limit, for the drain to publish."""
    status = app.get("status")
    if status == "DELETING":
        return None
    owed = {ConnectorStateKeys.PENDING_FULL_SYNC: True}
    if app.get(ConnectorStateKeys.IS_ACTIVE) is not True or app.get(ConnectorStateKeys.IS_AUTHENTICATED) is False:
        return owed
    if status in (AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value):
        return {**owed, ConnectorStateKeys.PENDING_RESYNC: True}
    if status == AppStatus.QUEUED.value:
        return owed
    now = get_epoch_timestamp_in_ms()
    return {**owed, ConnectorStateKeys.PENDING_RESYNC: True, "status": AppStatus.QUEUED.value,
            "queuedAtTimestamp": now, "updatedAtTimestamp": now}


async def _queue_org(
    graph_provider: IGraphDBProvider, logger: logging.Logger, queued: list[str], failed: list[str], org_id: str,
) -> None:
    for app in await graph_provider.get_org_apps(org_id, active_only=False, raise_on_error=True) or []:
        cid = app.get("_key") or app.get("id")
        if not cid or app.get("accessLimit") is None or not gated(app):
            continue
        updates = _owed_full_sync(app)
        if updates is None:
            continue
        try:
            await graph_provider.update_node(cid, CollectionNames.APPS.value, updates)
            queued.append(cid)
        except Exception as e:
            failed.append(cid)
            error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="licence_lift",
                                    target_connector_id=cid)


async def queue_limited_full_syncs(graph_provider: IGraphDBProvider, logger: logging.Logger) -> bool:
    """Queue a full sync for every limited connector (``_owed_full_sync``), then release the queue. Returns whether
    every connector was handled; False asks the caller to run it again. Writing again what an earlier pass wrote is
    harmless, and a connector already lifted is not limited any more."""
    queued: list[str] = []
    failed: list[str] = []
    failed_orgs = await for_each_org(
        graph_provider, "licence_lift", functools.partial(_queue_org, graph_provider, logger, queued, failed), logger,
        active=True, raise_on_error=True,
    )
    if queued:
        logger.info("Licensed: full syncs owed by limited connectors", extra={"target_connector_ids": ",".join(queued)})
        await drain_queued_syncs(graph_provider, logger)
    return not failed and not failed_orgs


async def watch_licence(
    config_service: ConfigurationService, graph_provider: IGraphDBProvider, logger: logging.Logger,
    interval_s: float = LICENCE_MAX_AGE_SECONDS,
) -> None:
    """Every ``interval_s`` (the licence is read at most that often anyway), queue the limited connectors' full syncs
    when the install has become licensed: on the first check after a start, which covers a key entered while this
    process was down, and on every change from unlicensed to licensed. A pass that did not handle every connector, or
    a failed read, runs again on the next check."""
    done = False
    while True:
        try:
            if not await permission_sync_licensed(config_service):
                done = False
            elif not done:
                done = await queue_limited_full_syncs(graph_provider, logger)
        except Exception as e:
            done = False
            error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="licence_lift")
        await asyncio.sleep(interval_s)
