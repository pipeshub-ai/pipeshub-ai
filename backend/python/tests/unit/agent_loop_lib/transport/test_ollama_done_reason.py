"""Ollama reports `done_reason: "length"` when generation hits num_predict / the context
window. The transport must surface that as a truncated, MAX_TOKENS response."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import StreamCompleteEvent
from app.agent_loop_lib.transport.ollama import OllamaTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _http_response(body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = body
    resp.raise_for_status.return_value = None
    return resp


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakeStreamCM:
    def __init__(self, response: _FakeStream) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStream:
        return self._response

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_complete_length_marks_truncated() -> None:
    transport = OllamaTransport()
    body = {
        "message": {"content": "The answer is"},
        "done": True,
        "done_reason": "length",
    }
    transport._client.post = AsyncMock(return_value=_http_response(body))

    result = await transport.complete([UserMessage(content="hi")])

    assert result.message.truncated is True
    assert result.stop_reason == StopReason.MAX_TOKENS


async def test_complete_stop_is_not_truncated() -> None:
    transport = OllamaTransport()
    body = {"message": {"content": "done"}, "done": True, "done_reason": "stop"}
    transport._client.post = AsyncMock(return_value=_http_response(body))

    result = await transport.complete([UserMessage(content="hi")])

    assert result.message.truncated is False
    assert result.stop_reason == StopReason.END_TURN


async def test_stream_length_marks_truncated() -> None:
    transport = OllamaTransport()
    lines = [
        json.dumps({"message": {"content": "The answer is"}}),
        json.dumps({"message": {"content": ""}, "done": True, "done_reason": "length"}),
    ]
    transport._client.stream = MagicMock(return_value=_FakeStreamCM(_FakeStream(lines)))

    events = [event async for event in transport.stream([UserMessage(content="hi")])]

    final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
    assert final.message.truncated is True
    assert final.stop_reason == StopReason.MAX_TOKENS
