"""Sum every worker's load ledger over the alert window."""

from __future__ import annotations

import json
import logging
import math
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agent_loop_lib.sandbox.load_ledger import (
    FILE_VERSION,
    FLAG_BACKED_OFF,
    FLAG_QUALIFYING,
    KEEP_S,
    MINUTE_S,
    TICK_S,
)

logger = logging.getLogger(__name__)

# A file not written for this long belongs to a worker that is gone and holds
# nothing the longest window still needs.
STALE_AFTER_S = KEEP_S + 300

_REASONS = ("slots", "memory", "cpu", "orgShare")

# The files are written by this app, but anything that runs as its user can
# put files there too, so each one is read as untrusted input: a regular file
# (never a FIFO or a link), at most this big, nested at most this deep, with
# every number finite and in range. A file that breaks a rule is skipped.
MAX_FILE_BYTES = 64 * 1024
MAX_DEPTH = 6
MAX_FILES = 1024
# More runs in one minute of one worker than this is not a count.
MAX_COUNT = 1_000_000
_FLAG_MASK = 0xFF
_WARN_EVERY_S = 300.0
_warned = {"at": -math.inf}


class _BadFile(ValueError):
    pass


def _warn_skipped(name: str, why: object) -> None:
    now = time.monotonic()
    if now - _warned["at"] >= _WARN_EVERY_S:
        _warned["at"] = now
        logger.warning("sandbox load alerts: skipping %s: %s", name, why)
    else:
        logger.debug("sandbox load alerts: skipping %s: %s", name, why)


def _read_file(path: Path) -> object:
    """The parsed JSON of *path*; raises ``_BadFile`` (or ``OSError``,
    ``ValueError``) for one that breaks a rule. Never blocks on a FIFO and
    never reads more than ``MAX_FILE_BYTES``."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _BadFile("not a regular file")
        # The directory is private to this user, so only the owner is checked.
        if info.st_uid != os.getuid():
            raise _BadFile("owned by another user")
        if info.st_size > MAX_FILE_BYTES:
            raise _BadFile(f"{info.st_size} bytes, over {MAX_FILE_BYTES}")
        chunks: list[bytes] = []
        size = 0
        while size <= MAX_FILE_BYTES:
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    if size > MAX_FILE_BYTES:
        raise _BadFile(f"over {MAX_FILE_BYTES} bytes")
    data = json.loads(b"".join(chunks))
    _check_depth(data)
    return data


def _check_depth(value: object) -> None:
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth >= MAX_DEPTH:
                raise _BadFile(f"nested over {MAX_DEPTH} levels")
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in children)


@dataclass(frozen=True)
class WindowTotals:
    window_s: int
    runs: int = 0
    waited: int = 0
    refused: int = 0
    waited_unqualified: int = 0
    refused_unqualified: int = 0
    cancelled_waiting: int = 0
    waited_by_reason: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_REASONS, 0))
    max_wait_s: float = 0.0
    # 10 s ticks in the window in which any worker held a qualifying run back.
    busy_ticks: int = 0
    # Start of the earliest tick in which a memory wait ended with indexing backed off.
    backed_off_since: float | None = None
    files: int = 0
    stale: tuple[Path, ...] = ()


def _int(value: object) -> int:
    """A count: an int in ``0..MAX_COUNT``, else 0."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_COUNT:
        return value
    return 0


