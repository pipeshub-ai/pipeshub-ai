"""Tests for the writer of the indexing back-off signal (live_yield.py)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox import governor as governor_module
from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.host_reading import LiveMode
from app.agent_loop_lib.sandbox.live_limit import LiveLimit
from app.agent_loop_lib.sandbox.live_record import LiveRecord
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.agent_loop_lib.sandbox.live_yield import IndexingYield
from app.services.resource_governor.yield_signal import YieldSignal

from .live_fakes import FakeClock, FakeReader, capacity, sample

if TYPE_CHECKING:
    from pathlib import Path


def _mode(profile: str = "host") -> LiveMode:
    source = "sidecar" if profile == "sidecar" else profile
    return LiveMode(mode=f"on:{source}", reader=FakeReader(sample(1.4), source=source), profile=profile)  # type: ignore[arg-type]


class Rig:
    def __init__(
        self, tmp_path: Path, *, mode: LiveMode | None = None, settings: LiveSettings | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.settings = settings or LiveSettings()
        mode = mode or _mode()
        self.live = LiveLimit(
            capacity(), None, settings=self.settings, resolver=lambda: mode,
            directory=tmp_path / "state", clock=self.clock,
        )
        self.live.apply_mode(mode)
        self.signal = YieldSignal(tmp_path / "state", clock=self.clock)
        self.writer = IndexingYield(self.live, self.signal, self.settings, clock=self.clock)
        self.live.on_memory_wait = self.writer.on_memory_wait

    def set_record(self, *, limit: int, target: int) -> None:
        with self.live._ledger.transaction() as txn:
            assert txn is not None
            txn.write(LiveRecord(
                limit=limit, ceiling=6, min=2, target=target, rule="mem_target", shrink_cause="memory",
                stamp=self.clock.now, last_step_at=self.clock.now, sample_at=self.clock.now,
                mem_total=16 * 2**30, mem_available=int(1.4 * 2**30),
            ))
        self.live.view(fresh=True)

    def written(self) -> bool:
        return self.signal.read() is not None


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


class TestWhenItWrites:
    def test_a_three_second_wait_for_the_floor_writes(self, rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 3.0)
        assert rig.signal.read_state().active
        record = rig.signal.read()
        assert record is not None and record.reason == "host_memory_floor"
        assert record.expires_at == rig.clock.now + 20
        assert "asking indexing to slow down" in caplog.text

    def test_a_two_second_wait_does_not(self, rig: Rig) -> None:
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 2.0)
        assert not rig.written()

    def test_live_memory_with_target_at_or_below_the_limit_writes(self, rig: Rig) -> None:
        rig.set_record(limit=3, target=3)
        rig.live.waiting(HoldReason.LIVE_MEMORY, 4.0)
        assert rig.signal.read_state().active

    def test_live_memory_with_target_above_the_limit_does_not(self, rig: Rig) -> None:
        rig.set_record(limit=3, target=4)
        rig.live.waiting(HoldReason.LIVE_MEMORY, 4.0)
        assert not rig.written()

    @pytest.mark.parametrize("reason", [HoldReason.SLOTS_FULL, HoldReason.LIVE_CPU, HoldReason.ORG_SHARE])
    def test_other_reasons_never_write(self, rig: Rig, reason: HoldReason) -> None:
        rig.set_record(limit=3, target=3)
        rig.live.waiting(reason, 60.0)
        assert not rig.written()

    def test_refreshes_are_one_per_five_seconds(self, rig: Rig) -> None:
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 3.0)
        first = rig.signal.read()
        rig.clock.now += 4.9
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 7.9)
        assert rig.signal.read() == first
        rig.clock.now += 0.1
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 8.0)
        second = rig.signal.read()
        assert first is not None and second is not None
        assert second.set_at == first.set_at + 5.0 and second.since == first.since


class TestWhenItNeverWrites:
    def test_the_docker_in_docker_sidecar(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, mode=_mode("sidecar"))
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 10.0)
        assert not rig.written()

    @pytest.mark.parametrize("mode", [LiveMode.off("e2b", "no reading"), LiveMode.off("disabled", "off")])
    def test_without_a_live_reading(self, tmp_path: Path, mode: LiveMode) -> None:
        rig = Rig(tmp_path, mode=mode)
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 10.0)
        assert not rig.written()

    def test_with_the_setting_off(self, tmp_path: Path) -> None:
        settings = LiveSettings.from_env({"SANDBOX_YIELD_INDEXING": "off"})
        assert not settings.yield_indexing
        rig = Rig(tmp_path, settings=settings)
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 10.0)
        assert not rig.written()

    def test_without_a_signal_file(self, rig: Rig) -> None:
        writer = IndexingYield(rig.live, None, rig.settings, clock=rig.clock)
        assert not writer.enabled
        writer.on_memory_wait(HoldReason.HOST_MEMORY_FLOOR, 10.0)
        assert not rig.written()


class TestRelease:
    def test_the_worker_that_started_it_logs_the_release(
        self, rig: Rig, caplog: pytest.LogCaptureFixture,
    ) -> None:
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 3.0)
        rig.clock.now += 5
        rig.live.waiting(HoldReason.HOST_MEMORY_FLOOR, 8.0)
        rig.clock.now += 10
        with caplog.at_level(logging.INFO):
            rig.writer.check_lapsed()
            assert "released" not in caplog.text
            rig.clock.now += 20
            rig.writer.check_lapsed()
            rig.writer.check_lapsed()
        assert caplog.text.count("indexing back-off released after 25s") == 1


class TestSettings:
    def test_defaults_and_bounds(self) -> None:
        assert LiveSettings.from_env({}).yield_ttl_s == 20.0
        assert LiveSettings.from_env({}).yield_indexing
        assert LiveSettings.from_env({"SANDBOX_YIELD_TTL_S": "5"}).yield_ttl_s == 10.0
        assert LiveSettings.from_env({"SANDBOX_YIELD_TTL_S": "500"}).yield_ttl_s == 120.0
        assert LiveSettings.from_env({"SANDBOX_YIELD_INDEXING": "maybe"}).yield_indexing


class TestWiring:
    def test_attached_when_on(self, rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_STATE_ID", "yield-wiring")
        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        rig.live.on_memory_wait = None
        governor_module._attach_indexing_yield(rig.live)
        assert rig.live.on_memory_wait is not None and rig.live.after_sample is not None

    def test_not_attached_when_off(self, tmp_path: Path) -> None:
        rig = Rig(tmp_path, settings=LiveSettings(yield_indexing=False))
        rig.live.on_memory_wait = None
        governor_module._attach_indexing_yield(rig.live)
        assert rig.live.on_memory_wait is None
