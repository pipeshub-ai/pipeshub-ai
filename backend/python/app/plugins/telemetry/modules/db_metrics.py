"""Storage-port latency: ``pipeshub_db_query_seconds{store,op}``.

Observed from the ``db.query.completed`` platform event, which each store's
factory arranges (``app.utils.store_timing``). ``op`` is the port method name, a
set fixed by the interface, so cardinality is bounded by code.
"""

from app.plugins.telemetry.backend import METRICS_BACKEND

DB_QUERY_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_db_query_seconds",
    "Latency of storage port calls (graph, vector, kv) in seconds",
    ["store", "op"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
