"""Cells: one indexing worker process each, with one stats file per cell for ``/health``.

A uvicorn worker only knows its own governor. Each cell claims an index with a lock that dies
with the process, writes its stats to ``<dir>/<index>.json`` every tick, and the worker that
answers ``/health`` merges the fresh files, so every worker returns the same body and one
cell's dead loop or stale file turns the answer into a 503.

The claim is an open-file-description lock (``F_OFD_SETLK``). A reader asks whether it is held with
``F_OFD_GETLK``, which takes no lock, so a ``/health`` read can never make a starting worker lose its
index. The directory must belong to this uid and not be writable by others, and every file is opened
without following links, so another local user cannot redirect a write.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import secrets
import stat
import struct
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no POSIX file locks
    fcntl = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

STALE_TICKS = 3
_MAX_FILE_BYTES = 1 << 20
_MAX_TICK_S = 3600.0  # a file cannot stretch its own staleness window past this
# How long a cell may hold its index before its first write. A worker claims at process start, and
# a service can take minutes to load its models before it publishes, so this is deliberately wide.
STARTUP_GRACE_S = 600.0
CLAIM_ATTEMPTS = 5  # a worker that finds no free index tries again after these delays
CLAIM_BACKOFF_S = 0.05

# struct flock on Linux (64-bit): l_type, l_whence, l_start, l_len, l_pid, padding
_FLOCK = "hhqqi4x"
_LINUX = sys.platform.startswith("linux")
# Some Python builds do not export the open-file-description constants; the Linux values are fixed.
_F_OFD_GETLK = getattr(fcntl, "F_OFD_GETLK", 36)
_F_OFD_SETLK = getattr(fcntl, "F_OFD_SETLK", 37)
_OFD = fcntl is not None and _LINUX and struct.calcsize(_FLOCK) == 32
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key not in _warned:
        _warned.add(key)
        _log.warning(message, *args)


def directory_problem(directory: Path) -> str | None:
    """Why this directory is not safe to share stats through, or ``None``."""
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


def cells_directory(name: str = "pipeshub-indexing-cells") -> Path | None:
    """Where this container's cells share their files, per uid. ``claim_cell`` refuses it unless it is ours and private."""
    if not _OFD:
        return None
    return Path(tempfile.gettempdir()) / f"{name}-{os.getuid()}"


class CellClaim:
    """An exclusive claim on one cell index. Held until ``release`` or the process exits."""

    def __init__(self, directory: Path, index: int, fd: int) -> None:
        self.directory = directory
        self.index = index
        self._fd = fd

    def release(self) -> None:
        if self in _PROCESS:
            _PROCESS.remove(self)
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1


_PROCESS: list[CellClaim] = []  # this process's one claim


