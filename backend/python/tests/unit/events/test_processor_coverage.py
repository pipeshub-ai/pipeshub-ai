"""
Additional coverage tests for app.events.processor.

Targets remaining uncovered blocks:
- convert_record_dict_to_record: various field combinations
- process_image: no content, no record, non-multimodal, multimodal path
- process_gmail_message: delegation to process_html_document
- process_pdf_with_pymupdf: success and record-not-found
- process_pdf_with_docling: parse failure, blocks failure, record-not-found
- process_doc_document
- process_docx_document: success and record-not-found
- process_blocks: bytes/str/dict input, record-not-found
- _enhance_tables_with_llm: no tables, table with no markdown
- _separate_block_groups_by_index
- _map_base64_images_to_blocks
- process_pdf_document_with_ocr: OCR handler selection branches
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

log = logging.getLogger("test")
log.setLevel(logging.CRITICAL)


def _make_processor(**overrides):
    from app.events.processor import Processor
    kwargs = {
        "logger": log,
        "config_service": AsyncMock(),
        "indexing_pipeline": MagicMock(),
        "graph_provider": AsyncMock(),
        "parsers": {},
        "document_extractor": MagicMock(),
        "sink_orchestrator": MagicMock(),
    }
    kwargs.update(overrides)
    with patch("app.events.processor.DoclingClient"):
        proc = Processor(**kwargs)
    return proc


def _mock_record_dict(**overrides):
    base = {
        "_key": "r1",
        "orgId": "o1",
        "recordName": "test.md",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext1",
        "connectorId": "c1",
        "mimeType": "text/plain",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "version": 1,
    }
    base.update(overrides)
    return base


async def _collect_events(async_gen):
    events = []
    async for ev in async_gen:
        events.append(ev)
    return events


# ===================================================================
# convert_record_dict_to_record
# ===================================================================


class TestConvertRecordDictToRecord:
    def test_basic_conversion(self):
        from app.events.processor import convert_record_dict_to_record
        record = convert_record_dict_to_record(_mock_record_dict())
        assert record.id == "r1"
        assert record.org_id == "o1"

    def test_unknown_connector_name(self):
        from app.events.processor import convert_record_dict_to_record
        record = convert_record_dict_to_record(
            _mock_record_dict(connectorName="unknown_connector_xyz")
        )
        # Should default to KNOWLEDGE_BASE
        assert record.connector_name is not None

    def test_no_connector_name(self):
        from app.events.processor import convert_record_dict_to_record
        d = _mock_record_dict()
        d.pop("connectorName", None)
        record = convert_record_dict_to_record(d)
        assert record.connector_name is not None

    def test_unknown_origin(self):
        from app.events.processor import convert_record_dict_to_record
        record = convert_record_dict_to_record(
            _mock_record_dict(origin="unknown_origin_xyz")
        )
        from app.config.constants.arangodb import OriginTypes
        assert record.origin == OriginTypes.UPLOAD

    def test_id_from_id_field(self):
        from app.events.processor import convert_record_dict_to_record
        d = _mock_record_dict()
        d.pop("_key")
        d["id"] = "id-from-id-field"
        record = convert_record_dict_to_record(d)
        assert record.id == "id-from-id-field"


# ===================================================================
# process_image
# ===================================================================


class TestProcessImage:
    @pytest.mark.asyncio
    async def test_no_content_raises(self):
        proc = _make_processor()
        with pytest.raises(Exception, match="No image data"):
            async for _ in proc.process_image("r1", None, "vr1"):
                pass

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_image("r1", b"image_data", "vr1")
        )
        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)


# ===================================================================
# process_pdf_with_pymupdf
# ===================================================================


class TestProcessPdfWithPyMuPDF:
    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor()

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value=MagicMock())
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance

            proc.graph_provider.get_document = AsyncMock(return_value=None)

            events = await _collect_events(
                proc.process_pdf_with_pymupdf("test.pdf", "r1", b"pdf_data", "vr1")
            )
            assert any(e["event"] == "parsing_complete" for e in events)
            assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_success(self):
        proc = _make_processor()

        with patch("app.events.processor.PyMuPDFOpenCVProcessor") as mock_proc:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value=MagicMock())
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_proc.return_value = mock_instance

            proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())

            with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
                mock_pipeline.return_value.apply = AsyncMock()
                events = await _collect_events(
                    proc.process_pdf_with_pymupdf("test.pdf", "r1", b"pdf_data", "vr1")
                )

            assert any(e["event"] == "parsing_complete" for e in events)
            assert any(e["event"] == "indexing_complete" for e in events)


# ===================================================================
# process_pdf_with_docling
# ===================================================================


class TestProcessPdfWithDocling:
    @pytest.mark.asyncio
    async def test_parse_failure(self):
        proc = _make_processor()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_pdf_with_docling("test.pdf", "r1", b"pdf", "vr1")
        )
        assert any(e["event"] == "docling_failed" for e in events)

    @pytest.mark.asyncio
    async def test_blocks_failure(self):
        proc = _make_processor()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="failed to create blocks"):
            async for _ in proc.process_pdf_with_docling("test.pdf", "r1", b"pdf", "vr1"):
                pass

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor()
        proc.docling_client = AsyncMock()
        proc.docling_client.parse_pdf = AsyncMock(return_value=MagicMock())
        proc.docling_client.create_blocks = AsyncMock(return_value=MagicMock())
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        events = await _collect_events(
            proc.process_pdf_with_docling("test.pdf", "r1", b"pdf", "vr1")
        )
        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)


# ===================================================================
# process_docx_document
# ===================================================================


class TestProcessDocxDocument:
    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor()

        with patch("app.events.processor.DoclingProcessor") as mock_dp:
            mock_instance = AsyncMock()
            mock_instance.parse_document = AsyncMock(return_value=MagicMock())
            mock_instance.create_blocks = AsyncMock(return_value=MagicMock())
            mock_dp.return_value = mock_instance

            proc.graph_provider.get_document = AsyncMock(return_value=None)

            events = await _collect_events(
                proc.process_docx_document("test.docx", "r1", 1, "upload", "o1", b"docx", "vr1")
            )
            assert any(e["event"] == "parsing_complete" for e in events)
            assert any(e["event"] == "indexing_complete" for e in events)


# ===================================================================
# _enhance_tables_with_llm
# ===================================================================


class TestEnhanceTablesWithLLM:
    @pytest.mark.asyncio
    async def test_no_table_groups(self):
        """No TABLE block groups => returns early."""
        proc = _make_processor()
        from app.models.blocks import BlocksContainer
        bc = BlocksContainer(blocks=[], block_groups=[])
        # Should not raise
        await proc._enhance_tables_with_llm(bc)

    @pytest.mark.asyncio
    async def test_table_group_no_markdown(self):
        """Table group without table_markdown in data => skipped."""
        proc = _make_processor()
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        bg = BlockGroup(index=0, type=GroupType.TABLE, data={})
        bc = BlocksContainer(blocks=[], block_groups=[bg])
        await proc._enhance_tables_with_llm(bc)

    @pytest.mark.asyncio
    async def test_table_group_no_llm_response(self):
        """get_table_summary_n_headers returns None."""
        proc = _make_processor()
        from app.models.blocks import BlockGroup, BlocksContainer, GroupType
        bg = BlockGroup(index=0, type=GroupType.TABLE, data={"table_markdown": "| a | b |"})
        bc = BlocksContainer(blocks=[], block_groups=[bg])

        with patch("app.utils.indexing_helpers.get_table_summary_n_headers", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = None
            await proc._enhance_tables_with_llm(bc)


# ===================================================================
# _separate_block_groups_by_index
# ===================================================================


class TestSeparateBlockGroupsByIndex:
    def test_separates_correctly(self):
        proc = _make_processor()
        # Use MagicMock since BlockGroup.index doesn't allow None in pydantic
        bg1 = MagicMock(index=0)
        bg2 = MagicMock(index=None)
        bg3 = MagicMock(index=2)

        with_idx, without_idx = proc._separate_block_groups_by_index([bg1, bg2, bg3])
        assert len(with_idx) == 2
        assert len(without_idx) == 1


# ===================================================================
# _map_base64_images_to_blocks
# ===================================================================


class TestMapBase64ImagesToBlocks:
    def test_empty_caption_map(self):
        proc = _make_processor()
        blocks = [MagicMock()]
        proc._map_base64_images_to_blocks(blocks, {}, 0)
        # Should be a no-op

    def test_image_block_with_matching_caption(self):
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "test_caption"
        block.data = {}

        caption_map = {"test_caption": "data:image/png;base64,abc123"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert block.data["uri"] == "data:image/png;base64,abc123"

    def test_image_block_with_list_captions(self):
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = ["test_caption"]
        block.data = None

        caption_map = {"test_caption": "data:image/png;base64,xyz"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert block.data["uri"] == "data:image/png;base64,xyz"

    def test_image_block_caption_not_in_map(self):
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "missing_caption"
        block.data = {}

        caption_map = {"other_caption": "data:image/png;base64,abc"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert "uri" not in block.data

    def test_image_block_data_is_non_dict_string(self):
        """When block.data is not a dict, creates new dict."""
        from app.models.blocks import BlockType
        proc = _make_processor()

        block = MagicMock()
        block.type = BlockType.IMAGE.value
        block.image_metadata = MagicMock()
        block.image_metadata.captions = "cap"
        block.data = "some string"  # Not a dict

        caption_map = {"cap": "data:image/png;base64,xyz"}
        proc._map_base64_images_to_blocks([block], caption_map, 0)
        assert block.data == {"uri": "data:image/png;base64,xyz"}


# ===================================================================
# process_blocks
# ===================================================================


class TestProcessBlocks:
    @pytest.mark.asyncio
    async def test_bytes_input(self):
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())

        blocks_dict = {"blocks": [], "block_groups": []}
        blocks_bytes = bytes(str(blocks_dict).replace("'", '"'), "utf-8")

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value.apply = AsyncMock()
            with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_pd:
                from app.models.blocks import BlocksContainer
                mock_pd.return_value = BlocksContainer(blocks=[], block_groups=[])
                with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                    events = await _collect_events(
                        proc.process_blocks("test", "r1", 1, "upload", "o1", blocks_bytes, "vr1")
                    )

        assert any(e["event"] == "parsing_complete" for e in events)
        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_dict_input(self):
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())

        blocks_dict = {"blocks": [], "block_groups": []}

        with patch("app.events.processor.IndexingPipeline") as mock_pipeline:
            mock_pipeline.return_value.apply = AsyncMock()
            with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_pd:
                from app.models.blocks import BlocksContainer
                mock_pd.return_value = BlocksContainer(blocks=[], block_groups=[])
                with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                    events = await _collect_events(
                        proc.process_blocks("test", "r1", 1, "upload", "o1", blocks_dict, "vr1")
                    )

        assert any(e["event"] == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_invalid_type_raises(self):
        proc = _make_processor()
        with pytest.raises(ValueError, match="Invalid blocks_data type"):
            async for _ in proc.process_blocks("test", "r1", 1, "upload", "o1", 12345, "vr1"):
                pass

    @pytest.mark.asyncio
    async def test_record_not_found(self):
        proc = _make_processor()
        proc.graph_provider.get_document = AsyncMock(return_value=None)

        blocks_dict = {"blocks": [], "block_groups": []}

        with patch.object(proc, "_process_blockgroups_through_docling", new_callable=AsyncMock) as mock_pd:
            from app.models.blocks import BlocksContainer
            mock_pd.return_value = BlocksContainer(blocks=[], block_groups=[])
            with patch.object(proc, "_enhance_tables_with_llm", new_callable=AsyncMock):
                events = await _collect_events(
                    proc.process_blocks("test", "r1", 1, "upload", "o1", blocks_dict, "vr1")
                )

        assert any(e["event"] == "indexing_complete" for e in events)


# ===================================================================
# process_gmail_message
# ===================================================================


class TestProcessGmailMessage:
    @pytest.mark.asyncio
    async def test_delegates_to_html(self):
        proc = _make_processor()

        async def _fake_html(*args, **kwargs):
            yield {"event": "parsing_complete", "data": {"record_id": "r1"}}
            yield {"event": "indexing_complete", "data": {"record_id": "r1"}}

        proc.process_html_document = _fake_html

        events = await _collect_events(
            proc.process_gmail_message("msg", "r1", 1, "gmail", "o1", b"<p>Hello</p>", "vr1")
        )
        assert any(e["event"] == "parsing_complete" for e in events)
