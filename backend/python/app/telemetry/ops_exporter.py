"""A bounded, pid-free ``/metrics`` for the operators of one container.

How it works: every governed process writes a snapshot file (``<run dir>/signals/<pid>.json``,
atomic rename) every few seconds. Exactly one process of the container holds the port and serves a
custom collector that reads the snapshots on each scrape and aggregates them: live gauges by sum, max
or min, counters as the sum of the live processes plus a carry. The carry holds the last value of
every process that disappeared (killed or restarted), added once, so ``increase()`` over a worker
restart counts the real events instead of the whole counter again. A snapshot whose process is gone
is folded into the carry and deleted, so the file count stays bounded under a crash loop. A holder
that dies is replaced by another process within the rebind interval, which reads the same carry file.

This is deliberately not ``prometheus_client`` multiprocess mode (it would change the value class of
every registry in the process, including the push telemetry's) and it never touches ``METRICS_BACKEND``:
the registry here is private.

Only the series in ``SERIES`` are served. Labels come from closed sets or from the process's own
service name; nothing carries a pid, pod, member, org, connector or record.
"""
from __future__ import annotations

import collections
import contextlib
import ipaddress
import json
import logging
import os
import select
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from app.services.resource_governor.explain import (
    DOWNSTREAM_NAMES,
    PRESSURE_REASONS,
    ROLE_NAMES,
    normalise_dependency,
    project,
)
from app.services.resource_governor.probe import memory_domain_id, read_pressure

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping
    from typing import BinaryIO

    from app.services.resource_governor import ResourceGovernor

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - not available on Windows
    fcntl = None  # type: ignore[assignment]

DEFAULT_PORT = 9091
DEFAULT_RUN_DIR = "/tmp/pipeshub-run"
SNAPSHOT_INTERVAL_S = 5.0
STALE_AFTER_S = 20.0
REBIND_INTERVAL_S = 30.0
STOP_JOIN_S = 2.0  # how long stop() waits for a round in progress before it leaves the last snapshot as it is
MAX_FILES = 64
CARRY_FILE = "counters-carry.json"
_FOLDED_KEPT = 256
MAX_SERIES = 300
DEFAULT_BIND = "0.0.0.0"  # the scraper is another pod; the chart narrows it to the pod IP
MAX_CONNECTIONS = 16  # handler threads of the port; past them the oldest unfinished request is evicted
ADMIT_WAIT_S = 1.0  # how long a queued connection waits for a slot before it gets a 503
ADMIT_QUEUE_MAX = 32  # connections waiting for a slot; one more gets a 503 at once
REQUEST_DEADLINE_S = 2.0  # the whole request (line and headers) must arrive within this, however it trickles
CONNECTION_TIMEOUT_S = 5.0  # a single read or write that blocks this long is dropped (the answer's own I/O)
COLLECT_CACHE_TTL_S = 1.0  # a scrape inside this window reuses the folded result
TMP_MAX_AGE_S = 60.0  # a writer's temp file older than this is a leftover of a killed process


@dataclass(frozen=True)
class SeriesSpec:
    kind: str  # "gauge" | "counter"
    labels: tuple[str, ...]
    agg: str  # "sum" | "max" | "min": how live processes combine
    help: str
    fleet: bool = False  # served only from the fleet state, never from a process snapshot
    carry: bool = True  # counters: keep the last value of vanished processes
    allowed: tuple[tuple[str, frozenset[str]], ...] = ()  # a label's values for this series only (a subset of CLOSED_LABELS)


def _g(labels: tuple[str, ...], agg: str, text: str, *, fleet: bool = False, allowed: dict[str, frozenset[str]] | None = None) -> SeriesSpec:
    return SeriesSpec("gauge", labels, agg, text, fleet, allowed=tuple((allowed or {}).items()))


def _c(
    labels: tuple[str, ...],
    text: str,
    *,
    fleet: bool = False,
    carry: bool = True,
    agg: str = "sum",
    allowed: dict[str, frozenset[str]] | None = None,
) -> SeriesSpec:
    return SeriesSpec("counter", labels, agg, text, fleet, carry, tuple((allowed or {}).items()))


_PSI_KINDS = frozenset({"some", "full"})
_DOWNSTREAM_KINDS = frozenset({"throttle", "timeout", "exhaustion", "unavailable"})
_EPISODE_KINDS = frozenset(
    {"slo_small", "slo_large", "saturation", "spend_stopped", "spend_degraded", "jobs_paused", "provider_hold",
     "oom", "restart", "unavailable"}
)
_PRESSURE_REASON_VALUES = frozenset(r.value for r in PRESSURE_REASONS)
_PARSE_POOLS = frozenset({"heavy_parse", "light_parse"})
_PARSE_OUTCOMES = frozenset({"ok", "failed", "retried", "abandoned_shutdown", "stale", "skipped"})
_DOCUMENT_OUTCOMES = frozenset({"completed", "partial", "failed", "stale", "off", "sequential_fallback"})
_FALLBACK_REASONS = frozenset({"deadline", "unavailable", "throttled"})
_GUARD_STATES = frozenset({"strict", "compatible"})
_EPISODE_STATES = frozenset({"ok", "pending", "open", "suppressed"})
_NOTICE_EDGES = frozenset({"enter", "recover", "reminder"})
_UPGRADE_FROM = frozenset({"small", "large"})
_UPGRADE_TO = frozenset({"large", "huge"})


