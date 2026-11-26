"""SS2 AU-2, AU-3, AU-4, AU-6: what a run's model sees of other participants' turns.

Alice's run read record R through a tool and answered from it; Bob's run, in
the same shared session, must see none of it: one stub stands for Alice's
turn. Bob's own earlier turn reaches his model in full. A run with no audience
(a personal session) renders exactly as before.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from harness_core.contracts import canonical_json
from harness_core.journal import LegacyImport
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage, UserMessage
from harness_core.kernel.durable.audience import (
    FOREIGN_TURN_STUB,
    HistoryAudience,
    project_turns,
)
from harness_core.kernel.durable.transcript import PlainImportedHistory
from harness_core.kernel.fold import fold_session
from harness_core.testing.run_spec import minimal_run_spec

from .support import ORG, SESSION, World, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.kernel.core.messages import Message

ALICE, BOB = "p_alice", "p_bob"
SECRET = "R-7731: the merger closes on 12 March"


def _admit(world: World, run_id: str, principal: str, text: str, role: str | None = None, **extra: Any) -> None:  # noqa: ANN401
    world.journal.admit(
        run_id,
        org_id=ORG,
        session_id=SESSION,
        spec=minimal_run_spec(),
        input_text=text,
        principal_ref=principal,
        session_role=role,
        **extra,
    )


def _texts(messages: list[Message]) -> list[str]:
    out: list[str] = []
    for message in messages:
        if isinstance(message, (UserMessage, AssistantMessage)):
            out.append(message.content if isinstance(message.content, str) else message.text)
        elif isinstance(message, ToolMessage):
            out.append(f"tool:{message.content}")
    return out


def _all_text(messages: list[Message]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in messages])


async def _alice_reads_r(world: World) -> None:
    world.tools.add(
        "read_record",
        lambda args, ctx: _record(),  # type: ignore[arg-type, return-value]
        tags=[("category", "read")],
    )
    _admit(world, "run-a", ALICE, "what does R say?")
    _, port = scripted(reply("", ("read_record", {"id": "R"})), reply(f"R says: {SECRET}"))
    assert (await world.run(world.kernel(port), "run-a")).status == "completed"


async def _record() -> Any:  # noqa: ANN401
    from harness_core.kernel.durable import ToolExecution

    return ToolExecution(status="ok", content={"record": SECRET})


AUDIENCE_BOB = HistoryAudience(principal=BOB, owner=ALICE, own_runs=frozenset())


async def test_au6_bob_s_model_sees_a_stub_for_alice_s_turn_and_nothing_of_r() -> None:
    world = World()
    await _alice_reads_r(world)
    _admit(world, "run-b", BOB, "summarize the thread", role="collaborator")
    _, port = scripted(reply("done"))
    assert (await world.run(world.kernel(port, audience=AUDIENCE_BOB), "run-b")).status == "completed"
    messages = port.calls[0].messages
    assert _texts(messages) == [FOREIGN_TURN_STUB, FOREIGN_TURN_STUB, "summarize the thread"]
    assert SECRET not in _all_text(messages)
    assert "what does R say?" not in _all_text(messages)
    assert not any(isinstance(m, ToolMessage) for m in messages)


async def test_au6_bob_s_own_earlier_turn_is_his_in_full() -> None:
    world = World()
    await _alice_reads_r(world)
    _admit(world, "run-b", BOB, "first from bob", role="collaborator")
    _, port_b = scripted(reply("bob's first answer"))
    assert (await world.run(world.kernel(port_b, audience=AUDIENCE_BOB), "run-b")).status == "completed"
    _admit(world, "run-c", BOB, "second from bob", role="collaborator")
    _, port_c = scripted(reply("ok"))
    audience = HistoryAudience(principal=BOB, owner=ALICE, own_runs=frozenset({"run-b"}))
    assert (await world.run(world.kernel(port_c, audience=audience), "run-c")).status == "completed"
    assert _texts(port_c.calls[0].messages) == [
        FOREIGN_TURN_STUB,
        FOREIGN_TURN_STUB,
        "first from bob",
        "bob's first answer",
        "second from bob",
    ]


async def test_au2_the_owner_s_run_sees_a_collaborator_s_turn_as_a_stub_too() -> None:
    world = World()
    _admit(world, "run-a", ALICE, "alice one")
    _, port_a = scripted(reply("alice answer"))
    assert (await world.run(world.kernel(port_a), "run-a")).status == "completed"
    _admit(world, "run-b", BOB, "bob asks about his own file", role="collaborator")
    _, port_b = scripted(reply("bob's private answer"))
    assert (await world.run(world.kernel(port_b, audience=AUDIENCE_BOB), "run-b")).status == "completed"
    _admit(world, "run-c", ALICE, "alice again", role="owner")
    _, port_c = scripted(reply("ok"))
    audience = HistoryAudience(principal=ALICE, owner=ALICE, own_runs=frozenset({"run-a"}))
    assert (await world.run(world.kernel(port_c, audience=audience), "run-c")).status == "completed"
    assert _texts(port_c.calls[0].messages) == [
        "alice one",
        "alice answer",
        FOREIGN_TURN_STUB,
        FOREIGN_TURN_STUB,
        "alice again",
    ]
    # The fold reads sessionRole on run.admitted (a strict model would refuse an unknown field).
    session = fold_session(world.journal.events(SESSION), stream_id=SESSION)
    assert {e.run_id for e in session.entries} == {"run-a", "run-b", "run-c"}


async def test_au4_without_an_audience_every_turn_is_rendered_as_before() -> None:
    world = World()
    await _alice_reads_r(world)
    _admit(world, "run-b", ALICE, "and then?")
    _, port = scripted(reply("ok"))
    assert (await world.run(world.kernel(port), "run-b")).status == "completed"
    assert SECRET in _all_text(port.calls[0].messages)
    assert _texts(port.calls[0].messages)[0] == "what does R say?"


# ---- AU-3: history imported from the conversation, attributed by `sender` ----

LEGACY: list[dict[str, JsonValue]] = [
    {"role": "user_query", "content": "owner's question"},
    {"role": "bot_response", "content": "owner's answer"},
    {"role": "user_query", "content": "bob's question", "sender": BOB},
    {"role": "bot_response", "content": "bob's answer", "sender": BOB},
]


def test_au3_projection_keeps_own_turns_and_stubs_each_run_of_foreign_ones() -> None:
    assert project_turns(LEGACY, AUDIENCE_BOB) == [
        {"role": "user_query", "content": FOREIGN_TURN_STUB},
        {"role": "bot_response", "content": FOREIGN_TURN_STUB},
        {"role": "user_query", "content": "bob's question"},
        {"role": "bot_response", "content": "bob's answer"},
    ]
    owner = HistoryAudience(principal=ALICE, owner=ALICE, own_runs=frozenset())
    assert project_turns(LEGACY, owner) == [
        *LEGACY[:2],
        {"role": "user_query", "content": FOREIGN_TURN_STUB},
        {"role": "bot_response", "content": FOREIGN_TURN_STUB},
    ]
    # A stub carries nothing of what it stands for.
    rich = [{"role": "bot_response", "content": SECRET, "attachments": [{"recordId": "R"}], "tool_results": [SECRET]}]
    assert SECRET not in json.dumps(project_turns(rich, AUDIENCE_BOB))
    # Projecting again changes nothing.
    once = project_turns(LEGACY, AUDIENCE_BOB)
    assert project_turns([{**t, "sender": BOB} if "bob" in str(t["content"]) else t for t in once], AUDIENCE_BOB) == once


async def test_au3_imported_history_reaches_the_model_projected() -> None:
    world = World()
    _admit(world, "run-b", BOB, "next", role="collaborator", imported=LegacyImport(LEGACY, 4))
    seen: list[list[dict[str, Any]]] = []

    class _Recorder(PlainImportedHistory):
        async def messages(self, transcript: bytes) -> list[Message]:
            seen.append(cast("list[dict[str, Any]]", json.loads(transcript)))
            return await super().messages(transcript)

    _, port = scripted(reply("ok"))
    kernel = world.kernel(port, audience=AUDIENCE_BOB, history=_Recorder())
    assert (await world.run(kernel, "run-b")).status == "completed"
    assert seen == [project_turns(LEGACY, AUDIENCE_BOB)]
    assert _texts(port.calls[0].messages) == [
        FOREIGN_TURN_STUB,
        FOREIGN_TURN_STUB,
        "bob's question",
        "bob's answer",
        "next",
    ]
    assert "owner's" not in canonical_json(cast("JsonValue", _texts(port.calls[0].messages))).decode()


# P2.5a: another participant's turn whose label passed for the actor (S2-6, S2-7).


def _bob_seeing(*runs: str) -> HistoryAudience:
    return HistoryAudience(principal=BOB, owner=ALICE, own_runs=frozenset(), visible_runs=frozenset(runs))


async def test_s2_7_a_passed_turn_reaches_bob_as_its_question_and_answer_only() -> None:
    world = World()
    await _alice_reads_r(world)
    _admit(world, "run-b", BOB, "summarize the thread", role="collaborator")
    _, port = scripted(reply("done"))
    assert (await world.run(world.kernel(port, audience=_bob_seeing("run-a")), "run-b")).status == "completed"
    messages = port.calls[0].messages
    assert _texts(messages) == ["what does R say?", f"R says: {SECRET}", "summarize the thread"]
    # The answer drew on R and Bob may read R; the call and its raw result never reach him.
    assert not any(isinstance(m, ToolMessage) for m in messages)
    assert '\\"record\\"' not in _all_text(messages)
    assert "read_record" not in _all_text(messages)


async def test_s2_7_a_turn_that_did_not_pass_stays_a_stub() -> None:
    world = World()
    await _alice_reads_r(world)
    _admit(world, "run-b", BOB, "summarize the thread", role="collaborator")
    _, port = scripted(reply("done"))
    assert (await world.run(world.kernel(port, audience=_bob_seeing("run-x")), "run-b")).status == "completed"
    assert _texts(port.calls[0].messages) == [FOREIGN_TURN_STUB, FOREIGN_TURN_STUB, "summarize the thread"]
    assert SECRET not in _all_text(port.calls[0].messages)


async def test_s2_6_passed_and_stubbed_turns_keep_the_session_order() -> None:
    world = World()
    await _alice_reads_r(world)
    _admit(world, "run-a2", ALICE, "alice two")
    _, port = scripted(reply("alice answer two"))
    assert (await world.run(world.kernel(port), "run-a2")).status == "completed"
    _admit(world, "run-b", BOB, "bob asks", role="collaborator")
    _, port = scripted(reply("ok"))
    assert (await world.run(world.kernel(port, audience=_bob_seeing("run-a2")), "run-b")).status == "completed"
    assert _texts(port.calls[0].messages) == [
        FOREIGN_TURN_STUB,
        FOREIGN_TURN_STUB,
        "alice two",
        "alice answer two",
        "bob asks",
    ]
    assert SECRET not in _all_text(port.calls[0].messages)
