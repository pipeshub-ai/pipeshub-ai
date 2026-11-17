"""Tests for resolve_live_mode(): which reading the live limit uses."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import DockerInfo, SandboxCapacity
from app.agent_loop_lib.sandbox.host_reading import (
    HostReader,
    LiveMode,
    boot_id_hash,
    resolve_live_mode,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    HttpGet = Callable[[str, float], tuple[int, object]]

TOTAL_KB = 16384000
TOTAL = TOTAL_KB * 1024


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    (root / "sys" / "kernel" / "random").mkdir(parents=True)
    (root / "sys" / "kernel" / "random" / "boot_id").write_text("boot-a\n")
    (root / "meminfo").write_text(f"MemTotal: {TOTAL_KB} kB\nMemAvailable: 1000 kB\n")
    return root


def _capacity(mode: str = "compose", info: DockerInfo | None = None) -> SandboxCapacity:
    capacity = SandboxCapacity.fixed(4, mode=mode)  # type: ignore[arg-type]
    capacity.docker_info = info
    return capacity


def _get(status: int, body: object = None) -> HttpGet:
    calls: list[str] = []

    def http_get(path: str, timeout: float) -> tuple[int, object]:
        calls.append(path)
        return status, body

    http_get.calls = calls  # type: ignore[attr-defined]
    return http_get


def _resolve(capacity: SandboxCapacity, proc: Path, http_get: HttpGet, *,
             settings: LiveSettings | None = None, limit: int | None = None,
             platform: str = "linux") -> LiveMode:
    return resolve_live_mode(
        capacity, settings or LiveSettings(), http_get=http_get, host_reader=HostReader(proc),
        cgroup_reader=lambda: "cgroup-reader",  # type: ignore[arg-type,return-value]
        own_cgroup_limit=lambda: limit, platform=platform, proc=proc,
    )


def _boom(path: str, timeout: float) -> tuple[int, object]:
    raise ConnectionError("no proxy")


class TestResolveLiveMode:
    def test_matching_boot_id_reads_the_host(self, proc: Path) -> None:
        mode = _resolve(_capacity(), proc, _get(200, {"v": 1, "bootIdHash": boot_id_hash(proc)}))
        assert (mode.mode, mode.note, mode.profile) == ("on:host", "boot id", "host")
        assert isinstance(mode.reader, HostReader)

    def test_another_boot_id_is_off(self, proc: Path) -> None:
        mode = _resolve(_capacity(), proc, _get(200, {"v": 1, "bootIdHash": "0" * 32}))
        assert mode.mode == "off:other-machine" and not mode.on

    def test_old_proxy_falls_back_to_the_fingerprint(self, proc: Path) -> None:
        info = DockerInfo(mem_total_bytes=TOTAL, kernel_version=os.uname().release)
        mode = _resolve(_capacity(info=info), proc, _get(403, {"message": "no"}))
        assert (mode.mode, mode.note) == ("on:host", "fingerprint")

    @pytest.mark.parametrize(("delta", "on"), [(0.004, True), (0.006, False)])
    def test_fingerprint_tolerates_half_a_percent(self, proc: Path, delta: float, on: bool) -> None:
        info = DockerInfo(mem_total_bytes=int(TOTAL * (1 + delta)), kernel_version=os.uname().release)
        assert _resolve(_capacity(info=info), proc, _boom).on is on

    def test_another_kernel_is_off(self, proc: Path) -> None:
        info = DockerInfo(mem_total_bytes=TOTAL, kernel_version="1.0.0-other")
        mode = _resolve(_capacity(info=info), proc, _get(404))
        assert mode.mode == "off:unverified"

    def test_unverified_daemon_is_resolved_again_later(self, proc: Path) -> None:
        mode = _resolve(_capacity(), proc, _boom)
        assert mode.mode == "off:unverified" and mode.retry

    def test_lxcfs_is_off(self, proc: Path) -> None:
        get = _get(200, {"bootIdHash": boot_id_hash(proc)})
        mode = _resolve(_capacity(), proc, get, limit=TOTAL + 1000)
        assert mode.mode == "off:lxcfs"
        assert get.calls == []

    def test_not_linux_is_off(self, proc: Path) -> None:
        assert _resolve(_capacity(), proc, _boom, platform="darwin").mode == "off:not-linux"

    def test_disabled_is_off(self, proc: Path) -> None:
        mode = _resolve(_capacity(), proc, _boom, settings=LiveSettings(enabled=False))
        assert mode.mode == "off:disabled"

    def test_e2b_is_off_and_local_reads_the_cgroup(self, proc: Path) -> None:
        assert _resolve(_capacity("e2b"), proc, _boom).mode == "off:remote"
        local = _resolve(_capacity("local"), proc, _boom)
        assert (local.mode, local.reader) == ("on:cgroup", "cgroup-reader")

    def test_no_meminfo_is_off(self, tmp_path: Path) -> None:
        mode = _resolve(_capacity(), tmp_path / "missing", _boom)
        assert mode.mode == "off:no-meminfo"


class TestSettings:
    def test_junk_is_auto_with_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert LiveSettings.from_env({"SANDBOX_LIVE_LIMIT": "maybe"}).enabled
        assert "SANDBOX_LIVE_LIMIT" in caplog.text

    def test_off(self) -> None:
        assert not LiveSettings.from_env({"SANDBOX_LIVE_LIMIT": "OFF"}).enabled

    def test_defaults(self) -> None:
        s = LiveSettings.from_env({})
        assert (s.enabled, s.interval_s, s.host_mem_floor_mb, s.host_mem_soft_mb) == (True, 15.0, None, None)
        assert (s.host_cpu_brake, s.host_cpu_psi_brake, s.memory_overcommit) == (0.90, 0.75, 1.0)

    @pytest.mark.parametrize(("env", "field", "value"), [
        ({"SANDBOX_LIVE_INTERVAL_S": "1"}, "interval_s", 5.0),
        ({"SANDBOX_LIVE_INTERVAL_S": "abc"}, "interval_s", 15.0),
        ({"SANDBOX_HOST_MEM_FLOOR_MB": "10"}, "host_mem_floor_mb", 256),
        ({"SANDBOX_HOST_MEM_SOFT_MB": "4096"}, "host_mem_soft_mb", 4096),
        ({"SANDBOX_HOST_CPU_BRAKE": "5"}, "host_cpu_brake", 1.0),
        ({"SANDBOX_HOST_CPU_PSI_BRAKE": "0"}, "host_cpu_psi_brake", 0.05),
        ({"SANDBOX_MEMORY_OVERCOMMIT": "5"}, "memory_overcommit", 4.0),
        ({"SANDBOX_MEMORY_OVERCOMMIT": "0.5"}, "memory_overcommit", 1.0),
        ({"SANDBOX_MEMORY_OVERCOMMIT": "abc"}, "memory_overcommit", 1.0),
    ])
    def test_bounds(self, env: dict[str, str], field: str, value: object) -> None:
        assert getattr(LiveSettings.from_env(env), field) == value
