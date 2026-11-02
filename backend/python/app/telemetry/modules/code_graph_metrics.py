"""Code-graph edge builds: outcomes, duration, what they resolved, the backlog.

No per-org or per-repo labels, like ``scheduling_metrics``: the label set stays
bounded however many repos an install indexes. Which repo is affected is in the
build logs and on GET /repos.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.modules.code_graph.edge_builder import EdgeBuildResult

BUILDS = METRICS_BACKEND.counter(
    "pipeshub_code_graph_builds_total",
    "Edge builds by how they settled: ready, waiting_on_indexing, too_large, error "
    "(retried with backoff, or failed for good) or lease_lost",
    ["outcome"],
)

BUILD_DURATION = METRICS_BACKEND.histogram(
    "pipeshub_code_graph_build_duration_seconds",
    "Wall-clock time of an edge build that ran to the end",
    ["mode"],
    buckets=(0.5, 2.0, 10.0, 30.0, 60.0, 300.0, 900.0, 1800.0, 3600.0),
)

EDGES_WRITTEN = METRICS_BACKEND.counter(
    "pipeshub_code_graph_edges_written_total",
    "Edges written by edge builds, by relation",
    ["relation"],
)

REFERENCES = METRICS_BACKEND.counter(
    "pipeshub_code_graph_references_total",
    "References a build could not bind: external (stdlib, packages), unresolved, "
    "or ambiguous and skipped rather than guessed",
    ["result"],
)

EVENTS = METRICS_BACKEND.counter(
    "pipeshub_code_graph_events_total",
    "files_without_facts: files a build kept old edges for (stored facts unreadable); "
    "busy_request: requests left to a build already running; "
    "reconciler_start: owed builds the reconciler started",
    ["event"],
)

# A backlog that only grows means builds are failing or never started.
OWED_BUILDS = METRICS_BACKEND.gauge(
    "pipeshub_code_graph_owed_builds",
    "Repos owed a build at the last reconciler sweep, due now or backing off",
    ["state"],
)


def record_build(outcome: str, result: EdgeBuildResult | None = None,
                 mode: str | None = None) -> None:
    BUILDS.inc(outcome)
    if result is None:
        return
    if mode:
        BUILD_DURATION.observe(mode, value=result.duration_s)
    for relation, count in result.edges_by_type.items():
        EDGES_WRITTEN.inc(relation, value=count)
    for name, count in (("external", result.external_refs), ("unresolved", result.unresolved),
                        ("ambiguous_skipped", result.ambiguous_skipped)):
        if count:
            REFERENCES.inc(name, value=count)
    if result.facts_missing:
        EVENTS.inc("files_without_facts", value=result.facts_missing)


def record_busy_request() -> None:
    EVENTS.inc("busy_request")


def record_reconcile_sweep(due: int, waiting: int, started: int) -> None:
    OWED_BUILDS.set("due", value=due)
    OWED_BUILDS.set("waiting", value=waiting)
    if started:
        EVENTS.inc("reconciler_start", value=started)