# The pinned series table. Adding a series is a deliberate edit of this table (and its test).
SERIES: dict[str, SeriesSpec] = {
    # Governor and cgroup, one set per service of the container.
    "pipeshub_governor_pool_limit": _g(("service", "pool"), "sum", "Current admission limit of the pool."),
    "pipeshub_governor_pool_ceiling": _g(("service", "pool"), "sum", "Upper bound of the pool's limit."),
    "pipeshub_governor_pool_in_use": _g(("service", "pool"), "sum", "Permits held in the pool."),
    "pipeshub_governor_pool_waiters": _g(("service", "pool"), "sum", "Acquires queued for a permit."),
    "pipeshub_governor_pool_braked": _g(("service", "pool"), "max", "1 while a pressure rule holds the pool down."),
    "pipeshub_governor_brakes_total": _c(
        ("service", "pool", "reason"), "Times a pressure rule began to hold a pool down.",
        allowed={"reason": _PRESSURE_REASON_VALUES},
    ),
    "pipeshub_governor_oldest_not_started_seconds": _g(
        ("service",), "max", "Age of the oldest work published and not yet started."
    ),
    "pipeshub_cgroup_memory_pressure_ratio": _g(
        ("service",), "max",
        "Memory pressure the admission governor works from; it stands in for the governor's own figure until it "
        "exposes its reserved bytes, so today it is the observed working set over the cgroup memory limit.",
    ),
    "pipeshub_cgroup_cpu_utilisation_ratio": _g(("service",), "max", "CPU used over the CPU quota."),
    "pipeshub_cgroup_memory_working_set_ratio": _g(
        ("service",), "max", "Observed cgroup working set over the cgroup memory limit (what the kernel sees)."
    ),
    "pipeshub_cgroup_psi_ratio": _g(
        ("service", "resource", "kind", "window"), "max", "Pressure stall ratio.", allowed={"kind": _PSI_KINDS}
    ),
    # Every process of the container reads the same cgroup file: the value is the cgroup's, so the
    # processes combine by max (a sum would multiply it by the process count). A high-water mark is kept in
    # the carry, so the series never falls when the process that read the newest value dies.
    "pipeshub_cgroup_oom_kills_total": _c(("service",), "OOM kills in the container's cgroup.", carry=False, agg="max"),
    "pipeshub_downstream_events_total": _c(
        ("service", "dependency", "kind"), "Downstream symptoms reported to the governor.",
        allowed={"kind": _DOWNSTREAM_KINDS},
    ),
    "pipeshub_worker_last_progress_timestamp_seconds": _g(("service",), "min", "When the worker last made progress."),
    "pipeshub_draining": _g(("service",), "max", "1 while the process is draining."),
    "pipeshub_load_outbox_overflow_total": _c(
        ("edge",), "Notice edges skipped because the outbox was full.", allowed={"edge": _NOTICE_EDGES}
    ),
    "pipeshub_shutdown_grace_exceeded_total": _c((), "Requested stops that outlived their grace."),
    # Parse admission and parsing (producers: parse workers and the in-process executor).
    "pipeshub_parse_units_total": _c(("class", "outcome"), "Parse units by outcome.", allowed={"outcome": _PARSE_OUTCOMES}),
    "pipeshub_parse_admission_running": _g(("pool", "class"), "sum", "Parse permits running.", allowed={"pool": _PARSE_POOLS}),
    "pipeshub_parse_admission_waiting": _g(("pool", "class"), "sum", "Parse admissions waiting.", allowed={"pool": _PARSE_POOLS}),
    "pipeshub_parse_admission_grants_total": _c(
        ("pool", "class", "rule"), "Parse admissions granted by rule.", allowed={"pool": _PARSE_POOLS}
    ),
    "pipeshub_parse_admission_capacity": _g(
        ("pool", "class"), "sum", "Per-pod capacity a class gate can grant.", allowed={"pool": _PARSE_POOLS}
    ),
    "pipeshub_parse_engine_limit": _g(("engine",), "sum", "Limit of a parse engine."),
    "pipeshub_parse_engine_in_use": _g(("engine",), "sum", "Permits in use of a parse engine."),
    "pipeshub_parse_engine_fallback_total": _c(
        ("reason",), "Falls back from the primary parse engine.", allowed={"reason": _FALLBACK_REASONS}
    ),
    "pipeshub_parse_unknown_messages_total": _c(("action",), "Parse messages of an unknown shape."),
    "pipeshub_parse_cost_upgrades_total": _c(
        ("from", "to", "mode"), "Parse cost upgrades.", allowed={"from": _UPGRADE_FROM, "to": _UPGRADE_TO}
    ),
    "pipeshub_parse_admission_unscoped_total": _c((), "Parse admissions without a scope."),
    "pipeshub_parse_documents_total": _c(("outcome",), "Parsed documents by outcome.", allowed={"outcome": _DOCUMENT_OUTCOMES}),
    "pipeshub_stage_schema_guard": _g(("state",), "max", "Stage write-mode guard state.", allowed={"state": _GUARD_STATES}),
    # Fleet: from the load state the evaluator keeps; absent when there is none.
    "pipeshub_queue_pending": _g(("queue", "class"), "max", "Messages pending.", fleet=True),
    "pipeshub_queue_in_flight": _g(("queue", "class"), "max", "Messages in flight.", fleet=True),
    "pipeshub_queue_oldest_age_seconds": _g(("queue", "class"), "max", "Age of the oldest message.", fleet=True),
    "pipeshub_queue_pending_cost_units": _g(("queue", "class"), "max", "Pending cost units.", fleet=True),
    "pipeshub_load_state": _g(
        ("kind", "state"), "max", "Load episode state.", fleet=True,
        allowed={"kind": _EPISODE_KINDS, "state": _EPISODE_STATES},
    ),
    "pipeshub_load_notifications_total": _c(
        ("kind", "edge"), "Load notices queued.", fleet=True, allowed={"kind": _EPISODE_KINDS, "edge": _NOTICE_EDGES}
    ),
    "pipeshub_load_evaluator_last_tick_timestamp_seconds": _g((), "max", "Last evaluator tick.", fleet=True),
    "pipeshub_load_clock_regressions_total": _c((), "Clock regressions the evaluator re-anchored.", fleet=True),
}

# Closed label sets. A value outside the set drops the sample (a label can never carry free text).
CLOSED_LABELS: dict[str, frozenset[str]] = {
    "pool": frozenset({"index_heavy", "index_light", "heavy_parse", "light_parse"}),
    "class": frozenset({"small", "large", "huge"}),
    "reason": frozenset(
        {"none", "ramping", "memory_target", "memory_hard", "memory_soft", "cpu", "downstream", "frozen",
         "deadline", "unavailable", "throttled"}
    ),
    "resource": frozenset({"cpu", "memory"}),
    "kind": frozenset(
        {"some", "full", "throttle", "timeout", "exhaustion", "slo_small", "slo_large", "saturation",
         "spend_stopped", "spend_degraded", "jobs_paused", "provider_hold", "oom", "restart", "unavailable"}
    ),
    "window": frozenset({"avg10", "avg60"}),
    "queue": frozenset({"record-events", "parse"}),
    "engine": frozenset({"docling", "cpu", "vlm"}),
    "outcome": frozenset({"ok", "failed", "retried", "abandoned_shutdown", "stale", "skipped", "completed",
                          "partial", "off", "sequential_fallback"}),
    "rule": frozenset({"A1", "A2", "A3", "A4"}),
    "edge": frozenset({"enter", "recover", "email", "reminder", "event"}),
    "state": frozenset({"strict", "compatible", "ok", "pending", "open", "suppressed", "unknown"}),
    "action": frozenset({"requeued", "dead_lettered"}),
    "mode": frozenset({"in_place", "requeue"}),
    "from": frozenset({"small", "large", "huge"}),
    "to": frozenset({"small", "large", "huge"}),
}
# The values PIPESHUB_SERVICE takes (contracts section 16) plus the role names the governor reports:
# the closed set of this container's own services. A name outside it is never a label value.
SERVICE_NAMES = frozenset(ROLE_NAMES | {"parse-worker", "connectors", "parsing"})


