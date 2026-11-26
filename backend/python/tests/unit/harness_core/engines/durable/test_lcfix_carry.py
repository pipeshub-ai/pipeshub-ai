"""LCFIX: a Coder turn's ``code_task`` child (CTL-6) continues the engine conversation the previous turn's child
ended with. Each child journals on its own stream, so admission pins the state it carries
(``x.pipeshub.engineCarry``); the reference journal plays Node's admission here."""

from __future__ import annotations

from typing import Any

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.durable.carry import pinned_carry
from harness_core.engines.events import EngineStateBlob
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)

from ...durable.support import reply, scripted
from .test_children import (  # pyright: ignore[reportPrivateUsage]
    PROFILE,
    _loop_parent,
    _parent_kernel,
)


def _principal(spec: RunSpec, principal: str) -> RunSpec:
    return spec.model_copy(update={"principal_ref": principal})


async def _turn(w: EngineWorld, run_id: str, goal: str, *, session: str = SESSION, principal: str | None = None) -> str:
    """One Coder turn: a root loop run hands ``goal`` to a code_task child, which runs to completion."""
    spec = _loop_parent() if principal is None else _principal(_loop_parent(), principal)
    w.journal.admit(run_id, org_id=ORG, session_id=session, spec=spec, input_text=goal)
    _, port = scripted(reply("", ("code_task", {"goal": goal})), reply("done"))
    claim = await w.journal.claim(run_id, worker_id="w")
    assert claim is not None
    assert (await _parent_kernel(w, port).run_claimed(claim)).status == "suspended"
    (child,) = w.journal.children_of(run_id)
    assert (await w.attempt(child)).status == "completed"
    claim = await w.journal.claim(run_id, worker_id="w")
    assert claim is not None
    assert (await _parent_kernel(w, port).run_claimed(claim)).status == "completed"
    return child


def _restored(w: EngineWorld) -> list[bool]:
    adapter = w.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    return [state is not None for state in adapter.restored]


def _carry_of(w: EngineWorld, child: str) -> Any:  # noqa: ANN401 - JSON
    spec = next(e for e in w.journal.events(child) if e.type == "run.spec_pinned").data["spec"]
    return spec["extensions"]["x.pipeshub"].get("engineCarry")  # type: ignore[index,union-attr]


async def test_lcfix_the_next_code_task_continues_the_previous_ones_engine_session() -> None:
    w = EngineWorld(approvals={"create": "allow"}, engine_authority=PROFILE)
    first = await _turn(w, "turn-1", "say first answer")
    second = await _turn(w, "turn-2", "say second answer")
    assert _carry_of(w, first) is None
    assert _carry_of(w, second)["runId"] == first
    assert _restored(w) == [False, True]
    messages = [m.content for m in w.gateway.model_calls[-1].messages if m.role == "user"]
    assert any("say first answer" in m for m in messages) and any("say second answer" in m for m in messages)


async def test_lcfix_another_persons_code_task_is_never_carried() -> None:
    w = EngineWorld(approvals={"create": "allow"}, engine_authority=PROFILE)
    _ = await _turn(w, "turn-1", "say mine")
    theirs = await _turn(w, "turn-2", "say theirs", principal="p_other")
    assert _carry_of(w, theirs) is None
    assert _restored(w) == [False, False]


async def test_lcfix_a_forked_conversation_starts_fresh() -> None:
    w = EngineWorld(approvals={"create": "allow"}, engine_authority=PROFILE)
    _ = await _turn(w, "turn-1", "say first")
    forked = await _turn(w, "turn-f", "say in the fork", session="ses-fork")
    assert _carry_of(w, forked) is None
    assert _restored(w) == [False, False]


def _spec_with(carry: object) -> RunSpec:
    raw = _loop_parent().model_dump(mode="json", by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["engineCarry"] = carry
    return validate_contract(RunSpec, raw)


CHECKPOINT = {
    "blob": {"objectId": "obj-1", "contentHash": "h", "sizeBytes": 3},
    "engine": {"kind": "opencode", "version": "1.18.34", "binaryDigest": "sha256:" + "0" * 64},
    "schemaVersion": 1,
    "turnId": "t1",
}


def test_lcfix_a_pinned_carry_of_the_same_engine_is_read_with_its_snapshot() -> None:
    snapshot = {"snapshot": {"objectId": "snap", "contentHash": "h", "sizeBytes": 1}, "turnId": "t1", "reason": "turn_end"}
    found = pinned_carry(_spec_with({"runId": "task-1", "checkpoint": CHECKPOINT, "snapshot": snapshot}), engine_kind="opencode")
    assert found is not None
    run_id, state, snap = found
    assert run_id == "task-1" and isinstance(state, EngineStateBlob) and state.content.object_id == "obj-1"
    assert snap is not None and snap.object_id == "snap"


def test_lcfix_a_pinned_carry_is_refused_for_another_engine_or_when_malformed() -> None:
    assert pinned_carry(_spec_with({"runId": "task-1", "checkpoint": CHECKPOINT}), engine_kind="codex") is None
    assert pinned_carry(_spec_with({"runId": "task-1", "checkpoint": {"blob": {}}}), engine_kind="opencode") is None
    assert pinned_carry(_loop_parent(), engine_kind="opencode") is None
