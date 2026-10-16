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

Every file is opened once, when the worker starts, and locked and unlocked
through that descriptor. A lock belongs to the open file, not to its name, so
if something removes the directory the workers already running still count
each other's slots. Each worker also holds a shared lock on the directory
itself, which ``systemd-tmpfiles`` honours by not cleaning it. A worker
started after the directory was removed regardless opens new files and does
not see slots held on the old ones until they are released.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_MUTEX_FILE = "mutex"


class SlotHold:
    """Slots taken by one ``try_acquire``; ``release`` gives them back once."""

    def __init__(self, owner: SharedSlots | None = None, indices: list[int] | None = None) -> None:
        self._owner = owner
        self._indices = indices or []

    def release(self) -> None:
        indices, self._indices = self._indices, []
        if self._owner is not None and indices:
            self._owner._release(indices)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)


class SharedSlots:
    """Up to ``capacity`` slots shared by every process using ``directory``."""

    def __init__(self, directory: Path, capacity: int) -> None:
        if fcntl is None:
            raise OSError("shared slots need POSIX file locks")
        self._capacity = max(1, capacity)
        # A lock on an open file does not conflict with itself, so this
        # process's own holds are tracked here, and the lock keeps two threads
        # from counting through the same descriptors at once.
        self._lock = threading.Lock()
        self._held_here: set[int] = set()
        self._closed = False
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        opened: list[int] = []
        try:
            self._directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            opened.append(self._directory_fd)
            fcntl.flock(self._directory_fd, fcntl.LOCK_SH)
            self._mutex_fd = self._open(directory, _MUTEX_FILE, opened)
            self._slot_fds = [
                self._open(directory, str(index), opened) for index in range(self._capacity)
            ]
        except BaseException:
            for fd in opened:
                os.close(fd)
            raise

    @staticmethod
    def _open(directory: Path, name: str, opened: list[int]) -> int:
        fd = os.open(directory / name, os.O_RDWR | os.O_CREAT, 0o600)
        opened.append(fd)
        return fd

    @property
    def capacity(self) -> int:
        return self._capacity

    def try_acquire(self, limit: int, count: int = 1) -> SlotHold | None:
        """Take *count* slots if that keeps the held total within *limit*.

        Like ``AdmissionGate``, a request is admitted alone when no slot is
        held, even if *count* exceeds *limit*, so an oversized parse cannot
        wait forever. Returns ``None`` when there is no room, or when another
        process is counting at this instant; the caller polls.
        """
        count = min(max(1, count), self._capacity)
        with self._lock:
            if not _try_lock(self._mutex_fd):
                return None
            taken: list[int] = []
            try:
                held = 0
                for index, fd in enumerate(self._slot_fds):
                    if index in self._held_here or not _try_lock(fd):
                        held += 1
                    elif len(taken) < count:
                        taken.append(index)
                    else:
                        _unlock(fd)
                if len(taken) == count and (held == 0 or held + count <= limit):
                    self._held_here.update(taken)
                    return SlotHold(self, taken)
                for index in taken:
                    _unlock(self._slot_fds[index])
                return None
            except BaseException:
                for index in taken:
                    _unlock(self._slot_fds[index])
                raise
            finally:
                _unlock(self._mutex_fd)

    def close(self) -> None:
        """Close every file, which drops this process's locks with them."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fds = [self._directory_fd, self._mutex_fd, *self._slot_fds]
            self._slot_fds = []
            self._held_here.clear()
            for fd in fds:
                os.close(fd)

    def _release(self, indices: list[int]) -> None:
        with self._lock:
            for index in indices:
                if index in self._held_here:
                    self._held_here.discard(index)
                    _unlock(self._slot_fds[index])


def open_shared_slots(directory: Path, capacity: int) -> SharedSlots | None:
    """``SharedSlots`` in *directory*, or ``None`` (logged) when it cannot be used."""
    try:
        return SharedSlots(directory, capacity)
    except OSError as exc:
        logger.warning(
            "Shared heavy-parse slots unavailable in %s (%s); the cluster-wide "
            "heavy parsing lease falls back to one worker's share",
            directory,
            exc,
        )
        return None
