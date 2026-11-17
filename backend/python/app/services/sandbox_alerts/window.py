"""Sum every worker's load ledger over the alert window."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

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
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


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
            with open(path, "rb") as fh:
                data = json.loads(fh.read())
        except (OSError, ValueError):
            logger.debug("sandbox load alerts: skipping unreadable %s", name, exc_info=True)
            continue
        if not isinstance(data, dict) or data.get("v") != FILE_VERSION:
            logger.debug("sandbox load alerts: skipping %s of another version", name)
            continue
        updated = data.get("updatedAt")
        if isinstance(updated, (int, float)) and updated < now - STALE_AFTER_S:
            stale.append(path)
            continue
        files += 1
        minutes = data.get("minutes")
        for key, bucket in (minutes.items() if isinstance(minutes, dict) else ()):
            try:
                minute = int(key)
            except ValueError:
                continue
            if (minute + 1) * MINUTE_S <= since or not isinstance(bucket, dict):
                continue
            for name_ in sums:
                sums[name_] += _int(bucket.get(name_))
            reasons = bucket.get("waitedByReason")
            if isinstance(reasons, dict):
                for reason in _REASONS:
                    by_reason[reason] += _int(reasons.get(reason))
            wait = bucket.get("maxWaitS")
            if isinstance(wait, (int, float)):
                max_wait = max(max_wait, float(wait))
        raw_ticks = data.get("ticks")
        for key, flags in (raw_ticks.items() if isinstance(raw_ticks, dict) else ()):
            try:
                tick = int(key)
            except ValueError:
                continue
            if tick * TICK_S < since or tick * TICK_S > now:
                continue
            ticks[tick] = ticks.get(tick, 0) | _int(flags)
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
