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
import itertools
import random
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from app.modules.code_graph import enabled as code_graph_switch
from app.modules.code_graph.build_state import BuildStateStore, PendingBuild, is_due
from app.telemetry.modules import code_graph_metrics
from app.utils.env_config import env_seconds

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.services.code_graph_event_service import CodeGraphEventService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "FULL_REBUILD_INTERVAL_SECONDS",
    "RECONCILE_INTERVAL_SECONDS",
    "RECONCILE_MAX_STARTS_PER_SWEEP",
    "reconcile_forever",
    "reconcile_once",
    "request_due_full_rebuilds",
]

RECONCILE_INTERVAL_SECONDS = env_seconds(
    "CODE_EDGE_BUILD_RECONCILE_INTERVAL_SECONDS", 600.0
)
# A full build scans every block of a repo; after a long outage the first
# sweep would otherwise start one for every repo at once.
RECONCILE_MAX_STARTS_PER_SWEEP = 5
# Incremental builds are checked against a full one this often per repo; the
# full build's removed-edge count is the drift the incremental ones left (R-3).
FULL_REBUILD_INTERVAL_SECONDS = env_seconds(
    "CODE_EDGE_FULL_REBUILD_INTERVAL_SECONDS", 7 * 24 * 3600.0
)
_JITTER_FRACTION = 0.1
_FIRST_SWEEP_DELAY_SECONDS = (30.0, 90.0)


def _round_robin_by_org(builds: list[PendingBuild]) -> list[PendingBuild]:
    """Oldest-due first within an org, one org at a time, so one org's
    backlog cannot take every start of a sweep."""
    by_org: dict[str, list[PendingBuild]] = defaultdict(list)
    for build in sorted(builds, key=lambda b: (b.state.next_attempt_at or 0,
                                               b.state.requested_at or 0)):
        by_org[build.org_id].append(build)
    return [b for group in itertools.zip_longest(*by_org.values()) for b in group if b]


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
    and is left alone, as is a retry whose backoff has not run out and a repo
    whose builds failed for good. ``start_build`` returning False means another
    build holds the repo's lock, on this replica or another, so it is skipped too.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    owed = await BuildStateStore(graph_provider, log).all(owed_only=True)
    due = [b for b in owed if is_due(b.state, now_ms)]
    started = await _start_due(event_service, log, due, limit)
    code_graph_metrics.record_reconcile_sweep(len(due), len(owed) - len(due), started)
    return started


async def _start_due(event_service: CodeGraphEventService, log: Logger,
                     due: list[PendingBuild], limit: int) -> int:
    started = 0
    for pending in _round_robin_by_org(due):
        try:
            handled = await event_service.start_build(
                pending.org_id, pending.connector_id, pending.record_group_id,
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


async def request_due_full_rebuilds(
    graph_provider: IGraphDBProvider,
    log: Logger,
    *,
    now_ms: int | None = None,
    interval_s: float = FULL_REBUILD_INTERVAL_SECONDS,
) -> int:
    """Owe a full rebuild for every built repo whose last full one is older than
    ``interval_s``. The owed builds are started by later sweeps, within their limit.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    store = BuildStateStore(graph_provider, log)
    requested = 0
    for build in await store.all():
        state = build.state
        if state.pending or state.last_build is None:
            continue
        if state.last_full_build is not None and \
                now_ms - state.last_full_build < interval_s * 1000:
            continue
        await store.request_full(build.org_id, build.connector_id, build.record_group_id)
        await store.mark_requested(build.org_id, build.connector_id, build.record_group_id,
                                   now_ms)
        requested += 1
    if requested:
        log.info("Requested scheduled full code edge rebuilds for %d repo(s)", requested)
    return requested


async def reconcile_forever(
    graph_provider: IGraphDBProvider,
    event_service: CodeGraphEventService,
    log: Logger,
    interval_s: float = RECONCILE_INTERVAL_SECONDS,
) -> None:
    """Sweep after a short randomized delay, then every ``interval_s`` until cancelled.

    The first pass is the startup sweep. Delaying it keeps a replica that is
    crash-looping on a build from starting that build again the moment it
    comes up; jitter keeps replicas that restarted together out of lockstep.
    """
    if not code_graph_switch.CODE_GRAPH_ENABLED:
        log.info("CODE_GRAPH_ENABLED is false; code edge build reconciler not running")
        return
    await asyncio.sleep(random.uniform(*_FIRST_SWEEP_DELAY_SECONDS))
    while True:
        try:
            await request_due_full_rebuilds(graph_provider, log)
            await reconcile_once(graph_provider, event_service, log)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Code edge build reconcile sweep failed")
        await asyncio.sleep(interval_s * (1 + random.uniform(0, _JITTER_FRACTION)))
