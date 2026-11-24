"""The PostgreSQL knowledge-graph projection (kg): backfill, bootstrap, drain, flush, restamp, the net and its reads."""

from app.plugins.telemetry.backend import METRICS_BACKEND

BACKFILL_BATCHES = METRICS_BACKEND.counter("pipeshub_kg_backfill_batches_total", "Backfill batches committed", ["scope"])

BACKFILL_FLIPS = METRICS_BACKEND.counter("pipeshub_kg_backfill_flips_total", "Backfills that ended in a verify, by outcome", ["outcome"])

BOOTSTRAP_PROMOTED = METRICS_BACKEND.counter("pipeshub_kg_bootstrap_total", "Connectors the worker took to enforce, or left on the walk, by outcome", ["outcome"])

COUNTS_CHECKED = METRICS_BACKEND.counter("pipeshub_kg_counts_checked_total", "Checks of a connector's counters against a recount of its rows, by what they found (same; differs: the "
                                  "totals read from them are wrong; unseeded: not counted whole yet, nothing compared; error: the check did not run)", ["result"])

DRAINER_DRAINED = METRICS_BACKEND.counter("pipeshub_kg_drained_rows_total", "Change-log rows projected by the drainer", ["where"])

DRAINER_LAG = METRICS_BACKEND.counter("pipeshub_kg_drain_failed_total", "Drainer passes that failed and will be retried", ["where"])

DRAINER_RETRY_LATER = METRICS_BACKEND.counter("pipeshub_kg_drain_retry_later_total", "Drainer passes whose projection gave up reaching connectors it had not locked (nothing written, retried)", ["where"])

FLUSH_FLUSH_FAILED = METRICS_BACKEND.counter("pipeshub_kg_flush_failed_total", "Projections in a writer's transaction that failed and left the change log rows", ["where"])

FLUSH_FLUSHED = METRICS_BACKEND.counter("pipeshub_kg_flushed_nodes_total", "Nodes projected by a writer's own transaction", ["where"])

GUARD_UNCLASSIFIED = METRICS_BACKEND.counter("pipeshub_kg_unclassified_graph_writes_total", "Graph writes by a provider method the kg registry does not list", ["method"])

HIERARCHY = METRICS_BACKEND.counter("pipeshub_kg_hierarchy_total", "Descendant and level reads, by who answered and why the walk did", ["what", "answer"])

LEGACY_LEGACY = METRICS_BACKEND.gauge("pipeshub_kg_legacy_records", "Records of a connector that keep today's walk, by the compiler's reason", ["org", "app", "reason"])

LEGACY_FENCE_AGE = METRICS_BACKEND.gauge("pipeshub_kg_fence_oldest_seconds", "Age of the oldest queued restamp job (a fence hides until it is done)", ["org"])

LEGACY_QUEUE = METRICS_BACKEND.gauge("pipeshub_kg_restamp_queue", "Queued restamp jobs, by whether a worker holds them", ["org", "state"])

LEGACY_QUARANTINED = METRICS_BACKEND.gauge("pipeshub_kg_quarantined_rows", "Change-log rows the drain cannot read (alert on > 0)", ["org"])

LEGACY_LAG = METRICS_BACKEND.gauge("pipeshub_kg_change_log_rows", "Committed change-log rows not yet projected", ["org"])

NET_NET = METRICS_BACKEND.counter("pipeshub_kg_net_total", "Checks of an enforced connector against the walk, by where they ran and what they found (over: kg admitted what the walk denies; under: kg denied what the walk admits; same; skipped; busy: a sample found the process at its limit; error)", ["source", "result"])

NET_LAST_VERIFIED = METRICS_BACKEND.gauge("pipeshub_kg_net_last_verified_seconds", "Unix time a connector's last complete verify against the graph finished (alert when it is older than a few verify intervals)", ["org", "connector"])

NET_OVERDUE = METRICS_BACKEND.gauge("pipeshub_kg_net_verify_overdue", "Enforced connectors whose full verify has not completed within 24 verify intervals", ["org"])

NET_OLDER = METRICS_BACKEND.gauge("pipeshub_kg_net_older_epoch", "Enforced connectors whose copy is of an older projection code epoch: not served or verified until the bootstrap brings them up to date (alert when it stays above 0)", ["org"])

NET_DEMOTED = METRICS_BACKEND.counter("pipeshub_kg_net_demoted_total", "Connectors taken back to the walk because kg differs from it", ["source"])

PROJECT_REFUSED = METRICS_BACKEND.counter("pipeshub_kg_refused_rows_total", "Rows with a value a kg writer refuses (a node written without it, a grant left out), and their connector taken off the readers", ["coll"])

READ_ANSWERS = METRICS_BACKEND.counter("pipeshub_kg_read_nodes_total", "Nodes an access check decided, by who answered", ["answer"])

READ_FALLBACKS = METRICS_BACKEND.counter("pipeshub_kg_read_fallback_total", "Nodes (or whole calls) that kg handed to the walk, by reason", ["reason"])

RESTAMP_JOBS = METRICS_BACKEND.counter("pipeshub_kg_restamp_jobs_total", "Restamp jobs queued", ["kind"])

RESTAMP_BATCHES = METRICS_BACKEND.counter("pipeshub_kg_restamp_batches_total", "Restamp batches committed", ["kind"])

RESTAMP_FAILED = METRICS_BACKEND.counter("pipeshub_kg_restamp_failed_total", "Restamp batches that failed and will be retried", ["kind"])

WORKER_LOOP_FAILED = METRICS_BACKEND.counter("pipeshub_kg_worker_loop_failed_total", "Iterations of the projection worker's loop that raised and were continued", ["where"])
