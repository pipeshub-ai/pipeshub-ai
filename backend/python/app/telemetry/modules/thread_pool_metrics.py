"""Shared connector thread pool metrics, refreshed on a tick (see connectors_main).

``live_threads`` is the number this change exists to bound: it should climb during
the startup sync burst and then plateau at or below ``max_workers``. Growth after
the burst settles means threads are leaking again. ``lease_queued`` above zero is
expected backpressure, not an error.
"""

from app.connectors.core.thread_pool import PoolSnapshot
from app.telemetry.backend import METRICS_BACKEND

CONNECTOR_THREAD_POOL = METRICS_BACKEND.gauge(
    "pipeshub_connector_thread_pool",
    "Shared connector thread pool utilisation",
    ["state"],
)

CONNECTOR_LEASE_INFLIGHT = METRICS_BACKEND.gauge(
    "pipeshub_connector_lease_inflight",
    "Pool threads currently held, by connector type",
    ["connector"],
)


def set_connector_thread_pool(snapshot: PoolSnapshot) -> None:
    """Replace both series with the current pool state."""
    CONNECTOR_THREAD_POOL.set("max_workers", value=snapshot.max_workers)
    CONNECTOR_THREAD_POOL.set("live_threads", value=snapshot.live_threads)
    CONNECTOR_THREAD_POOL.set("dispatched", value=snapshot.dispatched)
    CONNECTOR_THREAD_POOL.set("lease_queued", value=snapshot.lease_queued)
    CONNECTOR_THREAD_POOL.set("pool_queued", value=snapshot.pool_queued)
    CONNECTOR_THREAD_POOL.set("leases", value=snapshot.leases)

    # Labelled by connector type, never connector_id — cardinality must stay bounded.
    CONNECTOR_LEASE_INFLIGHT.clear()
    for connector, count in snapshot.per_type_inflight.items():
        CONNECTOR_LEASE_INFLIGHT.set(connector or "unknown", value=count)
