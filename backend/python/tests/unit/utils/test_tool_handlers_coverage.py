"""
Additional coverage tests for app.utils.tool_handlers

Targets:
- normalize_web_search_image_settings (all branches)
- ToolResultType enum
- ContentHandler.build_tool_instructions
- RecordsHandler
- WebSearchHandler
- _block_attr / _block_citation_url helpers
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.tool_handlers import (
    ContentHandler,
    RecordsHandler,
    ToolResultType,
    WebSearchHandler,
    _block_attr,
    _block_citation_url,
    normalize_web_search_image_settings,
    DEFAULT_WEB_SEARCH_INCLUDE_IMAGES,
    DEFAULT_WEB_SEARCH_MAX_IMAGES,
    MAX_WEB_SEARCH_IMAGES,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# normalize_web_search_image_settings
# ---------------------------------------------------------------------------

class TestNormalizeWebSearchImageSettings:
    def test_none_returns_defaults(self):
        include, max_img = normalize_web_search_image_settings(None)
        assert include is DEFAULT_WEB_SEARCH_INCLUDE_IMAGES
        assert max_img == DEFAULT_WEB_SEARCH_MAX_IMAGES

    def test_empty_dict_returns_defaults(self):
        include, max_img = normalize_web_search_image_settings({})
        assert include is DEFAULT_WEB_SEARCH_INCLUDE_IMAGES
        assert max_img == DEFAULT_WEB_SEARCH_MAX_IMAGES

    def test_include_images_true(self):
        include, _ = normalize_web_search_image_settings({"includeImages": True})
        assert include is True

    def test_include_images_false(self):
        include, _ = normalize_web_search_image_settings({"includeImages": False})
        assert include is False

    def test_include_images_non_bool_ignored(self):
        include, _ = normalize_web_search_image_settings({"includeImages": "yes"})
        assert include is DEFAULT_WEB_SEARCH_INCLUDE_IMAGES

    def test_max_images_int(self):
        _, max_img = normalize_web_search_image_settings({"maxImages": 10})
        assert max_img == 10

    def test_max_images_string_parseable(self):
        _, max_img = normalize_web_search_image_settings({"maxImages": "5"})
        assert max_img == 5

    def test_max_images_string_non_parseable(self):
        _, max_img = normalize_web_search_image_settings({"maxImages": "abc"})
        assert max_img == DEFAULT_WEB_SEARCH_MAX_IMAGES

    def test_max_images_below_range(self):
        _, max_img = normalize_web_search_image_settings({"maxImages": 0})
        assert max_img == DEFAULT_WEB_SEARCH_MAX_IMAGES

    def test_max_images_above_range(self):
        _, max_img = normalize_web_search_image_settings({"maxImages": 9999})
        assert max_img == DEFAULT_WEB_SEARCH_MAX_IMAGES

    def test_max_images_at_boundary_1(self):
        _, max_img = normalize_web_search_image_settings({"maxImages": 1})
        assert max_img == 1

    def test_max_images_at_boundary_max(self):
        _, max_img = normalize_web_search_image_settings({"maxImages": MAX_WEB_SEARCH_IMAGES})
        assert max_img == MAX_WEB_SEARCH_IMAGES

    def test_bool_true_not_treated_as_int(self):
        # In Python, bool is subclass of int. True == 1. Ensure it's excluded.
        _, max_img = normalize_web_search_image_settings({"maxImages": True})
        assert max_img == DEFAULT_WEB_SEARCH_MAX_IMAGES

    def test_combined_settings(self):
        include, max_img = normalize_web_search_image_settings({
            "includeImages": True,
            "maxImages": 7,
        })
        assert include is True
        assert max_img == 7


# ---------------------------------------------------------------------------
# ToolResultType enum
# ---------------------------------------------------------------------------

class TestToolResultType:
    def test_all_values(self):
        assert ToolResultType.RECORDS.value == "records"
        assert ToolResultType.WEB_SEARCH.value == "web_search"
        assert ToolResultType.URL_CONTENT.value == "url_content"
        assert ToolResultType.CONTENT.value == "content"

    def test_is_str_enum(self):
        assert isinstance(ToolResultType.RECORDS, str)
        assert ToolResultType.RECORDS == "records"


# ---------------------------------------------------------------------------
# ContentHandler
# ---------------------------------------------------------------------------

class TestContentHandler:
    def test_format_message_with_list_content(self):
        handler = ContentHandler()
        tool_result = {"content": [{"type": "text", "text": "hello"}]}
        result = _run(handler.format_message(tool_result, {}))
        assert result[0] == {"type": "text", "text": "Internal Knowledge Search results:"}
        assert result[1] == {"type": "text", "text": "hello"}

    def test_format_message_with_string_content(self):
        handler = ContentHandler()
        tool_result = {"content": "plain text"}
        result = _run(handler.format_message(tool_result, {}))
        assert {"type": "text", "text": "Internal Knowledge Search results:"} in result
        assert "plain text" in result

    def test_format_message_no_content_key(self):
        handler = ContentHandler()
        tool_result = {"other": "data"}
        result = _run(handler.format_message(tool_result, {}))
        # Falls back to str(tool_result)
        assert isinstance(result, str)

    def test_build_tool_instructions_without_sql(self):
        instructions = ContentHandler.build_tool_instructions(has_sql_connector=False)
        assert "fetch_full_record" in instructions
        assert "execute_sql_query" not in instructions

    def test_build_tool_instructions_with_sql(self):
        instructions = ContentHandler.build_tool_instructions(has_sql_connector=True)
        assert "fetch_full_record" in instructions
        assert "execute_sql_query" in instructions

    def test_extract_records_returns_empty(self):
        handler = ContentHandler()
        assert handler.extract_records({}) == []

    def test_needs_token_management_false(self):
        handler = ContentHandler()
        assert handler.needs_token_management() is False


# ---------------------------------------------------------------------------
# RecordsHandler
# ---------------------------------------------------------------------------

class TestRecordsHandler:
    def test_format_message_with_content(self):
        handler = RecordsHandler()
        context = {
            "message_contents": [[{"type": "text", "text": "block1"}]],
        }
        result = _run(handler.format_message({}, context))
        assert result == [{"type": "text", "text": "block1"}]

    def test_format_message_with_not_available(self):
        handler = RecordsHandler()
        context = {"message_contents": []}
        tool_result = {"not_available_ids": {"id1": True, "id2": True}}
        result = _run(handler.format_message(tool_result, context))
        text = result[0]["text"]
        assert "not available" in text

    def test_extract_records(self):
        handler = RecordsHandler()
        tool_result = {"records": [{"_id": "r1"}, {"_id": "r2"}]}
        records = handler.extract_records(tool_result)
        assert len(records) == 2

    def test_extract_records_empty(self):
        handler = RecordsHandler()
        assert handler.extract_records({}) == []

    def test_needs_token_management_true(self):
        handler = RecordsHandler()
        assert handler.needs_token_management() is True


# ---------------------------------------------------------------------------
# WebSearchHandler
# ---------------------------------------------------------------------------

class TestWebSearchHandler:
    def test_format_message_basic(self):
        handler = WebSearchHandler()
        tool_result = {
            "query": "test query",
            "web_results": [
                {"title": "Result 1", "link": "https://example.com", "snippet": "Some info"},
            ],
        }
        context = {"ref_mapper": None}
        result = _run(handler.format_message(tool_result, context))
        assert any("test query" in b.get("text", "") for b in result)
        assert any("Result 1" in b.get("text", "") for b in result)
        assert any("Some info" in b.get("text", "") for b in result)

    def test_format_message_non_list_web_results(self):
        handler = WebSearchHandler()
        tool_result = {"query": "q", "web_results": "not a list"}
        result = _run(handler.format_message(tool_result, {"ref_mapper": None}))
        # Should only have the header block
        assert len(result) == 1

    def test_format_message_invalid_items_skipped(self):
        handler = WebSearchHandler()
        tool_result = {
            "query": "q",
            "web_results": ["not a dict", None, {"title": "Good", "link": "http://x.com", "snippet": "s"}],
        }
        result = _run(handler.format_message(tool_result, {"ref_mapper": None}))
        assert len(result) == 2  # header + one valid result

    def test_extract_records(self):
        handler = WebSearchHandler()
        tool_result = {
            "web_results": [
                {"title": "Page", "link": "http://x.com", "snippet": "content"},
            ],
        }
        records = handler.extract_records(tool_result, org_id="org1")
        assert len(records) == 1
        assert records[0]["title"] == "Page"
        assert records[0]["source_type"] == "web"
        assert records[0]["org_id"] == "org1"

    def test_extract_records_non_list(self):
        handler = WebSearchHandler()
        assert handler.extract_records({"web_results": "invalid"}) == []

    def test_extract_records_empty(self):
        handler = WebSearchHandler()
        assert handler.extract_records({}) == []


# ---------------------------------------------------------------------------
# _block_attr
# ---------------------------------------------------------------------------

class TestBlockAttr:
    def test_dict_access(self):
        block = {"type": "text", "content": "hello"}
        assert _block_attr(block, "type") == "text"
        assert _block_attr(block, "content") == "hello"
        assert _block_attr(block, "missing") == ""

    def test_dict_default(self):
        assert _block_attr({}, "key", "default") == "default"

    def test_dataclass_access(self):
        class FakeBlock:
            type = "image"
            url = "http://img.com/a.png"
        assert _block_attr(FakeBlock(), "type") == "image"
        assert _block_attr(FakeBlock(), "url") == "http://img.com/a.png"
        assert _block_attr(FakeBlock(), "missing") == ""

    def test_dataclass_default(self):
        class FakeBlock:
            pass
        assert _block_attr(FakeBlock(), "x", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# _block_citation_url
# ---------------------------------------------------------------------------

class TestBlockCitationUrl:
    def test_text_block_generates_fragment_url(self):
        block = {"type": "text", "content": "important text"}
        result = _block_citation_url("https://example.com", block)
        assert "example.com" in result

    def test_image_block_with_http_url(self):
        block = {"type": "image", "url": "https://cdn.com/img.png"}
        result = _block_citation_url("https://page.com", block)
        assert result == "https://cdn.com/img.png"

    def test_image_block_without_http_url(self):
        block = {"type": "image", "url": "data:image/png;base64,abc"}
        result = _block_citation_url("https://page.com", block)
        assert result == "https://page.com"

    def test_image_block_empty_url(self):
        block = {"type": "image", "url": ""}
        result = _block_citation_url("https://page.com", block)
        assert result == "https://page.com"
