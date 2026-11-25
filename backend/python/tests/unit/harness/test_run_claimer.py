"""P1.16: run slots over the Mongo claim queue; the broker only wakes (O-4, O-4a, O-5)."""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.inline import drain_on_termination
from app.harness.worker.claimer import (
    BASE_MIB,
    MAX_SLOTS,
    MIB,
    PER_RUN_MIB,
    DispatchHints,
    RunClaimer,
    run_slots,
)
from app.services.messaging.config import StreamMessage
from harness_core import bindings
from harness_core.driver import AssembledRuntime, RunDriver
from harness_core.journal import InMemoryContentStore, InMemoryJournal
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.durable import SampledTurn
from harness_core.testing import ManualClock
from harness_core.testing.clock import settle
from harness_core.testing.durable import FunctionToolPort
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.journal import Claim
    from harness_core.kernel.durable import DeltaKind, ModelCall



class _GatedModel:
    """Answers once ``gate`` opens; counts runs waiting on it concurrently."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.waiting = 0
        self.peak = 0
        self.entered = asyncio.Event()

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        self.entered.set()
        self.waiting += 1
        self.peak = max(self.peak, self.waiting)
        await self.gate.wait()
        self.waiting -= 1
        return SampledTurn(message=AssistantMessage(content=[TextPart(text="done")]))

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return None


class _Assembler:
    def __init__(self, model: _GatedModel) -> None:
        self.model = model
        self.content = InMemoryContentStore()
        self.built: list[str] = []

    async def build(self, claim: Claim) -> AssembledRuntime:
        self.built.append(claim.run_id)
        return AssembledRuntime(
            model=self.model, tools=FunctionToolPort(), content=self.content, bindings=bindings.NEUTRAL
        )


IDLE_POLL_S = 0.01


@dataclass
class _World:
    journal: InMemoryJournal
    claimer: RunClaimer
    model: _GatedModel
    clock: ManualClock

    async def poll_again(self) -> None:
        """The idle poller is waiting on the clock; its next poll is due once the clock passes it."""
        await settle(lambda: self.clock.pending_timers() > 0)
        _ = self.clock.advance(2 * IDLE_POLL_S)


def _world(
    runs: int, *, slots: int, gate_open: bool = False, claims: Any = None, clock: ManualClock | None = None,  # noqa: ANN401
    **options: float,
) -> _World:
    clock = clock or ManualClock()
    journal = InMemoryJournal(clock=clock)
    for index in range(runs):
        journal.admit(f"run-{index}", org_id="org-1", session_id=f"ses-{index}", spec=minimal_run_spec(), input_text="q")
    gate = asyncio.Event()
    if gate_open:
        gate.set()
    model = _GatedModel(gate)
    driver = RunDriver(journal=journal, assembler=_Assembler(model), clock=clock)
    claimer = RunClaimer(
        journal=claims(journal) if claims is not None else journal, driver=driver, worker_id="w1", slots=slots,
        clock=clock, sleep=clock.sleep, idle_poll_s=IDLE_POLL_S, max_idle_poll_s=2 * IDLE_POLL_S, **options,
    )
    return _World(journal, claimer, model, clock)


def _assembled(claimer: RunClaimer) -> list[str]:
    return claimer._driver.assembler.built  # type: ignore[attr-defined]


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    return [r.harness for r in caplog.records if getattr(r, "harness_event", None) == event]  # type: ignore[attr-defined]


async def _until(condition: Callable[[], bool]) -> None:
    await settle(condition, turns=20_000)


async def _draining(draining: asyncio.Task[None], caplog: pytest.LogCaptureFixture) -> None:
    """The drain has started and is waiting: what it waits for has not answered yet."""
    await settle(lambda: bool(_events(caplog, "slots.drain")))
    await _turns(50)
    assert not draining.done()


async def _turns(count: int) -> None:
    """Lets every other task run ``count`` times: what could happen without the clock moving has happened."""
    for _ in range(count):
        await asyncio.sleep(0)


def _hint(run_id: str, kind: str = "run_ready") -> StreamMessage:
    return StreamMessage(
        eventType="agent_run_dispatch_requested",
        payload={"schemaVersion": 1, "commandId": f"c-{run_id}", "runId": run_id, "orgId": "org-1", "kind": kind, "createdAt": 1},
    )


async def test_o4_twenty_runs_progress_together_on_one_worker() -> None:
    w = _world(20, slots=20)
    w.claimer.start()
    await _until(lambda: w.model.waiting == 20)
    w.model.gate.set()
    await _until(lambda: all(w.journal.status(f"run-{i}") == "completed" for i in range(20)))
    await w.claimer.stop()
    assert w.model.peak == 20


async def test_opik_each_claimed_attempt_runs_inside_one_trace_named_for_its_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup audit, Opik item: every span of an attempt nests under one trace whose thread is the conversation and
    whose metadata names the run, so erasing the conversation finds them. Off when Opik is not configured."""
    import contextlib

    from app.harness.worker import claimer as claimer_module

    opened: list[dict[str, Any]] = []
    inside: list[bool] = []

    @contextlib.contextmanager
    def trace(**kwargs: object):  # noqa: ANN202
        opened.append(kwargs)
        inside.append(True)
        try:
            yield None
        finally:
            inside.pop()

    monkeypatch.setattr(claimer_module, "maybe_start_conversation_trace", trace)
    monkeypatch.setattr(claimer_module, "opik_tracing", lambda: (True, "proj"))
    w = _world(1, slots=1)
    seen_inside: list[bool] = []
    original = w.model.gate.wait

    async def wait() -> bool:
        seen_inside.append(bool(inside))
        return await original()

    monkeypatch.setattr(w.model.gate, "wait", wait)
    w.model.gate.set()
    w.claimer.start()
    await _until(lambda: w.journal.status("run-0") == "completed")
    await w.claimer.stop()
    assert opened == [{"enabled": True, "org_id": "org-1", "conversation_id": "ses-0", "run_id": "run-0", "project_name": "proj"}]
    assert seen_inside and all(seen_inside), "the model was called inside the attempt's trace"


