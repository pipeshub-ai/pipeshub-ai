"""RunDriver.run_claimed: settles what the assembler refuses, runs the kernel on scoped bindings."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from harness_core import bindings
from harness_core.driver import (
    AssembledRuntime,
    CapabilityLost,
    RunDriver,
    RunRefused,
)
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.capability import CAPABILITY_RECHECK_S
from harness_core.sandbox.errors import (
    IsolationUnavailable,
    PolicyUnenforceable,
    SandboxRefusal,
)
from harness_core.testing.contracts.kernel_registry import ENGINE
from harness_core.testing.kernels import RecordingKernel
from harness_core.testing.run_spec import minimal_run_spec

from ..durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.journal import Claim
    from harness_core.journal.ports import LiveSinkPort
    from harness_core.journal.types import LiveFrame
    from harness_core.kernel.durable import DurableModelPort, ToolContext


class _Assembler:
    def __init__(
        self,
        world: World,
        port: DurableModelPort | None = None,
        *,
        fail: BaseException | None = None,
        runtime_bindings: bindings.KernelBindings = bindings.NEUTRAL,
        on_close: Callable[[], Awaitable[None]] | None = None,
        live: Callable[[LiveSinkPort], LiveSinkPort] | None = None,
    ) -> None:
        self.world = world
        self.live = live
        self.port = port
        self.fail = fail
        self.on_close = on_close
        self.runtime_bindings = runtime_bindings
        self.built: list[Claim] = []
        self.closed = 0

    async def build(self, claim: Claim) -> AssembledRuntime:
        self.built.append(claim)
        if self.fail is not None:
            raise self.fail
        assert self.port is not None

        async def close() -> None:
            if self.on_close is not None:
                await self.on_close()
            self.closed += 1

        return AssembledRuntime(
            model=self.port,
            tools=self.world.tools,
            content=self.world.content,
            bindings=self.runtime_bindings,
            live=self.live,
            close=close,
        )


def _driver(world: World, assembler: _Assembler, **overrides: Any) -> RunDriver:  # noqa: ANN401
    return RunDriver(
        journal=world.journal, assembler=assembler, clock=world.clock, **overrides
    )


async def test_runs_the_kernel_on_the_assembled_runtime_and_closes_it() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("hello"))
    assembler = _Assembler(world, port)
    done = await _driver(world, assembler).run_claimed(await world.claim())
    assert done.status == "completed"
    assert assembler.closed == 1
    assert world.journal.status(RUN) == "completed"


async def test_citestream_the_run_streams_through_the_sink_its_runtime_wraps() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("hello"))
    sent: list[LiveFrame] = []
    wrapped: list[LiveFrame] = []

    class _Bus:
        async def publish(self, frame: LiveFrame) -> None:
            sent.append(frame)

    class _Wrap:
        def __init__(self, inner: LiveSinkPort) -> None:
            self.inner = inner

        async def publish(self, frame: LiveFrame) -> None:
            wrapped.append(frame)
            await self.inner.publish(frame)

    assembler = _Assembler(world, port, live=_Wrap)
    done = await _driver(world, assembler, live=_Bus()).run_claimed(await world.claim())
    assert done.status == "completed"
    assert sent, "the model's text streamed"
    assert wrapped == sent


async def test_l4_7_a_close_that_raises_keeps_the_settled_disposition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("hello"))

    async def broken() -> None:
        raise OSError("sandbox daemon gone")

    caplog.set_level(logging.INFO)
    done = await _driver(world, _Assembler(world, port, on_close=broken)).run_claimed(
        await world.claim()
    )
    assert done.status == "completed"
    assert world.journal.status(RUN) == "completed"
    (line,) = _lines(caplog, "run.close_failed")
    assert line.levelno == logging.ERROR
    assert line.harness["errorType"] == "OSError"


async def test_l4_8_close_finishes_when_the_attempt_is_cancelled_while_closing() -> None:
    world = World()
    calling, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def hang(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        calling.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def slow_close() -> None:
        closing.set()
        await release.wait()

    world.tools.add("hang", hang, tags=[("category", "read")])
    world.admit()
    _, port = scripted(reply("", ("hang", {})), reply("never"))
    assembler = _Assembler(world, port, on_close=slow_close)
    attempt = asyncio.create_task(_driver(world, assembler).run_claimed(await world.claim()))
    await calling.wait()
    attempt.cancel()
    await closing.wait()
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert assembler.closed == 1


async def test_d6_the_run_sees_its_own_bindings_never_the_installed_ones() -> None:
    world = World()
    seen: list[bindings.KernelBindings] = []

    async def probe(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        seen.append(bindings.current())
        return ToolExecution(status="ok", content="ok")

    world.tools.add("probe", probe, tags=[("category", "read")])
    world.admit()
    _, port = scripted(reply("", ("probe", {})), reply("done"))
    durable = bindings.KernelBindings()
    legacy = bindings.KernelBindings(streamed_answer_argument=lambda name: "legacy")
    previous = bindings.current()
    bindings.install(legacy)
    try:
        assembler = _Assembler(world, port, runtime_bindings=durable)
        done = await _driver(world, assembler).run_claimed(await world.claim())
        assert bindings.current() is legacy
    finally:
        bindings.install(previous)
    assert done.status == "completed"
    assert seen == [durable]
    assert seen[0] is not legacy


async def test_d9_capability_lost_at_build_is_rechecked_without_sampling() -> None:
    world = World()
    world.admit()
    assembler = _Assembler(world, fail=CapabilityLost(missing=["mcp:inst-1"]))
    lost = await _driver(world, assembler).run_claimed(await world.claim())
    assert (lost.status, lost.reason) == ("yielded", "capability_wait")
    assert world.journal.status(RUN) == "queued"
    (event,) = world.events("run.yielded")
    assert event.data["reason"] == "capability_wait"
    assert world.events("run.suspended") == []


async def test_pa7_capability_lost_at_build_fails_after_the_bounded_rechecks() -> None:
    world = World()
    world.admit()
    assembler = _Assembler(world, fail=CapabilityLost(missing=["mcp:inst-1"]))
    driver = _driver(world, assembler)
    seen = []
    for delay in CAPABILITY_RECHECK_S:
        seen.append((await driver.run_claimed(await world.claim())).status)
        _ = world.clock.advance(delay)
    final = await driver.run_claimed(await world.claim())
    assert seen == ["yielded"] * len(CAPABILITY_RECHECK_S)
    assert (final.status, final.reason) == ("failed", "capability_lost")
    (event,) = world.events("run.failed")
    assert event.data == {"errorType": "capability_lost"}
    assert world.journal.status(RUN) == "failed"


async def test_a_refused_run_fails_terminally() -> None:
    world = World()
    world.admit()
    assembler = _Assembler(world, fail=RunRefused("agent_not_found", "agent a1 is gone"))
    failed = await _driver(world, assembler).run_claimed(await world.claim())
    assert (failed.status, failed.reason) == ("failed", "agent_not_found")
    (event,) = world.events("run.failed")
    assert event.data == {"errorType": "agent_not_found", "message": "agent a1 is gone"}


@pytest.mark.parametrize(
    ("refusal", "error_type"),
    [
        (IsolationUnavailable("no gvisor on this host", reason="mode_unavailable"), "isolation_unavailable"),
        (PolicyUnenforceable("egress allowlist needs the proxy", reason="egress_proxy_missing"), "policy_unenforceable"),
    ],
)
async def test_x1_10_a_sandbox_refusal_fails_the_run_terminally_with_its_code(
    refusal: SandboxRefusal, error_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    world = World()
    world.admit()
    failed = await _driver(world, _Assembler(world, fail=refusal)).run_claimed(await world.claim())
    assert (failed.status, failed.reason) == ("failed", error_type)
    (event,) = world.events("run.failed")
    assert event.data["errorType"] == error_type
    (line,) = [r for r in caplog.records if getattr(r, "harness_event", None) == "run.refused"]
    assert line.harness["reasonCode"] == refusal.reason  # type: ignore[attr-defined]


async def test_a_failing_build_is_released_and_quarantined_at_the_attempt_cap() -> None:
    world = World()
    world.admit()
    assembler = _Assembler(world, fail=ConnectionError("graph down"))
    driver = _driver(world, assembler)
    statuses = [(await driver.run_claimed(await world.claim())).status for _ in range(3)]
    assert statuses == ["released", "released", "quarantined"]
    assert world.journal.status(RUN) == "quarantined"
    (event,) = world.events("run.quarantined")
    assert event.data == {"attempts": 3, "lastErrorType": "assembly_failed"}


async def test_drain_reaches_a_running_attempt() -> None:
    world = World()
    world.admit()
    driver: RunDriver | None = None

    async def drain_then_read(
        args: Mapping[str, JsonValue], ctx: ToolContext
    ) -> ToolExecution:
        assert driver is not None
        driver.request_drain()
        return ToolExecution(status="ok", content="read")

    world.tools.add("drain_then_read", drain_then_read, tags=[("category", "read")])
    _, port = scripted(reply("", ("drain_then_read", {})), reply("never"))
    driver = _driver(world, _Assembler(world, port))
    yielded = await driver.run_claimed(await world.claim())
    assert (yielded.status, yielded.reason) == ("yielded", "drain")
    assert world.journal.status(RUN) == "queued"


async def test_ru1a_a_claim_taken_while_draining_is_released_before_assembly() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("never"))
    assembler = _Assembler(world, port)
    driver = _driver(world, assembler)
    driver.request_drain()
    released = await driver.run_claimed(await world.claim())
    assert (released.status, released.reason) == ("released", "drain")
    assert assembler.built == []
    assert world.journal.status(RUN) == "queued"
    assert not world.events("run.yielded")


@pytest.mark.parametrize("status", ["completed"])
async def test_driver_is_reusable_across_claims(status: str) -> None:
    world = World()
    world.admit("r1")
    world.admit("r2")
    _, port = scripted(reply("one"), reply("two"))
    driver = _driver(world, _Assembler(world, port))
    assert (await driver.run_claimed(await world.claim("r1"))).status == status
    assert (await driver.run_claimed(await world.claim("r2"))).status == status


@dataclass(frozen=True)
class _Settle:
    """One commit the driver makes itself, and where it leaves the run."""

    kind: str
    fail: Callable[[], BaseException]
    status: str
    reason: str
    event: str
    journal_status: str
    prior_waits: int = 0
    max_attempts: int = 3


def _capability_lost() -> BaseException:
    return CapabilityLost(missing=["mcp:inst-1"])


SETTLES = [
    _Settle("capability_wait", _capability_lost, "yielded", "capability_wait", "run.yielded", "queued"),
    _Settle(
        "capability_lost",
        _capability_lost,
        "failed",
        "capability_lost",
        "run.failed",
        "failed",
        prior_waits=len(CAPABILITY_RECHECK_S),
    ),
    _Settle(
        "refused",
        lambda: RunRefused("agent_not_found", "agent a1 is gone"),
        "failed",
        "agent_not_found",
        "run.failed",
        "failed",
    ),
    _Settle(
        "assembly_failed",
        lambda: ConnectionError("graph down"),
        "quarantined",
        "assembly_failed",
        "run.quarantined",
        "quarantined",
        max_attempts=1,
    ),
]


def _lines(caplog: pytest.LogCaptureFixture, event: str) -> list[Any]:
    return [r for r in caplog.records if getattr(r, "harness_event", None) == event]


async def _settling(world: World, case: _Settle, **overrides: Any) -> RunDriver:  # noqa: ANN401
    """A driver about to make ``case``'s settle commit on the next claim."""
    world.admit()
    driver = _driver(
        world,
        _Assembler(world, fail=case.fail()),
        max_attempts=case.max_attempts,
        **overrides,
    )
    for delay in CAPABILITY_RECHECK_S[: case.prior_waits]:
        assert (await driver.run_claimed(await world.claim())).status == "yielded"
        _ = world.clock.advance(delay)
    return driver


