"""The lane plan: how many parse lanes a container can carry and how many
threads each gets.

Pure arithmetic over numbers the caller already has. Nothing here opens a
file, a descriptor or a thread, so it is safe to call from ``/health`` in any
mode.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import random
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from app.exceptions.indexing_exceptions import IndexingError
from app.services.resource_governor.lane_ledger import (
    BLANK,
    BRAKE_SPACING_SECONDS,
    LEDGER_VERSION,
    LOCAL_OVERLAY_SECONDS,
    WAITER_COOLDOWN_SECONDS,
    WAITER_REWRITE_SECONDS,
    DomainLedger,
    LedgerRecord,
    LedgerTxn,
    Presence,
    active_waiter,
    decide_spawn,
    effective_cap,
    next_lane_cap,
    pid_alive,
    spawn_allowance,
    with_plan,
)
from app.services.resource_governor.policy import INCIDENT_COOLDOWN_SECONDS
from app.services.resource_governor.shared_slots import (
    SharedSlots,
    SlotHold,
    ensure_private_directory,
    shared_slots_directory,
)

if TYPE_CHECKING:
    from pathlib import Path

    from app.services.resource_governor.controller import ResourceGovernor

logger = logging.getLogger(__name__)

# Upper bound on lanes in one container (the legacy pool capped itself at 4).
MAX_LANES = 64
LANE_SLOT_CAPACITY = MAX_LANES
LANE_DIRECTORY_PREFIX = "pipeshub-cpu-lanes"
# Lease weights of the local Docling conversion (CPU cores measured in 2a').
DOCLING_PDF_WEIGHT = 3
DOCLING_OTHER_WEIGHT = 1
DOCLING_AUTO_LANES_PER_CHILD = 3
# 0.85 GiB for the parse worker plus 0.40 GiB for its persistent raster child.
DEFAULT_WORKING_SET_GB = 1.25
DEFAULT_WARMUP_SECONDS = 20.0
# A worker idle this long may be stopped on a request or a brake step, never
# sooner, so a worker that has just returned is not killed for a stale request.
REAP_MIN_IDLE_SECONDS = 5.0
REAP_TICK_SECONDS = 1.0
# A request older than this is ignored; a requester still denied rewrites it on every poll.
REAP_REQUEST_FRESH_SECONDS = 2.0
REPLAN_SECONDS = 60.0
CPUS_PER_LANE = 2
MAX_THREADS_PER_LANE = 4

# The ``lanes`` block of ``/health`` reports this when the budget is not on.
LANES_MODE = "off"


@dataclass(frozen=True)
class LanePlan:
    cpus: int
    heavy: int
    lanes: int
    threads_per_lane: int
    cpus_per_lane: int
    source: str


# (cpus, lanes) -> threads, measured by pinning the parse benchmark to that many CPUs.
THREADS_RULE: dict[tuple[int, int], int] = {
    (1, 1): 1,
    (2, 1): 1,
    (2, 2): 1,
    (4, 1): 2,
    (4, 2): 1,
    (4, 4): 1,
    (8, 4): 2,
}


def docling_pdf_weight(plan: LanePlan) -> int:
    """Lease units of a Docling PDF conversion: 3 cores' worth, but never so many that one
    PDF takes every lane of a small plan and serialises the other conversions behind it."""
    return min(DOCLING_PDF_WEIGHT, max(1, plan.lanes - 1))


def docling_auto_width(plan: LanePlan) -> int:
    """Executor children for ``LOCAL_DOCLING_PARSE_WORKERS=auto``.

    A PDF conversion holds three lease units and one lane stays free of Docling at the
    full limit, so more children than this could never run together. 0 or 1 means the
    leased thread path.
    """
    return max(0, (plan.lanes - 1) // DOCLING_AUTO_LANES_PER_CHILD)


def threads_rule(cpus: int, lanes: int) -> int:
    """Threads per lane: the measured table, else ``clamp(cpus // lanes, 1, 4)``.

    ``lanes x threads <= cpus`` holds for every row and for the fallback. The
    16-CPU and 7-lane row is not measured; the tier run decides it.
    """
    lanes = max(1, lanes)
    measured = THREADS_RULE.get((cpus, lanes))
    if measured is not None:
        return measured
    return max(1, min(MAX_THREADS_PER_LANE, cpus // lanes))


def plan_lanes(
    *,
    cpus: int,
    heavy: int,
    cpus_per_lane: int = CPUS_PER_LANE,
    max_lanes: int = MAX_LANES,
    explicit_threads: int | None = None,
) -> LanePlan:
    """Lanes follow the heavy ceiling (``heavy // cpus_per_lane``, at least 1).

    Explicit pool sizes (``PARSE_POOL_WORKERS`` and friends) are private pools
    and deliberately have no parameter here. An explicit thread count wins over
    the rule, as ``PDF_TABLE_MODEL_THREADS`` does today.
    """
    # Never more lanes than CPUs, even if an explicit parse cap lifts heavy above them.
    lanes = max(1, min(heavy // max(1, cpus_per_lane), max_lanes, cpus))
    if explicit_threads is not None and explicit_threads > 0:
        return LanePlan(cpus, heavy, lanes, explicit_threads, cpus_per_lane, "explicit_threads")
    return LanePlan(cpus, heavy, lanes, threads_rule(cpus, lanes), cpus_per_lane, "rule")


def plan_for_governor(governor: ResourceGovernor, cpus: int) -> LanePlan:
    """The plan a governor's process computes: its fleet's heavy ceiling over the CPUs."""
    try:
        explicit = int(os.environ.get("PDF_TABLE_MODEL_THREADS", ""))
    except ValueError:
        explicit = None
    try:
        per_lane = int(os.environ.get("PARSE_LANE_CPUS", ""))
    except ValueError:
        per_lane = CPUS_PER_LANE
    return plan_lanes(
        cpus=cpus,
        heavy=governor.fleet_ceilings.heavy,
        cpus_per_lane=per_lane if per_lane >= 1 else CPUS_PER_LANE,
        explicit_threads=explicit,
    )


def lanes_health(governor: ResourceGovernor | None, cpus: int) -> dict[str, Any]:
    """The ``lanes`` block of ``/health``.

    Off: the mode and the plan, nothing opened. On: the budget's numbers plus the
    pool gauges.
    """
    if governor is None:
        return {"mode": LANES_MODE, "plan": None, "in_use_domain": None}
    budget = getattr(governor, "lanes", None)
    if isinstance(budget, LaneBudget):
        from app.modules.parsers.parse_pool import pool_stats

        block = budget.stats()
        gauges = pool_stats()
        block["pool"] = gauges.get("pool")
        block["interactive_pool"] = gauges.get("interactive")
        block["explicit_private"]["parse_pool"] = gauges.get("explicit_private")
        _add_consumer_blocks(block)
        return block
    plan = plan_for_governor(governor, cpus)
    return {"mode": LANES_MODE, "plan": asdict(plan), "in_use_domain": None}


def _add_consumer_blocks(block: dict[str, Any]) -> None:
    """Docling, the rasterizer and the SVG renderers, from the modules this process loaded
    (importing them here would load Docling into a service that never uses it)."""
    modules = sys.modules
    docling = modules.get("app.modules.parsers.pdf.docling_processor")
    if docling is not None:
        block["docling"] = docling.docling_lane_stats()
        explicit = explicit_setting(DOCLING_WORKERS_ENV)
        block["explicit_private"]["docling"] = explicit
    raster = modules.get("app.modules.parsers.pdf.pdf_rasterizer")
    if raster is not None:
        block["raster_jobs"] = raster.raster_jobs()
        block["explicit_private"]["raster"] = explicit_setting(RASTER_WORKERS_ENV)
    svg = modules.get("app.modules.parsers.image_parser.svg_worker")
    live = len(getattr(getattr(svg, "_renderers", None), "_all", ())) if svg is not None else 0
    block["unleased"]["svg_renderers_live"] = live


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_INT = re.compile(r"[+-]?\d+", re.ASCII)
POOL_WORKERS_ENV = "PARSE_POOL_WORKERS"
RASTER_WORKERS_ENV = "PDF_RASTER_WORKERS"
DOCLING_WORKERS_ENV = "LOCAL_DOCLING_PARSE_WORKERS"
# What the Docling worker setting means when nobody chose a value. Every
# shipped compose file passes this through, so it is not a choice.
DOCLING_DEFAULT_WORKERS = 1


def _integer(raw: str | None) -> int | None:
    text = (raw or "").strip()
    return int(text) if _INT.fullmatch(text) else None


def explicit_setting(name: str, environ: Mapping[str, str] | None = None) -> int | None:
    """The size an operator chose for a pool, or ``None`` when they did not.

    The one place that decides what "explicit" means; every pool calls it.
    ``PARSE_POOL_WORKERS`` is explicit for any non-negative integer (``0`` means
    no pool), ``PDF_RASTER_WORKERS`` for an integer of at least 1, and
    ``LOCAL_DOCLING_PARSE_WORKERS`` for an integer above its built-in default of 1.
    Unset, empty, malformed and ``auto`` are never explicit. The values that the
    existing pools already clamp away (negative, or below the default) are not
    explicit either, since they select the width the default would.
    """
    if name not in (POOL_WORKERS_ENV, RASTER_WORKERS_ENV, DOCLING_WORKERS_ENV):
        raise ValueError(f"{name} is not a pool size setting")
    env = os.environ if environ is None else environ
    value = _integer(env.get(name))
    if value is None:
        return None
    if name == POOL_WORKERS_ENV:
        return value if value >= 0 else None
    if name == RASTER_WORKERS_ENV:
        return value if value >= 1 else None
    return value if value > DOCLING_DEFAULT_WORKERS else None


def docling_auto(environ: Mapping[str, str] | None = None) -> bool:
    """``LOCAL_DOCLING_PARSE_WORKERS=auto``: its own opt-in mode."""
    env = os.environ if environ is None else environ
    return (env.get(DOCLING_WORKERS_ENV) or "").strip().lower() == "auto"


def parse_lanes_mode(environ: Mapping[str, str] | None = None) -> str:
    """``on`` only for an exact ``on``; anything else is ``off``."""
    env = os.environ if environ is None else environ
    return "on" if (env.get("PARSE_LANES") or "").strip().lower() == "on" else "off"


def interactive_workers(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    value = _integer(env.get("PARSE_INTERACTIVE_WORKERS"))
    return 1 if value is None or value < 1 else value


def _float_env(name: str, default: float, environ: Mapping[str, str] | None) -> float:
    env = os.environ if environ is None else environ
    try:
        value = float(env.get(name) or "")
    except ValueError:
        return default
    return value if value > 0 else default


# ---------------------------------------------------------------------------
# Errors and the nested scope
# ---------------------------------------------------------------------------


class LaneBudgetClosed(IndexingError):
    """The budget was closed while, or before, a caller wanted a lane."""


class LaneWaitCancelled(IndexingError):
    """The caller gave up waiting for a lane."""


class LaneWaitTimeout(IndexingError):
    """No lane became free within the allowed time."""


_scope = threading.local()


@contextlib.contextmanager
def lane_scope() -> Iterator[None]:
    """Mark the calling thread as running a job on a lane.

    Thread-local on purpose: a lease holder must not wait for another budget
    unit, and only the lane thread itself is covered. A ContextVar would be
    copied into every task and executor job started from here, which are other
    threads of control that may wait on their own. Set and cleared by the same
    thread; there are no tokens to release in another context.
    """
    _scope.depth = getattr(_scope, "depth", 0) + 1
    try:
        yield
    finally:
        _scope.depth -= 1


def in_lane_scope() -> bool:
    return getattr(_scope, "depth", 0) > 0


# ---------------------------------------------------------------------------
# Leases and tickets
# ---------------------------------------------------------------------------


class LaneLease:
    """The right to run one job on a lane. ``release`` is idempotent."""

    def __init__(self, hold: SlotHold | None, weight: int, waited_seconds: float = 0.0) -> None:
        self._hold = hold
        self.weight = weight
        self.waited_seconds = waited_seconds
        self._lock = threading.Lock()

    @property
    def nested(self) -> bool:
        """True for the free lease handed to a job already on a lane."""
        return self.weight == 0

    def release(self) -> None:
        with self._lock:
            hold, self._hold = self._hold, None
        if hold is not None:
            hold.release()

    def __enter__(self) -> LaneLease:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class SpawnTicket:
    """Permission to start one worker: a resident token and a window slot.

    ``release(started=False)`` gives both back (the worker did not start, or the
    caller was cancelled). ``release(started=True)`` keeps the token, which the
    worker holds until it exits; ``worker_exited`` then gives it back.
    """

    def __init__(
        self, budget: LaneBudget, token: SlotHold, window_start: float, *, local_window: bool
    ) -> None:
        self._budget = budget
        self.token = token
        self.window_start = window_start
        self._local_window = local_window
        self._done = False
        self._lock = threading.Lock()

    def release(self, *, started: bool) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        if not started:
            self.token.release()
            self._budget._return_window_slot(self.window_start, local_window=self._local_window)

    def worker_exited(self) -> None:
        self.token.release()


# ---------------------------------------------------------------------------
# In-process stand-ins, used when the shared files cannot be had
# ---------------------------------------------------------------------------


class _LocalSlots:
    """The ``SharedSlots`` contract for one process, with no files."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._held: set[int] = set()
        self._lock = threading.Lock()

    def try_acquire(self, limit: int, count: int = 1) -> SlotHold | None:
        count = min(max(1, count), self._capacity)
        with self._lock:
            held = len(self._held)
            free = [i for i in range(self._capacity) if i not in self._held]
            if len(free) < count or not (held == 0 or held + count <= limit):
                return None
            taken = free[:count]
            self._held.update(taken)
            return SlotHold(self, taken, held)  # type: ignore[arg-type]

    def _release(self, indices: list[int]) -> None:
        with self._lock:
            self._held.difference_update(indices)

    def held_count(self, budget: float = 1.0) -> int:
        with self._lock:
            return len(self._held)

    def close(self) -> None:
        return None


class _LocalTxn:
    def __init__(self, ledger: _LocalLedger) -> None:
        self._ledger = ledger

    def read(self) -> LedgerRecord:
        return self._ledger.record

    def write(self, record: LedgerRecord) -> None:
        self._ledger.record = record


class _LocalLedger:
    """The ledger of a process that has no shared directory."""

    degraded = False

    def __init__(self) -> None:
        self.record = BLANK
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[_LocalTxn | None]:
        with self._lock:
            yield _LocalTxn(self)

    def read(self) -> LedgerRecord:
        return self.record

    def invalidate(self) -> None:
        return None

    def close(self) -> None:
        return None


class _PresenceView(Presence):
    """Counts live processes without registering this one.

    Only a process that owns a budgeted pool is a standby holder, so presence
    is taken by ``register()`` when that pool is built, not when the budget is
    opened (a process with only a private pool must not raise the resident
    limit for the others).
    """

    def __init__(self, directory: Path, pid: int | None = None) -> None:
        self._base = directory
        self._pid = pid
        self._fd = None
        self._directory = directory / "presence"
        self._registered = False

    def register(self) -> None:
        if not self._registered:
            Presence.__init__(self, self._base, self._pid)
            self._registered = True


MemoryReader = Callable[[], "tuple[int | None, int | None]"]


def _probe_memory_reader() -> MemoryReader:
    probe = None

    def read() -> tuple[int | None, int | None]:
        nonlocal probe
        if probe is None:
            from app.services.resource_governor.probe import build_probe

            probe = build_probe()
        snap = probe.snapshot()
        raw = snap.mem_working_set_raw_bytes
        return snap.mem_limit_bytes, raw if raw is not None else snap.mem_working_set_bytes

    return read


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------


class BudgetOptions(TypedDict, total=False):
    memory_reader: MemoryReader
    clock: Callable[[], float]
    alive: Callable[[int], bool]
    pid: int
    working_set_bytes: int
    warmup_seconds: float
    plan_refresh: Callable[[], LanePlan | None]
    rng: Callable[[], float]
    view_ttl: float
    lock_budget: float
    fallback_seconds: float


_LEDGER_OPTIONS = ("view_ttl", "lock_budget", "fallback_seconds")


class _WaiterRequest:
    """One weighted acquire: whether it holds the waiter flag and when it last wrote it."""

    def __init__(self) -> None:
        self.registered = False
        self.last_write = float("-inf")


class LaneBudget:
    """One budget of CPU lanes for every process in a memory domain.

    Run leases bound the jobs running at once; resident tokens bound the worker
    processes alive at once (idle ones included); the ledger carries the brake
    and the spawn window. Lock order is the ledger lock, then a slot mutex;
    slot code never takes the ledger lock, and nothing waits while holding
    either.
    """

    def __init__(
        self,
        plan: LanePlan,
        *,
        run: SharedSlots | _LocalSlots,
        resident: SharedSlots | _LocalSlots,
        ledger: DomainLedger | _LocalLedger,
        presence: Presence | None = None,
        slots_kind: str = "shared",
        memory_reader: MemoryReader | None = None,
        clock: Callable[[], float] = time.monotonic,
        alive: Callable[[int], bool] = pid_alive,
        pid: int | None = None,
        working_set_bytes: int | None = None,
        warmup_seconds: float | None = None,
        plan_refresh: Callable[[], LanePlan | None] | None = None,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._plan = plan
        self._run, self._resident, self._ledger = run, resident, ledger
        self._presence = presence
        self._slots_kind = slots_kind
        self._reader = memory_reader or _probe_memory_reader()
        self._clock = clock
        self._alive = alive
        self._pid = os.getpid() if pid is None else pid
        self._ws_bytes = (
            working_set_bytes
            if working_set_bytes is not None
            else int(_float_env("GOVERNOR_LANE_WORKING_SET_GB", DEFAULT_WORKING_SET_GB, None) * 2**30)
        )
        self._warmup = (
            warmup_seconds
            if warmup_seconds is not None
            else _float_env("LANE_WARMUP_SECONDS", DEFAULT_WARMUP_SECONDS, None)
        )
        self._plan_refresh = plan_refresh
        self._replan_at = clock() + REPLAN_SECONDS
        self._rng = rng
        self._guard = threading.RLock()
        self._closed = False
        self._state = threading.Lock()
        self._reading: tuple[float, tuple[int | None, int | None]] | None = None
        self._overlay: tuple[int, float] | None = None
        self._pending_returns: list[float] = []
        self._local_window = (0.0, 0)
        self._waiting = 0
        self._waits: collections.deque[float] = collections.deque(maxlen=256)
        self._degrade_logged = False
        self._mismatch_logged = False
        self._live_cache: tuple[float, int] | None = None
        self._presence_registered = False
        self._waiter_owner: _WaiterRequest | None = None

    # -- construction ----------------------------------------------------------

    @classmethod
    def open(cls, plan: LanePlan, directory: Path, **options: Unpack[BudgetOptions]) -> LaneBudget:
        """The shared budget in *directory*. Raises ``OSError`` if it cannot be had."""
        rest: dict[str, Any] = dict(options)
        ledger_options = {k: rest.pop(k) for k in _LEDGER_OPTIONS if k in rest}
        clock = rest.get("clock", time.monotonic)
        opened: list[Any] = []
        ensure_private_directory(directory)
        try:
            run = SharedSlots(directory / "run", LANE_SLOT_CAPACITY)
            opened.append(run)
            resident = SharedSlots(directory / "resident", LANE_SLOT_CAPACITY)
            opened.append(resident)
            ledger = DomainLedger(directory, clock=clock, **ledger_options)
            opened.append(ledger)
            presence = _PresenceView(directory, rest.get("pid"))
            opened.append(presence)
        except BaseException:
            for item in opened:
                item.close()
            raise
        return cls(plan, run=run, resident=resident, ledger=ledger, presence=presence, **rest)

    @classmethod
    def local(cls, plan: LanePlan, **options: Unpack[BudgetOptions]) -> LaneBudget:
        """A budget of this process alone: today's behaviour, with the same API."""
        rest: dict[str, Any] = {k: v for k, v in options.items() if k not in _LEDGER_OPTIONS}
        return cls(
            plan,
            run=_LocalSlots(LANE_SLOT_CAPACITY),
            resident=_LocalSlots(LANE_SLOT_CAPACITY),
            ledger=_LocalLedger(),
            slots_kind="local",
            **rest,
        )

    @property
    def plan(self) -> LanePlan:
        refresh = self._plan_refresh
        if refresh is not None and self._clock() >= self._replan_at:
            self._replan_at = self._clock() + REPLAN_SECONDS
            fresh = refresh()
            if fresh is not None:
                self._plan, self._plan_refresh = fresh, None
        return self._plan

    # -- memory reading --------------------------------------------------------

    def _memory(self) -> tuple[int | None, int | None]:
        """``(limit, raw working set)``, read from the probe at most once a second."""
        now = self._clock()
        with self._state:
            cached = self._reading
            if cached is not None and 0 <= now - cached[0] < 1.0:
                return cached[1]
            try:
                value = self._reader()
            except Exception:
                logger.warning("lane budget: memory reading failed", exc_info=True)
                value = (None, None)
            self._reading = (now, value)
            return value

    def _pressure(self) -> float | None:
        limit, raw = self._memory()
        return None if not limit or raw is None else raw / limit

    # -- the limit ---------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise LaneBudgetClosed("the lane budget is closed")

    def _live_processes(self) -> int:
        now = self._clock()
        cached = self._live_cache
        if cached is not None and 0 <= now - cached[0] < 1.0:
            return cached[1]
        count = self._presence.live_count() if self._presence is not None else 1
        self._live_cache = (now, count)
        return count

    def register_presence(self) -> None:
        """Count this process among the pool owners. Called once by the first budgeted pool."""
        presence = self._presence
        if isinstance(presence, _PresenceView) and not self._presence_registered:
            presence.register()
            self._presence_registered = True
            self._live_cache = None

    def now(self) -> float:
        """The budget's clock, so a pool judging idle time uses the same one."""
        return self._clock()

    def live_processes(self) -> int:
        """Processes in the domain that own a budgeted pool (1 in local mode)."""
        return self._live_processes()

    def resident_limit(self) -> int:
        """Worker processes alive at once: the lanes plus one standby per extra pool owner."""
        return min(MAX_LANES, self.plan.lanes + max(0, self._live_processes() - 1))

    def _overlay_cap(self, now: float) -> int | None:
        overlay = self._overlay
        if overlay is None:
            return None
        if now - overlay[1] > LOCAL_OVERLAY_SECONDS:
            self._overlay = None
            return None
        return overlay[0]

    def cap(self) -> int:
        """The brake's cap for this process, from 1 to the plan's lanes."""
        plan = self.plan
        now = self._clock()
        if self._ledger.degraded:
            self._log_degraded()
            cap = max(1, plan.lanes // self._live_processes())
        else:
            self._degrade_logged = False
            record = self._ledger.read()
            self._note_mismatch(record)
            cap, _ = effective_cap(record, plan.lanes, now, self._pressure, self._alive)
        overlay = self._overlay_cap(now)
        if overlay is not None:
            cap = min(cap, overlay)
        return max(1, min(plan.lanes, cap))

    def limit(self) -> int:
        return self.cap()

    def _log_degraded(self) -> None:
        if not self._degrade_logged:
            self._degrade_logged = True
            logger.error(
                "lane budget: the ledger lock has been unavailable; the brake and the spawn "
                "window are per process until it returns. Run leases and resident tokens "
                "stay shared."
            )

    def _note_mismatch(self, record: LedgerRecord) -> None:
        if record.plan_lanes not in (0, self._plan.lanes) and not self._mismatch_logged:
            self._mismatch_logged = True
            logger.error(
                "lane budget: processes in this domain compute different plans (%s and %s lanes)",
                record.plan_lanes,
                self._plan.lanes,
            )

    # -- run leases --------------------------------------------------------------

    def _waiter_limit(self, limit: int, weight: int, request: _WaiterRequest | None) -> int | None:
        """The limit to use while a weighted request waits, or None to take nothing.

        Everyone but the waiter leaves room for it: a weight-1 caller may take a unit
        only if the waiter still fits, and nothing is taken at all when the waiter can
        only run alone.
        """
        waiter = active_waiter(self._ledger.read(), self._clock())
        if waiter is None:
            return limit
        waiter_weight, waiter_pid = waiter
        if request is not None and request is self._waiter_owner and waiter_pid == self._pid:
            return limit
        if waiter_weight >= limit:
            return None
        room = limit - waiter_weight
        return room if weight <= room else None

    def try_acquire(self, *, weight: int = 1, _request: _WaiterRequest | None = None) -> LaneLease | None:
        if in_lane_scope():
            return LaneLease(None, 0)
        weight = max(1, weight)
        limit = self.limit()
        self._check_open()
        limit_now = self._waiter_limit(limit, weight, _request)
        if limit_now is None:
            return None
        with self._guard:
            self._check_open()
            hold = self._run.try_acquire(limit_now, weight)
        return None if hold is None else LaneLease(hold, weight)

    def _backoff(self, delay: float) -> float:
        time.sleep(delay * (0.5 + self._rng()))
        return min(0.1, delay * 2)

    def _wait_done(self, started: float, *, waited: bool) -> None:
        if waited:
            self._waits.append(self._clock() - started)

    def _hold_place(self, request: _WaiterRequest, weight: int) -> None:
        """Keep a denied weighted request's place in the queue: register as the waiter,
        or rewrite the stamp, at most every ``WAITER_REWRITE_SECONDS``."""
        now = self._clock()
        if weight <= 1 or now - request.last_write < WAITER_REWRITE_SECONDS:
            return
        request.last_write = now
        mine = False

        def change(record: LedgerRecord) -> LedgerRecord | None:
            nonlocal mine
            if now < record.waiter_clear_at:
                return None
            if active_waiter(record, now) is not None and not (
                self._waiter_owner is request and record.waiter_pid == self._pid
            ):
                return None
            mine = True
            return replace(record, waiter_at=now, waiter_weight=weight, waiter_pid=self._pid)

        try:
            self._update(change)
        except Exception:
            logger.warning("lane budget: could not write the waiter flag", exc_info=True)
            return
        if mine:
            self._waiter_owner = request
            request.registered = True
            self._ledger.invalidate()

    def _release_place(self, request: _WaiterRequest) -> None:
        """Clear the flag (best effort) and start the cooldown, on admission or giving up."""
        if not request.registered:
            return
        request.registered = False
        if self._waiter_owner is request:
            self._waiter_owner = None
        now = self._clock()

        def change(record: LedgerRecord) -> LedgerRecord | None:
            if record.waiter_pid != self._pid:
                return None
            return replace(
                record,
                waiter_at=0.0,
                waiter_weight=0,
                waiter_pid=0,
                waiter_clear_at=now + WAITER_COOLDOWN_SECONDS,
            )

        if self._closed:
            return
        try:
            for _ in range(3):
                if self._update(change):
                    break
        except Exception:
            logger.warning("lane budget: could not clear the waiter flag", exc_info=True)
        self._ledger.invalidate()

    def acquire(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
        weight: int = 1,
        timeout: float | None = None,
    ) -> LaneLease:
        """Block the calling thread (never an event loop) until a lane is free."""
        started, delay, waited = self._clock(), 0.01, False
        request = _WaiterRequest()
        try:
            while True:
                lease = self.try_acquire(weight=weight, _request=request)
                if lease is not None:
                    lease.waited_seconds = self._clock() - started
                    return lease
                if not waited:
                    waited = True
                    with self._state:
                        self._waiting += 1
                if cancelled is not None and cancelled():
                    raise LaneWaitCancelled("cancelled while waiting for a lane")
                if timeout is not None and self._clock() - started >= timeout:
                    raise LaneWaitTimeout("no lane became free in time")
                self._hold_place(request, weight)
                delay = self._backoff(delay)
        finally:
            self._release_place(request)
            if waited:
                with self._state:
                    self._waiting -= 1
            self._wait_done(started, waited=waited)

    async def acquire_async(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
        weight: int = 1,
        timeout: float | None = None,
    ) -> LaneLease:
        started, delay, waited = self._clock(), 0.01, False
        request = _WaiterRequest()
        try:
            while True:
                lease = self.try_acquire(weight=weight, _request=request)
                if lease is not None:
                    lease.waited_seconds = self._clock() - started
                    return lease
                if not waited:
                    waited = True
                    with self._state:
                        self._waiting += 1
                if cancelled is not None and cancelled():
                    raise LaneWaitCancelled("cancelled while waiting for a lane")
                if timeout is not None and self._clock() - started >= timeout:
                    raise LaneWaitTimeout("no lane became free in time")
                self._hold_place(request, weight)
                await asyncio.sleep(delay * (0.5 + self._rng()))
                delay = min(0.1, delay * 2)
        finally:
            self._release_place(request)
            if waited:
                with self._state:
                    self._waiting -= 1
            self._wait_done(started, waited=waited)

    # -- ledger writes -----------------------------------------------------------

    def _apply_overlay(self, record: LedgerRecord, now: float) -> LedgerRecord:
        overlay = self._overlay_cap(now)
        if overlay is None:
            return record
        eff, _ = effective_cap(record, self._plan.lanes, now, self._pressure, self._alive)
        self._overlay = None
        if overlay >= eff:
            return record
        return replace(record, cap=overlay, cap_stamp=now, cap_owner_pid=self._pid, last_brake_at=now)

    def _drain_returns(self, record: LedgerRecord) -> LedgerRecord:
        pending, self._pending_returns = self._pending_returns, []
        for window_start in pending:
            if record.window_start == window_start and record.spawns_in_window > 0:
                record = replace(record, spawns_in_window=record.spawns_in_window - 1)
        return record

    def _update(self, change: Callable[[LedgerRecord], LedgerRecord | None]) -> bool:
        """Apply *change* to the record under the lock; False if the lock was busy."""
        now = self._clock()
        with self._ledger.transaction() as txn:
            if txn is None:
                return False
            stored = txn.read()
            record = self._drain_returns(self._apply_overlay(stored, now))
            new = change(record)
            final = record if new is None else new
            if final != stored:
                txn.write(with_plan(final, self._plan.lanes))
            return True

    def report_incident(self) -> int:
        """A worker was killed for memory: halve now and hold recovery for the cooldown."""
        now = self._clock()

        def change(record: LedgerRecord) -> LedgerRecord:
            eff, _ = effective_cap(record, self._plan.lanes, now, self._pressure, self._alive)
            return replace(
                record,
                cap=max(1, eff // 2),
                cap_stamp=now,
                cap_owner_pid=self._pid,
                cooldown_until=now + INCIDENT_COOLDOWN_SECONDS,
                cooldown_owner_pid=self._pid,
                last_brake_at=now,
            )

        if not self._update(change):
            cap = max(1, self.cap() // 2)
            overlay = self._overlay_cap(now)
            self._overlay = (cap if overlay is None else min(cap, overlay), now)
        self._ledger.invalidate()
        return self.cap()

    def brake_step(self, pressure: float | None = None) -> int | None:
        """One sampler tick: step the cap down under memory pressure.

        Applied at most once per ``BRAKE_SPACING_SECONDS`` across the domain.
        Recovery is not a step: it is read from the clock.
        """
        now = self._clock()
        reading = self._pressure() if pressure is None else pressure
        applied: list[int] = []

        def change(record: LedgerRecord) -> LedgerRecord | None:
            if now - record.last_brake_at < BRAKE_SPACING_SECONDS:
                return None
            eff, _ = effective_cap(record, self._plan.lanes, now, lambda: reading, self._alive)
            new = next_lane_cap(eff, reading)
            if new is None:
                return None
            applied.append(new)
            return replace(record, cap=new, cap_stamp=now, cap_owner_pid=self._pid, last_brake_at=now)

        if not self._update(change):
            eff = self.cap()
            new = next_lane_cap(eff, reading)
            if new is not None:
                self._overlay = (new, now)
                applied.append(new)
        self._ledger.invalidate()
        return applied[0] if applied else None

    # -- spawn admission ---------------------------------------------------------

    def admit_spawn(self, ws_bytes: int | None = None) -> SpawnTicket | None:
        """A ticket to start one worker, or ``None`` (poll again).

        One critical section under the ledger lock: the memory reading, the
        resident token, the window count and the record write. The token is
        taken first and tells whether it is the only one in the domain, which is
        what exempts the first worker from the headroom test.
        """
        self._check_open()
        plan = self.plan
        ws = self._ws_bytes if ws_bytes is None else ws_bytes
        mem_limit, raw = self._memory()
        now = self._clock()
        # The guard is taken only once the ledger lock is held, never while
        # waiting for it, so a stopped lock holder cannot stall leases.
        with self._ledger.transaction() as txn:
            if txn is not None:
                with self._guard:
                    self._check_open()
                    return self._admit_shared(txn, plan, now, ws, mem_limit, raw)
        self._check_open()  # a closed ledger yields no transaction: that is not a busy lock
        if not self._ledger.degraded:
            return None
        self._log_degraded()
        with self._guard:
            self._check_open()
            return self._admit_local(plan, now, ws, mem_limit, raw)

    def _admit_shared(
        self, txn: LedgerTxn | _LocalTxn, plan: LanePlan, now: float, ws: int, mem_limit: int | None, raw: int | None
    ) -> SpawnTicket | None:
        stored = txn.read()
        record = self._drain_returns(self._apply_overlay(stored, now))
        if record != stored:
            txn.write(with_plan(record, plan.lanes))
        token = self._resident.try_acquire(self.resident_limit())
        if token is None:
            self._maybe_request_reap(txn, record, plan, now)
            return None
        try:
            decision = decide_spawn(
                held_before=token.held_before,
                window_start=record.window_start,
                spawns_in_window=record.spawns_in_window,
                now=now,
                plan_lanes=plan.lanes,
                raw_working_set=raw,
                mem_limit=mem_limit,
                ws_bytes=ws,
                warmup_seconds=self._warmup,
            )
            if not decision.admit:
                token.release()
                if decision.reason in ("headroom", "unknown_memory"):
                    txn.write(with_plan(replace(record, reap_request_at=now), plan.lanes))
                return None
            txn.write(
                with_plan(
                    replace(
                        record,
                        window_start=decision.window_start,
                        spawns_in_window=decision.spawns_in_window,
                    ),
                    plan.lanes,
                )
            )
        except BaseException:
            token.release()
            raise
        return SpawnTicket(self, token, decision.window_start, local_window=False)

    def _maybe_request_reap(self, txn: LedgerTxn | _LocalTxn, record: LedgerRecord, plan: LanePlan, now: float) -> None:
        try:
            full = self._resident.held_count(0.0) >= self.resident_limit()
        except TimeoutError:
            return
        if full:
            txn.write(with_plan(replace(record, reap_request_at=now), plan.lanes))

    def _admit_local(
        self, plan: LanePlan, now: float, ws: int, mem_limit: int | None, raw: int | None
    ) -> SpawnTicket | None:
        token = self._resident.try_acquire(self.resident_limit())
        if token is None:
            return None
        start, spawns = self._local_window
        live = self._live_processes()
        decision = decide_spawn(
            held_before=token.held_before,
            window_start=start,
            spawns_in_window=spawns,
            now=now,
            plan_lanes=plan.lanes,
            raw_working_set=raw,
            mem_limit=mem_limit,
            ws_bytes=ws,
            warmup_seconds=self._warmup,
            allowance=max(1, spawn_allowance(plan.lanes) // live),
        )
        if not decision.admit:
            token.release()
            return None
        self._local_window = (decision.window_start, decision.spawns_in_window)
        return SpawnTicket(self, token, decision.window_start, local_window=True)

    def _return_window_slot(self, window_start: float, *, local_window: bool) -> None:
        if local_window:
            start, spawns = self._local_window
            if start == window_start and spawns > 0:
                self._local_window = (start, spawns - 1)
            return

        self._pending_returns.append(window_start)
        if self._closed:
            return
        try:
            self._update(lambda record: None)
        except Exception:
            logger.warning("lane budget: could not return a window slot", exc_info=True)

    def request_reap(self) -> None:
        """Ask the domain's idle warm workers to stop at their next reaper tick."""
        now = self._clock()
        self._update(lambda record: replace(record, reap_request_at=now))
        self._ledger.invalidate()

    def reap_requested_at(self) -> float:
        """When a spawner last asked for a reap (monotonic seconds; 0 if never)."""
        return self._ledger.read().reap_request_at

    # -- observation -------------------------------------------------------------

    def held_count(self) -> tuple[int, int]:
        """``(run leases, resident tokens)`` held in the domain, counted under the mutex."""
        with self._guard:
            if self._closed:
                return 0, 0
            return self._run.held_count(), self._resident.held_count()

    def stats(self) -> dict[str, Any]:
        plan = self.plan
        record = self._ledger.read()
        run, resident = self.held_count()
        waits = sorted(self._waits)
        p95 = waits[int(0.95 * (len(waits) - 1))] if waits else 0.0
        degraded = self._ledger.degraded
        return {
            "mode": "on",
            "plan": asdict(plan),
            "limit": self.limit(),
            "cap": self.cap(),
            "resident_limit": self.resident_limit(),
            "standby": self.resident_limit() - plan.lanes,
            "live_processes": self._live_processes(),
            "run_in_use_domain": run,
            "resident_domain": resident,
            "spawns_in_window": record.spawns_in_window,
            "waiting": self._waiting,
            "waiter_active": active_waiter(record, self._clock()) is not None,
            "wait_seconds_p95": p95,
            "slots": self._slots_kind,
            "ledger": "none" if self._slots_kind == "local" else ("degraded" if degraded else "ok"),
            "plan_mismatch": record.plan_lanes not in (0, plan.lanes),
            "explicit_private": {"parse_pool": None, "raster": None, "docling": None},
            "unleased": {"interactive": interactive_workers(), "svg_renderers_max": 4},
        }

    def close(self) -> None:
        """Close every file; waiters get ``LaneBudgetClosed`` at their next poll."""
        with self._guard:
            if self._closed:
                return
            self._closed = True
            for part in (self._run, self._resident, self._presence):
                if part is not None:
                    part.close()
        # Outside the guard: a spawner holds the ledger lock while it waits for the guard.
        self._ledger.close()


def open_lane_budget(
    plan: LanePlan,
    *,
    local_share: int,
    environ: Mapping[str, str] | None = None,
    directory: Path | None = None,
    **options: Unpack[BudgetOptions],
) -> LaneBudget | None:
    """The budget for ``PARSE_LANES``, or ``None`` when it is off.

    Off constructs and opens nothing. On, any failure to set up the shared
    files (a read-only ``/tmp``, no descriptors, no POSIX locks) logs one
    error and degrades to a budget of this process alone, sized at
    *local_share*: the pool's width before lanes existed. It never fails startup.
    """
    if parse_lanes_mode(environ) != "on":
        return None
    try:
        if directory is None:
            from app.services.resource_governor.probe import memory_domain_id

            domain = memory_domain_id()
            name = f"{LANE_DIRECTORY_PREFIX}-v{LEDGER_VERSION}-{LANE_SLOT_CAPACITY}" + (f"-{domain}" if domain else "")
            directory = shared_slots_directory(name)
            if directory is None:
                raise OSError("shared slots need POSIX file locks")
        return LaneBudget.open(plan, directory, **options)
    except Exception as exc:
        logger.error(
            "lane budget: the shared directory is unusable (%s); this process keeps a budget of "
            "its own with %s lane(s)",
            exc,
            local_share,
        )
        return LaneBudget.local(replace(plan, lanes=max(1, local_share)), **options)
