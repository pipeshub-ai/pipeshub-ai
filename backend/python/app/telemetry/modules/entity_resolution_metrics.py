"""Counters for taxonomy entity resolution in the indexing pipeline.

Kept low-cardinality on purpose: outcomes and reasons only, never org or
entity names.
"""

from __future__ import annotations

from app.telemetry.backend import METRICS_BACKEND

NAMES = METRICS_BACKEND.counter(
    "pipeshub_entity_resolution_names_total",
    "Extracted taxonomy names by how they were resolved",
    ["kind", "outcome"],
)

MODEL_CALLS = METRICS_BACKEND.counter(
    "pipeshub_entity_resolution_model_calls_total",
    "Merge-decision model calls by result",
    ["result"],
)

FALLBACKS = METRICS_BACKEND.counter(
    "pipeshub_entity_resolution_fallbacks_total",
    "Names that fell back to a safe default, by reason",
    ["reason"],
)

LATENCY = METRICS_BACKEND.histogram(
    "pipeshub_entity_resolution_seconds",
    "Wall-clock time of one record's resolution",
    ["mode"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)


def record_name_outcome(kind: str, outcome: str, count: int = 1) -> None:
    if count > 0:
        NAMES.inc(kind, outcome, value=float(count))


def record_model_call(result: str) -> None:
    MODEL_CALLS.inc(result)


def record_fallback(reason: str, count: int = 1) -> None:
    if count > 0:
        FALLBACKS.inc(reason, value=float(count))


def record_latency(mode: str, seconds: float) -> None:
    LATENCY.observe(mode, value=seconds)


__all__ = [
    "record_fallback",
    "record_latency",
    "record_model_call",
    "record_name_outcome",
]
