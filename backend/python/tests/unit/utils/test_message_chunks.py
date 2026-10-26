"""`merge_message_chunks` builds the same message as adding a streamed reply's
chunks one at a time, in one pass instead of one pass per chunk."""

from __future__ import annotations

import threading

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.utils import message_chunks
from app.utils.message_chunks import (
    OFFLOAD_MIN_CHUNKS,
    merge_message_chunks,
    merge_message_chunks_off_loop,
)
from tests.unit.agents.adapter.support.recorded_stream_chunks import (
    RECORDED_SCENARIOS,
    add_chunk_by_chunk,
    recorded_chunks,
)

# Everything later code reads off the assembled message.
_VIEWS = (
    "content", "content_blocks", "tool_calls", "invalid_tool_calls", "tool_call_chunks",
    "usage_metadata", "response_metadata", "additional_kwargs", "id", "chunk_position",
)


def _text_chunks(count: int) -> list[AIMessageChunk]:
    return [AIMessageChunk(content="word ") for _ in range(count)]


class TestSameMessageAsAddingChunkByChunk:
    @pytest.mark.parametrize("scenario", RECORDED_SCENARIOS)
    def test_recorded_provider_stream(self, scenario: str) -> None:
        expected = add_chunk_by_chunk(recorded_chunks(scenario))

        merged = merge_message_chunks(recorded_chunks(scenario))

        assert len(recorded_chunks(scenario)) > 1
        assert type(merged) is type(expected)
        for view in _VIEWS:
            assert getattr(merged, view) == getattr(expected, view), view
        assert merged == expected

    def test_recordings_cover_the_shapes_that_differ_between_providers(self) -> None:
        merged = {name: merge_message_chunks(recorded_chunks(name)) for name in RECORDED_SCENARIOS}
        block_types = {
            block["type"] for message in merged.values() for block in message.content_blocks
        }

        assert {"text", "reasoning", "tool_call"} <= block_types
        assert any(len(m.tool_calls) > 1 for m in merged.values())
        assert any(m.invalid_tool_calls for m in merged.values())
        assert any(isinstance(m.content, list) for m in merged.values())
        assert any(isinstance(m.content, str) for m in merged.values())
        assert all(m.usage_metadata for m in merged.values())
        assert {m.response_metadata.get("model_provider") for m in merged.values()} == {
            "openai", "anthropic", "google_genai", "ollama",
        }

    def test_a_single_chunk_is_returned_as_is(self) -> None:
        only = AIMessageChunk(content="hello")

        assert merge_message_chunks([only]) is only

    def test_a_single_whole_message_is_returned_as_is(self) -> None:
        """The non-streaming fallback hands over one finished `AIMessage`."""
        only = AIMessage(content="hello")

        assert merge_message_chunks([only]) is only

    def test_the_callers_list_is_left_alone(self) -> None:
        chunks = _text_chunks(3)

        merge_message_chunks(chunks)

        assert len(chunks) == 3


class TestLongRepliesMergeOffTheEventLoop:
    @pytest.fixture
    def merge_thread(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """Thread ids `merge_message_chunks` ran on."""
        seen: list[int] = []
        real = message_chunks.merge_message_chunks

        def recording(chunks: list[AIMessageChunk]) -> AIMessageChunk:
            seen.append(threading.get_ident())
            return real(chunks)

        monkeypatch.setattr(message_chunks, "merge_message_chunks", recording)
        return seen

    async def test_a_long_reply_is_merged_in_a_worker_thread(self, merge_thread: list[int]) -> None:
        merged = await merge_message_chunks_off_loop(_text_chunks(OFFLOAD_MIN_CHUNKS))

        assert merged.content == "word " * OFFLOAD_MIN_CHUNKS
        assert len(merge_thread) == 1
        assert merge_thread[0] != threading.get_ident()

    async def test_a_short_reply_is_merged_inline(self, merge_thread: list[int]) -> None:
        merged = await merge_message_chunks_off_loop(_text_chunks(OFFLOAD_MIN_CHUNKS - 1))

        assert merged.content == "word " * (OFFLOAD_MIN_CHUNKS - 1)
        assert merge_thread == [threading.get_ident()]
