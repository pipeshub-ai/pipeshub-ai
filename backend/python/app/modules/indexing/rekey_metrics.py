"""Counters of the move of a record's stored points to content-derived block ids (``VectorStore._move_points_to_new_ids``)."""
from app.telemetry.backend import METRICS_BACKEND

REKEYED = METRICS_BACKEND.counter(
    "pipeshub_vector_rekeyed_blocks_total",
    "Blocks whose stored points were moved to a content-derived id with their stored vectors, not embedded again", ["store"],
)
REKEY_FALLBACK = METRICS_BACKEND.counter(
    "pipeshub_vector_rekey_fallback_total",
    "Blocks embedded again because their stored points could not be moved to a content-derived id", ["reason"],
)
