"""A slot counter shared by the worker processes of one container.

Each worker process runs its own ``ResourceGovernor``, and each worker's gate
admits into an empty pool whatever its limit, so N workers always allow at
least N concurrent heavy parses. When the cgroup's free memory holds fewer,
nothing per-worker can hold them to it; they need one counter they all see.
The cluster-wide Redis leases are the wrong scope for that, since every
replica of a release shares them and one pod's memory says nothing about
another's.

A slot is an exclusive ``flock`` on one file in a directory the workers share.
The kernel drops a lock when its holder exits, however it exits, so a worker
that is OOM-killed mid-parse gives its slots back with it and nothing needs
renewing or expiring. Counting the held slots and taking free ones happen
under a mutex file, so two workers cannot both see room for the last one.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_MUTEX_FILE = "mutex"


@dataclass
class SlotHold:
    """Slots taken by one ``try_acquire``; ``release`` gives them back once."""

    _fds: list[int] = field(default_factory=list)

    def release(self) -> None:
        fds, self._fds = self._fds, []
        for fd in fds:
            _unlock_and_close(fd)


def _unlock_and_close(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class SharedSlots:
    """Up to ``capacity`` slots shared by every process using ``directory``."""

    def __init__(self, directory: Path, capacity: int) -> None:
        if fcntl is None:
            raise OSError("shared slots need POSIX file locks")
        self._directory = directory
        self._capacity = max(1, capacity)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Fail at startup, not on the first parse, if the directory belongs to
        # someone else or is not writable.
        os.close(self._open(_MUTEX_FILE))

    @property
    def capacity(self) -> int:
        return self._capacity

    def _open(self, name: str) -> int:
        return os.open(self._directory / name, os.O_RDWR | os.O_CREAT, 0o600)

    def _try_lock(self, name: str) -> int | None:
        fd = self._open(name)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _count_held(self, keep: list[int], want: int = 0) -> int:
        """Count held slots; lock and append up to *want* free ones to *keep*."""
        held = 0
        for index in range(self._capacity):
            fd = self._try_lock(str(index))
            if fd is None:
                held += 1
            elif len(keep) < want:
                keep.append(fd)
            else:
                _unlock_and_close(fd)
        return held

    def try_acquire(self, limit: int, count: int = 1) -> SlotHold | None:
        """Take *count* slots if that keeps the held total within *limit*.

        Like ``AdmissionGate``, a request is admitted alone when no slot is
        held, even if *count* exceeds *limit*, so an oversized parse cannot
        wait forever. Returns ``None`` when there is no room, or when another
        process is counting at this instant; the caller polls.
        """
        count = min(max(1, count), self._capacity)
        mutex = self._try_lock(_MUTEX_FILE)
        if mutex is None:
            return None
        taken: list[int] = []
        try:
            held = self._count_held(taken, want=count)
            if len(taken) == count and (held == 0 or held + count <= limit):
                return SlotHold(taken)
            for fd in taken:
                _unlock_and_close(fd)
            return None
        except BaseException:
            for fd in taken:
                _unlock_and_close(fd)
            raise
        finally:
            _unlock_and_close(mutex)


def open_shared_slots(directory: Path, capacity: int) -> SharedSlots | None:
    """``SharedSlots`` in *directory*, or ``None`` (logged) when it cannot be used."""
    try:
        return SharedSlots(directory, capacity)
    except OSError as exc:
        logger.warning(
            "Shared heavy-parse slots unavailable in %s (%s); each worker process "
            "is held only to its own share of free memory",
            directory,
            exc,
        )
        return None