def allowed_values(spec: SeriesSpec, name: str) -> frozenset[str] | None:
    """The values *name* may take in *spec*: its own narrower set, else the closed set; ``None`` for the
    service label (this container's own names)."""
    for label, values in spec.allowed:
        if label == name:
            return values
    if name == "dependency":
        return DOWNSTREAM_NAMES | {"other"}
    return CLOSED_LABELS.get(name)


def _label_ok(spec: SeriesSpec, name: str, value: str) -> bool:
    closed = allowed_values(spec, name)
    if closed is not None:
        return value in closed
    return value in SERVICE_NAMES


def _row_ok(spec: SeriesSpec, labels: tuple[str, ...]) -> bool:
    """Arity and the closed-set check, for every row from any source (live, folded or carried)."""
    return len(labels) == len(spec.labels) and all(
        _label_ok(spec, n, v) for n, v in zip(spec.labels, labels, strict=True)
    )


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _proc_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _write_json(path: Path, body: object) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(body, separators=(",", ":")))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# What a process writes
# ---------------------------------------------------------------------------

Sample = tuple[tuple[str, ...], float]


def derive_metrics(service: str, signal: Mapping[str, Any]) -> dict[str, list[list[Any]]]:
    """The gauge series one process's signal gives, as ``{name: [[label values, value], ...]}``."""
    out: dict[str, list[list[Any]]] = {}

    def put(name: str, labels: Iterable[str], value: object) -> None:
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        if isinstance(value, (int, float)) and value == value:
            out.setdefault(name, []).append([list(labels), float(value)])

    for pool, row in (signal.get("pools") or {}).items():
        put("pipeshub_governor_pool_limit", (service, pool), row.get("limit"))
        put("pipeshub_governor_pool_ceiling", (service, pool), row.get("ceiling"))
        put("pipeshub_governor_pool_in_use", (service, pool), row.get("inUse"))
        put("pipeshub_governor_pool_waiters", (service, pool), row.get("waiters"))
        put("pipeshub_governor_pool_braked", (service, pool), row.get("state") == "braked")
    put("pipeshub_governor_oldest_not_started_seconds", (service,), (signal.get("waiting") or {}).get("oldestNotStartedS"))
    cpu, mem = signal.get("cpu") or {}, signal.get("mem") or {}
    put("pipeshub_cgroup_memory_pressure_ratio", (service,), mem.get("pressureRaw"))
    put("pipeshub_cgroup_memory_working_set_ratio", (service,), mem.get("workingSetRatio"))
    put("pipeshub_cgroup_cpu_utilisation_ratio", (service,), cpu.get("util"))
    for resource, block, keys in (
        ("cpu", cpu, (("some", "avg10", "psiSomeAvg10"), ("some", "avg60", "psiSomeAvg60"))),
        ("memory", mem, (("some", "avg60", "psiSomeAvg60"), ("full", "avg60", "psiFullAvg60"))),
    ):
        for kind, window, key in keys:
            put("pipeshub_cgroup_psi_ratio", (service, resource, kind, window), block.get(key))
    put("pipeshub_cgroup_oom_kills_total", (service,), mem.get("oomKillsTotal"))
    put("pipeshub_draining", (service,), signal.get("draining"))
    return out


