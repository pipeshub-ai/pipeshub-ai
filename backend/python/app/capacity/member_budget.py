"""The member budget: one CPU token budget and one memory budget shared by every process of a container.

A file ledger under one **blocking** ``flock``: a 4 KiB hot block (totals, held floors, waiter heads,
the controller block), written double-buffered with a generation and a CRC, and a process table of
256-byte slots of which an operation writes only the caller's. Contention is a short wait for the
lock, never a refusal: a ``None`` or a timeout means there is no capacity. Each process holds a
lifetime ``flock`` on ``procs/<slot>.lock``; when it dies the kernel frees the lock, and the next
reserver that needs tokens subtracts the dead slot's holdings.

The rules (who may hold what, in which order) are pure and live in :mod:`.budget_policy`; this
module is the mechanism. Stdlib only.

The ledger also elects the container's controller (:meth:`MemberBudget.elect_controller`): the
process in the slot named by the header, with an epoch that fences a stale holder, a heartbeat, and
a state block a successor resumes from.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import stat
import struct
import tempfile
import threading
import time
import weakref
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.capacity.budget_policy import (
    PRECEDENCE,
    AdmissionPolicy,
    Demand,
    Family,
    Floors,
    GrantPlan,
    LedgerState,
    WaiterHead,
    admit,
    is_critical,
    may_take,
    next_grant,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX only
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_LOG = logging.getLogger(__name__)

FAMILIES = tuple(Family)
CLASSES = ("small", "large", "huge", None)
DEMAND_POOLS = ("heavy_parse", "light_parse", "index_heavy", "index_light")
MAX_SLOTS = 256
MAX_HEADS = 48
STATE_BYTES = 1024
HOT = 4096
SLOT = 256
TABLE_OFFSET = 2 * HOT
CHANGE_OFFSET = TABLE_OFFSET + MAX_SLOTS * SLOT  # two counters (cpu, llm) bumped under the lock when capacity frees
FILE_SIZE = CHANGE_OFFSET + 64
MAGIC = 0x50484C47
DEAD_SCAN_INTERVAL_S = 0.1
WATCH_S = 0.002  # how often a process's one watcher reads the change counters (no lock taken)
REAP_S = 0.5  # while anyone waits, the watcher also looks for dead holders this often (a dead holder bumps no counter)
WAKE_MANY = 8  # a change that is not one freed token (floors, memory, the order): wake several waiters
FALLBACK_S = 0.05  # a waiter re-checks at least this often (jittered) in case a wake was missed
SPIN_TRIES = int(os.environ.get("PIPESHUB_LEDGER_SPIN", "200"))
_NF, _NC, _NK = len(FAMILIES), len(CLASSES), len(FAMILIES) * len(CLASSES)
_NFLOOR = len(PRECEDENCE)

_HDR = struct.Struct("<IQII")  # magic, generation, crc of the body, length of the body
_BODY = struct.Struct(
    f"<{_NK}i"  # used[family][class]
    "ii QQ"  # llm_used, llm_cap, next_ticket, last_critical_ms
    "i q q q q"  # cpu_tokens, mem_total, mem_charged, mem_excess, baseline_ws
    "i i Q Q"  # ctl_pid, ctl_slot, ctl_epoch, ctl_beat_ms
    f"{_NFLOOR}i ii I"  # floors, docling warm and max workers, config hash
    f"H {STATE_BYTES}s"  # controller state
    "4Q H"  # occupancy bits, number of heads
)
_CHANGES = struct.Struct("<QQ")  # cpu-side changes, llm-side changes
_HEAD = struct.Struct("<QQQfHBBB")
_DEMAND = struct.Struct("<fIIH")
_SLOT = struct.Struct(f"<iQ{_NK}iqqi16s" + "fIIH" * len(DEMAND_POOLS) + "Q")  # ..., the time demand was published
assert _SLOT.size <= SLOT
assert _BODY.size + MAX_HEADS * _HEAD.size <= HOT - _HDR.size


class AdmissionTimeout(Exception):
    """``reserve`` reached its deadline; its waiter is removed."""


class Cancelled(Exception):
    """``reserve`` was stopped by its :class:`CancelToken`."""


class CancelToken:
    """Stops a wait: set at shutdown or when a lease is lost."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set()


def _fam(f: Family) -> int:
    return FAMILIES.index(f)


def _cls(c: str | None) -> int:
    return CLASSES.index(c if c in ("small", "large", "huge") else None)


def _key(family: Family, cls: str | None) -> int:
    return _fam(family) * _NC + _cls(cls)


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _Hot:
    gen: int
    used: list[int]
    llm_used: int
    llm_cap: int
    next_ticket: int
    last_critical_ms: int
    cpu_tokens: int
    mem_total: int
    mem_charged: int
    mem_excess: int
    baseline_ws: int
    ctl_pid: int
    ctl_slot: int
    ctl_epoch: int
    ctl_beat_ms: int
    floors: list[int]
    docling_warm: int
    docling_max: int
    cfg: int
    ctl_state: bytes
    occupancy: list[int]
    heads: list[WaiterHead]
    dirty: bool = False

    @staticmethod
    def fresh() -> _Hot:
        return _Hot(0, [0] * _NK, 0, 24, 1, 0, 0, 0, 0, 0, 0, 0, -1, 0, 0, [0] * _NFLOOR, 1, 1, 0, b"", [0] * 4, [])

    def encode(self) -> bytes:
        state = self.ctl_state[:STATE_BYTES]
        body = bytearray(_BODY.pack(
            *self.used, self.llm_used, self.llm_cap, self.next_ticket, self.last_critical_ms,
            self.cpu_tokens, self.mem_total, self.mem_charged, self.mem_excess, self.baseline_ws,
            self.ctl_pid, self.ctl_slot, self.ctl_epoch, self.ctl_beat_ms,
            *self.floors, self.docling_warm, self.docling_max, self.cfg,
            len(state), state, *self.occupancy, min(len(self.heads), MAX_HEADS),
        ))
        for h in self.heads[:MAX_HEADS]:
            body += _HEAD.pack(h.ticket, h.enqueue_ms, h.mem_bytes, h.est_seconds or 0.0, h.cpu,
                               _fam(h.family), _cls(h.cls), h.proc_slot)
        body = bytes(body)
        return _HDR.pack(MAGIC, self.gen, zlib.crc32(body), len(body)) + body


