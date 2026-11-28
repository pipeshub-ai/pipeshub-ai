"""LG-3: where a log line is the only signal of a failure path, it is asserted."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from harness_core.driver import AssembledRuntime, CapabilityLost, RunDriver
from harness_core.failpoints import Raise, registry
from harness_core.kernel.durable.capability import CAPABILITY_RECHECK_S
from harness_core.logs import HarnessLog
from harness_core.testing import ManualClock

from .support import RUN, Crash, World, reply, scripted
from .test_needs_input_and_capabilities import _Observed

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest

    from harness_core.journal import Claim
    from harness_core.kernel.durable import DeltaKind, ModelCall, SampledTurn


def _lines(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "harness_event", None) == event]


def test_the_helper_puts_correlation_fields_first_and_in_extra(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    HarnessLog.named("t", runId="r1", orgId="o1").bind(fence=3).info("run.x", reason="y", count=2)
    (record,) = caplog.records
    assert record.getMessage() == "run.x orgId=o1 runId=r1 fence=3 count=2 reason=y"
    assert record.harness == {"runId": "r1", "orgId": "o1", "fence": 3, "reason": "y", "count": 2}  # type: ignore[attr-defined]


async def test_quarantine_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()
    _, port = scripted(reply("never"))
    for _ in range(3):
        _ = await world.claim()
        world.expire()
    quarantined = await world.run(world.kernel(port))
    assert quarantined.status == "quarantined"
    (line,) = _lines(caplog, "run.quarantine")
    assert line.levelno == logging.WARNING
    assert line.harness["runId"] == RUN and line.harness["reason"] == "crash_loop"  # type: ignore[attr-defined]
    assert line.harness["attempt"] == 4  # type: ignore[attr-defined]


async def test_self_fence_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    world = World(worker_clock=ManualClock())
    world.admit()
    _, port = scripted(reply("", ("post_message", {"text": "late"})), reply("x"))
    original = port.sample

    async def sleepy(call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        _ = world.clock.suspend(120)
        return await original(call, on_delta)

    port.sample = sleepy  # type: ignore[method-assign]
    lost = await world.run(world.kernel(port))
    assert lost.status == "lost"
    (line,) = _lines(caplog, "run.self_fence")
    assert line.levelno == logging.WARNING and line.harness["runId"] == RUN  # type: ignore[attr-defined]
    assert _lines(caplog, "run.lease_lost") == []


async def test_a_digest_mismatch_is_logged_with_what_changed(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    observed = _Observed({"mcp:inst-1": "sha256:" + "a" * 64, "tool:t": "sha256:" + "c" * 64})
    registry.arm("worker.model.after_response_commit", Raise(Crash))
    await world.crash(world.kernel(port, capability_check=observed))
    registry.reset()
    observed.value = {"mcp:inst-1": "sha256:" + "b" * 64, "tool:t": "sha256:" + "c" * 64}
    _ = await world.run(world.kernel(port, capability_check=observed))
    (wait,) = _lines(caplog, "run.capability_wait")
    assert wait.levelno == logging.INFO
    assert wait.harness["reason"] == "digest_mismatch"  # type: ignore[attr-defined]
    assert wait.harness["changed"] == ["mcp:inst-1"]  # type: ignore[attr-defined]
    assert wait.harness["recheck"] == 1  # type: ignore[attr-defined]
    assert _lines(caplog, "run.capabilities_recorded")
    for delay in CAPABILITY_RECHECK_S:
        _ = world.clock.advance(delay)
        _ = await world.run(world.kernel(port, capability_check=observed))
    (line,) = _lines(caplog, "run.capability_lost")
    assert line.levelno == logging.WARNING
    assert line.harness["outcome"] == "failed"  # type: ignore[attr-defined]
    assert line.harness["rechecks"] == len(CAPABILITY_RECHECK_S)  # type: ignore[attr-defined]


async def test_capability_lost_at_build_is_logged_with_what_is_missing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()

    class _Lost:
        async def build(self, claim: Claim) -> AssembledRuntime:
            raise CapabilityLost(missing=["mcp:inst-9", "tool:jira@i/x"])

    driver = RunDriver(journal=world.journal, assembler=_Lost(), clock=world.clock)
    _ = await driver.run_claimed(await world.claim())
    (line,) = _lines(caplog, "run.capability_wait")
    assert line.harness["missing"] == ["mcp:inst-9", "tool:jira@i/x"]  # type: ignore[attr-defined]
    assert line.harness["runId"] == RUN  # type: ignore[attr-defined]
    assert _lines(caplog, "run.settled")[0].harness["status"] == "yielded"  # type: ignore[attr-defined]
    for delay in CAPABILITY_RECHECK_S:
        _ = world.clock.advance(delay)
        _ = await driver.run_claimed(await world.claim())
    (lost,) = _lines(caplog, "run.capability_lost")
    assert lost.levelno == logging.WARNING
    assert lost.harness["missing"] == ["mcp:inst-9", "tool:jira@i/x"]  # type: ignore[attr-defined]
    assert _lines(caplog, "run.settled")[-1].harness["status"] == "failed"  # type: ignore[attr-defined]


async def test_a_quarantine_after_failing_builds_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()

    class _Broken:
        async def build(self, claim: Claim) -> AssembledRuntime:
            raise ConnectionError("graph down")

    driver = RunDriver(journal=world.journal, assembler=_Broken(), clock=world.clock)
    for _ in range(3):
        _ = await driver.run_claimed(await world.claim())
    assert len(_lines(caplog, "run.assembly_failed")) == 3
    assert all(r.levelno == logging.ERROR and r.exc_info for r in _lines(caplog, "run.assembly_failed"))
    (line,) = _lines(caplog, "run.quarantine")
    assert line.harness["reason"] == "assembly_failed"  # type: ignore[attr-defined]
