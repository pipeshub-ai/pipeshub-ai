"""Unit tests for reasoning token splitting and SSE helpers."""

import pytest

from app.utils.streaming import (
    REASONING_SUMMARY_MAX_CHARS,
    _cap_reasoning_summary,
    _reasoning_chunk_event,
    _reasoning_summary_payload,
    _split_redacted_thinking_string,
    _split_stream_content,
)


class TestSplitStreamContent:
    def test_anthropic_thinking_blocks(self) -> None:
        content = [
            {"type": "thinking", "thinking": "Step one."},
            {"type": "text", "text": "Final answer."},
        ]
        reasoning, answer = _split_stream_content(content)
        assert reasoning == "Step one."
        assert answer == "Final answer."

    def test_redacted_thinking_xml_in_string(self) -> None:
        raw = "<think>hidden</think>Visible answer."
        reasoning, answer = _split_redacted_thinking_string(raw)
        assert reasoning == "hidden"
        assert answer == "Visible answer."

    def test_text_only_string(self) -> None:
        reasoning, answer = _split_stream_content("Hello world")
        assert reasoning == ""
        assert answer == "Hello world"

    def test_cap_reasoning_summary(self) -> None:
        long_text = "x" * (REASONING_SUMMARY_MAX_CHARS + 10)
        capped = _cap_reasoning_summary(long_text)
        assert len(capped) == REASONING_SUMMARY_MAX_CHARS
        assert capped.endswith("…")


class TestReasoningSseHelpers:
    def test_reasoning_chunk_event(self) -> None:
        buf = "alpha beta"
        event, new_len = _reasoning_chunk_event(buf, 0)
        assert new_len == len(buf)
        assert event is not None
        assert event["event"] == "reasoning_chunk"
        assert event["data"]["delta"] == "alpha beta"
        assert event["data"]["accumulated"] == buf

    def test_reasoning_summary_payload_omits_empty(self) -> None:
        assert _reasoning_summary_payload("   ") == {}

    def test_reasoning_summary_payload_includes_text(self) -> None:
        assert _reasoning_summary_payload("thought") == {"reasoningSummary": "thought"}
