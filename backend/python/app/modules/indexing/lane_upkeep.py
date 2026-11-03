"""Once-a-minute upkeep of the Redis Streams lane map.

Runs in the indexing service, inside the stale-record recovery pass, so under
the cluster-wide ``recovery`` lock and on one replica at a time. Each pass:

1. Settles moves and deletes (a deleted entry stays in the map, so a late
   event keeps its lane), rebuilds the per-lane counts and writes the busy
   flags, in one atomic script (``LaneAssignments.upkeep``), from the same
   backlog read the stranded-record sweep uses.
2. Reads the connectors and knowledge bases from the graph: corrects a class
   that was guessed on a first publish, and frees the lane of any connector
   that no longer exists (in case a delete path missed it).
3. Publishes the lane metrics and keeps a report for ``GET /health``.

There is no one-time step for an install upgraded from hashing: a connector
is placed by the rule on its first publish after the switch, and the events
it had already queued finish on the lane they are on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.services.messaging.config import messaging_env
from app.services.messaging.lanes.assignment_policy import is_large_class
from app.services.messaging.lanes.hash_router import RedisLaneRouter
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY
from app.services.messaging.lanes.lifecycle import connector_class_of
from app.telemetry.modules import scheduling_metrics as metrics

if TYPE_CHECKING:
    from collections.abc import Mapping
    from logging import Logger

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.lanes.assignment import LaneAssignments, LaneEntry
    from app.services.messaging.lanes.backlog import LaneBacklog

__all__ = [
    "LaneReport",
    "last_lane_report",
    "run_lane_upkeep",
]

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
    backlog_read: bool
    lanes: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "updatedAt": self.updated_at_ms,
            "laneCount": self.lane_count,
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
    backlog: LaneBacklog | None,
    logger: Logger,
) -> LaneReport:
    """One upkeep pass. ``backlog`` is None when the broker could not be read."""
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
    released = corrected = 0
    if apps is not None:
        released, corrected = await _reconcile_with_graph(
            assignments, graph_provider, entries, apps, logger
        )

    if settled.fenced or settled.cleared or released or corrected:
        logger.info(
            "Queue lanes: %d move(s) or delete(s) fenced, %d old lane(s) let go, "
            "%d lane(s) freed for connectors that no longer exist, %d class(es) "
            "corrected",
            settled.fenced,
            settled.cleared,
            released,
            corrected,
        )

    report = _report(
        topic,
        await assignments.read_map(),
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


def _report(
    topic: str,
    entries: Mapping[str, LaneEntry],
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
        # Deleted entries stay in the map for good, so only live ones are listed.
        live = [(c, e) for c, e in here if e.is_live]
        live.sort(key=lambda item: (not is_large_class(item[1].connector_class), item[0]))
        oldest = backlog.oldest_waiting_ms.get(stream) if backlog is not None else None
        lanes.append(
            {
                "lane": lane,
                "stream": stream,
                "large": sum(is_large_class(e.connector_class) for _, e in live),
                "small": sum(not is_large_class(e.connector_class) for _, e in live),
                "deleted": len(here) - len(live),
                "connectors": [
                    {
                        "id": connector_id,
                        "name": apps[connector_id].name if connector_id in apps else None,
                        "class": entry.connector_class,
                        "state": entry.state,
                    }
                    for connector_id, entry in live[:_LISTED_PER_LANE]
                ],
                "connectorsNotListed": max(0, len(live) - _LISTED_PER_LANE),
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
    return LaneReport(
        topic=topic,
        updated_at_ms=now_ms,
        lane_count=lane_count,
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
