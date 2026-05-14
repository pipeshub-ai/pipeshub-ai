"""Unit tests for app.utils.attachment_utils."""
from __future__ import annotations

import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.attachment_utils import (
    _extract_image_blocks,
    build_multimodal_content,
    ensure_attachment_blocks,
    inject_attachment_blocks,
    resolve_attachments,
    resolve_pdf_blocks_simple,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_PNG_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_LOGGER = logging.getLogger("test_attachment_utils")


def _make_image_record(uri: str = _PNG_URI) -> dict:
    return {
        "block_containers": {
            "blocks": [{"type": "image", "data": {"uri": uri}}]
        }
    }


class _HM:
    """Minimal HumanMessage stand-in that stores content as a real attribute."""

    def __init__(self, content=""):
        if isinstance(content, list):
            self.content = list(content)
        else:
            self.content = content


@pytest.fixture(autouse=False)
def patch_human_message():
    """Replace the mock HumanMessage with a real class.

    inject_attachment_blocks does ``from langchain_core.messages import HumanMessage``
    inside the function body, which resolves via sys.modules["langchain_core.messages"].
    We patch that specific entry so isinstance() checks work correctly.
    """
    with patch("langchain_core.messages.HumanMessage", _HM):
        yield _HM


# ---------------------------------------------------------------------------
# build_multimodal_content
# ---------------------------------------------------------------------------

class TestBuildMultimodalContent:
    def test_empty_blocks_returns_text_unchanged(self):
        assert build_multimodal_content("hello", []) == "hello"

    def test_none_blocks_returns_text_unchanged(self):
        assert build_multimodal_content("hello", None) == "hello"

    def test_with_blocks_returns_list(self):
        img_block = {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}
        result = build_multimodal_content("query", [img_block])
        assert isinstance(result, list)
        assert result[0] == {"type": "text", "text": "query"}
        assert result[-1] == img_block

    def test_separator_block_inserted(self):
        img_block = {"type": "image_url", "image_url": {"url": _PNG_URI}}
        result = build_multimodal_content("q", [img_block])
        text_blocks = [b for b in result if b.get("type") == "text"]
        assert len(text_blocks) >= 2

    def test_multiple_blocks_all_appended(self):
        blocks = [{"type": "image_url", "image_url": {"url": _PNG_URI}}] * 3
        result = build_multimodal_content("q", blocks)
        image_blocks = [b for b in result if b.get("type") == "image_url"]
        assert len(image_blocks) == 3


# ---------------------------------------------------------------------------
# inject_attachment_blocks
# ---------------------------------------------------------------------------

class TestInjectAttachmentBlocks:
    def test_noop_when_blocks_empty(self, patch_human_message):
        msgs = [patch_human_message(content="hello")]
        inject_attachment_blocks(msgs, [])
        assert msgs[0].content == "hello"

    def test_noop_when_messages_empty(self, patch_human_message):
        inject_attachment_blocks([], [{"type": "image_url", "image_url": {"url": _PNG_URI}}])

    def test_noop_when_last_message_not_human(self, patch_human_message):
        class _NotHuman:
            content = "ai response"
        msgs = [_NotHuman()]
        original = msgs[-1]
        blocks = [{"type": "image_url", "image_url": {"url": _PNG_URI}}]
        inject_attachment_blocks(msgs, blocks)
        # Last message must be unchanged — _NotHuman is not a _HM instance.
        assert msgs[-1] is original
        assert msgs[-1].content == "ai response"

    def test_string_content_replaced_with_multimodal_list(self, patch_human_message):
        msgs = [patch_human_message(content="what is this?")]
        blocks = [{"type": "image_url", "image_url": {"url": _PNG_URI}}]
        inject_attachment_blocks(msgs, blocks)
        assert isinstance(msgs[-1].content, list)
        assert msgs[-1].content[0] == {"type": "text", "text": "what is this?"}

    def test_list_content_appended(self, patch_human_message):
        existing = [{"type": "text", "text": "describe this"}]
        msgs = [patch_human_message(content=existing)]
        blocks = [{"type": "image_url", "image_url": {"url": _PNG_URI}}]
        inject_attachment_blocks(msgs, blocks)
        assert isinstance(msgs[-1].content, list)
        assert msgs[-1].content[0] == {"type": "text", "text": "describe this"}
        assert msgs[-1].content[-1] == blocks[-1]

    def test_non_string_non_list_content_stringified(self, patch_human_message):
        msgs = [patch_human_message(content=42)]
        blocks = [{"type": "image_url", "image_url": {"url": _PNG_URI}}]
        inject_attachment_blocks(msgs, blocks)
        assert isinstance(msgs[-1].content, list)
        assert msgs[-1].content[0]["text"] == "42"

    def test_separator_text_inserted_before_blocks_for_list_content(self, patch_human_message):
        msgs = [patch_human_message(content=[{"type": "text", "text": "q"}])]
        blocks = [{"type": "image_url", "image_url": {"url": _PNG_URI}}]
        inject_attachment_blocks(msgs, blocks)
        texts = [b["text"] for b in msgs[-1].content if isinstance(b, dict) and b.get("type") == "text"]
        assert any("Attached files" in t for t in texts)


# ---------------------------------------------------------------------------
# _extract_image_blocks
# ---------------------------------------------------------------------------

class TestExtractImageBlocks:
    def test_https_uri_yields_image_url_block(self):
        record = _make_image_record("https://example.com/photo.jpg")
        result = _extract_image_blocks(record, "photo", _LOGGER)
        assert result == [{"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}}]

    def test_http_uri_yields_image_url_block(self):
        record = _make_image_record("http://example.com/photo.jpg")
        result = _extract_image_blocks(record, "photo", _LOGGER)
        assert len(result) == 1
        assert result[0]["image_url"]["url"].startswith("http://")

    def test_base64_png_uri_yields_image_url_block(self):
        record = _make_image_record(_PNG_URI)
        result = _extract_image_blocks(record, "img.png", _LOGGER)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    def test_unsupported_uri_format_skipped(self):
        record = _make_image_record("ftp://example.com/image.png")
        result = _extract_image_blocks(record, "img", _LOGGER)
        assert result == []

    def test_non_image_type_block_skipped(self):
        record = {"block_containers": {"blocks": [{"type": "text", "data": {"uri": _PNG_URI}}]}}
        result = _extract_image_blocks(record, "rec", _LOGGER)
        assert result == []

    def test_non_dict_block_skipped(self):
        record = {"block_containers": {"blocks": ["not a dict"]}}
        result = _extract_image_blocks(record, "rec", _LOGGER)
        assert result == []

    def test_non_dict_data_skipped(self):
        record = {"block_containers": {"blocks": [{"type": "image", "data": "not a dict"}]}}
        result = _extract_image_blocks(record, "rec", _LOGGER)
        assert result == []

    def test_missing_uri_skipped(self):
        record = {"block_containers": {"blocks": [{"type": "image", "data": {}}]}}
        result = _extract_image_blocks(record, "rec", _LOGGER)
        assert result == []

    def test_legacy_top_level_blocks_path(self):
        """Records without block_containers fall back to record['blocks']."""
        record = {"blocks": [{"type": "image", "data": {"uri": _PNG_URI}}]}
        result = _extract_image_blocks(record, "img", _LOGGER)
        assert len(result) == 1

    def test_empty_record_returns_empty(self):
        result = _extract_image_blocks({}, "rec", _LOGGER)
        assert result == []

    def test_multiple_image_blocks_returned(self):
        blocks = [
            {"type": "image", "data": {"uri": _PNG_URI}},
            {"type": "image", "data": {"uri": "https://cdn.example.com/pic.jpg"}},
        ]
        record = {"block_containers": {"blocks": blocks}}
        result = _extract_image_blocks(record, "multi", _LOGGER)
        assert len(result) == 2

    def test_block_containers_not_dict_uses_legacy(self):
        record = {
            "block_containers": "not_a_dict",
            "blocks": [{"type": "image", "data": {"uri": _PNG_URI}}],
        }
        result = _extract_image_blocks(record, "rec", _LOGGER)
        assert len(result) == 1

    def test_jpeg_base64_uri_accepted(self):
        uri = "data:image/jpeg;base64,/9j/4AAQSkZJRgAB"
        result = _extract_image_blocks(
            {"block_containers": {"blocks": [{"type": "image", "data": {"uri": uri}}]}},
            "pic",
            _LOGGER,
        )
        assert len(result) == 1

    def test_webp_base64_uri_accepted(self):
        uri = "data:image/webp;base64,UklGRlYAAABXRUJQ"
        result = _extract_image_blocks(
            {"block_containers": {"blocks": [{"type": "image", "data": {"uri": uri}}]}},
            "pic",
            _LOGGER,
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# resolve_pdf_blocks_simple
# ---------------------------------------------------------------------------

class TestResolvePdfBlocksSimple:
    def _record(self, blocks: list) -> dict:
        return {"block_containers": {"blocks": blocks}}

    def test_empty_record_returns_empty(self):
        assert resolve_pdf_blocks_simple({}, False) == []

    def test_text_block_included(self):
        record = self._record([{"type": "text", "data": "hello world"}])
        result = resolve_pdf_blocks_simple(record, False)
        assert result == [{"type": "text", "text": "hello world"}]

    def test_whitespace_only_text_skipped(self):
        record = self._record([{"type": "text", "data": "   "}])
        result = resolve_pdf_blocks_simple(record, False)
        assert result == []

    def test_table_row_with_nl_text(self):
        record = self._record([{"type": "table_row", "data": {"row_natural_language_text": "col1: val"}}])
        result = resolve_pdf_blocks_simple(record, False)
        assert result == [{"type": "text", "text": "col1: val"}]

    def test_table_row_without_nl_text_skipped(self):
        record = self._record([{"type": "table_row", "data": {}}])
        assert resolve_pdf_blocks_simple(record, False) == []

    def test_table_row_non_dict_data_skipped(self):
        record = self._record([{"type": "table_row", "data": "not a dict"}])
        assert resolve_pdf_blocks_simple(record, False) == []

    def test_image_block_included_when_multimodal(self):
        record = self._record([{"type": "image", "data": {"uri": _PNG_URI}}])
        result = resolve_pdf_blocks_simple(record, is_multimodal_llm=True)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    def test_image_block_skipped_when_not_multimodal(self):
        record = self._record([{"type": "image", "data": {"uri": _PNG_URI}}])
        result = resolve_pdf_blocks_simple(record, is_multimodal_llm=False)
        assert result == []

    def test_image_block_non_base64_uri_skipped(self):
        record = self._record([{"type": "image", "data": {"uri": "ftp://invalid"}}])
        result = resolve_pdf_blocks_simple(record, is_multimodal_llm=True)
        assert result == []

    def test_image_block_non_dict_data_skipped_when_multimodal(self):
        record = self._record([{"type": "image", "data": "not_a_dict"}])
        result = resolve_pdf_blocks_simple(record, is_multimodal_llm=True)
        assert result == []

    def test_unknown_block_type_skipped(self):
        record = self._record([{"type": "unknown", "data": "something"}])
        assert resolve_pdf_blocks_simple(record, False) == []

    def test_none_block_containers_returns_empty(self):
        record = {"block_containers": None}
        assert resolve_pdf_blocks_simple(record, False) == []

    def test_mixed_blocks(self):
        blocks = [
            {"type": "text", "data": "paragraph"},
            {"type": "table_row", "data": {"row_natural_language_text": "row text"}},
            {"type": "image", "data": {"uri": _PNG_URI}},
        ]
        record = self._record(blocks)
        result = resolve_pdf_blocks_simple(record, is_multimodal_llm=True)
        assert len(result) == 3

    def test_text_block_non_str_data_skipped(self):
        record = self._record([{"type": "text", "data": 42}])
        assert resolve_pdf_blocks_simple(record, False) == []


# ---------------------------------------------------------------------------
# resolve_attachments
# ---------------------------------------------------------------------------

class TestResolveAttachments:
    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_resolve_attachments")

    async def test_empty_attachments_returns_empty(self, logger):
        result = await resolve_attachments([], None, "org1", True, logger)
        assert result == []

    async def test_missing_virtual_record_id_skipped(self, logger):
        att = {"mimeType": "image/png", "recordName": "photo.png"}
        result = await resolve_attachments([att], None, "org1", True, logger)
        assert result == []

    async def test_unsupported_mime_type_skipped(self, logger):
        att = {"mimeType": "text/plain", "recordName": "file.txt", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], None, "org1", True, logger)
        assert result == []

    async def test_image_non_multimodal_returns_text_hint(self, logger):
        att = {"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], None, "org1", is_multimodal_llm=False, logger=logger)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert "photo.png" in result[0]["text"]

    async def test_image_multimodal_no_blob_store_returns_text_hint(self, logger):
        att = {"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], None, "org1", is_multimodal_llm=True, logger=logger)
        assert len(result) == 1
        assert result[0]["type"] == "text"

    async def test_image_multimodal_blob_returns_none_skipped(self, logger):
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = None
        att = {"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], blob, "org1", True, logger)
        assert result == []

    async def test_image_multimodal_blob_returns_record_with_image(self, logger):
        record = _make_image_record(_PNG_URI)
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = record
        att = {"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], blob, "org1", True, logger)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    async def test_out_records_populated(self, logger):
        record = _make_image_record(_PNG_URI)
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = record
        att = {"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vrid1"}
        out: dict = {}
        await resolve_attachments([att], blob, "org1", True, logger, out_records=out)
        assert "vrid1" in out

    async def test_image_blob_raises_exception_continues(self, logger):
        blob = AsyncMock()
        blob.get_record_from_storage.side_effect = RuntimeError("storage error")
        att = {"mimeType": "image/jpeg", "recordName": "photo.jpg", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], blob, "org1", True, logger)
        assert result == []

    async def test_pdf_no_blob_store_returns_text_hint(self, logger):
        att = {"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], None, "org1", True, logger)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert "doc.pdf" in result[0]["text"]

    async def test_pdf_blob_returns_none_skipped(self, logger):
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = None
        att = {"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], blob, "org1", True, logger)
        assert result == []

    async def test_pdf_blob_returns_record(self, logger):
        pdf_record = {"block_containers": {"blocks": [{"type": "text", "data": "pdf content"}]}}
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = pdf_record
        att = {"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vrid1"}
        pdf_blocks = [{"type": "text", "text": "pdf content"}]
        with patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=(pdf_blocks, None),
        ):
            result = await resolve_attachments([att], blob, "org1", False, logger)
        assert result == pdf_blocks

    async def test_pdf_passes_ref_mapper_to_record_to_message_content(self, logger):
        mapper = MagicMock()
        pdf_record = {"block_containers": {"blocks": [{"type": "text", "data": "pdf content"}]}}
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = pdf_record
        att = {"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vrid1"}
        captured: dict = {}

        def fake_rtc(record, ref_mapper=None, is_multimodal_llm=False):
            captured["ref_mapper"] = ref_mapper
            return ([{"type": "text", "text": "ok"}], ref_mapper)

        with patch(
            "app.utils.chat_helpers.record_to_message_content",
            side_effect=fake_rtc,
        ):
            result = await resolve_attachments(
                [att], blob, "org1", False, logger, ref_mapper=mapper
            )
        assert captured.get("ref_mapper") is mapper
        assert result == [{"type": "text", "text": "ok"}]

    async def test_pdf_out_records_populated(self, logger):
        pdf_record = {"block_containers": {"blocks": [{"type": "text", "data": "pdf content"}]}}
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = pdf_record
        att = {"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vpdf1"}
        pdf_blocks = [{"type": "text", "text": "pdf content"}]
        out: dict = {}
        with patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=(pdf_blocks, None),
        ):
            await resolve_attachments([att], blob, "org1", False, logger, out_records=out)
        assert "vpdf1" in out

    async def test_pdf_empty_content_blocks(self, logger):
        pdf_record = {"block_containers": {"blocks": []}}
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = pdf_record
        att = {"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vrid1"}
        with patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([], None),
        ):
            result = await resolve_attachments([att], blob, "org1", False, logger)
        assert result == []

    async def test_pdf_blob_raises_exception_continues(self, logger):
        blob = AsyncMock()
        blob.get_record_from_storage.side_effect = OSError("disk error")
        att = {"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], blob, "org1", True, logger)
        assert result == []

    async def test_multiple_attachments_processed(self, logger):
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = _make_image_record(_PNG_URI)
        atts = [
            {"mimeType": "image/png", "recordName": "a.png", "virtualRecordId": "v1"},
            {"mimeType": "image/png", "recordName": "b.png", "virtualRecordId": "v2"},
        ]
        result = await resolve_attachments(atts, blob, "org1", True, logger)
        assert len(result) == 2

    async def test_image_with_no_image_blocks_in_record(self, logger):
        record = {"block_containers": {"blocks": []}}
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = record
        att = {"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vrid1"}
        result = await resolve_attachments([att], blob, "org1", True, logger)
        assert result == []


# ---------------------------------------------------------------------------
# ensure_attachment_blocks
# ---------------------------------------------------------------------------

class TestEnsureAttachmentBlocks:
    @pytest.fixture
    def logger(self):
        return logging.getLogger("test_ensure_attachment_blocks")

    async def test_returns_cached_when_already_resolved(self, logger):
        cached = [{"type": "image_url", "image_url": {"url": _PNG_URI}}]
        state = {"resolved_attachment_blocks": cached}
        result = await ensure_attachment_blocks(state, logger)
        assert result is cached

    async def test_returns_empty_list_when_no_attachments(self, logger):
        state: dict = {}
        result = await ensure_attachment_blocks(state, logger)
        assert result == []
        assert state["resolved_attachment_blocks"] == []

    async def test_uses_existing_blob_store_from_state(self, logger):
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = _make_image_record(_PNG_URI)
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "v1"}
            ],
            "blob_store": blob,
            "org_id": "org1",
            "is_multimodal_llm": True,
        }
        result = await ensure_attachment_blocks(state, logger)
        assert len(result) == 1
        assert state["blob_store"] is blob

    async def test_creates_blob_store_when_absent(self, logger):
        mock_blob = AsyncMock()
        mock_blob.get_record_from_storage.return_value = None
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "v1"}
            ],
            "org_id": "org1",
            "is_multimodal_llm": True,
        }
        with patch(
            "app.modules.transformers.blob_storage.BlobStorage", return_value=mock_blob
        ) as mock_cls:
            result = await ensure_attachment_blocks(state, logger)
        mock_cls.assert_called_once()
        assert state["blob_store"] is mock_blob

    async def test_creates_citation_ref_mapper_when_absent(self, logger):
        mock_blob = AsyncMock()
        mock_blob.get_record_from_storage.return_value = None
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "v1"}
            ],
            "blob_store": mock_blob,
            "org_id": "org1",
            "is_multimodal_llm": True,
        }
        from app.utils.chat_helpers import CitationRefMapper
        with patch(
            "app.utils.chat_helpers.CitationRefMapper",
            return_value=CitationRefMapper(),
        ) as mock_cls:
            await ensure_attachment_blocks(state, logger)
        mock_cls.assert_called_once()

    async def test_attachment_records_added_to_vrmap(self, logger):
        record = _make_image_record(_PNG_URI)
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = record
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "vrid99"}
            ],
            "blob_store": blob,
            "org_id": "org1",
            "is_multimodal_llm": True,
        }
        await ensure_attachment_blocks(state, logger)
        assert "vrid99" in state.get("virtual_record_id_to_result", {})

    async def test_existing_vrmap_entry_not_clobbered(self, logger):
        existing_record = {"existing": True}
        record = _make_image_record(_PNG_URI)
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = record
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "vrid99"}
            ],
            "blob_store": blob,
            "org_id": "org1",
            "is_multimodal_llm": True,
            "virtual_record_id_to_result": {"vrid99": existing_record},
        }
        await ensure_attachment_blocks(state, logger)
        assert state["virtual_record_id_to_result"]["vrid99"] is existing_record

    async def test_exception_returns_empty_list(self, logger):
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "v1"}
            ],
            "org_id": "org1",
            "is_multimodal_llm": True,
        }
        with patch(
            "app.modules.transformers.blob_storage.BlobStorage",
            side_effect=RuntimeError("boom"),
        ):
            result = await ensure_attachment_blocks(state, logger)
        assert result == []
        assert state["resolved_attachment_blocks"] == []

    async def test_vrmap_created_when_state_has_non_dict_vrmap(self, logger):
        record = _make_image_record(_PNG_URI)
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = record
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "vnew"}
            ],
            "blob_store": blob,
            "org_id": "org1",
            "is_multimodal_llm": True,
            "virtual_record_id_to_result": "not_a_dict",
        }
        await ensure_attachment_blocks(state, logger)
        assert isinstance(state["virtual_record_id_to_result"], dict)
        assert "vnew" in state["virtual_record_id_to_result"]

    async def test_returns_empty_list_when_attachments_is_none(self, logger):
        state = {"attachments": None}
        result = await ensure_attachment_blocks(state, logger)
        assert result == []
        assert state["resolved_attachment_blocks"] == []

    async def test_uses_existing_citation_ref_mapper(self, logger):
        """When citation_ref_mapper is already in state, no new one is created."""
        existing_mapper = MagicMock()
        blob = AsyncMock()
        blob.get_record_from_storage.return_value = None
        state = {
            "attachments": [
                {"mimeType": "image/png", "recordName": "x.png", "virtualRecordId": "v1"}
            ],
            "blob_store": blob,
            "org_id": "org1",
            "is_multimodal_llm": True,
            "citation_ref_mapper": existing_mapper,
        }
        with patch("app.utils.chat_helpers.CitationRefMapper") as mock_cls:
            await ensure_attachment_blocks(state, logger)
        mock_cls.assert_not_called()
        assert state["citation_ref_mapper"] is existing_mapper
