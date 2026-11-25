"""WP77-PF05-1..3: the worker's and the Model Gateway's startup heap is frozen out of the cyclic GC.

PF-05's 40 calls/s gate showed one stall in every run: per-call overhead 200-330 ms for ~50 calls, max 0.9-1.1 s.
Traced (2026-11-03, WP-77): a generation-2 collection of the process hosting the Tool Gateway and the runs' kernels
took 799 ms, starting at the stalled call and ending as the tail drained; the escalating gen-2 pauses before it
(11 ms to 684 ms) are the same walk over a growing heap. The worker imports the whole app at start (C-17 warm-up):
about 890,000 objects, which every gen-2 collection walks again (445 ms on the shared host) while every run in the
process waits. They live as long as the process, so after warm-up the process moves them to the permanent
generation (``gc.freeze``) and a collection walks only what runs allocate.
"""

from __future__ import annotations

import gc
import inspect
import logging

import pytest

from app.utils.gc_startup import freeze_startup_heap
from harness_core.logs import HarnessLog


def test_wp77_pf05_1_freezing_moves_the_startup_heap_out_of_every_collection(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    held = [[i] for i in range(10_000)]
    try:
        frozen = freeze_startup_heap(HarnessLog.named("tests.wp77"))
        assert frozen >= len(held) and gc.get_freeze_count() >= len(held)
        assert any("process.heap_frozen" in r.getMessage() and f"objects={frozen}" in r.getMessage() for r in caplog.records)
    finally:
        gc.unfreeze()


@pytest.mark.parametrize(
    ("module", "function", "before", "after"),
    [
        ("app.harness_main", "run_worker", "await warm_up(log)", "claimer.start()"),
        ("app.model_gateway_main", "run_gateway", "await server.start(", "await stopped.wait()"),
    ],
)
def test_wp77_pf05_2_each_process_freezes_after_its_start_and_before_it_serves(
    module: str, function: str, before: str, after: str
) -> None:
    import importlib

    source = inspect.getsource(getattr(importlib.import_module(module), function))
    assert "freeze_startup_heap(" in source, f"{module}.{function} does not freeze its startup heap"
    at = source.index("freeze_startup_heap(")
    assert source.index(before) < at < source.index(after), f"{module}: freeze after {before!r} and before {after!r}"
