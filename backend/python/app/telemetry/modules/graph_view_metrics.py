"""Graph view requests: outcomes, duration and how much each returned.

Counts and timings only, with no org, repo, user or node in a label or a log
line, like ``code_graph_metrics``.
"""

from __future__ import annotations

from app.telemetry.backend import METRICS_BACKEND

REQUESTS = METRICS_BACKEND.counter(
    "pipeshub_graph_view_requests_total",
    "Graph view requests by operation (expand, revalidate) and outcome: "
    "ok, bad_request, not_found, refused (a scope or knowledge-limit refusal) or unavailable (a failed permission check or store read)",
    ["operation", "outcome"],
)

DURATION = METRICS_BACKEND.histogram(
    "pipeshub_graph_view_duration_seconds",
    "Wall-clock time of a graph view request, whatever its outcome",
    ["operation"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

ITEMS = METRICS_BACKEND.counter(
    "pipeshub_graph_view_items_total",
    "Nodes, edges and revalidated ids returned",
    ["operation", "kind"],
)


def record(operation: str, outcome: str, seconds: float, **items: int) -> None:
    REQUESTS.inc(operation, outcome)
    DURATION.observe(operation, value=seconds)
    for kind, count in items.items():
        if count:
            ITEMS.inc(operation, kind, value=count)
