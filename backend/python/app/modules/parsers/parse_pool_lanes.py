"""The parse pool on the lane budget (``PARSE_LANES=on``).

``parse_pool`` keeps the legacy pool for ``off``; this module is only imported
when the mode is ``on``, so ``off`` neither builds a budget nor starts a reaper.

A job takes a *lease* (a CPU lane) and then a worker process. A worker holds a
*resident token* for as long as its process group lives, idle or not, so the
tokens bound the memory the pool can hold and the leases bound the CPUs it can
use. Idle workers stay warm and are stopped by the reaper (timer, request from
a process refused a spawn, brake); every worker is in exactly one state, under
one pool lock:

    IDLE         in the idle list, usable by a checkout or the reaper
    CHECKED_OUT  owned by one runner: starting, running a job, or just finished
    REAPING      removed from the list and being stopped
    DEAD         the whole process group is gone and the token is returned

The pool lock is never held while waiting for a lease, a token, a lock file or
a process.

Known limit: when a service is killed, its leases go back at once (the kernel
releases them) while its workers run on until they notice the parent is gone,
up to about 3 s. Running jobs in the domain can exceed the limit by those
workers for that long.

Known limit: in runs that kill services and workers at random, a lane thread has
occasionally been seen waiting for a reply from a live, idle-looking worker with
its lease held, so that lane runs nothing until the job timeout
(``RECORD_PROCESSING_TIMEOUT``) stops the job and replaces the worker. It was
not seen with a single process, and its cause is not established. Lanes are off
by default.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import math
import os
import queue
import threading
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from app.modules.parsers import parse_pool as legacy
from app.modules.parsers.parse_pool import (
    ParseAbandonedError,
    ParseWorkerError,
    _Job,
    _Lane,
    _PoolClosed,
    _Worker,
)
from app.services.messaging.config import messaging_env
from app.services.resource_governor.kinds import SCALES, ParseKind
from app.services.resource_governor.lanes import (
    REAP_MIN_IDLE_SECONDS,
    REAP_REQUEST_FRESH_SECONDS,
    REAP_TICK_SECONDS,
    LaneBudget,
    LaneBudgetClosed,
    LaneWaitCancelled,
    LaneWaitTimeout,
    explicit_setting,
    interactive_workers,
    lane_scope,
    threads_rule,
)
from app.utils.user_errors import PARSE_WORKER_UNAVAILABLE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.services.resource_governor import ResourceGovernor

_logger = logging.getLogger(__name__)

# Set while a person waits on the parse (a chat attachment). Every pool call
# made under it, however deep in a parser and in threads started with
# asyncio.to_thread (which copy the context), goes to the interactive pool.
_interactive: contextvars.ContextVar[bool] = contextvars.ContextVar("parse_pool_interactive", default=False)


@contextlib.contextmanager
def interactive_parsing() -> Iterator[None]:
    """Send every parse-pool call in this block to the interactive pool (``PARSE_LANES=on``)."""
    token = _interactive.set(True)
    try:
        yield
    finally:
        _interactive.reset(token)

WAIT_ENV = "LANE_WAIT_SECONDS"
THREADS_ENV = "PDF_TABLE_MODEL_THREADS"
# The image sets this to 2 for every service, so a 2 cannot be told from the default.
_OMP_IMAGE_DEFAULT = "2"
_WAIT_LOG_SECONDS = 30.0
_UNREAPED_ERROR_SECONDS = 30.0
_SETTLE_SECONDS = 1.0
_ADMIT_POLL_SECONDS = 0.05
_NEVER_IN_PARENT = frozenset({ParseKind.PDF_LAYOUT, ParseKind.EXCEL_LOAD, ParseKind.RENDER, ParseKind.INSPECT})


class _Lease(Protocol):
    def release(self) -> None: ...


class _Ticket(Protocol):
    def release(self, *, started: bool) -> None: ...

    def worker_exited(self) -> None: ...


class _LeaseSource(Protocol):
    def acquire(
        self, *, cancelled: Callable[[], bool] | None = None, weight: int = 1, timeout: float | None = None
    ) -> _Lease: ...


class _WorkerLike(Protocol):
    """What the pool needs from a worker; ``_Worker`` and the test stubs both fit."""

    process: Any

    def send(self, obj: object) -> None: ...
    def reply_ready(self, timeout: float) -> bool: ...
    def receive(self) -> object: ...
    def alive(self) -> bool: ...
    def stop(self) -> None: ...


# -- settings ---------------------------------------------------------------


def lane_wait_seconds() -> float:
    """How long a job may wait for a lease or a spawn before it fails as unavailable."""
    try:
        value = float(os.environ.get(WAIT_ENV) or "")
    except ValueError:
        value = 0.0
    return value if value > 0 else float(messaging_env.record_processing_timeout)


def worker_thread_env(threads: int, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment that gives a worker its lane's thread count.

    An explicit ``PDF_TABLE_MODEL_THREADS`` (``0`` included) wins in every pool
    kind: ONNX reads it itself, and the lane value never replaces it for OpenCV
    or the BLAS libraries. ``0`` means "do not limit", so those are left alone.
    An operator's ``OMP_NUM_THREADS`` other than the image default is kept.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(THREADS_ENV) or "").strip()
    explicit = int(raw) if raw.lstrip("-").isdigit() else None
    if explicit is not None:
        threads = explicit
    out: dict[str, str] = {"PIPESHUB_LANE_WORKER": "1"}
    if threads <= 0:
        if "PIPESHUB_LANE_CV2_THREADS" not in env:
            out["PIPESHUB_LANE_CV2_THREADS"] = "0"
        return out
    value = str(threads)
    out["PIPESHUB_LANE_THREADS"] = value
    # ``runtime_threads`` copies OMP_NUM_THREADS (image default 2) into the BLAS variables of
    # every service, so a BLAS value of 2 or one equal to an operator's OMP value is derived,
    # not chosen.
    omp = (env.get("OMP_NUM_THREADS") or "").strip()
    operator_omp = omp not in ("", _OMP_IMAGE_DEFAULT)
    if not operator_omp:
        out["OMP_NUM_THREADS"] = value
    for name in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        current = (env.get(name) or "").strip()
        if not operator_omp and current in ("", _OMP_IMAGE_DEFAULT):
            out[name] = value
    if "PIPESHUB_LANE_CV2_THREADS" not in env:
        out["PIPESHUB_LANE_CV2_THREADS"] = value
    return out


# -- process groups ---------------------------------------------------------


def group_has_live_member(pgid: int) -> bool:
    """True if a process that is not a zombie still belongs to group *pgid*.

    A zombie raster child under a PID 1 that never reaps it is gone for every
    purpose that matters here: it holds no memory.
    """
    try:
        names = os.listdir("/proc")
    except OSError:
        return False
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[-1].split()
        except OSError:
            continue
        if len(fields) > 2 and fields[0] != b"Z" and fields[2] == str(pgid).encode():
            return True
    return False


def group_rss_bytes(pgids: set[int]) -> dict[int, int]:
    """Resident bytes of every live process in each of the process groups *pgids* (one /proc scan)."""
    sums: dict[int, int] = {}
    try:
        names = os.listdir("/proc")
        page = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return sums
    wanted = {str(g).encode(): g for g in pgids}
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[-1].split()
            if len(fields) < 22 or fields[0] == b"Z" or fields[2] not in wanted:
                continue
            sums[wanted[fields[2]]] = sums.get(wanted[fields[2]], 0) + int(fields[21]) * page
        except (OSError, ValueError):
            continue
    return sums


def group_gone(worker: _WorkerLike) -> bool:
    """The leader has exited and no live process remains in its group."""
    check = getattr(worker, "group_gone", None)
    if callable(check):
        return bool(check())
    return worker.process.poll() is not None and not group_has_live_member(worker.process.pid)


# -- leases and tickets of the private pools --------------------------------


class _CounterLease:
    def __init__(self, owner: _CounterLeases) -> None:
        self._owner: _CounterLeases | None = owner

    def release(self) -> None:
        owner, self._owner = self._owner, None
        if owner is not None:
            owner._release()


class _CounterLeases:
    """An in-process lease counter: the lease side of a pool outside the budget."""

    def __init__(self, limit: Callable[[], int]) -> None:
        self._limit = limit
        self._held = 0
        self._lock = threading.Lock()

    def _release(self) -> None:
        with self._lock:
            self._held -= 1

    def acquire(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
        weight: int = 1,
        timeout: float | None = None,
    ) -> _CounterLease:
        started, delay = time.monotonic(), 0.005
        while True:
            limit = max(1, self._limit())
            with self._lock:
                if self._held < limit:
                    self._held += 1
                    return _CounterLease(self)
            if cancelled is not None and cancelled():
                raise LaneWaitCancelled("cancelled while waiting for a lane")
            if timeout is not None and time.monotonic() - started >= timeout:
                raise LaneWaitTimeout("no lane became free in time")
            time.sleep(delay)
            delay = min(0.1, delay * 2)

    def held(self) -> int:
        with self._lock:
            return self._held


class _FreeTicket:
    """Spawn permission for a pool that has no admission."""

    def release(self, *, started: bool) -> None:
        return None

    def worker_exited(self) -> None:
        return None


_FREE_TICKET = _FreeTicket()


# -- workers ----------------------------------------------------------------


class WorkerState(Enum):
    IDLE = "idle"
    CHECKED_OUT = "checked_out"
    REAPING = "reaping"
    DEAD = "dead"


class _Entry:
    """A pooled worker and what the pool tracks about it."""

    def __init__(
        self,
        worker: _WorkerLike,
        ticket: _Ticket,
        now: float,
        charged_bytes: int = 0,
        kind: object | None = None,
        charged_scale: float = 1.0,
    ) -> None:
        self.worker = worker
        self.ticket = ticket
        # The largest charge admitted for this worker (its high-water mark), and the kind it was for.
        self.charged_bytes = charged_bytes
        self.kind = kind
        self.charged_scale = charged_scale
        self.state = WorkerState.CHECKED_OUT  # born checked out by the runner that starts it
        self.last_used = now
        self.unreaped_since: float | None = None


class _LaneRunner(_Lane):
    """One job on one lease: the legacy ``_run``, with the worker taken from the pool.

    Only worker acquisition and disposal differ from ``_Lane``: every spawn goes
    through admission, and a worker's token is returned only once its whole
    process group is gone.
    """

    def __init__(self, pool: LanePool, lease: _Lease | None) -> None:
        super().__init__(pool)  # type: ignore[arg-type]
        self._lane_pool = pool
        self.lease = lease
        self._entry: _Entry | None = None

    def checkout(self, job: _Job) -> None:
        entry = self._lane_pool.checkout(job)
        if entry is not None:
            self._entry, self._worker = entry, entry.worker

    def finish(self, *, retire: bool = False) -> None:
        """Give a surviving worker back to the pool, or stop it (always, for a *retire* job)."""
        entry, self._entry = self._entry, None
        self._worker = None
        if entry is not None and (retire or not self._lane_pool.checkin(entry)):
            self._lane_pool.retire(entry)

    def _new_worker(self) -> _Worker:
        return self._lane_pool.make_worker()

    def _start_worker(self, job: _Job) -> _Worker:
        ticket, charged = self._lane_pool.admit(job, self._abandoned)
        if isinstance(ticket, _Entry):  # a worker came free while the spawn was refused: use it
            self._entry, self._worker = ticket, ticket.worker
            # It was charged for the job it last ran; the pre-run growth check saw no worker yet.
            self.grow(job)
            return ticket.worker
        try:
            worker = super()._start_worker(job)
        except BaseException:
            ticket.release(started=False)
            raise
        ticket.release(started=True)
        self._entry = self._lane_pool.born(worker, ticket, charged, job.kind)
        return worker

    def grow(self, job: _Job) -> None:
        """Admit the extra memory a heavier job brings to a worker that already holds less."""
        entry = self._entry
        if entry is not None:
            self._lane_pool.grow(entry, job, self._abandoned)

    def _drop_worker(self) -> None:
        entry, self._entry = self._entry, None
        self._worker = None
        if entry is not None:
            self._lane_pool.retire(entry)


class LanePool:
    """The parse pool on lanes: ``kind`` is ``budgeted``, ``explicit`` or ``interactive``."""

    def __init__(
        self,
        *,
        kind: str,
        threads: int,
        budget: LaneBudget | None,
        leases: _LeaseSource | None = None,
        admit: bool = False,
        worker_env: dict[str, str] | None = None,
        worker_factory: Callable[[], _WorkerLike] | None = None,
        clock: Callable[[], float] | None = None,
        reap_tick: float = REAP_TICK_SECONDS,
        idle_seconds: float | None = None,
    ) -> None:
        self.kind = kind
        self.budget = budget
        self.workers = threads
        self.admitting = admit and budget is not None
        self._leases: _LeaseSource = leases if leases is not None else budget  # type: ignore[assignment]
        self._worker_env = dict(worker_env or {})
        self._worker_factory = worker_factory
        self._clock = clock or (budget.now if budget is not None else time.monotonic)
        self._idle_seconds = idle_seconds
        self._tick = reap_tick
        self.jobs: queue.Queue[_Job | None] = queue.Queue()
        self.closed = threading.Event()
        self._submit_lock = threading.Lock()
        self._lock = threading.Lock()
        self._baton = threading.Lock()
        self._idle: list[_Entry] = []
        self._out: set[_Entry] = set()
        self._unreaped: list[_Entry] = []
        self._pending = 0
        self._pinned: set[int] = set()
        self._closing = False
        self._acted_stamp = 0.0
        self._spawn_waiters = 0
        self.spawns = 0
        self.died_idle = 0
        self.reaped = {"timer": 0, "request": 0, "brake": 0}
        self.reaped_for_other = 0
        self._by_kind: dict[str, int] = {}
        self._stop_reaper = threading.Event()
        self._reaper: threading.Thread | None = None
        self._threads = [
            threading.Thread(target=self._serve, name=f"parse-lane-{kind}-{i}", daemon=True)
            for i in range(threads)
        ]
        for thread in self._threads:
            thread.start()
        self.ensure_reaper()

    # -- submitting ---------------------------------------------------------------

    def enqueue(self, job: _Job) -> None:
        if self.admitting and job.kind in _NEVER_IN_PARENT:
            # Under the lane budget these kinds are charged as worker memory and the permit
            # prices only the parent, so a parent-thread fallback would hold the worker's
            # memory unpriced: it fails (retryable) instead.
            job.require_worker = True
        with self._submit_lock:
            if self.closed.is_set():
                raise _PoolClosed
            self.ensure_reaper()
            with self._lock:
                self._pending += 1
                self._pinned.add(id(job))
            self.jobs.put(job)

    async def submit(self, job: _Job) -> object:
        self.enqueue(job)
        try:
            return await asyncio.wrap_future(job.future)
        except asyncio.CancelledError:
            job.cancelled.set()
            raise

    def _unpin(self, job: _Job) -> None:
        """The job no longer waits for a worker: it holds one, is about to start one, or is gone."""
        with self._lock:
            if id(job) in self._pinned:
                self._pinned.discard(id(job))
                self._pending -= 1

    # -- the lane threads -----------------------------------------------------------

    def _serve(self) -> None:
        while True:
            taken = self._next()
            if taken is None:
                return
            job, lease = taken
            self._run_one(job, lease)

    def _next(self) -> tuple[_Job, _Lease] | None:
        # One thread at a time waits for a lease, so jobs get leases in queue order.
        with self._baton:
            while True:
                job = self.jobs.get()
                if job is None:
                    return None
                if not job.future.set_running_or_notify_cancel():
                    self._unpin(job)
                    continue
                try:
                    return job, self._lease(job)
                except BaseException as exc:
                    self._unpin(job)
                    job.future.set_exception(exc)

    def _lease(self, job: _Job) -> _Lease:
        started = time.monotonic()
        logged = False

        def cancelled() -> bool:
            nonlocal logged
            if not logged and time.monotonic() - started >= _WAIT_LOG_SECONDS:
                logged = True
                _logger.info("Parsing %s has waited %.0fs for a CPU lane", job.label, _WAIT_LOG_SECONDS)
            return job.cancelled.is_set() or self.closed.is_set()

        try:
            return self._leases.acquire(cancelled=cancelled, weight=1, timeout=lane_wait_seconds())
        except (LaneWaitCancelled, LaneBudgetClosed) as exc:
            raise ParseAbandonedError("parse abandoned while waiting for a CPU lane") from exc
        except LaneWaitTimeout as exc:
            _logger.error("No CPU lane became free for %s in time", job.label)
            raise ParseWorkerError(PARSE_WORKER_UNAVAILABLE, "unavailable") from exc

    def _run_one(self, job: _Job, lease: _Lease) -> None:
        runner = _LaneRunner(self, lease)
        result: object = None
        error: BaseException | None = None
        try:
            # The scope is thread-local: the in-thread fallback runs inside it, so a
            # budget call made by the job rides on this lease instead of waiting for one.
            with lane_scope():
                runner.checkout(job)
                runner.grow(job)
                result = runner._run(job)
        except BaseException as exc:  # handed to the waiting caller
            error = exc
        finally:
            self._unpin(job)
            try:
                runner.finish(retire=job.kind is ParseKind.EXCEL_LOAD and self.admitting)
            except Exception:
                _logger.exception("Could not settle a parse worker")
            finally:
                lease.release()
        if error is not None:
            job.future.set_exception(error)
        else:
            job.future.set_result(result)

    # -- admission and worker lifecycle ----------------------------------------------

    def make_worker(self) -> _WorkerLike:
        if self._worker_factory is not None:
            return self._worker_factory()
        return _Worker(self._worker_env)

    def admit(self, job: _Job, abandoned: Callable[[_Job], bool]) -> tuple[_Ticket | _Entry, int]:
        """A ticket for one spawn and the worker's charge, polling while admission refuses.

        Every spawn comes through here. The spawn adds the job's charge by kind to the domain's
        in-flight window; an Excel loader, retired after its job, does not count toward the spawn
        allowance (it loads no models).

        The job stays pinned until the spawn is admitted, and each refused poll looks at the idle
        list again: a worker another job just returned is taken instead of waiting for headroom.
        """
        budget = self.budget
        if not self.admitting or budget is None:
            self._unpin(job)
            return _FREE_TICKET, 0
        charged, window = budget.charge_for(job.kind, job.size, job.covered)
        deadline = time.monotonic() + lane_wait_seconds()
        with self._lock:
            self._spawn_waiters += 1
        try:
            while True:
                if abandoned(job):
                    raise ParseAbandonedError("parse abandoned while waiting to start a worker")
                try:
                    ticket = budget.admit_spawn(window, count_spawn=job.kind is not ParseKind.EXCEL_LOAD)
                except LaneBudgetClosed as exc:
                    raise ParseAbandonedError("parse abandoned: the lane budget closed") from exc
                if ticket is not None:
                    self._unpin(job)
                    self._note_spawn(job.kind)
                    return ticket, charged
                idle = self.checkout(job)
                if idle is not None:
                    return idle, 0
                if time.monotonic() >= deadline:
                    _logger.error("No worker could be started for %s in time", job.label)
                    raise ParseWorkerError(PARSE_WORKER_UNAVAILABLE, "unavailable")
                self.closed.wait(_ADMIT_POLL_SECONDS)
        finally:
            with self._lock:
                self._spawn_waiters -= 1

    def _note_spawn(self, kind: object | None) -> None:
        key = (kind or ParseKind.UNKNOWN).value  # type: ignore[attr-defined]
        with self._lock:
            self._by_kind[key] = self._by_kind.get(key, 0) + 1

    def grow(self, entry: _Entry, job: _Job, abandoned: Callable[[_Job], bool]) -> None:
        """Charge the growth of a warm worker handed a heavier job, polling while admission refuses.

        The job already holds its lease and the worker its token; growth takes neither. The worker's
        high-water charge is raised once, so a later job of the same weight pays nothing.
        """
        budget = self.budget
        if not self.admitting or budget is None:
            return
        charged, _ = budget.charge_for(job.kind, job.size, job.covered)
        if charged <= entry.charged_bytes:
            return
        deadline = time.monotonic() + lane_wait_seconds()
        delta = charged - entry.charged_bytes
        with self._lock:
            self._spawn_waiters += 1
        try:
            while True:
                if abandoned(job):
                    raise ParseAbandonedError("parse abandoned while waiting for worker memory")
                try:
                    admitted = budget.admit_growth(delta)
                except LaneBudgetClosed as exc:
                    raise ParseAbandonedError("parse abandoned: the lane budget closed") from exc
                if admitted:
                    entry.charged_bytes = charged
                    entry.kind = job.kind
                    entry.charged_scale = SCALES.scale(job.kind)  # type: ignore[arg-type]
                    budget.note_growth(job.kind, delta)
                    return
                if time.monotonic() >= deadline:
                    _logger.error("No memory was admitted for %s in time", job.label)
                    raise ParseWorkerError(PARSE_WORKER_UNAVAILABLE, "unavailable")
                self.closed.wait(_ADMIT_POLL_SECONDS)
        finally:
            with self._lock:
                self._spawn_waiters -= 1

    def born(self, worker: _WorkerLike, ticket: _Ticket, charged: int = 0, kind: object | None = None) -> _Entry:
        entry = _Entry(worker, ticket, self._clock(), charged, kind, SCALES.scale(kind))  # type: ignore[arg-type]
        with self._lock:
            self._out.add(entry)
            self.spawns += 1
        return entry

    def checkout(self, job: _Job) -> _Entry | None:
        """The most recently used live idle worker, now owned by the caller."""
        dead: list[_Entry] = []
        found: _Entry | None = None
        with self._lock:
            # A loader that is stopped after its job never takes a warm worker: that one holds
            # a layout parse's heap and the loader sets its own data limit.
            while self._idle and not (self.admitting and job.kind is ParseKind.EXCEL_LOAD):
                entry = self._idle.pop(0)
                if entry.worker.alive():
                    entry.state = WorkerState.CHECKED_OUT
                    self._out.add(entry)
                    found = entry
                    self._unpin_locked(job)
                    break
                entry.state = WorkerState.REAPING
                dead.append(entry)
        for entry in dead:  # died idle: no job's fault, no incident
            self.died_idle += 1
            self.retire(entry)
        return found

    def _unpin_locked(self, job: _Job) -> None:
        if id(job) in self._pinned:
            self._pinned.discard(id(job))
            self._pending -= 1

    def checkin(self, entry: _Entry) -> bool:
        """Make a live worker idle again. False if the pool is closing or the worker died."""
        with self._lock:
            self._out.discard(entry)
            if self._closing or not entry.worker.alive():
                entry.state = WorkerState.REAPING
                return False
            entry.state = WorkerState.IDLE
            entry.last_used = self._clock()
            self._idle.insert(0, entry)
            return True

    def retire(self, entry: _Entry) -> bool:
        """Stop a worker and return its token once the whole process group is gone."""
        with self._lock:
            self._out.discard(entry)
            if entry.state is WorkerState.DEAD:
                return True
            entry.state = WorkerState.REAPING
        try:
            entry.worker.stop()
        except Exception:
            _logger.exception("Stopping a parse worker failed")
        deadline = time.monotonic() + _SETTLE_SECONDS
        while not group_gone(entry.worker):
            if time.monotonic() >= deadline:
                with self._lock:
                    if entry.unreaped_since is None:
                        entry.unreaped_since = time.monotonic()
                    if entry not in self._unreaped:
                        self._unreaped.append(entry)
                return False
            time.sleep(0.02)
        return self._finalize(entry)

    def _finalize(self, entry: _Entry) -> bool:
        with self._lock:
            if entry.state is WorkerState.DEAD:
                return True
            entry.state = WorkerState.DEAD
            if entry in self._unreaped:
                self._unreaped.remove(entry)
        try:
            entry.ticket.worker_exited()
        except Exception:  # the budget was closed under the pool: nothing is left to give back to
            _logger.warning("Could not return a resident token", exc_info=True)
        return True

    def _retry_unreaped(self) -> None:
        with self._lock:
            survivors = list(self._unreaped)
        for entry in survivors:
            if group_gone(entry.worker):
                self._finalize(entry)
                continue
            with contextlib.suppress(Exception):
                entry.worker.stop()
            if group_gone(entry.worker):
                self._finalize(entry)
            elif entry.unreaped_since is not None and time.monotonic() - entry.unreaped_since > _UNREAPED_ERROR_SECONDS:
                _logger.error("A parse worker's process group survived SIGKILL for over %.0fs", _UNREAPED_ERROR_SECONDS)
                entry.unreaped_since = time.monotonic()

    # -- the reaper ---------------------------------------------------------------

    def ensure_reaper(self) -> None:
        thread = self._reaper
        if thread is not None and thread.is_alive():
            return
        if self.closed.is_set():
            return
        self._reaper = threading.Thread(target=self._reap_loop, name="parse-lane-reaper", daemon=True)
        self._reaper.start()

    def _reap_loop(self) -> None:
        while not self._stop_reaper.wait(self._tick):
            try:
                self.reap_once()
            except Exception:
                _logger.exception("The parse pool reaper failed; it continues")

    def _idle_limit(self) -> float:
        return legacy.IDLE_SECONDS if self._idle_seconds is None else self._idle_seconds

    def reap_once(self, now: float | None = None) -> None:
        """One reaper tick. Only ``IDLE`` workers are reaped, never one a queued job needs."""
        now = self._clock() if now is None else now
        self._retry_unreaped()
        if self.admitting:
            self.observe_charges()
        signals = self._read_signals()
        chosen: list[tuple[_Entry, str]] = []
        dead: list[_Entry] = []
        with self._lock:
            if self._closing:
                return
            dead.extend(e for e in self._idle if not e.worker.alive())  # an idle death frees its token within a tick
            for entry in dead:
                self._idle.remove(entry)
                entry.state = WorkerState.REAPING
            pinned = min(self._pending, len(self._idle))
            candidates = self._idle[pinned:]
            limit = self._idle_limit()
            chosen.extend((e, "timer") for e in candidates if now - e.last_used >= limit)
            picked = {id(entry) for entry, _ in chosen}
            extra = self._pick(
                now,
                [e for e in candidates if id(e) not in picked],
                [e for e in self._idle if id(e) not in picked],
                pinned,
                signals,
            )
            if extra is not None:
                chosen.append(extra)
            for entry, _ in chosen:
                self._idle.remove(entry)
                entry.state = WorkerState.REAPING
            waiting_here = self._spawn_waiters > 0
        for entry in dead:
            self.died_idle += 1
            self.retire(entry)
        for entry, reason in chosen:
            self.reaped[reason] += 1
            if reason == "request" and not waiting_here:
                self.reaped_for_other += 1
            self.retire(entry)

    def observe_charges(self) -> None:
        """Compare each charged worker's measured tree with its charge; a kind that holds more raises its scale."""
        with self._lock:
            entries = [e for e in (*self._idle, *self._out) if e.charged_bytes > 0 and e.kind is not None]
        pids = {}
        for entry in entries:
            pid = getattr(getattr(entry.worker, "process", None), "pid", None)
            if isinstance(pid, int) and pid > 0:
                pids[id(entry)] = pid
        if not pids:
            return
        sums = group_rss_bytes(set(pids.values()))
        for entry in entries:
            rss = sums.get(pids.get(id(entry), -1))
            if rss:
                SCALES.observe(entry.kind, rss, entry.charged_bytes, entry.charged_scale)  # type: ignore[arg-type]

    def _read_signals(self) -> dict[str, Any] | None:
        budget = self.budget
        if budget is None or not self.admitting:
            return None
        try:
            run, resident = budget.held_count()
            return {
                "stamp": budget.reap_requested_at(),
                "cap": budget.cap(),
                "lanes": budget.plan.lanes,
                "run": run,
                "resident": resident,
            }
        except LaneBudgetClosed:
            return None
        except Exception:
            _logger.warning("Could not read the lane budget for the reaper", exc_info=True)
            return None

    def _pick(
        self,
        now: float,
        candidates: list[_Entry],
        pinnable: list[_Entry],
        pinned: int,
        signals: dict[str, Any] | None,
    ) -> tuple[_Entry, str] | None:
        """At most one worker per tick for a request or the brake: the one idle longest.

        A request ignores the pins: it means a job in some process holds a lease and cannot get a
        worker. A job queued here waits for a lease that holder keeps, so the worker it pins
        cannot run before the holder gives up, and nothing else could free the memory.
        """
        if signals is None:
            return None
        stamp = signals["stamp"]
        if stamp > self._acted_stamp and 0 <= now - stamp <= REAP_REQUEST_FRESH_SECONDS:
            eligible = [e for e in pinnable if now - e.last_used >= REAP_MIN_IDLE_SECONDS and e.last_used < stamp]
            if eligible:
                self._acted_stamp = stamp
                return min(eligible, key=lambda e: e.last_used), "request"
        old = [e for e in candidates if now - e.last_used >= REAP_MIN_IDLE_SECONDS]
        if not old:
            return None
        if signals["cap"] < signals["lanes"] and signals["resident"] > max(signals["cap"], signals["run"] + pinned):
            return min(old, key=lambda e: e.last_used), "brake"
        return None

    # -- shutdown and observation ---------------------------------------------------

    def shutdown(self) -> None:
        with self._submit_lock:
            self.closed.set()
        with self._lock:
            self._closing = True
            idle, self._idle = self._idle, []
            for entry in idle:
                entry.state = WorkerState.REAPING
        self._stop_reaper.set()
        reaper = self._reaper
        if reaper is not None and reaper is not threading.current_thread():
            reaper.join(timeout=5)
        for _ in self._threads:
            self.jobs.put(None)
        for entry in idle:
            self.retire(entry)
        for thread in self._threads:
            thread.join(timeout=5)
        deadline = time.monotonic() + 5.0
        while self._unreaped and time.monotonic() < deadline:
            self._retry_unreaped()
            time.sleep(0.05)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            idle, busy, unreaped, pending = len(self._idle), len(self._out), len(self._unreaped), self._pending
            charged = sum(e.charged_bytes for e in (*self._idle, *self._out))
            by_kind = {k: {"spawns": n} for k, n in self._by_kind.items()}
        reaper = self._reaper
        return {
            "kind": self.kind,
            "threads": int(self._worker_env["PIPESHUB_LANE_THREADS"]) if "PIPESHUB_LANE_THREADS" in self._worker_env else None,
            "width": self.workers,
            "idle": idle,
            "busy": busy,
            "pending": pending,
            "spawns": self.spawns,
            "charged_resident_bytes": charged,
            "by_kind": by_kind,
            "charged": self.admitting,
            "reaped": dict(self.reaped),
            "reaped_for_other": self.reaped_for_other,
            "died_idle": self.died_idle,
            "unreaped": unreaped,
            "reaper": "ok" if reaper is not None and reaper.is_alive() else "dead",
        }


