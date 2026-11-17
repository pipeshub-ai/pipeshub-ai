"""Capacity fallbacks: unreadable /proc files, a failing machine reader,
listeners, local mode sizing and the process-wide reset."""

from __future__ import annotations

import asyncio
import gc
import io
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox import capacity as capacity_mod
from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    MachineSize,
    SandboxCapacity,
    read_machine_size,
    read_own_mem_total,
    read_pid_limit,
)

if TYPE_CHECKING:
    from collections.abc import Callable

GiB = 2**30


def _fake_open(files: dict[str, str | type[BaseException]]) -> Callable[..., io.StringIO]:
    def opener(path: str, *args: object, **kwargs: object) -> io.StringIO:
        value = files.get(path, FileNotFoundError)
        if isinstance(value, type):
            raise value(path)
        return io.StringIO(value)

    return opener


class TestProcReads:
    def test_pid_limit_is_the_smaller_of_the_two_kernel_limits(self, monkeypatch) -> None:
        monkeypatch.setattr(capacity_mod, "open", _fake_open({
            "/proc/sys/kernel/pid_max": "4194304\n", "/proc/sys/kernel/threads-max": "62000\n",
        }), raising=False)
        assert read_pid_limit() == 62000

    def test_pid_limit_skips_an_unreadable_or_garbled_file(self, monkeypatch) -> None:
        monkeypatch.setattr(capacity_mod, "open", _fake_open({
            "/proc/sys/kernel/pid_max": "lots\n", "/proc/sys/kernel/threads-max": "500\n",
        }), raising=False)
        assert read_pid_limit() == 500

    def test_pid_limit_unknown_when_neither_reads(self, monkeypatch) -> None:
        monkeypatch.setattr(capacity_mod, "open", _fake_open({
            "/proc/sys/kernel/pid_max": PermissionError, "/proc/sys/kernel/threads-max": "0\n",
        }), raising=False)
        assert read_pid_limit() is None

    @pytest.mark.parametrize(("meminfo", "expected"), [
        ("MemFree: 10 kB\nMemTotal: 2048 kB\n", 2048 * 1024),
        ("MemFree: 10 kB\n", None),            # no MemTotal line
        ("MemTotal:\n", None),                 # no value
        ("MemTotal: many kB\n", None),         # not a number
        (OSError, None),
    ])
    def test_own_mem_total(self, monkeypatch, meminfo, expected) -> None:
        monkeypatch.setattr(capacity_mod, "open", _fake_open({"/proc/meminfo": meminfo}), raising=False)
        assert read_own_mem_total() == expected


class TestLocalMode:
    async def test_local_mode_sizes_from_the_app_cgroup(self, monkeypatch) -> None:
        from app.services.resource_governor import probe

        class _Probe:
            def snapshot(self) -> SimpleNamespace:
                return SimpleNamespace(mem_limit_bytes=8 * GiB, cpu_quota=0)

        monkeypatch.setattr(probe, "SystemResourceProbe", _Probe)
        machine, info = await read_machine_size("local", CapacitySettings())
        # A zero quota means no CPU limit, not zero CPUs.
        assert machine == MachineSize(8 * GiB, None, None, "app_cgroup")
        assert info is None


class TestSizingFailure:
    async def test_an_unexpected_reader_error_sizes_at_the_minimum_and_warns(self, caplog) -> None:
        async def broken() -> tuple[MachineSize, None]:
            raise ValueError("garbled /info")

        cap = SandboxCapacity("compose", CapacitySettings(), slot_files=16, machine_reader=broken)
        with caplog.at_level(logging.WARNING, logger=capacity_mod.__name__):
            await cap.ensure_sized(timeout=2)
        assert cap.measured and cap.sized
        assert cap.ceiling() == CapacitySettings().min_total
        messages = [r.getMessage() for r in caplog.records]
        assert any("reading the machine size failed" in m for m in messages)
        assert any("the machine size is unknown; using the minimum of 2" in m for m in messages)

    async def test_start_sizing_does_nothing_once_measured(self) -> None:
        calls = []

        async def reader() -> tuple[MachineSize, None]:
            calls.append(1)
            return MachineSize(16 * GiB, 4.0, None, "docker_info"), None

        cap = SandboxCapacity("compose", CapacitySettings(), slot_files=16, machine_reader=reader)
        await cap.wait_measured(timeout=2)
        cap.start_sizing()
        await asyncio.sleep(0)
        assert calls == [1]
        assert cap.ceiling() == 6


class TestListeners:
    def test_a_dropped_listener_is_forgotten_and_a_failing_one_does_not_stop_the_rest(self, caplog) -> None:
        cap = SandboxCapacity.fixed(4, slot_files=16)
        seen: list[str] = []

        class Listener:
            def __init__(self, name: str, fail: bool = False) -> None:
                self.name, self.fail = name, fail

            def changed(self) -> None:
                seen.append(self.name)
                if self.fail:
                    raise RuntimeError("listener broke")

        failing, kept = Listener("failing", fail=True), Listener("kept")
        dropped = Listener("dropped")
        for listener in (failing, dropped, kept):
            cap.add_listener(listener.changed)
        del dropped
        gc.collect()
        with caplog.at_level(logging.DEBUG, logger=capacity_mod.__name__):
            cap.override(max_total=5, max_per_org=None)
        assert seen == ["failing", "kept"]
        assert len(cap._listeners) == 2
        assert any("listener failed" in r.getMessage() for r in caplog.records)
        assert cap.ceiling() == 5


class TestResolveMode:
    def test_a_failing_sandbox_mode_counts_as_compose(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding import settings

        def broken() -> str:
            raise RuntimeError("SANDBOX_MODE unreadable")

        monkeypatch.setattr(settings, "resolve_sandbox_mode", broken)
        assert capacity_mod._resolve_mode() == "compose"

    @pytest.mark.parametrize(("backend", "mode"), [("e2b", "e2b"), ("local", "local"), ("docker", "compose")])
    def test_backends(self, monkeypatch, backend, mode) -> None:
        from app.agent_loop_lib.sandbox.coding import settings

        monkeypatch.setattr(settings, "resolve_sandbox_mode", lambda: backend)
        monkeypatch.delenv("SANDBOX_DOCKER_TOPOLOGY", raising=False)
        assert capacity_mod._resolve_mode() == mode


class TestReset:
    async def test_reset_cancels_a_sizing_still_running(self, monkeypatch) -> None:
        started = asyncio.Event()

        async def never() -> tuple[MachineSize, None]:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        cap = SandboxCapacity("compose", CapacitySettings(), slot_files=16, machine_reader=never)
        monkeypatch.setattr(capacity_mod, "_capacity", cap)
        cap.start_sizing()
        await started.wait()
        task = cap._task
        assert task is not None
        capacity_mod.reset_sandbox_capacity()
        assert capacity_mod._capacity is None
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)

    def test_reset_with_a_closed_loop_does_not_raise(self, monkeypatch) -> None:
        loop = asyncio.new_event_loop()
        task = SimpleNamespace(done=lambda: False, get_loop=lambda: loop, cancel=lambda: None)
        cap = SimpleNamespace(_task=task)
        loop.close()
        monkeypatch.setattr(capacity_mod, "_capacity", cap)
        capacity_mod.reset_sandbox_capacity()
        assert capacity_mod._capacity is None
