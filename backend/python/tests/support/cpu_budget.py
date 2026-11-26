"""CPU time of the calling thread, with the cyclic collector paused, for a bound on work the code does itself.

A wall-clock delta also holds the time the process spent off the CPU, which a full gate on the shared host makes
seconds long. ``time.thread_time()`` does not, but a collection of a worker's large heap still lands in whichever
test triggers it (a 3.6 s CPU "stall" on a gate for a stream that takes 0.4 ms alone, WALLCLOCK-1). With the
collector paused for the block, the reading is the code's own work: use it to hold a guard against quadratic or
catastrophic work (minutes when it regresses, milliseconds when it holds) to a bound with that margin.

It counts this thread only: work handed to another thread or process is not in it.
"""

from __future__ import annotations

import contextlib
import gc
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class CpuReading:
    seconds: float = 0.0


@contextlib.contextmanager
def thread_cpu() -> Iterator[CpuReading]:
    reading = CpuReading()
    collecting = gc.isenabled()
    gc.disable()
    started = time.thread_time()
    try:
        yield reading
    finally:
        reading.seconds = time.thread_time() - started
        if collecting:
            gc.enable()