class GovernorSource:
    """Builds ``(signal, metrics)`` from a governor: the projection, the gauges, and the counters it
    keeps itself (brakes entered, downstream symptoms), which only this process can count."""

    def __init__(self, governor: ResourceGovernor, service: str, identity: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self._governor = governor
        self._service = service
        self._identity = identity
        self._project = project
        self._pressure = {r.value for r in PRESSURE_REASONS}
        self._brakes: dict[tuple[str, str], float] = {}
        self._downstream: dict[tuple[str, str], float] = {}

    def __call__(self) -> tuple[dict[str, Any], dict[str, list[list[Any]]]]:
        stats = self._governor.stats()
        util, ratio = stats.get("cpu_utilisation"), stats.get("mem_pressure_raw")
        try:
            pressure = read_pressure(util, ratio)
        except Exception:
            pressure = None
        signal = self._project(stats, self._identity() if self._identity else None, pressure)
        service = self._service
        # Brake entries are counted by the governor where the decision is made and read here, never inferred
        # from the states this process happens to see. Without the block the series is absent, not wrong.
        admission = stats.get("admission")
        entries = admission.get("brakeEntries") if isinstance(admission, dict) else None
        for pool, by_reason in (entries.items() if isinstance(entries, dict) else ()):
            for reason, n in (by_reason.items() if isinstance(by_reason, dict) else ()):
                if reason in self._pressure and isinstance(pool, str) and isinstance(n, int) and not isinstance(n, bool):
                    self._brakes[(pool, reason)] = max(self._brakes.get((pool, reason), 0.0), float(n))
        # The downstream symptoms are the governor's running totals, read in the same lock hold as the rest
        # of the admission block (`admission_explain`), so the figure is one consistent snapshot: a sample
        # taken between two reads, or two samples inside one tick, cannot be counted twice or dropped.
        # Without the admission block the counter is absent, not wrong.
        feedback = admission.get("feedback") if isinstance(admission, dict) else None
        totals = feedback.get("totals") if isinstance(feedback, dict) else None
        if isinstance(totals, dict):
            kinds = {"throttles": "throttle", "timeouts": "timeout", "exhaustions": "exhaustion",
                     "unavailable": "unavailable"}
            counted: dict[tuple[str, str], float] = {}
            for column, kind in kinds.items():
                names = totals.get(column)
                for dep, n in (names.items() if isinstance(names, dict) else ()):
                    if isinstance(n, int) and not isinstance(n, bool) and isinstance(dep, str):
                        name = normalise_dependency(dep)
                        counted[(name, kind)] = counted.get((name, kind), 0.0) + n
            for key, value in counted.items():  # a counter never falls, whatever the totals do
                self._downstream[key] = max(self._downstream.get(key, 0.0), value)
        # Process-wide counters, only when the load is known: never a zero for "no reading".
        metrics = derive_metrics(service, signal)
        metrics["pipeshub_governor_brakes_total"] = [[[service, p, r], v] for (p, r), v in self._brakes.items()]
        metrics["pipeshub_downstream_events_total"] = [
            [[service, d, k], v] for (d, k), v in self._downstream.items()
        ]
        return signal, metrics


class SnapshotWriter:
    """Writes this process's snapshot file, ``<pid>-<start time>.json``: a new process that reuses a
    dead one's pid has another start time, so it can never overwrite the dead one's counters before
    they are folded."""

    def __init__(self, run_dir: Path, service: str, source: Callable[[], tuple[dict[str, Any], dict[str, Any]]],
                 clock: Callable[[], float] = time.time,
                 domain: Callable[[], str | None] = memory_domain_id) -> None:
        self._dir = run_dir / "signals"
        self._domain = domain
        self._service = service
        self._source = source
        self._clock = clock
        self._pid = os.getpid()
        self._nonce = uuid.uuid4().hex[:12]
        self._start = _proc_start(self._pid)
        self._warned = False
        self._retired = False
        self._write_lock = threading.Lock()
        self.path = self._dir / (f"{self._pid}-{self._start}.json" if self._start else f"{self._pid}.json")

    def retire(self) -> None:
        """No more writes: a round still running when the process leaves must not bring the file back
        after it was folded into the carry."""
        with self._write_lock:
            self._retired = True

    def write(self) -> bool:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            signal, metrics = self._source()  # may be slow: not under the lock
            with self._write_lock:
                if self._retired:
                    return False
                _write_json(
                    self.path,
                    {"pid": self._pid, "nonce": self._nonce, "start": self._start, "at": self._clock(),
                     "service": self._service, "domain": self._domain(), "signal": signal, "metrics": metrics},
                )
            return True
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logger.warning("ops exporter: cannot write the snapshot under %s (%s); only fleet series are served",
                               self._dir, type(exc).__name__)
            return False

    def remove(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()


# ---------------------------------------------------------------------------
# What the port holder serves
# ---------------------------------------------------------------------------


def _pid_gone(pid: int) -> bool:
    """True when there is no such process (Linux only; elsewhere unknown, so False)."""
    return Path("/proc/self").exists() and not Path(f"/proc/{pid}").exists()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class SignalCollector:
    """Reads the snapshots on each scrape; folds and deletes the ones whose process is gone.

    Counters never go backwards: a live process whose snapshot is late keeps its counters in the sum
    (only its gauges are hidden), and a process that exits is folded into the carry under the same lock
    the scan reads under, so no scrape sees a process both live and carried.

    The result is cached for ``cache_ttl`` seconds, so a scrape flood costs one scan per window.

    Series are served in the order of ``SERIES``. Over ``MAX_SERIES`` the tail of the table is cut
    first (fleet series, then the parse families) and a warning says so; the governor and cgroup
    families at the head are the last to go.
    """

    def __init__(
        self,
        run_dir: Path,
        *,
        stale_after: float = STALE_AFTER_S,
        clock: Callable[[], float] = time.time,
        fleet_source: Callable[[], Iterable[tuple[str, tuple[str, ...], float]] | None] | None = None,
        is_alive: Callable[[int, str | None], bool] | None = None,
        cache_ttl: float = COLLECT_CACHE_TTL_S,
        max_series: int = MAX_SERIES,
        domain: Callable[[], str | None] = memory_domain_id,
    ) -> None:
        self._domain = domain
        self._dir = run_dir / "signals"
        self._carry_path = run_dir / CARRY_FILE
        self._stale_after = stale_after
        self._clock = clock
        self._fleet_source = fleet_source
        self._is_alive = is_alive or self._alive_by_proc
        self._cache_ttl = cache_ttl
        self._max_series = max_series
        self._cache: tuple[float, dict[str, dict[tuple[str, ...], float]]] | None = None
        self._cache_lock = threading.Lock()
        self._last_cut_warning = float("-inf")

    @staticmethod
    def _alive_by_proc(pid: int, start: str | None) -> bool:
        if start is None:
            return False
        return _proc_start(pid) == start

    # -- carry -----------------------------------------------------------------

    def _load_carry(self) -> dict[str, Any]:
        """The carry file. Its high-water marks belong to the memory domain that wrote them.

        A new cgroup (a container restart that kept the run directory) starts from its own kernel
        counters, so the marks of the old one are dropped; kept, they would hide its first kills.
        """
        carry: dict[str, Any] = {"folded": [], "values": {}, "hw": {}}
        try:
            loaded = json.loads(self._carry_path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("values"), dict):
                carry = loaded
                carry.setdefault("folded", [])
                if not isinstance(carry.get("hw"), dict):
                    carry["hw"] = {}
        except (OSError, ValueError):
            pass
        domain = self._domain()
        if carry.get("domain") != domain:
            carry["hw"] = {}
        carry["domain"] = domain  # also when the domain is unknown (None): a fold reads it
        return carry

    @contextlib.contextmanager
    def _carry_lock(self) -> Iterator[None]:
        if fcntl is None:
            yield
            return
        try:
            self._carry_path.parent.mkdir(parents=True, exist_ok=True)
            handle = (self._carry_path.parent / ".carry.lock").open("a")
        except OSError:  # an unwritable run dir: nothing to protect, only fleet series are served
            yield
            return
        with handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _fold_locked(self, snapshot: Mapping[str, Any]) -> None:
        """Add a vanished process's counters to the carry, once (by its nonce). The caller holds the lock."""
        ident = f"{snapshot.get('pid')}:{snapshot.get('nonce')}"
        carry = self._load_carry()
        if ident in carry["folded"]:
            return
        # A snapshot written under another memory cgroup (the container restarted and kept the run
        # directory) must not seed this cgroup's high-water mark; its summed counters still count.
        other_domain = "domain" in snapshot and snapshot["domain"] != carry["domain"]
        for name, rows in (snapshot.get("metrics") or {}).items():
            spec = SERIES.get(name)
            if spec is None or spec.kind != "counter" or spec.fleet or not isinstance(rows, list):
                continue
            if not spec.carry and spec.agg != "max":
                continue
            for row in rows:
                try:
                    labels, value = tuple(str(x) for x in row[0]), float(row[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if not _row_ok(spec, labels) or not _finite(value):
                    continue
                if "service" in spec.labels and labels[spec.labels.index("service")] != snapshot.get("service"):
                    continue
                key = json.dumps([name, *labels], separators=(",", ":"))
                if spec.carry:
                    carry["values"][key] = carry["values"].get(key, 0.0) + value
                elif other_domain:
                    continue  # a reading from another cgroup is not this one's
                else:
                    carry["hw"][key] = max(carry["hw"].get(key, 0.0), value)
        carry["folded"] = [*carry["folded"], ident][-_FOLDED_KEPT:]
        _write_json(self._carry_path, carry)

    def _fold(self, snapshot: Mapping[str, Any]) -> None:
        with self._carry_lock():
            self._fold_locked(snapshot)

    def retire(self, path: Path) -> None:
        """A process leaving cleanly: fold its snapshot into the carry and delete the file."""
        with self._carry_lock():
            try:
                snapshot = json.loads(path.read_text())
            except (OSError, ValueError):
                snapshot = None
            if isinstance(snapshot, dict):
                try:
                    self._fold_locked(snapshot)
                except OSError:
                    logger.debug("ops exporter: the carry could not be written; the snapshot stays for a later scan", exc_info=True)
                    return  # the counters live in this file until the carry has them
                except (ValueError, AttributeError):
                    pass  # an unusable snapshot: nothing to keep
            with contextlib.suppress(OSError):
                path.unlink()
        self._invalidate()

    # -- scan ------------------------------------------------------------------

    def _sweep_tmp(self, directory: Path, now: float) -> None:
        """Delete the temp files of writers that were killed between the write and the rename."""
        try:
            names = os.listdir(directory)
        except OSError:
            return
        for name in names:
            if not (name.startswith(".") and name.endswith(".tmp")):
                continue
            path = directory / name
            try:
                pid_text = name[: -len(".tmp")].rsplit(".", 1)[1]
                gone = pid_text.isdigit() and _pid_gone(int(pid_text))
                if gone or now - path.stat().st_mtime > TMP_MAX_AGE_S:
                    path.unlink()
            except (OSError, IndexError):
                continue

    def _scan_locked(self) -> list[dict[str, Any]]:
        live: list[dict[str, Any]] = []
        now = self._clock()
        self._sweep_tmp(self._dir, now)
        self._sweep_tmp(self._carry_path.parent, now)
        try:
            names = sorted(f for f in os.listdir(self._dir) if f.endswith(".json") and not f.startswith("."))
        except OSError:
            return live
        for name in names[: MAX_FILES * 2]:
            path = self._dir / name
            try:
                snapshot = json.loads(path.read_text())
                pid = int(snapshot["pid"])
                at = float(snapshot["at"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            fresh = abs(now - at) <= self._stale_after
            if self._is_alive(pid, snapshot.get("start")):
                if not fresh:
                    snapshot["_stale"] = True  # its gauges are old news; its counters still count
                live.append(snapshot)
                continue
            if snapshot.get("start") is None and (fresh or _pid_exists(pid)):
                # No /proc to prove it dead (not Linux). A fresh file is trusted; a late one is hidden
                # while its pid exists (folding it would count it again when it writes next), and
                # folded only once the pid is gone.
                snapshot["_stale"] = not fresh
                live.append(snapshot)
                continue
            try:  # the process may have written once more between our read and its exit: fold the last one
                latest = json.loads(path.read_text())
                if isinstance(latest, dict) and latest.get("pid") == snapshot.get("pid") and latest.get("nonce") == snapshot.get("nonce"):
                    snapshot = latest
            except (OSError, ValueError):
                pass
            try:
                self._fold_locked(snapshot)
            except OSError:
                # The carry cannot be written (disk full): the file keeps the counters, and its process is
                # still counted, as a late one, so the series does not dip. A later scan folds it.
                snapshot["_stale"] = True
                live.append(snapshot)
                continue
            with contextlib.suppress(OSError):
                path.unlink()
        return live

    def scan(self) -> list[dict[str, Any]]:
        """The live snapshots. Snapshots of vanished processes are folded, then deleted."""
        with self._carry_lock():
            return self._scan_locked()

    def gc(self) -> None:
        self.scan()
        self._invalidate()

    def _invalidate(self) -> None:
        with self._cache_lock:
            self._cache = None

    # -- collect ---------------------------------------------------------------

    def _merge(self) -> dict[str, dict[tuple[str, ...], float]]:
        with self._carry_lock():
            live = self._scan_locked()
            carried = self._load_carry()
        carry, high = carried["values"], carried["hw"]
        merged: dict[str, dict[tuple[str, ...], float]] = {}
        for snapshot in live:
            stale = bool(snapshot.get("_stale"))
            service = snapshot.get("service")
            for name, rows in (snapshot.get("metrics") or {}).items():
                spec = SERIES.get(name)
                if spec is None or spec.fleet or not isinstance(rows, list):
                    continue
                if stale and spec.kind == "gauge":
                    continue
                for row in rows:
                    try:
                        labels, value = tuple(str(x) for x in row[0]), float(row[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if not _row_ok(spec, labels) or not _finite(value):
                        continue
                    if "service" in spec.labels and labels[spec.labels.index("service")] != service:
                        continue  # a row names its own process's service, nothing else
                    bucket = merged.setdefault(name, {})
                    if labels not in bucket:
                        bucket[labels] = value
                    elif spec.agg == "max":
                        bucket[labels] = max(bucket[labels], value)
                    elif spec.agg == "min":
                        bucket[labels] = min(bucket[labels], value)
                    else:
                        bucket[labels] += value
        for key, value in carry.items():
            try:
                name, *labels_list = json.loads(key)
                labels = tuple(labels_list)
            except (ValueError, TypeError):
                continue
            spec = SERIES.get(name)
            if spec is None or spec.kind != "counter" or not spec.carry or not _row_ok(spec, labels):
                continue
            bucket = merged.setdefault(name, {})
            bucket[labels] = bucket.get(labels, 0.0) + float(value)
        for key, value in high.items():
            try:
                name, *labels_list = json.loads(key)
                labels = tuple(labels_list)
            except (ValueError, TypeError):
                continue
            spec = SERIES.get(name)
            if spec is None or spec.kind != "counter" or spec.carry or not _row_ok(spec, labels):
                continue
            bucket = merged.setdefault(name, {})
            bucket[labels] = max(bucket.get(labels, 0.0), float(value))

        if self._fleet_source is not None:
            try:
                for name, labels, value in self._fleet_source() or ():
                    spec = SERIES.get(name)
                    if spec is not None and spec.fleet and len(labels) == len(spec.labels) and all(
                        _label_ok(spec, n, v) for n, v in zip(spec.labels, labels, strict=True)
                    ):
                        merged.setdefault(name, {})[tuple(labels)] = float(value)
            except Exception:
                logger.debug("ops exporter: the fleet source failed", exc_info=True)
        return merged

    def _merged_cached(self) -> dict[str, dict[tuple[str, ...], float]]:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache
        if cached is not None and 0 <= now - cached[0] < self._cache_ttl:
            return cached[1]
        merged = self._merge()
        with self._cache_lock:
            self._cache = (now, merged)
        return merged

    def collect(self) -> Iterator[Any]:
        merged = self._merged_cached()
        budget = self._max_series
        cut: list[str] = []
        for name, spec in SERIES.items():  # the table's order is the priority: its tail is cut first
            series = merged.get(name)
            if not series:
                continue
            if spec.kind == "counter":
                family: Any = CounterMetricFamily(name.removesuffix("_total"), spec.help, labels=spec.labels)
            else:
                family = GaugeMetricFamily(name, spec.help, labels=spec.labels)
            added = 0
            for labels, value in sorted(series.items()):
                if budget <= 0:
                    if name not in cut:
                        cut.append(name)
                    continue
                family.add_metric(list(labels), value)
                budget -= 1
                added += 1
            if added:
                yield family
        if cut:
            self._warn_cut(cut)

    def _warn_cut(self, cut: list[str]) -> None:
        now = time.monotonic()
        if now - self._last_cut_warning < 60.0:
            return
        self._last_cut_warning = now
        logger.warning("ops exporter: over %d series; these families were cut (the end of the table goes first): %s",
                       self._max_series, ", ".join(cut[:8]))


def family_for(host: str) -> socket.AddressFamily:
    """The socket family of a bind address (``valid_bind`` accepts IPv4 and IPv6)."""
    return socket.AF_INET6 if ":" in host else socket.AF_INET


class SingleFlight:
    """One render at a time: scrapes that arrive while a render runs share its result.

    The next render starts only after the running one has finished, so no result is reused beyond the
    flight it was made in. A flood of scrapes then costs one render on the holder's event loop, not one per
    connection slot.
    """

    class _Flight:
        __slots__ = ("done", "error", "value")

        def __init__(self) -> None:
            self.done = threading.Event()
            self.value: bytes = b""
            self.error: BaseException | None = None

    def __init__(self, wait_s: float = CONNECTION_TIMEOUT_S) -> None:
        self._wait = wait_s
        self._lock = threading.Lock()
        self._current: SingleFlight._Flight | None = None
        self.waiting = 0  # followers inside the current flight (read by tests)

    def run(self, render: Callable[[], bytes]) -> bytes:
        with self._lock:
            flight = self._current
            leader = flight is None
            if flight is None:
                flight = self._current = SingleFlight._Flight()
            else:
                self.waiting += 1
        if leader:
            try:
                flight.value = render()
            except BaseException as exc:
                flight.error = exc
            finally:
                with self._lock:
                    self._current = None
                    self.waiting = 0
                flight.done.set()
        elif not flight.done.wait(self._wait):
            raise TimeoutError("the shared render did not finish in time")
        if flight.error is not None:
            raise flight.error
        return flight.value


class _MarkingReader:
    """The request reader: tells the server, as soon as a byte of the request has been read, that this client
    has sent something (an idle or not-yet-sending client has not)."""

    def __init__(self, inner: BinaryIO, started: Callable[[], None]) -> None:
        self._inner = inner
        self._started: Callable[[], None] | None = started

    def readline(self, limit: int = -1) -> bytes:
        data = self._inner.readline(limit)
        if data and self._started is not None:
            started, self._started = self._started, None  # once
            started()
        return data

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _Handler(BaseHTTPRequestHandler):
    registry: CollectorRegistry
    timeout = CONNECTION_TIMEOUT_S  # StreamRequestHandler applies it to the socket

    def setup(self) -> None:
        super().setup()
        mark = getattr(self.server, "mark_started", None)
        if mark is not None:
            self.rfile = _MarkingReader(self.rfile, lambda: mark(self.request))

    def do_GET(self) -> None:
        mark = getattr(self.server, "mark_serving", None)
        if mark is not None:
            mark(self.request)  # the request is complete: the deadline no longer applies
        if self.path.split("?", 1)[0] != "/metrics":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        render = getattr(self.server, "render", None)
        body = render(self.registry) if render is not None else generate_latest(self.registry)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


_REFUSAL = b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


class BoundedServer(HTTPServer):
    """A scrape is always answerable, however many clients sit on the port doing nothing.

    The port is unauthenticated. Each connection has one deadline for the whole request (``request_deadline``,
    not one per ``recv``), enforced by a reaper that closes the socket under the reader. Handler threads
    are bounded by ``max_connections``, counted until the thread has exited.

    The acceptor never waits: it takes the connection at once and either starts a handler (a slot is free) or
    puts it in a bounded admission queue (``ADMIT_QUEUE_MAX``). One admitter thread gives queued connections
    slots as they free up, evicting the oldest connection that has not yet sent a complete request, so idle or
    trickling clients can never keep a real scrape out. A connection that is already being answered is never
    evicted. One that finds the queue full, or waits ``ADMIT_WAIT_S`` for a slot, gets a short 503 and is closed.
    """

    daemon_threads = True
    request_queue_size = MAX_CONNECTIONS  # the kernel's accept queue is as deep as the handler cap: a deeper one only holds stale connections

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        max_connections: int = MAX_CONNECTIONS,
        request_deadline: float | None = None,
    ) -> None:
        # Everything server_close() touches exists before the base class binds: a failed bind (the port is
        # held by another process) calls server_close() from inside __init__.
        self._max = max_connections
        self.request_queue_size = max_connections  # the listen backlog follows this server's cap
        self._deadline = REQUEST_DEADLINE_S if request_deadline is None else request_deadline
        self._conns: dict[socket.socket, list[Any]] = {}  # socket -> [accepted at, being answered, has sent a byte]
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._live: set[threading.Thread] = set()  # handler threads that have not yet exited: the real slot count
        self._cut_socks: set[socket.socket] = set()  # evicted, not yet gone
        self._queue: collections.deque[tuple[socket.socket, object, float]] = collections.deque()
        self._closing = threading.Event()
        self._flight = SingleFlight()
        self.address_family = family_for(address[0])
        super().__init__(address, handler)
        threading.Thread(target=self._reap, name="ops-exporter-reaper", daemon=True).start()
        self._admitter = threading.Thread(target=self._admit_loop, name="ops-exporter-admit", daemon=True)
        self._admitter.start()

    def render(self, registry: CollectorRegistry) -> bytes:
        return self._flight.run(lambda: generate_latest(registry))

    def mark_started(self, sock: socket.socket) -> None:
        # No lock: a dict read and a list store are atomic, and a handler that waited on the server lock for
        # every line of every request would queue behind whichever thread the GIL has parked while holding it.
        entry = self._conns.get(sock)
        if entry is not None and len(entry) > 2:
            entry[2] = True

    def mark_serving(self, sock: socket.socket) -> None:
        with self._lock:
            entry = self._conns.get(sock)
            if entry is not None:
                entry[1] = True

    @staticmethod
    def _cut(sock: socket.socket, *, read_side_only: bool = False) -> None:
        """Wake the handler blocked in ``recv`` so that it leaves and frees its slot.

        ``read_side_only`` ends the reading only: bytes already received (a whole request the handler has not
        parsed yet, whatever the load) are still delivered to it and answered, then it leaves; a client that
        sent nothing, or part of a line, gets EOF and the handler leaves at once.
        """
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RD if read_side_only else socket.SHUT_RDWR)

    def _reap(self) -> None:
        tick = max(0.05, min(0.25, self._deadline / 4))
        while not self._closing.wait(tick):
            now = time.monotonic()
            with self._lock:
                late = [(s, not (len(entry) > 2 and entry[2])) for s, entry in self._conns.items()
                        if not entry[1] and now - entry[0] > self._deadline and s not in self._cut_socks]
                self._cut_socks.update(s for s, _silent in late)  # once: the handler is on its way out
            for sock, silent in late:
                self._dismiss(sock, silent=silent)

    def _dismiss(self, sock: socket.socket, *, silent: bool) -> None:
        """Make the handler of an unanswered connection leave.

        A client that has sent nothing, with nothing of a request waiting in the socket, is told ``503`` and
        cut on both sides, so that no answer can follow the refusal. Anything else (bytes already read by the
        handler, or still waiting in the socket, a whole request or a part of one) gets only its read side
        ended: what has arrived is still delivered to the handler and answered, then the handler leaves.
        """
        if silent and self._send_refusal(sock):
            self._cut(sock)
        else:
            self._cut(sock, read_side_only=True)

    @staticmethod
    def _send_refusal(sock: socket.socket) -> bool:
        """Send the 503 if nothing of a request is waiting in the socket and it can take the reply right now
        (never waits, whatever the socket's timeout). True when it was sent."""
        try:
            readable, writable, _ = select.select([sock], [sock], [], 0)
            if readable or not writable:
                return False  # bytes (or an EOF) are waiting, or the socket cannot take a reply: no 503
            sock.send(_REFUSAL, socket.MSG_DONTWAIT)
            return True
        except (OSError, ValueError):
            return False

    def _refuse(self, sock: socket.socket) -> None:
        """A short reply and a close, never a wait: the socket is non-blocking."""
        with contextlib.suppress(OSError):
            sock.setblocking(False)
            with contextlib.suppress(BlockingIOError):
                sock.recv(65536)  # drain what the client sent, so the close is not a reset that eats the reply
            sock.send(_REFUSAL)
        self.shutdown_request(sock)

    @staticmethod
    def _peer_gone(sock: socket.socket) -> bool:
        """True when the client has already closed (or reset) the connection: it needs no slot and no answer."""
        try:
            # select first: an accepted socket inherits the process's default timeout, and recv on such a
            # socket waits for readability before it looks at MSG_DONTWAIT. Nothing here may wait.
            readable, _, _ = select.select([sock], [], [], 0)
            if not readable:
                return False  # open, nothing sent yet
            return sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
        except (BlockingIOError, InterruptedError):
            return False
        except (OSError, ValueError):
            return True

    def _free_slots_locked(self) -> int:
        self._live = {t for t in self._live if t.is_alive()}  # a slot is held until its thread has exited
        return self._max - len(self._live)

    def _take_slot_locked(self, sock: socket.socket, client_address: object) -> bool:
        """Give *sock* a slot and start its handler, under the lock so the slot count never counts a thread
        that is registered but not yet started. False when the thread cannot start."""
        thread = threading.Thread(target=self._serve, args=(sock, client_address), name="ops-exporter-conn", daemon=True)
        self._conns[sock] = [time.monotonic(), False, False]  # accepted at, being answered, has sent a byte
        self._live.add(thread)
        try:
            thread.start()
        except Exception:
            self._conns.pop(sock, None)
            self._live.discard(thread)
            return False
        return True

    def _evict_locked(self) -> None:
        """Cut the oldest incomplete request, once per queued connection (one slot is enough for each)."""
        if len(self._cut_socks) >= len(self._queue):
            return
        waiting = [(len(e) > 2 and bool(e[2]), e[0], s) for s, e in self._conns.items() if not e[1] and s not in self._cut_socks]
        if waiting:
            sent_something, _at, victim = min(waiting, key=lambda item: (item[0], item[1]))  # the silent ones first
            self._cut_socks.add(victim)
            self._dismiss(victim, silent=not sent_something)

    def process_request(self, request: socket.socket | tuple[bytes, socket.socket], client_address: object) -> None:
        """Runs on the acceptor thread: it never waits."""
        sock: socket.socket = request  # type: ignore[assignment]
        if self._peer_gone(sock):
            self.shutdown_request(sock)  # it left before it was taken: no slot, no queue place, no thread
            return
        with self._lock:
            if not self._queue and self._free_slots_locked() > 0:
                refuse = not self._take_slot_locked(sock, client_address)
            elif len(self._queue) >= ADMIT_QUEUE_MAX:
                refuse = True  # the queue is full
            else:
                self._queue.append((sock, client_address, time.monotonic() + ADMIT_WAIT_S))
                self._evict_locked()
                self._wake.notify()
                return
        if refuse:
            self._refuse(sock)

    def _admit_loop(self) -> None:
        while not self._closing.is_set():
            refused: list[socket.socket] = []
            gone: list[socket.socket] = []
            with self._wake:
                if not self._queue:
                    self._wake.wait(0.5)
                    continue
                now = time.monotonic()
                while self._queue and self._queue[0][2] <= now:
                    refused.append(self._queue.popleft()[0])
                free = self._free_slots_locked()
                while self._queue and free > 0:
                    sock, client_address, _ = self._queue.popleft()
                    if self._peer_gone(sock):
                        gone.append(sock)  # it gave up while it waited: the slot goes to the next one
                        continue
                    free -= 1
                    if not self._take_slot_locked(sock, client_address):
                        refused.append(sock)
                if self._queue:
                    self._evict_locked()
                    self._wake.wait(0.005)  # an evicted handler is leaving: look again once its thread has exited
            for sock in refused:
                self._refuse(sock)
            for sock in gone:
                self.shutdown_request(sock)

    def _serve(self, request: socket.socket | tuple[bytes, socket.socket], client_address: object) -> None:
        try:
            self.finish_request(request, client_address)  # type: ignore[arg-type]
        except Exception:
            logger.debug("ops exporter: a scrape failed", exc_info=True)
        finally:
            with self._lock:
                self._conns.pop(request, None)  # type: ignore[arg-type]
                self._cut_socks.discard(request)  # type: ignore[arg-type]
            self.shutdown_request(request)

    def server_close(self) -> None:
        self._closing.set()
        with self._wake:
            queued = [item[0] for item in self._queue]
            self._queue.clear()
            self._wake.notify_all()
        for sock in queued:
            self.shutdown_request(sock)
        with self._lock:
            active = list(self._conns)
        for sock in active:  # handlers leave now, however slowly their clients trickle
            self._cut(sock)
        super().server_close()

    def handle_error(self, request: object, client_address: object) -> None:
        logger.debug("ops exporter: a connection failed", exc_info=True)


def valid_port(value: object) -> int | None:
    """*value* as a TCP port (1-65535), else ``None``."""
    try:
        port = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def valid_bind(value: object) -> str | None:
    """*value* as an IP address to bind, else ``None``."""
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None


class OpsExporter:
    """Per process: write the snapshot on a timer; and, until it holds it, try to bind the port."""

    def __init__(
        self,
        *,
        service: str,
        run_dir: str | Path,
        port: int,
        source: Callable[[], tuple[dict[str, Any], dict[str, Any]]],
        fleet_source: Callable[[], Iterable[tuple[str, tuple[str, ...], float]] | None] | None = None,
        bind: str = DEFAULT_BIND,
        interval: float = SNAPSHOT_INTERVAL_S,
        rebind_interval: float = REBIND_INTERVAL_S,
        stale_after: float = STALE_AFTER_S,
        cache_ttl: float = COLLECT_CACHE_TTL_S,
    ) -> None:
        if valid_port(port) is None:
            raise ValueError(f"the metrics port must be 1-65535, got {port!r}")
        if valid_bind(bind) is None:
            raise ValueError(f"the metrics bind address must be an IP address, got {bind!r}")
        self._run_dir = Path(run_dir)
        self._port = int(port)
        self._bind = bind
        self._interval = interval
        self._rebind = rebind_interval
        self.writer = SnapshotWriter(self._run_dir, service, source)
        self.collector = SignalCollector(self._run_dir, stale_after=stale_after, fleet_source=fleet_source, cache_ttl=cache_ttl)
        self.registry = CollectorRegistry(auto_describe=False)
        self.registry.register(self.collector)  # type: ignore[arg-type]
        self._server: BoundedServer | None = None
        self._bind_lock = threading.Lock()  # a bind and the final swap in stop() never overlap
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def holds_port(self) -> bool:
        return self._server is not None

    def try_bind(self) -> bool:
        with self._bind_lock:
            if self._server is not None:
                return True
            if self._stop.is_set():
                return False  # a stopped exporter never starts serving
            handler = type("Handler", (_Handler,), {"registry": self.registry})
            try:
                server = BoundedServer((self._bind, self._port), handler)
            except (OSError, OverflowError):
                return False
            self._server = server
            threading.Thread(target=server.serve_forever, name="ops-exporter-http", daemon=True).start()
            return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="ops-exporter", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        last_bind = float("-inf")
        while not self._stop.is_set():
            try:
                self.writer.write()
                now = time.monotonic()
                if self._server is None and now - last_bind >= self._rebind:
                    last_bind = now
                    self.try_bind()
                if self._server is not None:
                    self.collector.gc()
            except Exception:
                logger.warning("ops exporter: a round failed", exc_info=True)
            self._stop.wait(self._interval)

    def stop(self) -> None:
        """Graceful exit: stop writing, fold this process's counters into the carry and remove its file."""
        self._stop.set()
        round_running = False
        if self._thread is not None:
            self._thread.join(timeout=STOP_JOIN_S)
            round_running = self._thread.is_alive()
        if not round_running:  # never call the source beside a round that is still inside it
            self.writer.write()
        self.writer.retire()  # a round still running past the join must not bring the file back
        with contextlib.suppress(Exception):
            self.collector.retire(self.writer.path)
        with contextlib.suppress(Exception):
            self.collector.gc()
        with self._bind_lock:  # waits for a bind in progress, which then finds the exporter stopped
            server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()


def exporter_from_env(governor: ResourceGovernor, service: str, identity: Callable[[], Mapping[str, Any]] | None = None) -> OpsExporter | None:
    """An exporter for *governor* when ``PIPESHUB_METRICS_ENABLED`` is true, else ``None`` (the default).

    ``PIPESHUB_METRICS_PORT`` (1-65535) and ``PIPESHUB_METRICS_BIND`` (an IP address) that are not valid are
    refused here with a warning and the defaults are used.
    """
    if os.environ.get("PIPESHUB_METRICS_ENABLED", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    port = valid_port(os.environ.get("PIPESHUB_METRICS_PORT", DEFAULT_PORT))
    if port is None:
        logger.warning("ops exporter: PIPESHUB_METRICS_PORT is not a port (1-65535); using %d", DEFAULT_PORT)
        port = DEFAULT_PORT
    bind = valid_bind(os.environ.get("PIPESHUB_METRICS_BIND", DEFAULT_BIND))
    if bind is None:
        logger.warning("ops exporter: PIPESHUB_METRICS_BIND is not an IP address; using %s", DEFAULT_BIND)
        bind = DEFAULT_BIND
    return OpsExporter(
        service=service,
        run_dir=os.environ.get("PIPESHUB_RUN_DIR", DEFAULT_RUN_DIR),
        port=port,
        bind=bind,
        source=GovernorSource(governor, service, identity),
    )
