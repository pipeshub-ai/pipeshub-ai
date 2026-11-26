"""K-13 and the fold spec (REPORT 6.1.6): pure, total, versioned, fail closed."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness_core.contracts.generated.event_catalog import EVENT_MODELS
from harness_core.journal import JournalEvent
from harness_core.kernel.fold import (
    FOLD_VERSION,
    FoldIncompatible,
    RunState,
    checkpoint_is_valid,
    fold_run,
    fold_session,
    make_checkpoint,
    restore_checkpoint,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

REF = {"ref": "cas:x", "hash": "sha256:" + "a" * 64, "size": 1}
DIGEST = "sha256:" + "b" * 64
LABELS: dict[str, JsonValue] = {"integrity": "trusted"}
SINK: dict[str, JsonValue] = {"egress": "external", "argsLeaveBoundary": True}


class Log:
    def __init__(self, stream_id: str = "ses1") -> None:
        self.stream_id = stream_id
        self.events: list[JournalEvent] = []

    def add(
        self,
        type_: str,
        data: dict[str, JsonValue] | None = None,
        *,
        run_id: str | None = "run1",
        critical: bool = True,
        writer: str = "worker",
        attempt_id: str | None = "run1.a1",
        schema_version: int = 1,
    ) -> JournalEvent:
        event = JournalEvent.model_validate(
            {
                "org_id": "org1",
                "session_id": "ses1",
                "stream_id": self.stream_id,
                "seq": len(self.events) + 1,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "fence": 1,
                "type": type_,
                "schema_version": schema_version,
                "critical": critical,
                "ts": "2026-01-01T00:00:00.000Z",
                "writer": writer,
                "data": data or {},
            }
        )
        self.events.append(event)
        return event

    def admit_and_claim(self, run_id: str = "run1") -> None:
        self.add(
            "run.admitted",
            {"runKind": "interactive", "principalRef": "p_u"},
            run_id=run_id,
            writer="node",
        )
        self.add(
            "run.claimed",
            {"attempt": 1, "fence": 1, "leaseMs": 30000},
            run_id=run_id,
            writer="node",
        )

    def response(self, *calls: str, run_id: str = "run1") -> None:
        self.add(
            "model.response",
            {
                "messageRef": REF,
                "toolCalls": [
                    {"callId": c, "modelName": "tool", "argsDigest": DIGEST}
                    for c in calls
                ],
                "usage": {"inputTokens": 10, "outputTokens": 2},
                "provider": "scripted",
                "transport": "scripted",
            },
            run_id=run_id,
        )

    def outcome(self, inv: str, status: str = "ok", run_id: str = "run1") -> None:
        self.add(
            "tool.outcome",
            {"invocationId": inv, "status": status, "resultRef": REF, "labels": LABELS},
            run_id=run_id,
        )

    def intent(self, inv: str, effect: str = "write") -> None:
        self.add(
            "tool.intent",
            {
                "invocationId": inv,
                "toolId": "local@default/tool",
                "argsDigest": DIGEST,
                "effect": effect,
                "sink": SINK,
            },
        )

    def fold(self) -> RunState:
        return fold_run(self.events, run_id="run1")


def test_cursor_walks_a_two_turn_run() -> None:
    log = Log()
    log.admit_and_claim()
    assert log.fold().cursor.next_action == "call_model"
    log.add("turn.started", {"turn": 1})
    assert (log.fold().cursor.next_action, log.fold().cursor.turn) == ("call_model", 1)
    log.response("c0", "c1")
    cursor = log.fold().cursor
    assert (cursor.next_action, cursor.pending) == (
        "dispatch_pending",
        ["t1.0", "t1.1"],
    )
    log.outcome("t1.0")
    assert log.fold().cursor.pending == ["t1.1"]
    log.outcome("t1.1")
    assert (log.fold().cursor.next_action, log.fold().cursor.turn) == ("call_model", 2)
    log.add("turn.started", {"turn": 2})
    assert log.fold().cursor.next_action == "call_model"
    log.response()
    assert log.fold().cursor.next_action == "assemble"
    log.add("run.completed", {"outcome": "answered"}, writer="node")
    state = log.fold()
    assert state.cursor.next_action == "done"
    assert state.consumed.model_responses == 2 and state.consumed.input_tokens == 20


def test_intent_without_outcome_records_the_attempt() -> None:
    log = Log()
    log.admit_and_claim()
    log.add("turn.started", {"turn": 1})
    log.response("c0")
    log.intent("t1.0")
    inv = log.fold().invocations["t1.0"]
    assert (inv.state, inv.effect, inv.intent_attempt_id) == (
        "intent",
        "write",
        "run1.a1",
    )
    assert log.fold().cursor.next_action == "dispatch_pending"


def test_approval_suspends_then_resumes_only_the_waiting_call() -> None:
    log = Log()
    log.admit_and_claim()
    log.add("turn.started", {"turn": 1})
    log.response("r0", "r1", "w2")
    log.outcome("t1.0")
    ask = {
        "interactionId": "ix.approve.t1.2",
        "kind": "approval",
        "bindingDigest": DIGEST,
    }
    log.add("interaction.requested", ask)
    assert log.fold().cursor.next_action == "await_barrier"
    log.outcome("t1.1")
    log.add(
        "run.suspended",
        {"reason": "approval", "interactionId": "ix.approve.t1.2"},
        writer="node",
    )
    assert log.fold().cursor.next_action == "suspended"
    resolved = {
        "interactionId": "ix.approve.t1.2",
        "decision": "approved",
        "by": "p_u",
        "channel": "web",
    }
    log.add("interaction.resolved", resolved, writer="node")
    log.add("run.resumed", {"cause": "resolved"}, writer="node")
    state = log.fold()
    assert (state.cursor.next_action, state.cursor.pending) == (
        "dispatch_pending",
        ["t1.2"],
    )
    assert state.invocations["t1.2"].state == "approved"


def test_unknown_effect_waits_for_a_human() -> None:
    log = Log()
    log.admit_and_claim()
    log.add("turn.started", {"turn": 1})
    log.response("w0")
    log.intent("t1.0")
    log.add("tool.unknown", {"invocationId": "t1.0", "reason": "ack lost"})
    log.add(
        "interaction.requested",
        {
            "interactionId": "ix.unknown.t1.0",
            "kind": "approval",
            "bindingDigest": DIGEST,
        },
    )
    log.add(
        "run.suspended",
        {"reason": "approval", "interactionId": "ix.unknown.t1.0"},
        writer="node",
    )
    assert log.fold().invocations["t1.0"].state == "unknown"
    resolved = {
        "interactionId": "ix.unknown.t1.0",
        "decision": "denied",
        "by": "p_u",
        "channel": "web",
    }
    log.add("interaction.resolved", resolved, writer="node")
    log.add("run.resumed", {"cause": "resolved"}, writer="node")
    assert log.fold().invocations["t1.0"].state == "rejected"


def test_unknown_critical_event_fails_closed_and_non_critical_is_ignored() -> None:
    log = Log()
    log.admit_and_claim()
    log.add("x.acme.telemetry.ping", {"n": 1}, critical=False)
    state = log.fold()
    assert state.ignored_events == 1 and state.seq == 3
    log.add("tool.teleported", {}, critical=True)
    with pytest.raises(FoldIncompatible) as info:
        _ = log.fold()
    assert info.value.event_type == "tool.teleported" and info.value.seq == 4


@pytest.mark.parametrize(
    ("data", "schema_version"),
    [({"turn": 1}, 2), ({"turn": "one"}, 1)],
    ids=["newer-schema-version", "payload-fails-schema"],
)
def test_known_type_this_build_cannot_read_fails_closed(
    data: dict[str, JsonValue], schema_version: int
) -> None:
    log = Log()
    log.add("turn.started", data, schema_version=schema_version)
    with pytest.raises(FoldIncompatible):
        _ = log.fold()
    lenient = Log()
    lenient.add("turn.started", data, schema_version=schema_version, critical=False)
    assert lenient.fold().ignored_events == 1


def test_fold_is_pure_and_incremental() -> None:
    log = Log()
    log.admit_and_claim()
    log.add("turn.started", {"turn": 1})
    log.response("c0")
    head, tail = log.events[:3], log.events[3:]
    partial = fold_run(head, run_id="run1")
    snapshot = partial.model_dump()
    assert fold_run(tail, run_id="run1", state=partial) == fold_run(
        log.events, run_id="run1"
    )
    assert partial.model_dump() == snapshot
    assert fold_run(log.events, run_id="run1") == fold_run(log.events, run_id="run1")


def test_slots_keep_the_latest_value_per_key() -> None:
    log = Log()
    log.add("loop.state_saved", {"slots": {"pre_run.goal": "a", "phase": "plan"}})
    log.add("loop.state_saved", {"slots": {"phase": "execute"}})
    assert log.fold().slots == {"pre_run.goal": "a", "phase": "execute"}


def test_other_runs_in_the_session_do_not_leak_into_the_run() -> None:
    log = Log()
    log.admit_and_claim("run0")
    log.add("turn.started", {"turn": 1}, run_id="run0")
    log.response("c0", run_id="run0")
    log.admit_and_claim()
    state = log.fold()
    assert state.responses == [] and state.cursor.next_action == "call_model"
    assert state.seq == len(log.events)


def test_session_fold_orders_results_by_the_response_and_tracks_session_state() -> None:
    log = Log()
    log.admit_and_claim()
    log.add("turn.started", {"turn": 1})
    log.add("message.completed", {"role": "user", "textRef": REF, "isFinal": False})
    log.response("c0", "c1")
    log.outcome("t1.1")
    log.outcome("t1.0")
    log.add(
        "context.compacted", {"range": {"fromSeq": 1, "toSeq": 2}, "summaryRef": REF}
    )
    log.add("workspace.committed", {"workspaceId": "ws1", "revision": 3, "changed": []})
    log.add(
        "task.created",
        {"taskId": "task1", "title": "T", "status": "pending", "deps": []},
    )
    log.add(
        "interaction.requested",
        {"interactionId": "ix1", "kind": "question", "bindingDigest": DIGEST},
    )
    session = fold_session(log.events, stream_id="ses1")
    user, assistant = session.entries
    assert user.role == "user" and assistant.response is not None
    assert [c.call_id for c in assistant.response.tool_calls] == ["c0", "c1"]
    assert set(assistant.results) == {"c0", "c1"}
    assert session.workspace_heads == {"ws1": 3}
    assert session.tasks["task1"].status == "pending"
    assert session.open_interactions == {"ix1": "question"}
    assert len(session.compacted) == 1


def test_checkpoint_validity_rules() -> None:
    log = Log()
    log.admit_and_claim()
    run = fold_run(log.events, run_id="run1")
    session = fold_session(log.events, stream_id="ses1")
    checkpoint = make_checkpoint(run, session)
    assert restore_checkpoint(checkpoint) == (run, session)
    ok = {"run_id": "run1", "head_seq": 2, "ref": "ck1", "referenced_ref": "ck1"}
    assert checkpoint_is_valid(checkpoint, **ok)
    assert not checkpoint_is_valid(checkpoint, **{**ok, "referenced_ref": "ck0"})
    assert not checkpoint_is_valid(checkpoint, **{**ok, "head_seq": 1})
    assert not checkpoint_is_valid(checkpoint, **{**ok, "run_id": "run2"})
    stale = checkpoint.model_copy(update={"fold_version": FOLD_VERSION + 1})
    assert not checkpoint_is_valid(stale, **ok)
    assert not checkpoint_is_valid(None, **ok)


_TYPES = [*sorted(EVENT_MODELS), "x.acme.thing.done", "tool.teleported"]
_json = st.recursive(
    st.none() | st.booleans() | st.integers(-5, 5) | st.text(max_size=4),
    lambda inner: st.lists(inner, max_size=3)
    | st.dictionaries(st.text(max_size=6), inner, max_size=3),
    max_leaves=6,
)
_data = st.dictionaries(
    st.sampled_from(
        [
            "turn",
            "invocationId",
            "status",
            "slots",
            "interactionId",
            "decision",
            "attempt",
            "fence",
        ]
    ),
    _json,
    max_size=4,
)


@settings(max_examples=300, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(_TYPES),
            _data,
            st.booleans(),
            st.sampled_from(["run1", "run2"]),
        ),
        max_size=25,
    )
)
def test_fold_is_total(raw: list[tuple[str, dict[str, JsonValue], bool, str]]) -> None:
    log = Log()
    for type_, data, critical, run_id in raw:
        log.add(type_, data, critical=critical, run_id=run_id)
    for fold in (
        lambda: fold_run(log.events, run_id="run1"),
        lambda: fold_session(log.events, stream_id="ses1"),
    ):
        try:
            state = fold()
        except FoldIncompatible:
            continue
        assert state.seq == len(log.events)
    with contextlib.suppress(FoldIncompatible):
        _ = fold_run(log.events, run_id="run1").cursor
