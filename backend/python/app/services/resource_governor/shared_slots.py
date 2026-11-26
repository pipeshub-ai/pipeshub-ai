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

import contextlib
import errno
import logging
import os
import re
import stat
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from app.utils.private_dirs import per_user_temp_dir

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
        self.directory = directory
        # A folder others can read or write, or a link, is refused: its name under /tmp is guessable.
        ensure_private_directory(directory)
        opened: list[int] = []
        try:
            # The directory is opened once, without following a link, and
            # checked again through that descriptor; every file is then opened
            # relative to it. Swapping the name for a link after the check
            # changes nothing for this worker.
            self._directory_fd = _open_private_directory(directory)
            opened.append(self._directory_fd)
            fcntl.flock(self._directory_fd, fcntl.LOCK_SH)
            self._mutex_fd = self._open(directory, self._directory_fd, _MUTEX_FILE, opened)
            self._slot_fds = [
                self._open(directory, self._directory_fd, str(index), opened)
                for index in range(self._capacity)
            ]
        except BaseException:
            for fd in opened:
                os.close(fd)
            raise

    @staticmethod
    def _open(directory: Path, directory_fd: int, name: str, opened: list[int]) -> int:
        fd = open_private_file(directory / name, dir_fd=directory_fd)
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


_PRIVATE_FIX = (
    "remove it (it is recreated private), or point this stack elsewhere: set "
    "SANDBOX_STATE_DIR to a directory only this user can change, "
    "SANDBOX_STATE_ID for the code sandbox's state, or TMPDIR"
)
_reported: set[tuple[str, int, int]] = set()


def _refuse(path: Path, info: os.stat_result | None, what: str, *, log: bool = True) -> PermissionError:
    """A ``PermissionError`` for *path*, with one ERROR per path, owner and
    mode: a refused directory turns cross-worker counting, the live limit,
    the indexing back-off and the load alerts off, which must not go unseen.
    A refused file inside a private directory is left to its caller to log:
    only this uid can have put it there."""
    uid = -1 if info is None else info.st_uid
    mode = 0 if info is None else stat.S_IMODE(info.st_mode)
    key = (str(path), uid, mode)
    if log and key not in _reported:
        _reported.add(key)
        logger.error(
            "%s is %s (owner uid %d, mode %04o; this process runs as uid %d and needs a "
            "directory it owns with mode 0700). Shared state there is not used, so each "
            "worker keeps its own; %s",
            path, what, uid, mode, os.getuid(), _PRIVATE_FIX,
        )
    return PermissionError(f"{path} is {what} (owner uid {uid}, mode {mode:04o})")


def _private(info: os.stat_result) -> bool:
    return info.st_uid == os.getuid() and not info.st_mode & 0o077


def _parent_unsafe(info: os.stat_result) -> bool:
    """Whether another user could rename a directory out of this one: it is
    owned by someone else (root aside), or writable by everyone without the
    sticky bit that ``/tmp`` has."""
    world_write = bool(info.st_mode & 0o002) and not info.st_mode & stat.S_ISVTX
    return info.st_uid not in (0, os.getuid()) or world_write


def _check_parent(parent: Path) -> None:
    """Refuse a parent that another user could rename our directory out of
    (see `_parent_unsafe`)."""
    try:
        info = os.stat(parent)
    except OSError:
        return  # the mkdir below reports it
    if not stat.S_ISDIR(info.st_mode):
        return
    if _parent_unsafe(info):
        raise _refuse(parent, info, "a parent directory that another user can change")


def _open_private_directory(directory: Path) -> int:
    """*directory* opened without following a link, checked through the
    descriptor. Raises ``PermissionError`` (logged once) when it is not ours alone."""
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        try:
            info = os.lstat(directory)
        except OSError:
            raise exc from None
        if stat.S_ISLNK(info.st_mode):
            raise _refuse(directory, info, "a symlink, not a directory") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise _refuse(directory, info, "not a directory") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or not _private(info):
            raise _refuse(directory, info, "not a private directory owned by this user")
    except BaseException:
        os.close(fd)
        raise
    return fd


