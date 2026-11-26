"""Round 11 item E (WP-44 x engines): a full worker preempts a native-loop run before an engine run of its class.

R11-PRI-2 an engine attempt yields only before a turn after one it drove, and comes back through a new sandbox and
          readiness; a loop run yields at its next step. Within the lowest held class, the claimer asks a loop run
          first, the longest held first, and an engine run only when no loop run of that class is preemptible.
"""

from __future__ import annotations

from typing import Any

from app.harness.worker.claimer import RunClaimer
from harness_core.priority import PreemptRequests
from harness_core.testing import ManualClock


def _claimer(clock: ManualClock) -> RunClaimer:
    return RunClaimer(
        journal=object(),  # type: ignore[arg-type]
        driver=object(),  # type: ignore[arg-type]
        worker_id="w1",
        slots=2,
        clock=clock,
        sleep=clock.sleep,
        preempt=PreemptRequests(),
        min_slice_s=0,
    )


def _hold(claimer: RunClaimer, held: dict[str, tuple[Any, float, bool]]) -> None:
    claimer._held.update(held)  # pyright: ignore[reportPrivateUsage]
    claimer._node_preempts = True  # pyright: ignore[reportPrivateUsage]
    claimer._busy = 2  # pyright: ignore[reportPrivateUsage]


def test_r11_pri2_a_loop_run_is_preempted_before_an_engine_run_of_its_class() -> None:
    clock = ManualClock()
    claimer = _claimer(clock)
    _hold(claimer, {"engine-old": ("background", 0.0, True), "loop-new": ("background", 5.0, False)})
    clock.advance(10)
    assert claimer._victim() == ("loop-new", "background")  # pyright: ignore[reportPrivateUsage]


def test_r11_pri2_an_engine_run_is_still_preempted_when_it_is_the_only_one() -> None:
    clock = ManualClock()
    claimer = _claimer(clock)
    _hold(claimer, {"engine": ("background", 0.0, True), "turn": ("interactive", 1.0, False)})
    clock.advance(10)
    assert claimer._victim() == ("engine", "background")  # pyright: ignore[reportPrivateUsage]


def test_r11_pri2_class_still_decides_first() -> None:
    clock = ManualClock()
    claimer = _claimer(clock)
    _hold(claimer, {"engine-batch": ("batch", 0.0, True), "loop-bg": ("background", 0.0, False)})
    clock.advance(10)
    victim = claimer._victim()  # pyright: ignore[reportPrivateUsage]
    from harness_core.priority import priority_rank

    lowest = max(("batch", "background"), key=priority_rank)
    assert victim is not None and victim[1] == lowest
