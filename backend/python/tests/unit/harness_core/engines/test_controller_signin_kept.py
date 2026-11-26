"""KS-1..KS-12 (SIGNIN-5): one sign-in per conversation.

A turn on the person's own plan that ends cleanly keeps its signed-in sandbox for the next turn of the same person,
conversation, workspace and engine profile (never anyone else's), until it is idle for the idle timeout, the sign-in
reaches its maximum lifetime, or a revocation; then ``claude auth logout`` and the wipe run as at a turn's end. A turn
whose worker died parks nothing, so the next turn wipes the sandbox and asks the person to sign in again.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.controller import (
    KEPT_LABEL,
    KEPT_LOGOUT_GRACE_S,
    KeptSandboxWatches,
    kept_sandbox_key,
)
from harness_core.engines.events import SignInProgress
from harness_core.engines.signin import PlanSignIn, SignInKeep, SignInRefused
from harness_core.engines.types import EngineMode
from harness_core.sandbox.errors import LeaseFenced
from harness_core.sandbox.types import FAREWELL_LABEL, LIFETIME_LABEL, LeaseRef
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.types import ObjectScope, WorkspaceFile
from tests.unit.harness_core.engines.test_controller import (
    ORG,
    RUN,
    SCOPE,
    SESSION,
    task,
)
from tests.unit.harness_core.engines.test_controller_signin import (
    HOSTS,
    OWN,
    FakeRelay,
    Idle,
    SigningAdapter,
    _adapter,  # pyright: ignore[reportPrivateUsage]
    _world,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineRunContext
    from tests.unit.harness_core.engines.test_controller import World

OTHER = PlanSignIn(user_id="user-2", account_id="users/user-2/claude-plan")


def _run(world: World, n: int, **changes: object) -> EngineRunContext:
    """The conversation's n-th run (its own run id, as each new message is)."""
    return world.run(run_id=f"{RUN}-{n}", attempt_id=f"{RUN}-{n}.a1", **{"own_login": OWN, **changes})


async def _turn(world: World, n: int, **changes: object) -> None:
    outcome = await world.controller.run_turn(_run(world, n, **changes), task(0, "say done"))
    assert outcome.reason == "completed"


def _lines(world: World) -> list[tuple[str, str | None]]:
    return [(e.state, e.plan) for _, e in world.sink.events if isinstance(e, SignInProgress)]


async def _until(predicate: object, timeout: float = 5.0) -> None:
    async def wait() -> None:
        # The fakes signal nothing: polling their state is the test.
        while not predicate():  # type: ignore[operator]  # noqa: ASYNC110
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), timeout)


def _shared(world: World) -> World:
    """Every controller the world makes shares one set of watches, as a worker's attempts do."""
    watches = KeptSandboxWatches()
    base = world.make_controller

    def make(key: bytes = b"k" * 32) -> object:
        controller = base(key)
        controller._parked = watches  # pyright: ignore[reportPrivateUsage]
        return controller

    world.make_controller = make  # type: ignore[method-assign]
    world.controller = make()  # type: ignore[assignment]
    return world


async def test_ks1_the_next_message_of_the_conversation_reuses_the_sandbox_and_its_sign_in() -> None:
    relay = FakeRelay()
    world = _shared(_world(relay=relay))
    await _turn(world, 1)
    world.controller = world.make_controller()  # the next message's attempt gets its own controller
    await _turn(world, 2)
    adapter = _adapter(world)
    assert adapter.calls == [
        "own_login", "sign_out", "sign_in", "open", "park",
        "own_login", "signed_in", "open", "park",
    ]
    assert len(relay.attempts) == 1  # the person was asked once
    assert _lines(world) == [("requested", None), ("signed_in", "team"), ("signed_in", "team")]
    [(first, key1), (second, key2)] = world.broker.acquired
    assert key1 == key2 == kept_sandbox_key(_run(world, 1))
    assert [c[0] for c in world.broker.calls if c[0] in ("acquire", "rebind", "destroy")] == ["acquire", "rebind"]
    # The second turn's own tool route reaches the sandbox's egress, not the first turn's.
    assert first.egress.token_routes != second.egress.token_routes
    [sandbox] = world.broker.sandboxes.values()
    assert sandbox.spec.egress == second.egress


