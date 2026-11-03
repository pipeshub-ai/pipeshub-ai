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