async def test_o4_a_one_partition_topic_never_serializes_runs() -> None:
    """Hints arrive one after another, as from a single partition; each is
    acknowledged at once, so the 20 runs are all in flight together."""
    w = _world(20, slots=20)
    hints = DispatchHints(w.claimer)
    for index in range(20):
        assert await hints.handle(_hint(f"run-{index}")) is True
    await _until(lambda: w.model.waiting == 20)
    assert len(w.claimer.running) == 20
    w.model.gate.set()
    await _until(lambda: all(w.journal.status(f"run-{i}") == "completed" for i in range(20)))
    await w.claimer.stop()


async def test_o5_with_the_broker_down_runs_are_claimed_by_polling() -> None:
    w = _world(0, slots=4, gate_open=True)
    w.claimer.start()
    await settle(lambda: w.clock.pending_timers() > 0)
    for index in range(3):
        w.journal.admit(f"late-{index}", org_id="org-1", session_id=f"late-{index}", spec=minimal_run_spec(), input_text="q")
    await _turns(50)
    assert all(w.journal.status(f"late-{i}") == "queued" for i in range(3)), "nothing hinted: only the poll claims"
    await w.poll_again()
    await _until(lambda: all(w.journal.status(f"late-{i}") == "completed" for i in range(3)))
    await w.claimer.stop()


async def test_o4a_a_full_worker_acks_hints_without_claiming_and_a_freed_slot_claims_next() -> None:
    w = _world(2, slots=1)
    hints = DispatchHints(w.claimer)
    assert await hints.handle(_hint("run-0")) is True
    await _until(lambda: w.model.waiting == 1)
    assert await hints.handle(_hint("run-1")) is True
    assert w.journal.status("run-1") == "queued"
    w.claimer.start()
    w.model.gate.set()
    await _until(lambda: w.journal.status("run-1") == "completed")
    await w.claimer.stop()


class _CountedClaims:
    """Counts the claims sent to Node, by run id (``None``: a poll); a poll waits for ``gate``."""

    def __init__(self, journal: InMemoryJournal) -> None:
        self.journal = journal
        self.sent: list[str | None] = []
        self.polling = asyncio.Event()
        self.gate = asyncio.Event()

    async def claim(self, run_id: str | None = None, **kwargs: Any) -> Any:  # noqa: ANN401
        self.sent.append(run_id)
        if run_id is None:
            self.polling.set()
            _ = await self.gate.wait()
        return await self.journal.claim(run_id, **kwargs)

    async def release(self, token: Any, *, reason: str) -> None:  # noqa: ANN401
        await self.journal.release(token, reason=reason)

    def forget(self, token: Any) -> None:  # noqa: ANN401
        self.journal.forget(token)


