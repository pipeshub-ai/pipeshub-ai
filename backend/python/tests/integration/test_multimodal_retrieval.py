"""
End-to-end integration tests for multimodal retrieval → LLM context assembly.

These tests exercise the real (non-mocked) chat_helpers functions that turn
vector search results and vector-metadata-only records into the message
content sent to the LLM, covering the two scenarios the Phase 1/4 fixes
changed:

  1. Image search results whose ``content`` is a real base64 image (native
     multimodal embedding path) must still be rendered as ``image_url``
     blocks for multimodal LLMs.
  2. Image search results whose ``content`` is now a text description
     (Phase 1 fix: image VectorPoint.page_content stores a description, not
     the raw base64 URI) must be rendered as text, never as a broken
     ``image_url`` block — for both multimodal and text-only LLMs.
  3. Records reconstructed purely from vector metadata (no blob storage
     fetch) must not crash when they contain image blocks, and must not
     leak raw base64 into text content downstream.

No Docker services are required; only in-process code.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.blocks import BlockType
from app.utils.chat_helpers import (
    build_message_content_array,
    create_block_from_metadata,
    create_record_from_vector_metadata,
    record_to_message_content,
)

pytestmark = pytest.mark.integration

# A real, minimal 1x1 transparent PNG — is_base64_image() validates magic
# bytes after decoding, so an arbitrary "AAAA..." string would not pass.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
_BASE64_IMAGE_URI = f"data:image/png;base64,{_TINY_PNG_B64}"
_IMAGE_DESCRIPTION = "A network architecture diagram with three tiers"


def _record(vr_id: str) -> dict:
    return {
        "id": "rec-1",
        "virtual_record_id": vr_id,
        "frontend_url": "https://app.example.com",
        "context_metadata": "Doc summary",
    }


class TestBuildMessageContentArrayImageHandling:
    def test_real_base64_image_becomes_image_url_for_multimodal_llm(self):
        vr_id = "vr-1"
        flattened = [{
            "virtual_record_id": vr_id,
            "block_index": 0,
            "block_type": BlockType.IMAGE.value,
            "content": _BASE64_IMAGE_URI,
        }]
        vr_map = {vr_id: _record(vr_id)}

        contents, _ = build_message_content_array(
            flattened, vr_map, is_multimodal_llm=True, from_tool=False
        )

        flat = contents[0]
        image_blocks = [c for c in flat if c["type"] == "image_url"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["image_url"]["url"] == _BASE64_IMAGE_URI

    def test_description_only_image_is_rendered_as_text_not_image_url(self):
        """Phase 1 fix: image page_content now stores a description. Retrieval
        must degrade gracefully to text instead of trying (and failing) to
        send a non-base64 string to the LLM as an image_url."""
        vr_id = "vr-1"
        flattened = [{
            "virtual_record_id": vr_id,
            "block_index": 0,
            "block_type": BlockType.IMAGE.value,
            "content": _IMAGE_DESCRIPTION,
        }]
        vr_map = {vr_id: _record(vr_id)}

        contents, _ = build_message_content_array(
            flattened, vr_map, is_multimodal_llm=True, from_tool=False
        )

        flat = contents[0]
        assert not any(c["type"] == "image_url" for c in flat)
        assert any(
            _IMAGE_DESCRIPTION in c.get("text", "")
            for c in flat if c["type"] == "text"
        )

    def test_real_base64_image_is_skipped_not_leaked_as_text_for_non_multimodal_llm(self):
        """A text-only LLM must never receive the raw base64 blob as text —
        the block is dropped entirely rather than dumping binary-ish data."""
        vr_id = "vr-1"
        flattened = [{
            "virtual_record_id": vr_id,
            "block_index": 0,
            "block_type": BlockType.IMAGE.value,
            "content": _BASE64_IMAGE_URI,
        }]
        vr_map = {vr_id: _record(vr_id)}

        contents, _ = build_message_content_array(
            flattened, vr_map, is_multimodal_llm=False, from_tool=False
        )

        flat = contents[0]
        assert not any(c["type"] == "image_url" for c in flat)
        assert not any(_TINY_PNG_B64 in c.get("text", "") for c in flat if c["type"] == "text")

    def test_text_block_alongside_image_block_both_render_correctly(self):
        """Mixed text + image results in the same record: text always
        renders as text, image renders per its content shape."""
        vr_id = "vr-1"
        flattened = [
            {
                "virtual_record_id": vr_id,
                "block_index": 0,
                "block_type": BlockType.TEXT.value,
                "content": "Some retrieved paragraph.",
            },
            {
                "virtual_record_id": vr_id,
                "block_index": 1,
                "block_type": BlockType.IMAGE.value,
                "content": _BASE64_IMAGE_URI,
            },
        ]
        vr_map = {vr_id: _record(vr_id)}

        contents, _ = build_message_content_array(
            flattened, vr_map, is_multimodal_llm=True, from_tool=False
        )

        flat = contents[0]
        assert any("Some retrieved paragraph." in c.get("text", "") for c in flat if c["type"] == "text")
        assert any(c["type"] == "image_url" for c in flat)


class TestVectorMetadataReconstructionToMessageContent:
    """Exercises create_record_from_vector_metadata -> create_block_from_metadata
    -> record_to_message_content, the fallback path used when a record must be
    rebuilt purely from what's stored in the vector DB (no blob storage
    fetch). This is the path that used to raise AttributeError for image
    blocks before the Phase 4 fix."""

    async def _build_record(self, image_page_content: str) -> dict:
        record_metadata = {
            "recordId": "rec-1",
            "recordName": "Architecture Doc",
            "recordType": "FILE",
            "mimeType": "application/pdf",
        }

        text_point = MagicMock()
        text_point.id = str(uuid4())
        text_point.payload = {
            "metadata": {
                "blockType": BlockType.TEXT.value,
                "blockNum": [0],
                "blockText": "Some paragraph text.",
            },
            "page_content": "Some paragraph text.",
        }

        image_point = MagicMock()
        image_point.id = str(uuid4())
        image_point.payload = {
            "metadata": {
                "blockType": BlockType.IMAGE.value,
                "blockNum": [1],
                "isImage": True,
            },
            "page_content": image_page_content,
        }

        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()

        mock_vector_service = AsyncMock()
        mock_vector_service.filter_collection = AsyncMock(return_value="mock_filter")
        mock_vector_service.scroll = AsyncMock(return_value=([text_point, image_point], None))

        real_utils_mod = sys.modules.pop("app.containers.utils.utils", None)
        fake_utils = ModuleType("app.containers.utils.utils")
        mock_cls_container = MagicMock()
        fake_utils.ContainerUtils = mock_cls_container
        sys.modules["app.containers.utils.utils"] = fake_utils
        try:
            mock_container = mock_cls_container.return_value
            mock_container.get_vector_db_service = AsyncMock(return_value=mock_vector_service)
            record, _ = await create_record_from_vector_metadata(
                record_metadata, "org-1", "vr-1", blob_store
            )
        finally:
            if real_utils_mod is not None:
                sys.modules["app.containers.utils.utils"] = real_utils_mod
            else:
                sys.modules.pop("app.containers.utils.utils", None)
        return record

    @pytest.mark.asyncio
    async def test_image_block_has_dict_data_not_raw_string(self):
        """create_block_from_metadata must wrap the description in a dict —
        never hand back a bare string for an image block's `data` field."""
        record = await self._build_record(_IMAGE_DESCRIPTION)
        blocks = record["block_containers"]["blocks"]
        image_block = next(b for b in blocks if b["type"] == BlockType.IMAGE.value)

        assert isinstance(image_block["data"], dict)
        assert image_block["data"]["uri"] is None
        assert image_block["data"]["description"] == _IMAGE_DESCRIPTION

    @pytest.mark.asyncio
    async def test_record_to_message_content_does_not_crash_multimodal(self):
        record = await self._build_record(_IMAGE_DESCRIPTION)

        content, _ = record_to_message_content(record, is_multimodal_llm=True)

        assert isinstance(content, list)
        assert any(
            "Some paragraph text." in c.get("text", "")
            for c in content if c.get("type") == "text"
        )
        # No URI was ever available for this fallback path, so no image_url
        # is emitted — but critically, no AttributeError either.
        assert not any(c.get("type") == "image_url" for c in content)

    @pytest.mark.asyncio
    async def test_record_to_message_content_does_not_crash_non_multimodal(self):
        record = await self._build_record(_IMAGE_DESCRIPTION)

        content, _ = record_to_message_content(record, is_multimodal_llm=False)

        assert isinstance(content, list)
        assert not any(c.get("type") == "image_url" for c in content)

    def test_create_block_from_metadata_image_block_direct(self):
        """Direct unit-level confirmation of the Phase 4 fix, kept alongside
        the full-chain tests above for fast failure localisation."""
        block = create_block_from_metadata(
            {"blockType": BlockType.IMAGE.value, "blockNum": [2]},
            _IMAGE_DESCRIPTION,
        )
        assert block["data"] == {"uri": None, "description": _IMAGE_DESCRIPTION}