def _seconds(value: object, high: float) -> float:
    """A finite number of seconds in ``0..high``, else 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) and 0.0 <= number <= high else 0.0


def read_window(directory: Path, now: float, window_s: int) -> WindowTotals:
    """Totals over ``[now - window_s, now]`` from every ``w-*.json`` in
    *directory*. Busy ticks are OR-ed across files, so four workers held back
    in the same 10 s count once. Unreadable or invalid files are skipped.
    Files older than ``STALE_AFTER_S`` are listed in ``stale`` for the caller
    to delete; nothing is deleted here."""
    since = now - window_s
    sums = dict.fromkeys(
        ("runs", "waited", "refused", "waitedUnqualified", "refusedUnqualified", "cancelledWaiting"), 0,
    )
    by_reason = dict.fromkeys(_REASONS, 0)
    max_wait = 0.0
    ticks: dict[int, int] = {}
    files = 0
    stale: list[Path] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return WindowTotals(window_s=window_s)
    if len(entries) > MAX_FILES:
        _warn_skipped(f"{len(entries) - MAX_FILES} of {len(entries)} files", f"more than {MAX_FILES}")
        entries = sorted(entries, key=lambda e: e.name)[:MAX_FILES]
    for entry in entries:
        name = entry.name
        if not name.startswith("w-"):
            continue
        path = Path(entry.path)
        if name.endswith(".tmp"):
            try:
                if entry.stat(follow_symlinks=False).st_mtime < now - STALE_AFTER_S:
                    stale.append(path)
            except OSError:
                pass
            continue
        if not name.endswith(".json"):
            continue
        try:
            data = _read_file(path)
        except FileNotFoundError:
            continue  # its worker replaced or removed it since the listing
        except (OSError, ValueError, RecursionError) as exc:
            _warn_skipped(name, exc)
            continue
        if not isinstance(data, dict) or data.get("v") != FILE_VERSION:
            logger.debug("sandbox load alerts: skipping %s of another version", name)
            continue
        updated = data.get("updatedAt")
        if isinstance(updated, (int, float)) and not isinstance(updated, bool) and updated < now - STALE_AFTER_S:
            stale.append(path)
            continue
        try:
            part = _file_totals(data, since, now)
        except Exception as exc:  # one bad file must never stop the evaluation
            _warn_skipped(name, exc)
            continue
        files += 1
        file_sums, file_reasons, file_wait, file_ticks = part
        for key, value in file_sums.items():
            sums[key] += value
        for key, value in file_reasons.items():
            by_reason[key] += value
        max_wait = max(max_wait, file_wait)
        for tick, flags in file_ticks.items():
            ticks[tick] = ticks.get(tick, 0) | flags
    backed_off = [tick for tick, flags in ticks.items() if flags & FLAG_BACKED_OFF]
    return WindowTotals(
        window_s=window_s,
        runs=sums["runs"],
        waited=sums["waited"],
        refused=sums["refused"],
        waited_unqualified=sums["waitedUnqualified"],
        refused_unqualified=sums["refusedUnqualified"],
        cancelled_waiting=sums["cancelledWaiting"],
        waited_by_reason=by_reason,
        max_wait_s=max_wait,
        busy_ticks=sum(1 for flags in ticks.values() if flags & FLAG_QUALIFYING),
        backed_off_since=min(backed_off) * TICK_S if backed_off else None,
        files=files,
        stale=tuple(stale),
    )


def _key(raw: str) -> int | None:
    # A short decimal only: int() of a long string is slow, and no minute or
    # tick number is longer than this.
    if len(raw) > 15:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _file_totals(
    data: dict[str, Any], since: float, now: float,
) -> tuple[dict[str, int], dict[str, int], float, dict[int, int]]:
    """One worker's file summed over ``(since, now]``."""
    sums = dict.fromkeys(
        ("runs", "waited", "refused", "waitedUnqualified", "refusedUnqualified", "cancelledWaiting"), 0,
    )
    by_reason = dict.fromkeys(_REASONS, 0)
    max_wait = 0.0
    ticks: dict[int, int] = {}
    minutes = data.get("minutes")
    for key, bucket in (minutes.items() if isinstance(minutes, dict) else ()):
        minute = _key(key)
        if minute is None:
            continue
        # A minute that starts after now is left by a clock that was
        # stepped back; it is not part of this window.
        if (minute + 1) * MINUTE_S <= since or minute * MINUTE_S > now or not isinstance(bucket, dict):
            continue
        for name_ in sums:
            sums[name_] += _int(bucket.get(name_))
        reasons = bucket.get("waitedByReason")
        if isinstance(reasons, dict):
            for reason in _REASONS:
                by_reason[reason] += _int(reasons.get(reason))
        # No wait is recorded longer than the ledger keeps (KEEP_S).
        max_wait = max(max_wait, _seconds(bucket.get("maxWaitS"), KEEP_S))
    raw_ticks = data.get("ticks")
    for key, flags in (raw_ticks.items() if isinstance(raw_ticks, dict) else ()):
        tick = _key(key)
        if tick is None:
            continue
        # Ticks starting in (since, now]: exactly window_s / TICK_S of them,
        # so the busy share never passes 100 %.
        if tick * TICK_S <= since or tick * TICK_S > now:
            continue
        ticks[tick] = ticks.get(tick, 0) | (_int(flags) & _FLAG_MASK)
    return sums, by_reason, max_wait, ticks
