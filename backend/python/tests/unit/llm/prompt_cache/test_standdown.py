from __future__ import annotations

from app.llm.prompt_cache.standdown import count_existing_breakpoints, should_stand_down


class TestNoExistingBreakpoints:
    def test_empty_payload(self) -> None:
        assert should_stand_down([], None, None) is False

    def test_plain_messages_system_tools(self) -> None:
        messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        system = [{"type": "text", "text": "sys"}]
        tools = [{"name": "a"}]
        assert should_stand_down(messages, system, tools) is False


class TestDetectsMessageLevelMarkers:
    def test_marker_on_nested_content_block(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
            }
        ]
        assert should_stand_down(messages, None, None) is True

    def test_marker_directly_on_a_string_content_message_shape(self) -> None:
        # Non-standard shape (marker on the message dict itself rather than
        # a nested content block) — still detected rather than assumed absent.
        messages = [{"role": "user", "content": "hi", "cache_control": {"type": "ephemeral"}}]
        assert should_stand_down(messages, None, None) is True


class TestDetectsSystemLevelMarkers:
    def test_marker_on_system_block(self) -> None:
        system = [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
        assert should_stand_down([], system, None) is True


class TestDetectsToolLevelMarkers:
    def test_marker_on_anthropic_shaped_tool(self) -> None:
        tools = [{"name": "a", "cache_control": {"type": "ephemeral"}}]
        assert should_stand_down([], None, tools) is True

    def test_marker_on_openai_shaped_tool_function_wrapper(self) -> None:
        tools = [{"type": "function", "function": {"name": "a", "cache_control": {"type": "ephemeral"}}}]
        assert should_stand_down([], None, tools) is True


class TestDetectsOpenAIPromptCacheBreakpointMarker:
    """OpenAI's own explicit marker shape (`prompt_cache_breakpoint`)
    must also trigger stand-down — not just Anthropic's `cache_control`
    — since a gateway may have pre-annotated a payload with either."""

    def test_marker_on_message_content_block(self) -> None:
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "stable", "prompt_cache_breakpoint": {"mode": "explicit"}}
                ],
            }
        ]
        assert should_stand_down(messages, None, None) is True

    def test_marker_absent_is_not_a_false_positive(self) -> None:
        messages = [{"role": "system", "content": [{"type": "text", "text": "stable"}]}]
        assert should_stand_down(messages, None, None) is False


class TestCount:
    def test_counts_every_marker_across_messages_system_and_tools(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}},
                ],
            }
        ]
        system = [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
        tools = [{"name": "a", "cache_control": {"type": "ephemeral"}}]
        assert count_existing_breakpoints(messages, system, tools) == 4

    def test_zero_when_nothing_marked(self) -> None:
        messages = [{"role": "user", "content": [{"type": "text", "text": "a"}]}]
        assert count_existing_breakpoints(messages, [{"type": "text", "text": "s"}], [{"name": "t"}]) == 0