def _decode(buf: bytes) -> _Hot | None:
    if len(buf) < HOT:
        return None
    magic, gen, crc, length = _HDR.unpack_from(buf, 0)
    body = buf[_HDR.size:_HDR.size + length]
    if magic != MAGIC or length < _BODY.size or _HDR.size + length > HOT or zlib.crc32(body) != crc:
        return None
    v = _BODY.unpack_from(body, 0)
    used = list(v[:_NK])
    (llm_used, llm_cap, ticket, last_crit, cpu_tokens, mem_total, charged, excess, baseline,
     ctl_pid, ctl_slot, ctl_epoch, ctl_beat) = v[_NK:_NK + 13]
    j = _NK + 13
    floors = list(v[j:j + _NFLOOR])
    warm, dmax, cfg, slen, state = v[j + _NFLOOR:j + _NFLOOR + 5]
    occ = list(v[j + _NFLOOR + 5:j + _NFLOOR + 9])
    nheads = v[j + _NFLOOR + 9]
    heads: list[WaiterHead] = []
    off = _BODY.size
    for _ in range(min(nheads, MAX_HEADS)):
        t, enq, mem, est, cpu, f, c, slot = _HEAD.unpack_from(body, off)
        off += _HEAD.size
        heads.append(WaiterHead(t, CLASSES[c], FAMILIES[f], cpu, mem, est or None, enq, slot))
    return _Hot(gen, used, llm_used, llm_cap, ticket, last_crit, cpu_tokens, mem_total, charged, excess, baseline,
                ctl_pid, ctl_slot, ctl_epoch, ctl_beat, floors, warm, dmax, cfg, state[:slen], occ, heads)


def default_directory() -> Path:
    """Where the container's processes share the ledger: per uid, ``PIPESHUB_CAPACITY_DIR`` overrides."""
    override = os.environ.get("PIPESHUB_CAPACITY_DIR", "").strip()
    return Path(override) if override else Path(tempfile.gettempdir()) / f"pipeshub-capacity-{os.getuid()}"


def _directory_problem(directory: Path) -> str | None:
    try:
        st = os.lstat(directory)
    except OSError as exc:
        return f"cannot inspect it: {exc}"
    if not stat.S_ISDIR(st.st_mode):
        return "it is not a directory (or is a link)"
    if st.st_uid != os.getuid():
        return "it belongs to another user"
    if st.st_mode & 0o022:
        return f"it is writable by others (mode {st.st_mode & 0o777:o})"
    return None


def _open(path: Path, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)