def ensure_private_directory(directory: Path) -> None:
    """Create *directory* if needed and refuse one that is not ours alone.

    The names under ``/tmp`` are predictable, so another local user could have
    created the directory first, or planted a symlink at its name. A real
    directory, owned by this uid, with no group or other bits, is the only kind
    that can be trusted with files we write to. Its parent must not let another
    user rename it: owned by this uid or root, and not writable by everyone
    unless sticky (as ``/tmp`` is). Raises ``PermissionError``, after one
    ERROR naming the path, its owner and mode, and the fix.

    A missing parent is created private too: ``mkdir(parents=True)`` applies
    *mode* to the last directory only, so ``<state>/slots`` created first would
    leave ``<state>`` at ``0o777 & ~umask``, which every other user of
    ``<state>`` then refuses.
    """
    parent = directory.parent
    if parent != directory:
        if not os.path.lexists(parent):
            ensure_private_directory(parent)
        else:
            _check_parent(parent)
    with contextlib.suppress(FileExistsError):
        directory.mkdir(mode=0o700)
    os.close(_open_private_directory(directory))


def open_private_file(path: Path, *, dir_fd: int | None = None) -> int:
    """Open (creating) a file we write to, refusing links and files that are not
    ours alone. With *dir_fd*, *path*'s last part is opened relative to that
    directory. Raises ``OSError`` or ``PermissionError``."""
    name: str | Path = path.name if dir_fd is not None else path
    try:
        fd = os.open(
            name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dir_fd,
        )
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            raise
        try:
            info = os.lstat(name, dir_fd=dir_fd)
        except OSError:
            raise exc from None
        raise _refuse(path, info, "a symlink, not a regular file", log=False) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o177:
            raise _refuse(path, info, "not a private regular file owned by this user", log=False)
    except BaseException:
        os.close(fd)
        raise
    return fd


def file_replaced(fd: int, path: Path) -> bool:
    """Whether the file open as *fd* is no longer the one at *path*: it was
    deleted, or something else now has its name. Workers that opened it
    before would then keep using a file that workers started later never see."""
    try:
        current = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        held = os.fstat(fd)
    except OSError:
        return True
    return held.st_nlink == 0 or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)


_STATE_DIR_ENV = "SANDBOX_STATE_DIR"
_warned_locations: set[tuple[str, ...]] = set()


def _unsafe_ancestor(directory: Path, *, home: bool = False) -> tuple[Path, os.stat_result | None] | None:
    """The first existing ancestor of *directory* (its parent, or further up
    when the parents are still to be created private) when another user
    could change it, else ``None``. With *home*, that ancestor must also be
    owned by this uid and not writable by everyone, sticky or not: it is a
    home directory, not a ``/tmp``."""
    for parent in directory.parents:
        try:
            info = os.stat(parent)
        except FileNotFoundError:
            continue
        except OSError:
            return parent, None
        if not stat.S_ISDIR(info.st_mode) or _parent_unsafe(info):
            return parent, info
        if home and (info.st_uid != os.getuid() or info.st_mode & 0o002):
            return parent, info
        return None
    return None


def _home_directories() -> list[Path]:
    """``$HOME``, then the account's home from the password database when it
    differs (a container may point ``HOME`` at a shared scratch volume).
    Only absolute paths: a relative one would differ between processes."""
    homes: list[Path] = []
    raw = os.environ.get("HOME") or ""
    if os.path.isabs(raw):
        homes.append(Path(raw))
    try:
        import pwd

        account = pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        account = ""
    if os.path.isabs(account) and Path(account) not in homes:
        homes.append(Path(account))
    return homes


def _once(key: tuple[str, ...]) -> bool:
    if key in _warned_locations:
        return False
    _warned_locations.add(key)
    return True


