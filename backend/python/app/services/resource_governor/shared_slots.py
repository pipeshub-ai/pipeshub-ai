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
A worker stopped (SIGSTOP) while it counts keeps that mutex until it resumes
or exits, and every other caller gets ``None`` and polls until then.

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
import re
import stat
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

_MUTEX_FILE = "mutex"
# A tagged slot file holds its holder's tag, space-padded to this many bytes,
# then a newline.
_TAG_BYTES = 16


class SlotHold:
    """Slots taken by one ``try_acquire``; ``release`` gives them back once."""

    def __init__(
        self,
        owner: SharedSlots | None = None,
        indices: list[int] | None = None,
        held_before: int = 0,
    ) -> None:
        self._owner = owner
        self._indices = indices or []
        # Slots held by anyone when these were taken, counted under the same
        # mutex: lets a caller tell "mine is the only one" without a second look.
        self.held_before = held_before

    def release(self) -> None:
        indices, self._indices = self._indices, []
        if self._owner is not None and indices:
            self._owner._release(indices)


@dataclass(frozen=True)
class SlotDenied:
    """Why ``try_acquire_tagged`` took nothing.

    ``busy``: another process was counting at that instant (``held`` and
    ``tag_held`` are then 0). ``full``: *limit* slots are held. ``tag_full``:
    the holders with this tag reached *tag_limit*.
    """

    reason: Literal["busy", "full", "tag_full"]
    held: int = 0
    tag_held: int = 0


def _pad_tag(tag: bytes) -> bytes:
    return tag[:_TAG_BYTES].ljust(_TAG_BYTES) + b"\n"


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
        # The tag of each slot held here, which this process cannot read back
        # through a lock of its own.
        self._tags: dict[int, bytes] = {}
        self._closed = False
        ensure_private_directory(directory)
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
                    return SlotHold(self, taken, held)
                for index in taken:
                    _unlock(self._slot_fds[index])
                return None
            except BaseException:
                for index in taken:
                    _unlock(self._slot_fds[index])
                raise
            finally:
                _unlock(self._mutex_fd)

    def try_acquire_tagged(
        self, limit: int, *, tag: bytes, tag_limit: int | None,
    ) -> SlotHold | SlotDenied:
        """Take one slot, stamped with *tag*, if fewer than *limit* slots are
        held and fewer than *tag_limit* of them carry *tag*.

        Never blocks: a mutex held by another process gives
        ``SlotDenied("busy")`` at once, so this may run on an event loop. An
        empty tag is never tag-limited. Unlike ``try_acquire`` there is no
        "admit alone" rule; every holder costs one slot.

        A tag is only read from a slot whose lock is held, and it is written
        under the mutex before that lock is seen as held, so a stale tag left
        in a free file is never counted. A holder that died has no lock, so its
        slot reads as free whatever its file says.
        """
        tag = tag[:_TAG_BYTES].strip()
        with self._lock:
            if self._closed:
                raise OSError("shared slots are closed")
            if not _try_lock(self._mutex_fd):
                return SlotDenied("busy")
            free: int | None = None
            taken = False
            try:
                held = tag_held = 0
                for index, fd in enumerate(self._slot_fds):
                    if index in self._held_here:
                        holder = self._tags.get(index, b"")
                    elif not _try_lock(fd):
                        holder = os.pread(fd, _TAG_BYTES + 1, 0)[:_TAG_BYTES].strip()
                    else:
                        if free is None:
                            free = index
                        else:
                            _unlock(fd)
                        continue
                    held += 1
                    if tag and holder == tag:
                        tag_held += 1
                if free is None or held >= limit:
                    return SlotDenied("full", held, tag_held)
                if tag and tag_limit is not None and tag_held >= tag_limit:
                    return SlotDenied("tag_full", held, tag_held)
                os.pwrite(self._slot_fds[free], _pad_tag(tag), 0)
                self._held_here.add(free)
                self._tags[free] = tag
                taken = True
                return SlotHold(self, [free], held)
            finally:
                if free is not None and not taken:
                    _unlock(self._slot_fds[free])
                _unlock(self._mutex_fd)

    def held_by_tag(self, budget: float = 0.0) -> Counter[bytes]:
        """Held slots by tag (``b""`` for untagged), from every process.
        Read-only, like ``held_count``.

        With ``budget=0.0`` it makes one try at the mutex and raises
        ``TimeoutError`` at once when another process holds it, so it may run
        on an event loop. A larger budget spins and must not.
        """
        deadline = time.monotonic() + budget
        while True:
            with self._lock:
                if self._closed:
                    return Counter()
                if _try_lock(self._mutex_fd):
                    try:
                        counts: Counter[bytes] = Counter()
                        for index, fd in enumerate(self._slot_fds):
                            if index in self._held_here:
                                counts[self._tags.get(index, b"")] += 1
                            elif not _try_lock(fd):
                                counts[os.pread(fd, _TAG_BYTES + 1, 0)[:_TAG_BYTES].strip()] += 1
                            else:
                                _unlock(fd)
                        return counts
                    finally:
                        _unlock(self._mutex_fd)
            if time.monotonic() >= deadline:
                raise TimeoutError("shared slot mutex stayed busy")
            time.sleep(0.001)

    def held_count(self, budget: float = 1.0) -> int:
        """Slots held now, by any process. Read-only: takes the mutex, then
        probes each slot and puts it straight back.

        Raises ``TimeoutError`` if the mutex stays taken for *budget* seconds.
        """
        deadline = time.monotonic() + budget
        while True:
            with self._lock:
                if self._closed:
                    return 0
                if _try_lock(self._mutex_fd):
                    try:
                        held = 0
                        for index, fd in enumerate(self._slot_fds):
                            if index in self._held_here or not _try_lock(fd):
                                held += 1
                            else:
                                _unlock(fd)
                        return held
                    finally:
                        _unlock(self._mutex_fd)
            if time.monotonic() >= deadline:
                raise TimeoutError("shared slot mutex stayed busy")
            time.sleep(0.001)

    def close(self) -> None:
        """Close every file, which drops this process's locks with them."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fds = [self._directory_fd, self._mutex_fd, *self._slot_fds]
            self._slot_fds = []
            self._held_here.clear()
            self._tags.clear()
            for fd in fds:
                os.close(fd)

    def _release(self, indices: list[int]) -> None:
        with self._lock:
            for index in indices:
                if index in self._held_here:
                    self._held_here.discard(index)
                    self._tags.pop(index, None)
                    _unlock(self._slot_fds[index])


class LocalTaggedSlots:
    """``SharedSlots``' tagged interface, counted in this process only.

    For hosts without POSIX file locks or a usable private directory: each
    worker then counts its own slots, so N workers may hold N times the limit.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, capacity)
        self._lock = threading.Lock()
        self._tags: dict[int, bytes] = {}

    @property
    def capacity(self) -> int:
        return self._capacity

    def try_acquire_tagged(
        self, limit: int, *, tag: bytes, tag_limit: int | None,
    ) -> SlotHold | SlotDenied:
        tag = tag[:_TAG_BYTES].strip()
        with self._lock:
            held = len(self._tags)
            tag_held = sum(1 for held_tag in self._tags.values() if tag and held_tag == tag)
            if held >= min(limit, self._capacity):
                return SlotDenied("full", held, tag_held)
            if tag and tag_limit is not None and tag_held >= tag_limit:
                return SlotDenied("tag_full", held, tag_held)
            free = next(i for i in range(self._capacity) if i not in self._tags)
            self._tags[free] = tag
            return SlotHold(self, [free], held)  # type: ignore[arg-type]

    def held_by_tag(self, budget: float = 0.0) -> Counter[bytes]:
        with self._lock:
            return Counter(self._tags.values())

    def close(self) -> None:
        with self._lock:
            self._tags.clear()

    def _release(self, indices: list[int]) -> None:
        with self._lock:
            for index in indices:
                self._tags.pop(index, None)


