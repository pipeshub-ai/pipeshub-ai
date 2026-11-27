"""Live widening of a graph collection's schema by a newer image."""

from app.plugins.telemetry.backend import METRICS_BACKEND

REWIDEN_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_record_schema_rewiden_total",
    "Times this process widened a live collection schema; above 0 in steady state means an older image is running",
    ["collection", "trigger"],
)

REWIDEN_CONTENDED_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_record_schema_rewiden_contended_total",
    "Widenings not sent because a stream transaction was still open after the quiet-point wait; the poll tries again",
    ["collection", "trigger"],
)

REWIDEN_WAIT_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_record_schema_rewiden_wait_seconds",
    "Time from sending the schema change to its answer or its client timeout",
    ["collection", "trigger"],
    (0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10),
)
