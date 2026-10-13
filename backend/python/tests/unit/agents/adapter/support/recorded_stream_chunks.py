"""The chunks LangChain's provider integrations yield for one streamed reply.

`recorded_stream_chunks.json` was captured by feeding each provider's wire
format (OpenAI and Azure chat completions, the OpenAI Responses API, Anthropic
messages, Gemini, Ollama) through the real integration's `astream()` with the
HTTP layer mocked, at the package versions the file names under
`recorded_with`. Each chunk was saved as it arrived, since LangChain's own
end-of-stream merge edits the first chunk's list content in place.

Tests that fold these into one message should use them instead of hand-built
chunks: the shapes differ per provider in ways that are easy to get wrong
(string or list content, indexed blocks, whole or fragmented tool calls, where
usage and the stop reason arrive).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from langchain_core.messages import AIMessageChunk


@cache
def _recordings() -> dict[str, list[dict]]:
    with Path(__file__).with_suffix(".json").open(encoding="utf-8") as handle:
        return json.load(handle)["scenarios"]


RECORDED_SCENARIOS = tuple(sorted(_recordings()))


def recorded_chunks(scenario: str) -> list[AIMessageChunk]:
    """Fresh chunk objects for `scenario`; merging mutates them, so never reuse a list."""
    return [AIMessageChunk(**json.loads(json.dumps(chunk))) for chunk in _recordings()[scenario]]


def add_chunk_by_chunk(chunks: list[AIMessageChunk]) -> AIMessageChunk:
    """How a streamed reply was assembled before `merge_message_chunks`: the
    reference its result has to match."""
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk
    return merged