def claim_cell(directory: Path, cells: int) -> CellClaim | None:
    """The lowest free index in ``range(cells)``, or ``None`` when all are taken, locks are missing,
    or the directory is not safe (one WARNING, once)."""
    if not _OFD:
        return None
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    problem = directory_problem(directory)
    if problem is not None:
        _warn_once(f"dir:{directory}", "Not publishing cell stats: %s is not a safe cells directory: %s", directory, problem)
        return None
    for index in range(max(1, cells)):
        fd = os.open(directory / f"{index}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.fcntl(fd, _F_OFD_SETLK, struct.pack(_FLOCK, fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0))
        except OSError:
            os.close(fd)
            continue
        # The claim time is the lock file's mtime, so a reader can bound the wait for a first write.
        with contextlib.suppress(OSError):
            os.utime(fd)
        # The index is ours: whatever its dead predecessor left is not this cell's.
        with contextlib.suppress(OSError):
            (directory / f"{index}.json").unlink()
        for leftover in directory.glob(f".{index}.*.tmp"):
            with contextlib.suppress(OSError):
                leftover.unlink()
        return CellClaim(directory, index, fd)
    return None


def process_claim(directory: Path, cells: int) -> CellClaim | None:
    """This process's one claim: NUMA pinning claims at start, the service claims again for its stats
    file, and a second flock on the same file would be refused."""
    if _PROCESS and _PROCESS[0].directory == directory and _PROCESS[0]._fd >= 0:
        return _PROCESS[0]
    _PROCESS.clear()
    claim = claim_cell(directory, cells)
    if claim is not None:
        _PROCESS.append(claim)
    return claim


def write_cell(claim: CellClaim, stats: dict[str, Any], *, loop_failure: str | None, tick_s: float,
               now_ms: int | None = None) -> None:
    """Replace this cell's file atomically."""
    body = {
        "cell": claim.index,
        "pid": os.getpid(),
        "ts_ms": int(time.time() * 1000) if now_ms is None else now_ms,
        "tick_s": tick_s,
        "loop_failure": loop_failure,
        "stats": stats,
    }
    target = claim.directory / f"{claim.index}.json"
    tmp = claim.directory / f".{claim.index}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(body, default=str))
        os.rename(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


@dataclass(frozen=True)
class MergedCells:
    healthy: bool
    cells: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"healthy": self.healthy, "cells": self.cells}


def _slot_held(directory: Path, index: int) -> bool:
    """True while some process holds the cell's index lock. Takes no lock itself."""
    if not _OFD:
        return False
    try:
        fd = os.open(directory / f"{index}.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        probe = struct.pack(_FLOCK, fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)
        answer = fcntl.fcntl(fd, _F_OFD_GETLK, probe)
        return struct.unpack(_FLOCK, answer)[0] != fcntl.F_UNLCK
    except OSError:
        return False
    finally:
        os.close(fd)


def _read_cell_file(path: Path) -> object:
    """The JSON in a cell file, read without following a link or blocking.

    The file must be a regular file of at most ``_MAX_FILE_BYTES``; the read is bounded on the open
    descriptor, so a link to a device or a FIFO cannot hang or exhaust ``/health``.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("cell file is not a regular file")
        chunks: list[bytes] = []
        total = 0
        while total <= _MAX_FILE_BYTES:
            chunk = os.read(fd, _MAX_FILE_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > _MAX_FILE_BYTES:
            raise ValueError("cell file too large")
        return json.loads(b"".join(chunks))
    finally:
        os.close(fd)


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} is not a finite number")
    return float(value)


def _claimed_for_s(directory: Path, index: int, now_ms: int) -> float | None:
    """Seconds since this index was claimed (the lock file's mtime), or ``None`` if unknown."""
    try:
        return max(0.0, now_ms / 1000 - os.lstat(directory / f"{index}.lock").st_mtime)
    except OSError:
        return None


def read_cells(directory: Path, cells: int, *, now_ms: int | None = None) -> MergedCells:
    """The files of the cells whose index lock is held, in index order.

    A held cell whose file is stale, unreadable (including valid JSON that is not an object) or reports
    a dead loop is unhealthy. A file whose lock is free belongs to a cell that died and was not replaced:
    it is ignored, so a dead cell cannot hold ``/health`` at 503 for good. A reader never deletes a file,
    because its view of the lock can be one claim behind and the file may be a new cell's first. A cell that holds its lock but has not written
    yet is starting and is not counted, for ``STARTUP_GRACE_S`` after its claim; a cell still silent
    after that is unhealthy.
    """
    now = int(time.time() * 1000) if now_ms is None else now_ms
    merged: list[dict[str, Any]] = []
    healthy = True
    if (problem := directory_problem(directory)) is not None:
        _warn_once(f"dir:{directory}", "Ignoring cell stats: %s is not a safe cells directory: %s", directory, problem)
        return MergedCells(healthy=True, cells=[])
    for index in range(max(1, cells)):
        path = directory / f"{index}.json"
        if not _slot_held(directory, index):
            continue  # the next cell to claim this index removes its predecessor's file
        entry: dict[str, Any] = {"cell": index, "status": "healthy"}
        try:
            raw = _read_cell_file(path)
            if not isinstance(raw, dict):
                raise ValueError("cell file is not a JSON object")
            tick_s = _finite(raw["tick_s"], "tick_s") if raw.get("tick_s") is not None else 5.0
            if not 0 < tick_s <= _MAX_TICK_S:
                raise ValueError("tick_s out of range")
            age_ms = now - int(_finite(raw["ts_ms"], "ts_ms"))
            entry.update(pid=raw.get("pid"), age_ms=max(0, age_ms), stats=raw.get("stats"))
            if raw.get("loop_failure"):
                entry.update(status="unhealthy", reason="loop_failure", loop_failure=raw["loop_failure"])
            elif age_ms > STALE_TICKS * tick_s * 1000:
                entry.update(status="unhealthy", reason="stale")
        except FileNotFoundError:
            waited = _claimed_for_s(directory, index, now)
            if waited is None or waited <= STARTUP_GRACE_S:
                continue  # still starting
            entry.update(status="unhealthy", reason="no_first_write")
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            entry.update(status="unhealthy", reason="unreadable")
        healthy = healthy and entry["status"] == "healthy"
        merged.append(entry)
    return MergedCells(healthy=healthy, cells=merged)
