"""Anthropic ``model_context_window_exceeded`` stop reason is a truncation, not a clean end of turn."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.transport.anthropic import AnthropicTransport


def _response(stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="msg_1",
        content=[SimpleNamespace(type="text", text="partial")],
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
        ),
    )


async def test_context_window_exceeded_is_reported_as_truncated() -> None:
    transport = AnthropicTransport(api_key="x", model="claude-sonnet-4-5")
    transport._client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=_response("model_context_window_exceeded"))),
    )

    result = await transport.complete([])

    assert result.stop_reason == StopReason.MAX_TOKENS
    assert result.message.truncated is True
