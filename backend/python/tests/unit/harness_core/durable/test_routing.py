"""P4.7 MP-9 (port): requests stay valid for the route they go to."""

from __future__ import annotations

from harness_core.kernel.core.messages import (
    AssistantMessage,
    ProviderState,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.durable.routing import portable_id, portable_messages


def test_mp9_portable_ids_are_stable_and_only_change_when_a_provider_would_refuse_them() -> None:
    assert portable_id("toolu_01ABC-x") == "toolu_01ABC-x"
    rewritten = portable_id("toolu.01/x")
    assert rewritten == portable_id("toolu.01/x") and rewritten.startswith("call_") and len(rewritten) <= 40
    assert portable_id("a.b") != portable_id("a/b")
    assert portable_id("x" * 41) != "x" * 41


def _history() -> list[object]:
    return [
        UserMessage(content="go"),
        AssistantMessage(
            content=[ThinkingPart(thinking="private"), TextPart(text="looking")],
            tool_calls=[ToolCall(id="toolu.01/x", name="lookup", arguments={})],
            provider_state=ProviderState(wire="anthropic_messages", blocks=[{"type": "thinking"}]),
        ),
        ToolMessage(content="r", tool_call_id="toolu.01/x"),
    ]


def test_mp9_normalized_messages_drop_state_and_thinking_and_keep_pairs() -> None:
    out = portable_messages(_history(), normalize=True)  # type: ignore[arg-type]
    assistant, result = out[1], out[2]
    assert isinstance(assistant, AssistantMessage) and isinstance(result, ToolMessage)
    assert assistant.provider_state is None and assistant.content == [TextPart(text="looking")]
    assert assistant.tool_calls is not None and assistant.tool_calls[0].id == result.tool_call_id == portable_id("toolu.01/x")


def test_mp9_on_the_route_that_rendered_them_state_is_kept() -> None:
    out = portable_messages(_history(), normalize=False)  # type: ignore[arg-type]
    assistant = out[1]
    assert isinstance(assistant, AssistantMessage) and assistant.provider_state is not None
    assert any(isinstance(p, ThinkingPart) for p in assistant.content)
