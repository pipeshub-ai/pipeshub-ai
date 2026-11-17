"""The live and yield records against damaged, forged or orphaned files.

Every process running as the app's user can write these records, and their
checksum is not a key. One bad write must not pin the live limit or refuse
every run for long: times beyond what any step sets are bounded, burst
charges count for at most one run per slot, and a record file that was
deleted or replaced is opened again by the workers still on the old one.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.host_reading import LiveMode
from app.agent_loop_lib.sandbox.live_limit import (
    HARD_COOLDOWN_S,
    LiveLimit,
    host_profile,
    initial_record,
    step,
)
from app.agent_loop_lib.sandbox.live_record import LiveLedger, LiveRecord
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.services.resource_governor import yield_signal
from app.services.resource_governor.yield_signal import (
    YieldRecord,
    YieldSignal,
    read_yield_state,
    state_of,
)

from ..live_fakes import FakeClock, FakeReader, capacity, sample

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

GiB = 2**30
M = 512 * 2**20
PROFILE = host_profile(LiveSettings())


def _live(tmp_path: Path, clock: FakeClock, reader: FakeReader) -> LiveLimit:
    mode = LiveMode(mode="on:host", reader=reader, profile="host", note="test")
    live = LiveLimit(
        capacity(), None, settings=LiveSettings(), resolver=lambda: mode,  # type: ignore[arg-type]
        directory=tmp_path / "state", clock=clock,
    )
    live.apply_mode(mode)
    return live


def _forge(live: LiveLimit, record: LiveRecord) -> None:
    with live._ledger.transaction() as txn:
        assert txn is not None
        txn.write(record)


class TestCooldown:
    def test_a_cooldown_far_ahead_holds_the_limit_no_longer_than_a_hard_cooldown(self) -> None:
        now = 1000.0
        rec = initial_record(
            None, sample(2.5), profile=PROFILE, m=M, ceiling=6, floor=2, held=0, now=now, pid=1,
        )
        rec = replace(rec, limit=2, cooldown_until=now + 10**9)
        rules = []
        for _ in range(8):
            now += 15.0
            rec = replace(rec, last_wait_limit_at=now - 1.0)  # runs wait at the limit
            rec = step(rec, sample(15.0), profile=PROFILE, m=M, ceiling=6, floor=2, held=2, now=now)
            rules.append(rec.rule)
            assert rec.cooldown_until <= now + HARD_COOLDOWN_S
        assert "grow" in rules[: int(HARD_COOLDOWN_S // 15) + 1], rules
        assert rec.limit > 2

    def test_an_infinite_cooldown_is_a_damaged_record(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(15.0)))
        assert live.sample_once() is not None
        good = live._ledger.read()
        assert good is not None
        assert LiveRecord.decode(replace(good, cooldown_until=float("inf")).encode()) is None
        _forge(live, replace(good, limit=2, rule="mem_hard", cooldown_until=float("inf"), owner_pid=1))
        clock.now += 15.0
        new = live.sample_once()
        # Read as damaged, so the sampler starts afresh at the memory target.
        assert new is not None and new.limit == 6 and new.cooldown_until < clock.now + HARD_COOLDOWN_S


class TestFutureTimes:
    def test_a_step_dated_in_the_future_does_not_stop_the_samplers(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(15.0)))
        assert live.sample_once() is not None
        good = live._ledger.read()
        assert good is not None
        _forge(live, replace(good, limit=2, stamp=clock.now, last_step_at=clock.now + 10**6, owner_pid=1))
        clock.now += 15.0
        assert live.sample_once() is not None

    def test_a_wait_stamped_in_the_future_is_not_demand(self) -> None:
        now = 1000.0
        rec = initial_record(
            None, sample(2.5), profile=PROFILE, m=M, ceiling=6, floor=2, held=0, now=now, pid=1,
        )
        rec = replace(rec, limit=2, cooldown_until=0.0, last_wait_limit_at=now + 10**6)
        rec = step(rec, sample(15.0), profile=PROFILE, m=M, ceiling=6, floor=2, held=2, now=now + 15.0)
        assert rec.rule == "no_demand" and rec.limit == 2


class TestCharges:
    async def test_a_huge_charge_cannot_refuse_every_run(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(15.0)))
        _forge(live, LiveRecord(charges=((clock.now + 5.0, 2**50),)))  # 1 PiB
        assert await live.admit_and_charge(held=0) is None

    async def test_real_bursts_still_hold_runs_back(self, tmp_path: Path) -> None:
        # 3.5 GiB free: a 1 GiB floor plus six charged runs leaves no room.
        clock = FakeClock()
        live = _live(tmp_path, clock, FakeReader(sample(3.5)))
        outcomes = [await live.admit_and_charge(held=0) for _ in range(8)]
        assert outcomes.count(None) < 8


class TestReplacedRecord:
    def test_a_worker_on_a_deleted_record_reopens_it(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        old = LiveLedger(state)
        os.unlink(state / "live")
        os.unlink(state / "live.lock")
        new = LiveLedger(state)
        with new.transaction() as txn:
            assert txn is not None
            txn.write(LiveRecord(limit=3, ceiling=6, min=2, stamp=1.0))
        assert old.read() != new.read()
        assert old.reopen_if_replaced() is True
        assert old.read() == new.read()
        assert old.reopen_if_replaced() is False
        old.close()
        new.close()

    def test_a_replaced_record_is_reopened(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        old = LiveLedger(state)
        replacement = state / "live.new"
        replacement.write_bytes(LiveRecord(limit=4, ceiling=6, min=2, stamp=1.0).encode())
        replacement.chmod(0o600)
        os.replace(replacement, state / "live")
        assert old.reopen_if_replaced() is True
        assert old.read() is not None and old.read().limit == 4  # type: ignore[union-attr]
        old.close()

    def test_each_sample_checks_the_record(self, tmp_path: Path) -> None:
        clock = FakeClock()
        a = _live(tmp_path, clock, FakeReader(sample(15.0)))
        assert a.sample_once() is not None
        os.unlink(tmp_path / "state" / "live")
        b = _live(tmp_path, clock, FakeReader(sample(2.5)))
        clock.now += 15.0
        assert b.sample_once() is not None
        assert a.sample_once() is None  # b stepped this interval, in the file a now reads
        assert a._ledger.read() == b._ledger.read()
        assert a.view(fresh=True).limit == b.view(fresh=True).limit


class TestYield:
    def test_an_episode_that_starts_after_its_refresh_is_not_real(self) -> None:
        record = YieldRecord(set_at=1000.0, expires_at=1020.0, since=5000.0, writer_pid=os.getpid())
        assert not state_of(record, 1001.0).active

    def test_non_finite_times_are_a_damaged_record(self) -> None:
        for field in ("set_at", "expires_at", "since"):
            record = replace(
                YieldRecord(set_at=1000.0, expires_at=1020.0, since=1000.0, writer_pid=1),
                **{field: float("-inf")},
            )
            assert YieldRecord.decode(record.encode()) is None

    def test_a_writer_on_a_deleted_record_reopens_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(yield_signal, "REOPEN_CHECK_S", 0.0, raising=False)
        state = tmp_path / "state"
        signal = YieldSignal(state)
        os.unlink(state / "yield")
        os.unlink(state / "yield.lock")
        assert signal.refresh(reason="host_memory_floor") is not None
        assert read_yield_state(state).active
        signal.close()

    def test_a_fifo_at_the_name_does_not_block_a_reader(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        os.mkfifo(state / "yield", 0o600)
        assert not read_yield_state(state).active
