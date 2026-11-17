"""CPU time of a child process, read from ``/proc`` so a budget kill can tell a busy body from a slow dependency."""

from __future__ import annotations

import os

try:
    _TICKS = os.sysconf("SC_CLK_TCK")
except (AttributeError, ValueError, OSError):  # not a platform with /proc
    _TICKS = 0


def parse_stat(text: str) -> tuple[int, int] | None:
    """``(utime + stime in ticks, starttime)`` from the contents of ``/proc/<pid>/stat``.

    The process name is the second field and may hold spaces and ``)``, so the fields are counted from the
    last ``)`` and never from the start of the line.
    """
    rest = text.rsplit(")", 1)
    if len(rest) != 2:
        return None
    fields = rest[1].split()
    try:
        return int(fields[11]) + int(fields[12]), int(fields[19])
    except (IndexError, ValueError):
        return None


def read(pid: int | None, proc_root: str = "/proc") -> tuple[int, int] | None:
    """One reading of a live process, or None when it cannot be taken (no /proc, gone, unreadable)."""
    if pid is None or _TICKS <= 0:
        return None
    try:
        with open(f"{proc_root}/{pid}/stat", encoding="utf-8", errors="replace") as f:
            return parse_stat(f.read())
    except OSError:
        return None


def seconds_between(
    start: tuple[int, int] | None, end: tuple[int, int] | None
) -> float | None:
    """CPU seconds used between two readings of the same process; None when either is missing or the process
    is not the same one (a recycled pid has another start time)."""
    if start is None or end is None or start[1] != end[1] or _TICKS <= 0:
        return None
    return max(0, end[0] - start[0]) / _TICKS


#: A budget kill is a strike when the child used at least this share of the wall time it held the process.
CPU_STRIKE_RATIO = 0.5


def cpu_bound(cpu_s: float | None, wall_s: float) -> bool:
    return cpu_s is not None and wall_s > 0 and cpu_s / wall_s >= CPU_STRIKE_RATIO
