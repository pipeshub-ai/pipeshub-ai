"""NUMA pinning of indexing cells: off by default, ``PIPESHUB_NUMA_PINNING=auto`` turns it on.

A cell is pinned to one node's CPUs before any ML library loads, so first-touch allocation is
node-local. It needs more than one node and at least as many cells as nodes. Pure helpers here;
:mod:`app.capacity.apply` calls :func:`pin` once at process start.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from app.capacity.facts import CapacityFacts

NUMA_ENV = "PIPESHUB_NUMA_PINNING"
# The CPUs this container had before a cell was pinned to one node. A pin is a placement choice, not a
# quota: the governor, which splits the container's CPUs over its cells, must keep sizing from the
# container-wide count (utilisation counters are container-wide too). Children inherit it with the pin.
CONTAINER_CPUS_ENV = "PIPESHUB_CONTAINER_CPUS"


def enabled(env: Mapping[str, str]) -> bool:
    return (env.get(NUMA_ENV) or "off").strip().lower() == "auto"


def masks(facts: CapacityFacts, cells: int) -> tuple[tuple[int, ...], ...]:
    """One CPU mask per cell index (cells spread over the nodes in turn); empty when pinning does not apply."""
    nodes = [tuple(sorted(cpus)) for _, cpus in sorted(facts.numa) if cpus]
    if len(nodes) < 2 or cells < len(nodes):
        return ()
    return tuple(nodes[i % len(nodes)] for i in range(cells))


def pin(
    index: int,
    all_masks: tuple[tuple[int, ...], ...],
    env: MutableMapping[str, str] | None = None,
    *,
    all_threads: bool = False,
) -> bool:
    """Restrict this process to its cell's node. False when there is nothing to do or the OS refuses.

    ``all_threads`` also moves the threads that already exist: the affinity of ``pid 0`` is the calling
    thread's, and threads started later inherit it. A cell pinned at import has no other thread yet;
    one pinned later (see ``apply.pin_claimed_cell``) does.

    On success the CPU count before the pin is left in ``env`` (default ``os.environ``) as
    ``CONTAINER_CPUS_ENV``, for the resource governor and the processes this one starts.
    """
    if not all_masks or not hasattr(os, "sched_setaffinity"):
        return False
    target = os.environ if env is None else env
    mask = set(all_masks[index % len(all_masks)])
    try:
        before = len(os.sched_getaffinity(0))
        os.sched_setaffinity(0, mask)
    except OSError:
        return False
    if all_threads:
        try:
            tids = [int(t) for t in os.listdir("/proc/self/task")]
        except (OSError, ValueError):
            tids = []
        for tid in tids:
            with contextlib.suppress(OSError):  # a thread that just exited
                os.sched_setaffinity(tid, mask)
    target.setdefault(CONTAINER_CPUS_ENV, str(before))
    return True
