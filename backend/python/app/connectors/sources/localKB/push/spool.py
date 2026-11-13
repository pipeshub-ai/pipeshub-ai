"""Parent-owned scratch files for a push's body and stored bytes.

The body is written here by the connectors process and read by the worker; the
worker writes the stored bytes here and the upload reads them. A 16 MiB body then
never crosses a pipe, and the files of a dead worker are still owned (and
removed) by the parent. Each process run uses its own directory.

A run directory is created under a ``.new-`` name, its ``.alive`` file is locked
(``flock``, held for the life of the process), and only then is it renamed to
``run-``. The sweeper removes a ``run-`` directory only when it can take that lock
(its owner is gone) and the directory is older than a floor, so a live process is
never swept, whatever the interleaving. A lock error other than "held" means skip, at
any age: a failed probe says nothing about the owner, and an mtime does not prove it died.
A descriptor inherited by a forked child keeps a directory alive after its owner
died (a leak, never an early delete).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import shutil
import tempfile
import time
import uuid
from typing import TYPE_CHECKING

from .contract import DISK_FULL, spool_full

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

try:
    import fcntl
except ImportError:  # no flock on this platform: runs then go by age only
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

RUN_PREFIX = "run-"
NEW_PREFIX = ".new-"
TRASH_PREFIX = ".trash-"
ALIVE = ".alive"
#: A dead owner's directory younger than this stays (it may still be being created or read).
FLOOR_S = 900.0
#: A directory without a lock file (a crash between creating it and locking it).
NO_LOCK_AGE_S = 1800.0
#: Leftovers of an interrupted creation or removal.
LEFTOVER_AGE_S = 3600.0
#: Work directories and files inside a live run that are older than this belong to a push that is long gone.
LIVE_TRIM_AGE_S = 1800.0
#: Without flock on the platform, a run directory goes by this age alone.
UNLOCKABLE_AGE_S = 86400.0
SWEEP_INTERVAL_S = 300.0


def default_root() -> str:
    return os.environ.get("PUSH_SPOOL_DIR") or os.path.join(tempfile.gettempdir(), "pipeshub-push-spool")


def _age(path: str, now: float) -> float | None:
    try:
        return now - os.stat(path, follow_symlinks=False).st_mtime
    except OSError:
        return None


def _owner_state(path: str) -> str:
    """``dead`` (lock taken and released), ``alive`` (held), ``none`` (no lock file), ``error`` (the probe failed)
    or ``unknown`` (no flock on this platform)."""
    if fcntl is None:
        return "unknown"
    try:
        fd = os.open(os.path.join(path, ALIVE), os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return "none"
    except OSError:
        return "error"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "alive"
        except OSError:
            return "error"
        return "dead"
    finally:
        os.close(fd)


def sweep(root: str, floor_s: float = FLOOR_S, max_age_s: float = NO_LOCK_AGE_S, now: float | None = None,
          leftover_age_s: float = LEFTOVER_AGE_S) -> int:
    """Remove the directories of runs that ended; returns how many. Blocking: call it in a thread from the loop."""
    now = time.time() if now is None else now
    removed = unprobed = 0
    if not os.path.isdir(root):
        return 0
    for entry in os.scandir(root):
        if not entry.is_dir(follow_symlinks=False):
            continue
        age = _age(entry.path, now)
        if age is None:
            continue
        name = entry.name
        if name.startswith(RUN_PREFIX):
            state = _owner_state(entry.path)
            unprobed += state == "error"
            doomed = (
                (state == "dead" and age > floor_s)
                or (state == "none" and age > max_age_s)
                or (state == "unknown" and age > UNLOCKABLE_AGE_S)
            )
        elif name.startswith(NEW_PREFIX):
            doomed = age > leftover_age_s and _owner_state(entry.path) in ("dead", "none")
        elif name.startswith(TRASH_PREFIX):
            doomed = age > leftover_age_s
        else:
            continue
        if doomed:
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
    if unprobed:
        logger.warning("the push spool sweep skipped %d run directories whose lock could not be probed", unprobed)
    return removed


def spool_report(root: str, mountinfo: str = "/proc/self/mountinfo") -> dict[str, str]:
    """The file system the spool lives on: ``{"fstype", "mount"}`` ("unknown" when it cannot be read)."""
    unknown = {"fstype": "unknown", "mount": "unknown"}
    try:
        with open(mountinfo, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return unknown
    target = os.path.realpath(root)
    best: tuple[int, dict[str, str]] | None = None
    for line in lines:
        left, sep, right = line.partition(" - ")
        fields, rest = left.split(), right.split()
        if not sep or len(fields) < 5 or not rest:
            continue
        mount = fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")
        inside = target == mount or target.startswith(mount.rstrip("/") + "/")
        if inside and (best is None or len(mount) >= best[0]):
            best = (len(mount), {"fstype": rest[0], "mount": mount})
    return best[1] if best else unknown


class Spool:
    def __init__(self, root: str | None = None) -> None:
        self.root = root or default_root()
        try:
            os.makedirs(self.root, mode=0o700, exist_ok=True)
        except OSError as e:
            if e.errno in DISK_FULL:
                raise spool_full() from e
            raise
        name = uuid.uuid4().hex
        self.dir = os.path.join(self.root, f"{RUN_PREFIX}{name}")
        self._lock_fd: int | None = None
        try:
            self._create_locked(os.path.join(self.root, f"{NEW_PREFIX}{name}"))
        except OSError as e:
            # A full disk here must not fail every push: run without the lock; the sweeper then keeps the
            # directory for the long age and the push that cannot write answers SPOOL_FULL by itself.
            logger.warning("the push spool could not take its lock file (errno %s); running without it", e.errno)
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            with contextlib.suppress(OSError):
                os.makedirs(self.dir, mode=0o700, exist_ok=True)

    def _create_locked(self, tmp: str) -> None:
        os.makedirs(tmp, mode=0o700)
        try:
            fd = os.open(os.path.join(tmp, ALIVE), os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o600)
            if fcntl is not None:
                self._lock_fd = fd
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                os.close(fd)
            os.rename(tmp, self.dir)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def new_path(self, suffix: str) -> str:
        return os.path.join(self.dir, f"{uuid.uuid4().hex}.{suffix}")

    def write(self, data: bytes) -> str:
        path = self.new_path("in")
        try:
            # Another process's sweep may have removed this idle process's directory.
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            self.remove(path)
            if e.errno in DISK_FULL:
                raise spool_full() from e
            raise
        return path

    def touch(self) -> None:
        with contextlib.suppress(OSError):
            os.utime(self.dir)

    @staticmethod
    def remove(*paths: str | None) -> None:
        for p in paths:
            if p:
                with contextlib.suppress(OSError):
                    os.unlink(p)

    def trim(self, max_age_s: float = LIVE_TRIM_AGE_S, now: float | None = None) -> int:
        """Remove work directories and files of this run that no live push can still own (a push is far shorter).

        Each is renamed to a ``.trash-`` name first, so a half-removed directory is never seen by a push."""
        now = time.time() if now is None else now
        removed = 0
        try:
            entries = list(os.scandir(self.dir))
        except OSError:
            return 0
        for entry in entries:
            if entry.name == ALIVE:
                continue
            age = _age(entry.path, now)
            if age is None or age <= max_age_s:
                continue
            trash = os.path.join(self.root, f"{TRASH_PREFIX}{uuid.uuid4().hex}")
            try:
                os.rename(entry.path, trash)
            except OSError:
                continue
            if os.path.isdir(trash) and not os.path.islink(trash):
                shutil.rmtree(trash, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    os.unlink(trash)
            removed += 1
        return removed

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
        if self._lock_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._lock_fd)
            self._lock_fd = None


async def sweep_loop(spool: Spool, interval_s: float = SWEEP_INTERVAL_S, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, **kwargs: float) -> None:
    """Sweep other runs' directories and trim this run's, at once and then forever, always in a thread. One failed
    round never ends it. ``spool`` holds its lock before this task exists, so no pass can see its run as dead."""
    while True:
        try:
            removed = await asyncio.to_thread(sweep, spool.root, **kwargs)
            removed += await asyncio.to_thread(spool.trim)
            if removed:
                logger.info("the push spool sweep removed %d leftover entries", removed)
        except Exception as e:  # the timer must outlive any one failure
            logger.warning("the push spool sweep failed: %s", e)
        await sleep(interval_s * random.uniform(0.9, 1.1))
