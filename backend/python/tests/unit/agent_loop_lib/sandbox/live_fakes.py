"""Fakes shared by the live limit tests."""

from __future__ import annotations

import threading
from typing import Literal

from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    MachineSize,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.host_reading import HostSample

GiB = 2**30


def sample(avail_gib: float, total_gib: float = 16, **kwargs: object) -> HostSample:
    return HostSample(
        mem_total=int(total_gib * GiB), mem_available=int(avail_gib * GiB), source="host", **kwargs,  # type: ignore[arg-type]
    )


class FakeReader:
    def __init__(
        self, first: HostSample | None = None,
        source: Literal["host", "cgroup", "sidecar", "sidecar-containers"] = "host",
    ) -> None:
        self.sample = first if first is not None else sample(7.0)
        self.source = source
        self.reads = 0
        self.memory_reads = 0

    def read(self) -> HostSample:
        self.reads += 1
        return self.sample

    def memory(self) -> tuple[int | None, int | None]:
        self.memory_reads += 1
        return self.sample.mem_available, self.sample.mem_total


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeGovernor:
    def __init__(self, held: int | None = 0) -> None:
        self.held = held
        self.changed = 0
        self.held_threads: list[threading.Thread] = []

    def held_total(self, budget: float = 0.0) -> int | None:
        self.held_threads.append(threading.current_thread())
        return self.held

    def capacity_changed(self) -> None:
        self.changed += 1


def capacity(memory_gib: float = 16, cpus: float = 4, *, max_total: int | None = None,
             slot_files: int = 256) -> SandboxCapacity:
    """A sized docker-mode capacity: 16 GiB / 4 CPUs gives a ceiling of 6."""
    cap = SandboxCapacity("compose", CapacitySettings(max_total=max_total), slot_files=slot_files)
    cap._apply(MachineSize(int(memory_gib * GiB), cpus, None, "docker_info"), None)
    return cap