def ensure_private_directory(directory: Path) -> None:
    """Create *directory* if needed and refuse one that is not ours alone.

    The names under ``/tmp`` are predictable, so another local user could have
    created the directory first, or planted a symlink at its name. A real
    directory, owned by this uid, with no group or other bits, is the only kind
    that can be trusted with files we write to. Raises ``PermissionError``.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError(f"{directory} is not a private directory owned by this user")


def open_private_file(path: Path) -> int:
    """Open (creating) a file we write to, refusing links and files that are not
    ours alone. Raises ``OSError`` or ``PermissionError``."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o177:
            raise PermissionError(f"{path} is not a private regular file owned by this user")
    except BaseException:
        os.close(fd)
        raise
    return fd


def shared_slots_directory(name: str) -> Path | None:
    """Where this container's workers share the slots called *name*.

    Every worker of a container opens the same directory; another container
    has its own /tmp. Per uid so a directory left by another user on a shared
    host cannot block startup. ``None`` without POSIX file locks, as on
    Windows, which has no uids to name the directory by either.
    """
    if fcntl is None:
        return None
    return Path(tempfile.gettempdir()) / f"{name}-{os.getuid()}"


_STATE_ID_ENV = "SANDBOX_STATE_ID"
_STATE_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,63}")
_warned_state_ids: set[str] = set()


def sandbox_state_directory(env: Mapping[str, str] | None = None) -> Path | None:
    """The private directory the code sandbox keeps its cross-worker state in.

    ``/tmp/pipeshub-sandbox-<uid>``, or ``/tmp/pipeshub-sandbox-<id>-<uid>``
    when ``SANDBOX_STATE_ID`` is set. In a container every stack has its own
    ``/tmp``. Stacks whose services run directly on one host as one user share
    it, so each sets its own id (the same one for its query and indexing
    processes), or they share one set of slots. An id that is not 1-63 of
    ``A-Z a-z 0-9 _ . -`` is ignored with one warning. ``None`` without POSIX
    file locks.
    """
    env = os.environ if env is None else env
    raw = (env.get(_STATE_ID_ENV) or "").strip()
    name = "pipeshub-sandbox"
    if raw:
        if _STATE_ID_RE.fullmatch(raw):
            name = f"{name}-{raw}"
        elif raw not in _warned_state_ids:
            _warned_state_ids.add(raw)
            logger.warning(
                "Ignoring %s=%r: use 1-63 letters, digits, '_', '.' or '-'", _STATE_ID_ENV, raw,
            )
    return shared_slots_directory(name)


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
