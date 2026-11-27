# pyright: reportPrivateUsage=false
"""P4.15 AF-7: the run-start note right after the user's input, on each wire format the transports send.

The driver's own first and second requests (a run with a note) go through the real transports: Messages (Anthropic),
Chat Completions (OpenAI), Responses and Converse (Bedrock). A user-role note after a user turn must stay a user turn, keep its
place, and on Messages carry the history breakpoint (F5) where the compiler marked it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from harness_core.context.notes import note_message
from harness_core.kernel.core.messages import render_context_note
from harness_core.models.sigv4 import AwsCredentials
from harness_core.models.transports.bedrock_converse import BedrockConverseTransport
from harness_core.models.transports.openai_responses import format_responses_input
from tests.unit.harness_core.durable.support import World

from .kernel_support import answered, looked_up
from .test_notes import TIME, NotingHost, _run
from .test_provider_layouts import _anthropic, _openai, _send

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import ModelCall

# On the wire a note is the transports' context note around the host's own framing (main #4534's `as_sent`).
NOTE = render_context_note(note_message([TIME]))


async def _calls() -> list[ModelCall]:
    return await _run(World(), NotingHost(), "run-1", looked_up(300, "a"), answered())


def _text(content: Any) -> str:  # noqa: ANN401
    if isinstance(content, str):
        return content
    blocks = cast("list[Any]", content)
    return "".join(str(cast("dict[str, Any]", b).get("text", "")) for b in blocks if isinstance(b, dict))


async def test_af7_messages_keeps_the_note_as_a_user_turn_with_the_breakpoint_on_it() -> None:
    first = (await _calls())[0]
    transport, capture = _anthropic()
    await _send(transport, first)
    messages = capture.kwargs["messages"]
    assert [m["role"] for m in messages] == ["user", "user"]
    assert [_text(m["content"]) for m in messages] == ["look it up", NOTE]
    assert first.cache_marks == (1,)
    flags = [isinstance(m["content"], list) and "cache_control" in m["content"][-1] for m in messages]
    assert flags == [False, True], "the breakpoint ends the history band: the note"


async def test_af7_chat_completions_sends_the_note_after_the_input_in_place() -> None:
    second = (await _calls())[1]
    transport, capture = _openai()
    await _send(transport, second)
    roles = [(m["role"], m.get("content")) for m in capture.kwargs["messages"]]
    assert roles[:3] == [("system", second.system), ("user", "look it up"), ("user", NOTE)]
    assert roles[3][0] == "assistant"


async def test_af7_converse_joins_the_input_and_the_note_into_one_user_turn_in_order() -> None:
    transport = BedrockConverseTransport(region="us-east-1", credentials=AwsCredentials("a", "b"), model="m")
    first = (await _calls())[0]
    turns = transport._messages(list(first.messages))
    assert [t["role"] for t in turns] == ["user"]
    assert [block["text"] for block in turns[0]["content"]] == ["look it up", NOTE]


async def test_af7_responses_sends_the_note_as_a_user_input_item_after_the_input() -> None:
    first = (await _calls())[0]
    items = format_responses_input(list(first.messages), first.system)
    assert [(i.get("role"), _text(i.get("content"))) for i in items[1:]] == [("user", "look it up"), ("user", NOTE)]
