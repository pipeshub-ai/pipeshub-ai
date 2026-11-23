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


#: Read by the Node licence notices (``ee/modules/license/license-notice.ts``): every company-wide connector that copies
#: access from its source, with its limit and sync status, as ``{"connectors": [...]}``. Storage and database connectors
#: (``gated`` false) are never in it, so they never show as limited.
LICENCE_CONNECTORS_KEY = "/services/licence/connectors"

#: Full syncs one check after another queues for a connector that is still limited, before leaving it to its schedule or a person.
MAX_QUEUED_FULL_SYNCS = 3


def _owed_full_sync(app: dict[str, Any], tries: dict[str, int]) -> tuple[dict[str, Any] | None, bool]:
    """The write that gets this limited connector its full sync (None: none now), and whether a later check must look
    at it again.

    Disabled or signed out: only owed, so the sync that enabling it starts runs full. Running or queued: that run may
    lift the limit, so it is left alone and watched; a running sync flagged here re-issued a full sync after one that
    had already lifted it. A queued sync is made full. Otherwise queued, as at the concurrency limit, for the drain to
    publish: again on a later check when a queued full sync ended with the limit still set (it failed, or read
    nothing), up to ``MAX_QUEUED_FULL_SYNCS`` times."""
    cid = app.get("_key") or app.get("id")
    status = app.get("status")
    owes = not app.get(ConnectorStateKeys.PENDING_FULL_SYNC)
    if status == "DELETING":
        return None, False
    if app.get(ConnectorStateKeys.IS_ACTIVE) is not True or app.get(ConnectorStateKeys.IS_AUTHENTICATED) is False:
        return ({ConnectorStateKeys.PENDING_FULL_SYNC: True} if owes else None), False
    if status in (AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value):
        return ({ConnectorStateKeys.PENDING_FULL_SYNC: True} if owes and status == AppStatus.QUEUED.value else None), True
    if tries.get(cid, 0) >= MAX_QUEUED_FULL_SYNCS:
        return None, False
    now = get_epoch_timestamp_in_ms()
    return {ConnectorStateKeys.PENDING_FULL_SYNC: True, ConnectorStateKeys.PENDING_RESYNC: True,
            "status": AppStatus.QUEUED.value, "queuedAtTimestamp": now, "updatedAtTimestamp": now}, True


async def _queue_org(
    graph_provider: IGraphDBProvider, logger: logging.Logger, tries: dict[str, int], queued: list[str],
    failed: list[str], watched: list[str], parked: list[str], org_id: str,
) -> None:
    for app in await graph_provider.get_org_apps(org_id, active_only=False, raise_on_error=True) or []:
        cid = app.get("_key") or app.get("id")
        if not cid or app.get("accessLimit") is None or not gated(app):
            continue
        updates, watch = _owed_full_sync(app, tries)
        if watch:
            watched.append(cid)
            if app.get("status") == AppStatus.QUEUED.value:
                parked.append(cid)
        elif tries.get(cid, 0) == MAX_QUEUED_FULL_SYNCS and app.get("status") == AppStatus.IDLE.value:
            tries[cid] += 1                         # said once
            logger.warning("Still limited after the full syncs queued for it; left to its schedule or a person",
                           extra={"target_connector_id": cid, "tries": MAX_QUEUED_FULL_SYNCS})
        if updates is None:
            continue
        try:
            await graph_provider.update_node(cid, CollectionNames.APPS.value, updates)
            if "status" in updates:
                tries[cid] = tries.get(cid, 0) + 1
                queued.append(cid)
        except Exception as e:
            failed.append(cid)
            error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="licence_lift",
                                    target_connector_id=cid)


async def queue_limited_full_syncs(
    graph_provider: IGraphDBProvider, logger: logging.Logger, tries: dict[str, int] | None = None,
) -> bool:
    """Queue a full sync for every limited connector that needs one (``_owed_full_sync``), then release the queue.
    ``tries`` counts the full syncs queued per connector across passes. Returns whether nothing is left to watch:
    False while a limited connector is running or queued (it may end still limited) or any write failed, and the
    caller then runs it again. A connector already lifted is not limited any more."""
    tries = {} if tries is None else tries
    queued: list[str] = []
    failed: list[str] = []
    watched: list[str] = []
    parked: list[str] = []          # limited and already QUEUED: an earlier drain may not have published them
    failed_orgs = await for_each_org(
        graph_provider, "licence_lift",
        functools.partial(_queue_org, graph_provider, logger, tries, queued, failed, watched, parked), logger,
        active=True, raise_on_error=True,
    )
    if queued:
        logger.info("Licensed: full syncs owed by limited connectors", extra={"target_connector_ids": ",".join(queued)})
    if queued or parked:
        await drain_queued_syncs(graph_provider, logger)
    return not failed and not failed_orgs and not watched


def connector_states(apps_by_org: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """What the Node notices read: each gated connector's id, org, name, type, creator, limit and sync status."""
    return {"connectors": [
        {"id": app.get("_key") or app.get("id"), "orgId": org_id, "name": app.get("name") or "",
         "type": app.get("type") or "", "createdBy": app.get("createdBy"), "accessLimit": app.get("accessLimit"),
         "status": app.get("status")}
        for org_id, apps in apps_by_org.items() for app in apps
        if gated(app) and (app.get("_key") or app.get("id")) and app.get("status") != "DELETING"
    ]}


async def publish_connector_states(
    config_service: ConfigurationService, graph_provider: IGraphDBProvider, logger: logging.Logger,
    last: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Writes ``connector_states`` when it changed since ``last``, and returns what is published. Only after every org
    was read: a partial list would tell members their results are complete. A failure is logged and keeps ``last``."""
    apps_by_org: dict[str, list[dict[str, Any]]] = {}

    async def read(org_id: str) -> None:
        apps_by_org[org_id] = await graph_provider.get_org_apps(org_id, active_only=False, raise_on_error=True) or []

    try:
        if await for_each_org(graph_provider, "licence_connector_states", read, logger, raise_on_error=True):
            return last
        states = connector_states(apps_by_org)
        if states != last:
            await config_service.set_config(LICENCE_CONNECTORS_KEY, states)
        return states
    except Exception as e:
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.WARNING, step="licence_connector_states")
        return last


async def watch_licence(
    config_service: ConfigurationService, graph_provider: IGraphDBProvider, logger: logging.Logger,
    interval_s: float = LICENCE_MAX_AGE_SECONDS,
) -> None:
    """Every ``interval_s`` (the licence is read at most that often anyway), queue the limited connectors' full syncs
    when the install has become licensed: on the first check after a start, which covers a key entered while this
    process was down, and on every change from unlicensed to licensed. Checks go on while a limited connector is
    still running or queued, or a pass or the licence read failed, so one whose full sync ends with the limit still set
    is queued again."""
    done = False
    tries: dict[str, int] = {}
    published: dict[str, Any] | None = None
    while True:
        try:
            if not await permission_sync_licensed(config_service):
                done = False
                tries.clear()
            elif not done:
                done = await queue_limited_full_syncs(graph_provider, logger, tries)
        except Exception as e:
            done = False
            error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="licence_lift")
        # After the pass, so a connector it queued is published as such.
        published = await publish_connector_states(config_service, graph_provider, logger, published)
        await asyncio.sleep(interval_s)
