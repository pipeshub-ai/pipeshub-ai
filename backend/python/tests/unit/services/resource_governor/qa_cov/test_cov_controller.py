"""The governor keeps sampling when the code sandbox's yield signal fails."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.feedback import DownstreamFeedback
from app.services.resource_governor.models import Pool
from tests.unit.services.resource_governor.test_controller import (
    FakeYieldSignal,
    ManualClock,
    ScriptedProbe,
    _snap,
)

if TYPE_CHECKING:
    import pytest


class AckFails(FakeYieldSignal):
    def ack(self, pid: int, now: float | None = None, *, at_floor: bool) -> bool:
        self.acks.append((pid, at_floor))
        raise OSError("lock file gone")


def _governor(signal: FakeYieldSignal, clock: ManualClock) -> ResourceGovernor:
    return ResourceGovernor(
        logger=logging.getLogger("test.yield.ack"),
        probe=ScriptedProbe([_snap(mem_pressure_working_set_gb=4.0, mem_limit_gb=32.0, cpu_quota=8.0)]),
        feedback=DownstreamFeedback(),
        sample_interval=15.0,
        clock=clock,
        yield_signal=signal,  # type: ignore[arg-type]
    )


async def test_a_failing_ack_still_slows_indexing_and_keeps_sampling(caplog: pytest.LogCaptureFixture) -> None:
    clock = ManualClock()
    signal = AckFails()
    governor = _governor(signal, clock)
    before = governor.limit(Pool.INDEX_HEAVY)
    with caplog.at_level(logging.DEBUG, logger="test.yield.ack"):
        await governor._sample_once()
        clock.now += 15.0
        await governor._sample_once()
    assert governor.limit(Pool.INDEX_HEAVY) < before
    assert len(signal.acks) == 2
    assert caplog.text.count("could not ack the sandbox yield signal") == 2
    assert not any(r.levelno >= logging.WARNING and "ack" in r.getMessage() for r in caplog.records)
    assert governor.stats()["yield_to_sandboxes"]["active"] is True