def shared_slots_directory(name: str) -> Path | None:
    """Where this container's workers share the slots called *name*.

    Every worker of a container opens the same directory; another container
    has its own /tmp. Per uid so a directory left by another user on a shared
    host cannot block startup. ``None`` without POSIX file locks, as on
    Windows, which has no uids to name the directory by either.

    The place is chosen from the environment and the file system alone, so
    every process of one user with the same environment (the query and the
    indexing service) picks the same one:

    1. ``$SANDBOX_STATE_DIR/<name>-<uid>`` when ``SANDBOX_STATE_DIR`` is set
       to an absolute path. No fallback: the operator chose it.
    2. ``$TMPDIR/<name>-<uid>`` (``/tmp``), when another user cannot change
       ``TMPDIR``: it is owned by this uid or root, and not writable by
       everyone unless sticky.
    3. Otherwise (a ``/tmp`` of mode 0777 without the sticky bit, such as a
       Kubernetes ``emptyDir``), ``~/.cache/<name>-<uid>`` under ``$HOME``,
       then under the account's home, if that home is owned by this uid and
       not writable by everyone. One WARNING names both paths.
    4. When none is safe, the ``TMPDIR`` path, which every user refuses with
       one ERROR, so each worker keeps its own state.
    """
    if fcntl is None:
        return None
    leaf = f"{name}-{os.getuid()}"
    override = (os.environ.get(_STATE_DIR_ENV) or "").strip()
    if override:
        if os.path.isabs(override):
            return Path(override) / leaf
        if _once(("relative", override)):
            logger.warning(
                "Ignoring %s=%r: it must be an absolute path, the same for every process",
                _STATE_DIR_ENV, override,
            )
    primary = Path(per_user_temp_dir(name))
    unsafe = _unsafe_ancestor(primary)
    if unsafe is None:
        return primary
    parent, info = unsafe
    owner = -1 if info is None else info.st_uid
    mode = 0 if info is None else stat.S_IMODE(info.st_mode)
    tried: list[str] = []
    for home in _home_directories():
        candidate = home / ".cache" / leaf
        if _unsafe_ancestor(candidate, home=True) is None:
            if _once(("fallback", str(primary), str(candidate))):
                logger.warning(
                    "%s cannot hold shared state safely: its parent %s can be changed by "
                    "another user (owner uid %d, mode %04o; a temp directory needs the "
                    "sticky bit). Using %s instead. Set %s to choose the place",
                    primary, parent, owner, mode, candidate, _STATE_DIR_ENV,
                )
            return candidate
        tried.append(str(candidate))
    # Nothing safe: the TMPDIR path, whose parent every user refuses. One
    # ERROR here, naming every place tried, stands for theirs.
    if info is not None:
        _reported.add((str(parent), info.st_uid, mode))
    if _once(("none", str(primary), *tried)):
        logger.error(
            "No safe place for shared state: the parent %s of %s can be changed by "
            "another user (owner uid %d, mode %04o), and no home directory owned by "
            "uid %d and closed to other users was found (tried: %s). Each worker keeps "
            "its own state, so the caps count per worker; set %s to a directory only "
            "this user can change",
            parent, primary, owner, mode, os.getuid(), ", ".join(tried) or "no HOME",
            _STATE_DIR_ENV,
        )
    return primary


_STATE_ID_ENV = "SANDBOX_STATE_ID"
_STATE_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,63}")
_warned_state_ids: set[str] = set()


def sandbox_state_directory(env: Mapping[str, str] | None = None) -> Path | None:
    """The private directory the code sandbox keeps its cross-worker state in.

    ``/tmp/pipeshub-sandbox-<uid>``, or ``/tmp/pipeshub-sandbox-<id>-<uid>``
    when ``SANDBOX_STATE_ID`` is set; under ``SANDBOX_STATE_DIR`` or the home
    directory instead of ``/tmp`` as `shared_slots_directory` explains. In a container every stack has its own
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
