from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox import capacity as capacity_module
from app.agent_loop_lib.sandbox import governor as governor_module
from app.agent_loop_lib.sandbox.capacity import HoldReason, SandboxCapacity
from app.agent_loop_lib.sandbox.load_ledger import (
    FLAG_BACKED_OFF,
    FLAG_CPU,
    FLAG_HELD,
    FLAG_MEMORY,
    FLAG_QUALIFYING,
    FLAG_SLOTS,
    KEEP_S,
    LoadLedger,
)
from app.services.resource_governor.yield_signal import YieldState, indexing_backed_off

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

T0 = 1_763_291_000.0  # a minute boundary minus 20 s, so waits can cross it


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make(tmp_path: Path, *, mode: str = "compose", backed_off: bool = False, clock: Clock | None = None) -> tuple[LoadLedger, Clock]:
    clock = clock or Clock()
    ledger = LoadLedger(tmp_path / "state", mode=mode, clock=clock, backed_off=lambda: backed_off)
    return ledger, clock


def minutes(ledger: LoadLedger) -> dict:
    return ledger.to_json()["minutes"]


def total(ledger: LoadLedger, key: str) -> int:
    return sum(b[key] for b in minutes(ledger).values())


def ticks(ledger: LoadLedger) -> dict[int, int]:
    return {int(k): v for k, v in ledger.to_json()["ticks"].items()}


