"""PS-5 / FL-2: with provider state not kept (``HARNESS_PROVIDER_STATE`` off), every request the
direct transports build is byte-equal to what they built before P4.3 (goldens recorded at
366571a8c), and a message without state serializes as before.

Record with ``UPDATE_GOLDENS=1`` only when a change to today's requests is intended.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.openai import OpenAITransport, RequestDefaults

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import Message

GOLDENS = Path(__file__).parent / "goldens" / "flag_off"


class _Captured(Exception):
    pass


def _history() -> list[Message]:
    """A tool turn whose assistant message carries what a restored raw blob carries (thinking)."""
    return [
        UserMessage(content="find the bug"),
        AssistantMessage(
            content=[ThinkingPart(thinking="look it up first"), TextPart(text="looking")],
            tool_calls=[ToolCall(id="toolu_01", name="lookup", arguments={"q": "bug"})],
        ),
        ToolMessage(content="found PROJ-1", tool_call_id="toolu_01"),
        UserMessage(content="now file it"),
    ]


def _tools() -> list[ToolSchema]:
    return [
        ToolSchema(name="lookup", description="Look up", input_schema={"type": "object", "properties": {"q": {"type": "string"}}}),
        ToolSchema(name="create_issue", description="File", input_schema={"type": "object", "properties": {}}),
    ]


def _check(name: str, kwargs: dict[str, Any]) -> None:
    path = GOLDENS / f"{name}.json"
    body = json.dumps(kwargs, indent=1, sort_keys=True, default=str) + "\n"
    if os.environ.get("UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    assert body == path.read_text(), f"{name}: the request changed with provider state off"


def _capturing(seen: list[dict[str, Any]]) -> Any:  # noqa: ANN401
    def call(**kwargs: Any) -> Any:  # noqa: ANN401
        seen.append(kwargs)
        raise _Captured

    return call


def _anthropic(seen: list[dict[str, Any]], **kwargs: Any) -> AnthropicTransport:  # noqa: ANN401
    transport = AnthropicTransport(api_key="sk-test", model="claude-x", **kwargs)
    transport._client = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        messages=SimpleNamespace(create=_async(_capturing(seen)), stream=_capturing(seen))
    )
    return transport


def _async(fn: Any) -> Any:  # noqa: ANN401
    async def call(**kwargs: Any) -> Any:  # noqa: ANN401
        return fn(**kwargs)

    return call


def _openai(seen: list[dict[str, Any]], *, responses: bool) -> OpenAITransport:
    transport = OpenAITransport(
        api_key="sk-test",
        model="gpt-x",
        defaults=RequestDefaults(model="gpt-x", use_responses_api=responses, reasoning={"effort": "low"} if responses else None),
    )
    create = _async(_capturing(seen))
    transport._client = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        responses=SimpleNamespace(create=create),
    )
    return transport


async def test_ps5_anthropic_complete_request_is_unchanged() -> None:
    seen: list[dict[str, Any]] = []
    transport = _anthropic(seen, thinking={"type": "enabled", "budget_tokens": 2048})
    with pytest.raises(Exception):  # noqa: B017 - the stub stops the call after capturing it
        await transport.complete(_history(), tools=_tools(), system="be brief")
    _check("anthropic_complete", seen[0])


async def test_ps5_anthropic_stream_request_is_unchanged() -> None:
    seen: list[dict[str, Any]] = []
    transport = _anthropic(seen)
    with pytest.raises(Exception):  # noqa: B017
        async for _ in transport.stream(_history(), tools=_tools(), system_blocks=["stable", "volatile"]):
            pass
    _check("anthropic_stream", seen[0])


async def test_ps5_openai_responses_request_is_unchanged() -> None:
    seen: list[dict[str, Any]] = []
    transport = _openai(seen, responses=True)
    with pytest.raises(Exception):  # noqa: B017
        await transport.complete(_history(), tools=_tools(), system="be brief")
    _check("openai_responses_complete", seen[0])


async def test_ps5_openai_chat_request_is_unchanged() -> None:
    seen: list[dict[str, Any]] = []
    transport = _openai(seen, responses=False)
    with pytest.raises(Exception):  # noqa: B017
        await transport.complete(_history(), tools=_tools(), system="be brief")
    _check("openai_chat_complete", seen[0])


def test_ps5_a_message_without_state_serializes_as_before() -> None:
    message = AssistantMessage(
        content=[ThinkingPart(thinking="t"), TextPart(text="x")],
        tool_calls=[ToolCall(id="c1", name="lookup", arguments={"q": 1})],
    )
    _check("assistant_message_json", json.loads(message.model_dump_json()))
    assert "provider_state" not in message.model_dump(mode="json")
