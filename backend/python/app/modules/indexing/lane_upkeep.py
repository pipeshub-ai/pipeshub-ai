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
from app.services.graph_db.common.utils import org_id_from_app_edge
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
    always put it. For each lane, the large connectors that may have events
    on it are its occupants and any that moved off it on their first publish
    after the upgrade (``prevLane``). Where there are two or more, the one
    with the most records waiting keeps the lane, since its backlog is
    already there. Then, largest queue first:

    - another occupant moves to a lane of its own, if one is free, and its
      queued records are re-sent there; one still settling an earlier move
      holds a free lane for a later pass, so a smaller queue cannot take it;
      with no lane free it stays, which is a decision;
    - a connector that already moved off has its queued records re-sent to
      the lane it is on now, since its new events already go there.

    A lane is free only if no large connector is on it or still settling off
    it (its backlog is there). The edition's rule picks the lane; where it
    picks a lane already taken, that is its decision. Re-sending leaves the
    old copies, which are harmless: the handler skips a record that is
    already complete, and the record lease stops two copies running at once.

    Returns (moved, re-sent, finished). It is not finished, and runs again
    next pass, while a connector could not be recorded, a lane's queued
    counts could not all be read, a connector holding a lane is still
    settling, or a move or re-send failed. Progress is kept per connector,
    so records already re-sent are not sent again.
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
        on_lane.setdefault(entry.lane, []).append(connector_id)
        # A connector the fix-up already moved off a lane is done with it (its
        # records are re-sent below if that stopped short); one that moved on
        # its own may still have events there.
        if entry.prev_lane is not None and connector_id not in progress:
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

    # Every lane's keeper first, from real counts.
    waiting: dict[str, int] = {}
    keeper_of: dict[int, str] = {}
    for lane, connectors in sorted(on_lane.items()):
        if len(connectors) < 2:
            continue
        for connector_id in connectors:
            if connector_id not in waiting:
                count = await _queued(graph_provider, apps[connector_id], connector_id, logger)
                if count is not None:
                    waiting[connector_id] = count
        if any(c not in waiting for c in connectors):
            # An unknown backlog must not read as an empty one: the connector
            # that holds the lane's backlog is the one most likely to time out.
            finished = False
            continue
        keeper_of[lane] = min(
            connectors,
            key=lambda c, lane=lane: (-waiting[c], entries[c].lane != lane, c),
        )

    # Then each connector that has to leave a lane, largest queue first, so a
    # free lane goes to the one with the most waiting. Per connector the lane
    # it is on is decided first, and its records are re-sent only to the
    # lane it ends up on.
    leaves: dict[str, set[int]] = {}
    for lane, keeper in keeper_of.items():
        for connector_id in on_lane[lane]:
            if connector_id != keeper:
                leaves.setdefault(connector_id, set()).add(lane)

    # A lane is taken while a large connector is on it, is still settling off
    # it, or a waiting connector holds it for a later pass.
    taken = {e.lane for e in large.values()} | {
        e.prev_lane for e in large.values() if e.prev_lane is not None
    }

    def free_lanes() -> list[int]:
        return [lane for lane in range(assignments.lane_count) if lane not in taken]

    for connector_id in sorted(leaves, key=lambda c: (-waiting[c], c)):
        entry = entries[connector_id]
        left_one = any(lane != entry.lane for lane in leaves[connector_id])
        if entry.lane in leaves[connector_id] and free_lanes():
            if entry.prev_lane is not None:
                # Still settling an earlier move, so it cannot move yet: it
                # holds a free lane until it can, and nothing is re-sent to a
                # lane it is about to leave.
                taken.add(free_lanes()[0])
                finished = False
                continue
            try:
                placed = await assignments.move(
                    connector_id,
                    LaneRequestReason.UPGRADE,
                    org_id=apps[connector_id].org_id,
                    still_held=tuple(taken),
                )
            except Exception as e:
                finished = False
                logger.info(
                    "Queue lanes: connector %s has to leave lane %d and will be "
                    "moved on a later pass: %s: %s",
                    connector_id,
                    entry.lane,
                    type(e).__name__,
                    e,
                )
                continue
            if placed.lane != entry.lane:
                left_lane, entry = entry.lane, placed
                entries[connector_id] = large[connector_id] = entry
                taken.add(entry.lane)
                moved += 1
                await assignments.note_fix_up_progress(connector_id, "moved")
                leaves[connector_id].add(left_lane)
                left_one = True
        # Its queued records wait on a lane it no longer holds: they follow
        # it to the lane it is on, which is where its new events already go.
        # A connector staying where its records already are has nothing to send.
        if not left_one:
            continue
        sent = await rescue(connector_id, entry)
        rescued += sent
        logger.info(
            "Queue lanes: connector %s (%s) is on lane %d; %d of its %d queued "
            "record(s) re-sent there from lane(s) %s",
            apps[connector_id].name or connector_id,
            connector_id,
            entry.lane,
            sent,
            waiting[connector_id],
            ", ".join(str(lane) for lane in sorted(leaves[connector_id] - {entry.lane})),
        )
    return moved, rescued, finished


async def _org_of(
    graph_provider: IGraphDBProvider, app: _App, connector_id: str
) -> str | None:
    """The connector's org: knowledge bases carry ``orgId``; connectors created
    before it was stored on the document have only the org-app edge."""
    return app.org_id or await org_id_from_app_edge(graph_provider, connector_id)


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

    The records are read in one query, the oldest ``cap`` of them. Paging
    through them instead would race the consumer, which takes records out of
    QUEUED as it goes: with offsets a record can slide into a page already
    read and be skipped, and no number of further walks can prove one was not.
    One query is one snapshot. Records put in line after the re-send began are
    left alone, their events are on the new lane already. Returns how many
    were sent and whether every one of them was.
    """
    started_ms = get_epoch_timestamp_in_ms()
    try:
        records = await graph_provider.get_documents_paginated(
            CollectionNames.RECORDS.value,
            skip=0,
            limit=cap,
            filters={
                "connectorId": connector_id,
                "indexingStatus": ProgressStatus.QUEUED.value,
            },
            sort_field="queuedAtTimestamp",
            raise_on_error=True,
        )
    except Exception as e:
        logger.warning(
            "Queue lanes: could not read the queued records of connector %s to "
            "re-send them: %s: %s",
            connector_id,
            type(e).__name__,
            e,
        )
        return 0, False
    sent = 0
    for record in records:
        record_key = str(record.get("_key") or record.get("id") or "")
        if not record_key or is_parked_duplicate(record):
            continue
        try:
            if float(record.get("queuedAtTimestamp")) > started_ms:  # type: ignore[arg-type]
                continue
        except (TypeError, ValueError):
            pass
        event_type, payload = record_event(record, record_key=record_key, connector_id=connector_id)
        send = producer.send_event(
            topic=stream, event_type=event_type, payload=payload, key=record_key
        )
        try:
            await (run(send) if run is not None else send)
        except Exception as e:
            logger.warning(
                "Queue lanes: stopped re-sending the queued records of connector %s "
                "after %d: %s: %s",
                connector_id,
                sent,
                type(e).__name__,
                e,
            )
            return sent, False
        sent += 1
    return sent, True


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
