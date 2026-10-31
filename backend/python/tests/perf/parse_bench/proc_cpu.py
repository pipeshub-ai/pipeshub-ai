"""Cores used by a process and all its descendants, read from ``/proc``.

``getrusage(RUSAGE_CHILDREN)`` counts only children that were waited for, so a pool worker that is
still alive, or killed with its process group, is missing from it. This sampler reads
``/proc/<pid>/stat`` for the root and every descendant on each tick and keeps the highest CPU
reading seen per pid, so a process that exits between ticks still counts for what it had used.
Cores used over a window is the CPU seconds added in it divided by its wall time.

Known limit: a grandchild whose parent dies is re-parented outside the tree, so its CPU from then
on is not counted. The parse pool and its raster children do not orphan processes while a run is
measured, so the benchmark is not affected.

Stdlib only, no psutil: it must work in a minimal container.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

TICKS_PER_SECOND = os.sysconf("SC_CLK_TCK")
# A process that added at least this many CPU seconds since the last tick counts as busy.
BUSY_SECONDS_PER_TICK = 0.02


def parse_stat(text: str) -> tuple[int, int] | None:
    """``(ppid, utime + stime in clock ticks)`` from the text of ``/proc/<pid>/stat``, or None.

    The command name is in parentheses and may itself hold spaces and parentheses, so the fields
    are counted from the last ``)``.
    """
    end = text.rfind(")")
    if end < 0:
        return None
    fields = text[end + 2:].split()
    # After the name: state(0) ppid(1) ... utime(11) stime(12)
    if len(fields) < 13:
        return None
    try:
        return int(fields[1]), int(fields[11]) + int(fields[12])
    except ValueError:
        return None


def read_table(proc: Path = Path("/proc")) -> dict[int, tuple[int, int]]:
    """``pid -> (ppid, cpu ticks)`` for every process that can be read right now."""
    table: dict[int, tuple[int, int]] = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parsed = parse_stat((entry / "stat").read_text())
        except OSError:
            continue
        if parsed is not None:
            table[int(entry.name)] = parsed
    return table


def descendants(table: dict[int, tuple[int, int]], root: int) -> set[int]:
    """*root* and every process below it in *table*."""
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    found, todo = {root}, [root]
    while todo:
        for child in kids.get(todo.pop(), ()):
            if child not in found:
                found.add(child)
                todo.append(child)
    return found


def cores_used(cpu_seconds: float, wall_seconds: float) -> float:
    return cpu_seconds / wall_seconds if wall_seconds > 0 else 0.0


class DescendantCpu(threading.Thread):
    """Samples the CPU of *root* and its descendants; ``total_seconds()`` includes exited ones."""

    def __init__(self, root: int | None = None, interval: float = 0.25, proc: Path = Path("/proc")) -> None:
        super().__init__(name="descendant-cpu", daemon=True)
        self.root = os.getpid() if root is None else root
        self.interval = interval
        self._proc = proc
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._seen: dict[int, int] = {}  # pid -> highest tick count read
        self._ticks = 0
        self.busy_sum = 0  # busy descendants summed over ticks
        self.busy_peak = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.sample()
            self._stop_event.wait(self.interval)

    def sample(self) -> None:
        try:
            table = read_table(self._proc)
        except OSError:
            return
        mine = descendants(table, self.root)
        busy = 0
        with self._lock:
            for pid in mine:
                ticks = table[pid][1]
                before = self._seen.get(pid)
                # A process first seen mid-run is charged from its start: all its ticks count.
                delta = ticks if before is None else max(0, ticks - before)
                if delta / TICKS_PER_SECOND >= BUSY_SECONDS_PER_TICK:
                    busy += 1
                self._seen[pid] = max(ticks, before or 0)
            self._ticks += 1
            self.busy_sum += busy
            self.busy_peak = max(self.busy_peak, busy)

    def total_seconds(self) -> float:
        with self._lock:
            return sum(self._seen.values()) / TICKS_PER_SECOND

    def mean_busy(self) -> float:
        with self._lock:
            return self.busy_sum / self._ticks if self._ticks else 0.0
