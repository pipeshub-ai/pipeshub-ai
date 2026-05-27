"""
Additional coverage tests for app.utils.chat_helpers

Targets:
- build_multimodal_user_content async function and its branches
- _safe_stringify_content exception branch
- count_tokens_in_content_list
- enrich_virtual_record_id_to_result_with_fk_children
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.chat_helpers import (
    _safe_stringify_content,
    build_multimodal_user_content,
    count_tokens_in_content_list,
    enrich_virtual_record_id_to_result_with_fk_children,
)


def _run(coro):
    return asyncio.run(coro)


class TestBuildMultimodalUserContent:
    def test_no_attachments_returns_text(self):
        result = _run(build_multimodal_user_content(
            "Hello", [], MagicMock(), "org1"
        ))
        assert result == "Hello"

    def test_no_blob_store_returns_text(self):
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, None, "org1"
        ))
        assert result == "Hello"

    def test_no_org_id_returns_text(self):
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, MagicMock(), ""
        ))
        assert result == "Hello"

    def test_non_image_attachments_returns_text(self):
        attachments = [{"mimeType": "application/pdf", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, MagicMock(), "org1"
        ))
        assert result == "Hello"

    def test_image_attachment_fetched_successfully(self):
        # 1x1 PNG base64 data URI
        png_data_uri = (
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {
                "blocks": [
                    {"type": "image", "data": {"uri": png_data_uri}},
                ],
            },
        })
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "See this", attachments, mock_blob, "org1"
        ))
        # Should return a list with text + image_url blocks
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "See this"
        assert result[1]["type"] == "image_url"

    def test_no_record_found_returns_text(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value=None)
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, mock_blob, "org1"
        ))
        assert result == "Hello"

    def test_no_virtual_record_id_skipped(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value=None)
        attachments = [{"mimeType": "image/png"}]  # no virtualRecordId
        result = _run(build_multimodal_user_content(
            "Hello", attachments, mock_blob, "org1"
        ))
        assert result == "Hello"

    def test_non_image_block_type_skipped(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {
                "blocks": [
                    {"type": "text", "data": {"content": "not an image"}},
                ],
            },
        })
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, mock_blob, "org1"
        ))
        assert result == "Hello"

    def test_string_data_field(self):
        png_data_uri = (
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {
                "blocks": [
                    {"type": "image", "data": png_data_uri},
                ],
            },
        })
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Check", attachments, mock_blob, "org1"
        ))
        assert isinstance(result, list)
        assert any(b.get("type") == "image_url" for b in result)

    def test_exception_during_fetch_handled(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(side_effect=Exception("Network error"))
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, mock_blob, "org1"
        ))
        # Should fall back to plain text on error
        assert result == "Hello"

    def test_non_base64_uri_skipped(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {
                "blocks": [
                    {"type": "image", "data": {"uri": "https://example.com/img.png"}},
                ],
            },
        })
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, mock_blob, "org1"
        ))
        # Non-base64 URI is not included
        assert result == "Hello"

    def test_empty_block_containers(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {},
        })
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(build_multimodal_user_content(
            "Hello", attachments, mock_blob, "org1"
        ))
        assert result == "Hello"

    def test_multiple_image_attachments(self):
        png_data_uri = (
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {
                "blocks": [{"type": "image", "data": {"uri": png_data_uri}}],
            },
        })
        attachments = [
            {"mimeType": "image/png", "virtualRecordId": "vr1"},
            {"mimeType": "image/jpeg", "virtualRecordId": "vr2"},
        ]
        result = _run(build_multimodal_user_content(
            "Images", attachments, mock_blob, "org1"
        ))
        assert isinstance(result, list)
        image_blocks = [b for b in result if b.get("type") == "image_url"]
        assert len(image_blocks) == 2


# ---------------------------------------------------------------------------
# count_tokens_in_content_list
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _safe_stringify_content
# ---------------------------------------------------------------------------

class TestSafeStringifyContent:
    def test_normal_value(self):
        assert _safe_stringify_content("hello") == "hello"

    def test_integer(self):
        assert _safe_stringify_content(42) == "42"

    def test_none(self):
        assert _safe_stringify_content(None) == "None"

    def test_exception_returns_empty(self):
        class BadStr:
            def __str__(self):
                raise RuntimeError("cannot stringify")
        result = _safe_stringify_content(BadStr())
        assert result == ""


class TestCountTokensInContentList:
    def test_empty_list(self):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        assert count_tokens_in_content_list([], enc) == 0

    def test_text_items_counted(self):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        content = [
            {"type": "text", "text": "Hello world"},
            {"type": "text", "text": "Another line"},
        ]
        result = count_tokens_in_content_list(content, enc)
        assert result > 0

    def test_non_text_items_skipped(self):
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        assert count_tokens_in_content_list(content, enc) == 0


# ---------------------------------------------------------------------------
# enrich_virtual_record_id_to_result_with_fk_children
# ---------------------------------------------------------------------------

class TestEnrichVirtualRecordIdToResultWithFkChildren:
    def test_empty_inputs_no_crash(self):
        mock_blob = MagicMock()
        mock_blob.get_fk_relations = AsyncMock(return_value={})
        _run(enrich_virtual_record_id_to_result_with_fk_children({}, mock_blob, "org1"))

    def test_with_records_no_fk(self):
        mock_blob = MagicMock()
        mock_blob.get_fk_relations = AsyncMock(return_value={})
        vmap = {"vr1": {"record_name": "table1"}}
        _run(enrich_virtual_record_id_to_result_with_fk_children(vmap, mock_blob, "org1"))
        assert "fk_parent_record_ids" not in vmap.get("vr1", {})
