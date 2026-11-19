"""An empty assistant turn must not be replayed to Chat Completions."""

from __future__ import annotations

from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.transport.openai import OpenAITransport
from app.agent_loop_lib.transport.prompt_cache import OpenAICachePlan


def _roles(messages: list) -> list[str]:
    formatted = OpenAITransport(api_key="x")._format_messages(messages, None)
    return [m["role"] for m in formatted if m["role"] != "system"]


def test_empty_assistant_turn_is_skipped() -> None:
    messages = [
        UserMessage(content="hi"),
        AssistantMessage(content=[]),
        UserMessage(content="again"),
    ]
    assert _roles(messages) == ["user", "user"]


def test_assistant_turn_with_text_or_tool_calls_is_kept() -> None:
    call = ToolCall(id="c1", name="search", arguments={})
    messages = [
        UserMessage(content="hi"),
        AssistantMessage(content="hello"),
        AssistantMessage(content=[], tool_calls=[call]),
    ]
    assert _roles(messages) == ["user", "assistant", "assistant"]


def test_only_empty_assistant_turns_with_cache_marker_does_not_raise() -> None:
    transport = OpenAITransport(api_key="x", cache_plan=OpenAICachePlan(last_message=True))
    assert transport._format_messages([AssistantMessage(content=[])], None) == []
