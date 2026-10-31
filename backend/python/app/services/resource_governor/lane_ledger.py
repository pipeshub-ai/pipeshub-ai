"""The domain ledger: the brake and the spawn window that every process in one
memory domain shares, kept in a single small file.

One 128-byte record, written with one ``pwrite`` under a lock file and read
with one ``pread``. Everything that can go wrong with shared mutable state is
handled here rather than at the callers:

* a record that is missing, short, zero-filled, torn or of another version
  reads as "no cap", never as a cap of zero;
* the lock is only ever tried (``LOCK_NB``) for a bounded time, so a stopped
  or slow holder can never block a caller, least of all an event loop;
* a cap that nobody has refreshed goes stale, and one whose owner died goes
  stale sooner, so a restart does not inherit an old incident;
* a halved cap recovers with the clock, so recovery needs no sampling loop.

The functions at the top are pure; the file handling is below them.
"""
from __future__ import annotations

import contextlib
import errno
import logging
import os
import struct
import threading
import time
import zlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.services.resource_governor.policy import (
    INCIDENT_COOLDOWN_SECONDS,
    MEM_HARD,
    MEM_SOFT,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

LANE_CAP_STALE_SECONDS = 300.0
LANE_BRAKE_PERIOD_SECONDS = 15.0
# GROW_BAND defaults to 0, so it cannot give the brake any hysteresis.
LANE_RECOVERY_BAND = 0.05
LEDGER_LOCK_BUDGET = 0.020
LEDGER_FALLBACK_SECONDS = 2.0
LEDGER_VIEW_TTL = 1.0
LOCAL_OVERLAY_SECONDS = 60.0
# Several processes sample every ~15 s with jitter; one step per 13 s in the union.
BRAKE_SPACING_SECONDS = LANE_BRAKE_PERIOD_SECONDS - 2.0

NO_CAP = 0xFFFFFFFF
_MAGIC = b"PXLEDGR\0"
_VERSION = 1
RECORD_SIZE = 128
_HEADER = struct.Struct("<8sII")
_BODY = struct.Struct("<IIdIdIddId")


@dataclass(frozen=True)
class LedgerRecord:
    plan_lanes: int = 0
    cap: int = NO_CAP
    cap_stamp: float = 0.0
    cap_owner_pid: int = 0
    cooldown_until: float = 0.0
    cooldown_owner_pid: int = 0
    last_brake_at: float = 0.0
    window_start: float = 0.0
    spawns_in_window: int = 0
    reap_request_at: float = 0.0

    def encode(self) -> bytes:
        body = _BODY.pack(
            self.plan_lanes,
            self.cap,
            self.cap_stamp,
            self.cap_owner_pid,
            self.cooldown_until,
            self.cooldown_owner_pid,
            self.last_brake_at,
            self.window_start,
            self.spawns_in_window,
            self.reap_request_at,
        )
        raw = bytearray(RECORD_SIZE)
        _HEADER.pack_into(raw, 0, _MAGIC, _VERSION, 0)
        raw[_HEADER.size : _HEADER.size + len(body)] = body
        _HEADER.pack_into(raw, 0, _MAGIC, _VERSION, zlib.crc32(bytes(raw)))
        return bytes(raw)

    @classmethod
    def decode(cls, data: bytes) -> LedgerRecord | None:
        """The record, or ``None`` for anything that is not a whole, valid one."""
        if len(data) != RECORD_SIZE:
            return None
        magic, version, crc = _HEADER.unpack_from(data, 0)
        if magic != _MAGIC or version != _VERSION:
            return None
        zeroed = bytearray(data)
        _HEADER.pack_into(zeroed, 0, magic, version, 0)
        if zlib.crc32(bytes(zeroed)) != crc:
            return None
        values = _BODY.unpack_from(data, _HEADER.size)
        return cls(*values)


BLANK = LedgerRecord()


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Whether *pid* exists. ``EPERM`` means it does, in another account."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def cap_is_live(record: LedgerRecord, now: float, alive: Callable[[int], bool]) -> bool:
    """False for a record that holds no cap, or one nobody refreshes any more."""
    if record.cap == NO_CAP:
        return False
    age = now - record.cap_stamp
    if age < -1.0 or age > LANE_CAP_STALE_SECONDS:
        return False
    return not (age > INCIDENT_COOLDOWN_SECONDS and not alive(record.cap_owner_pid))


def effective_cap(
    record: LedgerRecord,
    plan_lanes: int,
    now: float,
    pressure: Callable[[], float | None],
    alive: Callable[[int], bool] = pid_alive,
) -> tuple[int, bool]:
    """``(cap_eff, cooldown_active)`` for a process whose plan is *plan_lanes*.

    A pure function of the record, the clock and one memory reading, which is
    only taken when a live cap could recover. Each process applies
    ``min(own plan, record cap)``: the record's plan field never decides.
    """
    if not cap_is_live(record, now, alive):
        return plan_lanes, False
    base = min(plan_lanes, record.cap)
    if now < record.cooldown_until:
        return base, True
    if base >= plan_lanes:
        return plan_lanes, False
    reading = pressure()
    if reading is not None and reading >= MEM_SOFT - LANE_RECOVERY_BAND:
        return base, False
    elapsed = now - max(record.cap_stamp, record.cooldown_until)
    steps = int(max(0.0, elapsed) // LANE_BRAKE_PERIOD_SECONDS)
    step = max(1, plan_lanes // 4)
    return min(plan_lanes, base + steps * step), False


def next_lane_cap(cap_eff: int, pressure: float | None) -> int | None:
    """The cap after one brake step, or ``None`` when nothing should move.

    One lane down from ``MEM_SOFT``, half from ``MEM_HARD``. Between
    ``MEM_SOFT - LANE_RECOVERY_BAND`` and ``MEM_SOFT`` nothing moves, so the
    cap does not saw-tooth.
    """
    if pressure is None or pressure < MEM_SOFT:
        return None
    if pressure >= MEM_HARD:
        return max(1, cap_eff // 2)
    return max(1, cap_eff - 1)


@dataclass(frozen=True)
class SpawnDecision:
    admit: bool
    window_start: float
    spawns_in_window: int
    reason: str


def spawn_allowance(plan_lanes: int) -> int:
    return max(2, plan_lanes // 4)


def decide_spawn(
    *,
    held_before: int,
    window_start: float,
    spawns_in_window: int,
    now: float,
    plan_lanes: int,
    raw_working_set: int | None,
    mem_limit: int | None,
    ws_bytes: int,
    warmup_seconds: float,
    allowance: int | None = None,
) -> SpawnDecision:
    """Whether one more worker may start now.

    *held_before* is the number of resident tokens held by others when this
    caller's own token was taken. With none, the spawn is admitted whatever
    the headroom: the same floor of one that the heavy gate has, so a small
    busy box never refuses its first worker. Otherwise a worker still loading
    is charged at *ws_bytes* because the memory reading cannot show it yet.
    """
    if held_before == 0 or now - window_start >= warmup_seconds or now < window_start - 1.0:
        window_start, spawns_in_window = now, 0
    if held_before == 0:
        return SpawnDecision(True, window_start, spawns_in_window + 1, "first")
    limit = spawn_allowance(plan_lanes) if allowance is None else allowance
    if spawns_in_window >= limit:
        return SpawnDecision(False, window_start, spawns_in_window, "allowance")
    if mem_limit is not None and mem_limit > 0:
        if raw_working_set is None:
            return SpawnDecision(False, window_start, spawns_in_window, "unknown_memory")
        charged = raw_working_set + (spawns_in_window + 1) * ws_bytes
        if charged / mem_limit >= MEM_SOFT:
            return SpawnDecision(False, window_start, spawns_in_window, "headroom")
    return SpawnDecision(True, window_start, spawns_in_window + 1, "headroom_ok")


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


class LedgerTxn:
    """Access to the record while the ledger lock is held."""

    def __init__(self, ledger: DomainLedger) -> None:
        self._ledger = ledger

    def read(self) -> LedgerRecord:
        """The record as it is now. Only a read made under the lock may decide
        a bad record is uninitialised, and that reads as ``BLANK``."""
        data = os.pread(self._ledger._record_fd, RECORD_SIZE, 0)
        record = LedgerRecord.decode(data)
        if record is None:
            record = BLANK
        self._ledger._remember(record)
        return record

    def write(self, record: LedgerRecord) -> None:
        os.pwrite(self._ledger._record_fd, record.encode(), 0)
        self._ledger._remember(record)


class DomainLedger:
    def __init__(
        self,
        directory: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        view_ttl: float = LEDGER_VIEW_TTL,
        lock_budget: float = LEDGER_LOCK_BUDGET,
        fallback_seconds: float = LEDGER_FALLBACK_SECONDS,
    ) -> None:
        if fcntl is None:
            raise OSError("the ledger needs POSIX file locks")
        self._clock = clock
        self._view_ttl = view_ttl
        self._lock_budget = lock_budget
        self._fallback_seconds = fallback_seconds
        self._thread_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cache: tuple[float, LedgerRecord] | None = None
        self._last_good: LedgerRecord | None = None
        self._fail_since: float | None = None
        self._closed = False
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._record_fd = os.open(directory / "ledger", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self._lock_fd = os.open(directory / "ledger.lock", os.O_RDWR | os.O_CREAT, 0o600)
        except BaseException:
            os.close(self._record_fd)
            raise

    # -- state shared by readers -------------------------------------------------

    def _remember(self, record: LedgerRecord) -> None:
        with self._state_lock:
            self._last_good = record
            self._cache = (self._clock(), record)

    @property
    def degraded(self) -> bool:
        """True after the lock has failed continuously for the fallback time.

        Per process, not per call: any success by any thread resets it, so
        many threads polling a busy lock do not trip it.
        """
        with self._state_lock:
            since = self._fail_since
        return since is not None and self._clock() - since >= self._fallback_seconds

    def _note_lock_result(self, *, ok: bool) -> None:
        with self._state_lock:
            if ok:
                self._fail_since = None
            elif self._fail_since is None:
                self._fail_since = self._clock()

    # -- the lock ----------------------------------------------------------------

    @contextlib.contextmanager
    def transaction(self) -> Iterator[LedgerTxn | None]:
        """The ledger lock, tried for at most ``lock_budget``; ``None`` if busy."""
        if self._closed:
            yield None
            return
        # Once degraded, one non-blocking try per call: no retry loop, so many
        # callers do not each spend the budget on a lock that is known to be stuck,
        # and the first success returns the process to shared mode.
        budget = 0.0 if self.degraded else self._lock_budget
        deadline = time.monotonic() + budget
        if not self._thread_lock.acquire(timeout=budget):
            self._note_lock_result(ok=False)
            yield None
            return
        locked = False
        try:
            while True:
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                        break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.001)
            self._note_lock_result(ok=locked)
            yield LedgerTxn(self) if locked else None
        finally:
            if locked:
                with contextlib.suppress(OSError):
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._thread_lock.release()

    # -- unlocked reads ----------------------------------------------------------

    def read(self) -> LedgerRecord:
        """The record as of at most ``view_ttl`` ago, without waiting.

        A read that fails its checksum is retried; if every try fails the
        reader asks once under the lock, and failing that keeps the last good
        view (or "no cap" if it never had one). It never turns a torn read
        into a lifted brake.
        """
        now = self._clock()
        with self._state_lock:
            cache = self._cache
            if self._closed:
                return self._last_good or BLANK
        if cache is not None and 0 <= now - cache[0] < self._view_ttl:
            return cache[1]
        for _ in range(3):
            record = LedgerRecord.decode(os.pread(self._record_fd, RECORD_SIZE, 0))
            if record is not None:
                self._remember(record)
                return record
        with self.transaction() as txn:
            if txn is not None:
                return txn.read()
        with self._state_lock:
            return self._last_good or BLANK

    def invalidate(self) -> None:
        with self._state_lock:
            self._cache = None

    def close(self) -> None:
        self._closed = True
        with self._thread_lock:
            for fd in (self._record_fd, self._lock_fd):
                with contextlib.suppress(OSError):
                    os.close(fd)


# ---------------------------------------------------------------------------
# Live processes
# ---------------------------------------------------------------------------


class Presence:
    """One file per live process, ``presence/<pid>``, locked while it lives.

    A file whose lock can be taken belongs to a dead process. This is how the
    degraded mode splits the budget without a ledger: slot files record no
    pids, so they cannot say who is alive.
    """

    def __init__(self, directory: Path, pid: int | None = None) -> None:
        self._directory = directory / "presence"
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        pid = os.getpid() if pid is None else pid
        self._fd: int | None = None
        for attempt in range(64):
            name = str(pid) if attempt == 0 else f"{pid}.{attempt}"
            fd = os.open(self._directory / name, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            self._fd = fd
            self.name = name
            return
        raise OSError("no free presence file")

    def live_count(self) -> int:
        """Processes holding a presence file, removing the files of dead ones."""
        live = 0
        try:
            names = os.listdir(self._directory)
        except OSError:
            return 1
        for name in names:
            path = self._directory / name
            try:
                fd = os.open(path, os.O_RDWR)
            except OSError:
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                live += 1
            else:
                with contextlib.suppress(OSError):
                    path.unlink()
            finally:
                os.close(fd)
        return max(1, live)

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


def with_plan(record: LedgerRecord, plan_lanes: int) -> LedgerRecord:
    """Note the smallest plan seen; informational, it never resets anything."""
    seen = plan_lanes if record.plan_lanes == 0 else min(record.plan_lanes, plan_lanes)
    return replace(record, plan_lanes=seen)
