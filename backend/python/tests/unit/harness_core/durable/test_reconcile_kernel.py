"""KR-1..KR-11, KO-1..KO-4: the durable kernel reconciles unknown effects (P2.9, R-31)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from harness_core.effects.reconcile import (
    ReconcileTiming,
    TimedReconciler,
    current_effect_tag,
)
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.durable import EffectUnknown, ToolExecution
from harness_core.kernel.fold import fold_run
from harness_core.testing import armed
from harness_core.testing.fake_saas import AckLost

from .support import (
    LEASE_MS,
    ORG,
    RUN,
    Crash,
    NoteLookup,
    World,
    reply,
    scripted,
    stepping,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext
    from harness_core.testing.durable import ScriptedModelPort

CRASH = Raise(Crash)
NOTE = "local@default/send_note"
TIMING = ReconcileTiming(tool_timeout_s=60.0, read_after_write=True)
KEY = f"{ORG}:{RUN}:t1.0"


def _world(*, lose_ack: bool = False) -> tuple[World, NoteLookup]:
    world = World()
    world.admit()
    lookup = NoteLookup(world)

    async def send_note(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        payload: dict[str, JsonValue] = dict(args)
        tag = current_effect_tag()
        if tag is not None:
            payload["tag"] = tag
        if lose_ack and not world.saas.effects:
            world.saas.lose_ack_after_commit = True
        try:
            posted = world.saas.call("send_note", payload)
        except AckLost as exc:
            raise EffectUnknown("ack lost") from exc
        finally:
            world.saas.lose_ack_after_commit = False
        return ToolExecution(status="ok", content={"note": posted.receipt_id})

    world.tools.add("send_note", send_note, tags=[("category", "write")])
    return world, lookup


def _kernel(world: World, port: ScriptedModelPort, lookup: NoteLookup | None, **extra: object) -> DurableKernel:
    reconciler = (
        TimedReconciler({NOTE: lookup}, world.clock, stepping(world), retry_s=1.0)
        if lookup is not None
        else None
    )
    return world.kernel(
        port, reconciler=reconciler, renew_interval_s=5.0, sleep=world.clock.sleep, **extra
    )


def _commit_of(world: World, type_: str) -> tuple[str | None, int | None]:
    (event,) = world.events(type_)
    return event.attempt_id, event.fence


def _intent_ts(world: World) -> datetime:
    from datetime import datetime as dt

    return dt.fromisoformat(world.events("tool.intent")[0].ts.replace("Z", "+00:00"))


async def test_kr1_crash_after_the_effect_reconciles_applied_without_a_re_call() -> None:
    world, lookup = _world()
    model, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_effect": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    assert len(world.saas.effects) == 1
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed" and model.remaining == 0
    assert len(world.saas.effects) == 1
    (reconciled,) = world.events("tool.reconciled")
    assert reconciled.data == {
        "invocationId": "t1.0",
        "verdict": "applied",
        "reason": "found",
        "lookups": 1,
        "source": "reconciler",
    }
    assert reconciled.critical is False
    (outcome,) = world.events("tool.outcome")
    assert outcome.seq == reconciled.seq + 1 and outcome.attempt_id == reconciled.attempt_id
    assert outcome.data["status"] == "ok"
    receipt = outcome.data["receipt"]
    assert isinstance(receipt, dict) and receipt["reconciled"] == {"verdict": "applied", "source": "reconciler"}
    result = port.calls[-1].messages[-1]
    assert isinstance(result, ToolMessage) and "send_note-1" in result.text


async def test_kr2_crash_before_the_send_reconciles_not_applied_and_re_calls_once() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    assert world.saas.effects == []
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed"
    assert len(world.saas.effects) == 1
    assert world.saas.effects[0].payload["tag"]
    types = [t for t in world.types() if t.startswith("tool.")]
    assert types == ["tool.intent", "tool.reconciled", "tool.intent", "tool.outcome"]
    assert world.events("tool.reconciled")[0].data["verdict"] == "not_applied"


async def test_kr3_still_unknown_parks_with_the_verdict_in_the_suspension_commit() -> None:
    world, lookup = _world()
    lookup.fail = True
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    parked = await world.run(_kernel(world, port, lookup))
    assert parked.status == "suspended" and parked.reason == "ix.unknown.t1.0"
    assert world.types()[-4:] == ["tool.reconciled", "tool.unknown", "interaction.requested", "run.suspended"]
    reconciled = world.events("tool.reconciled")[0]
    assert (reconciled.data["verdict"], reconciled.data["reason"]) == ("still_unknown", "lookup_failed")
    assert world.saas.effects == []


async def test_kr4_a_crash_during_the_re_call_is_never_re_called_again() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    # The re-call's own intent is durable, then the worker dies before sending.
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    assert world.saas.effects == []
    parked = await world.run(_kernel(world, port, lookup))
    assert parked.status == "suspended"
    assert world.saas.effects == []
    verdicts = [(e.data["verdict"], e.data["reason"]) for e in world.events("tool.reconciled")]
    assert verdicts == [("not_applied", "absent"), ("still_unknown", "recall_spent")]


async def test_kr4_a_re_call_that_landed_before_the_crash_is_found_applied() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    with armed({"worker.tool.after_effect": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed" and len(world.saas.effects) == 1


async def test_kr5_without_a_reconciler_the_journal_is_todays() -> None:
    async def journal(lookup_on: bool) -> list[tuple[str, dict[str, JsonValue]]]:
        world, lookup = _world()
        _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
        with armed({"worker.tool.after_intent": CRASH}):
            await world.crash(world.kernel(port))
        _ = await world.run(world.kernel(port) if not lookup_on else _kernel(world, port, None))
        assert world.saas.effects == []
        return [(e.type, dict(e.data)) for e in world.events() if not e.type.startswith("run.claimed")]

    assert await journal(False) == await journal(True)


async def test_kr6_a_tool_the_reconciler_does_not_cover_takes_todays_path() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("post_message", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    parked = await world.run(_kernel(world, port, lookup))
    assert parked.status == "suspended"
    assert world.events("tool.reconciled") == [] and lookup.calls == []


async def test_kr7_an_idempotent_tool_with_a_hook_is_retried_by_key_not_reconciled() -> None:
    world, lookup = _world()
    reconciler = TimedReconciler({"local@default/create_issue": lookup}, world.clock, stepping(world))
    _, port = scripted(reply("", ("create_issue", {"title": "bug"})), reply("filed"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(world.kernel(port, reconciler=reconciler))
    done = await world.run(world.kernel(port, reconciler=reconciler))
    assert done.status == "completed" and lookup.calls == []
    world.saas.assert_exactly_once(f"{ORG}:{RUN}:t1.0")


@pytest.mark.parametrize("landed", [True, False], ids=["applied", "not_applied"])
async def test_kr8_an_unknown_in_the_same_attempt_is_reconciled_after_the_window(landed: bool) -> None:
    world, lookup = _world(lose_ack=landed)
    if not landed:

        async def refused(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
            if not world.tools.executed[:-1]:
                raise EffectUnknown("connection reset before the request was read")
            posted = world.saas.call("send_note", {**args, "tag": current_effect_tag() or ""})
            return ToolExecution(status="ok", content={"note": posted.receipt_id})

        world.tools.add("send_note", refused, tags=[("category", "write")])
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed"
    assert len(world.saas.effects) == 1
    ready = _intent_ts(world) + timedelta(seconds=TIMING.tool_timeout_s + LEASE_MS / 1000)
    assert lookup.calls and lookup.calls[0] >= ready
    verdict = world.events("tool.reconciled")[0].data["verdict"]
    assert verdict == ("applied" if landed else "not_applied")


async def test_kr9_replay_makes_no_lookup_and_no_commit_and_folds_equal() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_effect": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed"
    calls, seq = len(lookup.calls), world.events()[-1].seq
    events = [e for e in world.events() if e.run_id == RUN]
    first = fold_run(events, run_id=RUN)
    assert fold_run(events, run_id=RUN) == first
    assert first.invocations["t1.0"].state == "completed"
    assert first.invocations["t1.0"].intents == 1
    assert len(lookup.calls) == calls and world.events()[-1].seq == seq


@pytest.mark.parametrize(
    ("point", "verdicts"),
    [
        ("worker.reconcile.before_commit", ["applied"]),
        ("worker.reconcile.after_not_applied", ["not_applied", "not_applied"]),
    ],
    ids=["before_verdict_commit", "after_not_applied_before_recall"],
)
async def test_kr10_a_crash_at_each_reconcile_point_decides_once(point: str, verdicts: list[str]) -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    first_crash = "worker.tool.after_effect" if verdicts == ["applied"] else "worker.tool.after_intent"
    with armed({first_crash: CRASH}):
        await world.crash(_kernel(world, port, lookup))
    with armed({point: CRASH}):
        await world.crash(_kernel(world, port, lookup))
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed"
    assert len(world.saas.effects) == 1
    assert [e.data["verdict"] for e in world.events("tool.reconciled")] == verdicts
    assert len(world.events("tool.intent")) == (1 if verdicts == ["applied"] else 2)


async def test_kr10_no_lookup_after_an_applied_commit() -> None:
    world, lookup = _world()
    # The crashed attempt sampled the second reply; the next samples it again.
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"), reply("sent"))
    with armed({"worker.tool.after_effect": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    with armed({"worker.model.before_response_commit": Arm(CRASH, Nth(1))}):
        await world.crash(_kernel(world, port, lookup))
    looked = len(lookup.calls)
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed" and len(lookup.calls) == looked
    assert len(world.events("tool.reconciled")) == 1


async def test_kr11_a_lease_lost_while_waiting_journals_nothing() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    before = len(world.events())
    # No lease keeper: waiting out the window outlives the lease.
    reconciler = TimedReconciler({NOTE: lookup}, world.clock, stepping(world))
    done = await world.run(world.kernel(port, reconciler=reconciler))
    assert done.status in ("lost", "released")
    assert [e.type for e in world.events()[before:]] == ["run.claimed"]
    assert world.saas.effects == []


async def _parked(world: World, lookup: NoteLookup, port: ScriptedModelPort) -> None:
    lookup.fail = True
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    parked = await world.run(_kernel(world, port, lookup))
    assert parked.status == "suspended"
    lookup.fail = False


async def test_ko1_an_operator_applied_journals_the_outcome_and_never_executes() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    await _parked(world, lookup, port)
    world.journal.resolve_interaction(RUN, "ix.unknown.t1.0", "answered", by="p_admin")
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed" and world.saas.effects == []
    reconciled = world.events("tool.reconciled")[-1]
    assert (reconciled.data["verdict"], reconciled.data["source"]) == ("applied", "operator")
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "ok"


async def test_ko2_an_operator_not_applied_re_calls_once_with_a_fresh_intent() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    await _parked(world, lookup, port)
    world.journal.resolve_interaction(RUN, "ix.unknown.t1.0", "approved", by="p_admin")
    with armed({"worker.tool.after_intent": CRASH}):
        await world.crash(_kernel(world, port, lookup))
    assert len(world.events("tool.intent")) == 2
    parked = await world.run(_kernel(world, port, lookup))
    assert parked.status == "suspended" and world.saas.effects == []
    assert world.events("tool.reconciled")[-1].data["reason"] == "recall_spent"


async def test_ko3_an_operator_abandon_fails_the_run() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    await _parked(world, lookup, port)
    world.journal.resolve_interaction(RUN, "ix.unknown.t1.0", "cancelled", by="p_admin")
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "failed" and done.reason == "effect_abandoned"
    assert world.saas.effects == []
    (failed,) = world.events("run.failed")
    assert failed.data["errorType"] == "effect_abandoned"


async def test_ko4_a_denial_is_todays_refusal() -> None:
    world, lookup = _world()
    _, port = scripted(reply("", ("send_note", {"text": "hi"})), reply("sent"))
    await _parked(world, lookup, port)
    world.journal.resolve_interaction(RUN, "ix.unknown.t1.0", "denied")
    done = await world.run(_kernel(world, port, lookup))
    assert done.status == "completed" and world.saas.effects == []
    (outcome,) = world.events("tool.outcome")
    assert outcome.data["status"] == "error"
