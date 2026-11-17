"""The indexing back-off signal: query asks indexing to slow down while code
sandbox runs wait for memory that indexing shares.

One 256-byte record, ``yield`` in the code sandbox's state directory, written
with one ``pwrite`` under ``yield.lock`` and read with one ``pread``, with the
same checksum scheme as the live record and the lane ledger. A query worker
whose run waits for memory refreshes it; it is never cleared, so it ends by
its TTL once the last waiter stops refreshing. Each indexing worker that acts
on it writes its own entry in the ack table, saying whether its pools are at
their floor.

A record that is missing, torn, of another version or otherwise not
believable reads as inactive: indexing is never slowed by garbage. All times
are ``time.monotonic()``, which every process in one container shares. The
file can outlive a host reboot (a container restarted in place keeps its
``/tmp``) while the clock starts again, so a record set well after now is
inactive.

It lives here rather than beside the sandbox code so that indexing does not
import the agent library.
"""
from __future__ import annotations

import contextlib
import errno
import logging
import math
import os
import stat
import struct
import threading
import time
import zlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.services.resource_governor.lane_ledger import pid_alive
from app.services.resource_governor.shared_slots import (
    ensure_private_directory,
    file_replaced,
    open_private_file,
    sandbox_state_directory,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

__all__ = [
    "YieldAck",
    "YieldRecord",
    "YieldSignal",
    "YieldState",
    "indexing_backed_off",
    "read_yield_state",
]

logger = logging.getLogger(__name__)

RECORD_SIZE = 256
_MAGIC = b"PXSBXYD\0"
_VERSION = 1
_HEADER = struct.Struct("<8sII")
# set_at expires_at since (d), writer_pid (I), reason (B).
_BODY = struct.Struct("<3dIB")
# pid (I), ack_at (d), at_floor (B).
_ACK = struct.Struct("<IdB")
MAX_ACKS = 8
assert _HEADER.size + _BODY.size + MAX_ACKS * _ACK.size <= RECORD_SIZE

REASONS = ("", "host_memory_floor", "live_memory")
DEFAULT_TTL_S = 20.0
# An ack older than this, or from a dead pid, no longer speaks for indexing.
ACK_VALID_S = 30.0
# A writer that died stops refreshing; once its pid is gone, release indexing
# this soon instead of waiting out the TTL.
DEAD_WRITER_S = 10.0
# No refresh sets an expiry further out than the largest TTL; one that does
# is corrupt.
MAX_SPAN_S = 120.0
# How far ahead of a reader's clock a refresh may be (it read the clock just
# before another process wrote); further ahead, it was written before the
# clock restarted.
CLOCK_SLACK_S = 1.0
LOCK_BUDGET = 0.020
# How often a writer checks that its open files are still the ones at their
# names (see ``YieldSignal._reopen_if_replaced``).
REOPEN_CHECK_S = 1.0
# "Indexing has backed off": an ack this recent, and its pools at their floor
# or the episode on for this long.
BACKED_OFF_ACK_S = 30.0
BACKED_OFF_EPISODE_S = 120.0


@dataclass(frozen=True)
class YieldAck:
    pid: int
    ack_at: float
    at_floor: bool


@dataclass(frozen=True)
class YieldRecord:
    set_at: float = 0.0
    expires_at: float = 0.0
    since: float = 0.0
    writer_pid: int = 0
    reason: str = ""
    acks: tuple[YieldAck, ...] = ()

    def active(self, now: float, alive: Callable[[int], bool] = pid_alive) -> bool:
        return (
            0 < self.set_at <= now + CLOCK_SLACK_S
            and now < self.expires_at
            and self.expires_at - self.set_at <= MAX_SPAN_S
            # An episode starts at or before its latest refresh.
            and self.since <= self.set_at
            and (now - self.set_at < DEAD_WRITER_S or alive(self.writer_pid))
        )

    def valid_acks(self, now: float, alive: Callable[[int], bool] = pid_alive) -> list[YieldAck]:
        """Acks made in this episode within ``ACK_VALID_S``, by live pids."""
        return [
            ack for ack in self.acks
            if ack.pid > 0 and ack.ack_at >= self.since and 0 <= now - ack.ack_at <= ACK_VALID_S
            and alive(ack.pid)
        ]

    def encode(self) -> bytes:
        raw = bytearray(RECORD_SIZE)
        offset = _HEADER.size
        _BODY.pack_into(
            raw, offset, self.set_at, self.expires_at, self.since, self.writer_pid & 0xFFFFFFFF,
            REASONS.index(self.reason) if self.reason in REASONS else 0,
        )
        offset += _BODY.size
        for ack in self.acks[:MAX_ACKS]:
            _ACK.pack_into(raw, offset, ack.pid & 0xFFFFFFFF, ack.ack_at, 1 if ack.at_floor else 0)
            offset += _ACK.size
        _HEADER.pack_into(raw, 0, _MAGIC, _VERSION, 0)
        _HEADER.pack_into(raw, 0, _MAGIC, _VERSION, zlib.crc32(bytes(raw)))
        return bytes(raw)

    @classmethod
    def decode(cls, data: bytes) -> YieldRecord | None:
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
        offset = _HEADER.size
        set_at, expires_at, since, writer_pid, reason = _BODY.unpack_from(data, offset)
        if reason >= len(REASONS) or not all(map(math.isfinite, (set_at, expires_at, since))):
            return None
        offset += _BODY.size
        acks = []
        for _ in range(MAX_ACKS):
            pid, ack_at, at_floor = _ACK.unpack_from(data, offset)
            offset += _ACK.size
            if pid and math.isfinite(ack_at):
                acks.append(YieldAck(pid=pid, ack_at=ack_at, at_floor=bool(at_floor)))
        return cls(
            set_at=set_at, expires_at=expires_at, since=since, writer_pid=writer_pid,
            reason=REASONS[reason], acks=tuple(acks),
        )


@dataclass(frozen=True)
class YieldState:
    """What the signal says now.

    ``since`` is when the current episode started. ``acked_at`` is the newest
    valid indexing ack and ``ackers`` how many indexing pids have one.
    ``index_at_floor`` is true only when there is at least one valid ack and
    every one reports its pools at their floor.
    """

    active: bool = False
    since: float | None = None
    acked_at: float | None = None
    index_at_floor: bool = False
    ackers: int = 0


INACTIVE = YieldState()


def state_of(record: YieldRecord | None, now: float, alive: Callable[[int], bool] = pid_alive) -> YieldState:
    if record is None or not record.active(now, alive):
        return INACTIVE
    acks = record.valid_acks(now, alive)
    return YieldState(
        active=True,
        since=record.since,
        acked_at=max((ack.ack_at for ack in acks), default=None),
        index_at_floor=bool(acks) and all(ack.at_floor for ack in acks),
        ackers=len(acks),
    )


@dataclass(frozen=True)
class Refreshed:
    """A refresh that was written: ``started`` when it began a new episode."""

    started: bool
    since: float
    previous: YieldRecord | None


class YieldSignal:
    """The ``yield`` record and its ``yield.lock`` in *directory*.

    Raises ``OSError`` (``PermissionError`` for a directory or file that is
    not private) when *directory* cannot be used.
    """

    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.monotonic) -> None:
        if fcntl is None:
            raise OSError("the yield signal needs POSIX file locks")
        self.directory = directory
        self._clock = clock
        self._thread_lock = threading.Lock()
        self._checked_at = time.monotonic()
        self._replaced_logged = False
        ensure_private_directory(directory)
        self._record_fd, self._lock_fd = self._open_files(directory)

    @staticmethod
    def _open_files(directory: Path) -> tuple[int, int]:
        record_fd = open_private_file(directory / "yield")
        try:
            return record_fd, open_private_file(directory / "yield.lock")
        except BaseException:
            os.close(record_fd)
            raise

    def _reopen_if_replaced(self) -> None:
        """At most every ``REOPEN_CHECK_S``: open ``yield`` and ``yield.lock``
        again when either was deleted or replaced since, or this process would
        keep signalling into a file indexing no longer reads. Called with the
        thread lock held; a few syscalls."""
        now = time.monotonic()
        if self._record_fd < 0 or now - self._checked_at < REOPEN_CHECK_S:
            return
        self._checked_at = now
        directory = self.directory
        if not (
            file_replaced(self._record_fd, directory / "yield")
            or file_replaced(self._lock_fd, directory / "yield.lock")
        ):
            return
        try:
            fds = self._open_files(directory)
        except OSError as exc:
            logger.debug("indexing back-off signal: reopening failed: %s", exc)
            return
        old = (self._record_fd, self._lock_fd)
        self._record_fd, self._lock_fd = fds
        for fd in old:
            with contextlib.suppress(OSError):
                os.close(fd)
        if not self._replaced_logged:
            self._replaced_logged = True
            logger.warning(
                "indexing back-off signal: the record in %s was deleted or replaced; reopened it",
                directory,
            )

    @classmethod
    def open_default(cls, *, role: str) -> YieldSignal | None:
        """The signal in the code sandbox's state directory, or ``None``
        (one WARNING) when it cannot be used."""
        directory = sandbox_state_directory()
        if directory is None:
            return None
        try:
            return cls(directory)
        except OSError as exc:
            logger.warning(
                "indexing back-off signal unavailable in %s (%s); the %s does not use it",
                directory, exc, role,
            )
            return None

    def close(self) -> None:
        for fd in (self._record_fd, self._lock_fd):
            with contextlib.suppress(OSError):
                os.close(fd)
        self._record_fd = self._lock_fd = -1

    def _read_raw(self) -> YieldRecord | None:
        try:
            return YieldRecord.decode(os.pread(self._record_fd, RECORD_SIZE, 0))
        except OSError:
            return None

    def read(self) -> YieldRecord | None:
        """The record without waiting; a torn read is retried, then given up.
        A reader that never takes the lock still notices a replaced file."""
        if self._thread_lock.acquire(blocking=False):
            try:
                self._reopen_if_replaced()
            finally:
                self._thread_lock.release()
        for _ in range(3):
            record = self._read_raw()
            if record is not None:
                return record
        return None

    def read_state(self, now: float | None = None) -> YieldState:
        return state_of(self.read(), self._clock() if now is None else now)

    @contextlib.contextmanager
    def _locked(self, budget: float) -> Iterator[bool]:
        """The lock, tried for at most *budget* seconds (0: once)."""
        if not self._thread_lock.acquire(blocking=budget > 0, timeout=budget if budget > 0 else -1):
            yield False
            return
        locked = False
        try:
            self._reopen_if_replaced()
            deadline = time.monotonic() + budget
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
            yield locked
        finally:
            if locked:
                with contextlib.suppress(OSError):
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._thread_lock.release()

    def refresh(
        self, *, reason: str, ttl_s: float = DEFAULT_TTL_S, now: float | None = None,
        pid: int | None = None, budget: float = 0.0,
    ) -> Refreshed | None:
        """Keep the signal up for *ttl_s* more seconds. The episode's start
        is kept while the previous record is still active. ``None`` when the
        lock was busy (with the default budget of 0 it is tried once, so this
        may run on an event loop) or the write failed."""
        now = self._clock() if now is None else now
        ttl_s = min(max(ttl_s, 1.0), MAX_SPAN_S)
        with self._locked(budget) as locked:
            if not locked:
                return None
            previous = self.read()
            live = previous is not None and previous.active(now)
            since = previous.since if live and previous is not None else now
            record = YieldRecord(
                set_at=now, expires_at=now + ttl_s, since=since,
                writer_pid=os.getpid() if pid is None else pid, reason=reason,
                # A new episode starts with no acks: an old at-floor ack must
                # not say indexing has already backed off.
                acks=previous.acks if live and previous is not None else (),
            )
            try:
                os.pwrite(self._record_fd, record.encode(), 0)
            except OSError:
                logger.debug("indexing back-off signal: write failed", exc_info=True)
                return None
        return Refreshed(started=not live, since=since, previous=previous)

    def ack(self, pid: int, now: float | None = None, *, at_floor: bool, budget: float = LOCK_BUDGET) -> bool:
        """Record that indexing worker *pid* acted on the active signal. It
        updates its own entry, or takes a free one, or the oldest. The
        writer's fields are left as they are. Blocks for up to *budget*."""
        now = self._clock() if now is None else now
        with self._locked(budget) as locked:
            if not locked:
                return False
            record = self.read()
            if record is None or not record.active(now):
                return False
            acks = [ack for ack in record.acks if ack.pid != pid]
            if len(acks) >= MAX_ACKS:
                acks.remove(min(acks, key=lambda ack: ack.ack_at))
            acks.append(YieldAck(pid=pid, ack_at=now, at_floor=at_floor))
            try:
                os.pwrite(self._record_fd, replace(record, acks=tuple(acks)).encode(), 0)
            except OSError:
                return False
        return True


