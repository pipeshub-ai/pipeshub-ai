"""Unit tests for `ToolMessage`'s multipart content support
(`app/agent_loop_lib/core/messages.py`) — the extension that lets a tool
result carry images (e.g. internal-knowledge search/fetch surfacing
IMAGE blocks) alongside text, matching `UserMessage`'s existing
`str | list[Part]` shape."""

from __future__ import annotations

from app.agent_loop_lib.core.messages import (
    ImagePart,
    ImageSource,
    TextPart,
    ThinkingPart,
    ToolMessage,
)


class TestToolMessageContent:
    def test_default_content_is_empty_string(self) -> None:
        msg = ToolMessage(tool_call_id="tc1")
        assert msg.content == ""
        assert msg.text == ""

    def test_plain_string_content_round_trips(self) -> None:
        msg = ToolMessage(content="hello world", tool_call_id="tc1")
        assert msg.content == "hello world"
        assert msg.text == "hello world"

    def test_multipart_content_accepts_text_and_image_parts(self) -> None:
        parts = [
            TextPart(text="[ref1] (image)"),
            ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
        ]
        msg = ToolMessage(content=parts, tool_call_id="tc1")
        assert isinstance(msg.content, list)
        assert msg.content == parts

    def test_text_property_concatenates_text_parts_only(self) -> None:
        msg = ToolMessage(
            content=[
                TextPart(text="part one "),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
                TextPart(text="part two"),
            ],
            tool_call_id="tc1",
        )
        assert msg.text == "part one part two"

    def test_text_property_ignores_thinking_parts(self) -> None:
        msg = ToolMessage(
            content=[TextPart(text="visible"), ThinkingPart(thinking="internal reasoning")],
            tool_call_id="tc1",
        )
        assert msg.text == "visible"

    def test_text_property_empty_for_image_only_content(self) -> None:
        msg = ToolMessage(
            content=[ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123"))],
            tool_call_id="tc1",
        )
        assert msg.text == ""

    def test_model_copy_preserves_multipart_content(self) -> None:
        """Context shapers (`shape_tool_result_clearing`, etc.) rely on
        `model_copy(update={...})` to swap content without disturbing
        other fields — confirm multipart content survives an unrelated
        copy untouched."""
        parts = [TextPart(text="hi"), ImagePart(source=ImageSource(type="url", data="https://x/y.png"))]
        msg = ToolMessage(content=parts, tool_call_id="tc1")
        copied = msg.model_copy(update={"is_error": True})
        assert copied.content == parts
        assert copied.is_error is True

    def test_serialization_round_trips_through_model_dump(self) -> None:
        msg = ToolMessage(
            content=[
                TextPart(text="hi"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
            ],
            tool_call_id="tc1",
        )
        dumped = msg.model_dump()
        restored = ToolMessage.model_validate(dumped)
        assert restored.content == msg.content
