"""EP-1..EP-17 on ``RunToolEndpoint`` in process, plus EP-9's exact cadence, EP-16 and EP-18."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from harness_core.testing.clock import ManualClock, settle
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    SECRET,
    ToolEndpointContract,
    build_endpoint,
    claims_for,
)

if TYPE_CHECKING:
    from harness_core.effects import HoldDecision
    from harness_core.gateway import RunToolEndpoint, ToolEndpoint


class InProcess:
    async def serve(self, endpoint: RunToolEndpoint) -> ToolEndpoint:
        return endpoint


class TestRunToolEndpoint(ToolEndpointContract):
    @pytest.fixture
    def endpoint_harness(self) -> InProcess:
        return InProcess()


async def test_ep9_progress_every_interval_while_held() -> None:
    """On the endpoint's injected clock: one progress per interval while held, and no timer left once answered."""
    clock = ManualClock()
    release = asyncio.Event()

    class Hold:
        async def hold(self, call: object) -> HoldDecision:
            await release.wait()
            return "approved"

    world = build_endpoint(
        approvals={"transfer": "ask"}, hold=Hold(), hold_s=120.0, progress_every_s=20.0, sleep=clock.sleep
    )
    ticks: list[float] = []

    async def progress(elapsed: float) -> None:
        ticks.append(elapsed)

    call = asyncio.ensure_future(
        world.endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"}, progress=progress)
    )
    for expected in (1, 2, 3):
        await settle(lambda: clock.pending_timers() == 1)
        _ = clock.advance(20.0)
        await settle(lambda n=expected: len(ticks) == n)
    release.set()
    result = await call
    assert result.disposition == "executed"
    assert ticks == [20.0, 40.0, 60.0]
    assert clock.pending_timers() == 0
    assert clock.advance(100.0) == 0
    assert ticks == [20.0, 40.0, 60.0]


async def test_ep16_each_refusal_is_logged_once_with_its_code(caplog: pytest.LogCaptureFixture) -> None:
    endpoint = build_endpoint().endpoint
    with caplog.at_level(logging.INFO):
        await endpoint.call_tool(claims_for(BINDING), "ghost", {"q": "secret-arg"})
        await endpoint.call_tool(claims_for(BINDING), "lookup", {"q": SECRET})
    refused = [r for r in caplog.records if r.levelno == logging.WARNING]
    codes = [r.__dict__["harness"].get("reason") for r in refused]
    assert codes == ["not_granted", "forbidden_arguments"]
    for record in refused:
        fields = record.__dict__["harness"]
        assert (fields["orgId"], fields["runId"], fields["attemptId"]) == ("org-1", "run-1", "run-1.a2")
    assert "secret-arg" not in caplog.text and SECRET not in caplog.text


async def test_ep16_a_binding_mismatch_is_logged_once() -> None:
    endpoint = build_endpoint().endpoint
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture(level=logging.WARNING)
    logging.getLogger("harness_core.gateway").addHandler(handler)
    try:
        from harness_core.gateway import BindingMismatch
        from harness_core.testing.contracts.tool_endpoint import EARLIER_ATTEMPT

        with pytest.raises(BindingMismatch):
            await endpoint.list_tools(claims_for(EARLIER_ATTEMPT))
    finally:
        logging.getLogger("harness_core.gateway").removeHandler(handler)
    assert [r.__dict__["harness"]["reason"] for r in records] == ["binding_mismatch"]


async def test_a_disconnect_does_not_cut_an_effect_between_intent_and_outcome() -> None:
    """The invoke is shielded: cancelling the caller lets the effect finish and record its outcome."""
    held, decide = asyncio.Event(), asyncio.Event()

    class Hold:
        async def hold(self, call: object) -> HoldDecision:
            held.set()
            await decide.wait()
            return "approved"

    world = build_endpoint(approvals={"transfer": "ask"}, hold=Hold())
    task = asyncio.ensure_future(world.endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"}))
    await held.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert world.runs == []
    decide.set()
    await settle(lambda: world.runs == ["transfer"])
    again = await world.endpoint.call_tool(claims_for(BINDING), "transfer", {"q": "1"})
    assert again.disposition == "recorded"
    assert world.runs == ["transfer"]


def test_ep18_the_gateway_imports_alone() -> None:
    code = (
        "import sys, harness_core.gateway, harness_core.testing.contracts.tool_endpoint\n"
        "bad = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')"
        " or m.startswith('harness_core.kernel.durable'))\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
