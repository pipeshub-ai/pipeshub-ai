"""P1b T3 (MG-1, MG-2, MG-4): the legacy history a session imported.

Node journals ``session.imported`` before the first durable run of a legacy
session (and before a later run, for turns legacy wrote meanwhile). The fold
places it where Node journaled it; the renderer gives the model its messages
there, through the product's ``ImportedHistory``, once per attempt. An import
that replaces the history (the conversation changed underneath the journal)
drops everything before it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from harness_core.contracts import canonical_json, dump_contract
from harness_core.failpoints import Arm, Nth, Raise, registry
from harness_core.journal import LegacyImport
from harness_core.journal.inline import inline_ref
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import AssistantMessage, UserMessage
from harness_core.kernel.durable.transcript import (
    PlainImportedHistory,
    TranscriptRenderer,
)
from harness_core.kernel.fold import FOLD_VERSION, fold_session

from .support import RUN, SESSION, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.durable import ModelCall

HISTORY: list[dict[str, JsonValue]] = [
    {"role": "user_query", "content": "What is our leave policy?"},
    {"role": "bot_response", "content": "25 days a year."},
]


class _Recorder:
    """An ``ImportedHistory`` that marks what it rendered and counts renders."""

    def __init__(self) -> None:
        self.renders: list[list[dict[str, Any]]] = []

    async def messages(self, transcript: bytes) -> list[Message]:
        turns = cast("list[dict[str, Any]]", json.loads(transcript))
        self.renders.append(turns)
        return [
            UserMessage(content=f"legacy:{t['content']}")
            if t["role"] == "user_query"
            else AssistantMessage(content=f"legacy:{t['content']}")
            for t in turns
        ]


def _texts(messages: list[Message]) -> list[str]:
    return [
        m.content if isinstance(m.content, str) else m.text
        for m in messages
        if isinstance(m, (UserMessage, AssistantMessage))
    ]


def _event(seq: int, type_: str, data: dict[str, JsonValue], run: str = RUN) -> JournalEvent:
    return JournalEvent(
        org_id="org1",
        session_id=SESSION,
        stream_id=SESSION,
        seq=seq,
        run_id=run,
        type=type_,
        schema_version=1,
        critical=True,
        ts="2026-09-28T00:00:00.000Z",
        writer="node" if type_ == "session.imported" else "worker",
        visibility="owner",
        data=data,
    )


def _imported(seq: int, turns: list[dict[str, JsonValue]], **extra: JsonValue) -> JournalEvent:
    return _event(seq, "session.imported", {**LegacyImport(turns, 4).data(), **extra})


# ---- the fold ----


def test_fold_6_places_an_import_where_node_journaled_it() -> None:
    assert FOLD_VERSION >= 7
    user = cast("JsonValue", dump_contract(inline_ref(b"next")))
    events = [
        _imported(1, HISTORY),
        _event(2, "message.completed", {"role": "user", "textRef": user, "isFinal": False}),
    ]
    session = fold_session(events, stream_id=SESSION)
    assert [(e.seq, e.role) for e in session.entries] == [(1, "imported"), (2, "user")]
    (imported, _) = session.entries
    assert imported.text_ref is not None
    assert imported.text_ref.hash == events[0].data["transcriptDigest"]


def test_an_import_that_replaces_the_history_drops_everything_before_it() -> None:
    user = cast("JsonValue", dump_contract(inline_ref(b"earlier")))
    events = [
        _imported(1, HISTORY),
        _event(2, "message.completed", {"role": "user", "textRef": user, "isFinal": False}, run="run0"),
        _imported(3, [{"role": "user_query", "content": "all of it"}], replacesHistory=True),
    ]
    session = fold_session(events, stream_id=SESSION)
    assert [(e.seq, e.role) for e in session.entries] == [(3, "imported")]


def test_an_import_without_a_transcript_adds_nothing() -> None:
    event = _event(1, "session.imported", {"upToMessageSeq": 3, "transcriptDigest": "sha256:" + "a" * 64})
    assert fold_session([event], stream_id=SESSION).entries == []


async def test_the_plain_history_is_each_entrys_text() -> None:
    messages = await PlainImportedHistory().messages(
        canonical_json(cast("JsonValue", [*HISTORY, {"role": "bot_response", "content": ""}, {"role": "system", "content": "x"}]))
    )
    assert messages == [UserMessage(content="What is our leave policy?"), AssistantMessage(content="25 days a year.")]


# ---- MG-1: the model sees the imported history before the run's input ----


async def test_mg1_the_first_request_starts_with_the_imported_history() -> None:
    world = World()
    world.admit(imported=LegacyImport(HISTORY, 2), text="and sick leave?")
    history = _Recorder()
    seen: list[int | None] = []

    class _Shaper:
        async def shape(self, call: ModelCall, turn: int, state: object) -> ModelCall:
            seen.append(call.input_index)
            return call

        async def enter(self, turn: int, state: object) -> None:
            return None

    _, port = scripted(reply("", ("lookup", {"q": "sick"})), reply("10 days."))
    done = await world.run(world.kernel(port, history=history, shaper=_Shaper()))
    assert done.status == "completed"
    first, second = (call.messages for call in port.calls)
    assert _texts(first) == ["legacy:What is our leave policy?", "legacy:25 days a year.", "and sick leave?"]
    assert _texts(second)[:3] == _texts(first)
    assert_paired(second)
    # The run's own input is where the shaper looks for it, after the history.
    assert seen == [2, 2]
    # Rendered once for the attempt, as legacy seeds a request once.
    assert history.renders == [HISTORY]
    (imported,) = world.events("session.imported")
    assert world.types()[:2] == ["session.imported", "run.admitted"]
    assert imported.writer == "node" and imported.data["messages"] == 2


async def test_mg1_without_a_product_history_the_entries_are_plain_text() -> None:
    world = World()
    world.admit(imported=LegacyImport(HISTORY, 2), text="and sick leave?")
    _, port = scripted(reply("10 days."))
    assert (await world.run(world.kernel(port))).status == "completed"
    assert port.calls[0].messages == [
        UserMessage(content="What is our leave policy?"),
        AssistantMessage(content="25 days a year."),
        UserMessage(content="and sick leave?"),
    ]


# ---- MG-2: re-claim ----


async def test_mg2_a_reclaimed_attempt_renders_the_same_history_and_imports_nothing() -> None:
    world = World()
    world.admit(imported=LegacyImport(HISTORY, 2), text="and sick leave?")
    first = _Recorder()
    _, port = scripted(reply("", ("lookup", {"q": "sick"})), reply("10 days."))
    registry.arm("worker.model.after_response_commit", Arm(Raise(Crash), Nth(1)))
    await world.crash(world.kernel(port, history=first))
    registry.reset()
    second = _Recorder()
    _, resumed = scripted(reply("10 days."))
    assert (await world.run(world.kernel(resumed, history=second))).status == "completed"
    assert first.renders == second.renders == [HISTORY]
    assert _texts(resumed.calls[0].messages)[:3] == _texts(port.calls[0].messages)
    assert len(world.events("session.imported")) == 1


# ---- MG-4: a later run keeps the import in place; a later import follows the runs before it ----


async def test_mg4_a_later_run_sees_the_import_then_the_earlier_run_then_turns_legacy_wrote_meanwhile() -> None:
    world = World()
    world.admit("run-a", imported=LegacyImport(HISTORY, 2), text="and sick leave?")
    _, port_a = scripted(reply("10 days."))
    assert (await world.run(world.kernel(port_a), "run-a")).status == "completed"
    meanwhile: list[dict[str, JsonValue]] = [
        {"role": "user_query", "content": "and parental leave?"},
        {"role": "bot_response", "content": "16 weeks."},
    ]
    world.admit("run-b", imported=LegacyImport(meanwhile, 6), text="and unpaid leave?")
    _, port_b = scripted(reply("Up to a year."))
    assert (await world.run(world.kernel(port_b), "run-b")).status == "completed"
    assert _texts(port_b.calls[0].messages) == [
        "What is our leave policy?",
        "25 days a year.",
        "and sick leave?",
        "10 days.",
        "and parental leave?",
        "16 weeks.",
        "and unpaid leave?",
    ]


async def test_mg2_a_replacing_import_is_the_whole_history_the_next_run_sees() -> None:
    world = World()
    world.admit("run-a", imported=LegacyImport(HISTORY, 2), text="and sick leave?")
    _, port_a = scripted(reply("10 days."))
    assert (await world.run(world.kernel(port_a), "run-a")).status == "completed"
    now: list[dict[str, JsonValue]] = [
        {"role": "user_query", "content": "What is our leave policy?"},
        {"role": "bot_response", "content": "25 days a year."},
        {"role": "user_query", "content": "and sick leave?"},
        {"role": "bot_response", "content": "Twelve days, regenerated."},
    ]
    world.admit("run-b", imported=LegacyImport(now, 4, replaces_history=True), text="and unpaid leave?")
    _, port_b = scripted(reply("Up to a year."))
    assert (await world.run(world.kernel(port_b), "run-b")).status == "completed"
    assert _texts(port_b.calls[0].messages) == [
        "What is our leave policy?",
        "25 days a year.",
        "and sick leave?",
        "Twelve days, regenerated.",
        "and unpaid leave?",
    ]


# ---- MG-1c: a sealed transcript ----


async def test_mg1c_a_transcript_in_the_content_store_is_read_and_verified() -> None:
    world = World()
    turns = [{"role": "user_query", "content": "x" * 300_000}, {"role": "bot_response", "content": "ok"}]
    stored = await world.content.put(LegacyImport(cast("Any", turns), 2).transcript())
    assert not stored.ref.startswith("inline:")
    world.admit(imported=LegacyImport(cast("Any", turns), 2, transcript_ref=stored), text="next")
    history = _Recorder()
    _, port = scripted(reply("done"))
    assert (await world.run(world.kernel(port, history=history))).status == "completed"
    assert history.renders == [turns]
    (imported,) = world.events("session.imported")
    assert imported.data["transcriptDigest"] == stored.hash


async def test_the_renderer_hands_out_copies_of_a_cached_rendering() -> None:
    world = World()
    world.admit(imported=LegacyImport(HISTORY, 2))
    session = fold_session(world.journal.events(SESSION), stream_id=SESSION)
    renderer = TranscriptRenderer(world.content, cast("Any", None), _Recorder())
    first = await renderer.render(session)
    first[0].content = "mutated by a shaper"
    again = await renderer.render(session)
    assert _texts(again)[0] == "legacy:What is our leave policy?"