class Permit:
    """Tokens and memory held for one unit. ``release`` is idempotent; the holder's death releases it."""

    def __init__(self, budget: MemberBudget, demand: Demand) -> None:
        self._budget = budget
        self.demand = demand
        self.family = demand.family
        self.cpu = demand.cpu
        self.mem_bytes = demand.mem_bytes
        self.cls = demand.cls
        self.released = False
        self.adopted = False
        self.actual_bytes = 0
        self.owner_pid = os.getpid()  # a forked child that inherits this permit must not release the parent's tokens
        self._lock = threading.Lock()

    def adopt(self) -> Permit:
        """The lane that runs the unit takes over the release; it reserves nothing of its own."""
        self.adopted = True
        return self

    def observe_rss(self, rss_bytes: int) -> None:
        """The unit's worker RSS: memory is charged ``max(actual, estimate)`` once (read at the next refresh)."""
        self.actual_bytes = max(0, int(rss_bytes))

    def release(self) -> None:
        if os.getpid() != self.owner_pid:
            return  # inherited across a fork: the owner process still holds it (checked before the lock, which a
            # parent's other thread may have held when the fork happened)
        with self._lock:
            if self.released:
                return
            self.released = True
        self._budget._release(self)

    def __enter__(self) -> Permit:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class _Watcher:
    """Wakes this process's waiters when the ledger's capacity changes.

    One thread per process reads the two change counters (a plain ``pread``, no ledger lock) every ``WATCH_S``
    while anyone waits. A counter moves by the number of tokens (CPU side) or LLM slots (LLM side) that were freed,
    and the watcher wakes that many waiters of the side, critical ones first and then oldest first: a crowd of
    waiters costs about one attempt per freed token, not one per waiter. A waiter tries only after it is woken.
    One waiter per side is also woken per jittered ``FALLBACK_S`` tick, in turn, in case a wake was missed. A waiter
    reports the counters it read before its first attempt (``since``), so a change that lands before the watcher's
    own first read is never missed.
    """

    def __init__(self, budget: MemberBudget) -> None:
        self._budget = budget
        self._lock = threading.Lock()
        self._entries: dict[object, tuple[bool, bool, Any]] = {}  # token -> (llm, critical, wake), oldest first
        self._thread: threading.Thread | None = None
        self._last: tuple[int, int] = (0, 0)
        self._turn = {False: 0, True: 0}

    def add(self, wake: Any, *, llm: bool, critical: bool, since: tuple[int, int]) -> object:  # noqa: ANN401
        token = object()
        missed = False
        with self._lock:
            self._entries[token] = (llm, critical, wake)
            if self._thread is None or not self._thread.is_alive():
                self._last = since
                self._thread = threading.Thread(target=self._run, name="ledger-watch", daemon=True)
                self._thread.start()
            else:
                side = 1 if llm else 0
                missed = since[side] < self._last[side]  # a change the watcher already read, after this waiter's read
        if missed:
            wake()
        return token

    def remove(self, token: object) -> None:
        with self._lock:
            self._entries.pop(token, None)

    def _pick(self, entries: list[tuple[object, tuple[bool, bool, Any]]], n: int, *, llm: bool, fallback: bool) -> list[Any]:
        side = sorted((e for e in entries if e[1][0] == llm), key=lambda e: not e[1][1])  # critical first, then oldest
        if not side:
            return []
        if fallback:
            self._turn[llm] = (self._turn[llm] + 1) % len(side)
            return [side[self._turn[llm]][1][2]]
        return [e[1][2] for e in side[:n]]

    def _run(self) -> None:
        next_reap = time.monotonic() + REAP_S
        next_fallback = time.monotonic() + FALLBACK_S
        while True:
            time.sleep(WATCH_S)
            if time.monotonic() >= next_reap:
                next_reap = time.monotonic() + REAP_S
                with contextlib.suppress(Exception):
                    self._budget._reap_dead()  # a reap bumps the counters, which wakes the waiters below
            with self._lock:
                if not self._entries or self._budget._closed:
                    self._thread = None
                    return
                entries = list(self._entries.items())
                last = self._last
            try:
                cur = self._budget._changes()
            except OSError:
                continue
            wakes = self._pick(entries, llm=False, n=cur[0] - last[0], fallback=False) + self._pick(entries, llm=True, n=cur[1] - last[1], fallback=False)
            now = time.monotonic()
            if now >= next_fallback:
                next_fallback = now + FALLBACK_S * (1 + random.random())
                wakes += self._pick(entries, llm=False, n=1, fallback=True) + self._pick(entries, llm=True, n=1, fallback=True)
            with self._lock:
                self._last = cur
            for wake in wakes:
                try:
                    wake()
                except Exception:  # a callback whose loop closed must not end the watcher for everyone else
                    _LOG.debug("capacity ledger: a wake callback failed (its waiter is gone)", exc_info=True)


_HANDLES: weakref.WeakSet[MemberBudget] = weakref.WeakSet()


def _close_inherited_descriptors() -> None:
    """In a forked child, drop what the parent's handles opened, at once.

    The parent's lifetime lock lives in an open file description both processes now share; it is released only when
    every copy is closed. A child that stays alive without using the budget would otherwise keep a dead parent's slot
    looking alive. Closing a copy does not unlock the parent's lock. A child that needs the budget takes a slot of its
    own on first use (``_after_fork``).
    """
    for handle in list(_HANDLES):
        handle._drop_inherited()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_close_inherited_descriptors)


