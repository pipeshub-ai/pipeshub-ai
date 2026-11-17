"""Governor error and fallback paths the main suites do not reach."""

from __future__ import annotations

import asyncio
import logging
import random
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox import governor as governor_mod
from app.agent_loop_lib.sandbox.capacity import FixedLimit, HoldReason, SandboxCapacity
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxLimitExceeded,
    SandboxResourceGovernor,
    _LoopQueue,
    _open_slots,
    _Waiter,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.services.resource_governor.shared_slots import LocalTaggedSlots, SharedSlots

if TYPE_CHECKING:
    from collections import Counter
    from pathlib import Path


class _Recorder:
    def __init__(self) -> None:
        self.outcomes: list[tuple[str, HoldReason | None]] = []

    def wait_start(self, reason: HoldReason) -> None:
        pass

    def wait_reason(self, reason: HoldReason) -> None:
        pass

    def outcome(self, outcome: str, waited_s: float, reason: HoldReason | None) -> None:
        self.outcomes.append((outcome, reason))


class _EndFails(FixedLimit):
    def __init__(self, capacity: SandboxCapacity) -> None:
        super().__init__(capacity)
        self.ended = 0

    def wait_ended(self, *, granted: bool) -> None:
        self.ended += 1
        raise RuntimeError("wait_ended broke")


class _FlakyCount(LocalTaggedSlots):
    """Counting for the poller fails; taking a slot still works."""

    def __init__(self, capacity: int, error: type[BaseException] = RuntimeError) -> None:
        super().__init__(capacity)
        self.error = error
        self.looks = 0

    def held_by_tag(self, budget: float = 0.0) -> Counter[bytes]:
        self.looks += 1
        raise self.error("count failed")


def _governor(limit: int, slots: object | None = None, *, provider: type = FixedLimit,
              observers: tuple = (), clock=None) -> SandboxResourceGovernor:
    capacity = SandboxCapacity.fixed(limit)
    kwargs = {"clock": clock} if clock is not None else {}
    return SandboxResourceGovernor(
        capacity, slots or LocalTaggedSlots(capacity.slot_files), limit_provider=provider(capacity),
        observers=observers, poll_s=0.01, rng=random.Random(0), **kwargs,
    )


class TestWaitEndedFailure:
    async def test_a_failing_wait_ended_does_not_replace_the_refusal(self, caplog) -> None:
        recorder = _Recorder()
        gov = _governor(1, provider=_EndFails, observers=(recorder,))
        lease = await gov.acquire()
        with caplog.at_level(logging.DEBUG, logger=governor_mod.__name__), pytest.raises(SandboxLimitExceeded) as err:
            await gov.acquire(wait_s=0)
        assert "busy" in str(err.value)
        assert gov.limit_provider.ended == 1  # type: ignore[attr-defined]
        assert recorder.outcomes[-1] == ("refused", HoldReason.SLOTS_FULL)
        assert any("wait_ended failed" in r.message for r in caplog.records)
        await lease.release()

    async def test_a_failing_wait_ended_does_not_cost_the_granted_slot(self) -> None:
        gov = _governor(1, provider=_EndFails)
        first = await gov.acquire()
        waiter = asyncio.ensure_future(gov.acquire(wait_s=5))
        await asyncio.sleep(0.05)
        await first.release()
        lease = await asyncio.wait_for(waiter, 2)
        assert gov.held_total() == 1
        await lease.release()
        assert gov.held_total() == 0


class TestPollerFailure:
    async def test_a_failing_count_warns_once_and_a_local_release_still_serves(self, caplog) -> None:
        slots = _FlakyCount(16)
        gov = _governor(1, slots)
        lease = await gov.acquire()
        # Keep the waiting log (which counts too) out of the way: only the poller counts.
        gov._waiting_logged_at = 1e18
        with caplog.at_level(logging.WARNING, logger=governor_mod.__name__):
            waiter = asyncio.ensure_future(gov.acquire(wait_s=5))
            for _ in range(200):
                if slots.looks >= 3:
                    break
                await asyncio.sleep(0.01)
            assert slots.looks >= 3  # the poller kept polling after the failure
            await lease.release()
            second = await asyncio.wait_for(waiter, 2)
        warned = [r for r in caplog.records if "waiters' poll failed" in r.message]
        assert len(warned) == 1
        await second.release()
        for _ in range(100):
            if not gov._queues:
                break
            await asyncio.sleep(0.01)
        assert gov._queues == {}  # the queue goes once its last waiter left


class TestOnLoops:
    def test_a_closed_loop_is_skipped(self) -> None:
        gov = _governor(2)
        loop = asyncio.new_event_loop()
        loop.close()
        gov._queues[loop] = _LoopQueue(loop)
        gov.capacity_changed()  # does not raise

    def test_a_loop_closing_in_between_is_ignored(self) -> None:
        gov = _governor(2)
        calls: list[str] = []

        def closing(*args: object) -> None:
            calls.append("called")
            raise RuntimeError("Event loop is closed")

        class _Loop:
            def is_closed(self) -> bool:
                return False

            call_soon_threadsafe = staticmethod(closing)

        loop = _Loop()
        gov._queues[loop] = SimpleNamespace(loop=loop, kick_poller=lambda: None, wake_one=lambda tag: None)  # type: ignore[index]
        gov.capacity_changed()
        gov._wake_one(b"")
        assert calls == ["called", "called"]