def read_yield_state(directory: Path | None = None, *, now: float | None = None) -> YieldState:
    """Whether indexing is being asked to slow down, read from *directory*
    (default: the code sandbox's state directory). Never creates the files
    and never blocks; anything unreadable is inactive.

    Indexing has backed off when the state is ``active``, ``acked_at`` is
    within the last 30 s, and either ``index_at_floor`` or the episode
    (``since``) has lasted at least 120 s.
    """
    directory = sandbox_state_directory() if directory is None else directory
    if directory is None:
        return INACTIVE
    try:
        # O_NONBLOCK: opening a FIFO planted at the name for reading would
        # otherwise wait for a writer, and this runs on the event loop.
        fd = os.open(directory / "yield", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return INACTIVE
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return INACTIVE
        record = None
        for _ in range(3):
            record = YieldRecord.decode(os.pread(fd, RECORD_SIZE, 0))
            if record is not None:
                break
    except OSError:
        return INACTIVE
    finally:
        os.close(fd)
    return state_of(record, time.monotonic() if now is None else now)


def indexing_backed_off(state: YieldState, now: float) -> bool:
    """The back-off rule on a ``YieldState`` read at *now*: active, acked
    within ``BACKED_OFF_ACK_S``, and at the floor or on for
    ``BACKED_OFF_EPISODE_S``."""
    return (
        state.active
        and state.acked_at is not None and now - state.acked_at <= BACKED_OFF_ACK_S
        and (state.index_at_floor or (state.since is not None and now - state.since >= BACKED_OFF_EPISODE_S))
    )
