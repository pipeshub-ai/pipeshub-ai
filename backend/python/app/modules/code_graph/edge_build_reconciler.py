"""Pick up edge builds that were asked for and never finished.

The consumer acknowledges a ``buildCodeEdges`` message before the build runs,
so a build that dies with its process, raises, or is dead-lettered while another
build holds the lock leaves the repo marked ``edgeBuildPending`` with nobody
left to act on it. This sweep reads that mark and hands each stale repo back to
the same handler path the consumer uses; the lock, drained check and watermark
there make a repeat request harmless.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING

from app.config.constants.arangodb import EventTypes
from app.modules.code_graph import edge_build_trigger
from app.utils.env_config import env_seconds

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.services.code_graph_event_service import CodeGraphEventService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "RECONCILE_INTERVAL_SECONDS",
    "RECONCILE_MAX_STARTS_PER_SWEEP",
    "reconcile_forever",
    "reconcile_once",
]

RECONCILE_INTERVAL_SECONDS = env_seconds(
    "CODE_EDGE_BUILD_RECONCILE_INTERVAL_SECONDS", 600.0
)
# A full build scans every block of a repo; after a long outage the first
# sweep would otherwise start one for every repo at once.
RECONCILE_MAX_STARTS_PER_SWEEP = 5
_JITTER_FRACTION = 0.1


async def reconcile_once(
    graph_provider: IGraphDBProvider,
    event_service: CodeGraphEventService,
    log: Logger,
    *,
    now_ms: int | None = None,
    limit: int = RECONCILE_MAX_STARTS_PER_SWEEP,
) -> int:
    """Start builds for repos still owed one. Returns how many were started.

    A request younger than the dedupe window still has its message in flight
    and is left alone. ``process_event`` returning False means another build
    holds the repo's lock, on this replica or another, so it is skipped too.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    started = 0
    for pending in await edge_build_trigger.pending_builds(graph_provider):
        if not edge_build_trigger.request_is_stale(pending.state, now_ms):
            continue
        try:
            handled = await event_service.process_event(
                EventTypes.BUILD_CODE_EDGES.value,
                {
                    "orgId": pending.org_id,
                    "connectorId": pending.connector_id,
                    "recordGroupId": pending.record_group_id,
                },
            )
        except Exception:
            log.exception(
                "Failed to reconcile code edge build for org=%s record_group=%s",
                pending.org_id,
                pending.record_group_id,
            )
            continue
        if not handled:
            continue
        started += 1
        log.info(
            "Reconciler started an owed code edge build for org=%s record_group=%s",
            pending.org_id,
            pending.record_group_id,
        )
        if started >= limit:
            break
    return started


async def reconcile_forever(
    graph_provider: IGraphDBProvider,
    event_service: CodeGraphEventService,
    log: Logger,
    interval_s: float = RECONCILE_INTERVAL_SECONDS,
) -> None:
    """Sweep now, then every ``interval_s`` until cancelled.

    The first pass is the startup sweep. Jitter keeps replicas that restarted
    together from querying the graph in lockstep.
    """
    while True:
        try:
            await reconcile_once(graph_provider, event_service, log)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Code edge build reconcile sweep failed")
        await asyncio.sleep(interval_s * (1 + random.uniform(0, _JITTER_FRACTION)))
