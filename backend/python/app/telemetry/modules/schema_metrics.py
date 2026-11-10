"""Graph writes the schema validator refused: a permanent error, never retried."""

from app.telemetry.backend import METRICS_BACKEND

SCHEMA_REJECTIONS = METRICS_BACKEND.counter(
    "pipeshub_graph_schema_rejections_total",
    "Graph writes refused by schema validation, by collection",
    ["collection"],
)


def record_schema_rejection(collection: str) -> None:
    SCHEMA_REJECTIONS.inc(collection)
