"""Unit tests for StreamingJsonStringExtractor.

Covers adversarial fragmentation including escape sequences split across
fragment boundaries, multi-call-chunk scenarios, and wrong-key scenarios.
"""
from __future__ import annotations

import pytest

from app.agent_loop_lib.core.json_stream import StreamingJsonStringExtractor


def _feed_all(key: str, fragments: list[str]) -> str:
    """Helper: feed all fragments and return concatenated output."""
    extractor = StreamingJsonStringExtractor(key)
    return "".join(extractor.feed(f) for f in fragments)


class TestBasicExtraction:
    def test_single_fragment_full_json(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "Hello world"}'])
        assert result == "Hello world"

    def test_key_missing(self) -> None:
        result = _feed_all("answer_markdown", ['{"other_key": "value"}'])
        assert result == ""

    def test_empty_string_value(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": ""}'])
        assert result == ""

    def test_done_flag_set(self) -> None:
        ext = StreamingJsonStringExtractor("answer_markdown")
        ext.feed('{"answer_markdown": "Hi"}')
        assert ext.done is True

    def test_done_flag_not_set_while_scanning(self) -> None:
        ext = StreamingJsonStringExtractor("answer_markdown")
        ext.feed('{"answer_markdown": "Hello')
        assert ext.done is False

    def test_feed_after_done_is_noop(self) -> None:
        ext = StreamingJsonStringExtractor("answer_markdown")
        ext.feed('{"answer_markdown": "Hi"}')
        assert ext.done
        result = ext.feed(' more text')
        assert result == ""


class TestFragmentedDelivery:
    def test_value_split_across_fragments(self) -> None:
        frags = ['{"answer_mark', 'down": "Hello', ' wor', 'ld"}']
        assert _feed_all("answer_markdown", frags) == "Hello world"

    def test_key_split_across_fragments(self) -> None:
        frags = ['{"ans', 'wer_', 'mark', 'down', '": "text"}']
        assert _feed_all("answer_markdown", frags) == "text"

    def test_colon_and_quote_split(self) -> None:
        frags = ['{"answer_markdown"', ':', '"value"}']
        assert _feed_all("answer_markdown", frags) == "value"

    def test_one_char_per_fragment(self) -> None:
        json_str = '{"answer_markdown": "AB"}'
        frags = list(json_str)
        assert _feed_all("answer_markdown", frags) == "AB"

    def test_many_fields_before_target(self) -> None:
        frags = ['{"sources": ["a", "b"], "confidence": "high", "answer_markdown": "final"}']
        assert _feed_all("answer_markdown", frags) == "final"


class TestEscapeSequences:
    def test_newline_escape(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "line1\\nline2"}'])
        assert result == "line1\nline2"

    def test_tab_escape(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "col1\\tcol2"}'])
        assert result == "col1\tcol2"

    def test_backslash_escape(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "a\\\\b"}'])
        assert result == "a\\b"

    def test_quote_escape(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "say \\"hi\\""}'])
        assert result == 'say "hi"'

    def test_unicode_escape(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "\\u00e9"}'])
        assert result == "é"

    def test_escape_split_across_fragment_boundary(self) -> None:
        # The backslash arrives in one fragment, the escape character in the next.
        frags = ['{"answer_markdown": "a\\', 'nb"}']
        assert _feed_all("answer_markdown", frags) == "a\nb"

    def test_unicode_escape_split_at_each_digit(self) -> None:
        # \u00e9 split as: \u | 0 | 0 | e | 9
        frags = ['{"answer_markdown": "', '\\u', '0', '0', 'e', '9', '"}']
        assert _feed_all("answer_markdown", frags) == "é"

    def test_unicode_escape_split_mid_hex(self) -> None:
        # \u00e9 split as: \u00 | e9
        frags = ['{"answer_markdown": "\\u00', 'e9"}']
        assert _feed_all("answer_markdown", frags) == "é"

    def test_carriage_return_escape(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "a\\rb"}'])
        assert result == "a\rb"

    def test_slash_escape(self) -> None:
        result = _feed_all("answer_markdown", ['{"answer_markdown": "https:\\/\\/example.com"}'])
        assert result == "https://example.com"


class TestMultipleKeys:
    def test_extracts_target_key_not_other_string_keys(self) -> None:
        json_str = '{"confidence": "high", "answer_markdown": "The answer"}'
        assert _feed_all("answer_markdown", [json_str]) == "The answer"
        # confidence is a different key — extracting it yields "high", not "The answer"
        assert _feed_all("confidence", [json_str]) == "high"

    def test_stops_at_closing_quote(self) -> None:
        # Extra content after the value should be ignored
        frags = ['{"answer_markdown": "done", "extra": "ignored"}']
        assert _feed_all("answer_markdown", frags) == "done"


class TestEdgeCases:
    def test_multiline_markdown(self) -> None:
        md = "# Title\\n\\nSome paragraph.\\n\\n- item1\\n- item2"
        frags = [f'{{"answer_markdown": "{md}"}}']
        result = _feed_all("answer_markdown", frags)
        assert "Title" in result
        assert "item1" in result

    def test_long_value_split_many_ways(self) -> None:
        value = "A" * 1000
        json_str = f'{{"answer_markdown": "{value}"}}'
        # Split into 100-char chunks
        frags = [json_str[i:i + 100] for i in range(0, len(json_str), 100)]
        assert _feed_all("answer_markdown", frags) == value

    def test_key_prefix_not_matched(self) -> None:
        # "other_answer_markdown" should not match "answer_markdown" mid-prefix
        result = _feed_all("answer_markdown", ['{"other_answer_markdown": "no", "answer_markdown": "yes"}'])
        assert result == "yes"

    def test_empty_fragments(self) -> None:
        frags = ["", '{"answer_markdown": "hi"}', ""]
        assert _feed_all("answer_markdown", frags) == "hi"

    def test_invalid_unicode_escape_falls_back(self) -> None:
        # \uXXZZ is invalid hex — should not crash, may emit raw sequence
        frags = ['{"answer_markdown": "\\uXXZZ!"}']
        result = _feed_all("answer_markdown", frags)
        assert "!" in result  # the '!' after the escape must be emitted

    def test_ollama_single_chunk_scenario(self) -> None:
        """Ollama delivers tool calls whole — extractor handles it in one feed call."""
        full_json = '{"answer_markdown": "The complete answer arrives at once.", "confidence": "high"}'
        ext = StreamingJsonStringExtractor("answer_markdown")
        result = ext.feed(full_json)
        assert result == "The complete answer arrives at once."
        assert ext.done
