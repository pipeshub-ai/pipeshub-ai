"""Shared helpers for ``ResourceGovernor`` integration tests.

These tests drive the real ``ResourceGovernor`` / ``AdmissionGate`` classes
end-to-end and only fake two things: the resource probe (a scripted sequence
of ``ResourceSnapshot``s) and the controller's notion of "now" (a manually
advanced clock), so a test can walk through minutes of sample-interval/
cooldown logic without any real-time sleeping or scheduling races.

Only the *controller's* clock is faked. Anything that actually awaits inside
the event loop (``AdmissionGate.acquire``'s poll loop, ``asyncio.sleep``
inside simulated work) still runs on real wall-clock time — mixing the two
is what a caller must watch for (see the module docstring note on
``AdmissionGate.acquire(timeout=...)`` below).
"""
from __future__ import annotations

import asyncio
import contextlib

from app.services.resource_governor.models import ResourceSnapshot


class ScriptedProbe:
    """Replays whatever snapshot list it currently holds; a test swaps
    ``snapshots`` in place to change conditions between samples."""

    def __init__(self, snapshots: list[ResourceSnapshot]) -> None:
        self.snapshots = snapshots

    def snapshot(self) -> ResourceSnapshot:
        return self.snapshots[-1]


class ManualClock:
    """A ``time.monotonic``-compatible callable a test advances explicitly.

    Note: ``AdmissionGate.acquire(timeout=...)`` computes its deadline from
    this same clock but *polls* via real ``asyncio.wait_for``, so if a test
    jumps this clock far ahead of real elapsed time while a task is blocked
    on ``acquire(timeout=<finite>)``, that task's remaining-time check can
    go permanently negative without the poll loop ever getting a chance to
    return ``False`` promptly (it re-checks only after each real-time poll
    tick) — effectively hanging. Prefer ``timeout=None`` (or a task you
    cancel yourself, never awaiting its result) for anything held across a
    manual-clock jump; reserve finite timeouts for immediate, same-tick
    admission checks.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_snapshot(
    mem_pressure: float,
    *,
    mem_limit_gb: float = 32.0,
    cpu_quota: float = 8.0,
    cpu_utilisation: float = 0.1,
) -> ResourceSnapshot:
    """Build a ``ResourceSnapshot`` from a target memory-pressure fraction.

    ``mem_limit_gb`` defaults generously above
    ``HEAVY_PARSE_WORKING_SET_GB * env_parse`` so a *healthy* snapshot's
    growth target isn't itself memory-bound below the explicit ceiling —
    tests that want to exercise memory-derived target sizing should pass an
    explicit ``mem_limit_gb`` instead of relying on this default.
    """
    return ResourceSnapshot(
        cpu_quota=cpu_quota,
        cpu_utilisation=cpu_utilisation,
        cpu_throttled_ratio=0.0,
        cpu_pressure=0.0,
        mem_limit_bytes=int(mem_limit_gb * 1024 ** 3),
        mem_working_set_bytes=int(mem_pressure * mem_limit_gb * 1024 ** 3),
        source="scripted",
    )


async def cancel_all(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