class MemberBudget:
    """One process's handle on its container's ledger."""

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        cpu_tokens: int,
        mem_bytes: int,
        floors: Floors | None = None,
        policy: AdmissionPolicy | None = None,
        llm_cap: int = 24,
        service: str = "",
    ) -> None:
        if fcntl is None:  # pragma: no cover
            raise RuntimeError("the member budget needs POSIX file locks")
        self.directory = Path(directory) if directory is not None else default_directory()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (problem := _directory_problem(self.directory)) is not None:
            raise RuntimeError(f"{self.directory} is not a safe ledger directory: {problem}")
        (self.directory / "procs").mkdir(exist_ok=True, mode=0o700)
        self.policy = policy or AdmissionPolicy()
        self.service = service[:16]
        self._initial_floors = floors
        self._want = (int(cpu_tokens), int(mem_bytes), int(llm_cap))
        self._tlock = threading.RLock()
        self._pid = os.getpid()
        self._closed = False
        self._fd = self._lockfd = self._slot_fd = -1
        self._slot = -1
        self._last_scan = 0.0
        self._holdings = [0] * _NK
        self._mem_est = 0
        self._mem_excess = 0
        self._llm = 0
        self._service_raw = self.service.encode()[:16]
        self._demand_flat: list[Any] = [0.0, 0, 0, 0] * len(DEMAND_POOLS)
        self._demand_ms = 0
        self._live: set[Permit] = set()
        self._live_lock = threading.Lock()
        self._watcher = _Watcher(self)
        _HANDLES.add(self)
        self._open_files()
        self._init_file()
        self._attach()

    # ---- file access ---------------------------------------------------------------------------

    def _open_files(self) -> None:
        self._fd = _open(self.directory / "ledger", os.O_RDWR | os.O_CREAT)
        self._lockfd = _open(self.directory / "ledger.lock", os.O_RDWR | os.O_CREAT)

    @contextlib.contextmanager
    def _txn(self) -> Iterator[_Hot]:
        self._attach_if_forked()  # before the thread lock: a lock copied from a parent's other thread would never free
        with self._tlock:
            if self._closed:
                raise RuntimeError("the member budget is closed")
            self._lock()
            try:
                hot = self._read_hot()
                yield hot
                if hot.dirty:
                    hot.gen += 1
                    os.pwrite(self._fd, hot.encode(), (hot.gen % 2) * HOT)
                    hot.dirty = False
            finally:
                fcntl.flock(self._lockfd, fcntl.LOCK_UN)

    def _lock(self) -> None:
        """Take the ledger lock: a few immediate tries (a holder keeps it for microseconds, and a sleeping waiter
        pays a wake-up many times that), then a blocking wait. Never a refusal."""
        for _ in range(SPIN_TRIES):
            try:
                fcntl.flock(self._lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                continue
        fcntl.flock(self._lockfd, fcntl.LOCK_EX)

    def _read_hot(self) -> _Hot:
        """The newest copy that passes its CRC; the other one only if the newest was torn."""
        buf = os.pread(self._fd, 2 * HOT, 0).ljust(2 * HOT, b"\0")
        gens = []
        for k in (0, 1):
            magic, gen, _, _ = _HDR.unpack_from(buf, k * HOT)
            if magic == MAGIC:
                gens.append((gen, k))
        for _, k in sorted(gens, reverse=True):
            hot = _decode(buf[k * HOT:(k + 1) * HOT])
            if hot is not None:
                return hot
        return _Hot.fresh()

    def _changes(self) -> tuple[int, int]:
        self._attach_if_forked()  # a forked child's descriptors are closed until it reattaches
        raw = os.pread(self._fd, _CHANGES.size, CHANGE_OFFSET)
        return _CHANGES.unpack(raw.ljust(_CHANGES.size, b"\0"))

    def _bump(self, *, cpu: int = 0, llm: int = 0) -> None:
        """Tell the waiters how much was freed: CPU tokens, LLM slots (under the ledger lock, so the read-add-write is
        not racy)."""
        a, b = self._changes()
        os.pwrite(self._fd, _CHANGES.pack(a + max(0, cpu), b + max(0, llm)), CHANGE_OFFSET)

    def _config_hash(self) -> int:
        return zlib.crc32(repr(self._want).encode()) or 1

    def _init_file(self) -> None:
        with self._txn() as hot:
            if os.fstat(self._fd).st_size < FILE_SIZE:
                os.ftruncate(self._fd, FILE_SIZE)
            self._reap(hot, force=True)
            alive = any(hot.occupancy)
            if hot.cpu_tokens == 0 or (hot.cfg != self._config_hash() and not alive):
                # A fresh ledger, or one of a previous start with other sizes and nobody left in it.
                gen = hot.gen
                fresh = _Hot.fresh()
                fresh.gen = gen
                fresh.cpu_tokens, fresh.mem_total, fresh.llm_cap = self._want
                fresh.cfg = self._config_hash()
                if self._initial_floors is not None:
                    self._set_floors(fresh, self._initial_floors)
                fresh.dirty = True
                hot.__dict__.update(fresh.__dict__)
            elif hot.cfg != self._config_hash():
                _LOG.warning("capacity ledger: another process fixed the budget at %d tokens; using it", hot.cpu_tokens)
            self.cpu_tokens, self.mem_total, self.llm_cap = hot.cpu_tokens, hot.mem_total, hot.llm_cap

    def _attach(self) -> None:
        with self._txn() as hot:
            self._reap(hot, force=True)
            for i in range(MAX_SLOTS):
                fd = _open(self.directory / "procs" / f"{i}.lock", os.O_RDWR | os.O_CREAT)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    continue
                self._slot, self._slot_fd = i, fd
                hot.occupancy[i // 64] |= 1 << (i % 64)
                hot.dirty = True
                self._write_slot()
                return
        raise RuntimeError("the process table is full")

    def _attach_if_forked(self) -> None:
        """A forked child reattaches (descriptors, slot, watcher) before it reads or waits on the ledger."""
        if os.getpid() != self._pid and not self._closed:
            self._after_fork()

    def _drop_inherited(self) -> None:
        """Close the descriptors a fork copied (the process id stays, so the next use sees the fork and re-attaches)."""
        if os.getpid() == self._pid:
            return
        for fd in (self._fd, self._lockfd, self._slot_fd):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
        self._fd = self._lockfd = self._slot_fd = -1
        self._tlock = threading.RLock()  # a lock a parent's other thread held would never free in this child
        self._live_lock = threading.Lock()

    def _after_fork(self) -> None:
        """A forked child shares the parent's open files; it must hold nothing of the parent's and take its own slot.

        Closing an inherited descriptor does not release the parent's lock (the lock belongs to the
        open file description the parent still holds), and the child's own are fresh.
        """
        for fd in (self._fd, self._lockfd, self._slot_fd):
            if fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd)
        self._pid = os.getpid()
        self._holdings = [0] * _NK
        self._mem_est = self._mem_excess = self._llm = 0
        self._demand_flat = [0.0, 0, 0, 0] * len(DEMAND_POOLS)
        self._demand_ms = 0
        self._live = set()
        self._live_lock = threading.Lock()
        self._tlock = threading.RLock()
        self._watcher = _Watcher(self)
        self._slot = self._slot_fd = -1
        self._open_files()
        self._attach()

    def _slot_offset(self, i: int) -> int:
        return TABLE_OFFSET + i * SLOT

    def _write_slot(self) -> None:
        raw = _SLOT.pack(self._pid, _now_ms(), *self._holdings, self._mem_est, self._mem_excess, self._llm,
                         self._service_raw, *self._demand_flat, self._demand_ms)
        os.pwrite(self._fd, raw, self._slot_offset(self._slot))

    def _read_slot(self, i: int) -> dict[str, Any]:
        v = _SLOT.unpack_from(os.pread(self._fd, SLOT, self._slot_offset(i)), 0)
        n = 2 + _NK
        demand = {}
        for k, pool in enumerate(DEMAND_POOLS):
            o = n + 4 + 4 * k
            demand[pool] = (float(v[o]), int(v[o + 1]), int(v[o + 2]), int(v[o + 3]))
        return {"pid": v[0], "beat_ms": v[1], "demand_ms": int(v[-1]), "holdings": list(v[2:n]), "mem": v[n],
                "excess": v[n + 1], "llm": v[n + 2], "service": v[n + 3].rstrip(b"\0").decode(errors="replace"), "demand": demand}

    def close(self) -> None:
        """Release what this process holds and give up its slot and the controller role."""
        with self._tlock:
            if self._closed:
                return
            if os.getpid() == self._pid and self._slot_fd >= 0:
                with self._txn() as hot:
                    self._drop_slot(hot, self._slot, self._holdings, self._mem_est, self._mem_excess, self._llm)
            self._closed = True
            for fd in (self._slot_fd, self._fd, self._lockfd):
                if fd >= 0:  # a forked child closes its copies too: they must not keep the parent's lock alive
                    with contextlib.suppress(OSError):
                        os.close(fd)
            self._slot_fd = self._fd = self._lockfd = -1

    # ---- dead holders ---------------------------------------------------------------------------

    def _drop_slot(self, hot: _Hot, i: int, holdings: list[int], mem: int, excess: int, llm: int) -> None:
        for k, n in enumerate(holdings):
            hot.used[k] = max(0, hot.used[k] - n)
        hot.mem_charged = max(0, hot.mem_charged - mem)
        hot.mem_excess = max(0, hot.mem_excess - excess)
        hot.llm_used = max(0, hot.llm_used - llm)
        hot.heads = [h for h in hot.heads if h.proc_slot != i]
        hot.occupancy[i // 64] &= ~(1 << (i % 64))
        if hot.ctl_slot == i:
            hot.ctl_pid, hot.ctl_slot = 0, -1
        os.pwrite(self._fd, bytes(SLOT), self._slot_offset(i))
        hot.dirty = True
        self._bump(cpu=max(1, sum(holdings)), llm=llm)

    def _reap(self, hot: _Hot, *, force: bool = False) -> int:
        """Return the holdings of processes that died: their lifetime lock is free."""
        now = time.monotonic()
        if not force and now - self._last_scan < DEAD_SCAN_INTERVAL_S:
            return 0
        self._last_scan = now
        reaped = 0
        for i in range(MAX_SLOTS):
            if i == self._slot or not hot.occupancy[i // 64] >> (i % 64) & 1:
                continue
            try:
                fd = _open(self.directory / "procs" / f"{i}.lock", os.O_RDWR)
            except OSError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue  # still held: the process is alive
                s = self._read_slot(i)
                self._drop_slot(hot, i, s["holdings"], s["mem"], s["excess"], s["llm"])
                reaped += 1
            finally:
                os.close(fd)
        if reaped:
            self._reconcile(hot)
        return reaped

    def _reap_dead(self) -> int:
        """Return the holdings of processes that died, for a waiter that no reservation attempt would reach.

        The look for a dead holder takes no ledger lock (it reads the hot block, which is checksummed, and tries each
        occupied slot's lifetime lock); the ledger lock is taken only when someone is found.
        """
        hot = self._read_hot()
        dead = False
        for i in range(MAX_SLOTS):
            if i == self._slot or not hot.occupancy[i // 64] >> (i % 64) & 1:
                continue
            try:
                fd = _open(self.directory / "procs" / f"{i}.lock", os.O_RDWR)
            except OSError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue  # still held: alive
                dead = True
            finally:
                os.close(fd)
            if dead:
                break
        if not dead:
            return 0
        with self._txn() as locked:
            return self._reap(locked, force=True)

    def _reconcile(self, hot: _Hot) -> None:
        """Rebuild the totals from the live slots. A slot is the single source of truth for what its process holds:
        a process that died between writing its slot and the hot block would otherwise leave the two disagreeing
        (tokens leaked, or tokens granted twice)."""
        used = [0] * _NK
        mem = excess = llm = 0
        for i in range(MAX_SLOTS):
            if not hot.occupancy[i // 64] >> (i % 64) & 1:
                continue
            s = self._read_slot(i)
            for k, n in enumerate(s["holdings"]):
                used[k] += n
            mem += s["mem"]
            excess += s["excess"]
            llm += s["llm"]
        if (used, mem, excess, llm) != (hot.used, hot.mem_charged, hot.mem_excess, hot.llm_used):
            _LOG.warning("capacity ledger: totals rebuilt from the process table after a holder died")
            hot.used, hot.mem_charged, hot.mem_excess, hot.llm_used = used, mem, excess, llm
            hot.dirty = True

    # ---- floors and shared settings -------------------------------------------------------------

    @staticmethod
    def _set_floors(hot: _Hot, floors: Floors) -> None:
        hot.floors = [int(floors.of(f)) for f in PRECEDENCE]
        hot.docling_warm = max(1, int(floors.docling_warm_workers))
        hot.docling_max = max(1, int(floors.docling_max_workers))

    @staticmethod
    def _floors(hot: _Hot) -> Floors:
        by = {f: hot.floors[i] for i, f in enumerate(PRECEDENCE)}
        return Floors(by, by, hot.docling_warm, hot.docling_max)

    def set_floors(self, floors: Floors, *, epoch: int | None = None) -> bool:
        """Publish the held floors (container-wide). With ``epoch`` the write is fenced to the controller."""
        with self._txn() as hot:
            if epoch is not None and (epoch != hot.ctl_epoch or hot.ctl_slot != self._slot):
                return False
            self._set_floors(hot, floors)
            hot.dirty = True
            self._bump(cpu=WAKE_MANY)
            return True

    def floors(self) -> Floors:
        with self._txn() as hot:
            return self._floors(hot)

    def set_baseline(self, baseline_ws: int) -> None:
        with self._txn() as hot:
            hot.baseline_ws = max(0, int(baseline_ws))
            hot.dirty = True
            self._bump(cpu=WAKE_MANY)

    # ---- reserve and release --------------------------------------------------------------------

    def _state(self, hot: _Hot) -> LedgerState:
        used = {(FAMILIES[k // _NC], CLASSES[k % _NC]): n for k, n in enumerate(hot.used) if n}
        return LedgerState(used=used, heads=list(hot.heads), llm_used=hot.llm_used, llm_cap=hot.llm_cap,
                           last_critical_ms=hot.last_critical_ms or None)

    @staticmethod
    def _mem_avail(hot: _Hot) -> int:
        """``M - baseline_ws - sum(max(actual_i, est_i))``: estimates plus what units exceed theirs by."""
        return max(0, hot.mem_total - hot.baseline_ws - hot.mem_charged - hot.mem_excess)

    def _candidate(self, hot: _Hot, d: Demand, now_ms: int) -> WaiterHead:
        return WaiterHead(hot.next_ticket, d.cls, d.family, d.cpu, d.mem_bytes, d.est_seconds, now_ms, self._slot)

    def _attempt(self, hot: _Hot, d: Demand, head: WaiterHead | None) -> bool:
        """Decided under the ledger lock by ``next_grant`` (order) and ``admit`` (capacity)."""
        now_ms = _now_ms()
        state = self._state(hot)
        floors = self._floors(hot)
        if d.family is not Family.LLM and (head is not None or hot.heads):
            # With no waiter in the ledger a new demand is trivially first, so only a contended ledger needs the order.
            candidate = head or self._candidate(hot, d, now_ms)
            probe = state if head else LedgerState(state.used, [*state.heads, candidate], state.llm_used,
                                                   state.llm_cap, state.last_critical_ms)
            plan = next_grant(probe, floors, self.policy, hot.cpu_tokens, now_ms / 1000)
            if not may_take(probe, candidate, plan, hot.cpu_tokens):
                return False
        if not admit(state, d, floors, self.policy, hot.cpu_tokens, self._mem_avail(hot), now_ms / 1000).ok:
            return False
        if d.family is Family.LLM:
            hot.llm_used += 1
            self._llm += 1
        else:
            k = _key(d.family, d.cls)
            hot.used[k] += d.cpu
            self._holdings[k] += d.cpu
        hot.mem_charged += d.mem_bytes
        self._mem_est += d.mem_bytes
        if is_critical(d.family, d.cls):
            hot.last_critical_ms = now_ms
        if head is not None:
            hot.heads = [h for h in hot.heads if h.ticket != head.ticket]
        hot.dirty = True
        self._write_slot()
        if hot.heads:
            self._bump(cpu=1)  # a head left the line (or tokens were taken): let the next one look
        return True

    def _take(self, d: Demand, head: WaiterHead | None) -> Permit | None:
        with self._txn() as hot:
            if self._attempt(hot, d, head) or (self._reap(hot) and self._attempt(hot, d, head)):
                permit = Permit(self, d)
                with self._live_lock:
                    self._live.add(permit)
                return permit
        return None

    def try_reserve(self, d: Demand) -> Permit | None:
        """A permit, or ``None`` when there is no capacity or the order does not allow it now. Never a refusal for contention."""
        return self._take(d, None)

    def try_take(self, d: Demand, head: WaiterHead) -> Permit | None:
        """One attempt for a registered head (a process-local queue calls this for the head it feeds)."""
        return self._take(d, head)

    def register_head(self, d: Demand) -> WaiterHead | None:
        """Put this process's waiter into the ledger's order; ``None`` when the head table is full."""
        with self._txn() as hot:
            if len(hot.heads) >= MAX_HEADS:
                return None
            head = self._candidate(hot, d, _now_ms())
            hot.next_ticket += 1
            hot.heads.append(head)
            hot.dirty = True
            return head

    def withdraw_head(self, head: WaiterHead | None) -> None:
        if head is None:
            return
        with self._txn() as hot:
            if any(h.ticket == head.ticket for h in hot.heads):
                hot.heads = [h for h in hot.heads if h.ticket != head.ticket]
                hot.dirty = True
                self._bump(cpu=1)  # the line moved up: the next waiter may fit

    def _release(self, permit: Permit) -> None:
        d = permit.demand
        with self._live_lock:
            self._live.discard(permit)
        if self._closed:
            return
        with self._txn() as hot:
            if d.family is Family.LLM:
                hot.llm_used = max(0, hot.llm_used - 1)
                self._llm = max(0, self._llm - 1)
            else:
                k = _key(d.family, d.cls)
                hot.used[k] = max(0, hot.used[k] - d.cpu)
                self._holdings[k] = max(0, self._holdings[k] - d.cpu)
            hot.mem_charged = max(0, hot.mem_charged - d.mem_bytes)
            self._mem_est = max(0, self._mem_est - d.mem_bytes)
            self._apply_excess(hot)
            hot.dirty = True
            self._write_slot()
            self._bump(cpu=0 if d.family is Family.LLM else max(1, d.cpu), llm=int(d.family is Family.LLM))

    def _apply_excess(self, hot: _Hot) -> None:
        with self._live_lock:
            live = list(self._live)
        excess = sum(max(0, p.actual_bytes - p.mem_bytes) for p in live)
        hot.mem_excess = max(0, hot.mem_excess - self._mem_excess + excess)
        self._mem_excess = excess

    def refresh_memory(self) -> int:
        """Charge each live unit ``max(actual, estimate)``: the part of the RSS above the estimate is added once."""
        with self._txn() as hot:
            self._apply_excess(hot)
            hot.dirty = True
            self._write_slot()
            self._bump(cpu=WAKE_MANY)
            return self._mem_excess

    @staticmethod
    def _check_wait(deadline_s: float | None, cancel: CancelToken | None) -> None:
        if deadline_s is None and cancel is None:
            raise ValueError("reserve needs a deadline or a cancel token: an unbounded wait cannot be stopped")

    @staticmethod
    def _check_stop(d: Demand, cancel: CancelToken | None, end: float | None, deadline_s: float | None) -> None:
        if cancel is not None and cancel.cancelled():
            raise Cancelled("reserve cancelled")
        if end is not None and time.monotonic() >= end:
            raise AdmissionTimeout(f"no {d.family.value} capacity within {deadline_s}s")

    async def reserve(self, d: Demand, *, deadline_s: float | None = None, cancel: CancelToken | None = None) -> Permit:
        """Wait for capacity in the ledger's order. ``AdmissionTimeout`` at the deadline, ``Cancelled`` on cancel.

        A waiter sleeps until the ledger frees something (see :class:`_Watcher`); it does not poll. The first
        attempt is immediate; each later one follows a wake, and a cancel or deadline is honoured before it.
        """
        self._check_wait(deadline_s, cancel)
        self._attach_if_forked()
        end = None if deadline_s is None else time.monotonic() + deadline_s
        head: WaiterHead | None = None
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        llm = d.family is Family.LLM
        token = self._watcher.add(
            lambda: loop.call_soon_threadsafe(event.set), llm=llm, critical=is_critical(d.family, d.cls), since=self._changes()
        )
        try:
            woken = True
            first = True
            while True:
                if not first:
                    self._check_stop(d, cancel, end, deadline_s)
                if woken:
                    event.clear()  # before the attempt: a change that lands after it is not lost
                    permit = self._take(d, head)
                    if permit is not None:
                        head = None
                        return permit
                    if head is None and not llm:
                        head = self.register_head(d)  # LLM demands are not ordered, so they take no place
                first = False
                left = None if end is None else end - time.monotonic()
                if left is not None and left <= 0:
                    self._check_stop(d, cancel, end, deadline_s)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), FALLBACK_S * 2 if left is None else min(FALLBACK_S * 2, left))
                woken = event.is_set()
        finally:
            self._watcher.remove(token)
            self.withdraw_head(head)

    def reserve_blocking(self, d: Demand, *, deadline_s: float | None = None, cancel: CancelToken | None = None) -> Permit:
        """``reserve`` for a thread."""
        self._check_wait(deadline_s, cancel)
        self._attach_if_forked()
        end = None if deadline_s is None else time.monotonic() + deadline_s
        head: WaiterHead | None = None
        event = threading.Event()
        llm = d.family is Family.LLM
        token = self._watcher.add(event.set, llm=llm, critical=is_critical(d.family, d.cls), since=self._changes())
        try:
            woken = True
            first = True
            while True:
                if not first:
                    self._check_stop(d, cancel, end, deadline_s)
                if woken:
                    event.clear()
                    permit = self._take(d, head)
                    if permit is not None:
                        head = None
                        return permit
                    if head is None and not llm:
                        head = self.register_head(d)
                first = False
                left = None if end is None else end - time.monotonic()
                if left is not None and left <= 0:
                    self._check_stop(d, cancel, end, deadline_s)
                woken = event.wait(FALLBACK_S * 2 if left is None else min(FALLBACK_S * 2, left))
        finally:
            self._watcher.remove(token)
            self.withdraw_head(head)

    def grant_plan(self) -> GrantPlan:
        """Who is served next, from the ledger's waiters (class priority, owner first, head of line)."""
        with self._txn() as hot:
            return next_grant(self._state(hot), self._floors(hot), self.policy, hot.cpu_tokens, _now_ms() / 1000)

    # ---- demand ---------------------------------------------------------------------------------

    def publish_demand(self, demand: Mapping[str, tuple[float, int, int, int]]) -> None:
        """This process's last interval per pool: ``(permit_seconds, blocked, completions, max_in_use)``."""
        flat: list[Any] = []
        for pool in DEMAND_POOLS:
            v = demand.get(pool, (0.0, 0, 0, 0))
            flat.extend((float(v[0]), max(0, int(v[1])), max(0, int(v[2])), min(65535, max(0, int(v[3])))))
        self._demand_flat = flat
        self._demand_ms = _now_ms()
        with self._txn():
            self._write_slot()

    def container_demand(self, *, max_age_ms: int | None = None, now_ms: int | None = None) -> dict[str, tuple[float, int, int, int]]:
        """The sum of every live process's published demand, this process's included."""
        now = _now_ms() if now_ms is None else now_ms
        total = {p: [0.0, 0, 0, 0] for p in DEMAND_POOLS}
        with self._txn() as hot:
            for i in range(MAX_SLOTS):
                if not hot.occupancy[i // 64] >> (i % 64) & 1:
                    continue
                s = self._read_slot(i)
                if max_age_ms is not None and now - s["demand_ms"] > max_age_ms:
                    continue
                for p, (ps, blocked, done, peak) in s["demand"].items():
                    t = total[p]
                    t[0] += ps
                    t[1] += blocked
                    t[2] += done
                    t[3] += peak
        return {p: (t[0], t[1], t[2], t[3]) for p, t in total.items()}

    # ---- the controller role --------------------------------------------------------------------

    def elect_controller(self, tick_s: float, now_ms: int | None = None) -> int | None:
        """This process's epoch if it is the controller after this call, else ``None``.

        Takeover happens when there is no controller (its process died: its slot was reaped) or its
        heartbeat is older than two ticks (it hangs). The epoch goes up on every takeover, so the old
        holder's writes are fenced.
        """
        now = _now_ms() if now_ms is None else now_ms
        with self._txn() as hot:
            self._reap(hot, force=True)
            if hot.ctl_slot == self._slot and hot.ctl_pid == os.getpid() and now - hot.ctl_beat_ms <= 2 * tick_s * 1000:
                return hot.ctl_epoch
            held = hot.ctl_slot >= 0
            if held and hot.ctl_slot != self._slot and now - hot.ctl_beat_ms <= 2 * tick_s * 1000:
                return None
            hot.ctl_pid, hot.ctl_slot, hot.ctl_beat_ms = os.getpid(), self._slot, now
            hot.ctl_epoch += 1
            hot.dirty = True
            return hot.ctl_epoch

    def controller_beat(self, epoch: int, state: Mapping[str, Any] | None = None, now_ms: int | None = None) -> bool:
        """Fenced heartbeat and state write. ``False`` when a newer epoch exists: the caller must stop."""
        raw = None if state is None else json.dumps(state, separators=(",", ":"), sort_keys=True).encode()
        if raw is not None and len(raw) > STATE_BYTES:
            raise ValueError(f"controller state is {len(raw)} bytes; its block holds {STATE_BYTES}")
        with self._txn() as hot:
            if epoch != hot.ctl_epoch or hot.ctl_slot != self._slot:
                return False
            hot.ctl_beat_ms = _now_ms() if now_ms is None else now_ms
            if raw is not None:
                hot.ctl_state = raw
            hot.dirty = True
            return True

    def controller(self) -> dict[str, Any]:
        with self._txn() as hot:
            state = json.loads(hot.ctl_state) if hot.ctl_state else None
            return {"pid": hot.ctl_pid, "epoch": hot.ctl_epoch, "beat_ms": hot.ctl_beat_ms, "slot": hot.ctl_slot,
                    "state": state}

    def resign_controller(self, epoch: int) -> None:
        with self._txn() as hot:
            if epoch == hot.ctl_epoch and hot.ctl_slot == self._slot:
                hot.ctl_pid, hot.ctl_slot = 0, -1
                hot.dirty = True

    # ---- observation ----------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Used, waiting and floor per family and class, memory, the LLM count and the controller."""
        with self._txn() as hot:
            state = self._state(hot)
            floors = self._floors(hot)
            used = {f.value: (hot.llm_used if f is Family.LLM else state.family_used(f)) for f in Family}
            waiting = {f.value: 0 for f in Family}
            waiting_by_class: dict[str, int] = {}
            for h in hot.heads:
                waiting[h.family.value] += 1
                waiting_by_class[h.cls or "none"] = waiting_by_class.get(h.cls or "none", 0) + 1
            by_class = {f"{f.value}/{c or 'none'}": n for (f, c), n in state.used.items()}
            now = _now_ms()
            return {
                "cpu_tokens": hot.cpu_tokens, "used": state.total(), "critical_used": state.critical_used(),
                "big_used": state.big_used(),
                "mem_total": hot.mem_total, "mem_charged": hot.mem_charged, "mem_excess": hot.mem_excess,
                "baseline_ws": hot.baseline_ws, "mem_available": self._mem_avail(hot),
                "families": {f: {"used": used[f], "waiting": waiting[f]} for f in used},
                "by_class": by_class, "waiting_by_class": waiting_by_class,
                "held_floor": floors.total, "floors": {f.value: floors.of(f) for f in PRECEDENCE},
                "docling_workers": {"warm": hot.docling_warm, "max": hot.docling_max},
                "llm": {"used": hot.llm_used, "cap": hot.llm_cap},
                "controller": {"pid": hot.ctl_pid, "epoch": hot.ctl_epoch,
                               "beat_age_ms": max(0, now - hot.ctl_beat_ms) if hot.ctl_slot >= 0 else None},
                "processes": sum(bin(w).count("1") for w in hot.occupancy),
                "generation": hot.gen,
            }
