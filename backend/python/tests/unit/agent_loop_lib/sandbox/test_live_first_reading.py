"""The live limit decides its mode only once the machine has been read.

With an explicit ceiling the capacity needs no measuring, but the
same-machine check without the proxy compares the daemon's ``/info`` with
this machine. The sampler must wait for that first reading, and while it is
still missing, try again soon rather than after the long re-resolve delay.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    DockerInfo,
    DockerUnreachable,
    MachineSize,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.host_reading import (
    HostReader,
    LiveMode,
    resolve_live_mode,
)
from app.agent_loop_lib.sandbox.live_limit import RERESOLVE_S, LiveLimit
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

if TYPE_CHECKING:
    from pathlib import Path

TOTAL_KB = 16384000
TOTAL = TOTAL_KB * 1024


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    (root / "sys" / "kernel" / "random").mkdir(parents=True)
    (root / "sys" / "kernel" / "random" / "boot_id").write_text("boot-a\n")
    (root / "meminfo").write_text(f"MemTotal: {TOTAL_KB} kB\nMemAvailable: 8000000 kB\n")
    return root


def _no_proxy(path: str, timeout: float) -> tuple[int, object]:
    raise ConnectionError("a unix socket straight to the daemon")


def _resolver(capacity: SandboxCapacity, proc: Path):  # noqa: ANN202
    return lambda: resolve_live_mode(
        capacity, LiveSettings(), http_get=_no_proxy, host_reader=HostReader(proc),
        own_cgroup_limit=lambda: None, platform="linux", proc=proc,
    )


def _same_machine_info() -> DockerInfo:
    return DockerInfo(mem_total_bytes=TOTAL, kernel_version=os.uname().release)


class TestFirstReading:
    async def test_explicit_ceiling_waits_for_the_daemon_info(self, tmp_path: Path, proc: Path) -> None:
        async def slow_reader() -> tuple[MachineSize, DockerInfo | None]:
            await asyncio.sleep(0.1)
            return MachineSize(TOTAL, 4.0, None, "docker_info", True), _same_machine_info()

        capacity = SandboxCapacity(
            "compose", CapacitySettings(max_total=2), slot_files=8, machine_reader=slow_reader,
        )
        assert capacity.sized and not capacity.measured
        capacity.start_sizing()
        live = LiveLimit(
            capacity, None, settings=LiveSettings(), resolver=_resolver(capacity, proc),
            directory=tmp_path / "state",
        )
        live.start()
        try:
            for _ in range(200):
                if not live.mode.mode.startswith("off:starting"):
                    break
                await asyncio.sleep(0.01)
            assert (live.mode.mode, live.mode.note) == ("on:host", "fingerprint")
        finally:
            await live.stop()

    async def test_unreachable_docker_does_not_hold_the_sampler(self, tmp_path: Path, proc: Path) -> None:
        async def unreachable() -> tuple[MachineSize, DockerInfo | None]:
            raise DockerUnreachable("no daemon")

        slept: list[float] = []

        async def no_sleep(delay: float) -> None:
            slept.append(delay)
            await asyncio.Event().wait()

        capacity = SandboxCapacity(
            "compose", CapacitySettings(max_total=2), slot_files=8,
            machine_reader=unreachable, sleep=no_sleep,
        )
        capacity.start_sizing()
        live = LiveLimit(
            capacity, None, settings=LiveSettings(), resolver=_resolver(capacity, proc),
            directory=tmp_path / "state",
        )
        live.start()
        try:
            for _ in range(200):
                if not live.mode.mode.startswith("off:starting"):
                    break
                await asyncio.sleep(0.01)
            assert live.mode.mode == "off:unread" and live.mode.retry
        finally:
            await live.stop()
            if capacity._task is not None:
                capacity._task.cancel()


class TestUnreadMode:
    def test_unmeasured_capacity_is_unread(self, proc: Path) -> None:
        capacity = SandboxCapacity("compose", CapacitySettings(max_total=2), slot_files=8)
        mode = _resolver(capacity, proc)()
        assert mode.mode == "off:unread" and mode.retry

    def test_measured_without_info_is_unverified(self, proc: Path) -> None:
        capacity = SandboxCapacity.fixed(2)
        assert _resolver(capacity, proc)().mode == "off:unverified"


class TestReresolveBackoff:
    def test_unread_is_retried_soon_then_less_often(self, tmp_path: Path) -> None:
        live = LiveLimit(
            SandboxCapacity.fixed(2), None, settings=LiveSettings(),
            resolver=lambda: LiveMode.off("unverified", "x"), directory=tmp_path / "state",
        )
        delays = []
        for _ in range(7):
            live.apply_mode(LiveMode.off("unread", "not read yet", retry=True))
            delays.append(live.reresolve_after_s())
        assert delays == [15.0, 30.0, 60.0, 120.0, 240.0, RERESOLVE_S, RERESOLVE_S]

    def test_other_retries_keep_the_long_delay(self, tmp_path: Path) -> None:
        live = LiveLimit(
            SandboxCapacity.fixed(2), None, settings=LiveSettings(),
            resolver=lambda: LiveMode.off("unverified", "x"), directory=tmp_path / "state",
        )
        live.apply_mode(LiveMode.off("unread", "not read yet", retry=True))
        live.apply_mode(LiveMode.off("unverified", "cannot prove", retry=True))
        assert live.reresolve_after_s() == RERESOLVE_S
        live.apply_mode(LiveMode.off("unread", "not read yet", retry=True))
        assert live.reresolve_after_s() == 15.0
