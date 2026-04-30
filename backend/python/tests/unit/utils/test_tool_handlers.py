"""Tests for app.utils.tool_handlers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.tool_handlers import (
    ContentHandler,
    RecordsHandler,
    ToolHandlerRegistry,
    ToolResultType,
    UrlContentHandler,
    WebSearchHandler,
    _block_attr,
    _block_citation_url,
    normalize_web_search_image_settings,
)


# ---------------------------------------------------------------------------
# normalize_web_search_image_settings
# ---------------------------------------------------------------------------


class TestNormalizeWebSearchImageSettings:
    def test_none_returns_defaults(self) -> None:
        include, max_imgs = normalize_web_search_image_settings(None)
        assert include is False
        assert max_imgs == 3

    def test_empty_dict_returns_defaults(self) -> None:
        include, max_imgs = normalize_web_search_image_settings({})
        assert include is False
        assert max_imgs == 3

    def test_include_images_true(self) -> None:
        include, _ = normalize_web_search_image_settings({"includeImages": True})
        assert include is True

    def test_include_images_false(self) -> None:
        include, _ = normalize_web_search_image_settings({"includeImages": False})
        assert include is False

    def test_include_images_non_bool_ignored(self) -> None:
        include, _ = normalize_web_search_image_settings({"includeImages": "yes"})
        assert include is False  # default, non-bool ignored

    def test_max_images_int(self) -> None:
        _, max_imgs = normalize_web_search_image_settings({"maxImages": 10})
        assert max_imgs == 10

    def test_max_images_string_parsed(self) -> None:
        _, max_imgs = normalize_web_search_image_settings({"maxImages": "7"})
        assert max_imgs == 7

    def test_max_images_invalid_string_uses_default(self) -> None:
        _, max_imgs = normalize_web_search_image_settings({"maxImages": "bad"})
        assert max_imgs == 3

    def test_max_images_zero_uses_default(self) -> None:
        _, max_imgs = normalize_web_search_image_settings({"maxImages": 0})
        assert max_imgs == 3  # 0 < 1, so not applied

    def test_max_images_exceeds_cap_uses_max(self) -> None:
        _, max_imgs = normalize_web_search_image_settings({"maxImages": 600})
        assert max_imgs == 3  # > MAX_WEB_SEARCH_IMAGES (500), so not applied

    def test_max_images_at_cap(self) -> None:
        _, max_imgs = normalize_web_search_image_settings({"maxImages": 500})
        assert max_imgs == 500

    def test_bool_for_max_images_ignored(self) -> None:
        # bool is a subclass of int; True == 1 but should be excluded
        _, max_imgs = normalize_web_search_image_settings({"maxImages": True})
        assert max_imgs == 3  # isinstance(True, bool) is True, so excluded


# ---------------------------------------------------------------------------
# ContentHandler
# ---------------------------------------------------------------------------


class TestContentHandler:
    def test_format_message_returns_content(self) -> None:
        handler = ContentHandler()
        result = asyncio.run(
            handler.format_message({"content": "Hello world"}, {})
        )
        assert result["ok"] is True
        assert result["content"] == "Hello world"

    def test_format_message_falls_back_to_str(self) -> None:
        handler = ContentHandler()
        result = asyncio.run(
            handler.format_message({"other_key": "value"}, {})
        )
        assert result["ok"] is True
        # Falls back to str(tool_result)
        assert "other_key" in result["content"]

    def test_extract_records_returns_empty(self) -> None:
        handler = ContentHandler()
        assert handler.extract_records({}) == []

    def test_needs_token_management_false(self) -> None:
        handler = ContentHandler()
        assert handler.needs_token_management() is False


# ---------------------------------------------------------------------------
# RecordsHandler
# ---------------------------------------------------------------------------


class TestRecordsHandler:
    def test_format_message_with_message_contents(self) -> None:
        handler = RecordsHandler()
        tool_result = {"not_available_ids": {}, "records": []}
        context = {
            "message_contents": [[{"type": "text", "text": "record text"}]]
        }
        result = asyncio.run(
            handler.format_message(tool_result, context)
        )
        assert isinstance(result, list)
        assert any(m["type"] == "text" for m in result)

    def test_format_message_appends_unavailable_note(self) -> None:
        handler = RecordsHandler()
        tool_result = {"not_available_ids": {"id1": "Record 1"}, "records": []}
        context = {"message_contents": []}
        result = asyncio.run(
            handler.format_message(tool_result, context)
        )
        assert any("id1" in m.get("text", "") for m in result)

    def test_format_message_empty_not_available(self) -> None:
        handler = RecordsHandler()
        context = {"message_contents": [[{"type": "text", "text": "hello"}]]}
        result = asyncio.run(
            handler.format_message({"not_available_ids": {}}, context)
        )
        assert len(result) == 1

    def test_extract_records_returns_records(self) -> None:
        handler = RecordsHandler()
        records = [{"id": "1", "content": "text"}]
        result = handler.extract_records({"records": records})
        assert result == records

    def test_extract_records_with_org_id(self) -> None:
        handler = RecordsHandler()
        result = handler.extract_records({"records": [{"id": "1"}]}, org_id="org123")
        assert result == [{"id": "1"}]

    def test_extract_records_empty(self) -> None:
        handler = RecordsHandler()
        assert handler.extract_records({}) == []

    def test_needs_token_management_true(self) -> None:
        handler = RecordsHandler()
        assert handler.needs_token_management() is True


# ---------------------------------------------------------------------------
# WebSearchHandler
# ---------------------------------------------------------------------------


class TestWebSearchHandler:
    def test_format_message_with_results(self) -> None:
        handler = WebSearchHandler()
        tool_result = {
            "query": "test query",
            "web_results": [
                {"title": "T1", "link": "https://t1.com", "snippet": "S1"},
                {"title": "T2", "link": "https://t2.com", "snippet": "S2"},
            ],
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", side_effect=lambda l, s: f"{l}#{s}"), \
             patch("app.utils.tool_handlers.display_url_for_llm", side_effect=lambda u, r: u):
            result = asyncio.run(
                handler.format_message(tool_result, {"ref_mapper": None})
            )

        assert isinstance(result, list)
        # First block is the header
        assert "test query" in result[0]["text"]
        assert len(result) == 3  # header + 2 results

    def test_format_message_with_no_snippet(self) -> None:
        handler = WebSearchHandler()
        tool_result = {
            "query": "q",
            "web_results": [{"title": "T", "link": "https://t.com", "snippet": ""}],
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://t.com"), \
             patch("app.utils.tool_handlers.display_url_for_llm", side_effect=lambda u, r: u):
            result = asyncio.run(
                handler.format_message(tool_result, {"ref_mapper": None})
            )
        assert len(result) == 2  # header + 1 result

    def test_format_message_non_list_web_results(self) -> None:
        handler = WebSearchHandler()
        tool_result = {"query": "q", "web_results": "not a list"}
        result = asyncio.run(
            handler.format_message(tool_result, {})
        )
        assert result[0]["type"] == "text"
        assert len(result) == 1  # only header

    def test_format_message_skips_non_dict_result(self) -> None:
        handler = WebSearchHandler()
        tool_result = {"query": "q", "web_results": ["invalid_string", {"title": "T", "link": "https://t.com", "snippet": "S"}]}
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://t.com#S"), \
             patch("app.utils.tool_handlers.display_url_for_llm", side_effect=lambda u, r: u):
            result = asyncio.run(
                handler.format_message(tool_result, {"ref_mapper": None})
            )
        # Header + 1 valid result (string skipped)
        assert len(result) == 2

    def test_extract_records_with_snippet(self) -> None:
        handler = WebSearchHandler()
        tool_result = {
            "web_results": [
                {"title": "T1", "link": "https://t1.com", "snippet": "S1"},
            ]
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://t1.com#S1"):
            records = handler.extract_records(tool_result, org_id="org1")

        assert len(records) == 1
        assert records[0]["url"] == "https://t1.com#S1"
        assert records[0]["title"] == "T1"
        assert records[0]["source_type"] == "web"
        assert records[0]["org_id"] == "org1"

    def test_extract_records_without_snippet_uses_link(self) -> None:
        handler = WebSearchHandler()
        tool_result = {
            "web_results": [
                {"title": "T1", "link": "https://t1.com", "snippet": ""},
            ]
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://t1.com#S1"):
            records = handler.extract_records(tool_result)
        # No snippet → citation_url = link directly
        assert records[0]["url"] == "https://t1.com"

    def test_extract_records_non_list_returns_empty(self) -> None:
        handler = WebSearchHandler()
        records = handler.extract_records({"web_results": "not-a-list"})
        assert records == []

    def test_extract_records_skips_non_dict(self) -> None:
        handler = WebSearchHandler()
        records = handler.extract_records({"web_results": ["bad", 123]})
        assert records == []

    def test_extract_records_content_fallback(self) -> None:
        handler = WebSearchHandler()
        tool_result = {"web_results": [{"title": "", "link": "https://t.com", "snippet": ""}]}
        records = handler.extract_records(tool_result)
        assert records[0]["content"] == "Search result"


# ---------------------------------------------------------------------------
# _block_attr
# ---------------------------------------------------------------------------


class TestBlockAttr:
    def test_dict_block(self) -> None:
        block = {"type": "text", "content": "hello"}
        assert _block_attr(block, "type") == "text"
        assert _block_attr(block, "content") == "hello"

    def test_dict_block_missing_key_returns_default(self) -> None:
        block = {"type": "text"}
        assert _block_attr(block, "url", "fallback") == "fallback"

    def test_object_block(self) -> None:
        class FakeBlock:
            type = "image"
            url = "https://img.com/img.png"

        assert _block_attr(FakeBlock(), "type") == "image"
        assert _block_attr(FakeBlock(), "url") == "https://img.com/img.png"

    def test_object_block_missing_attr_returns_default(self) -> None:
        class FakeBlock:
            type = "text"

        assert _block_attr(FakeBlock(), "url", "default") == "default"


# ---------------------------------------------------------------------------
# _block_citation_url
# ---------------------------------------------------------------------------


class TestBlockCitationUrl:
    def test_image_block_with_http_url_returns_image_url(self) -> None:
        block = {"type": "image", "url": "https://cdn.com/img.png"}
        result = _block_citation_url("https://page.com", block)
        assert result == "https://cdn.com/img.png"

    def test_image_block_with_relative_url_returns_base(self) -> None:
        block = {"type": "image", "url": "/local/img.png"}
        result = _block_citation_url("https://page.com", block)
        assert result == "https://page.com"

    def test_text_block_generates_fragment_url(self) -> None:
        block = {"type": "text", "content": "some content"}
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://page.com#some"):
            result = _block_citation_url("https://page.com", block)
        assert result == "https://page.com#some"

    def test_image_block_empty_url_returns_base(self) -> None:
        block = {"type": "image", "url": ""}
        result = _block_citation_url("https://page.com", block)
        assert result == "https://page.com"


# ---------------------------------------------------------------------------
# UrlContentHandler.extract_records
# ---------------------------------------------------------------------------


class TestUrlContentHandlerExtractRecords:
    def test_text_blocks_extracted(self) -> None:
        handler = UrlContentHandler()
        tool_result = {
            "url": "https://page.com",
            "blocks": [
                {"type": "text", "content": "Hello world"},
            ],
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://page.com#Hello"):
            records = handler.extract_records(tool_result, org_id="org1")

        assert len(records) == 1
        assert records[0]["content"] == "Hello world"
        assert records[0]["source_type"] == "web"
        assert records[0]["org_id"] == "org1"

    def test_image_blocks_use_alt_text(self) -> None:
        handler = UrlContentHandler()
        tool_result = {
            "url": "https://page.com",
            "blocks": [
                {"type": "image", "url": "https://cdn.com/img.png", "alt": "A photo"},
            ],
        }
        records = handler.extract_records(tool_result)
        assert len(records) == 1
        assert records[0]["content"] == "A photo"

    def test_image_block_no_alt_uses_image_default(self) -> None:
        handler = UrlContentHandler()
        tool_result = {
            "url": "https://page.com",
            "blocks": [
                {"type": "image", "url": "https://cdn.com/img.png", "alt": ""},
            ],
        }
        records = handler.extract_records(tool_result)
        assert records[0]["content"] == "Image"

    def test_empty_blocks_returns_empty(self) -> None:
        handler = UrlContentHandler()
        tool_result = {"url": "https://page.com", "blocks": []}
        records = handler.extract_records(tool_result)
        assert records == []


# ---------------------------------------------------------------------------
# UrlContentHandler.format_message (async)
# ---------------------------------------------------------------------------


class TestUrlContentHandlerFormatMessage:
    def _make_config_service(self, settings: dict | None = None) -> AsyncMock:
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"settings": settings or {}})
        return config_service

    def test_text_only_blocks_formatted(self) -> None:
        handler = UrlContentHandler()
        tool_result = {
            "url": "https://page.com",
            "blocks": [{"type": "text", "content": "Hello"}],
        }
        context = {
            "config_service": self._make_config_service(),
            "ref_mapper": None,
            "is_multimodal_llm": False,
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://page.com#Hello"), \
             patch("app.utils.tool_handlers.display_url_for_llm", side_effect=lambda u, r: u):
            result = asyncio.run(
                handler.format_message(tool_result, context)
            )

        assert isinstance(result, list)
        assert result[0]["text"] == "Blocks of content from the URL:"
        assert any("Hello" in b.get("text", "") for b in result)

    def test_images_skipped_when_not_multimodal(self) -> None:
        handler = UrlContentHandler()
        tool_result = {
            "url": "https://page.com",
            "blocks": [{"type": "image", "url": "https://cdn.com/img.png", "alt": "pic"}],
        }
        context = {
            "config_service": self._make_config_service({"includeImages": True}),
            "ref_mapper": None,
            "is_multimodal_llm": False,
        }
        result = asyncio.run(
            handler.format_message(tool_result, context)
        )
        # No image blocks should be in output when not multimodal
        assert not any(b.get("type") == "image" for b in result)

    def test_empty_blocks_returns_header_only(self) -> None:
        handler = UrlContentHandler()
        tool_result = {"url": "https://page.com", "blocks": []}
        context = {
            "config_service": self._make_config_service(),
            "ref_mapper": None,
            "is_multimodal_llm": False,
        }
        result = asyncio.run(
            handler.format_message(tool_result, context)
        )
        assert len(result) == 1
        assert result[0]["text"] == "Blocks of content from the URL:"

    def test_data_uri_image_included_when_multimodal(self) -> None:
        handler = UrlContentHandler()
        data_uri = "data:image/png;base64,iVBORw0KGgo="
        tool_result = {
            "url": "https://page.com",
            "blocks": [{"type": "image", "url": data_uri, "alt": ""}],
        }
        context = {
            "config_service": self._make_config_service({"includeImages": True}),
            "ref_mapper": None,
            "is_multimodal_llm": True,
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://page.com#"), \
             patch("app.utils.tool_handlers.display_url_for_llm", side_effect=lambda u, r: u), \
             patch("app.utils.tool_handlers.supported_mime_types", ["image/png"]):
            result = asyncio.run(
                handler.format_message(tool_result, context)
            )
        image_blocks = [b for b in result if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["mime_type"] == "image/png"

    def test_unsupported_data_uri_mime_skipped(self) -> None:
        handler = UrlContentHandler()
        data_uri = "data:image/svg+xml;base64,abc123"
        tool_result = {
            "url": "https://page.com",
            "blocks": [{"type": "image", "url": data_uri, "alt": ""}],
        }
        context = {
            "config_service": self._make_config_service({"includeImages": True}),
            "ref_mapper": None,
            "is_multimodal_llm": True,
        }
        with patch("app.utils.tool_handlers.supported_mime_types", ["image/png", "image/jpeg"]):
            result = asyncio.run(
                handler.format_message(tool_result, context)
            )
        assert not any(b.get("type") == "image" for b in result)

    def test_config_service_returns_non_dict(self) -> None:
        handler = UrlContentHandler()
        tool_result = {
            "url": "https://page.com",
            "blocks": [{"type": "text", "content": "text"}],
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)  # non-dict
        context = {
            "config_service": config_service,
            "ref_mapper": None,
            "is_multimodal_llm": False,
        }
        with patch("app.utils.tool_handlers.generate_text_fragment_url", return_value="https://page.com#text"), \
             patch("app.utils.tool_handlers.display_url_for_llm", side_effect=lambda u, r: u):
            result = asyncio.run(
                handler.format_message(tool_result, context)
            )
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ToolHandlerRegistry
# ---------------------------------------------------------------------------


class TestToolHandlerRegistry:
    def test_list_handlers_returns_registered_types(self) -> None:
        handlers = ToolHandlerRegistry.list_handlers()
        assert ToolResultType.RECORDS.value in handlers
        assert ToolResultType.WEB_SEARCH.value in handlers
        assert ToolResultType.URL_CONTENT.value in handlers
        assert ToolResultType.CONTENT.value in handlers

    def test_get_handler_by_explicit_result_type(self) -> None:
        handler = ToolHandlerRegistry.get_handler({"result_type": "records"})
        assert isinstance(handler, RecordsHandler)

    def test_get_handler_infers_records_from_key(self) -> None:
        handler = ToolHandlerRegistry.get_handler({"records": []})
        assert isinstance(handler, RecordsHandler)

    def test_get_handler_infers_web_search_from_key(self) -> None:
        handler = ToolHandlerRegistry.get_handler({"web_results": []})
        assert isinstance(handler, WebSearchHandler)

    def test_get_handler_unknown_falls_back_to_content(self) -> None:
        handler = ToolHandlerRegistry.get_handler({"something_else": "value"})
        assert isinstance(handler, ContentHandler)

    def test_get_handler_unknown_result_type_falls_back_to_content(self) -> None:
        handler = ToolHandlerRegistry.get_handler({"result_type": "nonexistent"})
        assert isinstance(handler, ContentHandler)

    def test_register_custom_handler(self) -> None:
        class MyHandler(ContentHandler):
            pass

        ToolHandlerRegistry.register("custom_type", MyHandler())
        handler = ToolHandlerRegistry.get_handler({"result_type": "custom_type"})
        assert isinstance(handler, MyHandler)

    def test_list_handlers_includes_custom(self) -> None:
        class AnotherHandler(ContentHandler):
            pass

        ToolHandlerRegistry.register("another_type", AnotherHandler())
        assert "another_type" in ToolHandlerRegistry.list_handlers()
