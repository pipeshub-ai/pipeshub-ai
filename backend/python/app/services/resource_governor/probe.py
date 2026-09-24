"""Cross-platform, no-root resource probing (plan section 5).

Every read here is best-effort: a missing file, permission error, or
malformed value degrades a single field to ``None`` rather than raising.
``SystemResourceProbe.snapshot()`` additionally wraps the whole read in a
blanket try/except so the ResourceGovernor's sample loop can never be
brought down by a probe failure (plan corner case: "Any probe read raises").

Resolution order, cheapest/most-precise first:

- Memory:      cgroup v2 -> cgroup v1 -> /proc/meminfo -> psutil (if
               importable) -> platform sysconf/ctypes total-only.
- CPU quota:   cgroup v2 cpu.max -> cgroup v1 cfs_quota/period ->
               sched_getaffinity -> os.cpu_count().
- CPU usage:   cgroup v2 cpu.stat usage_usec -> cgroup v1 cpuacct.usage ->
               /proc/stat -> os.times() (portable) -> psutil.cpu_times().

CPU *utilisation* is derived from the delta between two cumulative-counter
reads divided by wall-clock elapsed time (plan section 4.3) — never from
``psutil.cpu_percent()`` or ``getloadavg()``, both of which alias short
bursts and under-report exactly the small-record (Jira/Confluence) workload
this system must scale up for.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.services.resource_governor.models import ResourceSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - environment dependent, no hard dependency
    psutil = None  # type: ignore[assignment]

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_PROC_SELF_CGROUP = Path("/proc/self/cgroup")
_PROC_MEMINFO = Path("/proc/meminfo")
_PROC_STAT = Path("/proc/stat")
# Some kernels report this sentinel for memory.limit_in_bytes / memsw limits
# under cgroup v1 to mean "no limit" (2^63 - 4096, platform page aligned).
_MEM_V1_NO_LIMIT_SENTINEL = 1 << 62

# Baseline memory calibration (plan: "Fix 1 — Baseline Memory Reservation").
_BASELINE_MEMORY_ENV_VAR = "GOVERNOR_BASELINE_MEMORY_MB"
_BASELINE_CALIBRATION_SAMPLES = 3
# Most of the cgroup limit the baseline may claim. ``mem_pressure`` nets the
# baseline out of both sides of its ratio, so any value still reads 1.0 on a
# full cgroup; this cap is about keeping the remaining denominator wide
# enough that one document's working set can't swing the reading from idle
# to critical and set the controller oscillating.
_MAX_BASELINE_LIMIT_FRACTION = 0.5


class ResourceProbe(Protocol):
    def snapshot(self) -> ResourceSnapshot: ...


# ---------------------------------------------------------------------------
# Low-level file helpers — never raise.
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _resolve_cgroup_path(v1_controller: str, v1_filename: str, v2_filename: str | None = None) -> Path | None:
    """Locate a cgroup file under either version, including a host-mounted
    (non-namespaced) cgroupfs — see corner case: "Nested/host-mounted
    cgroupfs".

    Tries, in order: the v2 unified path directly under the cgroup root, the
    v1 controller path directly under the root (both true in the common
    Docker/Kubernetes case where the container's own cgroup is mounted at
    ``/sys/fs/cgroup``), then resolves this process's own relative path via
    ``/proc/self/cgroup`` and joins it — needed when the host's full
    cgroupfs is bind-mounted instead.
    """
    v2_name = v2_filename or v1_filename
    direct_v2 = _CGROUP_ROOT / v2_name
    if direct_v2.exists():
        return direct_v2
    direct_v1 = _CGROUP_ROOT / v1_controller / v1_filename
    if direct_v1.exists():
        return direct_v1

    cgroup_self = _read_text(_PROC_SELF_CGROUP)
    if not cgroup_self:
        return None
    for line in cgroup_self.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        _hier_id, controllers, rel_path = parts
        rel = rel_path.lstrip("/")
        if controllers == "":  # cgroup v2 line: "0::/path"
            candidate = _CGROUP_ROOT / rel / v2_name
            if candidate.exists():
                return candidate
        elif v1_controller in controllers.split(","):
            candidate = _CGROUP_ROOT / v1_controller / rel / v1_filename
            if candidate.exists():
                return candidate
    return None


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def _memory_stat_value(stat_text: str | None, key: str) -> int:
    """One counter out of ``memory.stat``, or 0 when it cannot be read."""
    if not stat_text:
        return 0
    for line in stat_text.splitlines():
        if line.startswith(key + " "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


def _cgroup_v2_memory() -> tuple[int | None, int | None]:
    """Returns ``(None, None)`` — not a partial result — when no limit is
    set, so the caller falls through the *whole* chain to a source that can
    supply a limit and working set together (corner case: ``memory.max ==
    "max"`` -> treated as no limit, falls through to host total).
    """
    max_path = _resolve_cgroup_path("memory", "memory.max")
    if max_path is None:
        return None, None
    raw_max = _read_text(max_path)
    if raw_max is None or raw_max == "max":
        return None, None
    try:
        limit = int(raw_max)
    except ValueError:
        return None, None

    current_path = _resolve_cgroup_path("memory", "memory.current")
    current = _read_int(current_path) if current_path else None

    stat_path = _resolve_cgroup_path("memory", "memory.stat")
    # All page cache, not just the inactive half. The k8s "working set"
    # convention subtracts only ``inactive_file`` because it is predicting
    # OOM-kill risk and wants to be conservative. This governor is answering a
    # different question — may another document be admitted — and braking on
    # memory the kernel frees on demand is what pins every pool at its floor
    # on a container whose cache is naturally hot (document blobs in, vectors
    # out). Observed on a 10 GiB container: 7.8 GiB unreclaimable read as
    # 9.4 GiB, tripping MEM_HARD with 2 GiB genuinely free, while
    # ``memory.events`` recorded 1431 successful reclaims and zero OOM kills.
    # ``_proc_meminfo_memory`` below already discounts reclaimable cache via
    # MemAvailable; this makes the cgroup paths agree with it.
    file_cache = _memory_stat_value(
        _read_text(stat_path) if stat_path else None, "file"
    )

    working_set = None if current is None else max(0, current - file_cache)
    return limit, working_set


def _cgroup_v1_memory() -> tuple[int | None, int | None]:
    """Same "all or nothing" contract as :func:`_cgroup_v2_memory` — see its
    docstring."""
    limit_path = _resolve_cgroup_path("memory", "memory.limit_in_bytes")
    limit = _read_int(limit_path) if limit_path else None
    if limit is None or limit >= _MEM_V1_NO_LIMIT_SENTINEL:
        return None, None

    usage_path = _resolve_cgroup_path("memory", "memory.usage_in_bytes")
    usage = _read_int(usage_path) if usage_path else None

    stat_path = _resolve_cgroup_path("memory", "memory.stat")
    # ``total_cache`` is v1's spelling of all reclaimable page cache — see the
    # v2 path for why the whole of it is discounted, not just its inactive half.
    file_cache = _memory_stat_value(
        _read_text(stat_path) if stat_path else None, "total_cache",
    )

    working_set = None if usage is None else max(0, usage - file_cache)
    return limit, working_set


def _proc_meminfo_memory() -> tuple[int | None, int | None]:
    text = _read_text(_PROC_MEMINFO)
    if not text:
        return None, None
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None:
        return None, None
    working_set = None if available is None else max(0, total - available)
    return total, working_set


def _sysconf_memory_total() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    if pages < 0 or page_size < 0:
        return None
    return pages * page_size


def _psutil_memory() -> tuple[int | None, int | None]:
    if psutil is None:
        return None, None
    try:
        vm = psutil.virtual_memory()
        return int(vm.total), int(vm.total - vm.available)
    except Exception:
        return None, None


def _windows_memory() -> tuple[int | None, int | None]:
    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return None, None
        total = int(stat.ullTotalPhys)
        working_set = total - int(stat.ullAvailPhys)
        return total, working_set
    except Exception:
        return None, None


def _resolve_memory() -> tuple[int | None, int | None, str]:
    if sys.platform.startswith("linux"):
        limit, working_set = _cgroup_v2_memory()
        if limit is not None:
            return limit, working_set, "cgroup_v2"
        limit, working_set = _cgroup_v1_memory()
        if limit is not None:
            return limit, working_set, "cgroup_v1"
        limit, working_set = _proc_meminfo_memory()
        if limit is not None:
            return limit, working_set, "proc_meminfo"
        limit, working_set = _psutil_memory()
        if limit is not None:
            return limit, working_set, "psutil"
        return None, None, "unknown"

    if sys.platform == "darwin":
        limit, working_set = _psutil_memory()
        if limit is not None:
            return limit, working_set, "psutil"
        total = _sysconf_memory_total()
        if total is not None:
            return total, None, "sysconf"
        return None, None, "unknown"

    if sys.platform.startswith("win"):
        limit, working_set = _windows_memory()
        if limit is not None:
            return limit, working_set, "win32"
        limit, working_set = _psutil_memory()
        if limit is not None:
            return limit, working_set, "psutil"
        return None, None, "unknown"

    limit, working_set = _psutil_memory()
    if limit is not None:
        return limit, working_set, "psutil"
    return None, None, "unknown"


# ---------------------------------------------------------------------------
# Baseline memory — estimate what co-located idle services (e.g. Docling's
# model weights sitting in the same cgroup/container) hold, so MEM_SOFT/
# MEM_HARD react to *workload* memory rather than a fixed baseline the
# container carries at all times (plan: "Fix 1 — Baseline Memory
# Reservation"). ``ResourceSnapshot.mem_pressure`` subtracts this figure
# from the cgroup limit as well as from the working set — netting it out of
# only the working set would put the brake thresholds beyond the largest
# reading the probe can ever produce.
# ---------------------------------------------------------------------------


class BaselineMemoryTracker:
    """Derives how much of the cgroup's working set is not attributable to
    this process's own workload, and subtracts it before ``mem_pressure`` is
    computed.

    Without this, a sibling service's idle memory (Docling's VLM model
    weights, ~3GB RSS even between documents) permanently inflates
    ``mem_pressure`` in a shared-container deployment. Since growth requires
    ``pressure < MEM_SOFT`` (minus optional ``GROW_BAND``), a baseline that
    never fluctuates can pin every pool's limit at its floor forever, even
    with idle CPU and genuinely free RAM.

    Resolution order:

    - ``GOVERNOR_BASELINE_MEMORY_MB`` env var, if set, wins outright — an
      operator who knows the deployment's idle footprint gets an exact,
      stable number instead of a heuristic.
    - Otherwise, auto-calibrate: track the lowest working-set reading seen
      so far. The first ``calibration_samples`` readings are used only to
      seed this low-water mark — no subtraction is applied yet, so early
      samples fall back to raw (pre-fix) behaviour rather than risking a
      wrong baseline from a cold/partial read. After that warm-up, the
      low-water mark is applied and keeps ratcheting down (never up) as
      lower idle readings arrive, so it also self-corrects if the initial
      calibration window overlapped with real workload memory.

    Either way the result is capped at ``_MAX_BASELINE_LIMIT_FRACTION`` of
    the cgroup limit when one is known.

    This is a heuristic, not a measurement of any specific process's RSS —
    it cannot know that the co-located memory belongs to "Docling"
    specifically, only that the cgroup's *minimum observed* working set is a
    lower bound on non-workload memory. An explicit env var override remains
    the precise option for operators who want it.
    """

    def __init__(self, calibration_samples: int = _BASELINE_CALIBRATION_SAMPLES) -> None:
        self._explicit_bytes = self._read_explicit_override()
        self._calibration_samples = max(1, calibration_samples)
        self._samples_seen = 0
        self._low_water_mark: int | None = None

    @staticmethod
    def _read_explicit_override() -> int | None:
        raw = os.getenv(_BASELINE_MEMORY_ENV_VAR)
        if not raw:
            return None
        try:
            mb = float(raw)
        except ValueError:
            return None
        if mb < 0:
            return None
        return int(mb * 1024 * 1024)

    def baseline_bytes(self, working_set: int) -> int | None:
        """Update internal state from *working_set* and return the current
        baseline in bytes, or ``None`` while still calibrating (auto mode)
        or if no reading has been seen yet."""
        if self._explicit_bytes is not None:
            return self._explicit_bytes
        self._low_water_mark = (
            working_set if self._low_water_mark is None else min(self._low_water_mark, working_set)
        )
        self._samples_seen += 1
        if self._samples_seen < self._calibration_samples:
            return None
        return self._low_water_mark

    def adjust(
        self, working_set: int | None, limit_bytes: int | None = None
    ) -> tuple[int | None, int | None]:
        """Returns ``(adjusted_working_set, baseline_used)``. Both are
        ``None`` if *working_set* itself is unknown; ``baseline_used`` is
        ``None`` (no adjustment) while still calibrating.

        *limit_bytes*, when known, caps the baseline at
        ``_MAX_BASELINE_LIMIT_FRACTION`` of the cgroup limit.
        """
        if working_set is None:
            return None, None
        baseline = self.baseline_bytes(working_set)
        if baseline is None:
            return working_set, None
        if limit_bytes is not None and limit_bytes > 0:
            baseline = min(baseline, int(limit_bytes * _MAX_BASELINE_LIMIT_FRACTION))
        return max(0, working_set - baseline), baseline


# ---------------------------------------------------------------------------
# CPU quota (how many cores this process may use)
# ---------------------------------------------------------------------------


def _cgroup_v2_cpu_quota() -> float | None:
    path = _resolve_cgroup_path("cpu", "cpu.max")
    text = _read_text(path) if path else None
    if not text:
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota_us, period_us = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if period_us <= 0:
        return None
    return quota_us / period_us


def _cgroup_v1_cpu_quota() -> float | None:
    quota_path = _resolve_cgroup_path("cpu", "cpu.cfs_quota_us")
    period_path = _resolve_cgroup_path("cpu", "cpu.cfs_period_us")
    quota = _read_int(quota_path) if quota_path else None
    period = _read_int(period_path) if period_path else None
    if quota is None or period is None or quota <= 0 or period <= 0:
        return None
    return quota / period


def _affinity_cpu_count() -> int | None:
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


def _resolve_cpu_quota() -> float:
    quota = _cgroup_v2_cpu_quota()
    if quota is not None:
        return quota
    quota = _cgroup_v1_cpu_quota()
    if quota is not None:
        return quota
    count = _affinity_cpu_count()
    if count:
        return float(count)
    count = os.cpu_count()
    return float(count) if count else 1.0


# ---------------------------------------------------------------------------
# CPU usage counters (cumulative, for delta-based utilisation — section 4.3)
# ---------------------------------------------------------------------------


def _clock_ticks_per_second() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError, AttributeError):
        return 100.0  # USER_HZ is 100 on virtually every Linux distro


def _cgroup_v2_cpu_stat_field(field: str) -> int | None:
    path = _resolve_cgroup_path("cpu", "cpu.stat")
    text = _read_text(path) if path else None
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith(field + " "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _cgroup_v1_cpu_usage_usec() -> int | None:
    path = _resolve_cgroup_path("cpuacct", "cpuacct.usage")
    nanos = _read_int(path) if path else None
    return None if nanos is None else nanos // 1000


def _proc_stat_cpu_usec() -> int | None:
    text = _read_text(_PROC_STAT)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("cpu "):
            fields = line.split()[1:]
            try:
                jiffies = sum(int(f) for f in fields[:8])
            except ValueError:
                return None
            hz = _clock_ticks_per_second()
            return int(jiffies * 1_000_000 / hz)
    return None


def _os_times_cpu_usec() -> int | None:
    """Portable fallback: this process's own (+ children's) CPU time.

    Not host-wide, but combined with ``cpu_quota`` this still yields a
    meaningful utilisation figure where no cgroup/proc exists (macOS,
    Windows) — see plan section 4.3.
    """
    try:
        times = os.times()
    except OSError:
        return None
    total = times.user + times.system + times.children_user + times.children_system
    return int(total * 1_000_000)


def _psutil_cpu_usec() -> int | None:
    if psutil is None:
        return None
    try:
        times = psutil.cpu_times()
        return int((times.user + times.system) * 1_000_000)
    except Exception:
        return None


# Sources whose counter sums CPU time across every CPU on the host, not just
# what this container/process is entitled to (/proc/stat's "cpu " line and
# psutil.cpu_times() are both host-wide aggregates). A delta from one of
# these must be normalised by the host's CPU count, never by cpu_quota — a
# small container on a big host would otherwise see a wildly inflated
# "utilisation" and trip the CPU brake despite being idle itself.
_HOST_WIDE_CPU_SOURCES = frozenset({"proc_stat", "psutil"})


def _host_cpu_count() -> int | None:
    count = os.cpu_count()
    return count if count else None


def _resolve_cpu_usage_usec() -> tuple[int | None, str]:
    if sys.platform.startswith("linux"):
        usage = _cgroup_v2_cpu_stat_field("usage_usec")
        if usage is not None:
            return usage, "cgroup_v2"
        usage = _cgroup_v1_cpu_usage_usec()
        if usage is not None:
            return usage, "cgroup_v1"
        usage = _proc_stat_cpu_usec()
        if usage is not None:
            return usage, "proc_stat"
    usage = _os_times_cpu_usec()
    if usage is not None:
        return usage, "os_times"
    usage = _psutil_cpu_usec()
    if usage is not None:
        return usage, "psutil"
    return None, "unknown"


def _cgroup_v2_cpu_throttled_usec() -> int | None:
    if not sys.platform.startswith("linux"):
        return None
    return _cgroup_v2_cpu_stat_field("throttled_usec")


# ---------------------------------------------------------------------------
# PSI (pressure stall information) — kernel-side moving average, used as-is.
# ---------------------------------------------------------------------------


def _read_psi_avg10(path: Path | None) -> float | None:
    if path is None:
        return None
    text = _read_text(path)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("some "):
            match = re.search(r"avg10=([\d.]+)", line)
            if match:
                try:
                    return float(match.group(1)) / 100.0
                except ValueError:
                    return None
    return None


def _cpu_pressure() -> float | None:
    if not sys.platform.startswith("linux"):
        return None
    return _read_psi_avg10(_resolve_cgroup_path("cpu", "cpu.pressure"))


# ---------------------------------------------------------------------------
# Probe implementation
# ---------------------------------------------------------------------------


class SystemResourceProbe:
    """Stateful cross-platform :class:`ResourceProbe`.

    Stateful only to compute the CPU-utilisation delta between consecutive
    snapshots (plan section 4.3); every individual read remains best-effort
    and the whole method is wrapped so a probe failure degrades to an
    all-``None`` snapshot rather than propagating (corner case: "Any probe
    read raises").
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        baseline_tracker: "BaselineMemoryTracker | None" = None,
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._prev_cpu_usec: int | None = None
        self._prev_cpu_source: str | None = None
        self._prev_throttled_usec: int | None = None
        self._prev_time: float | None = None
        self._baseline_tracker = baseline_tracker or BaselineMemoryTracker()

    def snapshot(self) -> ResourceSnapshot:
        try:
            return self._snapshot_unguarded()
        except Exception:
            logger.exception("ResourceProbe snapshot failed; falling back to a default snapshot")
            return ResourceSnapshot(
                cpu_quota=1.0,
                cpu_utilisation=None,
                cpu_throttled_ratio=None,
                cpu_pressure=None,
                mem_limit_bytes=None,
                mem_working_set_bytes=None,
                source="error",
            )

    def _snapshot_unguarded(self) -> ResourceSnapshot:
        with self._lock:
            now = self._clock()
            cpu_quota = _resolve_cpu_quota()
            mem_limit, mem_working_set_raw, mem_source = _resolve_memory()
            mem_working_set, mem_baseline = (
                self._baseline_tracker.adjust(mem_working_set_raw, mem_limit)
            )
            cpu_usage_usec, cpu_source = _resolve_cpu_usage_usec()
            throttled_usec = _cgroup_v2_cpu_throttled_usec()

            cpu_utilisation: float | None = None
            cpu_throttled_ratio: float | None = None
            if (
                cpu_usage_usec is not None
                and self._prev_cpu_usec is not None
                and self._prev_time is not None
                and cpu_source == self._prev_cpu_source
            ):
                # A counter delta is only meaningful within the same source:
                # a fallback switch (e.g. cgroup read failure -> os.times())
                # changes scope, so treat it like a first sample instead of
                # diffing two incompatible counters.
                elapsed = now - self._prev_time
                if elapsed > 0:
                    delta_usec = cpu_usage_usec - self._prev_cpu_usec
                    if cpu_source in _HOST_WIDE_CPU_SOURCES:
                        # Host-wide counter: normalise by the host's CPU
                        # count, not this container's quota. Left as None
                        # (no false brake) if that count isn't available.
                        host_cpus = _host_cpu_count()
                        if host_cpus:
                            cpu_utilisation = max(
                                0.0, delta_usec / (elapsed * 1_000_000 * host_cpus)
                            )
                    elif cpu_quota > 0:
                        cpu_utilisation = max(0.0, delta_usec / (elapsed * 1_000_000 * cpu_quota))
                    if throttled_usec is not None and self._prev_throttled_usec is not None:
                        throttled_delta = throttled_usec - self._prev_throttled_usec
                        cpu_throttled_ratio = max(0.0, throttled_delta / (elapsed * 1_000_000))

            self._prev_cpu_usec = cpu_usage_usec
            self._prev_cpu_source = cpu_source
            self._prev_throttled_usec = throttled_usec
            self._prev_time = now

            return ResourceSnapshot(
                cpu_quota=cpu_quota,
                cpu_utilisation=cpu_utilisation,
                cpu_throttled_ratio=cpu_throttled_ratio,
                cpu_pressure=_cpu_pressure(),
                mem_limit_bytes=mem_limit,
                mem_working_set_bytes=mem_working_set,
                mem_working_set_raw_bytes=mem_working_set_raw,
                mem_baseline_bytes=mem_baseline,
                source=f"mem={mem_source},cpu={cpu_source}",
            )


def build_probe() -> ResourceProbe:
    return SystemResourceProbe()