@pytest.mark.parametrize(
    "other",
    [
        {"own_login": OTHER},
        {"session_id": "ses-other"},
        {"workspace": "ws:fork-1"},
        {"profile_mode": EngineMode.PLAN},
    ],
    ids=["another-person", "another-conversation", "a-fork", "another-agent-profile"],
)
async def test_ks2_another_person_conversation_workspace_or_profile_never_gets_it(other: dict[str, object]) -> None:
    relay = FakeRelay()
    world = _shared(_world(relay=relay))
    await _turn(world, 1)
    changes = dict(other)
    if "profile_mode" in changes:
        changes["profile"] = dataclasses.replace(world.run().profile, mode=changes.pop("profile_mode"))
    run = _run(world, 2, **changes)
    assert kept_sandbox_key(run) != kept_sandbox_key(_run(world, 1))
    world.controller = world.make_controller()
    _ = await world.controller.run_turn(run, task(0, "say done"))
    assert len(relay.attempts) == 2  # asked again
    assert len({key for _, key in world.broker.acquired}) == 2
    assert len(world.broker.sandboxes) == 2  # two sandboxes, each kept for its own owner only


async def test_ks3_idle_timeout_logs_out_and_wipes_then_the_next_turn_asks_again() -> None:
    relay, idle = FakeRelay(), Idle()
    world = _shared(_world(relay=relay, idle=idle))
    await _turn(world, 1)
    keep = world.config.signin_keep
    await _until(lambda: idle.asked)
    assert idle.asked == [keep.idle_s]
    assert relay.ttls == [keep.idle_s + KEPT_LOGOUT_GRACE_S]
    idle.end()
    await _until(lambda: not world.broker.sandboxes)
    adapter = _adapter(world)
    assert adapter.calls[-1] == "sign_out" and adapter.logged_in == set()
    assert relay.kept == {}
    idle.ended.clear()
    world.controller = world.make_controller()
    await _turn(world, 2)
    assert len(relay.attempts) == 2


async def test_ks4_the_maximum_lifetime_ends_it_however_busy_the_conversation() -> None:
    relay, idle = FakeRelay(), Idle()
    keep = SignInKeep(idle_s=30 * 60, max_s=60 * 60)
    world = _shared(_world(relay=relay, idle=idle, signin_keep=keep))
    await _turn(world, 1)
    world.clock.advance(50 * 60)
    world.controller = world.make_controller()
    await _turn(world, 2)
    # Ten minutes are left of the hour: kept that long, not the idle half hour.
    await _until(lambda: idle.asked and idle.asked[-1] < 1_000)
    assert idle.asked[-1] == pytest.approx(10 * 60, abs=1)
    world.clock.advance(11 * 60)
    world.controller = world.make_controller()
    await _turn(world, 3)
    # Past the hour: not used, logged out and wiped, and the person signs in again.
    assert len(relay.attempts) == 2
    assert _adapter(world).calls[-5:] == ["own_login", "sign_out", "sign_in", "open", "park"]


async def test_ks5_a_revocation_while_kept_ends_it_at_once() -> None:
    relay, idle = FakeRelay(), Idle()
    world = _shared(_world(relay=relay, idle=idle))
    await _turn(world, 1)
    relay.revoke.set()  # the person disconnected, an admin signed them out, the org's switch, erasure or sharing
    await _until(lambda: not world.broker.sandboxes)
    assert _adapter(world).calls[-1] == "sign_out"
    assert relay.kept == {}
    assert idle.asked  # it was waiting for the idle time, not past it


async def test_ks6_a_revocation_its_worker_missed_ends_it_at_the_next_turn() -> None:
    relay = FakeRelay()
    world = _world(relay=relay)
    await _turn(world, 1)
    # The worker that kept it went away (its watch with it); the revocation was remembered, never heard.
    await world.controller._parked.aclose()  # pyright: ignore[reportPrivateUsage]
    relay.revoked_at = world.clock.now().timestamp()
    world.controller = world.make_controller()
    await _turn(world, 2)
    adapter = _adapter(world)
    assert adapter.calls[-5:] == ["own_login", "sign_out", "sign_in", "open", "park"]
    assert len(relay.attempts) == 2