def _assert_settled_once(world: World, case: _Settle) -> None:
    settled = world.events(case.event)
    prior = case.prior_waits if case.event == "run.yielded" else 0
    assert len(settled) == prior + 1
    assert world.journal.status(RUN) == case.journal_status
    assert all(e.data.get("errorType") != "kernel_error" for e in world.events("run.failed"))


def _advancing(world: World) -> Callable[[float], Any]:
    async def wait(seconds: float) -> None:
        _ = world.clock.advance(seconds)

    return wait


@pytest.mark.parametrize("case", SETTLES, ids=lambda c: c.kind)
async def test_pa21_sr1_a_settle_commit_retries_a_transient_outage(
    case: _Settle, caplog: pytest.LogCaptureFixture
) -> None:
    """Node restarting while the driver settles: the commit is retried while
    the lease holds, like a kernel commit; the run ends where it should."""
    caplog.set_level(logging.INFO)
    world = World()
    driver = await _settling(world, case, journal_retry_s=0)
    world.journal.unavailable_calls = 3
    done = await driver.run_claimed(await world.claim())
    assert (done.status, done.reason) == (case.status, case.reason)
    _assert_settled_once(world, case)
    retries = _lines(caplog, "run.settle_retry")
    assert [r.harness["retries"] for r in retries] == [1, 2, 3]
    assert {r.harness["kind"] for r in retries} == {case.kind}
    assert all(
        r.harness["runId"] == RUN and r.harness["attemptId"] and r.harness["commandId"]
        for r in retries
    )


