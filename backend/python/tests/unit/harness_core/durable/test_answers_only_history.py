"""A conversation others read (``answers_only_history``): a later run's model is given the earlier runs' questions
and answers' text, never their tool calls or what the tools returned.

Ada's first run searched, and the search returned a private document beside a public one; her answer cited only the
public one. Her later runs ask about what the first one found. In a chat others read their models get her question
and her answer, and nothing the search returned, so a fact the answer did not state must come from a fresh search
that the later run cites. A chat nobody else reads keeps the earlier results, as before.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from harness_core.contracts.generated.common import SeqRange
from harness_core.journal.inline import inline_ref
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage, UserMessage
from harness_core.kernel.durable.context_compiler import journal_context
from harness_core.kernel.durable.transcript import TranscriptRenderer
from harness_core.kernel.fold import CompactionRecord, SessionEntry, SessionState
from harness_core.testing.run_spec import minimal_run_spec

from .support import ORG, SESSION, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import Message

ADA = "p_ada"
PRIVATE = "Alpha budget 4.2M, the board has not seen it"
PUBLIC_ANSWER = "The public Charlie plan lists three themes."
FIRST = "what do our plans say?"
LATER = ("what did the Alpha result say?", "and its budget?", "list everything you found")


def _admit(world: World, run_id: str, text: str, **extra: Any) -> None:  # noqa: ANN401
    world.journal.admit(
        run_id, org_id=ORG, session_id=SESSION, spec=minimal_run_spec(), input_text=text, principal_ref=ADA, **extra
    )


def _texts(messages: list[Message]) -> list[str]:
    out: list[str] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            out.append(message.text)
        elif isinstance(message, UserMessage):
            out.append(message.content if isinstance(message.content, str) else "")
        elif isinstance(message, ToolMessage):
            out.append(f"tool:{message.content}")
    return out


def _all_text(messages: list[Message]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in messages])


async def _results() -> Any:  # noqa: ANN401
    from harness_core.kernel.durable import ToolExecution

    return ToolExecution(
        status="ok",
        content={"results": [{"title": "Alpha", "text": PRIVATE}, {"title": "Charlie", "text": "three themes"}]},
    )


async def _ada_searches(world: World) -> None:
    world.tools.add("search", lambda args, ctx: _results(), tags=[("category", "read")])  # type: ignore[arg-type, return-value]
    _admit(world, "run-1", FIRST)
    _, port = scripted(reply("", ("search", {"query": "plans"})), reply(PUBLIC_ANSWER))
    assert (await world.run(world.kernel(port), "run-1")).status == "completed"


async def test_turns_two_to_four_of_a_chat_others_read_are_given_ada_s_answer_and_nothing_her_search_returned() -> None:
    world = World()
    await _ada_searches(world)
    asked: list[str] = [FIRST, PUBLIC_ANSWER]
    for n, question in enumerate(LATER, start=2):
        _admit(world, f"run-{n}", question)
        _, port = scripted(reply("I would search again for that."))
        kernel = world.kernel(port, answers_only_history=True)
        assert (await world.run(kernel, f"run-{n}")).status == "completed"
        messages = port.calls[0].messages
        # The built context: the earlier turns' questions and answers, then this turn's question.
        assert _texts(messages) == [*asked, question]
        assert not any(isinstance(m, ToolMessage) for m in messages)
        assert not any(isinstance(m, AssistantMessage) and m.tool_calls for m in messages)
        assert PRIVATE not in _all_text(messages)
        assert "Alpha" not in _all_text(messages).replace("the Alpha result", "")
        asked += [question, "I would search again for that."]


async def test_a_chat_nobody_else_reads_still_reuses_the_earlier_search() -> None:
    world = World()
    await _ada_searches(world)
    _admit(world, "run-2", LATER[0])
    _, port = scripted(reply("It said the budget is 4.2M."))
    assert (await world.run(world.kernel(port), "run-2")).status == "completed"
    messages = port.calls[0].messages
    assert any(isinstance(m, ToolMessage) for m in messages)
    assert PRIVATE in _all_text(messages)


async def test_the_run_s_own_tool_results_reach_its_model_whole() -> None:
    world = World()
    await _ada_searches(world)
    _admit(world, "run-2", LATER[0])
    _, port = scripted(reply("", ("search", {"query": "Alpha"})), reply("Found it again, cited."))
    assert (await world.run(world.kernel(port, answers_only_history=True), "run-2")).status == "completed"
    first, second = (call.messages for call in port.calls)
    assert PRIVATE not in _all_text(first)
    # The second call carries this run's own search, the one its answer would cite.
    assert PRIVATE in _all_text(second)
    assert sum(isinstance(m, ToolMessage) for m in second) == 1


def _session(compacted_at: int) -> SessionState:
    return SessionState(
        stream_id=SESSION,
        seq=10,
        entries=[
            SessionEntry(seq=1, run_id="run-1", role="user", text_ref=inline_ref(b"earlier question")),
            SessionEntry(seq=5, run_id="run-2", role="user", text_ref=inline_ref(b"this question")),
        ],
        compactions=[
            CompactionRecord(
                seq=compacted_at,
                range=SeqRange(from_seq=1, to_seq=3),
                summary_ref=inline_ref(f"summary that quotes {PRIVATE}".encode()),
            )
        ],
    )


async def test_a_summary_an_earlier_run_wrote_is_not_used_in_a_chat_others_read() -> None:
    world = World()
    shared = TranscriptRenderer(world.content, cast("Any", None), run_id="run-2", answers_only=True)
    _, compactions, _ = await journal_context(shared, _session(compacted_at=4), "run-2")
    assert compactions == ()
    solo = TranscriptRenderer(world.content, cast("Any", None), run_id="run-2")
    _, compactions, _ = await journal_context(solo, _session(compacted_at=4), "run-2")
    assert [PRIVATE in c.summary for c in compactions] == [True]


async def test_a_summary_this_run_wrote_is_used() -> None:
    world = World()
    shared = TranscriptRenderer(world.content, cast("Any", None), run_id="run-2", answers_only=True)
    _, compactions, _ = await journal_context(shared, _session(compacted_at=7), "run-2")
    assert len(compactions) == 1
