"""Process memory (RSS/VMS) telemetry for OOM diagnosis.

Two independent pieces:

  * `MemoryMonitor` — a background daemon thread that samples this
    process's RSS/VMS on a fixed interval, republishes them as gauges
    (`pipeshub_process_memory_bytes`), and logs a warning once RSS crosses
    a configurable threshold. One instance per process; started/stopped
    from the app lifespan (mirrors `GraphMetricsRefresher`'s
    start/stop-in-a-thread shape).

  * `track_request_memory` — a per-request/per-run context manager that
    samples RSS before and after a block of code and logs + records the
    delta (`pipeshub_request_memory_delta_mb`), so a leak can be
    attributed to "which requests/operations grow RSS" instead of only
    "RSS grows over time" from the periodic sample alone.

Both are best-effort: any `psutil` failure (missing dependency, transient
read error, sandboxed environment without `/proc`) only disables the
telemetry, never the request or service itself.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from app.telemetry.modules.memory_metrics import (
    observe_request_memory_delta,
    set_process_memory,
)

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard runtime dependency
    psutil = None  # type: ignore[assignment]

_BYTES_PER_MB = 1024 * 1024


def get_process_memory_mb() -> tuple[float, float] | None:
    """Return `(rss_mb, vms_mb)` for the current process, or `None` if
    unavailable (`psutil` missing, or a transient read failure)."""
    if psutil is None:
        return None
    try:
        info = psutil.Process().memory_info()
        return info.rss / _BYTES_PER_MB, info.vms / _BYTES_PER_MB
    except Exception:
        return None


class MemoryMonitor:
    """Background thread periodically logging/publishing process RSS/VMS."""

    def __init__(
        self,
        logger: logging.Logger,
        service_name: str,
        interval_s: int = 60,
        warn_threshold_mb: float | None = None,
    ) -> None:
        self._logger = logger
        self._service_name = service_name
        self._interval_s = interval_s
        self._warn_threshold_mb = warn_threshold_mb
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if psutil is None:
            self._logger.warning(
                "MemoryMonitor: psutil unavailable, RSS/VMS telemetry disabled"
            )
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="memory-monitor", daemon=True
        )
        self._thread.start()
        self._logger.info(
            "✅ Memory monitor started (interval=%ss, warn_threshold=%s MB)",
            self._interval_s,
            self._warn_threshold_mb,
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)

    def sample_once(self) -> tuple[float, float] | None:
        """Sample RSS/VMS once, publish the gauges, and return the sample.

        Exposed separately from `_run` so `/health` (see Phase 4.2) can
        reuse it for an on-demand reading without waiting for the next
        periodic tick.
        """
        mem = get_process_memory_mb()
        if mem is None:
            return None
        rss_mb, vms_mb = mem
        set_process_memory(self._service_name, rss_mb, vms_mb)
        if self._warn_threshold_mb is not None and rss_mb >= self._warn_threshold_mb:
            self._logger.warning(
                "MemoryMonitor: RSS %.1f MB exceeds warn threshold %.1f MB (VMS=%.1f MB)",
                rss_mb, self._warn_threshold_mb, vms_mb,
            )
        else:
            self._logger.debug("MemoryMonitor: RSS=%.1f MB VMS=%.1f MB", rss_mb, vms_mb)
        return rss_mb, vms_mb

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as e:
                self._logger.warning("MemoryMonitor: sample failed: %s", e)
            self._stop.wait(self._interval_s)


@contextmanager
def track_request_memory(
    logger: logging.Logger,
    service_name: str,
    label: str,
    delta_warn_mb: float = 50.0,
) -> Iterator[None]:
    """Log + record the RSS delta across a request/agent-run.

    Usage::

        with track_request_memory(log, "query_service", "agent_loop_stream"):
            ...

    No-ops (yields straight through) if `psutil` is unavailable.
    """
    start = get_process_memory_mb()
    try:
        yield
    finally:
        if start is not None:
            end = get_process_memory_mb()
            if end is not None:
                delta_rss = end[0] - start[0]
                observe_request_memory_delta(service_name, label, delta_rss)
                log_fn = logger.warning if delta_rss >= delta_warn_mb else logger.debug
                log_fn(
                    "MemoryMonitor[%s]: RSS delta=%.1f MB (before=%.1f after=%.1f)",
                    label, delta_rss, start[0], end[0],
                )
