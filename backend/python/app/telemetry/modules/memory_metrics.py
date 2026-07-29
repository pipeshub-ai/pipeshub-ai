"""Process memory metrics, refreshed periodically by `MemoryMonitor`
(see `app/utils/memory_monitor.py`) and, per-request, by
`track_request_memory`.
"""

from app.telemetry.backend import METRICS_BACKEND

# Current-state gauges (not counters): the last observed RSS/VMS sample,
# not a cumulative total.
PROCESS_MEMORY_BYTES = METRICS_BACKEND.gauge(
    "pipeshub_process_memory_bytes",
    "Resident/virtual process memory in bytes, sampled periodically",
    ["service", "kind"],
)

# Wide buckets: a single chat/agent request is expected to add low tens of
# MB; anything above ~500 MB indicates a leak or an unbounded accumulator.
REQUEST_MEMORY_DELTA_MB = METRICS_BACKEND.histogram(
    "pipeshub_request_memory_delta_mb",
    "RSS delta (MB) across a single tracked request/operation",
    ["service", "label"],
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500),
)


def set_process_memory(service: str, rss_mb: float, vms_mb: float) -> None:
    PROCESS_MEMORY_BYTES.set(service, "rss", value=rss_mb * 1024 * 1024)
    PROCESS_MEMORY_BYTES.set(service, "vms", value=vms_mb * 1024 * 1024)


def observe_request_memory_delta(service: str, label: str, delta_mb: float) -> None:
    # Histograms need non-negative observations; a negative delta (GC ran
    # mid-request) is still useful signal but not a valid bucket value.
    REQUEST_MEMORY_DELTA_MB.observe(service, label, value=max(delta_mb, 0.0))
