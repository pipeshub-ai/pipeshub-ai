"""QA24-1, QA24-2 (TGW-19): one recovery table over effect class × crash position, and the lost acknowledgement.

Each row crashes a worker at one position of one call, lets the lease lapse and runs the next attempt to the end.
The journal at the crash is checked to be the position the row names, so a moved failpoint cannot make a row pass
by testing another position. The fake SaaS is the effect recorder: a write class never records two effects for one
call. ``read`` and ``workspace`` calls have no intent of their own: the batch commits a read's intent with its
outcome, so "after intent" is not a state their journal can be in (asserted, not skipped).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pytest

from harness_core.contracts.generated.enums import EFFECT_CLASSES
from harness_core.effects.reconcile import TimedReconciler, current_effect_tag
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.durable import ToolExecution
from harness_core.testing import armed

from .support import ORG, RUN, Crash, NoteLookup, World, reply, scripted, stepping

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.failpoints import Action
    from harness_core.kernel.durable import DurableKernel, ToolContext
    from harness_core.testing.durable import ScriptedModelPort

CRASH = Raise(Crash)
UNKNOWN = "ix.unknown.t1.0"

Position = Literal["before_intent", "after_intent", "after_execute", "after_outcome"]
Recovery = Literal["re_run", "key_dedupe", "reconcile_applied", "reconcile_recall", "parked", "done"]


@dataclass(frozen=True)
class EffectClass:
    name: str
    tool: str
    # The SaaS operation the tool commits; ``None`` for a class that changes nothing outside the run.
    op: str | None
    writes: bool


READ = EffectClass("read", "lookup", None, writes=False)
WORKSPACE = EffectClass("workspace", "touch_workspace", None, writes=False)
IDEMPOTENT = EffectClass("idempotent_write", "create_issue", "create_issue", writes=True)
RECONCILABLE = EffectClass("reconcilable_write", "send_note", "send_note", writes=True)
EXTERNAL = EffectClass("external_write", "post_message", "post_message", writes=True)
EXECUTE = EffectClass("execute", "run_job", "run_job", writes=True)
CLASSES = (READ, WORKSPACE, IDEMPOTENT, RECONCILABLE, EXTERNAL, EXECUTE)


def _world() -> World:
    world = World()
    world.admit()

    async def touch_workspace(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"touched": args.get("path")})

    async def send_note(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        note = world.call_saas("send_note", {**args, "tag": current_effect_tag() or ""}, None)
        return ToolExecution(status="ok", content={"note": note.receipt_id})

    async def run_job(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        job = world.call_saas("run_job", dict(args), None)
        return ToolExecution(status="ok", content={"job": job.receipt_id})

    world.tools.add("touch_workspace", touch_workspace, tags=[("effect", "workspace")])
    world.tools.add("send_note", send_note, tags=[("category", "write")])
    world.tools.add("run_job", run_job, tags=[("category", "execute")])
    return world


def _kernel(world: World, port: ScriptedModelPort, lookup: NoteLookup) -> DurableKernel:
    reconciler = TimedReconciler(
        {"local@default/send_note": lookup}, world.clock, stepping(world), retry_s=1.0
    )
    return world.kernel(port, reconciler=reconciler, renew_interval_s=5.0, sleep=world.clock.sleep)


def _failpoint(cls: EffectClass, position: Position) -> tuple[str, Action | Arm]:
    match position:
        case "before_intent":
            return "worker.model.after_response_commit", CRASH
        case "after_intent":
            return "worker.tool.after_intent", CRASH
        case "after_execute":
            return ("worker.tool.after_effect" if cls.writes else "worker.tool.before_outcome_commit"), CRASH
        case "after_outcome":
            # The next turn's response commit: the call's outcome is durable before the model is asked again.
            return "worker.model.before_response_commit", Arm(CRASH, Nth(2))


@dataclass(frozen=True)
class Row:
    cls: EffectClass
    position: Position
    recovery: Recovery


def _recovery(cls: EffectClass, position: Position) -> Recovery:
    """The documented outcome (TEST-STRATEGY §2.4, ARCHITECTURE B4), written out per class, not derived from code."""
    if position == "before_intent":
        return "re_run"
    if position == "after_outcome":
        return "done"
    if not cls.writes:
        return "re_run"
    if cls is IDEMPOTENT:
        return "key_dedupe"
    if cls is RECONCILABLE:
        return "reconcile_recall" if position == "after_intent" else "reconcile_applied"
    return "parked"


ROWS = [
    Row(cls, position, _recovery(cls, position))
    for cls in CLASSES
    for position in ("before_intent", "after_intent", "after_execute", "after_outcome")
    if cls.writes or position != "after_intent"
]


def _executions(world: World, cls: EffectClass) -> int:
    return sum(1 for name, _ in world.tools.executed if name == cls.tool)


def _effects(world: World, cls: EffectClass) -> int:
    return len(world.saas.effects_for(cls.op)) if cls.op else 0


def _assert_at(world: World, cls: EffectClass, position: Position) -> None:
    """The journal and the SaaS are in the state the position names."""
    intents = len(world.events("tool.intent"))
    outcomes = len(world.events("tool.outcome"))
    executed = _executions(world, cls)
    expected = {
        "before_intent": (0, 0, 0),
        "after_intent": (1, 0, 0),
        "after_execute": (1 if cls.writes else 0, 0, 1),
        "after_outcome": (1, 1, 1),
    }[position]
    assert (intents, outcomes, executed) == expected, f"{cls.name} crashed {position}"
    if cls.writes:
        assert _effects(world, cls) == executed


@pytest.mark.parametrize("row", ROWS, ids=[f"{r.cls.name}-{r.position}" for r in ROWS])
async def test_qa24_1_recovery_by_effect_class_and_crash_position(row: Row) -> None:
    cls = row.cls
    world = _world()
    lookup = NoteLookup(world)
    _, port = scripted(reply("", (cls.tool, {"q": "x"})), reply("done"), reply("done"))
    point, action = _failpoint(cls, row.position)
    with armed({point: action}):
        await world.crash(_kernel(world, port, lookup))
    _assert_at(world, cls, row.position)
    before = _executions(world, cls)

    done = await world.run(_kernel(world, port, lookup))

    reruns = _executions(world, cls) - before
    verdicts = [e.data["verdict"] for e in world.events("tool.reconciled")]
    match row.recovery:
        case "re_run":
            assert done.status == "completed" and reruns == 1
            assert len(world.events("tool.outcome")) == 1
        case "done":
            assert done.status == "completed" and reruns == 0
        case "key_dedupe":
            assert done.status == "completed" and reruns == 1
            _ = world.saas.assert_exactly_once(f"{ORG}:{RUN}:t1.0")
        case "reconcile_recall":
            assert done.status == "completed" and reruns == 1 and verdicts == ["not_applied"]
        case "reconcile_applied":
            assert done.status == "completed" and reruns == 0 and verdicts == ["applied"]
            assert world.events("tool.outcome")[0].data["status"] == "ok"
        case "parked":
            assert (done.status, done.reason) == ("suspended", UNKNOWN) and reruns == 0
            assert UNKNOWN in world.journal.open_interactions(RUN)
            assert world.types()[-3:] == ["tool.unknown", "interaction.requested", "run.suspended"]
    if cls.writes:
        # Parked before the send: nothing at the provider until a person decides.
        sent = 0 if (row.recovery, row.position) == ("parked", "after_intent") else 1
        assert _effects(world, cls) == sent, "one call, at most one effect at the provider"
    if row.recovery not in ("reconcile_recall", "reconcile_applied"):
        assert verdicts == [], "only a hooked tool is looked up"


def test_qa24_1_the_table_covers_every_effect_class_and_position() -> None:
    world = _world()
    described = {c.name: world.tools.describe(c.tool) for c in CLASSES}
    assert {e.effect for e in described.values() if e is not None} == set(EFFECT_CLASSES)
    writes = {name: e.idempotent for name, e in described.items() if e is not None and e.effect == "write"}
    assert writes == {"idempotent_write": True, "reconcilable_write": False, "external_write": False}
    for cls in CLASSES:
        positions = {r.position for r in ROWS if r.cls is cls}
        assert positions == {"before_intent", "after_execute", "after_outcome"} | (
            {"after_intent"} if cls.writes else set()
        ), cls.name


@pytest.mark.parametrize("cls", [READ, WORKSPACE], ids=["read", "workspace"])
async def test_qa24_1_a_read_intent_is_never_journaled_without_its_outcome(cls: EffectClass) -> None:
    world = _world()
    _, port = scripted(reply("", (cls.tool, {"q": "x"})), reply("done"))
    with armed({"worker.tool.before_outcome_commit": CRASH}):
        await world.crash(_kernel(world, port, NoteLookup(world)))
    assert _executions(world, cls) == 1
    assert world.events("tool.intent") == [] and world.events("tool.outcome") == []


LOST_ACK: list[tuple[EffectClass, Recovery]] = [
    (IDEMPOTENT, "key_dedupe"),
    (RECONCILABLE, "reconcile_applied"),
    (EXTERNAL, "parked"),
    (EXECUTE, "parked"),
]


@pytest.mark.parametrize(("cls", "recovery"), LOST_ACK, ids=[c.name for c, _ in LOST_ACK])
async def test_qa24_2_a_lost_acknowledgement_is_never_sent_again_blind(cls: EffectClass, recovery: Recovery) -> None:
    world = _world()
    world.lose_ack_calls = 1
    lookup = NoteLookup(world)
    _, port = scripted(reply("", (cls.tool, {"q": "x"})), reply("done"))

    done = await world.run(_kernel(world, port, lookup))

    assert _effects(world, cls) == 1
    match recovery:
        case "key_dedupe":
            assert done.status == "completed" and _executions(world, cls) == 2
            _ = world.saas.assert_exactly_once(f"{ORG}:{RUN}:t1.0")
        case "reconcile_applied":
            assert done.status == "completed" and _executions(world, cls) == 1
            assert [e.data["verdict"] for e in world.events("tool.reconciled")] == ["applied"]
        case _:
            assert (done.status, done.reason) == ("suspended", UNKNOWN) and _executions(world, cls) == 1
            assert world.events("tool.outcome") == []
