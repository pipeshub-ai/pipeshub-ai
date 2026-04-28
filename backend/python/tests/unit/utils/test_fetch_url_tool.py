"""Tests for app.utils.fetch_url_tool — LangChain fetch-URL tool factory."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.utils.fetch_url_tool import (
    FetchUrlArgs,
    _resolve_tiny_ref_url,
    create_fetch_url_tool,
    split_long_text,
)
from app.utils.url_fetcher import FetchResult


# ---------------------------------------------------------------------------
# FetchUrlArgs
# ---------------------------------------------------------------------------


class TestFetchUrlArgs:
    def test_valid_url(self) -> None:
        args = FetchUrlArgs(url="https://example.com")
        assert args.url == "https://example.com"

    def test_missing_url_raises(self) -> None:
        with pytest.raises(ValidationError):
            FetchUrlArgs()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# split_long_text
# ---------------------------------------------------------------------------


class TestSplitLongText:
    def test_empty_string_returns_empty_list(self) -> None:
        assert split_long_text("") == []

    def test_none_equivalent_empty(self) -> None:
        # A whitespace-only string splits to non-empty words — confirm it returns it
        result = split_long_text("   ")
        # "   ".split() == [] which is <= 200, so returns ["   "]
        assert result == ["   "]

    def test_short_text_returned_as_single_chunk(self) -> None:
        text = "Hello world. This is short."
        result = split_long_text(text, max_words=200)
        assert result == [text]

    def test_text_exactly_at_limit_returned_as_single_chunk(self) -> None:
        words = ["word"] * 200
        text = " ".join(words)
        result = split_long_text(text, max_words=200)
        assert len(result) == 1
        assert result[0] == text

    def test_long_text_split_at_sentence_boundary(self) -> None:
        # Build text with clear sentence boundaries
        sentence = "This is a sentence with exactly ten words here now. "
        text = sentence * 25  # ~250 words
        result = split_long_text(text, max_words=100)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk.split()) <= 110  # allow slight overflow at boundaries

    def test_no_sentence_boundaries_falls_back_to_word_split(self) -> None:
        # Text with no sentence-ending punctuation
        word = "superlongword"
        text = " ".join([word] * 300)
        result = split_long_text(text, max_words=100)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk.split()) <= 100

    def test_custom_max_words(self) -> None:
        words = ["word"] * 50
        text = " ".join(words)
        result = split_long_text(text, max_words=20)
        assert len(result) >= 2

    def test_preserves_all_words(self) -> None:
        text = "One two three. Four five six. Seven eight nine. Ten eleven."
        result = split_long_text(text, max_words=5)
        combined = " ".join(result)
        for word in ["One", "two", "three", "Four", "five"]:
            assert word in combined

    def test_single_long_sentence_no_boundary(self) -> None:
        """Single sentence longer than max_words with no boundary → word-based split."""
        text = " ".join(["word"] * 500)
        result = split_long_text(text, max_words=200)
        assert len(result) >= 2

    def test_mixed_sentence_endings(self) -> None:
        text = "Hello! Are you there? Yes I am. " * 20
        result = split_long_text(text, max_words=30)
        assert len(result) > 1


# ---------------------------------------------------------------------------
# _resolve_tiny_ref_url
# ---------------------------------------------------------------------------


class TestResolveTinyRefUrl:
    def test_empty_url_returned_unchanged(self) -> None:
        assert _resolve_tiny_ref_url("", None) == ""

    def test_plain_url_returned_unchanged(self) -> None:
        url = "https://example.com/page"
        assert _resolve_tiny_ref_url(url, None) == url

    def test_text_fragment_stripped(self) -> None:
        url = "https://example.com/page#:~:text=hello%20world"
        result = _resolve_tiny_ref_url(url, None)
        assert "#:~:text=" not in result
        assert result == "https://example.com/page"

    def test_text_fragment_stripped_with_no_mapper(self) -> None:
        url = "https://example.com/about#:~:text=mission"
        result = _resolve_tiny_ref_url(url, None)
        assert result == "https://example.com/about"

    def test_tiny_ref_resolved_via_mapper(self) -> None:
        mock_mapper = MagicMock()
        mock_mapper.ref_to_url = {"ref1": "https://real-url.com/page"}

        with patch("app.utils.fetch_url_tool.extract_tiny_ref", return_value="ref1"):
            result = _resolve_tiny_ref_url("https://ref1.xyz", mock_mapper)

        assert result == "https://real-url.com/page"

    def test_tiny_ref_not_in_mapper_returns_original(self) -> None:
        mock_mapper = MagicMock()
        mock_mapper.ref_to_url = {}

        with patch("app.utils.fetch_url_tool.extract_tiny_ref", return_value="ref99"):
            result = _resolve_tiny_ref_url("https://ref99.xyz", mock_mapper)

        assert result == "https://ref99.xyz"

    def test_tiny_ref_with_no_mapper_returns_original(self) -> None:
        with patch("app.utils.fetch_url_tool.extract_tiny_ref", return_value="ref1"):
            result = _resolve_tiny_ref_url("https://ref1.xyz", None)

        assert result == "https://ref1.xyz"

    def test_resolved_url_also_has_fragment_stripped(self) -> None:
        mock_mapper = MagicMock()
        mock_mapper.ref_to_url = {"ref1": "https://real.com/page#:~:text=hello"}

        with patch("app.utils.fetch_url_tool.extract_tiny_ref", return_value="ref1"):
            result = _resolve_tiny_ref_url("https://ref1.xyz", mock_mapper)

        assert result == "https://real.com/page"


# ---------------------------------------------------------------------------
# create_fetch_url_tool
# ---------------------------------------------------------------------------


class TestCreateFetchUrlTool:
    def test_creates_tool_without_mapper(self) -> None:
        tool = create_fetch_url_tool()
        assert tool.name == "fetch_url"

    def test_creates_tool_with_mapper(self) -> None:
        mock_mapper = MagicMock()
        tool = create_fetch_url_tool(ref_mapper=mock_mapper)
        assert tool.name == "fetch_url"

    def test_invalid_scheme_returns_error(self) -> None:
        tool = create_fetch_url_tool()
        result = tool.invoke({"url": "ftp://example.com/file"})
        import json
        data = json.loads(result)
        assert data["ok"] is False
        assert "scheme" in data["error"].lower() or "Invalid" in data["error"]

    def test_no_netloc_returns_error(self) -> None:
        tool = create_fetch_url_tool()
        result = tool.invoke({"url": "https://"})
        import json
        data = json.loads(result)
        assert data["ok"] is False

    def test_unresolved_tiny_ref_returns_error(self) -> None:
        tool = create_fetch_url_tool()
        with patch("app.utils.fetch_url_tool.extract_tiny_ref", return_value="ref1"), \
             patch("app.utils.fetch_url_tool._resolve_tiny_ref_url", return_value="https://ref1.xyz"):
            result = tool.invoke({"url": "https://ref1.xyz"})
        import json
        data = json.loads(result)
        assert data["ok"] is False

    def test_fetch_url_success_with_blocks(self) -> None:
        mock_resp = FetchResult(
            status_code=200,
            text="<html><body><p>Hello world</p></body></html>",
            content=b"...",
            headers={},
            url="https://example.com",
            strategy="requests",
        )

        mock_block = MagicMock()
        mock_block_dict = {"type": "paragraph", "content": "Hello world"}

        with patch("app.utils.fetch_url_tool.fetch_url", return_value=mock_resp), \
             patch("app.utils.fetch_url_tool.html_to_blocks", return_value=[mock_block]), \
             patch("dataclasses.asdict", return_value=mock_block_dict):
            tool = create_fetch_url_tool()
            result = tool.invoke({"url": "https://example.com"})

        import json
        data = json.loads(result)
        assert data["ok"] is True
        assert data["url"] == "https://example.com"
        assert len(data["blocks"]) == 1

    def test_fetch_url_non_200_response(self) -> None:
        mock_resp = FetchResult(
            status_code=403,
            text="Forbidden",
            content=b"Forbidden",
            headers={},
            url="https://example.com",
            strategy="requests",
        )

        with patch("app.utils.fetch_url_tool.fetch_url", return_value=mock_resp):
            tool = create_fetch_url_tool()
            result = tool.invoke({"url": "https://example.com/secret"})

        import json
        data = json.loads(result)
        assert data["ok"] is False
        assert "403" in data["error"]

    def test_fetch_url_empty_blocks_returns_error(self) -> None:
        mock_resp = FetchResult(
            status_code=200,
            text="<html></html>",
            content=b"<html></html>",
            headers={},
            url="https://example.com",
            strategy="requests",
        )

        with patch("app.utils.fetch_url_tool.fetch_url", return_value=mock_resp), \
             patch("app.utils.fetch_url_tool.html_to_blocks", return_value=[]):
            tool = create_fetch_url_tool()
            result = tool.invoke({"url": "https://example.com"})

        import json
        data = json.loads(result)
        assert data["ok"] is False
        assert "No readable content" in data["error"]

    def test_fetch_url_exception_returns_error(self) -> None:
        with patch("app.utils.fetch_url_tool.fetch_url", side_effect=RuntimeError("network failure")):
            tool = create_fetch_url_tool()
            result = tool.invoke({"url": "https://example.com"})

        import json
        data = json.loads(result)
        assert data["ok"] is False
        assert "network failure" in data["error"]

    def test_fetch_url_with_ref_mapper_resolves_url(self) -> None:
        mock_mapper = MagicMock()
        mock_mapper.ref_to_url = {}

        mock_resp = FetchResult(
            status_code=200,
            text="<html><body><p>content</p></body></html>",
            content=b"...",
            headers={},
            url="https://real.com",
            strategy="requests",
        )
        mock_block = MagicMock()

        with patch("app.utils.fetch_url_tool.extract_tiny_ref", return_value=None), \
             patch("app.utils.fetch_url_tool.fetch_url", return_value=mock_resp), \
             patch("app.utils.fetch_url_tool.html_to_blocks", return_value=[mock_block]), \
             patch("dataclasses.asdict", return_value={"type": "paragraph", "content": "content"}):
            tool = create_fetch_url_tool(ref_mapper=mock_mapper)
            result = tool.invoke({"url": "https://real.com/page"})

        import json
        data = json.loads(result)
        assert data["ok"] is True
