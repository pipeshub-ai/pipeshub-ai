"""K-6..K-10: batch barrier, synthetic cancelled outcomes, yield, quarantine and self-fencing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.failpoints import Always, Arm, Raise
from harness_core.journal import (
    AttemptToken,
    JournalUnavailable,
    LeaseGrant,
    OwnershipLost,
)
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.durable import Lease, LeaseKeeper
from harness_core.kernel.fold import fold_run
from harness_core.testing import ErrorSpec, ManualClock, Reply, armed
from tests.support.settle import settle

from .support import RUN, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable import (
        DeltaKind,
        ModelCall,
        SampledTurn,
        ToolEffect,
    )

CRASH = Raise(Crash)


def _world() -> World:
    world = World()
    world.admit()
    return world


def _ask_for_writes(call: ToolCall, effect: ToolEffect) -> str:
    return "ask" if effect.needs_intent_commit else "allow"


async def test_k6_barrier_commits_reads_then_suspends_and_resume_runs_only_the_write() -> (
    None
):
    world = _world()
    _, port = scripted(
        reply(
            "",
            ("lookup", {"q": "a"}),
            ("lookup", {"q": "b"}),
            ("create_issue", {"title": "x"}),
        ),
        reply("created"),
    )
    kernel = world.kernel(port, approval=_ask_for_writes)
    suspended = await world.run(kernel)
    assert suspended.status == "suspended" and suspended.reason == "ix.approve.t1.2"
    tail = world.types()[-5:]
    assert tail == [
        "tool.intent",
        "tool.outcome",
        # WP-97: the held write's reason, journaled with its request.
        "interaction.explained",
        "interaction.requested",
        "run.suspended",
    ]
    assert len(world.events("tool.outcome")) == 2 and world.saas.effects == []
    world.journal.resolve_interaction(RUN, "ix.approve.t1.2", "approved")
    done = await world.run(world.kernel(port, approval=_ask_for_writes))
    assert done.status == "completed"
    assert [name for name, _ in world.tools.executed] == [
        "lookup",
        "lookup",
        "create_issue",
    ]
    last = port.calls[-1].messages
    assert_paired(last)
    assert [m.tool_call_id for m in last if isinstance(m, ToolMessage)] == [
        "call_0_0",
        "call_0_1",
        "call_0_2",
    ]


async def test_k6_denied_approval_records_a_denial_outcome() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("create_issue", {"title": "x"})), reply("not allowed")
    )
    assert (
        await world.run(world.kernel(port, approval=_ask_for_writes))
    ).status == "suspended"
    world.journal.resolve_interaction(RUN, "ix.approve.t1.0", "denied")
    assert (
        await world.run(world.kernel(port, approval=_ask_for_writes))
    ).status == "completed"
    assert world.saas.effects == []
    assert world.events("tool.denied")[0].data["rule"] == "interaction_denied"


async def test_k7_cancel_mid_batch_closes_every_dangling_call_and_next_run_is_valid() -> (
    None
):
    world = _world()
    world.on_read = lambda w: w.journal.request_cancel(RUN)
    _, port = scripted(
        reply(
            "",
            ("lookup", {"q": "a"}),
            ("create_issue", {"title": "b"}),
            ("post_message", {"text": "c"}),
        ),
    )
    cancelled = await world.run(world.kernel(port))
    assert cancelled.status == "cancelled"
    outcomes = {
        str(e.data["invocationId"]): e.data["status"]
        for e in world.events("tool.outcome")
    }
    assert outcomes == {"t1.0": "ok", "t1.1": "cancelled", "t1.2": "cancelled"}
    assert world.saas.effects == []
    assert world.journal.status(RUN) == "cancelled"

    world.on_read = None
    world.admit("run2", text="try again")
    _, port2 = scripted(reply("second run"))
    assert (await world.run(world.kernel(port2), "run2")).status == "completed"
    request = port2.calls[0].messages
    assert_paired(request)
    assert request[-1].content == "try again"


async def test_k7_cancel_while_suspended_leaves_the_next_request_valid() -> None:
    world = _world()
    _, port = scripted(reply("", ("create_issue", {"title": "x"})))
    assert (
        await world.run(world.kernel(port, approval=_ask_for_writes))
    ).status == "suspended"
    world.journal.request_cancel(RUN)
    assert world.journal.status(RUN) == "cancelled"
    world.admit("run2", text="next")
    _, port2 = scripted(reply("ok"))
    assert (await world.run(world.kernel(port2), "run2")).status == "completed"
    assert_paired(port2.calls[0].messages)


async def test_k8_drain_yields_and_a_new_claim_resumes_at_the_same_cursor() -> None:
    world = _world()
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    kernel = world.kernel(port)
    world.on_read = lambda _w: kernel.request_drain()
    yielded = await world.run(kernel)
    assert yielded.status == "yielded" and yielded.reason == "drain"
    before = fold_run(world.journal.events("ses1"), run_id=RUN).cursor
    assert (before.next_action, before.turn) == ("call_model", 2)
    ref = world.journal.checkpoint_ref(RUN)
    assert ref is not None
    world.on_read = None
    claim = await world.claim()
    assert claim.checkpoint_ref == ref and claim.attempt == 1
    done = await world.kernel(port).run_claimed(claim)
    assert done.status == "completed"
    assert model.remaining == 0 and len(port.calls) == 2
    assert len(world.events("model.response")) == 2


def _drain_on_call(kernel: Any, port: Any, nth: int) -> None:  # noqa: ANN401
    """SIGTERM arrives while the ``nth`` model call streams."""
    sample = port.sample
    calls = 0

    async def draining(call: ModelCall, on_delta: Any) -> SampledTurn:  # noqa: ANN401
        nonlocal calls
        calls += 1
        if calls == nth:
            kernel.request_drain()
        return await sample(call, on_delta)

    port.sample = draining


async def test_ru1_a_drain_during_a_model_call_commits_it_then_yields_before_the_tools() -> None:
    world = _world()
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    kernel = world.kernel(port)
    _drain_on_call(kernel, port, 1)
    yielded = await world.run(kernel)
    assert (yielded.status, yielded.reason) == ("yielded", "drain")
    assert len(world.events("model.response")) == 1
    assert not world.events("tool.outcome")
    cursor = fold_run(world.journal.events("ses1"), run_id=RUN).cursor
    assert (cursor.next_action, cursor.turn) == ("dispatch_pending", 1)
    done = await world.kernel(port).run_claimed(await world.claim())
    assert done.status == "completed"
    # No re-sample: two turns, two model calls, one response each.
    assert model.remaining == 0 and len(port.calls) == 2
    assert len(world.events("model.response")) == 2
    assert len(world.events("tool.outcome")) == 1


async def test_ru1_a_drain_during_the_answer_finishes_the_run_instead_of_yielding() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    kernel = world.kernel(port)
    _drain_on_call(kernel, port, 2)
    done = await world.run(kernel)
    assert done.status == "completed"
    assert not world.events("run.yielded")


async def test_k8_long_llm_backoff_yields_with_not_before() -> None:
    world = _world()
    _, port = scripted(
        Reply(error=ErrorSpec(kind="rate_limited", retry_after_s=30)), reply("later")
    )
    yielded = await world.run(world.kernel(port))
    assert yielded.status == "yielded" and yielded.reason == "llm_backoff"
    assert await world.journal.claim(worker_id="w") is None
    _ = world.db_clock.advance(31)
    assert (await world.run(world.kernel(port))).status == "completed"


async def test_k9_crash_loop_is_quarantined_after_three_attempts() -> None:
    world = _world()
    _, port = scripted(*(reply("never lands") for _ in range(4)))
    with armed({"worker.model.before_response_commit": Arm(CRASH, Always())}):
        for _ in range(3):
            await world.crash(world.kernel(port))
    quarantined = await world.run(world.kernel(port))
    assert quarantined.status == "quarantined"
    assert world.journal.status(RUN) == "quarantined"
    assert world.events("run.quarantined")[0].data == {
        "attempts": 4,
        "lastErrorType": "crash_loop",
    }


async def test_k10_self_fence_stops_a_worker_that_slept_through_its_lease() -> None:
    world = World(worker_clock=ManualClock())
    world.admit()
    _, port = scripted(reply("", ("post_message", {"text": "late"})), reply("x"))
    worker_clock = world.clock

    original = port.sample

    async def sleepy(
        call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        _ = worker_clock.suspend(120)  # the laptop lid closed mid-call
        return await original(call, on_delta)

    port.sample = sleepy  # type: ignore[method-assign]
    lost = await world.run(world.kernel(port))
    assert lost.status == "lost"
    assert world.saas.effects == []
    assert world.events("tool.intent") == [] and world.events("model.response") == []
    assert world.journal.status(RUN) == "running", (
        "the DB still thinks the lease is live"
    )


# ---- leases ----


@dataclass
class _RenewJournal:
    outcomes: list[str]
    calls: int = 0

    async def renew(self, token: AttemptToken) -> LeaseGrant:
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if outcome == "unavailable":
            raise JournalUnavailable("503")
        if outcome == "lost":
            raise OwnershipLost("409")
        return LeaseGrant(30_000)


def _keeper(journal: _RenewJournal, clock: ManualClock, lease: Lease) -> LeaseKeeper:
    async def no_sleep(_: float) -> None:
        _ = clock.advance(1)

    return LeaseKeeper(
        cast("Any", journal),
        AttemptToken("r", "a", 1),
        lease,
        clock,
        interval_s=10,
        sleep=no_sleep,
    )


async def test_renew_retries_transient_errors_until_valid_until() -> None:
    clock = ManualClock()
    lease = Lease(clock, sent_at=0, lease_ms=30_000, margin_s=2)
    journal = _RenewJournal(["unavailable", "unavailable", "ok"])
    assert await _keeper(journal, clock, lease).renew_once()
    assert journal.calls == 3 and lease.valid_until == pytest.approx(2 + 28)


async def test_renew_gives_up_on_409_or_expiry() -> None:
    clock = ManualClock()
    lease = Lease(clock, sent_at=0, lease_ms=30_000, margin_s=2)
    assert not await _keeper(_RenewJournal(["lost"]), clock, lease).renew_once()
    assert lease.lost_reason is not None

    clock2 = ManualClock()
    lease2 = Lease(clock2, sent_at=0, lease_ms=3_000, margin_s=0)
    assert not await _keeper(
        _RenewJournal(["unavailable"] * 10), clock2, lease2
    ).renew_once()
    assert "expired" in (lease2.lost_reason or "")


async def test_self_fence_uses_the_suspend_aware_clock() -> None:
    clock = ManualClock()
    lease = Lease(clock, sent_at=0, lease_ms=30_000, margin_s=2)
    lease.check()
    _ = clock.suspend(40)
    assert clock.monotonic() == 0
    with pytest.raises(Exception, match="self-fence"):
        lease.check()


async def test_renew_does_not_wait_for_a_blocked_commit() -> None:
    world = _world()
    claim = await world.claim()
    gate = asyncio.Event()
    lock = asyncio.Lock()

    async def blocked_commit() -> None:
        async with lock:
            await gate.wait()

    commit = asyncio.create_task(blocked_commit())
    await asyncio.sleep(0)
    lease = Lease(world.clock, sent_at=0, lease_ms=claim.lease_ms, margin_s=2)
    keeper = LeaseKeeper(world.journal, claim.token, lease, world.clock, interval_s=10)
    assert await settle(keeper.renew_once())
    gate.set()
    await commit


class _FlakyContent:
    """Node's content route answers unreachable for the first ``failures`` puts (a Node replaced behind a balancer)."""

    def __init__(self, inner: Any, failures: int) -> None:  # noqa: ANN401 - the world's content store
        self.inner, self.failures, self.puts = inner, failures, 0

    async def put(self, data: bytes) -> Any:  # noqa: ANN401
        self.puts += 1
        if self.puts <= self.failures:
            raise JournalUnavailable("connection reset")
        return await self.inner.put(data)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self.inner, name)


async def test_ru4_an_unreachable_content_route_is_retried_like_a_commit_not_a_failed_run() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    flaky = _FlakyContent(world.content, failures=2)
    world.content = cast("Any", flaky)
    done = await world.run(world.kernel(port, journal_retry_s=0))
    assert done.status == "completed"
    assert flaky.puts > 2
    assert world.events("run.failed") == []