@pytest.mark.parametrize("case", SETTLES, ids=lambda c: c.kind)
async def test_pa21_sr2_an_outage_past_the_lease_releases_and_a_later_claim_settles(
    case: _Settle, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    driver = await _settling(world, case, journal_retry_s=5.0, sleep=_advancing(world))
    world.journal.unavailable_calls = 1_000
    released = await driver.run_claimed(await world.claim())
    assert (released.status, released.reason) == ("released", "journal_unavailable")
    assert world.events("run.failed") == []
    assert world.journal.status(RUN) == "queued"
    [line] = _lines(caplog, "run.release")
    assert line.harness["reason"] == "journal_unavailable" and line.harness["runId"] == RUN
    assert _lines(caplog, "run.settle_retry")

    world.journal.unavailable_calls = 0
    done = await driver.run_claimed(await world.claim())
    assert (done.status, done.reason) == (case.status, case.reason)
    _assert_settled_once(world, case)


@pytest.mark.parametrize("case", SETTLES, ids=lambda c: c.kind)
async def test_pa21_sr3_a_lost_ack_is_retried_with_the_same_command(
    case: _Settle, caplog: pytest.LogCaptureFixture
) -> None:
    """The journal applied the settle and the reply was lost: the retry is the
    same command, so the journal replays it instead of settling twice."""
    caplog.set_level(logging.INFO)
    world = World()
    driver = await _settling(world, case, journal_retry_s=0)
    world.journal.lost_acks = 1
    done = await driver.run_claimed(await world.claim())
    assert (done.status, done.reason) == (case.status, case.reason)
    _assert_settled_once(world, case)
    [retry] = _lines(caplog, "run.settle_retry")
    settled = _lines(caplog, "run.settled")[-1]
    assert retry.harness["commandId"] == settled.harness["commandId"]


async def test_pa21_sr4_the_recheck_read_retries_a_transient_outage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()
    world.journal.unavailable_reads = 3
    assembler = _Assembler(world, fail=_capability_lost())
    done = await _driver(world, assembler, journal_retry_s=0).run_claimed(
        await world.claim()
    )
    assert (done.status, done.reason) == ("yielded", "capability_wait")
    assert world.events("run.failed") == []
    retries = _lines(caplog, "run.settle_retry")
    assert [(r.harness["kind"], r.harness["retries"]) for r in retries] == [
        ("capability_read", 1),
        ("capability_read", 2),
        ("capability_read", 3),
    ]
    assert all(r.harness["runId"] == RUN and r.harness["attemptId"] for r in retries)


async def test_pa21_sr4_a_recheck_read_outage_past_the_lease_releases_the_claim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = World()
    world.admit()
    world.journal.unavailable_reads = 1_000
    assembler = _Assembler(world, fail=_capability_lost())
    driver = _driver(world, assembler, journal_retry_s=5.0, sleep=_advancing(world))
    released = await driver.run_claimed(await world.claim())
    assert (released.status, released.reason) == ("released", "journal_unavailable")
    assert world.journal.status(RUN) == "queued"
    assert world.events("run.failed") == []
    [line] = _lines(caplog, "run.release")
    assert line.harness["reason"] == "journal_unavailable"

    world.journal.unavailable_reads = 0
    done = await driver.run_claimed(await world.claim())
    assert (done.status, done.reason) == ("yielded", "capability_wait")


def _journal_shape(world: World) -> list[tuple[str, Any]]:
    """The run's journal without the pinned spec, which differs between v2 and v3 by design."""
    return [
        (e.type, {k: v for k, v in e.data.items() if k not in {"spec", "specDigest"}})
        for e in world.events()
    ]


async def test_ks5_a_v3_loop_spec_runs_the_durable_kernel_as_a_v2_spec_does() -> None:
    shapes = []
    for spec in (minimal_run_spec(), minimal_run_spec(executor={"kind": "loop"})):
        world = World()
        world.admit(spec=spec)
        _, port = scripted(reply("hello"))
        assembler = _Assembler(world, port)
        done = await _driver(world, assembler).run_claimed(await world.claim())
        assert done.status == "completed"
        assert len(assembler.built) == 1
        shapes.append(_journal_shape(world))
    assert shapes[0] == shapes[1]


async def test_ks6_an_engine_run_without_an_engine_kernel_is_refused_terminally(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    world = World()
    world.admit(spec=minimal_run_spec(executor=ENGINE), text="secret question")
    _, port = scripted(reply("never"))
    assembler = _Assembler(world, port)
    failed = await _driver(world, assembler).run_claimed(await world.claim())
    assert (failed.status, failed.reason) == ("failed", "executor_unavailable")
    assert assembler.built == []
    assert world.journal.status(RUN) == "failed"
    (event,) = world.events("run.failed")
    assert event.data["errorType"] == "executor_unavailable"
    (line,) = _lines(caplog, "run.refused")
    assert line.levelno == logging.WARNING
    assert line.harness["errorType"] == "executor_unavailable"
    assert line.harness["executorKind"] == "engine"
    assert {"runId", "attemptId", "orgId", "sessionId"} <= set(line.harness)
    assert not [r for r in caplog.records if "secret" in f"{r.getMessage()} {getattr(r, 'harness', '')}"]


async def test_ks7_a_registered_engine_kernel_gets_the_claim_and_the_drain() -> None:
    world = World()
    world.admit(spec=minimal_run_spec(executor=ENGINE))
    engine = RecordingKernel()
    assembler = _Assembler(world)
    driver = _driver(world, assembler, kernels={"engine": engine})
    done = await driver.run_claimed(await world.claim(), claim_sent_at=1.0)
    assert done == engine.disposition
    assert [c.run_id for c in engine.claims] == [RUN]
    assert assembler.built == []
    driver.request_drain()
    assert engine.drains == 1


def test_ks7_a_second_loop_kernel_is_refused() -> None:
    world = World()
    with pytest.raises(ValueError, match="two kernels"):
        _ = _driver(world, _Assembler(world), kernels={"loop": RecordingKernel()})