# -- building the pools ---------------------------------------------------------


def pool_width(governor: ResourceGovernor, ceiling: int) -> int:
    """Worker threads of the main pool under ``on``: the plan's lanes, or an explicit size."""
    configured = explicit_setting(legacy.WORKERS_ENV)
    if configured is not None:
        return min(configured, ceiling)
    budget = governor.lanes
    if budget is None:
        return max(1, min(legacy.DEFAULT_MAX_WORKERS, ceiling // 2))
    return budget.plan.lanes


def _build(governor: ResourceGovernor, *, interactive: bool) -> LanePool | None:
    budget = governor.lanes
    if budget is None:
        return None
    plan = budget.plan
    if interactive:
        width = interactive_workers()
        return LanePool(
            kind="interactive",
            threads=width,
            budget=None,
            leases=_CounterLeases(lambda: width),
            worker_env=worker_thread_env(threads_rule(plan.cpus, 1)),
        )
    configured = explicit_setting(legacy.WORKERS_ENV)
    if configured is not None:
        width = legacy.parse_pool_workers()
        lanes = plan.lanes

        def private_limit() -> int:
            return max(1, math.ceil(configured * budget.cap() / lanes))

        return LanePool(
            kind="explicit",
            threads=width,
            budget=budget,
            leases=_CounterLeases(private_limit),
            admit=False,
            worker_env=worker_thread_env(threads_rule(plan.cpus, configured)),
        )
    budget.register_presence()
    return LanePool(
        kind="budgeted",
        threads=plan.lanes,
        budget=budget,
        admit=True,
        worker_env=worker_thread_env(plan.threads_per_lane),
    )


def get_pool(governor: ResourceGovernor, *, interactive: bool) -> LanePool | None:
    interactive = interactive or _interactive.get()
    with legacy._State.lock:
        slot = "interactive_pool" if interactive else "pool"
        pool = getattr(legacy._State, slot)
        if pool is None:
            pool = _build(governor, interactive=interactive)
            if pool is None:
                return None
            setattr(legacy._State, slot, pool)
            _logger.info("Parse pool ready (%s): %d lane thread(s)", pool.kind, pool.workers)
        return pool


def pool_stats() -> dict[str, Any]:
    main, interactive = legacy._State.pool, getattr(legacy._State, "interactive_pool", None)
    main_stats = main.stats() if isinstance(main, LanePool) else None
    return {
        "pool": main_stats,
        "explicit_private": main.workers if isinstance(main, LanePool) and main.kind == "explicit" else None,
        "interactive": interactive.stats() if isinstance(interactive, LanePool) else None,
    }
