"""A long-running process's startup heap, moved out of the cyclic garbage collector (WP-77).

The harness worker imports the whole app before it claims (C-17's warm-up): about 890,000 objects that live as long
as the process. Every generation-2 collection walked them all again, 445 ms on the shared host and 799 ms in PF-05's
rig, and every run's event loop in the process waited for it: the one stall in each 40 calls/s gate run.
``gc.freeze`` moves what exists now to the permanent generation, so a collection walks only what runs allocate.

What startup freed (import-time temporaries, a tokenizer's rank table once its Rust core holds it) stays in the
process's heap unless the allocator is asked to give it back: ``malloc_trim`` returns those pages to the system,
about 10 MiB of the idle worker's resident set (R25OPS-FU-1). Where the C library has no ``malloc_trim`` (musl,
macOS) the heap is left as it is.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_core.logs import HarnessLog


def release_freed_heap() -> bool:
    """Returns the heap's free pages to the system; False where the C library cannot."""
    name = ctypes.util.find_library("c")
    try:
        trim = ctypes.CDLL(name).malloc_trim if name else None
    except (OSError, AttributeError):
        trim = None
    if trim is None:
        return False
    _ = trim(0)
    return True


def freeze_startup_heap(log: HarnessLog) -> int:
    """Collects the startup garbage first (so none of it is kept for good), freezes the rest and gives the freed pages
    back; how many objects the permanent generation holds. Called once, after the process has imported and built
    what it serves with."""
    _ = gc.collect()
    gc.freeze()
    frozen = gc.get_freeze_count()
    trimmed = release_freed_heap()
    log.info("process.heap_frozen", objects=frozen, trimmed=trimmed)
    return frozen


__all__ = ["freeze_startup_heap", "release_freed_heap"]
