"""NUMA pinning of indexing cells: off by default, ``PIPESHUB_NUMA_PINNING=auto`` turns it on.

A cell is pinned to one node's CPUs before any ML library loads, so first-touch allocation is
node-local. It needs more than one node and at least as many cells as nodes. Pure helpers here;
:mod:`app.capacity.apply` calls :func:`pin` once at process start.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.capacity.facts import CapacityFacts

NUMA_ENV = "PIPESHUB_NUMA_PINNING"


def enabled(env: Mapping[str, str]) -> bool:
    return (env.get(NUMA_ENV) or "off").strip().lower() == "auto"


def masks(facts: CapacityFacts, cells: int) -> tuple[tuple[int, ...], ...]:
    """One CPU mask per cell index (cells spread over the nodes in turn); empty when pinning does not apply."""
    nodes = [tuple(sorted(cpus)) for _, cpus in sorted(facts.numa) if cpus]
    if len(nodes) < 2 or cells < len(nodes):
        return ()
    return tuple(nodes[i % len(nodes)] for i in range(cells))


def pin(index: int, all_masks: tuple[tuple[int, ...], ...]) -> bool:
    """Restrict this process to its cell's node. False when there is nothing to do or the OS refuses."""
    if not all_masks or not hasattr(os, "sched_setaffinity"):
        return False
    try:
        os.sched_setaffinity(0, set(all_masks[index % len(all_masks)]))
    except OSError:
        return False
    return True
