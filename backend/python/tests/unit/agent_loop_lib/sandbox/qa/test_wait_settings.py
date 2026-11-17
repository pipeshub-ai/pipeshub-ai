"""Durations from the environment that would break the slot wait."""

from __future__ import annotations

import asyncio
import math

import pytest

from app.agent_loop_lib.sandbox.coding.base import SandboxContext
from app.agent_loop_lib.sandbox.coding.settings import EnvSandboxSettingsLoader
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxLimitExceeded,
    SandboxResourceGovernor,
)

_KEYS = {
    "SANDBOX_SLOT_WAIT_S": ("slot_wait_s", 120.0),
    "SANDBOX_PROVISION_TIMEOUT_S": ("provision_timeout_s", 60.0),
    "SANDBOX_MAX_LIFETIME_S": ("max_lifetime_s", 1800.0),
}


@pytest.mark.parametrize("key", list(_KEYS))
@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "-1", "-0.5", "1e999", "x", " "])
async def test_a_duration_that_is_not_a_finite_non_negative_number_is_the_default(
    key: str, raw: str, monkeypatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    monkeypatch.setenv(key, raw)
    settings = await EnvSandboxSettingsLoader().load(SandboxContext())
    field, default = _KEYS[key]
    assert getattr(settings, field) == default


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), (" 2.5 ", 2.5), ("30", 30.0)])
async def test_a_valid_slot_wait_is_kept(raw: str, expected: float, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "docker")
    monkeypatch.setenv("SANDBOX_SLOT_WAIT_S", raw)
    assert (await EnvSandboxSettingsLoader().load(SandboxContext())).slot_wait_s == expected


@pytest.mark.parametrize("wait_s", [math.nan, -1.0, -math.inf])
async def test_a_nan_or_negative_wait_refuses_at_once_without_spinning(wait_s: float) -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
    held = await governor.acquire()
    tries = 0
    original = governor._slots.try_acquire_tagged

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal tries
        tries += 1
        return original(*args, **kwargs)

    governor._slots.try_acquire_tagged = counted  # type: ignore[method-assign]
    with pytest.raises(SandboxLimitExceeded):
        await asyncio.wait_for(governor.acquire(wait_s=wait_s), 1.0)
    assert tries == 1
    await governor.release(held)
