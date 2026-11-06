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
SCOPE_PREFILTER_CAPPED = METRICS_BACKEND.counter(
    "pipeshub_scope_prefilter_capped_total",
    "Scoped searches that asked for the most candidates a store allows and still found fewer valid points than the limit (the candidates the query "
    "let through were refused by the exact rule), by store",
    ["store"],
)
SHADOW_COMPARED = METRICS_BACKEND.counter(
    "pipeshub_acl_shadow_total",
    "Records compared by the shadow scope filter, by outcome: agree (the filtered search and the post-check both admit), over_admit (the filtered "
    "search returned a record the post-check refuses: an alert), recall_loss (the post-check admits a record the filtered search did not return: "
    "expected for groups without a usable stamp)",
    ["outcome"],
)
SHADOW_SKIPPED = METRICS_BACKEND.counter(
    "pipeshub_acl_shadow_skipped_total",
    "Searches the shadow filter did not compare, by reason (sampled_out, busy, scopes_unavailable, no_current_connector, unsupported, too_large, "
    "timeout, error)",
    ["reason"],
)
SHADOW_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_acl_shadow_seconds",
    "Time the shadow comparison of one search took (it runs after the answer is built), by whether it compared (done) or gave up (skipped)",
    ["result"],
    (0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
