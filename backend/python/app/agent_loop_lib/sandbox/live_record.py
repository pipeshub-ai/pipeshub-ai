"""The live limit's shared record: one 256-byte file every query worker reads.

Written with one ``pwrite`` under a lock file and read with one ``pread``,
with the same checksum scheme as the indexing lane ledger. A record that is
missing, short, torn or of another version reads as ``None``; the reader
then keeps its last good view, so a torn read never lifts a limit.

``limit == 0`` means no step has been taken yet: a per-run check may already
have written the burst charge, and the first sampler step initialises the
rest. All times are ``time.monotonic()``, which every process in one
container shares. The file can outlive a host reboot (a container restarted
in place keeps its ``/tmp``) while the clock starts again, so a reader treats
a time too far ahead of now as left over from before.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import math
import os
import struct
import threading
import time
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.resource_governor.shared_slots import (
    ensure_private_directory,
    file_replaced,
    open_private_file,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

__all__ = ["BURST_SLOTS", "LiveLedger", "LiveRecord", "LiveTxn", "RULES"]

logger = logging.getLogger(__name__)

RECORD_SIZE = 256
_MAGIC = b"PXSBXLV\0"
_VERSION = 2
_HEADER = struct.Struct("<8sII")
# limit ceiling min target slow_start_step cpu_high_streak (H), rule
# shrink_cause in_slow_start (B), owner_pid wait_events (I), stamp
# last_step_at cooldown_until last_wait_limit_at last_wait_mem_at sample_at
# last_valid_at (d), mem_total mem_available cpu_busy cpu_total (Q).
_BODY = struct.Struct("<6H3B2I7d4Q")
# One burst charge: until (d), bytes (Q).
_CHARGE = struct.Struct("<dQ")
BURST_SLOTS = 8
assert _HEADER.size + _BODY.size + BURST_SLOTS * _CHARGE.size <= RECORD_SIZE

RULES = (
    "", "start", "mem_hard", "mem_target", "cpu", "grow", "no_demand", "cooldown",
    "cpu_hold", "at_target", "at_ceiling", "unknown",
)
_CAUSES = (None, "memory", "cpu")
_NO_TARGET = 0xFFFF
_NO_BYTES = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class LiveRecord:
    limit: int = 0
    ceiling: int = 0
    min: int = 0
    target: int | None = None
    rule: str = ""
    shrink_cause: str | None = None
    stamp: float = 0.0
    owner_pid: int = 0
    last_step_at: float = 0.0
    cooldown_until: float = 0.0
    cpu_high_streak: int = 0
    in_slow_start: bool = False
    slow_start_step: int = 1
    last_wait_limit_at: float = 0.0
    last_wait_mem_at: float = 0.0
    wait_events: int = 0
    # Memory of runs admitted but maybe not yet in use: (until, bytes) each,
    # oldest first, at most BURST_SLOTS.
    charges: tuple[tuple[float, int], ...] = ()
    sample_at: float = 0.0
    last_valid_at: float = 0.0
    mem_total: int | None = None
    mem_available: int | None = None
    # The CPU counters (jiffies) at the last step; 0 when there are none.
    cpu_busy: int = 0
    cpu_total: int = 0

    @property
    def initialised(self) -> bool:
        return self.limit > 0

    def encode(self) -> bytes:
        def small(value: int) -> int:
            return min(max(int(value), 0), 0xFFFE)

        def nbytes(value: int | None) -> int:
            return _NO_BYTES if value is None else min(max(int(value), 0), _NO_BYTES - 1)

        body = _BODY.pack(
            small(self.limit), small(self.ceiling), small(self.min),
            _NO_TARGET if self.target is None else small(self.target),
            small(self.slow_start_step), small(self.cpu_high_streak),
            RULES.index(self.rule) if self.rule in RULES else 0,
            _CAUSES.index(self.shrink_cause) if self.shrink_cause in _CAUSES else 0,
            1 if self.in_slow_start else 0,
            self.owner_pid & 0xFFFFFFFF, self.wait_events & 0xFFFFFFFF,
            self.stamp, self.last_step_at, self.cooldown_until, self.last_wait_limit_at,
            self.last_wait_mem_at, self.sample_at, self.last_valid_at,
            nbytes(self.mem_total), nbytes(self.mem_available),
            nbytes(self.cpu_busy), nbytes(self.cpu_total),
        )
        raw = bytearray(RECORD_SIZE)
        raw[_HEADER.size:_HEADER.size + len(body)] = body
        offset = _HEADER.size + len(body)
        for until, size in self.charges[-BURST_SLOTS:]:
            _CHARGE.pack_into(raw, offset, until, min(max(int(size), 0), _NO_BYTES - 1))
            offset += _CHARGE.size
        _HEADER.pack_into(raw, 0, _MAGIC, _VERSION, 0)
        _HEADER.pack_into(raw, 0, _MAGIC, _VERSION, zlib.crc32(bytes(raw)))
        return bytes(raw)

    @classmethod
    def decode(cls, data: bytes) -> LiveRecord | None:
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
        (limit, ceiling, minimum, target, slow_step, cpu_streak, rule, cause, slow,
         owner, waits, stamp, last_step, cooldown, wait_limit, wait_mem,
         sample_at, last_valid, total, available, cpu_busy, cpu_total) = _BODY.unpack_from(data, _HEADER.size)
        if rule >= len(RULES) or cause >= len(_CAUSES):
            return None
        # No writer stores an infinite or NaN time: such a record is damaged
        # (or forged), and an infinite cooldown would pin the limit for good.
        if not all(map(math.isfinite, (stamp, last_step, cooldown, wait_limit, wait_mem, sample_at, last_valid))):
            return None
        charges = []
        for slot in range(BURST_SLOTS):
            until, size = _CHARGE.unpack_from(data, _HEADER.size + _BODY.size + slot * _CHARGE.size)
            if size:
                if not math.isfinite(until):
                    return None
                charges.append((until, size))
        return cls(
            limit=limit, ceiling=ceiling, min=minimum,
            target=None if target == _NO_TARGET else target, rule=RULES[rule],
            shrink_cause=_CAUSES[cause], stamp=stamp, owner_pid=owner, last_step_at=last_step,
            cooldown_until=cooldown, cpu_high_streak=cpu_streak, in_slow_start=bool(slow),
            slow_start_step=slow_step, last_wait_limit_at=wait_limit, last_wait_mem_at=wait_mem,
            wait_events=waits, charges=tuple(charges), sample_at=sample_at, last_valid_at=last_valid,
            mem_total=None if total == _NO_BYTES else total,
            mem_available=None if available == _NO_BYTES else available,
            cpu_busy=cpu_busy, cpu_total=cpu_total,
        )


def _other_version(data: bytes) -> bool:
    if len(data) < _HEADER.size:
        return False
    magic, version, _ = _HEADER.unpack_from(data, 0)
    return magic == _MAGIC and version != _VERSION


class LiveTxn:
    """The record while the lock is held."""

    def __init__(self, ledger: LiveLedger) -> None:
        self._ledger = ledger

    def read(self) -> LiveRecord | None:
        """The record now, or ``None`` when there is no valid one: under the
        lock that means it is uninitialised and may be rewritten."""
        return self._ledger._read_raw(remember=True)

    def write(self, record: LiveRecord) -> None:
        self._ledger._write_raw(record)

    @property
    def corrupt(self) -> bool:
        """Whether the last ``read()`` found bytes that are not a record of
        any version: damaged, rather than missing or left by an older one."""
        return self._ledger._last_corrupt

    @property
    def last_good(self) -> LiveRecord | None:
        """The last valid record this worker read or wrote."""
        with self._ledger._state_lock:
            return self._ledger._last_good


class LiveLedger:
    """The ``live`` record and its ``live.lock`` in *directory*.

    With ``directory=None`` the record lives in this process only, for when
    the state directory cannot be used: each worker then runs its own limit.
    Raises ``OSError`` (``PermissionError`` for a directory that is not
    private) when *directory* cannot be used.
    """

    def __init__(self, directory: Path | None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._thread_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_good: LiveRecord | None = None
        self._last_corrupt = False
        self._memory: bytes = b""
        self._record_fd = self._lock_fd = -1
        self._directory = directory
        self._replaced_logged = False
        self.shared = directory is not None
        if directory is None:
            return
        if fcntl is None:
            raise OSError("the live record needs POSIX file locks")
        ensure_private_directory(directory)
        self._record_fd, self._lock_fd = self._open_files(directory)

    @staticmethod
    def _open_files(directory: Path) -> tuple[int, int]:
        record_fd = open_private_file(directory / "live")
        try:
            return record_fd, open_private_file(directory / "live.lock")
        except BaseException:
            os.close(record_fd)
            raise

    def reopen_if_replaced(self) -> bool:
        """Open ``live`` and ``live.lock`` again when either was deleted or
        replaced since they were opened: workers still on the old files
        would otherwise keep a second limit, and burst charges the others
        never see. Blocking (a few syscalls); call it off the event loop,
        e.g. once per sample. Returns whether it reopened them."""
        directory = self._directory
        if directory is None:
            return False
        with self._thread_lock:
            if self._record_fd < 0:
                return False
            if not (
                file_replaced(self._record_fd, directory / "live")
                or file_replaced(self._lock_fd, directory / "live.lock")
            ):
                return False
            try:
                record_fd, lock_fd = self._open_files(directory)
            except OSError as exc:
                logger.warning("sandbox live limit: reopening the replaced shared record failed: %s", exc)
                return False
            old = (self._record_fd, self._lock_fd)
            self._record_fd, self._lock_fd = record_fd, lock_fd
            for fd in old:
                with contextlib.suppress(OSError):
                    os.close(fd)
        if not self._replaced_logged:
            self._replaced_logged = True
            logger.warning(
                "sandbox live limit: the shared record in %s was deleted or replaced; reopened it",
                directory,
            )
        return True

    def _read_raw(self, *, remember: bool = False) -> LiveRecord | None:
        if not self.shared:
            data = self._memory
        else:
            try:
                data = os.pread(self._record_fd, RECORD_SIZE, 0)
            except OSError:
                return None
        record = LiveRecord.decode(data)
        self._last_corrupt = record is None and bool(data) and not _other_version(data)
        if record is not None and remember:
            with self._state_lock:
                self._last_good = record
        return record

    def _write_raw(self, record: LiveRecord) -> None:
        data = record.encode()
        if self.shared:
            os.pwrite(self._record_fd, data, 0)
        else:
            self._memory = data
        with self._state_lock:
            self._last_good = record

    def read(self) -> LiveRecord | None:
        """The record without waiting. A read that fails its checksum is
        retried, then asked once under the lock (without waiting), and
        failing that the last good record is kept. ``None`` only when there
        has never been a valid one."""
        for _ in range(3):
            record = self._read_raw(remember=True)
            if record is not None:
                return record
        with self.transaction(0.0) as txn:
            if txn is not None:
                record = txn.read()
                if record is not None:
                    return record
        with self._state_lock:
            return self._last_good

    @contextlib.contextmanager
    def transaction(self, lock_budget: float = 0.020) -> Iterator[LiveTxn | None]:
        """The lock, tried for at most *lock_budget* seconds; ``None`` if busy.
        Blocks for up to the budget: never call it with one on the event loop."""
        if lock_budget > 0:
            acquired = self._thread_lock.acquire(timeout=lock_budget)
        else:
            acquired = self._thread_lock.acquire(blocking=False)
        if not acquired:
            yield None
            return
        locked = False
        try:
            if not self.shared:
                locked = True
            elif self._lock_fd >= 0:  # a closed ledger has no lock to take
                deadline = time.monotonic() + lock_budget
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
            yield LiveTxn(self) if locked else None
        finally:
            if locked and self.shared:
                with contextlib.suppress(OSError):
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._thread_lock.release()

    def close(self) -> None:
        with self._thread_lock, self._state_lock:
            for fd in (self._record_fd, self._lock_fd):
                if fd >= 0:
                    with contextlib.suppress(OSError):
                        os.close(fd)
            self._record_fd = self._lock_fd = -1
