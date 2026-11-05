"""Counters of the permission-scope layer."""

from __future__ import annotations

from app.telemetry.backend import METRICS_BACKEND

SCOPES_UNAVAILABLE = METRICS_BACKEND.counter(
    "pipeshub_viewer_scopes_unavailable_total",
    "Searches that fell back to the legacy permission filter because viewer scopes could not be computed",
    ["reason"],
)
SCOPE_CACHE = METRICS_BACKEND.counter(
    "pipeshub_viewer_scope_cache_total",
    "Viewer-scope cache lookups by result (hit, miss, bypass when the version is unreadable)",
    ["result"],
)
SCOPE_REFUSED = METRICS_BACKEND.counter(
    "pipeshub_scope_fields_refused_total",
    "Records the scope-field stamper refused (they keep the legacy filter), by reason",
    ["reason"],
)
SCOPE_STAMP_FAILED = METRICS_BACKEND.counter(
    "pipeshub_scope_fields_failed_total",
    "Scope fields that could not be computed (the point is left without them and keeps the legacy filter), by where",
    ["where"],
)
SCOPE_PREFILTER_DROPPED = METRICS_BACKEND.counter(
    "pipeshub_scope_prefilter_dropped_total",
    "Points a store's scope pre-filter returned that the exact rule refuses (Redis compares tags without case and reads the connectors it excludes "
    "just before searching), dropped before the answer is returned, by store",
    ["store"],
)
