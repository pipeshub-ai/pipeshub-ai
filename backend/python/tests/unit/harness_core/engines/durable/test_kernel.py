"""EK-1..EK-7, EK-16..EK-19, JS-6 (E1): ``EngineKernel`` under ``RunDriver`` on the in-memory world."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, override

import pytest

from harness_core.driver import CapabilityLost, RunDriver
from harness_core.driver.kernels import ExecutorUnavailable
from harness_core.engines.binary import verify_binary
from harness_core.engines.durable.kernel import (
    ENGINE_PROFILE_MISMATCH,
    ENGINE_PROFILE_MISSING,
)
from harness_core.kernel.durable.capability import CAPABILITY_RECHECK_S
from harness_core.logs import HarnessLog
from harness_core.testing.engines.binary import sha256sum_program
from harness_core.testing.engines.durable import (
    PROGRESS,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.sandbox.fake import FakeSandboxHandle

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineState
    from harness_core.engines.ports import EngineSession
    from harness_core.engines.types import EngineLaunch
    from harness_core.sandbox import SandboxHandle

RUN = "run-ek"


@pytest.fixture
def world() -> EngineWorld:
    return EngineWorld()


async def test_ek1_an_engine_run_is_run_by_the_engine_kernel(world: EngineWorld) -> None:
    world.admit(RUN, "say hello")
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    assert world.journal.status(RUN) == "completed"


async def test_ek1_a_worker_without_the_engine_kernel_refuses_engine_runs(world: EngineWorld) -> None:
    world.admit(RUN, "say hello")
    claim = await world.journal.claim(RUN, worker_id="loop-only")
    assert claim is not None
    driver = RunDriver(journal=world.journal, assembler=None, clock=world.clock)  # type: ignore[arg-type]
    disposition = await driver.run_claimed(claim, claim_sent_at=world.clock.suspend_aware())
    assert (disposition.status, disposition.reason) == ("failed", ExecutorUnavailable.code)


async def test_ek2_the_happy_path_journal(world: EngineWorld) -> None:
    world.admit(RUN, "write out/report.md quarterly numbers\nsay all done")
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    types = world.types(RUN)
    core = [t for t in types if t not in (PROGRESS, "run.admitted", "run.claimed", "run.spec_pinned")]
    assert core[:2] == ["message.completed", "turn.started"]
    assert "workspace.committed" in core
    end = core.index("engine.checkpointed")
    assert core[end:] == [
        "engine.checkpointed",
        "model.response",
        "turn.completed",
        # ADR-RUN-01 D1: what the run produced and its one outcome, with the completion.
        "x.pipeshub.outcome.recorded",
        "x.pipeshub.outcome.settled",
        "message.completed",
        "run.completed",
    ]
    finished = [c for c in world.presenter.calls if c[0] == "finished"]
    assert finished == [("finished", "all done")]
    assert world.presenter.calls[0][0] == "started"
    assert world.broker.sandboxes == {}


async def test_ek3_a_profile_whose_digest_does_not_match_never_runs(world: EngineWorld) -> None:
    world.admit(RUN, "say hi", spec=engine_run_spec(digest="sha256:" + "f" * 64))
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", ENGINE_PROFILE_MISMATCH)
    assert world.broker.acquisitions == 0


async def test_ek3_a_spec_without_a_profile_never_runs(world: EngineWorld) -> None:
    spec = engine_run_spec()
    executor = spec.executor
    assert executor is not None
    stripped = spec.model_copy(
        update={"executor": executor.model_copy(update={"engine": executor.engine.model_copy(update={"profile": None})})}  # type: ignore[union-attr]
    )
    world.admit(RUN, "say hi", spec=stripped)
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", ENGINE_PROFILE_MISSING)


async def test_ek4_input_queued_during_a_turn_is_a_follow_up_turn(world: EngineWorld) -> None:
    world.admit(RUN, "hold\nsay first")
    attempt = asyncio.ensure_future(world.attempt(RUN))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    _ = world.journal.queue_input(RUN, "say second")
    world.gateway.release()
    disposition = await asyncio.wait_for(attempt, 10)
    assert disposition.status == "completed"
    types = world.types(RUN)
    assert types.count("turn.started") == 2
    assert "input.consumed" in types
    assert world.journal.pending_inputs(RUN) == []


async def test_ek5_cancel_mid_turn_discards_drafts_and_cancels(world: EngineWorld) -> None:
    world.admit(RUN, "write out/draft.md x\nhold\nsay never")
    attempt = asyncio.ensure_future(world.attempt(RUN))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    world.journal.request_cancel(RUN)
    world.wakeup.wake(RUN)
    disposition = await asyncio.wait_for(attempt, 10)
    world.gateway.release()
    assert disposition.status == "cancelled"
    assert world.journal.status(RUN) == "cancelled"
    assert world.broker.sandboxes == {}


async def test_ek5_a_cancel_before_the_first_turn_acquires_nothing(world: EngineWorld) -> None:
    world.admit(RUN, "say hi")
    claim = await world.journal.claim(RUN, worker_id="w")
    assert claim is not None
    world.journal.request_cancel(RUN)
    disposition = await world.driver().run_claimed(claim, claim_sent_at=world.clock.suspend_aware())
    assert disposition.status == "cancelled"
    assert world.broker.acquisitions == 0


async def test_ek6_a_spent_token_limit_ends_the_run_cut_short_with_its_partial_result(world: EngineWorld) -> None:
    """BUDGETEND: the engine's turn a limit stopped completes the run with what it wrote and a line that says it
    stopped early (the parent's model reads it), flagged ``budget``, instead of failing ``limit_tokens``."""
    world.admit(RUN, "say hi", spec=engine_run_spec(engine_profile(max_tokens=1)))
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    events = world.journal.events(SESSION)
    assert [e.data for e in events if e.type == "run.completed"] == [{"outcome": "answered", "truncatedBy": "budget"}]
    final = [e for e in events if e.type == "message.completed" and e.data.get("isFinal")]
    assert final, "the partial result is the run's answer"


async def test_ek16_an_attempt_reaps_the_sandboxes_an_earlier_one_left(world: EngineWorld) -> None:
    world.admit(RUN, "say hi")
    leftover = await world.broker.acquire(
        _spec_with(RUN), lease=_lease(), idempotency_key="dead-attempt"
    )
    assert leftover.id in world.broker.sandboxes
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    assert leftover.id not in world.broker.sandboxes


async def test_ek17_past_the_attempt_cap_the_run_is_quarantined(world: EngineWorld) -> None:
    world.admit(RUN, "say hi")
    for _ in range(3):
        claim = await world.journal.claim(RUN, worker_id="crashing")
        assert claim is not None
        world.expire()
    disposition = await world.attempt(RUN)
    assert disposition.status == "quarantined"


def _lease():  # noqa: ANN202
    from harness_core.sandbox.types import LeaseRef

    return LeaseRef("dead-worker", 1)


def _spec_with(run_id: str):  # noqa: ANN202
    from harness_core.sandbox.types import (
        EgressPolicy,
        Resources,
        RuntimeMode,
        SandboxSpec,
    )

    return SandboxSpec(
        image_digest="fake-engine-image",
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(),
        egress=EgressPolicy(),
        mounts=(),
        labels={"orgId": "org-e1", "sessionId": SESSION, "runId": run_id, "attemptId": "dead"},
    )


async def test_ek23_the_lease_is_renewed_while_a_dead_attempts_sandbox_is_reaped(world: EngineWorld) -> None:
    """Found by G4: reaping outlasted the lease, and renewal only started after it."""
    reap = world.broker.reap

    async def slow_reap(*, labels: dict[str, str], older_than_s: int) -> int:
        for _ in range(20):
            world.clock.advance(world.lease_ms / 1000 / 10)
            await asyncio.sleep(0.002)
        return await reap(labels=labels, older_than_s=older_than_s)

    world.broker.reap = slow_reap  # type: ignore[method-assign]
    world.admit(RUN, "say hello")
    disposition = await world.attempt(RUN, kernel=world.kernel(renew_interval_s=0.001))
    assert disposition.status == "completed", disposition


class _LostAssembler:
    """A host whose run lost a pinned tool since admission (an MCP tool its admin never approved)."""

    async def build(self, attempt: object) -> object:
        raise CapabilityLost(missing=["tool:mcp@inst-1/env_digest"])


async def test_e2e3_f4_a_lost_capability_is_rechecked_then_fails_never_a_crash_loop(world: EngineWorld) -> None:
    """Found by E2E3's matrix: the engine kernel released a ``CapabilityLost`` build as ``engine_kernel_error``, so
    the run crash-looped into quarantine. It settles as the loop's driver does: bounded re-checks, then failed."""
    world.admit(RUN, "say hi")
    kernel = world.kernel(assembler=_LostAssembler())
    seen = []
    for delay in CAPABILITY_RECHECK_S:
        disposition = await world.attempt(RUN, kernel=kernel)
        seen.append((disposition.status, disposition.reason))
        assert world.journal.status(RUN) == "queued"
        _ = world.clock.advance(delay)
    final = await world.attempt(RUN, kernel=kernel)
    assert seen == [("yielded", "capability_wait")] * len(CAPABILITY_RECHECK_S)
    assert (final.status, final.reason) == ("failed", "capability_lost")
    assert world.journal.status(RUN) == "failed"
    assert [e.data for e in world.events(RUN) if e.type == "run.failed"] == [{"errorType": "capability_lost"}]


def test_e2e3_f5_the_decisions_note_names_each_interaction_with_its_decision() -> None:
    """Found by E2E3's matrix (MC-5): two held calls of one tool, one approved and one denied, were told only "1
    approved, 1 not approved", so an engine could not know which to call again. The deferred results named each
    interaction; the note names them back."""
    from harness_core.engines.durable.kernel import (
        _decisions_note,  # pyright: ignore[reportPrivateUsage]
    )

    note = _decisions_note({"ix.approve.k1": "approved", "ix.approve.k2": "denied", "ix.approve.k3": "expired"})
    assert "1 approved, 2 not approved." in note
    assert "Approved: ix.approve.k1." in note
    assert "Not approved: ix.approve.k2, ix.approve.k3." in note
    assert "Approved:" not in _decisions_note({"ix.approve.k2": "denied"})


class _RetaggedBinary(ScriptedEngineAdapter):
    """An engine whose image was retagged by hand: its binary hashes to another digest than the pin."""

    @override
    async def open(  # pyright: ignore[reportIncompatibleMethodOverride] - the scripted adapter's open is untyped
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> EngineSession:
        assert isinstance(sandbox, FakeSandboxHandle)
        sandbox.install("sha256sum", sha256sum_program(lambda _path: "sha256:" + "e" * 64))
        log = HarnessLog(logging.getLogger("tests.eh1.engine")).bind(**dict(correlation or {}))
        await verify_binary(sandbox, "/usr/local/bin/fake-engine", launch.engine, log)
        raise AssertionError("a retagged binary was let through")


async def test_eh10_a_retagged_binary_fails_the_run_once_and_names_the_engine_to_the_admin(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = EngineWorld(adapter=_RetaggedBinary())
    world.admit(RUN, "say hi")
    with caplog.at_level(logging.WARNING):
        disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", "binary_mismatch")
    assert world.journal.status(RUN) == "failed"
    assert [e.data["errorType"] for e in world.events(RUN) if e.type == "run.failed"] == ["binary_mismatch"]
    assert world.types(RUN).count("run.claimed") == 1
    assert world.broker.sandboxes == {}
    [fields] = [
        r.harness  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) == "engine.binary.mismatch"
    ]
    assert (fields["runId"], fields["engineKind"], fields["pinnedVersion"]) == (RUN, "jcode", "0.0.0-fake")


async def test_ev10_an_engine_turns_journaled_usage_is_the_gateway_ledgers() -> None:
    """EV-10, OWD-8: the turn's ``model.response`` carries what the gateway billed for its calls, in the harness's
    convention (``inputIncludesCached``), so the run's journal usage and its ledger agree."""
    world = EngineWorld(metered=True)
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay counted')
    assert (await world.attempt(RUN)).status == "completed"
    [response] = [e for e in world.events(RUN) if e.type == "model.response"]
    usage = response.data["usage"]  # type: ignore[index]
    billed = world.gateway.billed
    assert len(billed) >= 2
    assert (usage["inputTokens"], usage["outputTokens"]) == (  # type: ignore[index]
        sum(u.input_tokens for u in billed),
        sum(u.output_tokens for u in billed),
    )
    assert usage["inputIncludesCached"] is True  # type: ignore[index]