async def test_ks7_a_crashed_turn_leaves_nothing_kept_the_next_turn_wipes_and_asks_again() -> None:
    relay = FakeRelay()
    world = _shared(_world(relay=relay))
    await _turn(world, 1)
    key = kept_sandbox_key(_run(world, 1))
    # A second turn's worker took the sandbox and its record, signed in, and died before its turn ended.
    taken = relay.kept.pop(key)
    await world.controller._parked.stop(key)  # pyright: ignore[reportPrivateUsage]
    [(spec, _)] = world.broker.acquired
    crashed = await world.broker.acquire(spec, lease=LeaseRef("dead-attempt", taken.fence + 1), idempotency_key=key)
    assert crashed.id == taken.sandbox_id
    world.clock.advance(60)  # its write lease lapsed; the next turn's is later
    world.controller = world.make_controller()
    await _turn(world, 3)
    adapter = _adapter(world)
    # Same sandbox, taken over by the later writer; whatever the dead turn left is logged out and wiped first.
    assert adapter.calls[-5:] == ["own_login", "sign_out", "sign_in", "open", "park"]
    assert len(relay.attempts) == 2
    assert {key for _, key in world.broker.acquired} == {key}


async def test_ks8_a_stale_holder_never_takes_a_sandbox_a_later_turn_holds() -> None:
    relay = FakeRelay()
    world = _shared(_world(relay=relay))
    await _turn(world, 1)
    key = kept_sandbox_key(_run(world, 1))
    [(spec, _)] = world.broker.acquired
    later = await world.broker.acquire(spec, lease=LeaseRef("later", 10**15), idempotency_key=key)
    with pytest.raises(LeaseFenced):
        _ = await world.broker.acquire(spec, lease=LeaseRef("stale", 1), idempotency_key=key)
    await world.broker.release(later)


async def test_ks9_a_failed_turn_is_not_kept() -> None:
    relay = FakeRelay()
    world = _world(relay=relay)
    adapter = _adapter(world)

    async def broken(*_a: object, **_k: object) -> object:
        raise RuntimeError("engine gone")

    adapter.open = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        _ = await world.controller.run_turn(_run(world, 1), task(0, "say done"))
    assert adapter.calls[-1] == "sign_out"
    assert world.broker.sandboxes == {} and relay.kept == {}


async def test_ks10_processes_left_behind_that_cannot_be_ended_keep_nothing() -> None:
    relay = FakeRelay()
    world = _world(relay=relay)
    adapter = _adapter(world)
    adapter.park_ok = False
    await _turn(world, 1)
    assert adapter.calls[-2:] == ["park", "sign_out"]
    assert world.broker.sandboxes == {} and relay.kept == {}


async def test_ks11_the_workspace_is_materialized_afresh_on_a_kept_sandbox() -> None:
    relay = FakeRelay()
    world = _shared(_world(relay=relay))
    await world.seed({"a.txt": b"one"})
    await _turn(world, 1)
    # Another writer changed the workspace between the turns (a gateway run, a file upload).
    store = world.store
    assert store is not None
    head = await store.head(ObjectScope(ORG), f"ws:{SESSION}")
    entry = WorkspaceFile("a.txt", await world.objects.put(SCOPE, b"two"))
    _ = await store.commit(ObjectScope(ORG), f"ws:{SESSION}", base_revision=head.revision,
                           changes=ChangeSet.of(entry), reason="seed")
    world.controller = world.make_controller()
    await _turn(world, 2)
    [sandbox] = world.broker.sandboxes.values()
    assert sandbox.files["/workspace/a.txt"].data == b"two"