async def test_perfd_15_a_hint_for_the_run_the_poll_is_claiming_sends_no_second_claim() -> None:
    """PERFC-F4: Node's notice wakes the poll, then the broker delivers the hint for the same run."""
    counted: list[_CountedClaims] = []
    w = _world(1, slots=4, claims=lambda j: counted.append(_CountedClaims(j)) or counted[0])
    claims = counted[0]
    w.claimer.start()
    _ = await claims.polling.wait()
    hinted = asyncio.create_task(DispatchHints(w.claimer).handle(_hint("run-0")))
    await _turns(20)
    assert claims.sent == [None], "the hint waits for the poll's answer"
    claims.gate.set()
    assert await hinted is True
    await _until(lambda: w.model.waiting == 1)
    assert "run-0" not in claims.sent, "the poll claimed it: no claim by id"
    w.model.gate.set()
    await _until(lambda: w.journal.status("run-0") == "completed")
    await w.claimer.stop()


async def test_perfd_15_a_hint_for_another_run_than_the_poll_claimed_is_claimed_by_id() -> None:
    counted: list[_CountedClaims] = []
    w = _world(2, slots=4, claims=lambda j: counted.append(_CountedClaims(j)) or counted[0])
    claims = counted[0]
    w.claimer.start()
    _ = await claims.polling.wait()
    hinted = asyncio.create_task(DispatchHints(w.claimer).handle(_hint("run-1")))
    await _turns(20)
    claims.gate.set()
    assert await hinted is True
    await _until(lambda: w.model.waiting == 2)
    assert claims.sent.count("run-1") <= 1
    w.model.gate.set()
    await _until(lambda: all(w.journal.status(f"run-{i}") == "completed" for i in range(2)))
    await w.claimer.stop()


async def test_perfd_15_a_hint_for_a_run_this_worker_runs_does_not_poll() -> None:
    """The run is here: there is nothing in the queue the hint could be about."""
    counted: list[_CountedClaims] = []
    w = _world(1, slots=4, claims=lambda j: counted.append(_CountedClaims(j)) or counted[0])
    claims = counted[0]
    claims.gate.set()
    hints = DispatchHints(w.claimer)
    assert await hints.handle(_hint("run-0")) is True
    await _until(lambda: w.model.waiting == 1)
    w.claimer.start()
    await settle(lambda: w.clock.pending_timers() > 0)
    polls = claims.sent.count(None)
    assert await hints.handle(_hint("run-0")) is True
    await _turns(50)
    assert claims.sent.count(None) == polls, "no poll for a run already running here"
    assert claims.sent.count("run-0") == 1
    w.model.gate.set()
    await _until(lambda: w.journal.status("run-0") == "completed")
    await w.claimer.stop()


async def test_invalid_foreign_and_cancel_hints_are_acknowledged_and_ignored() -> None:
    w = _world(1, slots=1)
    hints = DispatchHints(w.claimer)
    assert await hints.handle(StreamMessage(eventType="other", payload={})) is True
    assert await hints.handle(StreamMessage(eventType="agent_run_dispatch_requested", payload={"x": 1})) is True
    assert await hints.handle(_hint("run-0", kind="run_cancel")) is True
    assert w.journal.status("run-0") == "queued"


