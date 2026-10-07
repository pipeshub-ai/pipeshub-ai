"""`LangChainTransport.stream()` assembles a streamed reply into the same final
response however many chunks it arrived in, in one pass, and without holding
the event loop while it does.

The reported failure: a long tool call (a `final_answer` carrying the whole
answer, or a call the model never closes) was re-parsed from its first
character for every chunk received, on the event loop. One CPU core stayed
busy for minutes and the query service stopped answering `/health`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.messages import ai as langchain_ai

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, ToolCallDeltaEvent
from app.agents.agent_loop.converters import (
    convert_assistant_message_from_langchain,
    token_usage_from_ai_message,
)
from app.agents.agent_loop.langchain_transport import LangChainTransport
from tests.unit.agents.adapter.support.recorded_stream_chunks import (
    RECORDED_SCENARIOS,
    add_chunk_by_chunk,
    recorded_chunks,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.responses import ModelResponse

_OPENING = '{"answer_markdown": "'
_FRAGMENT = "word "
_CLOSING = '"}'

_EXPECTED_STOP_REASON = {
    "anthropic_cut_off_mid_tool_use": StopReason.MAX_TOKENS,
    "anthropic_thinking_text_tool_use": StopReason.TOOL_USE,
    "azure_openai_chat_tool_call": StopReason.TOOL_USE,
    "gemini_thought_text_function_call": StopReason.TOOL_USE,
    "ollama_text_and_whole_tool_call": StopReason.TOOL_USE,
    "openai_chat_cut_off_mid_arguments": StopReason.MAX_TOKENS,
    "openai_chat_final_answer_tool_call": StopReason.TOOL_USE,
    "openai_chat_malformed_arguments": StopReason.TOOL_USE,
    "openai_chat_text_and_two_tool_calls": StopReason.TOOL_USE,
    "openai_chat_text_only": StopReason.END_TURN,
    "openai_responses_reasoning_text_tool_call": StopReason.TOOL_USE,
}

# Longest the event loop may go without running anything else. The stream
# below held it for many seconds before the fix and for milliseconds after.
_LONGEST_STALL_S = 2.0


class _ChunkModel:
    """Yields its chunks back to back, never waiting on anything -- a reply
    that arrived in one network burst, the worst case for the event loop."""

    def __init__(self, chunks: list[AIMessageChunk]) -> None:
        self._chunks = chunks

    def bind_tools(self, tools: list[Any]) -> "_ChunkModel":
        return self

    async def astream(self, messages: list, config: dict | None = None) -> "AsyncIterator[AIMessageChunk]":
        for chunk in self._chunks:
            yield chunk


def _tool_call_stream(fragments: int) -> list[AIMessageChunk]:
    """One `final_answer` call whose answer arrives in `fragments` pieces, in
    the shape the OpenAI and Azure integrations stream it."""

    def piece(args: str, name: str | None = None, call_id: str | None = None) -> AIMessageChunk:
        return AIMessageChunk(
            content="", id="lc_run-1", response_metadata={"model_provider": "openai"},
            tool_call_chunks=[{"name": name, "args": args, "id": call_id, "index": 0}],
        )

    return [
        piece("", name="final_answer", call_id="call_1"),
        piece(_OPENING),
        *(piece(_FRAGMENT) for _ in range(fragments)),
        piece(_CLOSING),
        AIMessageChunk(
            content="", id="lc_run-1",
            response_metadata={"finish_reason": "tool_calls", "model_provider": "openai"},
        ),
        AIMessageChunk(
            content="", id="lc_run-1",
            usage_metadata={"input_tokens": 11, "output_tokens": fragments, "total_tokens": 11 + fragments},
        ),
        AIMessageChunk(content="", id="lc_run-1", chunk_position="last"),
    ]


async def _final_response(transport: LangChainTransport) -> "ModelResponse":
    events = [event async for event in transport.stream([UserMessage(content="hi")])]
    assert isinstance(events[-1], StreamCompleteEvent)
    return events[-1].response


class _ReparsedTheWholeReply(BaseException):
    """Not an `Exception`: LangChain swallows those while parsing tool-call
    arguments, and the point is to stop the test at once rather than let a
    quadratic merge run for minutes before an assertion can fail."""


class TestFinalResponseIsUnchanged:
    @pytest.mark.parametrize("scenario", RECORDED_SCENARIOS)
    async def test_recorded_provider_stream(self, scenario: str) -> None:
        reference = add_chunk_by_chunk(recorded_chunks(scenario))
        transport = LangChainTransport(_ChunkModel(recorded_chunks(scenario)), model_name="model-x")

        response = await _final_response(transport)

        assert response.message == convert_assistant_message_from_langchain(reference)
        assert response.usage == token_usage_from_ai_message(reference)
        assert response.stop_reason == _EXPECTED_STOP_REASON[scenario]
        assert response.model == "model-x"

    async def test_parallel_tool_calls_keep_their_order_ids_and_arguments(self) -> None:
        transport = LangChainTransport(
            _ChunkModel(recorded_chunks("openai_chat_text_and_two_tool_calls")),
        )

        response = await _final_response(transport)

        assert response.message.text == "Let me look."
        assert [(c.id, c.name, c.arguments) for c in response.message.tool_calls] == [
            ("call_a", "search", {"query": "cats and dogs"}),
            ("call_b", "search", {"query": "birds"}),
        ]
        assert (response.usage.input_tokens, response.usage.output_tokens) == (11, 7)
        assert response.usage.cache_read_tokens == 3

    async def test_a_reply_cut_off_mid_arguments_is_still_marked_cut_off(self) -> None:
        transport = LangChainTransport(
            _ChunkModel(recorded_chunks("anthropic_cut_off_mid_tool_use")),
        )

        response = await _final_response(transport)

        assert response.message.truncated is True
        assert response.message.tool_calls[0].arguments == {
            "answer_markdown": "This answer is cut off mid-sent",
        }


class TestLongToolCallIsAssembledInOnePass:
    async def test_arguments_are_parsed_once_not_once_per_fragment(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fragments = 20_000
        chunks = _tool_call_stream(fragments)
        arguments_chars = len(_OPENING) + fragments * len(_FRAGMENT) + len(_CLOSING)
        parsed_chars = 0
        real_parse = langchain_ai.parse_partial_json

        def counting_parse(text: str, **kwargs: Any) -> Any:  # noqa: ANN401
            nonlocal parsed_chars
            parsed_chars += len(text)
            if parsed_chars > 4 * arguments_chars:
                raise _ReparsedTheWholeReply
            return real_parse(text, **kwargs)

        # Patched after the chunks exist: building each one parses its own fragment.
        monkeypatch.setattr(langchain_ai, "parse_partial_json", counting_parse)

        response = await _final_response(LangChainTransport(_ChunkModel(chunks)))

        assert response.stop_reason == StopReason.TOOL_USE
        [call] = response.message.tool_calls
        assert (call.id, call.name) == ("call_1", "final_answer")
        assert call.arguments == {"answer_markdown": _FRAGMENT * fragments}
        assert response.usage.output_tokens == fragments
        assert arguments_chars <= parsed_chars <= 2 * arguments_chars

    async def test_every_fragment_still_reaches_the_live_answer(self) -> None:
        fragments = 2_000

        events = [
            event
            async for event in LangChainTransport(
                _ChunkModel(_tool_call_stream(fragments)),
            ).stream([UserMessage(content="hi")])
        ]

        deltas = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
        assert deltas[0].name == "final_answer"
        assert "".join(d.arguments_delta for d in deltas) == (
            _OPENING + _FRAGMENT * fragments + _CLOSING
        )


class TestEventLoopStaysResponsive:
    async def test_other_work_keeps_running_through_a_long_stream(self) -> None:
        """A coroutine that does nothing but yield stands in for `/health` and
        every other request on the worker: if it stops being scheduled, they do."""
        fragments = 8_000
        transport = LangChainTransport(_ChunkModel(_tool_call_stream(fragments)))
        turns = 0
        longest_stall = 0.0
        last_turn = time.perf_counter()

        def note_turn() -> None:
            nonlocal longest_stall, last_turn
            now = time.perf_counter()
            longest_stall = max(longest_stall, now - last_turn)
            last_turn = now

        async def heartbeat() -> None:
            nonlocal turns
            while True:
                await asyncio.sleep(0)
                note_turn()
                turns += 1

        beat = asyncio.create_task(heartbeat())
        try:
            response = await _final_response(transport)
            # The stream can finish without handing the loop back, so the wait
            # that ended with it is measured here rather than by the heartbeat.
            note_turn()
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

        assert response.message.tool_calls[0].arguments == {
            "answer_markdown": _FRAGMENT * fragments,
        }
        # The loop got a turn for every chunk, even with none of them waiting
        # on the network, and was never held for long by the final merge.
        assert turns >= fragments
        assert longest_stall < _LONGEST_STALL_S
