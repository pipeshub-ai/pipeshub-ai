"""Once-a-minute upkeep of the Redis Streams lane map, and the one-time upgrade fix-up.

Runs in the indexing service, inside the stale-record recovery pass, so under
the cluster-wide ``recovery`` lock and on one replica at a time. Each pass:

1. Settles moves and deletes, rebuilds the per-lane counts and writes the busy
   flags, in one atomic script (``LaneAssignments.upkeep``), from the same
   backlog read the stranded-record sweep uses.
2. Reads the connectors and knowledge bases from the graph: corrects a class
   that was guessed on a first publish, and frees the lane of any connector
   that no longer exists (in case a delete path missed it).
3. The first time, separates the large connectors that share a lane: the one
   with the most records waiting keeps it, the others are moved by the edition's
   rule and their queued records are re-published to their new lane.
4. Publishes the lane metrics and keeps a report for ``GET /health``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.indexing.record_republish import is_parked_duplicate, record_event
from app.services.messaging.config import messaging_env
from app.services.messaging.lanes.assignment_policy import (
    LaneRequestReason,
    is_large_class,
)
from app.services.messaging.lanes.hash_router import RedisLaneRouter
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY
from app.services.messaging.lanes.lifecycle import connector_class_of
from app.telemetry.modules import scheduling_metrics as metrics
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.messaging.lanes.assignment import LaneAssignments, LaneEntry
    from app.services.messaging.lanes.backlog import LaneBacklog

__all__ = [
    "RESCUE_CAP_PER_CONNECTOR",
    "LaneReport",
    "last_lane_report",
    "run_lane_upkeep",
]

_T = TypeVar("_T")

# A moved connector's already-queued records re-published to its new lane at
# the upgrade, at most this many per connector, oldest first.
RESCUE_CAP_PER_CONNECTOR = 20_000
# Producers may keep using a lane they cached for one cache lifetime after a
# move; the fence waits that long and this much more.
_FENCE_MARGIN_MS = 30_000
_PAGE = 500
# Walks over a connector's queued records per re-send; see _rescue.
_RESCUE_WALKS = 5
# The lane view lists at most this many connectors per lane, large ones first.
_LISTED_PER_LANE = 50


@dataclass(frozen=True)
class _App:
    connector_class: str
    name: str | None
    org_id: str | None
    connector_type: str | None


@dataclass(frozen=True)
class LaneReport:
    """The lane view on ``GET /health``, from the last upkeep pass."""

    topic: str
    updated_at_ms: int
    lane_count: int
    migrated_at_ms: int | None
    backlog_read: bool
    lanes: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "updatedAt": self.updated_at_ms,
            "laneCount": self.lane_count,
            "upgradeFixUpDoneAt": self.migrated_at_ms,
            "backlogRead": self.backlog_read,
            "lanes": self.lanes,
        }


# By topic: the view from the last pass this replica ran.
_last_reports: dict[str, LaneReport] = {}


def last_lane_report(topic: str) -> LaneReport | None:
    """The view from the last pass this replica ran, if it ran one."""
    return _last_reports.get(topic)


async def run_lane_upkeep(
    *,
    assignments: LaneAssignments,
    graph_provider: IGraphDBProvider,
    producer: IMessagingProducer,
    backlog: LaneBacklog | None,
    logger: Logger,
    rescue_cap: int = RESCUE_CAP_PER_CONNECTOR,
    run: Callable[[Awaitable[_T]], Awaitable[_T]] | None = None,
) -> LaneReport:
    """One upkeep pass. ``backlog`` is None when the broker could not be read.
    ``run`` wraps each re-send, as the other recovery publishes are wrapped."""
    topic = assignments.topic
    oldest_by_lane = (
        None if backlog is None else _by_lane_number(topic, backlog.oldest_waiting_ms)
    )
    fence_delay_ms = int(messaging_env.fair_scheduling_lane_cache_seconds * 1000) + _FENCE_MARGIN_MS
    settled = await assignments.upkeep(oldest_by_lane, fence_delay_ms=fence_delay_ms)

    # Read before the graph, so a connector created while the graph is read is
    # not in it, and cannot be mistaken for one whose document is gone.
    entries = await assignments.read_map()
    apps = await _read_apps(graph_provider, logger)
    released = corrected = moved = rescued = 0
    if apps is not None:
        released, corrected = await _reconcile_with_graph(
            assignments, graph_provider, entries, apps, logger
        )
        if "migratedAt" not in await assignments.read_meta():
            moved, rescued, finished = await _upgrade_fix_up(
                assignments, graph_provider, producer, apps, logger, rescue_cap, run
            )
            if finished:
                await assignments.mark_migrated()
            logger.info(
                "Queue lanes: the one-time upgrade fix-up %s; %d connector(s) moved "
                "off a shared lane and %d of their queued record(s) re-sent to their "
                "new lanes this pass",
                "is done" if finished else "carries on next pass",
                moved,
                rescued,
            )

    if settled.fenced or settled.cleared or settled.removed or released or corrected:
        logger.info(
            "Queue lanes: %d move(s) or delete(s) fenced, %d move(s) settled, %d "
            "deleted connector(s) removed, %d lane(s) freed for connectors that no "
            "longer exist, %d class(es) corrected",
            settled.fenced,
            settled.cleared,
            settled.removed,
            released,
            corrected,
        )

    report = _report(
        topic,
        await assignments.read_map(),
        await assignments.read_meta(),
        apps or {},
        backlog,
        assignments.lane_count,
        settled.now_ms,
    )
    _publish_metrics(report)
    _last_reports[topic] = report
    return report


_LANE = re.compile(r"\.(\d+)$")


def _by_lane_number(topic: str, by_stream: Mapping[str, float]) -> dict[int, float]:
    lanes: dict[int, float] = {}
    for stream, value in by_stream.items():
        match = _LANE.search(stream)
        if match and stream == f"{topic}.{match.group(1)}":
            lanes[int(match.group(1))] = value
    return lanes


async def _read_apps(graph_provider: IGraphDBProvider, logger: Logger) -> dict[str, _App] | None:
    """Every connector and knowledge base; None if they could not all be read,
    so nothing is freed on a partial answer."""
    apps: dict[str, _App] = {}
    skip = 0
    try:
        while True:
            page = await graph_provider.get_documents_paginated(
                CollectionNames.APPS.value,
                skip=skip,
                limit=_PAGE,
                sort_field="_key",
                raise_on_error=True,
            )
            for doc in page:
                key = doc.get("_key") or doc.get("id")
                if not key:
                    continue
                apps[str(key)] = _App(
                    connector_class=connector_class_of(doc.get("type"), doc.get("scope")),
                    name=doc.get("name"),
                    org_id=doc.get("orgId"),
                    connector_type=doc.get("type"),
                )
            if len(page) < _PAGE:
                return apps
            skip += len(page)
    except Exception as e:
        logger.warning(
            "Queue lanes: could not read the connectors, so no lane was freed or "
            "reclassified this pass: %s: %s",
            type(e).__name__,
            e,
        )
        return None


async def _reconcile_with_graph(
    assignments: LaneAssignments,
    graph_provider: IGraphDBProvider,
    entries: Mapping[str, LaneEntry],
    apps: Mapping[str, _App],
    logger: Logger,
) -> tuple[int, int]:
    released = corrected = 0
    for connector_id, entry in entries.items():
        if connector_id == DEFAULT_LANE_KEY or not entry.is_live:
            continue
        try:
            app = apps.get(connector_id)
            if app is None:
                # The paged scan can miss a live connector when one before it
                # is removed mid-scan, so a lane is freed only when a direct
                # read says the connector is gone. A failed read frees nothing.
                gone = not await graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value, raise_on_error=True
                )
                if gone:
                    released += await assignments.release(connector_id)
            elif app.connector_class != entry.connector_class:
                corrected += await assignments.correct_class(
                    connector_id, entry, app.connector_class
                )
        except Exception as e:
            logger.warning(
                "Queue lanes: could not update the entry of connector %s: %s: %s",
                connector_id,
                type(e).__name__,
                e,
            )
    return released, corrected


async def _upgrade_fix_up(
    assignments: LaneAssignments,
    graph_provider: IGraphDBProvider,
    producer: IMessagingProducer,
    apps: Mapping[str, _App],
    logger: Logger,
    rescue_cap: int,
    run: Callable[[Awaitable[_T]], Awaitable[_T]] | None,
) -> tuple[int, int, bool]:
    """Separate the large connectors that share a lane, once.

    Every connector without an entry is first recorded where hashing has
    always put it. Then, for each lane, the large connectors that may have
    events on it are its occupants and any that moved off it on their first
    publish after the upgrade (``prevLane``). If there is more than one, the
    one with the most records waiting keeps the lane, since its backlog is
    already there. Each other occupant is moved by the edition's rule; each
    other connector, moved now or before, has up to ``rescue_cap`` of its
    queued records re-sent to its new lane, oldest first, so they stop waiting
    behind the one that stayed. The copies left on the old lane are harmless:
    the handler skips a record that is already complete, and the record lease
    stops two copies running at once.

    The lazy placement alone keeps whichever connector published first; this
    is what keeps the one with the backlog instead.

    Returns (moved, re-sent, finished). It is not finished, and runs again next
    pass, while a connector could not be recorded, a lane's queued counts could
    not all be read, a connector that has to move is still settling an earlier
    move, or a re-send stopped short. Staying put because every lane already
    holds a large connector is a decision, and finishes. A connector moved or
    re-sent in an earlier pass (``fix_up_progress``) is not decided again.
    """
    finished = True
    for connector_id, app in apps.items():
        try:
            await assignments.record_at_hash_lane(
                connector_id,
                app.connector_class,
                org_id=app.org_id,
                connector_type=app.connector_type,
            )
        except Exception as e:
            finished = False
            logger.warning(
                "Queue lanes: could not record connector %s at its lane: %s: %s",
                connector_id,
                type(e).__name__,
                e,
            )

    entries = await assignments.read_map()
    progress = await assignments.fix_up_progress()
    large = {
        connector_id: entry
        for connector_id, entry in entries.items()
        if connector_id in apps and entry.is_live and is_large_class(entry.connector_class)
    }
    on_lane: dict[int, list[str]] = {}
    for connector_id, entry in large.items():
        if connector_id in progress:
            continue
        on_lane.setdefault(entry.lane, []).append(connector_id)
        if entry.prev_lane is not None:
            on_lane.setdefault(entry.prev_lane, []).append(connector_id)

    moved = rescued = 0
    router = RedisLaneRouter(assignments.lane_count)

    async def rescue(connector_id: str, entry: LaneEntry) -> int:
        nonlocal finished
        sent, complete = await _rescue(
            graph_provider,
            producer,
            connector_id,
            router.lane_name(assignments.topic, entry.lane),
            rescue_cap,
            logger,
            run,
        )
        if complete:
            await assignments.note_fix_up_progress(connector_id, "rescued")
        else:
            finished = False
        return sent

    # Moved on an earlier pass, but its re-send stopped short.
    for connector_id, step in progress.items():
        if step == "moved" and connector_id in large:
            rescued += await rescue(connector_id, large[connector_id])

    for lane, connectors in sorted(on_lane.items()):
        if len(connectors) < 2:
            continue
        waiting: dict[str, int] = {}
        for connector_id in connectors:
            count = await _queued(graph_provider, apps[connector_id], connector_id, logger)
            if count is not None:
                waiting[connector_id] = count
        if len(waiting) < len(connectors):
            # An unknown backlog must not read as an empty one: the connector
            # that holds the lane's backlog is the one most likely to time out.
            finished = False
            continue
        keeper = min(
            connectors,
            key=lambda c: (-waiting[c], entries[c].lane != lane, c),
        )
        for connector_id in connectors:
            if connector_id == keeper:
                continue
            entry = entries[connector_id]
            if entry.lane == lane:
                try:
                    entry = await assignments.move(
                        connector_id,
                        LaneRequestReason.UPGRADE,
                        org_id=apps[connector_id].org_id,
                        # A keeper that already moved off still has its backlog here.
                        still_held=(lane,) if entries[keeper].lane != lane else (),
                    )
                except Exception as e:
                    # Still settling an earlier move, or Redis said no: next pass.
                    finished = False
                    logger.info(
                        "Queue lanes: connector %s has to leave lane %d, which it "
                        "shares with %s, and will be moved on a later pass: %s: %s",
                        connector_id,
                        lane,
                        keeper,
                        type(e).__name__,
                        e,
                    )
                    continue
                if entry.lane == lane:
                    # Every lane already has a large connector; it stays.
                    continue
                moved += 1
                entries[connector_id] = large[connector_id] = entry
                await assignments.note_fix_up_progress(connector_id, "moved")
            elif any(
                other != connector_id and other_entry.lane == entry.lane
                for other, other_entry in large.items()
            ):
                # Its current lane is shared too; re-sending there would only
                # put its records behind someone else. The pass that moves it
                # off that lane re-sends them.
                finished = False
                continue
            sent = await rescue(connector_id, entry)
            rescued += sent
            logger.info(
                "Queue lanes: connector %s (%s) is on lane %d, off lane %d where %s "
                "(%d waiting) stays; %d of its %d queued record(s) re-sent there",
                apps[connector_id].name or connector_id,
                connector_id,
                entry.lane,
                lane,
                apps[keeper].name or keeper,
                waiting[keeper],
                sent,
                waiting[connector_id],
            )
    return moved, rescued, finished


async def _org_of(
    graph_provider: IGraphDBProvider, app: _App, connector_id: str
) -> str | None:
    """The connector's org: knowledge bases carry ``orgId``, but connectors
    created before it was stored on the document have only the org-app edge
    (the same resolution as ``ConnectorRegistry._org_id_from_edge``)."""
    if app.org_id:
        return app.org_id
    edges = await graph_provider.get_edges_to_node(
        f"{CollectionNames.APPS.value}/{connector_id}",
        CollectionNames.ORG_APP_RELATION.value,
    )
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        # Neo4j returns a bare id in from_id; Arango a handle in _from.
        source = edge.get("from_id") or edge.get("_from")
        if source:
            return str(source).rsplit("/", 1)[-1]
    return None


async def _queued(
    graph_provider: IGraphDBProvider, app: _App, connector_id: str, logger: Logger
) -> int | None:
    """Records of this connector put in line and not yet picked up; None if
    the graph could not say. The providers answer a failure with
    ``success: False`` rather than raising, and an unknown org with zeros, so
    both are caught here rather than read as an empty queue."""
    try:
        org_id = await _org_of(graph_provider, app, connector_id)
        if not org_id:
            logger.warning(
                "Queue lanes: could not find the org of connector %s to count its "
                "queued records",
                connector_id,
            )
            return None
        stats = await graph_provider.get_connector_stats(org_id, connector_id)
    except Exception as e:
        logger.warning(
            "Queue lanes: could not count the queued records of connector %s: %s: %s",
            connector_id,
            type(e).__name__,
            e,
        )
        return None
    counts = (((stats or {}).get("data") or {}).get("stats") or {}).get("indexingStatus")
    if not (stats or {}).get("success", True) or not isinstance(counts, dict):
        logger.warning(
            "Queue lanes: could not count the queued records of connector %s",
            connector_id,
        )
        return None
    return int(counts.get(ProgressStatus.QUEUED.value, 0) or 0)


async def _rescue(
    graph_provider: IGraphDBProvider,
    producer: IMessagingProducer,
    connector_id: str,
    stream: str,
    cap: int,
    logger: Logger,
    run: Callable[[Awaitable[_T]], Awaitable[_T]] | None,
) -> tuple[int, bool]:
    """Re-send up to ``cap`` of a moved connector's queued records to ``stream``,
    oldest in line first, so its new lane holds them ahead of what comes next.

    The records are paged by offset while the consumer is already working
    through them, so a record that leaves QUEUED moves every later one up a
    place and the next page can step over one. So the walk is repeated, sending
    only what has not been sent, until a whole walk finds nothing new. Records
    put in line after the re-send began are left alone: their events are on the
    new lane already. Returns how many were sent and whether it finished.
    """
    started_ms = get_epoch_timestamp_in_ms()
    sent: set[str] = set()

    def stopped(error: Exception) -> tuple[int, bool]:
        logger.warning(
            "Queue lanes: stopped re-sending the queued records of connector %s "
            "after %d: %s: %s",
            connector_id,
            len(sent),
            type(error).__name__,
            error,
        )
        return len(sent), False

    for _walk in range(_RESCUE_WALKS):
        found = False
        skip = 0
        while True:
            try:
                page = await graph_provider.get_documents_paginated(
                    CollectionNames.RECORDS.value,
                    skip=skip,
                    limit=_PAGE,
                    filters={
                        "connectorId": connector_id,
                        "indexingStatus": ProgressStatus.QUEUED.value,
                    },
                    sort_field="queuedAtTimestamp",
                    raise_on_error=True,
                )
            except Exception as e:
                return stopped(e)
            for record in page:
                if len(sent) >= cap:
                    return len(sent), True
                record_key = str(record.get("_key") or record.get("id") or "")
                if not record_key or record_key in sent or is_parked_duplicate(record):
                    continue
                try:
                    if float(record.get("queuedAtTimestamp")) > started_ms:  # type: ignore[arg-type]
                        continue
                except (TypeError, ValueError):
                    pass
                event_type, payload = record_event(
                    record, record_key=record_key, connector_id=connector_id
                )
                send = producer.send_event(
                    topic=stream, event_type=event_type, payload=payload, key=record_key
                )
                try:
                    await (run(send) if run is not None else send)
                except Exception as e:
                    return stopped(e)
                sent.add(record_key)
                found = True
            if len(page) < _PAGE:
                break
            skip += len(page)
        if not found:
            return len(sent), True
    # Still finding records it had skipped: the next pass carries on.
    return len(sent), False


def _report(
    topic: str,
    entries: Mapping[str, LaneEntry],
    meta: Mapping[str, str],
    apps: Mapping[str, _App],
    backlog: LaneBacklog | None,
    lane_count: int,
    now_ms: int,
) -> LaneReport:
    router = RedisLaneRouter(lane_count)
    numbers = set(range(lane_count)) | {e.lane for e in entries.values()}
    if backlog is not None:
        numbers |= set(_by_lane_number(topic, backlog.oldest_waiting_ms))
    lanes: list[dict[str, object]] = []
    for lane in sorted(numbers):
        stream = router.lane_name(topic, lane)
        here = [(c, e) for c, e in entries.items() if e.lane == lane]
        here.sort(key=lambda item: (not is_large_class(item[1].connector_class), item[0]))
        oldest = backlog.oldest_waiting_ms.get(stream) if backlog is not None else None
        lanes.append(
            {
                "lane": lane,
                "stream": stream,
                "large": sum(e.is_live and is_large_class(e.connector_class) for _, e in here),
                "small": sum(e.is_live and not is_large_class(e.connector_class) for _, e in here),
                "connectors": [
                    {
                        "id": connector_id,
                        "name": apps[connector_id].name if connector_id in apps else None,
                        "class": entry.connector_class,
                        "state": entry.state,
                    }
                    for connector_id, entry in here[:_LISTED_PER_LANE]
                ],
                "connectorsNotListed": max(0, len(here) - _LISTED_PER_LANE),
                "movingOn": [
                    {"id": c, "fromLane": e.prev_lane, "fencedAt": e.fence_ms}
                    for c, e in here
                    if e.prev_lane is not None
                ],
                "movingOff": [
                    {"id": c, "toLane": e.lane, "fencedAt": e.fence_ms}
                    for c, e in entries.items()
                    if e.prev_lane == lane
                ],
                "oldestWaitingSeconds": (
                    None if oldest is None else max(0.0, round((now_ms - oldest) / 1000, 1))
                ),
                "pending": backlog.pending.get(stream) if backlog is not None else None,
            }
        )
    try:
        migrated_at = int(meta["migratedAt"]) if meta.get("migratedAt") else None
    except ValueError:
        migrated_at = None
    return LaneReport(
        topic=topic,
        updated_at_ms=now_ms,
        lane_count=lane_count,
        migrated_at_ms=migrated_at,
        backlog_read=backlog is not None,
        lanes=lanes,
    )


def _publish_metrics(report: LaneReport) -> None:
    for view in report.lanes:
        lane = str(view["lane"])
        metrics.record_lane_connectors(lane, "large", int(view["large"]))  # type: ignore[call-overload]
        metrics.record_lane_connectors(lane, "small", int(view["small"]))  # type: ignore[call-overload]
        oldest = view["oldestWaitingSeconds"]
        if report.backlog_read:
            metrics.record_lane_oldest_waiting(lane, float(oldest or 0.0))  # type: ignore[arg-type]
