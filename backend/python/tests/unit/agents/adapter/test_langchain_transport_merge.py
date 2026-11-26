"""DUR2-T1: the transport assembles a streamed response in time linear in the stream (DUR1-F7).

DUR2 and main's #3943 fixed the same cause; main's ``app.utils.message_chunks`` is the one
mechanism kept at the merge with ``main`` (PR1), and these cases now hold it to DUR2's bounds.

Folding the chunks with ``+`` re-parsed the accumulated tool-call arguments on every
addition: 20,000 one-token argument chunks took about 35 s of CPU on the event loop,
a runaway answer streamed into a tool call up to the output limit half an hour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.utils.message_chunks import merge_message_chunks as assemble_chunks
from tests.support.cpu_budget import thread_cpu

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ARG_CHUNKS = 20_000


def _pairwise(chunks: list[AIMessage]) -> AIMessage:
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk
    return merged


def _tool_stream(n: int) -> list[AIMessage]:
    head = AIMessageChunk(
        content="", tool_call_chunks=[{"name": "final_answer", "args": '{"answer_markdown": "', "id": "c1", "index": 0}]
    )
    body = [
        AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": "ab ", "id": None, "index": 0}])
        for _ in range(n)
    ]
    tail = AIMessageChunk(
        content="",
        tool_call_chunks=[{"name": None, "args": '"}', "id": None, "index": 0}],
        usage_metadata={"input_tokens": 10, "output_tokens": n, "total_tokens": n + 10},
    )
    return [head, *body, tail]


def _mixed_stream() -> list[AIMessage]:
    return [
        AIMessageChunk(content=[{"type": "reasoning", "summary": [{"index": 0, "type": "summary_text", "text": "th"}], "index": 0}]),
        AIMessageChunk(content=[{"type": "reasoning", "summary": [{"index": 0, "type": "summary_text", "text": "ink"}], "index": 0}]),
        AIMessageChunk(content="Hello "),
        AIMessageChunk(content="world"),
        AIMessageChunk(content="", tool_call_chunks=[{"name": "search", "args": '{"q": ', "id": "c1", "index": 0}]),
        AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": '"cats"}', "id": None, "index": 0}]),
        AIMessageChunk(content="", usage_metadata={"input_tokens": 3, "output_tokens": 7, "total_tokens": 10}),
    ]


@pytest.mark.parametrize("stream", [_mixed_stream(), _tool_stream(50), [AIMessageChunk(content="only")]])
def test_dur2_t1_the_message_equals_the_pairwise_fold(stream: list[AIMessage]) -> None:
    linear, pairwise = assemble_chunks(stream), _pairwise(stream)
    assert linear.content == pairwise.content
    assert linear.tool_calls == pairwise.tool_calls
    assert linear.invalid_tool_calls == pairwise.invalid_tool_calls
    assert getattr(linear, "usage_metadata", None) == getattr(pairwise, "usage_metadata", None)


def test_dur2_t1_whole_messages_still_fold() -> None:
    assert assemble_chunks([AIMessage(content="whole")]).content == "whole"


def test_dur2_t1_many_argument_chunks_assemble_in_linear_time() -> None:
    stream = _tool_stream(ARG_CHUNKS)
    with thread_cpu() as cpu:
        message = assemble_chunks(stream)
    assert cpu.seconds < 8.0  # the pairwise fold: ≈ 35 s
    assert message.tool_calls[0]["args"]["answer_markdown"] == "ab " * ARG_CHUNKS


class _StreamingModel:
    def __init__(self, chunks: list[AIMessage]) -> None:
        self._chunks = chunks

    def bind_tools(self, tools: list[Any]) -> _StreamingModel:
        return self

    async def astream(self, messages: list[Any], config: Any = None) -> AsyncIterator[AIMessage]:  # noqa: ANN401
        for chunk in self._chunks:
            yield chunk


async def test_dur2_t1_the_transport_stream_completes_in_linear_time() -> None:
    transport = LangChainTransport(_StreamingModel(_tool_stream(ARG_CHUNKS)))
    with thread_cpu() as cpu:
        events = [e async for e in transport.stream([UserMessage(content="answer")])]
    assert cpu.seconds < 12.0
    final = events[-1]
    assert isinstance(final, StreamCompleteEvent)
    assert final.response.stop_reason == StopReason.TOOL_USE
    assert final.response.message.tool_calls[0].arguments["answer_markdown"] == "ab " * ARG_CHUNKS
