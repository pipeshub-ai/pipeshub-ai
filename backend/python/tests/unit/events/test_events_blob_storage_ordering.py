"""Tests verifying that blob_storage.apply() runs OUTSIDE the semantic_metadata
conditional in _orchestrate_via_services — it always executes after enrichment,
not only when metadata is present. This was a key change in the
pattern-matching branch: moving blob_storage.apply() outside the enrichment
conditional ensures storage paths are always computed for pattern match support.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.events import EventProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_processor():
    logger = MagicMock()
    processor = MagicMock()
    processor.indexing_pipeline = MagicMock()
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value={
        "_key": "rec-1",
        "id": "rec-1",
        "orgId": "org-1",
        "virtualRecordId": "vrid-1",
    })
    graph_provider.update_node = AsyncMock(return_value=True)
    graph_provider.get_departments = AsyncMock(return_value=[])
    config_service = AsyncMock()

    sink_orchestrator = MagicMock()
    sink_orchestrator.index = AsyncMock()
    sink_orchestrator.enrich = AsyncMock()
    sink_orchestrator.blob_storage = MagicMock()
    sink_orchestrator.blob_storage.apply = AsyncMock()
    sink_orchestrator.vector_store = MagicMock()
    sink_orchestrator.vector_store.index_record_summary = AsyncMock()

    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock()

    extraction_client = MagicMock()
    extraction_client.classify = AsyncMock(return_value=None)

    ep = EventProcessor(
        logger=logger,
        processor=processor,
        graph_provider=graph_provider,
        config_service=config_service,
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
    )
    return ep


def _setup_parse_result(ep):
    """Configure parsing_client to return a valid block container."""
    mock_block_container = MagicMock()
    mock_block_container.blocks = [MagicMock()]
    mock_block_container.block_groups = []
    ep.parsing_client.parse = AsyncMock(return_value=MagicMock(
        block_container=mock_block_container,
        provider_used=MagicMock(value="test"),
    ))


def _build_patches():
    """Return common patches for _orchestrate_via_services tests."""
    mock_record = MagicMock()
    mock_record.org_id = "org-1"
    mock_record.id = "rec-1"
    mock_record.virtual_record_id = "vrid-1"
    mock_record.block_containers = None
    mock_record.semantic_metadata = None

    return {
        "convert": patch(
            "app.events.processor.convert_record_dict_to_record",
            return_value=mock_record,
        ),
        "transform_ctx": patch(
            "app.modules.transformers.transformer.TransformContext",
        ),
        "pipeline": patch(
            "app.modules.transformers.pipeline.IndexingPipeline.build_reconciliation_context",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
    }


async def _collect_events(ep, **kwargs):
    """Run _orchestrate_via_services and collect yielded events."""
    defaults = dict(
        record_id="rec-1",
        org_id="org-1",
        virtual_record_id="vrid-1",
        record_name="test.txt",
        mime_type="text/plain",
        extension="txt",
        event_type="create",
        prev_virtual_record_id=None,
        file_content=b"hello world",
    )
    defaults.update(kwargs)
    events = []
    async for event in ep._orchestrate_via_services(**defaults):
        events.append(event)
    return events


class TestBlobStorageAlwaysRuns:
    """blob_storage.apply() must run regardless of semantic_metadata availability."""

    @pytest.mark.asyncio
    async def test_blob_apply_called_when_enrichment_deferred(self):
        """When defer_extraction is true, blob_storage.apply() must still be called."""
        ep = _make_event_processor()
        _setup_parse_result(ep)

        patches = _build_patches()
        with patch.dict("os.environ", {
            "USE_PARSING_SERVICE": "true",
            "DEFER_EXTRACTION": "true",
        }), patches["convert"], patches["transform_ctx"], patches["pipeline"]:
            await _collect_events(ep)

        ep.sink_orchestrator.blob_storage.apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_blob_apply_called_when_enrichment_fails(self):
        """When enrichment raises an exception, blob_storage.apply() must still run."""
        ep = _make_event_processor()
        _setup_parse_result(ep)
        ep.extraction_client.classify = AsyncMock(
            side_effect=RuntimeError("enrichment failed")
        )

        patches = _build_patches()
        with patch.dict("os.environ", {
            "USE_PARSING_SERVICE": "true",
            "DEFER_EXTRACTION": "false",
        }), patches["convert"], patches["transform_ctx"], patches["pipeline"]:
            await _collect_events(ep)

        ep.sink_orchestrator.blob_storage.apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_blob_apply_failure_does_not_block_indexing_complete(self):
        """If blob_storage.apply() raises, the pipeline should still yield
        INDEXING_COMPLETE so the document remains searchable."""
        ep = _make_event_processor()
        _setup_parse_result(ep)
        ep.sink_orchestrator.blob_storage.apply = AsyncMock(
            side_effect=RuntimeError("blob storage failed")
        )

        patches = _build_patches()
        with patch.dict("os.environ", {
            "USE_PARSING_SERVICE": "true",
            "DEFER_EXTRACTION": "true",
        }), patches["convert"], patches["transform_ctx"], patches["pipeline"]:
            events = await _collect_events(ep)

        event_names = [str(getattr(e, "event", e)) for e in events]
        assert any("INDEXING_COMPLETE" in name for name in event_names)
