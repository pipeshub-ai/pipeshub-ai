"""Reading the machine size, and the startup retry while Docker is down."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

import pytest

import app.agent_loop_lib.sandbox.capacity as capacity_module
from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    DockerInfo,
    DockerUnreachable,
    MachineSize,
    SandboxCapacity,
    fingerprint_same_machine,
    read_machine_size,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

GiB = 2**30
OWN_MEM = 16 * GiB


class _Refused(Exception):
    status_code = 403


def _info(mem: int = OWN_MEM, kernel: str | None = None) -> dict[str, object]:
    return {
        "NCPU": 4, "MemTotal": mem, "KernelVersion": kernel or os.uname().release,
        "OperatingSystem": "Ubuntu", "Driver": "overlay2",
    }


@pytest.fixture(autouse=True)
def _host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capacity_module, "read_own_mem_total", lambda: OWN_MEM)
    monkeypatch.setattr(capacity_module, "read_pid_limit", lambda: 4_194_304)
    capacity_module._warned.clear()


def _reader(payload: object) -> Callable[[], Awaitable[object]]:
    async def read() -> object:
        if isinstance(payload, BaseException):
            raise payload
        return payload
    return read


class TestFingerprint:
    def test_same_memory_and_kernel(self) -> None:
        assert fingerprint_same_machine(DockerInfo.from_api(_info()), OWN_MEM)

    def test_memory_within_half_a_percent_is_the_same_machine(self) -> None:
        assert fingerprint_same_machine(DockerInfo.from_api(_info(int(OWN_MEM * 1.004))), OWN_MEM)
        assert not fingerprint_same_machine(DockerInfo.from_api(_info(int(OWN_MEM * 1.006))), OWN_MEM)

    def test_another_kernel_is_another_machine(self) -> None:
        assert not fingerprint_same_machine(DockerInfo.from_api(_info(kernel="0.0.1-other")), OWN_MEM)

    def test_unknown_is_not_the_same(self) -> None:
        assert not fingerprint_same_machine(None, OWN_MEM)
        assert not fingerprint_same_machine(DockerInfo.from_api(_info()), None)


class TestReadMachineSize:
    async def test_docker_info(self) -> None:
        machine, info = await read_machine_size("compose", CapacitySettings(), docker_info=_reader(_info()))
        assert machine == MachineSize(OWN_MEM, 4.0, 4_194_304, "docker_info", True)
        assert info is not None and info.driver == "overlay2" and info.ncpus == 4

    async def test_the_pid_term_is_skipped_on_another_machine(self) -> None:
        machine, _ = await read_machine_size(
            "compose", CapacitySettings(), docker_info=_reader(_info(kernel="other")),
        )
        assert machine.same_machine is False and machine.pid_limit is None

    async def test_an_explicit_pid_limit_applies_anywhere(self) -> None:
        machine, _ = await read_machine_size(
            "compose", CapacitySettings(host_pid_limit=1000), docker_info=_reader(_info(kernel="other")),
        )
        assert machine.pid_limit == 1000

    async def test_unreadable_pid_files_skip_the_term(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(capacity_module, "read_pid_limit", lambda: None)
        machine, _ = await read_machine_size("compose", CapacitySettings(), docker_info=_reader(_info()))
        assert machine.pid_limit is None

    async def test_a_refused_info_falls_back_to_this_containers_view(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            for _ in range(2):
                machine, info = await read_machine_size(
                    "compose", CapacitySettings(), docker_info=_reader(_Refused("403 Forbidden")),
                )
        assert machine.source == "proc_fallback" and info is None
        assert machine.memory_bytes == OWN_MEM and machine.cpus == float(os.cpu_count())
        assert machine.same_machine is None and machine.pid_limit is None
        assert caplog.text.count("/info refused by the docker proxy") == 1

    async def test_an_unreachable_daemon_is_retried(self) -> None:
        with pytest.raises(DockerUnreachable):
            await read_machine_size(
                "compose", CapacitySettings(), docker_info=_reader(ConnectionError("refused")),
            )

    async def test_host_env_wins_over_info(self) -> None:
        async def never() -> object:
            raise AssertionError("/info must not be read")

        machine, info = await read_machine_size(
            "compose", CapacitySettings(host_memory_bytes=4 * GiB, host_cpu_millis=2000),
            docker_info=never,
        )
        assert machine == MachineSize(4 * GiB, 2.0, 4_194_304, "helm_env")
        assert info is None

    async def test_e2b_reads_nothing(self) -> None:
        machine, info = await read_machine_size("e2b", CapacitySettings())
        assert machine.source == "fixed" and info is None


class TestStartupSizing:
    def _capacity(self, answers: list[object], sleeps: list[float]) -> SandboxCapacity:
        async def reader() -> tuple[MachineSize, None]:
            answer = answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            await asyncio.sleep(0)

        return SandboxCapacity(
            "compose", CapacitySettings(), slot_files=256, machine_reader=reader, sleep=sleep,
        )

    async def test_starts_on_the_minimum_and_retries_until_docker_answers(self, caplog) -> None:
        machine = (MachineSize(16 * GiB, 4, 4_194_304, "docker_info", True), None)
        down = DockerUnreachable("ConnectionError")
        sleeps: list[float] = []
        cap = self._capacity([down] * 5 + [machine], sleeps)
        woken: list[bool] = []

        class Governor:
            def capacity_changed(self) -> None:
                woken.append(True)

        governor = Governor()
        cap.add_listener(governor.capacity_changed)
        assert (cap.ceiling(), cap.sized) == (2, False)
        with caplog.at_level(logging.INFO):
            plan = await cap.ensure_sized(timeout=1)
            assert plan.ceiling == 2 and not cap.sized
            await cap._task
        assert sleeps == [5.0, 15.0, 30.0, 60.0, 60.0]
        assert (cap.ceiling(), cap.sized, cap.plan.binding) == (6, True, "cpu")
        assert woken == [True]
        assert caplog.text.count("Docker unreachable") == 1
        assert "ceiling=6 (cpu) floor=2 slot_files=256 org_cap=all" in caplog.text

    async def test_a_reachable_daemon_is_sized_before_the_first_run(self) -> None:
        machine = (MachineSize(64 * GiB, 16, 4_194_304, "docker_info", True), None)
        cap = self._capacity([machine], [])
        plan = await cap.ensure_sized(timeout=1)
        assert plan.ceiling == 24 and cap.sized

    async def test_an_explicit_ceiling_needs_no_wait(self) -> None:
        cap = SandboxCapacity("compose", CapacitySettings(max_total=5), slot_files=256)
        assert cap.sized
        assert (await cap.ensure_sized(timeout=0)).ceiling == 5
        assert cap._task is None
