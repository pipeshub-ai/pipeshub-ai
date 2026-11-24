"""Stored paid results: cache lookups, reuse bumps, orphan checks, purges and re-purge windows."""

from app.plugins.telemetry.backend import METRICS_BACKEND

BUMP_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_paid_bump_total", "Reuse bumps of stored paid results by outcome", ["outcome"],
)

PAID_CACHE_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_paid_cache_total",
    "Paid-result cache lookups and stores by kind and outcome",
    ["kind", "outcome"],
)

ORPHAN_CHECKS = METRICS_BACKEND.counter(
    "pipeshub_paid_orphan_check_total",
    "Existence checks of paid work, by what decided and the answer",
    ["source", "result"],
)

ORPHAN_GRAPH_READS = METRICS_BACKEND.histogram(
    "pipeshub_paid_orphan_graph_reads_per_delivery",
    "Graph reads one delivery's existence checks made",
    ["kind"],
    (0, 1, 2, 3, 5, 10, 25, 60, 180),
)

ORPHAN_REPARSES = METRICS_BACKEND.counter(
    "pipeshub_paid_orphan_reparse_total",
    "Parses repeated because the parsing service refused with 410 and the record proved live",
    ["kind"],
)

ORPHAN_STOPPED = METRICS_BACKEND.counter(
    "pipeshub_paid_orphan_stopped_total", "Paid calls and stores refused for a deleted record", ["site"],
)

REPURGE_MESSAGES = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_messages_total", "Re-purge messages published, by kind", ["kind"],
)

REPURGE_FALLBACK = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_fallback_total",
    "Deleted records whose later hops went per record instead of into a window", ["reason"],
)

REPURGE_WINDOW_LOST = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_window_lost_total", "Windows whose ids were gone or replaced when their seal ran", ["reason"],
)

REPURGE_SEAL_REPAIRED = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_seal_repaired_total", "Overdue seals published again", ["kind"],
)

REPURGE_INVALID_IDS = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_invalid_ids_total", "Ids a window or page dropped because no result can be stored under them", ["where"],
)

REPURGE_FAILED_IDS = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_failed_ids_total", "Ids whose purge still failed after the tail attempts", ["kind"],
)

REPURGE_WINDOW_IDS = METRICS_BACKEND.histogram(
    "pipeshub_paid_repurge_window_ids", "Ids per sealed window", ["kind"],
    (1, 5, 25, 100, 500, 2_000, 10_000, 50_000, 250_000),
)

REPURGE_PAGES = METRICS_BACKEND.counter("pipeshub_paid_repurge_pages_total", "Pages published by seals", ["kind"])

SWEEP_CHANGED = METRICS_BACKEND.counter(
    "pipeshub_paid_sweep_changed_total",
    "Objects a retention sweep left in place because they changed after it listed them",
    [],
)

STORE_PURGE_REQUESTS = METRICS_BACKEND.counter(
    "pipeshub_paid_purge_storage_requests_total",
    "Storage-service requests made to purge paid-result folders, by route (batch carries up to 100 records, record one)",
    ["route"],
)

STORE_PURGE_RECORDS = METRICS_BACKEND.counter(
    "pipeshub_paid_purge_records_total",
    "Paid-result folders purged, by the route that purged them and the outcome",
    ["route", "outcome"],
)