class TestCounting:
    def test_granted_at_once_counts_a_run_and_nothing_else(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path)
        ledger.outcome("granted", 0.0, None)
        assert total(ledger, "runs") == 1
        assert total(ledger, "waited") == 0
        assert ticks(ledger) == {}

    def test_a_wait_for_full_slots_marks_every_tick_it_spans(self, tmp_path: Path) -> None:
        ledger, clock = make(tmp_path)
        ledger.wait_start(HoldReason.SLOTS_FULL)
        clock.now += 25
        ledger.outcome("granted", 25.0, HoldReason.SLOTS_FULL)
        assert total(ledger, "runs") == 1
        assert total(ledger, "waited") == 1
        flags = ticks(ledger)
        assert set(flags) == set(range(int(T0 // 10), int((T0 + 25) // 10) + 1))
        assert all(f == FLAG_QUALIFYING | FLAG_HELD | FLAG_SLOTS for f in flags.values())
        assert max(b["maxWaitS"] for b in minutes(ledger).values()) == pytest.approx(25.0)
        assert sum(b["waitedByReason"]["slots"] for b in minutes(ledger).values()) == 1

    @pytest.mark.parametrize("reason", [HoldReason.LIVE_MEMORY, HoldReason.HOST_MEMORY_FLOOR])
    def test_a_memory_refusal_after_indexing_backed_off_qualifies(self, tmp_path: Path, reason: HoldReason) -> None:
        ledger, _ = make(tmp_path, backed_off=True)
        ledger.outcome("refused", 5.0, reason)
        assert (total(ledger, "waited"), total(ledger, "refused")) == (1, 1)
        assert all(f & FLAG_BACKED_OFF and f & FLAG_MEMORY for f in ticks(ledger).values())

    def test_a_memory_refusal_before_indexing_backed_off_does_not(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path, backed_off=False)
        ledger.outcome("refused", 5.0, HoldReason.LIVE_MEMORY)
        assert total(ledger, "refused") == 0
        assert total(ledger, "refusedUnqualified") == 1
        assert set(ticks(ledger).values()) == {FLAG_HELD}

    def test_memory_waits_count_at_once_on_a_sidecar(self, tmp_path: Path) -> None:
        asked: list[bool] = []
        ledger = LoadLedger(tmp_path / "state", mode="helm_dind", clock=Clock(), backed_off=lambda: asked.append(True) or False)
        ledger.outcome("refused", 5.0, HoldReason.LIVE_MEMORY)
        assert total(ledger, "refused") == 1
        assert not asked
        assert not any(f & FLAG_BACKED_OFF for f in ticks(ledger).values())

    def test_cpu_waits_are_not_gated(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path, backed_off=False)
        ledger.outcome("refused", 5.0, HoldReason.LIVE_CPU)
        assert total(ledger, "refused") == 1
        assert all(f & FLAG_CPU for f in ticks(ledger).values())

    def test_org_share_waits_never_qualify(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path)
        ledger.outcome("granted", 3.0, HoldReason.ORG_SHARE)
        ledger.outcome("refused", 3.0, HoldReason.ORG_SHARE)
        assert total(ledger, "waited") == 0
        assert total(ledger, "waitedUnqualified") == 2
        assert total(ledger, "refusedUnqualified") == 1
        assert sum(b["waitedByReason"]["orgShare"] for b in minutes(ledger).values()) == 2

    def test_a_cancelled_wait_is_not_a_run(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path)
        ledger.outcome("cancelled", 4.0, HoldReason.SLOTS_FULL)
        assert (total(ledger, "runs"), total(ledger, "waited"), total(ledger, "refused")) == (0, 0, 0)
        assert total(ledger, "cancelledWaiting") == 1
        assert set(ticks(ledger).values()) == {FLAG_HELD}

    def test_a_wait_across_a_minute_counts_in_the_minute_it_ended(self, tmp_path: Path) -> None:
        ledger, clock = make(tmp_path)
        clock.now = 60 * 1000 + 50
        ledger.wait_start(HoldReason.SLOTS_FULL)
        clock.now += 20
        ledger.outcome("granted", 20.0, HoldReason.SLOTS_FULL)
        assert list(minutes(ledger)) == ["1001"]

    def test_the_last_reason_is_recorded(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path)
        ledger.wait_start(HoldReason.SLOTS_FULL)
        ledger.wait_reason(HoldReason.LIVE_CPU)
        ledger.outcome("granted", 1.0, HoldReason.LIVE_CPU)
        reasons = next(iter(minutes(ledger).values()))["waitedByReason"]
        assert reasons["cpu"] == 1 and reasons["slots"] == 0

    def test_entries_older_than_the_longest_window_are_pruned(self, tmp_path: Path) -> None:
        ledger, clock = make(tmp_path)
        ledger.outcome("refused", 1.0, HoldReason.SLOTS_FULL)
        clock.now += KEEP_S + 120
        ledger.outcome("granted", 0.0, None)
        data = ledger.to_json()
        assert list(data["minutes"]) == [str(int(clock.now // 60))]
        assert data["ticks"] == {}


class TestTheBackOffRule:
    """The ledger's default check is step 3's back-off rule, read at the outcome."""

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (YieldState(active=True, since=0.0, acked_at=90.0, index_at_floor=True, ackers=1), True),
            (YieldState(active=True, since=0.0, acked_at=60.0, index_at_floor=True, ackers=1), False),
            (YieldState(active=True, since=40.0, acked_at=90.0, index_at_floor=False, ackers=1), False),
            (YieldState(active=True, since=-30.0, acked_at=90.0, index_at_floor=False, ackers=1), True),
            (YieldState(), False),
        ],
        ids=["acked-at-floor", "ack-40s-old", "not-at-floor-60s", "not-at-floor-130s", "never-raised"],
    )
    def test_cases(self, state: YieldState, expected: bool) -> None:
        assert indexing_backed_off(state, 100.0) is expected

    def test_default_check_reads_the_yield_record(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.agent_loop_lib.sandbox import load_ledger

        seen: list[Path | None] = []

        def fake_read(directory: Path | None = None, *, now: float | None = None) -> YieldState:
            seen.append(directory)
            return YieldState()

        monkeypatch.setattr("app.services.resource_governor.yield_signal.read_yield_state", fake_read)
        ledger = LoadLedger(tmp_path, mode="compose", clock=Clock())
        ledger.outcome("refused", 1.0, HoldReason.LIVE_MEMORY)
        assert seen == [tmp_path]
        assert load_ledger is not None
        assert total(ledger, "refusedUnqualified") == 1


class TestFiles:
    def test_flush_writes_a_private_file_named_for_the_process(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path)
        ledger.outcome("granted", 0.0, None)
        assert ledger.path is not None and ledger.path.exists()
        assert ledger.path.name.startswith(f"w-{os.getpid()}-")
        assert ledger.path.stat().st_mode & 0o777 == 0o600
        assert json.loads(ledger.path.read_text())["v"] == 1

    def test_readers_never_see_a_torn_file(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path)
        ledger.flush()
        assert ledger.path is not None
        bad: list[str] = []
        stop = threading.Event()

        def read() -> None:
            while not stop.is_set():
                try:
                    json.loads(ledger.path.read_bytes())
                except FileNotFoundError:
                    pass
                except ValueError as exc:
                    bad.append(str(exc))

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for _ in range(1000):
                ledger.outcome("granted", 0.0, None)
                ledger.flush()
        finally:
            stop.set()
            reader.join()
        assert bad == []

    async def test_flushes_are_rate_limited_and_the_last_event_is_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ledger = LoadLedger(tmp_path / "state", mode="compose", clock=Clock(), backed_off=lambda: False, flush_every_s=0.3)
        writes: list[int] = []
        real = ledger.flush

        def counted() -> bool:
            writes.append(1)
            return real()

        monkeypatch.setattr(ledger, "flush", counted)
        for _ in range(100):
            ledger.outcome("granted", 0.0, None)
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.6)
        assert 1 <= len(writes) <= 2
        assert ledger.path is not None
        assert sum(b["runs"] for b in json.loads(ledger.path.read_text())["minutes"].values()) == 100

    def test_an_unusable_directory_disables_the_ledger(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        (state / "load").mkdir(mode=0o770)
        os.chmod(state / "load", 0o770)
        ledger, _ = make(tmp_path)
        with caplog.at_level("WARNING"):
            ledger.outcome("granted", 0.0, None)
            ledger.outcome("granted", 0.0, None)
        assert not ledger.enabled
        assert sum("sandbox load ledger: disabled" in r.message for r in caplog.records) == 1

    def test_a_short_write_keeps_the_last_whole_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ledger, _ = make(tmp_path)
        ledger.outcome("granted", 0.0, None)
        assert ledger.path is not None
        before = ledger.path.read_bytes()
        real_write = os.write
        monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[: len(data) // 2]))
        ledger.outcome("refused", 3.0, HoldReason.SLOTS_FULL)
        assert ledger.path.read_bytes() == before
        assert ledger._dirty  # retried by the next flush
        monkeypatch.setattr(os, "write", real_write)
        assert ledger.flush()
        assert sum(b["refused"] for b in json.loads(ledger.path.read_text())["minutes"].values()) == 1

    def test_close_writes_what_is_left(self, tmp_path: Path) -> None:
        ledger, _ = make(tmp_path)
        ledger._dirty = True  # as if a deferred flush had not run yet
        ledger.close()
        assert ledger.path is not None and ledger.path.exists()


class TestGovernorWiring:
    @pytest.fixture(autouse=True)
    def _reset(self) -> Iterator[None]:
        governor_module.reset_default_governor()
        yield
        governor_module.reset_default_governor()

    @pytest.mark.parametrize(("mode", "observers"), [("e2b", 0), ("compose", 1), ("local", 1), ("helm_dind", 1)])
    def test_the_ledger_observes_every_mode_but_e2b(self, monkeypatch: pytest.MonkeyPatch, mode: str, observers: int) -> None:
        capacity = SandboxCapacity.fixed(4, None)
        monkeypatch.setattr(capacity, "mode", mode)
        monkeypatch.setattr(governor_module, "get_sandbox_capacity", lambda: capacity)
        monkeypatch.setattr(capacity_module, "_capacity", capacity)

        class _StubLive:
            def __init__(self, *_a: object, **_k: object) -> None:
                pass

        monkeypatch.setattr("app.agent_loop_lib.sandbox.live_limit.LiveLimit", _StubLive)
        monkeypatch.setattr(governor_module, "_attach_indexing_yield", lambda _live: None)
        gov = governor_module.get_default_governor()
        ledgers = [o for o in gov._observers if isinstance(o, LoadLedger)]
        assert len(ledgers) == observers


class TestThroughTheGovernor:
    async def test_a_run_that_waits_for_a_full_governor_is_one_wait_and_one_run(self, tmp_path: Path) -> None:
        from app.agent_loop_lib.sandbox.capacity import FixedLimit
        from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor
        from app.services.resource_governor.shared_slots import LocalTaggedSlots

        ledger, _ = make(tmp_path)
        capacity = SandboxCapacity.fixed(1, None)
        gov = SandboxResourceGovernor(
            capacity, LocalTaggedSlots(capacity.slot_files),
            limit_provider=FixedLimit(capacity), observers=[ledger], poll_s=0.01,
        )
        first = await gov.acquire()
        waiter = asyncio.create_task(gov.acquire(wait_s=5.0))
        await asyncio.sleep(0.05)
        await gov.release(first)
        second = await waiter
        await gov.release(second)
        assert total(ledger, "runs") == 2
        assert total(ledger, "waited") == 1
        assert total(ledger, "refused") == 0

    async def test_a_refusal_and_a_cancel_are_counted_apart(self, tmp_path: Path) -> None:
        from app.agent_loop_lib.sandbox.capacity import FixedLimit
        from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor
        from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded
        from app.services.resource_governor.shared_slots import LocalTaggedSlots

        ledger, _ = make(tmp_path)
        capacity = SandboxCapacity.fixed(1, None)
        gov = SandboxResourceGovernor(
            capacity, LocalTaggedSlots(capacity.slot_files),
            limit_provider=FixedLimit(capacity), observers=[ledger], poll_s=0.01,
        )
        held = await gov.acquire()
        with pytest.raises(SandboxLimitExceeded):
            await gov.acquire(wait_s=0.05)
        waiter = asyncio.create_task(gov.acquire(wait_s=5.0))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await gov.release(held)
        assert (total(ledger, "runs"), total(ledger, "refused"), total(ledger, "cancelledWaiting")) == (2, 1, 1)