class _SlowClaims:
    """Node answers the claim only once ``gate`` opens (a claim in flight when SIGTERM arrives)."""

    def __init__(self, journal: InMemoryJournal) -> None:
        self.journal = journal
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def claim(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        self.entered.set()
        await self.gate.wait()
        return await self.journal.claim(*args, **kwargs)

    async def release(self, token: Any, *, reason: str) -> None:  # noqa: ANN401
        await self.journal.release(token, reason=reason)

    def forget(self, token: Any) -> None:  # noqa: ANN401
        self.journal.forget(token)


class _ClaimsFailingOnce:
    """Node's claim answer could not be read once (a torn body, a spec this build cannot parse)."""

    def __init__(self, journal: InMemoryJournal, error: Exception) -> None:
        self.journal = journal
        self.error: Exception | None = error
        self.failed = asyncio.Event()

    async def claim(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if self.error is not None:
            error, self.error = self.error, None
            self.failed.set()
            raise error
        return await self.journal.claim(*args, **kwargs)

    async def release(self, token: Any, *, reason: str) -> None:  # noqa: ANN401
        await self.journal.release(token, reason=reason)

    def forget(self, token: Any) -> None:  # noqa: ANN401
        self.journal.forget(token)


async def _finish_running(w: _World) -> None:
    """The failed poll is followed by the next one, whose run reaches its model call; the drain lets it finish."""
    await w.poll_again()
    await w.model.entered.wait()
    draining = w.claimer.begin_drain()
    w.model.gate.set()
    await draining


@pytest.mark.parametrize("error", [ValueError("not JSON"), KeyError("pinned")], ids=["value", "key"])
async def test_fx1_3_an_unexpected_claim_error_frees_the_slot_and_polling_goes_on(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    faulty: list[_ClaimsFailingOnce] = []
    w = _world(1, slots=2, claims=lambda j: faulty.append(_ClaimsFailingOnce(j, error)) or faulty[0])
    w.claimer.start()
    await _finish_running(w)
    assert faulty[0].failed.is_set()
    assert w.journal.status("run-0") == "completed"
    assert w.claimer.free == 2
    assert [e["errorType"] for e in _events(caplog, "slots.claim_failed")] == [type(error).__name__]


class _ClockFailingOnce(ManualClock):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    def suspend_aware(self) -> float:
        if self.failures:
            self.failures -= 1
            raise OSError("clock source unavailable")
        return super().suspend_aware()


async def test_fx1_5_a_poll_that_fails_outside_the_claim_does_not_end_the_poller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    w = _world(1, slots=1, clock=_ClockFailingOnce())
    w.claimer.start()
    await _finish_running(w)
    assert w.journal.status("run-0") == "completed"
    assert [e["errorType"] for e in _events(caplog, "slots.poll_failed")] == ["OSError"]


async def test_fx1_4_a_hint_whose_claim_fails_unexpectedly_is_acknowledged_and_frees_its_slot() -> None:
    w = _world(1, slots=1, claims=lambda j: _ClaimsFailingOnce(j, ValueError("x")))
    assert await DispatchHints(w.claimer).handle(_hint("run-0")) is True
    assert w.claimer.free == 1
    assert w.journal.claims("run-0") == 0
    w.claimer.start()
    await w.model.entered.wait()
    draining = w.claimer.begin_drain()
    w.model.gate.set()
    await draining
    assert w.journal.status("run-0") == "completed"


async def test_ru1a_a_draining_claimer_claims_no_new_run() -> None:
    w = _world(0, slots=2, gate_open=True)
    w.claimer.start()
    await w.claimer.stop()
    w.journal.admit("late", org_id="org-1", session_id="late", spec=minimal_run_spec(), input_text="q")
    assert await DispatchHints(w.claimer).handle(_hint("late")) is True
    # The drain ended the poller, so no timer can bring a later poll either.
    assert w.clock.pending_timers() == 0
    _ = w.clock.advance(60.0)
    await _turns(50)
    assert w.journal.status("late") == "queued"
    assert w.journal.claims("late") == 0


async def test_ru1a_a_claim_in_flight_at_sigterm_is_released_before_assembly(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    slow: list[_SlowClaims] = []
    w = _world(1, slots=1, gate_open=True, claims=lambda j: slow.append(_SlowClaims(j)) or slow[0])
    w.claimer.start()
    await slow[0].entered.wait()
    draining = w.claimer.begin_drain()
    await _draining(draining, caplog)
    slow[0].gate.set()
    await draining
    assert w.journal.claims("run-0") == 1
    assert w.journal.status("run-0") == "queued"
    assert _assembled(w.claimer) == []
    assert [e["reason"] for e in _events(caplog, "run.release")] == ["drain"]
    (drain,) = _events(caplog, "worker.drain")
    assert (drain["running"], drain["released"], drain["timedOut"]) == (1, 1, 0)


async def test_ru1a_a_hinted_claim_in_flight_at_sigterm_is_released_before_assembly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    slow: list[_SlowClaims] = []
    w = _world(1, slots=1, gate_open=True, claims=lambda j: slow.append(_SlowClaims(j)) or slow[0])
    hinted = asyncio.ensure_future(DispatchHints(w.claimer).handle(_hint("run-0")))
    await slow[0].entered.wait()
    draining = w.claimer.begin_drain()
    await _draining(draining, caplog)
    slow[0].gate.set()
    await draining
    assert await hinted is True
    assert (w.journal.claims("run-0"), w.journal.status("run-0")) == (1, "queued")
    assert _assembled(w.claimer) == []
    (drain,) = _events(caplog, "worker.drain")
    assert (drain["running"], drain["released"], drain["timedOut"]) == (1, 1, 0)


async def test_ru1b_the_drain_is_bounded_and_hands_back_what_it_cancels(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    w = _world(1, slots=1, drain_s=0.1, release_s=1.0)
    w.claimer.start()
    await _until(lambda: w.model.waiting == 1)
    draining = w.claimer.begin_drain()
    await settle(lambda: bool(_events(caplog, "slots.drain")))
    await _turns(50)
    assert w.clock.pending_timers() == 1, "the poller has ended; only the drain's deadline is left"
    _ = w.clock.advance(0.099)
    await _turns(50)
    assert not draining.done(), "the drain waits for the running attempt until its bound"
    _ = w.clock.advance(0.001)
    await draining
    # Released at once, not left running until its lease expires.
    assert w.journal.status("run-0") == "queued"
    assert w.model.waiting == 1, "the model call never answered: the bound, not the run, ended the drain"
    assert [e["reason"] for e in _events(caplog, "run.release")] == ["drain_timeout"]
    (drain,) = _events(caplog, "worker.drain")
    assert (drain["running"], drain["released"], drain["yielded"], drain["timedOut"]) == (1, 1, 0, 1)
    assert drain["ms"] == 100


async def test_ru1_stop_drains_running_attempts_within_the_bound(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    w = _world(1, slots=1, drain_s=5.0)
    w.claimer.start()
    await _until(lambda: w.model.waiting == 1)
    draining = w.claimer.begin_drain()
    await _draining(draining, caplog)
    w.model.gate.set()
    await draining
    # The boundary in progress (the model call) finished and committed: this one ended the run.
    assert w.journal.status("run-0") == "completed"
    (drain,) = _events(caplog, "worker.drain")
    assert (drain["running"], drain["ended"], drain["timedOut"], drain["ms"]) == (1, 1, 0, 0)


async def test_ru1c_sigterm_starts_the_inline_drain_before_uvicorn_handles_it() -> None:
    order: list[str] = []

    class _Inline:
        def begin_drain(self) -> None:
            order.append("drain")

    class _State:
        harness_inline = _Inline()

    original = signal.getsignal(signal.SIGTERM)
    _ = signal.signal(signal.SIGTERM, lambda *_: order.append("uvicorn"))
    try:
        drain_on_termination(_State(), signals=(signal.SIGTERM,))
        signal.raise_signal(signal.SIGTERM)
        await settle(lambda: len(order) == 2)
    finally:
        _ = signal.signal(signal.SIGTERM, original)
    assert order == ["uvicorn", "drain"]


@pytest.mark.parametrize(
    ("memory_mib", "cpus", "slots"),
    [
        (BASE_MIB + PER_RUN_MIB * 20, 8, 20),
        (BASE_MIB + PER_RUN_MIB * 2, 8, 2),
        (256, 8, 1),
        (64 * 1024, 64, MAX_SLOTS),
        (64 * 1024, 1, 16),
    ],
)
def test_o4a_slots_are_a_constant_formula_of_cgroup_memory_and_cpu(memory_mib: int, cpus: float, slots: int) -> None:
    assert run_slots(memory_mib * MIB, cpus) == slots


def test_o4a_slots_read_the_cgroup_when_not_given() -> None:
    assert 1 <= run_slots() <= MAX_SLOTS


def test_a_claimer_needs_a_slot() -> None:
    with pytest.raises(ValueError, match="slot"):
        _ = RunClaimer(journal=Any, driver=Any, worker_id="w", slots=0, clock=ManualClock())  # type: ignore[arg-type]
