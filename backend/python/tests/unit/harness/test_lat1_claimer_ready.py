"""LAT1 NT-3, NT-4, NT-5, NT-11: an idle claimer polls at once on ``run-ready``,
and its poll still claims a run whose notice never came. No sleeps: the poll
interval is an hour wherever only a notice may end the wait."""

from __future__ import annotations

import asyncio

from app.harness.worker.claimer import (
    MAX_IDLE_POLL_S,
    READY_MAX_IDLE_POLL_S,
    RunClaimer,
)
from harness_core.driver import RunDriver
from harness_core.journal import InMemoryJournal
from harness_core.journal.ready import InMemoryRunReadySignal
from harness_core.testing import ManualClock
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness.test_run_claimer import _Assembler, _GatedModel, _until

HOUR_S = 3600.0


def _world(
    *, ready: InMemoryRunReadySignal | None, idle_poll_s: float = HOUR_S, ready_max_idle_poll_s: float = HOUR_S
) -> tuple[InMemoryJournal, RunClaimer, _Assembler]:
    clock = ManualClock()
    journal = InMemoryJournal(clock=clock)
    gate = asyncio.Event()
    gate.set()
    assembler = _Assembler(_GatedModel(gate))
    claimer = RunClaimer(
        journal=journal,
        driver=RunDriver(journal=journal, assembler=assembler, clock=clock),
        worker_id="w1",
        slots=2,
        clock=clock,
        idle_poll_s=idle_poll_s,
        max_idle_poll_s=idle_poll_s,
        ready=ready,
        ready_max_idle_poll_s=ready_max_idle_poll_s,
    )
    return journal, claimer, assembler


def _admit(journal: InMemoryJournal, run_id: str) -> None:
    journal.admit(run_id, org_id="org-1", session_id=f"ses-{run_id}", spec=minimal_run_spec(), input_text="q")


async def _idle(claimer: RunClaimer) -> None:
    """Until the poll loop has found the queue empty and waits."""
    await _until(lambda: claimer._loop_task is not None and claimer._claiming == 0 and not claimer._wake.is_set())
    for _ in range(3):
        await asyncio.sleep(0)


async def test_nt4_a_notice_ends_the_longest_back_off_and_the_run_is_claimed() -> None:
    ready = InMemoryRunReadySignal()
    journal, claimer, assembler = _world(ready=ready)
    claimer.start()
    await _idle(claimer)
    _admit(journal, "run-1")
    ready.notify()
    await _until(lambda: assembler.built == ["run-1"])
    await claimer.stop()


async def test_nt4_without_a_notice_an_hour_long_poll_has_not_claimed_it() -> None:
    journal, claimer, assembler = _world(ready=InMemoryRunReadySignal())
    claimer.start()
    await _idle(claimer)
    _admit(journal, "run-1")
    for _ in range(50):
        await asyncio.sleep(0)
    assert assembler.built == []
    await claimer.stop()


async def test_nt3_nt5_a_notice_that_never_came_costs_one_poll_never_the_run() -> None:
    # Node crashed between the commit and the publish, or the notice was lost.
    ready = InMemoryRunReadySignal()
    journal, claimer, assembler = _world(ready=ready, idle_poll_s=0.01, ready_max_idle_poll_s=0.02)
    claimer.start()
    await _idle(claimer)
    _admit(journal, "run-lost")
    await _until(lambda: assembler.built == ["run-lost"])
    await claimer.stop()


async def test_nt5_a_notice_sent_before_anyone_listened_is_lost_and_the_poll_claims() -> None:
    ready = InMemoryRunReadySignal()
    journal, claimer, assembler = _world(ready=ready, idle_poll_s=0.01, ready_max_idle_poll_s=0.02)
    _admit(journal, "run-early")
    claimer.start()
    await _until(lambda: assembler.built == ["run-early"])
    await claimer.stop()


class _Unhealthy(InMemoryRunReadySignal):
    @property
    def healthy(self) -> bool:
        return False


class _Broken(InMemoryRunReadySignal):
    async def wait(self, timeout_s: float) -> bool:
        raise RuntimeError("adapter bug")


async def test_nt11_the_backstop_poll_is_longer_only_while_notices_can_arrive() -> None:
    assert READY_MAX_IDLE_POLL_S > MAX_IDLE_POLL_S
    for ready, expected in [
        (None, MAX_IDLE_POLL_S),
        (InMemoryRunReadySignal(), READY_MAX_IDLE_POLL_S),
        (_Unhealthy(), MAX_IDLE_POLL_S),
    ]:
        claimer = RunClaimer(
            journal=InMemoryJournal(clock=ManualClock()),
            driver=None,  # type: ignore[arg-type]
            worker_id="w",
            slots=1,
            clock=ManualClock(),
            ready=ready,
        )
        assert claimer._longest_idle_poll_s() == expected, ready


async def test_nt11_a_broken_listener_hands_the_poll_back() -> None:
    journal, claimer, _ = _world(ready=_Broken(), idle_poll_s=0.01, ready_max_idle_poll_s=0.02)
    claimer.start()
    await _until(lambda: claimer._listen_task is not None and claimer._listen_task.done())
    assert claimer._longest_idle_poll_s() == 0.01, "the configured back-off without notices"
    await claimer.stop()