class TestBusyMutexWarning:
    def test_warns_after_five_seconds_then_at_most_once_a_minute(self, caplog) -> None:
        now = [100.0]
        gov = _governor(2, _FlakyCount(16, TimeoutError), clock=lambda: now[0])
        queue = _LoopQueue(asyncio.new_event_loop())
        try:
            with caplog.at_level(logging.WARNING, logger=governor_mod.__name__):
                for t in (100.0, 104.0, 105.0, 130.0, 164.0, 165.5):
                    now[0] = t
                    assert gov._poll_once(queue) is False
            warned = [r for r in caplog.records if "slot mutex has been busy" in r.getMessage()]
            assert [r.getMessage().split("for ")[1].split("s;")[0] for r in warned] == ["5", "66"]
        finally:
            queue.loop.close()

    def test_a_successful_count_resets_the_busy_clock(self) -> None:
        now = [0.0]
        gov = _governor(2, clock=lambda: now[0])
        gov._busy_since = 1.0
        queue = _LoopQueue(asyncio.new_event_loop())
        try:
            assert gov._poll_once(queue) is True
            assert gov._busy_since is None
        finally:
            queue.loop.close()

    def test_a_waiter_already_trying_is_not_woken_by_the_poll(self) -> None:
        gov = _governor(2)
        loop = asyncio.new_event_loop()
        try:
            queue = _LoopQueue(loop)
            trying = _Waiter(tag=b"", start=0.0)  # no future: it is trying for a slot itself
            done_future = loop.create_future()
            done_future.set_result(None)
            woken = _Waiter(tag=b"", start=0.0, future=done_future)
            queue.waiters[trying] = None
            queue.waiters[woken] = None
            assert gov._poll_once(queue) is True
        finally:
            loop.close()


class TestOpenSlots:
    def test_an_unusable_directory_falls_back_to_per_worker_slots(self, tmp_path: Path, caplog) -> None:
        blocker = tmp_path / "state"
        blocker.write_text("not a directory")
        with caplog.at_level(logging.WARNING, logger=governor_mod.__name__):
            slots = _open_slots(blocker, 8)
        assert isinstance(slots, LocalTaggedSlots)
        assert any("counted per worker" in r.getMessage() and "no POSIX" not in r.getMessage()
                   for r in caplog.records)

    def test_no_directory_says_why(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger=governor_mod.__name__):
            slots = _open_slots(None, 8)
        assert isinstance(slots, LocalTaggedSlots)
        assert any("no POSIX file locks" in r.getMessage() for r in caplog.records)

    def test_a_usable_directory_shares(self, tmp_path: Path) -> None:
        slots = _open_slots(tmp_path / "state", 8)
        try:
            assert isinstance(slots, SharedSlots)
        finally:
            slots.close()


class TestDefaultGovernor:
    def test_later_limits_are_ignored(self, monkeypatch, caplog) -> None:
        built = _governor(3)
        monkeypatch.setattr(governor_mod, "_default_governor", built)
        with caplog.at_level(logging.DEBUG, logger=governor_mod.__name__):
            assert governor_mod.get_default_governor(GovernorLimits(max_total_sandboxes=50)) is built
            assert governor_mod.get_default_governor(GovernorLimits()) is built
        ignored = [r for r in caplog.records if "ignoring limits" in r.getMessage()]
        assert len(ignored) == 1
        assert built.capacity.ceiling() == 3


class TestAttachIndexingYield:
    def _live(self, **settings: object) -> SimpleNamespace:
        return SimpleNamespace(settings=LiveSettings(**settings), on_memory_wait=None, after_sample=None)

    def test_off_by_setting(self, caplog) -> None:
        live = self._live(yield_indexing=False)
        with caplog.at_level(logging.INFO, logger=governor_mod.__name__):
            governor_mod._attach_indexing_yield(live)  # type: ignore[arg-type]
        assert live.on_memory_wait is None and live.after_sample is None
        assert any("SANDBOX_YIELD_INDEXING=off" in r.getMessage() for r in caplog.records)

    def test_no_signal_leaves_the_live_limit_alone(self, monkeypatch) -> None:
        from app.services.resource_governor.yield_signal import YieldSignal

        monkeypatch.setattr(YieldSignal, "open_default", classmethod(lambda cls, *, role: None))
        live = self._live()
        governor_mod._attach_indexing_yield(live)  # type: ignore[arg-type]
        assert live.on_memory_wait is None and live.after_sample is None

    def test_a_signal_is_wired(self, monkeypatch, tmp_path: Path) -> None:
        from app.services.resource_governor.yield_signal import YieldSignal

        signal = YieldSignal(tmp_path / "state")
        monkeypatch.setattr(YieldSignal, "open_default", classmethod(lambda cls, *, role: signal))
        live = self._live()
        try:
            governor_mod._attach_indexing_yield(live)  # type: ignore[arg-type]
            assert live.on_memory_wait is not None and live.after_sample is not None
        finally:
            signal.close()
