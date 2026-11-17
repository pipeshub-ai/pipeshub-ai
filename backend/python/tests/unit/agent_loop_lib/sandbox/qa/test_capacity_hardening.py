"""Capacity edge cases: a slot file that fails while a run waits, and a
``SANDBOX_MIN_TOTAL`` above what the machine holds."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox import capacity as capacity_mod
from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    FixedLimit,
    MachineSize,
    SandboxCapacity,
)
from app.agent_loop_lib.sandbox.governor import (
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)
from app.services.resource_governor.shared_slots import SlotDenied

if TYPE_CHECKING:
    from collections import Counter

GiB = 2**30


class _FailingSlots:
    """Full, and every read of the counts fails with an I/O error."""

    capacity = 4

    def try_acquire_tagged(self, limit: int, *, tag: bytes, tag_limit: int | None) -> SlotDenied:
        return SlotDenied("full", limit, 0)

    def held_by_tag(self, budget: float = 0.0) -> Counter[bytes]:
        raise OSError(5, "Input/output error")

    def close(self) -> None:
        pass


async def test_a_slot_file_error_while_waiting_is_a_refusal() -> None:
    capacity = SandboxCapacity.fixed(4)
    governor = SandboxResourceGovernor(
        capacity, _FailingSlots(), limit_provider=FixedLimit(capacity),  # type: ignore[arg-type]
    )
    with pytest.raises(SandboxLimitExceeded):
        await governor.acquire(wait_s=0.05)
    assert governor.held_total() is None
    assert governor.snapshot()["stale"] is True


def test_a_minimum_above_the_machine_is_held_to_it_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(capacity_mod, "_warned", set())
    cap = SandboxCapacity("compose", CapacitySettings(min_total=200), slot_files=256)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            cap._apply(MachineSize(16 * GiB, 4, None, "docker_info"), None)
    # 16 GiB / 4 CPUs hold 6: with 200 the limit could neither stay within the
    # machine nor shrink below 200.
    assert (cap.ceiling(), cap.floor()) == (6, 6)
    warnings = [r.getMessage() for r in caplog.records if "SANDBOX_MIN_TOTAL" in r.getMessage()]
    assert len(warnings) == 1 and "200" in warnings[0]


def test_a_minimum_within_the_machine_is_kept() -> None:
    cap = SandboxCapacity("compose", CapacitySettings(min_total=4), slot_files=256)
    cap._apply(MachineSize(64 * GiB, 16, None, "docker_info"), None)
    assert cap.floor() == 4 and cap.ceiling() == 24


def test_the_default_minimum_still_applies_on_a_tiny_machine() -> None:
    cap = SandboxCapacity("compose", CapacitySettings(min_total=8), slot_files=256)
    cap._apply(MachineSize(2 * GiB, 1, None, "docker_info"), None)
    assert (cap.ceiling(), cap.floor()) == (2, 2)
