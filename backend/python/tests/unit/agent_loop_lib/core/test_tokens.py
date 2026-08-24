"""Unit tests for `app/agent_loop_lib/core/tokens.py`'s `extract_text` /
`count_message_tokens`, specifically the handling of `ToolMessage`'s
multipart `str | list[Part]` content — the known "token counting blind
spot" for images (see the Image Context Engineering plan): image bytes
never count as text tokens, but a multipart `ToolMessage` must not crash
or silently under-report its *text* content either."""

from __future__ import annotations

from app.agent_loop_lib.core.messages import (
    ImagePart,
    ImageSource,
    SystemMessage,
    TextPart,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_message_tokens, extract_text


class TestExtractTextToolMessage:
    def test_plain_string_content(self) -> None:
        msg = ToolMessage(content="hello", tool_call_id="tc1")
        assert extract_text(msg) == "hello"

    def test_multipart_content_extracts_text_parts_only(self) -> None:
        msg = ToolMessage(
            content=[
                TextPart(text="a description"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
            ],
            tool_call_id="tc1",
        )
        assert extract_text(msg) == "a description"

    def test_image_only_content_extracts_empty_string(self) -> None:
        msg = ToolMessage(
            content=[ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123"))],
            tool_call_id="tc1",
        )
        assert extract_text(msg) == ""

    def test_empty_string_content(self) -> None:
        msg = ToolMessage(tool_call_id="tc1")
        assert extract_text(msg) == ""


class TestCountMessageTokensToolMessage:
    def test_string_content_counts_roughly_chars_over_four(self) -> None:
        msg = ToolMessage(content="x" * 40, tool_call_id="tc1")
        assert count_message_tokens(msg) == 4 + (40 // 4)

    def test_image_bytes_do_not_inflate_token_count(self) -> None:
        """A multipart ToolMessage's image payload (base64, can be
        megabytes) must not be counted as text tokens — only its
        TextPart(s) contribute, matching the pre-existing behavior for
        UserMessage attachment images."""
        huge_base64 = "A" * 500_000
        msg = ToolMessage(
            content=[
                TextPart(text="short ref"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data=huge_base64)),
            ],
            tool_call_id="tc1",
        )
        text_only_msg = ToolMessage(content="short ref", tool_call_id="tc1")
        assert count_message_tokens(msg) == count_message_tokens(text_only_msg)

    def test_system_and_user_messages_unaffected(self) -> None:
        assert extract_text(SystemMessage(content="sys")) == "sys"
        assert extract_text(UserMessage(content="hi")) == "hi"
