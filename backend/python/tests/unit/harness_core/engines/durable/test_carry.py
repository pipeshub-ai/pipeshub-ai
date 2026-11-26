"""E1b CA-*: engine state carried across the runs of one session (EV2-27)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.controller import RESUME_NOTE
from harness_core.engines.durable.carry import SessionEngineRuns
from harness_core.engines.durable.fold import fold_engine_run
from harness_core.journal.types import TERMINAL_STATUSES, JournalEvent
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

WORKSPACE_NOTE = "\n\nAbout your workspace"


async def finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


def adapter_of(w: EngineWorld) -> ScriptedEngineAdapter:
    assert isinstance(w.adapter, ScriptedEngineAdapter)
    return w.adapter


def user_messages(w: EngineWorld) -> list[str]:
    """What each run's user wrote, as the engine's conversation holds it (a first turn's workspace note aside)."""
    return [m.content.split(WORKSPACE_NOTE, 1)[0] for m in w.gateway.model_calls[-1].messages if m.role == "user"]


async def test_ca1_a_later_run_of_the_same_person_continues_the_engines_conversation() -> None:
    w = EngineWorld()
    w.admit("run-a", "say first answer")
    assert await finish(w, "run-a") == "completed"
    w.admit("run-b", "say second answer")
    assert await finish(w, "run-b") == "completed"
    restored = adapter_of(w).restored
    assert restored[0] is None and restored[1] is not None
    assert user_messages(w) == ["say first answer", "say second answer"]


async def test_ca2_another_persons_run_starts_fresh() -> None:
    w = EngineWorld()
    w.admit("run-a", "say mine")
    assert await finish(w, "run-a") == "completed"
    w.admit("run-b", "say theirs", principal="p_other")
    assert await finish(w, "run-b") == "completed"
    assert adapter_of(w).restored == [None, None]
    assert user_messages(w) == ["say theirs"]


async def test_ca2_a_run_admitted_before_the_earlier_one_completed_starts_fresh() -> None:
    w = EngineWorld()
    w.admit("run-a", "say first")
    w.admit("run-b", "say second")
    assert await finish(w, "run-a") == "completed"
    assert await finish(w, "run-b") == "completed"
    assert adapter_of(w).restored == [None, None]


async def test_ca3_carried_state_that_cannot_be_read_starts_from_the_workspace_flagged() -> None:
    w = EngineWorld()
    w.admit("run-a", "say first")
    assert await finish(w, "run-a") == "completed"
    checkpoint = fold_engine_run(w.journal.events(SESSION), run_id="run-a").checkpoint
    assert checkpoint is not None
    del w.objects._objects[(ORG, checkpoint.content.object_id)]  # pyright: ignore[reportPrivateUsage]
    w.admit("run-b", "say second")
    assert await finish(w, "run-b") == "completed"
    assert adapter_of(w).restored == [None, None]
    assert RESUME_NOTE in w.gateway.model_calls[-1].system


async def test_ca4_a_redriven_first_turn_restores_the_same_carried_state() -> None:
    w = EngineWorld()
    w.admit("run-a", "say first")
    assert await finish(w, "run-a") == "completed"
    w.admit("run-b", "say second")
    assert await w.crash_at("run-b", "engine.turn.after_restore")
    assert await finish(w, "run-b") == "completed"
    restored = adapter_of(w).restored
    assert len(restored) == 3 and restored[1] is not None and restored[1] == restored[2]
    assert RESUME_NOTE not in w.gateway.model_calls[-1].system


# --- the choice itself, as a table over session events ---------------------------------------------------

_TS = "2026-10-01T00:00:00.000Z"


def _event(seq: int, run_id: str, kind: str, data: dict[str, JsonValue]) -> JournalEvent:
    return JournalEvent(
        org_id=ORG, session_id=SESSION, stream_id=SESSION, seq=seq, run_id=run_id, type=kind,
        schema_version=1, critical=True, ts=_TS, writer="node", visibility="owner", data=data,
    )


def _run(seq: int, run_id: str, *, principal: str = "p_u", kind: str | None = "opencode", parent: str | None = None,
         completed: bool = True, state_kind: str | None = None) -> list[JournalEvent]:
    admitted: dict[str, JsonValue] = {"runKind": "interactive", "principalRef": principal}
    if parent is not None:
        admitted["parentRunId"] = parent
    executor: JsonValue = {"kind": "engine", "engine": {"kind": kind}} if kind is not None else {"kind": "loop"}
    events = [
        _event(seq, run_id, "run.admitted", admitted),
        _event(seq + 1, run_id, "run.spec_pinned", {"specDigest": "d", "spec": {"executor": executor}}),
    ]
    if kind is not None:
        events.append(_event(seq + 2, run_id, "engine.checkpointed", {
            "blob": {"objectId": f"o-{run_id}", "contentHash": "h", "sizeBytes": 1},
            "engine": {"kind": state_kind or kind, "version": "1", "binaryDigest": "sha256:" + "0" * 64},
            "schemaVersion": 1, "turnId": "t",
        }))
    if completed:
        events.append(_event(seq + 3, run_id, "run.completed", {"outcome": "answered"}))
    return events


def _carried(events: list[JournalEvent], run_id: str = "me") -> str | None:
    runs = SessionEngineRuns()
    runs.apply(events)
    found = runs.carried(run_id, principal="p_u", engine_kind="opencode")
    return found[0] if found is not None else None


@pytest.mark.parametrize(
    ("earlier", "expected"),
    [
        (_run(1, "a"), "a"),
        (_run(1, "a") + _run(10, "b"), "b"),
        (_run(1, "a", principal="p_other"), None),
        (_run(1, "a") + _run(10, "b", completed=False), "a"),
        (_run(1, "a") + _run(10, "b", kind="codex"), None),
        (_run(1, "a") + _run(10, "b", state_kind="codex"), None),
        (_run(1, "a") + _run(10, "b", kind=None), "a"),
        (_run(1, "a") + _run(10, "child", parent="a"), "a"),
    ],
    ids=["one", "latest", "other-person", "not-completed", "other-kind", "other-state-kind", "loop-run", "child"],
)
def test_ca2_which_state_is_carried(earlier: list[JournalEvent], expected: str | None) -> None:
    me = [_event(50, "me", "run.admitted", {"runKind": "interactive", "principalRef": "p_u"})]
    assert _carried(earlier + me) == expected


def test_ca2_nothing_completed_after_admission_and_nothing_for_a_child() -> None:
    late = [_event(5, "me", "run.admitted", {"runKind": "interactive", "principalRef": "p_u"}), *_run(10, "a")]
    assert _carried(late) is None
    child = [*_run(1, "a"), _event(50, "me", "run.admitted", {"runKind": "child", "principalRef": "p_u", "parentRunId": "p"})]
    assert _carried(child) is None


def _code_task_child(seq: int, run_id: str, parent: str, *, completed: bool = True) -> list[JournalEvent]:
    """CTL-6: a root loop run's `code_task` handoff, an engine child of that turn."""
    return _run(seq, run_id, parent=parent, completed=completed)


@pytest.mark.parametrize(
    ("earlier", "expected"),
    [
        (_run(1, "turn-1", kind=None) + _code_task_child(5, "task-1", "turn-1"), "task-1"),
        (_run(1, "a") + _run(10, "turn-1", kind=None), "a"),
        (_run(1, "a") + _run(10, "turn-1", kind=None) + _code_task_child(15, "task-1", "turn-1"), "task-1"),
        (_run(1, "turn-1", kind=None) + _code_task_child(5, "task-1", "turn-1", completed=False), None),
        (_run(1, "a") + _run(10, "grandchild", parent="sub"), "a"),
    ],
    ids=["previous-handoff", "pre-ctl6-engine-run", "latest-handoff", "unfinished-handoff", "not-a-handoff"],
)
def test_ctl6_a_code_task_child_continues_the_sessions_engine_conversation(
    earlier: list[JournalEvent], expected: str | None
) -> None:
    """C6-8: "now add tests" in a later turn: its handoff starts from the engine state the last one ended with."""
    me = [
        *_run(60, "turn-2", kind=None, completed=False),
        _event(70, "me", "run.admitted", {"runKind": "child", "principalRef": "p_u", "parentRunId": "turn-2"}),
    ]
    assert _carried(earlier + me) == expected


def test_ctl6_another_persons_handoff_is_not_carried() -> None:
    earlier = [*_run(1, "turn-1", kind=None, principal="p_other"), *_run(5, "task-1", parent="turn-1", principal="p_other")]
    me = [
        *_run(60, "turn-2", kind=None, completed=False),
        _event(70, "me", "run.admitted", {"runKind": "child", "principalRef": "p_u", "parentRunId": "turn-2"}),
    ]
    assert _carried(earlier + me) is None