async def test_ks12_a_kept_sandbox_has_no_run_id_so_a_finished_runs_reap_spares_it() -> None:
    relay = FakeRelay()
    world = _shared(_world(relay=relay))
    await _turn(world, 1)
    [sandbox] = world.broker.sandboxes.values()
    labels = sandbox.spec.labels
    assert "runId" not in labels and "attemptId" not in labels
    assert labels[KEPT_LABEL] == "1" and int(labels[LIFETIME_LABEL]) > world.config.signin_keep.max_s
    assert await world.broker.reap(labels={"orgId": ORG, "runId": f"{RUN}-1"}, older_than_s=0) == 0
    # The kernel's crash-loop reap names it.
    assert await world.broker.reap(labels={"orgId": ORG, "sessionId": SESSION, KEPT_LABEL: "1"}, older_than_s=0) == 1


async def test_gaps2_a_kept_sandbox_names_its_engines_farewell_for_the_broker_and_a_turns_own_sandbox_none() -> None:
    """GAPS-2: the broker logs a kept sandbox out itself when it ends one no worker ended (expiry, reap, orphan): the
    sandbox names its engine's farewell. A plain turn's sandbox (no own login) is ended by its turn and names none."""
    world = _shared(_world(relay=FakeRelay()))
    await _turn(world, 1)
    [sandbox] = world.broker.sandboxes.values()
    assert sandbox.spec.labels[FAREWELL_LABEL] == _run(world, 1).profile.engine.kind

    plain = _world(relay=FakeRelay())
    outcome = await plain.controller.run_turn(plain.run(run_id=f"{RUN}-p", attempt_id=f"{RUN}-p.a1"), task(0, "say done"))
    assert outcome.reason == "completed"
    assert plain.broker.acquired
    assert all(FAREWELL_LABEL not in spec.labels for spec, _ in plain.broker.acquired)


async def test_ks15_compact_on_a_kept_signed_in_sandbox_never_asks_to_sign_in_again() -> None:
    """SLASH2 C2 x SIGNIN-5: the next message's ``/compact`` runs in the same signed-in sandbox, after the kept login is
    found and before the turn; the wipe and logout paths never open a session, so they never compact."""
    from harness_core.journal.types import CompactAsk

    relay, idle = FakeRelay(), Idle()
    world = _shared(_world(relay=relay, idle=idle))
    first = await world.controller.run_turn(_run(world, 1), task(0, "say done"))
    assert first.checkpoint is not None
    world.controller = world.make_controller()
    second = await world.controller.run_turn(
        _run(world, 2, restore=first.checkpoint, compact=CompactAsk("keep the numbers")), task(0, "say done")
    )
    adapter = _adapter(world)
    assert second.reason == "completed" and second.restored == "state"
    assert second.compacted is not None and second.compacted.outcome == "compacted"
    assert [s.compacted for s in adapter.sessions] == [[], ["keep the numbers"]]
    assert len(relay.attempts) == 1  # compaction asked nobody to sign in again
    assert adapter.calls[-4:] == ["own_login", "signed_in", "open", "park"]
    assert len({key for _, key in world.broker.acquired}) == 1
    # The idle end logs out and wipes without opening a session, so without compacting.
    idle.end()
    await _until(lambda: not world.broker.sandboxes)
    assert adapter.calls[-1] == "sign_out" and len(adapter.sessions) == 2


def test_ks13_the_keep_is_bounded() -> None:
    for idle, most in ((60, 3600), (3 * 3600, 3600), (1800, 600), (1800, 48 * 3600)):
        with pytest.raises(ValueError, match="must be"):
            _ = SignInKeep(idle_s=idle, max_s=most)


async def test_ks14_ended_mid_turn_on_a_kept_sandbox_is_wiped_and_not_kept_again() -> None:
    relay = FakeRelay()
    world = _shared(_world(relay=relay))
    await _turn(world, 1)
    adapter = _adapter(world)
    adapter.hold = True
    world.controller = world.make_controller()
    running = asyncio.ensure_future(world.controller.run_turn(_run(world, 2), task(0, "say done")))
    await _until(lambda: adapter.calls.count("open") == 2)
    relay.revoke.set()
    with pytest.raises(SignInRefused) as refused:
        _ = await asyncio.wait_for(running, 5)
    assert refused.value.reason_code == "account_disconnected"
    assert adapter.calls[-1] == "sign_out"
    assert world.broker.sandboxes == {} and relay.kept == {}


__all__ = ["HOSTS", "SigningAdapter"]
