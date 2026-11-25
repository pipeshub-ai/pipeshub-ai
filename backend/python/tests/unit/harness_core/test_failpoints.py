from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import (
    Always,
    Arm,
    Delay,
    FailpointError,
    Nth,
    Raise,
    afailpoint,
    failpoint,
    parse_arms,
    registry,
)
from harness_core.testing import armed, write_arms

if TYPE_CHECKING:
    from pathlib import Path

POINT = "worker.tool.after_intent"


class TestK3Raise:
    def test_raises_exactly_once_at_the_named_point(self) -> None:
        with armed({POINT: Raise()}):
            failpoint("worker.other")
            with pytest.raises(FailpointError, match=POINT):
                failpoint(POINT)
            failpoint(POINT)
            failpoint(POINT)
            assert registry.hits(POINT) == 3

    def test_unarmed_points_are_noops(self) -> None:
        assert not registry.active
        failpoint(POINT)
        assert registry.hits(POINT) == 0

    def test_unarmed_path_does_not_touch_the_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_name: str) -> None:
            raise AssertionError("take() must not run while nothing is armed")

        monkeypatch.setattr(registry, "take", boom)
        for _ in range(1000):
            failpoint(POINT)

    def test_custom_exception_type(self) -> None:
        with armed({POINT: Raise(ConnectionResetError, "net")}), pytest.raises(ConnectionResetError):
            failpoint(POINT)

    def test_armed_block_disarms_on_exit(self) -> None:
        with armed({POINT: Arm(Raise(), Always())}):
            pass
        failpoint(POINT)
        assert not registry.active


class TestTriggers:
    def test_nth_fires_only_on_that_hit(self) -> None:
        with armed({POINT: Arm(Raise(), Nth(3))}):
            failpoint(POINT)
            failpoint(POINT)
            with pytest.raises(FailpointError):
                failpoint(POINT)
            failpoint(POINT)

    def test_always_fires_every_time(self) -> None:
        with armed({POINT: Arm(Raise(), Always())}):
            for _ in range(3):
                with pytest.raises(FailpointError):
                    failpoint(POINT)

    def test_nth_is_one_based(self) -> None:
        with pytest.raises(ValueError, match="1-based"):
            Nth(0)


class TestDelay:
    def test_sync_delay_blocks(self) -> None:
        with armed({POINT: Delay(30)}):
            start = time.monotonic()
            failpoint(POINT)
            assert time.monotonic() - start >= 0.025

    async def test_async_delay_yields_to_the_loop(self) -> None:
        ticks: list[int] = []

        async def ticker() -> None:
            for i in range(3):
                ticks.append(i)
                await asyncio.sleep(0.005)

        with armed({POINT: Delay(40)}):
            await asyncio.gather(afailpoint(POINT), ticker())
        assert ticks == [0, 1, 2]

    async def test_async_raise(self) -> None:
        with armed({POINT: Raise()}), pytest.raises(FailpointError):
            await afailpoint(POINT)


def test_once_fires_once_across_threads() -> None:
    fired: list[int] = []
    barrier = threading.Barrier(16)

    def hit(i: int) -> None:
        barrier.wait()
        try:
            failpoint(POINT)
        except FailpointError:
            fired.append(i)

    with armed({POINT: Raise()}):
        threads = [threading.Thread(target=hit, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert registry.hits(POINT) == 16
    assert len(fired) == 1


class TestFileSource:
    def test_round_trip_and_reload_on_change(self, failpoint_file: Path) -> None:
        write_arms(failpoint_file, {POINT: Arm(Raise(message="boom"), Nth(2))})
        registry.install_file(failpoint_file)
        failpoint(POINT)
        with pytest.raises(FailpointError, match="boom"):
            failpoint(POINT)

        time.sleep(0.01)  # distinct mtime_ns on coarse filesystems
        write_arms(failpoint_file, {"worker.other": Raise()})
        failpoint(POINT)
        with pytest.raises(FailpointError):
            failpoint("worker.other")

    def test_missing_file_arms_nothing(self, failpoint_file: Path) -> None:
        registry.install_file(failpoint_file)
        failpoint(POINT)

    def test_file_cannot_carry_custom_exception_types(self, failpoint_file: Path) -> None:
        with pytest.raises(ValueError, match="FailpointError"):
            write_arms(failpoint_file, {POINT: Raise(KeyError)})

    @pytest.mark.parametrize(
        "raw",
        [[], {POINT: {"action": "explode"}}, {POINT: {"action": "raise", "trigger": "twice"}}],
    )
    def test_rejects_malformed_documents(self, raw: object) -> None:
        with pytest.raises(ValueError):
            parse_arms(raw)
