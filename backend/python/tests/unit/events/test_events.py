"""Tests for app.events.events (EventProcessor class).

This module tests the EventProcessor from the events.py module.
Since events.py and processor.py contain the same EventProcessor class,
these tests provide additional coverage for edge cases and boundary conditions.
"""

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from app.config.constants.arangodb import (
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
)
from app.events.events import DedupDecision, EventProcessor
from app.exceptions.indexing_exceptions import ProcessingError
from app.modules.parsers.pdf.untrusted_pdf import InspectedPdf, UnreadablePdfError
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_processor():
    """Create an EventProcessor with mocked deps from events module."""
    logger = MagicMock()
    processor = MagicMock()
    # Membership sync is awaited, so the pipeline must be an AsyncMock; a bare
    # MagicMock raises TypeError, which the caller now propagates rather than
    # swallowing.
    processor.indexing_pipeline = AsyncMock()
    # Dedup checks the twin's stored content before reusing it; default to present.
    processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="stored/path")
    graph_provider = AsyncMock()
    graph_provider.update_node = AsyncMock(return_value=True)
    config_service = MagicMock()

    ep = EventProcessor(logger, processor, graph_provider, config_service)
    return ep, logger, processor, graph_provider


def _make_event_payload(
    record_id="rec-1",
    mime_type="unknown",
    extension="unknown",
    event_type=None,
    connector_name="",
    buffer=b"hello",
    virtual_record_id=None,
    org_id="org-1",
    version=1,
    record_name=None,
):
    """Build event_data dict for on_event."""
    payload = {
        "recordId": record_id,
        "orgId": org_id,
        "virtualRecordId": virtual_record_id,
        "version": version,
        "connectorName": connector_name,
        "extension": extension,
        "mimeType": mime_type,
        "recordName": record_name or f"test-{record_id}",
        "buffer": buffer,
    }
    data = {"payload": payload}
    if event_type is not None:
        data["eventType"] = event_type
    return data


def _rereads_as_still_in_flight(twin, doc) -> AsyncMock:
    """The post-QUEUED re-read of the twin: same content and org, still running.

    Read at call time, after dedup has stored the computed md5 on ``doc``."""
    return AsyncMock(side_effect=lambda *_a, **_k: {
        **twin, "md5Checksum": doc.get("md5Checksum"), "orgId": doc.get("orgId"),
    })


async def _drain(async_gen):
    """Collect all items from an async generator."""
    items = []
    async for item in async_gen:
        items.append(item)
    return items


async def _mock_processor_gen(*args, **kwargs):
    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))


def _pdf_inspection(needs_ocr: bool = False, **patch_kwargs: object) -> AbstractContextManager[AsyncMock]:
    """Patch the PDF inspection to read every file as sent, deciding *needs_ocr*."""
    patch_kwargs.setdefault(
        "side_effect",
        lambda content, **_kw: InspectedPdf(content, needs_ocr=needs_ocr, repaired=False),
    )
    return patch("app.events.events.inspect_untrusted_pdf", new_callable=AsyncMock, **patch_kwargs)


# ===========================================================================
# Constructor
# ===========================================================================


class TestEventProcessorInit:
    """Tests for EventProcessor.__init__."""

    def test_initialization_stores_all_deps(self):
        """All dependencies are stored as attributes."""
        ep, logger, processor, graph_provider = _make_event_processor()

        assert ep.logger is logger
        assert ep.processor is processor
        assert ep.graph_provider is graph_provider
        logger.info.assert_called()  # "Initializing EventProcessor"

    def test_config_service_defaults_to_none(self):
        """config_service defaults to None when not provided."""
        logger = MagicMock()
        processor = MagicMock()
        graph_provider = AsyncMock()

        ep = EventProcessor(logger, processor, graph_provider)

        assert ep.config_service is None

    def test_config_service_stored_when_provided(self):
        """config_service is stored when explicitly provided."""
        ep, _, _, _ = _make_event_processor()

        assert ep.config_service is not None


# ===========================================================================
# mark_record_status - Additional Edge Cases
# ===========================================================================


class TestMarkRecordStatusEdgeCases:
    """Additional edge-case tests for mark_record_status."""

    @pytest.mark.asyncio
    async def test_completed_status(self):
        """COMPLETED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k1"}

        await ep.mark_record_status(doc, ProgressStatus.COMPLETED)

        assert doc["indexingStatus"] == ProgressStatus.COMPLETED.value
        assert "extractionStatus" not in doc

    @pytest.mark.asyncio
    async def test_failed_status(self):
        """FAILED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k2"}

        await ep.mark_record_status(doc, ProgressStatus.FAILED)

        assert doc["indexingStatus"] == ProgressStatus.FAILED.value
        assert "extractionStatus" not in doc

    @pytest.mark.asyncio
    async def test_not_started_status(self):
        """NOT_STARTED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k3"}

        await ep.mark_record_status(doc, ProgressStatus.NOT_STARTED)

        assert doc["indexingStatus"] == ProgressStatus.NOT_STARTED.value

    @pytest.mark.asyncio
    async def test_queued_status(self):
        """QUEUED status is applied correctly."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k4"}

        await ep.mark_record_status(doc, ProgressStatus.QUEUED)

        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_doc_modified_in_place(self):
        """The doc dict itself is mutated (not a copy)."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k5", "other": "data"}

        await ep.mark_record_status(doc, ProgressStatus.IN_PROGRESS)

        # The original dict should be modified
        assert "indexingStatus" in doc
        assert doc["other"] == "data"

    @pytest.mark.asyncio
    async def test_the_in_process_parse_start_marks_parsing_in_progress(self) -> None:
        """In-process, parse and index are one step: a re-index must not show the old parse as done."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k7", "parsingStatus": ProgressStatus.COMPLETED.value}

        await ep.mark_record_status(doc, ProgressStatus.IN_PROGRESS, parsing_starts=True)
        assert doc["parsingStatus"] == ProgressStatus.IN_PROGRESS.value

    @pytest.mark.asyncio
    async def test_other_in_progress_writes_leave_parsing_alone(self) -> None:
        """The stale-record scan requeues a parse left IN_PROGRESS, so only a real parse start sets it."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "k8", "parsingStatus": ProgressStatus.COMPLETED.value}

        await ep.mark_record_status(doc, ProgressStatus.IN_PROGRESS)
        assert doc["parsingStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_error_with_non_empty_status_raises(self):
        ep, _, _, gp = _make_event_processor()
        gp.update_node.side_effect = Exception("fail")
        doc = {"_key": "k6"}

        with pytest.raises(Exception, match="fail"):
            await ep.mark_record_status(doc, ProgressStatus.FAILED)

    @pytest.mark.asyncio
    async def test_error_with_empty_status_raises(self):
        """Errors with EMPTY status are re-raised."""
        ep, _, _, gp = _make_event_processor()
        gp.update_node.side_effect = Exception("fail")
        doc = {"_key": "k7"}

        with pytest.raises(Exception, match="fail"):
            await ep.mark_record_status(doc, ProgressStatus.EMPTY)

    @pytest.mark.asyncio
    async def test_silent_write_failure_raises_indexing_error(self):
        """A write that fails without raising (update_node returns False,
        e.g. a transient graph DB write failure) must not be swallowed.

        Without this, mark_record_status would report success on a status
        that was never actually persisted — leaving the record stuck at its
        prior status forever, since reconciliation only revisits QUEUED/
        IN_PROGRESS records and the caller would still yield completion
        events as if the write had succeeded.
        """
        from app.exceptions.indexing_exceptions import IndexingError  # noqa: PLC0415

        ep, _, _, gp = _make_event_processor()
        gp.update_node = AsyncMock(return_value=False)
        doc = {"_key": "k8"}

        with pytest.raises(IndexingError):
            await ep.mark_record_status(doc, ProgressStatus.FILE_TYPE_NOT_SUPPORTED)


# ===========================================================================
# _check_duplicate_by_md5 - Additional Edge Cases
# ===========================================================================


class TestCheckDuplicateMd5EdgeCases:
    """Additional tests for _check_duplicate_by_md5."""

    @pytest.mark.asyncio
    async def test_empty_string_content_no_md5(self):
        """Empty string content with no md5Checksum => returns False (no hash calculated)."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5("", doc)

        # Empty string is falsy in `if md5_checksum is None and content:` check
        assert result.skip_indexing is False

    @pytest.mark.asyncio
    async def test_empty_bytes_content_no_md5(self):
        """Empty bytes content with no md5Checksum => returns False."""
        ep, _, _, gp = _make_event_processor()
        doc = {"_key": "r2", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5(b"", doc)

        assert result.skip_indexing is False

    @pytest.mark.asyncio
    async def test_a_restored_record_without_its_checksum_is_hashed_again_and_indexed(self) -> None:
        """A restore clears md5Checksum and keeps virtualRecordId; unchanged content must
        get its checksum back and be indexed, never matched against itself."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = []
        doc = {"_key": "restored-1", "virtualRecordId": "vr-old", "recordType": "FILE", "sizeInBytes": 4}

        with patch.object(ep, "update_record_fields", new_callable=AsyncMock, return_value=True) as update:
            result = await ep._check_duplicate_by_md5(b"same", doc)

        md5 = ep._hash_for_dedup(b"same", "FILE", None)
        update.assert_awaited_once_with(doc, {"md5Checksum": md5})
        assert gp.find_duplicate_records.await_args.kwargs["record_key"] == "restored-1"
        assert gp.find_duplicate_records.await_args.kwargs["md5_checksum"] == md5
        assert result.skip_indexing is False

    @pytest.mark.asyncio
    async def test_completed_without_virtual_record_id_not_matched(self):
        """A COMPLETED duplicate without virtualRecordId is NOT treated as processed."""
        ep, _, _, gp = _make_event_processor()
        dup = {
            "_key": "dup-1",
            "virtualRecordId": None,  # No virtualRecordId
            "indexingStatus": ProgressStatus.COMPLETED.value,
        }
        gp.find_duplicate_records.return_value = [dup]
        doc = {"_key": "r3", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        result = await ep._check_duplicate_by_md5(b"x", doc)

        # COMPLETED without virtualRecordId is NOT matched as processed_duplicate
        # (the condition requires virtualRecordId AND COMPLETED)
        # So it falls through. It's also not IN_PROGRESS, so returns False.
        assert result.skip_indexing is False

    @pytest.mark.asyncio
    async def test_multiple_duplicates_prefers_processed(self):
        """When both processed and in-progress dups exist, processed takes priority."""
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-p",
            "virtualRecordId": "vr-p",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-p",
        }
        in_progress = {
            "_key": "dup-ip",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }
        gp.find_duplicate_records.return_value = [in_progress, processed]
        doc = {"_key": "r4", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        # Should be handled as processed, not queued
        assert doc["virtualRecordId"] == "vr-p"
        assert doc.get("indexingStatus") != ProgressStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_doc_uses_id_field_as_fallback_for_key(self):
        """copy_document_relationships uses doc['id'] when _key is missing."""
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-src",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"id": "r-fallback", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=200):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        gp.copy_document_relationships.assert_awaited_once_with("dup-src", "r-fallback")


class TestCheckDuplicateMd5MissingStoredContent:
    """A twin whose stored content is gone (e.g. deleted with the connector
    that indexed it first) must not be reused, or every re-index -- forced or
    not -- skips against it and the record stays unreadable."""

    @staticmethod
    def _setup(twin_status=ProgressStatus.COMPLETED.value, stored_path=None):
        ep, _, processor, gp = _make_event_processor()
        lookup = processor.sink_orchestrator.blob_storage.get_actual_content_path
        lookup.return_value = stored_path
        gp.find_duplicate_records.return_value = [{
            "_key": "twin",
            "virtualRecordId": "vr-shared",
            "indexingStatus": twin_status,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }]
        doc = {
            "_key": "r1", "orgId": "org-1", "virtualRecordId": "vr-shared",
            "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10,
        }
        return ep, gp, lookup, doc

    @pytest.mark.asyncio
    async def test_completed_twin_without_stored_content_is_indexed(self):
        ep, gp, lookup, doc = self._setup(stored_path=None)

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is False
        assert result.virtual_record_id is None
        assert doc["virtualRecordId"] == "vr-shared"
        lookup.assert_awaited_once_with("org-1", "vr-shared")
        gp.copy_document_relationships.assert_not_called()
        assert result.rebuild_shared_vrid is True

    @pytest.mark.asyncio
    async def test_twin_on_another_vrid_is_not_rebuilt_through_this_record(self):
        ep, _, _, doc = self._setup(stored_path=None)
        doc["virtualRecordId"] = "vr-own"

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is False
        assert result.rebuild_shared_vrid is False

    @pytest.mark.asyncio
    async def test_completed_twin_with_stored_content_is_still_reused(self):
        ep, gp, _, doc = self._setup(stored_path="PipesHub/records/c1/a.pdf")

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        gp.copy_document_relationships.assert_awaited_once_with("twin", "r1")

    @pytest.mark.asyncio
    async def test_empty_twin_is_reused_without_a_storage_lookup(self):
        ep, _, lookup, doc = self._setup(twin_status=ProgressStatus.EMPTY.value)

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        lookup.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_blob_storage_keeps_reusing_the_twin(self):
        ep, _, _, doc = self._setup()
        ep.processor.sink_orchestrator = None

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True


# ===========================================================================
# _check_duplicate_by_md5 - cross-collection dedup matrix
#
# Under a hypothetical per-connector-type strategy (never shipped in OSS,
# but the interface must support it), a duplicate found in a different
# collection than the current record must not be treated the same as one
# in the same collection: see the dedup decision matrix in the
# flexible-collection-strategy plan.
# ===========================================================================


# The real contract double rather than a local stub: dedup compares its answer
# against the write path's, so a stub that skips `required_axes` would let this
# suite pass while production refused the same context.
from app.services.vector_db.strategies.per_connector_type import (  # noqa: E402
    PerConnectorTypeStrategy as _PerConnectorNameStrategy,
)


def _make_multi_collection_event_processor():
    from app.events.events import EventProcessor

    logger = MagicMock()
    processor = MagicMock()
    processor.indexing_pipeline = AsyncMock()
    # Dedup checks the twin's stored content before reusing it; default to present.
    processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="stored/path")
    graph_provider = AsyncMock()
    graph_provider.update_node = AsyncMock(return_value=True)
    config_service = MagicMock()

    ep = EventProcessor(
        logger,
        processor,
        graph_provider,
        config_service,
        collection_strategy=_PerConnectorNameStrategy(),
    )
    return ep, graph_provider


class TestCheckDuplicateMd5UnscannedTwin:
    """A code file must not inherit a twin indexed before secret redaction:
    its vectors and blob hold the raw content."""

    @staticmethod
    def _setup(twin_mark) -> tuple:
        ep, _, _, gp = _make_event_processor()
        twin = {
            "_key": "twin", "virtualRecordId": "vr-shared", "recordType": "CODE_FILE",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }
        if twin_mark is not None:
            twin["secretsRedacted"] = twin_mark
        gp.find_duplicate_records.return_value = [twin]
        doc = {
            "_key": "r1", "orgId": "org-1", "virtualRecordId": "vr-shared",
            "md5Checksum": "abc", "recordType": "CODE_FILE", "sizeInBytes": 10,
        }
        return ep, gp, doc

    @pytest.mark.asyncio
    async def test_unscanned_completed_twin_is_not_reused(self) -> None:
        ep, gp, doc = self._setup(twin_mark=None)

        result = await ep._check_duplicate_by_md5(b"x", doc, scans_for_secrets=True)

        assert result.skip_indexing is False
        assert result.rebuild_shared_vrid is True
        gp.copy_document_relationships.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mark", [0, 2])
    async def test_scanned_twin_is_still_reused_and_lends_its_mark(self, mark) -> None:
        ep, gp, doc = self._setup(twin_mark=mark)

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc, scans_for_secrets=True)

        assert result.skip_indexing is True
        assert doc["secretsRedacted"] == mark

    @pytest.mark.asyncio
    async def test_records_not_scanned_reuse_any_twin(self) -> None:
        ep, _, doc = self._setup(twin_mark=None)

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True


class TestCheckDuplicateMd5CrossCollectionMatrix:
    @pytest.mark.asyncio
    async def test_same_collection_completed_duplicate_skips_indexing(self):
        """Same connectorName -> same collection -> full reuse, skip indexing."""
        ep, gp = _make_multi_collection_event_processor()
        dup = {
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [dup]
        doc = {
            "_key": "r1",
            "connectorName": "GOOGLE_DRIVE",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert result.virtual_record_id == "vr-1"

    @pytest.mark.asyncio
    async def test_different_collection_completed_duplicate_indexes_anyway(self):
        """Different connectorName -> different collection -> VRID reused but
        this record still gets indexed into its own (empty) collection."""
        ep, gp = _make_multi_collection_event_processor()
        dup = {
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [dup]
        doc = {
            "_key": "r1",
            "connectorName": "SLACK",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is False
        assert result.virtual_record_id == "vr-1"
        # sync_vector_membership must not run for a collection this record
        # isn't even part of.
        ep.processor.indexing_pipeline.sync_vector_membership.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_collection_in_progress_duplicate_queues(self):
        ep, gp = _make_multi_collection_event_processor()
        in_progress = {
            "_key": "dup-ip",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }
        gp.find_duplicate_records.return_value = [in_progress]
        doc = {
            "_key": "r1",
            "connectorName": "GOOGLE_DRIVE",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }
        gp.get_document = _rereads_as_still_in_flight(in_progress, doc)

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert result.virtual_record_id is None
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value

    @pytest.mark.asyncio
    async def test_different_collection_in_progress_duplicate_proceeds(self):
        """Must not queue behind another collection's in-flight work."""
        ep, gp = _make_multi_collection_event_processor()
        in_progress = {
            "_key": "dup-ip",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }
        gp.find_duplicate_records.return_value = [in_progress]
        doc = {
            "_key": "r1",
            "connectorName": "SLACK",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is False
        assert result.virtual_record_id is None
        assert doc.get("indexingStatus") != ProgressStatus.QUEUED.value


# ===========================================================================
# _check_duplicate_by_md5 - entities-collection sync on the finished-
# duplicate branch (SinkOrchestrator.sync_entities_for_duplicate)
# ===========================================================================


class TestCheckDuplicateMd5EntitySync:
    """sync_entities_for_duplicate must run only for same-collection,
    already-processed duplicates -- a different-collection duplicate
    continues to full indexing and gets entity sync from
    SinkOrchestrator.index()/enrich() instead."""

    _DUP = {
        "_key": "dup-1",
        "connectorName": "GOOGLE_DRIVE",
        "virtualRecordId": "vr-1",
        "indexingStatus": ProgressStatus.COMPLETED.value,
        "extractionStatus": ProgressStatus.COMPLETED.value,
    }

    @pytest.mark.asyncio
    async def test_same_collection_finished_duplicate_syncs_entities(self):
        ep, gp = _make_multi_collection_event_processor()
        ep.sink_orchestrator = AsyncMock()
        gp.find_duplicate_records.return_value = [self._DUP]
        doc = {
            "_key": "r1",
            "connectorName": "GOOGLE_DRIVE",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        ep.sink_orchestrator.sync_entities_for_duplicate.assert_awaited_once_with(doc)

    @pytest.mark.asyncio
    async def test_different_collection_finished_duplicate_skips_entity_sync(self):
        ep, gp = _make_multi_collection_event_processor()
        ep.sink_orchestrator = AsyncMock()
        gp.find_duplicate_records.return_value = [self._DUP]
        doc = {
            "_key": "r1",
            "connectorName": "SLACK",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is False
        ep.sink_orchestrator.sync_entities_for_duplicate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_sink_orchestrator_does_not_raise(self):
        """ep.sink_orchestrator defaults to None; must not AttributeError."""
        ep, gp = _make_multi_collection_event_processor()
        assert ep.sink_orchestrator is None
        gp.find_duplicate_records.return_value = [self._DUP]
        doc = {
            "_key": "r1",
            "connectorName": "GOOGLE_DRIVE",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True

    @pytest.mark.asyncio
    async def test_entity_sync_failure_is_non_fatal(self):
        """A raising sink still returns the normal dedup decision -- entity
        sync is bookkeeping, not part of the correctness of the decision."""
        ep, gp = _make_multi_collection_event_processor()
        ep.sink_orchestrator = AsyncMock()
        ep.sink_orchestrator.sync_entities_for_duplicate = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        gp.find_duplicate_records.return_value = [self._DUP]
        doc = {
            "_key": "r1",
            "connectorName": "GOOGLE_DRIVE",
            "md5Checksum": "abc",
            "recordType": "FILE",
            "sizeInBytes": 10,
        }

        result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result == DedupDecision(virtual_record_id="vr-1", skip_indexing=True)


# ===========================================================================
# on_event - Additional Edge Cases
# ===========================================================================


class TestOnEventEdgeCases:
    """Additional edge-case tests for on_event."""

    @pytest.mark.asyncio
    async def test_default_event_type_is_new_record(self):
        """When eventType is missing, defaults to NEW_RECORD."""
        ep, logger, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            # No eventType key in data
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_a_reprocessed_record_loses_its_previous_failure_reason(self) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE", "reason": "parse failed"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)),              patch.object(ep, "update_record_fields", new_callable=AsyncMock) as update:
            await _drain(ep.on_event(_make_event_payload(extension=ExtensionTypes.DOCX.value)))

        assert any(c.args[1] == {"reason": None} for c in update.await_args_list)

    @pytest.mark.asyncio
    async def test_a_failed_reason_clear_fails_the_attempt(self) -> None:
        from app.exceptions.indexing_exceptions import IndexingError

        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE", "reason": "parse failed"}
        gp.update_node = AsyncMock(return_value=False)
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             pytest.raises(IndexingError, match="reason"):
            await _drain(ep.on_event(_make_event_payload(extension=ExtensionTypes.DOCX.value)))

    @pytest.mark.asyncio
    async def test_virtual_record_id_from_record_when_not_in_payload(self):
        """If virtualRecordId not in payload, it's taken from the DB record."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "virtualRecordId": "from-db",
        }
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                virtual_record_id=None,
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["virtual_record_id"] == "from-db"

    @pytest.mark.asyncio
    async def test_virtual_record_id_generated_when_none_everywhere(self):
        """If virtualRecordId is None in both payload and DB record, a UUID is generated."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "virtualRecordId": None,
        }
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                virtual_record_id=None,
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        # Should be a UUID string (36 chars with hyphens)
        assert len(call_kwargs["virtual_record_id"]) == 36

    @pytest.mark.asyncio
    async def test_origin_defaults_to_connector_when_connector_name_present(self):
        """Origin defaults to CONNECTOR when connectorName is not empty."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.PLAIN_TEXT.value,
                connector_name="gmail",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_txt_document.call_args[1]
        assert call_kwargs["origin"] == "CONNECTOR"

    @pytest.mark.asyncio
    async def test_origin_defaults_to_upload_when_no_connector(self):
        """Origin defaults to UPLOAD when connectorName is empty."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.PLAIN_TEXT.value,
                connector_name="",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_txt_document.call_args[1]
        assert call_kwargs["origin"] == "UPLOAD"

    @pytest.mark.asyncio
    async def test_md5_check_exception_is_reraised(self):
        """If _check_duplicate_by_md5 raises, the exception propagates."""
        ep, _, _, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        with patch.object(
            ep,
            "_check_duplicate_by_md5",
            new_callable=AsyncMock,
            side_effect=RuntimeError("md5 fail"),
        ):
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            with pytest.raises(RuntimeError, match="md5 fail"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_record_name_defaults_to_untitled(self):
        """When recordName is missing from payload, defaults to Untitled-{recordId}."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            payload = {
                "recordId": "rec-1",
                "orgId": "org-1",
                "virtualRecordId": "vr-1",
                "version": 1,
                "connectorName": "",
                "extension": ExtensionTypes.DOCX.value,
                "mimeType": "unknown",
                "buffer": b"data",
                # No recordName key
            }
            event_data = {"payload": payload}
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["recordName"] == "Untitled-rec-1"

    @pytest.mark.asyncio
    async def test_docx_mime_type_routes_to_docx_processor(self):
        """DOCX MIME type routes to process_docx_document via elif branch."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_docx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_xlsx_mime_type_routes_to_excel_processor(self):
        """XLSX MIME type routes to process_excel_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.XLSX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_excel_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_csv_mime_type_routes_to_delimited(self):
        """CSV MIME type routes to process_delimited_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_delimited_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.CSV.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_delimited_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_pptx_mime_type_routes_to_pptx(self):
        """PPTX MIME type routes to process_pptx_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pptx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.PPTX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_pptx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_markdown_mime_type_routes_to_md(self):
        """Markdown MIME type routes to process_md_document."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.MARKDOWN.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_md_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_processor_exception_bubbles_up(self):
        """If the downstream processor raises, on_event re-raises."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def failing_processor(*args, **kwargs):
            raise ValueError("processor broke")
            yield  # noqa: unreachable - needed to make it an async generator

        processor.process_docx_document = MagicMock(side_effect=failing_processor)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            with pytest.raises(ValueError, match="processor broke"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_pymupdf_env_flag_routes_to_pymupdf(self):
        """ENABLE_PDFPLUMBER_PROCESSOR=true routes to process_pdf_with_pdf_plumber."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(needs_ocr=False), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "true"}):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_pdf_with_pdf_plumber.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_layout_parser_failure_is_not_sent_to_ocr(self) -> None:
        """A PDF with a text layer is never OCR'd page by page, one model call each."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def layout_parser_fails(*_args: object, **_kwargs: object) -> AsyncIterator[PipelineEvent]:
            raise ProcessingError("layout parser failed")
            yield  # noqa: unreachable

        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=layout_parser_fails)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(needs_ocr=False), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "true"}):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            with pytest.raises(ProcessingError):
                await _drain(ep.on_event(event_data))

        processor.process_pdf_document_with_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_pdf_larger_than_docling_accepts_goes_to_the_layout_parser(self) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_docling = MagicMock(side_effect=_mock_processor_gen)
        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}), \
             patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(needs_ocr=False), \
             patch("app.events.events.within_docling_size_limit", return_value=False):
            await _drain(ep.on_event(_make_event_payload(extension=ExtensionTypes.PDF.value)))

        processor.process_pdf_with_docling.assert_not_called()
        processor.process_pdf_with_pdf_plumber.assert_called_once()
        processor.process_pdf_document_with_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unreachable_docling_retries_the_record_without_another_parser(self) -> None:
        from app.services.docling.client import DoclingUnavailableError

        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def docling_down(*_args: object, **_kwargs: object) -> AsyncIterator[PipelineEvent]:
            raise DoclingUnavailableError("Docling could not parse rec-1.pdf: down")
            yield  # noqa: unreachable

        processor.process_pdf_with_docling = MagicMock(side_effect=docling_down)
        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}), \
             patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(needs_ocr=False):
            with pytest.raises(DoclingUnavailableError):
                await _drain(ep.on_event(_make_event_payload(extension=ExtensionTypes.PDF.value)))

        processor.process_pdf_with_pdf_plumber.assert_not_called()
        processor.process_pdf_document_with_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unreadable_pdf_fails_before_any_parser_reads_it(self) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_docling = MagicMock(side_effect=_mock_processor_gen)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}), \
             patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(side_effect=UnreadablePdfError("pdfium could not open the PDF either")):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            with pytest.raises(UnreadablePdfError):
                await _drain(ep.on_event(event_data))

        processor.process_pdf_with_docling.assert_not_called()
        processor.process_pdf_document_with_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_parsers_read_the_bytes_the_inspection_returned(self) -> None:
        """A rewritten file replaces the damaged one for every parser, the fallback included."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def docling_fails(*_args: object, **_kwargs: object) -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.DOCLING_FAILED, data=PipelineEventData(record_id="rec-1"))

        processor.process_pdf_with_docling = MagicMock(side_effect=docling_fails)
        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)
        rewritten = InspectedPdf(b"%PDF-rewritten", needs_ocr=False, repaired=True)

        with patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}), \
             patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(side_effect=None, return_value=rewritten) as inspect:
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value, buffer=b"%PDF-damaged")
            await _drain(ep.on_event(event_data))

        assert inspect.await_args.args[0] == b"%PDF-damaged"
        assert processor.process_pdf_with_docling.call_args.kwargs["pdf_binary"] == b"%PDF-rewritten"
        assert processor.process_pdf_with_pdf_plumber.call_args.kwargs["pdf_binary"] == b"%PDF-rewritten"


class TestEpubDispatch:
    """EPUB is read by the processor's EPUB reader, never converted to PDF."""

    @pytest.mark.parametrize(
        ("extension", "mime_type"),
        [(ExtensionTypes.EPUB.value, "unknown"), ("unknown", MimeTypes.EPUB.value)],
    )
    @pytest.mark.asyncio
    async def test_epub_routes_to_the_epub_processor(self, extension: str, mime_type: str) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_epub_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            event_data = _make_event_payload(
                extension=extension, mime_type=mime_type, record_name="book.epub"
            )
            events = await _drain(ep.on_event(event_data))

        processor.process_epub_document.assert_called_once()
        kwargs = processor.process_epub_document.call_args.kwargs
        assert kwargs["epub_binary"] == b"hello"
        assert kwargs["recordName"] == "book.epub"
        processor.process_pdf_with_docling.assert_not_called()
        processor.process_pdf_document_with_ocr.assert_not_called()
        spawn.assert_not_called()
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_an_epub_failure_bubbles_up(self) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        processor.process_epub_document = MagicMock(side_effect=RuntimeError("book could not be read"))

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.EPUB.value)
            with pytest.raises(RuntimeError, match="book could not be read"):
                await _drain(ep.on_event(event_data))

        processor.process_pdf_with_docling.assert_not_called()


# ===========================================================================
# on_event - MIME type dispatch branches (Google Workspace, HTML, Blocks, etc.)
# ===========================================================================


class TestOnEventMimeTypeDispatch:
    """Tests for MIME type-based routing in on_event."""

    @pytest.mark.asyncio
    async def test_google_slides_routes_to_pptx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pptx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_SLIDES.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_pptx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_docs_routes_to_docx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_DOCS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_docx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_google_sheets_routes_to_excel_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GOOGLE_SHEETS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_excel_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_html_mime_type_routes_to_html_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_html_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.HTML.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_html_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_plain_text_mime_routes_to_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.PLAIN_TEXT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_txt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_mime_type_routes_to_blocks_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_blocks = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.BLOCKS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_blocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_mime_type_routes_to_gmail_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_gmail_message = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.GMAIL.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_gmail_message.assert_called_once()


# ===========================================================================
# on_event - Extension-based dispatch branches
# ===========================================================================


class TestOnEventExtensionDispatch:
    """Tests for extension-based routing in on_event."""

    @pytest.mark.asyncio
    async def test_doc_extension_routes_to_doc_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_doc_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.DOC.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_doc_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_xls_extension_routes_to_xls_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_xls_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.XLS.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_xls_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_tsv_extension_routes_to_delimited_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_delimited_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.TSV.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_delimited_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_ppt_extension_routes_to_ppt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_ppt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.PPT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_ppt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_md_extension_routes_to_md_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.MD.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_md_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_mdx_extension_routes_to_mdx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_mdx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.MDX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_mdx_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_txt_extension_routes_to_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.TXT.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_txt_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_png_extension_routes_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.PNG.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_jpg_extension_routes_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.JPG.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_image.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_extension_raises(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension="xyz")
            with pytest.raises(Exception, match="Unsupported file extension"):
                await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_html_extension_routes_to_html_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_html_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.HTML.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_html_document.assert_called_once()

    @pytest.mark.asyncio
    async def test_code_mime_routes_to_code_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="exe",
                mime_type="text/x-python",
                record_name="main.py",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_code_document.assert_called_once()
        processor.process_md_document.assert_not_called()
        processor.process_txt_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_code_file_arriving_as_text_plain_still_reaches_code_processor(self):
        """Connectors walking a git tree default an unrecognised blob's mime to
        text/plain. The PLAIN_TEXT branch returns early, so the code dispatch
        has to precede it or every .jsx is silently parsed as prose."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="",
                mime_type="text/plain",
                record_name="App.jsx",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_code_document.assert_called_once()
        processor.process_txt_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_plain_text_that_is_not_code_still_uses_txt_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="",
                mime_type="text/plain",
                record_name="notes.txt",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_txt_document.assert_called_once()
        processor.process_code_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_code_extension_routes_to_code_processor_when_mime_unknown(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_code_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_md_document = MagicMock(side_effect=_mock_processor_gen)
        processor.process_txt_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension="py",
                mime_type="application/octet-stream",
                record_name="main.py",
            )
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_code_document.assert_called_once()
        processor.process_md_document.assert_not_called()
        processor.process_txt_document.assert_not_called()


# ===========================================================================
# _check_duplicate_by_md5 - string content path
# ===========================================================================


class TestCheckDuplicateMd5StringContent:
    """Test md5 calculation with string content — uses _normalize_content_for_dedup."""

    @pytest.mark.asyncio
    async def test_string_content_md5_calculated(self):
        """String content is encoded to bytes, normalized, then hashed."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = []
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5("hello world", doc)

        assert result.skip_indexing is False
        assert doc.get("md5Checksum") is not None
        # Content goes through _normalize_content_for_dedup (plain text returns as-is)
        normalized = ep._normalize_content_for_dedup(b"hello world", record_type="FILE")
        expected = hashlib.md5(normalized).hexdigest()
        assert doc["md5Checksum"] == expected

    @pytest.mark.asyncio
    async def test_bytes_content_md5_calculated(self):
        """Bytes content is normalized then hashed."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = []
        doc = {"_key": "r1", "recordType": "FILE"}

        result = await ep._check_duplicate_by_md5(b"hello world", doc)

        assert result.skip_indexing is False
        normalized = ep._normalize_content_for_dedup(b"hello world", record_type="FILE")
        expected = hashlib.md5(normalized).hexdigest()
        assert doc["md5Checksum"] == expected


# ===========================================================================
# on_event - update event creates new virtual_record_id
# ===========================================================================


class TestOnEventUpdateEvent:
    """Test UPDATE_RECORD / REINDEX_RECORD reconciliation logic."""

    @pytest.mark.asyncio
    async def test_update_non_reconciliation_type_generates_new_vrid(self):
        """Non-reconciliation types (e.g. XLSX) get a new UUID on update."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "virtualRecordId": "old-vr-id",
        }
        processor.process_excel_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.XLSX.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="old-vr-id",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_excel_document.call_args[1]
        assert call_kwargs["virtual_record_id"] != "old-vr-id"
        assert len(call_kwargs["virtual_record_id"]) == 36

    @pytest.mark.asyncio
    async def test_update_reconciliation_type_1to1_keeps_vrid(self):
        """Reconciliation type with 1:1 mapping keeps existing vrid."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "existing-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="existing-vrid",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] == "existing-vrid"
        assert call_kwargs["prev_virtual_record_id"] == "existing-vrid"

    @pytest.mark.asyncio
    async def test_update_reconciliation_type_nto1_isolates_vrid(self):
        """Reconciliation type with N:1 mapping generates new vrid."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "shared-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[
            {"_key": "rec-1"}, {"_key": "rec-2"},
        ])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="shared-vrid",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] != "shared-vrid"
        assert len(call_kwargs["virtual_record_id"]) == 36

    @pytest.mark.asyncio
    async def test_shared_vrid_with_missing_content_is_rebuilt_not_isolated(self):
        """Isolating would repair only this record; the others sharing the
        VRID would keep reading content that no longer exists."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "shared-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[
            {"_key": "rec-1"}, {"_key": "rec-2"},
        ])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)
        cleanup = AsyncMock()

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(rebuild_shared_vrid=True)),              patch.object(ep, "_cleanup_abandoned_vrid_storage", cleanup):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.REINDEX_RECORD.value,
                virtual_record_id="shared-vrid",
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] == "shared-vrid"
        assert call_kwargs["prev_virtual_record_id"] == "shared-vrid"
        cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_reindex_event_uses_same_reconciliation_logic(self):
        """REINDEX_RECORD follows the same reconciliation path as UPDATE_RECORD."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_VIEW",
            "virtualRecordId": "reindex-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_VIEW.value,
                extension=ExtensionTypes.SQL_VIEW.value,
                event_type=EventTypes.REINDEX_RECORD.value,
                virtual_record_id="reindex-vrid",
            )
            events = await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] == "reindex-vrid"

    @pytest.mark.asyncio
    async def test_reconciliation_type_no_existing_vrid_treats_as_new(self):
        """Reconciliation type with no existing vrid treats as new."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": None,
        }
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id=None,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert len(call_kwargs["virtual_record_id"]) == 36


# ===========================================================================
# on_event - docling fallback to OCR on docling failure
# ===========================================================================


class TestOnEventDoclingFallback:
    """A PDF Docling cannot read is read by the layout parser, never by per-page OCR."""

    @pytest.mark.asyncio
    async def test_docling_failure_falls_back_to_the_layout_parser(self) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        async def docling_fails(*_args: object, **_kwargs: object) -> AsyncIterator[PipelineEvent]:
            yield PipelineEvent(event=IndexingEvent.DOCLING_FAILED, data=PipelineEventData(record_id="rec-1"))

        processor.process_pdf_with_docling = MagicMock(side_effect=docling_fails)
        processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(needs_ocr=False), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}):
            event_data = _make_event_payload(extension=ExtensionTypes.PDF.value)
            await _drain(ep.on_event(event_data))

        processor.process_pdf_with_pdf_plumber.assert_called_once()
        processor.process_pdf_document_with_ocr.assert_not_called()


# ===========================================================================
# Coverage: on_event early return paths (lines 244-245, 253-254, 261-262, 280-281)
# ===========================================================================


class TestOnEventEarlyReturns:
    """Test on_event early return edge cases."""

    @pytest.mark.asyncio
    async def test_no_payload_raises_terminal_error(self):
        """A malformed envelope must fail loudly and once.

        Returning bare here yielded neither completion event, which the
        consumers read as an unexplained failure and retried to the dead-letter
        ceiling — three deliveries and no diagnosis for something no retry can
        fix.
        """
        ep, _logger, _, _ = _make_event_processor()
        event_data = {"eventType": "NEW_RECORD"}  # no payload key

        with pytest.raises(ProcessingError):
            await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_no_record_id_raises_terminal_error(self):
        """A payload with no recordId can never come good on a retry."""
        ep, _logger, _, _ = _make_event_processor()
        event_data = {"payload": {"orgId": "org-1"}}  # no recordId

        with pytest.raises(ProcessingError):
            await _drain(ep.on_event(event_data))

    @pytest.mark.asyncio
    async def test_record_not_found_drains_the_message(self):
        """A deleted record is drained, not retried.

        Unlike the two malformed cases above this is legitimately reachable —
        a record can be deleted between an event being published and consumed —
        so it completes the pipeline rather than raising.
        """
        ep, _logger, _, gp = _make_event_processor()
        gp.get_document.return_value = None
        event_data = _make_event_payload()

        events = await _drain(ep.on_event(event_data))

        assert [e.event for e in events] == [
            IndexingEvent.PARSING_COMPLETE,
            IndexingEvent.INDEXING_COMPLETE,
        ]

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_not_drained_as_a_deletion(self):
        """The graph being unreachable must not read as "this record is gone".

        Draining is permanent: the message is acknowledged and the record sits
        at QUEUED until the stranded sweep republishes it an hour later. During
        a graph restart every record in flight took that path.
        """
        ep, _logger, _, gp = _make_event_processor()

        async def unreachable_graph(*_args, raise_on_error: bool = False, **_kwargs):
            # What both providers do: swallow and answer None unless asked not to.
            # A double that raised either way would pass without the fix.
            if raise_on_error:
                raise RuntimeError("graph is restarting")
            return None

        gp.get_document.side_effect = unreachable_graph

        with pytest.raises(RuntimeError):
            await _drain(ep.on_event(_make_event_payload()))

    @pytest.mark.asyncio
    async def test_the_record_lookup_asks_for_failures_to_be_raised(self):
        """`raise_on_error` is what makes the None above mean "deleted"."""
        ep, _logger, _, gp = _make_event_processor()
        gp.get_document.return_value = None

        await _drain(ep.on_event(_make_event_payload()))

        assert gp.get_document.await_args.kwargs.get("raise_on_error") is True

    @pytest.mark.asyncio
    async def test_no_buffer_proceeds_with_none_content(self):
        """None buffer proceeds (no early return), duplicate check runs with None content."""
        ep, logger, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(buffer=None, extension=ExtensionTypes.DOCX.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 2


# ===========================================================================
# Coverage: duplicate record handling (lines 208-214, 290-293)
# ===========================================================================


class TestOnEventDuplicate:
    """Test duplicate detection in on_event."""

    @pytest.mark.asyncio
    async def test_duplicate_detected_yields_events(self):
        """Duplicate detected yields parsing_complete + indexing_complete (lines 290-293)."""
        ep, _, _, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}

        with patch.object(
            ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
            return_value=DedupDecision(virtual_record_id=None, skip_indexing=True),
        ):
            event_data = _make_event_payload()
            events = await _drain(ep.on_event(event_data))

        assert any(e.event == "parsing_complete" for e in events)
        assert any(e.event == "indexing_complete" for e in events)

    @pytest.mark.asyncio
    async def test_duplicate_skip_invalidates_accessible_records_cache(self):
        """When dedup skips indexing, the accessible-records cache must still
        be invalidated so the newly attached record is searchable immediately.

        Without this, a KB file re-uploaded with identical content (matched by
        MD5 to an existing record) would be marked COMPLETED but invisible to
        search until the cache TTL expires.
        """
        ep, _, _, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "FILE",
            "connectorName": "KB",
            "connectorId": "hidden-kb-1",
            "orgId": "org-1",
        }

        with patch.object(
            ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
            return_value=DedupDecision(virtual_record_id=None, skip_indexing=True),
        ), patch(
            "app.events.events.notify_record_indexed", new_callable=AsyncMock,
        ) as mock_notify:
            event_data = _make_event_payload(
                connector_name="KB",
            )
            events = await _drain(ep.on_event(event_data))

        mock_notify.assert_awaited_once()
        call_kwargs = mock_notify.call_args[1]
        assert call_kwargs["connector_name"] == "KB"
        assert call_kwargs["connector_id"] == "hidden-kb-1"
        assert call_kwargs["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_check_duplicate_in_progress_handling(self):
        """Duplicate record in IN_PROGRESS status gets QUEUED (lines 208-214)."""
        ep, _, _, gp = _make_event_processor()

        # find_duplicate_records returns an in-progress duplicate (no processed one)
        twin = {"_key": "dup-1", "indexingStatus": ProgressStatus.IN_PROGRESS.value}
        gp.find_duplicate_records = AsyncMock(return_value=[twin])
        gp.batch_update_nodes = AsyncMock()

        doc = {"_key": "rec-1", "md5Checksum": "abc123", "recordType": "FILE", "sizeInBytes": 100}
        gp.get_document = _rereads_as_still_in_flight(twin, doc)
        result = await ep._check_duplicate_by_md5(b"hello world", doc)
        assert result.skip_indexing is True
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value


# ===========================================================================
# Coverage: OCR exception and OCR path (lines 410, 417-427)
# ===========================================================================


class TestOnEventOcrPath:
    """Test PDF OCR processing path."""

    @pytest.mark.asyncio
    async def test_a_pdf_that_needs_no_ocr_goes_to_docling(self) -> None:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_with_docling = MagicMock(side_effect=_mock_processor_gen)
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(needs_ocr=False), \
             patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}):
            event_data = _make_event_payload(extension="pdf")
            await _drain(ep.on_event(event_data))

        processor.process_pdf_with_docling.assert_called_once()
        processor.process_pdf_document_with_ocr.assert_not_called()

    @pytest.mark.asyncio
    async def test_pdf_needs_ocr_uses_ocr_handler(self):
        """PDF that needs OCR goes through OCR handler (lines 417-427)."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             _pdf_inspection(needs_ocr=True):
            event_data = _make_event_payload(extension="pdf")
            events = await _drain(ep.on_event(event_data))

        processor.process_pdf_document_with_ocr.assert_called_once()


# ===========================================================================
# _normalize_content_for_dedup
# ===========================================================================


class TestNormalizeContentForDedup:
    """Tests for _normalize_content_for_dedup."""

    def test_plain_bytes_returned_as_is(self):
        """Non-JSON, non-HTML bytes are returned unchanged."""
        ep, _, _, _ = _make_event_processor()
        content = b"just plain text"
        result = ep._normalize_content_for_dedup(content)
        assert result == content

    def test_html_mime_type_strips_scripts_and_extracts_text(self):
        """HTML content strips script/style tags and extracts text."""
        ep, _, _, _ = _make_event_processor()
        html = b"<html><script>var x=1;</script><body><p>Hello</p></body></html>"
        result = ep._normalize_content_for_dedup(html, mime_type="text/html")
        assert b"var x=1" not in result
        assert b"Hello" in result

    def test_html_strips_local_id_and_emoji_attrs(self):
        """HTML normalization removes local-id, id, data-emoji-* attributes."""
        ep, _, _, _ = _make_event_processor()
        html = b'<div local-id="abc" id="x" data-emoji-id="e1" data-emoji-fallback="f">Text</div>'
        result = ep._normalize_content_for_dedup(html, mime_type="text/html")
        assert b"Text" in result
        assert b"abc" not in result

    def test_confluence_page_record_type_triggers_html_normalization(self):
        """CONFLUENCE_PAGE record type triggers HTML normalization even without HTML mime."""
        ep, _, _, _ = _make_event_processor()
        html = b"<p>Confluence content</p><style>.x{}</style>"
        result = ep._normalize_content_for_dedup(html, record_type="CONFLUENCE_PAGE")
        assert b".x{}" not in result
        assert b"Confluence content" in result

    def test_confluence_blogpost_triggers_html_normalization(self):
        ep, _, _, _ = _make_event_processor()
        html = b"<p>Blog</p>"
        result = ep._normalize_content_for_dedup(html, record_type="CONFLUENCE_BLOGPOST")
        assert b"Blog" in result

    def test_comment_record_type_triggers_html_normalization(self):
        ep, _, _, _ = _make_event_processor()
        html = b"<p>A comment</p>"
        result = ep._normalize_content_for_dedup(html, record_type="COMMENT")
        assert b"A comment" in result

    def test_inline_comment_record_type_triggers_html_normalization(self):
        ep, _, _, _ = _make_event_processor()
        html = b"<p>Inline</p>"
        result = ep._normalize_content_for_dedup(html, record_type="INLINE_COMMENT")
        assert b"Inline" in result

    def test_html_with_empty_text_returns_original(self):
        """HTML that produces empty text returns original content."""
        ep, _, _, _ = _make_event_processor()
        html = b"<script>only scripts</script>"
        result = ep._normalize_content_for_dedup(html, mime_type="text/html")
        assert result == html

    def test_json_with_block_groups_extracts_data(self):
        """JSON with block_groups extracts data fields."""
        ep, _, _, _ = _make_event_processor()
        payload = {
            "block_groups": [
                {"data": {"text": "hello"}},
                {"data": {"text": "world"}},
            ]
        }
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert b"hello" in result
        assert b"world" in result

    def test_json_with_blocks_extracts_data(self):
        """JSON with blocks key extracts data fields."""
        ep, _, _, _ = _make_event_processor()
        payload = {"blocks": [{"data": {"val": 42}}]}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert b"42" in result

    def test_json_block_groups_with_null_data_skipped(self):
        """Block groups with None data are skipped."""
        ep, _, _, _ = _make_event_processor()
        payload = {"block_groups": [{"data": None}, {"data": {"x": 1}}]}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert b'"x": 1' in result

    def test_json_block_groups_all_null_data_returns_original(self):
        """Block groups where all data is None returns original content."""
        ep, _, _, _ = _make_event_processor()
        payload = {"block_groups": [{"data": None}]}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert result == content

    def test_plain_json_dict_sorted(self):
        """Plain JSON dict is re-serialized with sorted keys."""
        ep, _, _, _ = _make_event_processor()
        payload = {"z": 1, "a": 2}
        content = json.dumps(payload).encode("utf-8")
        result = ep._normalize_content_for_dedup(content)
        assert result == json.dumps({"a": 2, "z": 1}, sort_keys=True).encode("utf-8")

    def test_json_list_returns_original(self):
        """JSON that parses to a list (not dict) returns original content."""
        ep, _, _, _ = _make_event_processor()
        content = b"[1, 2, 3]"
        result = ep._normalize_content_for_dedup(content)
        assert result == content

    def test_invalid_json_returns_original(self):
        """Invalid JSON returns original content."""
        ep, _, _, _ = _make_event_processor()
        content = b"not json at all"
        result = ep._normalize_content_for_dedup(content)
        assert result == content


# ===========================================================================
# _check_duplicate_by_md5 - source key fallback to "id"
# ===========================================================================


class TestCheckDuplicateSourceKeyFallback:
    """Test processed_duplicate source key falls back to 'id' field."""

    @pytest.mark.asyncio
    async def test_processed_duplicate_uses_id_when_key_missing(self):
        """copy_document_relationships uses processed_duplicate['id'] when _key is missing."""
        ep, _, _, gp = _make_event_processor()
        processed = {
            "id": "dup-src-id",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-1",
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"_key": "target", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=200):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        gp.copy_document_relationships.assert_awaited_once_with("dup-src-id", "target")


# ===========================================================================
# on_event - SQL_TABLE / SQL_VIEW routing
# ===========================================================================


class TestOnEventSqlRouting:
    """Tests for SQL_TABLE and SQL_VIEW dispatch."""

    @pytest.mark.asyncio
    async def test_sql_table_mime_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_TABLE"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.SQL_TABLE.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["record_type"] == "SQL_TABLE"

    @pytest.mark.asyncio
    async def test_sql_table_extension_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_TABLE"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.SQL_TABLE.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_sql_structured_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_sql_view_mime_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_VIEW"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(mime_type=MimeTypes.SQL_VIEW.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["record_type"] == "SQL_VIEW"

    @pytest.mark.asyncio
    async def test_sql_view_extension_routes_to_sql_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "SQL_VIEW"}
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(extension=ExtensionTypes.SQL_VIEW.value)
            events = await _drain(ep.on_event(event_data))

        assert len(events) == 3
        processor.process_sql_structured_data.assert_called_once()


# ===========================================================================
# on_event - event_type passed to processors
# ===========================================================================


class TestOnEventPassesEventType:
    """Verify event_type is forwarded to processor methods."""

    @pytest.mark.asyncio
    async def test_event_type_passed_to_docx_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                event_type=EventTypes.NEW_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["event_type"] == EventTypes.NEW_RECORD.value

    @pytest.mark.asyncio
    async def test_event_type_passed_to_image_processor(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_image = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.PNG.value,
                event_type=EventTypes.NEW_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_image.call_args[1]
        assert call_kwargs["event_type"] == EventTypes.NEW_RECORD.value

    @pytest.mark.asyncio
    async def test_event_type_passed_to_google_slides(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_pptx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.GOOGLE_SLIDES.value,
                event_type=EventTypes.UPDATE_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_pptx_document.call_args[1]
        assert call_kwargs["event_type"] == EventTypes.UPDATE_RECORD.value


# ===========================================================================
# on_event - prev_virtual_record_id passed to processor
# ===========================================================================


class TestOnEventPrevVirtualRecordId:
    """Verify prev_virtual_record_id is forwarded to processor calls."""

    @pytest.mark.asyncio
    async def test_new_record_passes_prev_vrid_as_none(self):
        """NEW_RECORD event passes prev_virtual_record_id=None."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        processor.process_docx_document = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                extension=ExtensionTypes.DOCX.value,
                event_type=EventTypes.NEW_RECORD.value,
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_docx_document.call_args[1]
        assert call_kwargs["prev_virtual_record_id"] is None

    @pytest.mark.asyncio
    async def test_update_reconciliation_type_passes_prev_vrid(self):
        """UPDATE on reconciliation type forwards prev_virtual_record_id."""
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "prev-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            event_data = _make_event_payload(
                mime_type=MimeTypes.SQL_TABLE.value,
                extension=ExtensionTypes.SQL_TABLE.value,
                event_type=EventTypes.UPDATE_RECORD.value,
                virtual_record_id="prev-vrid",
            )
            await _drain(ep.on_event(event_data))

        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["prev_virtual_record_id"] == "prev-vrid"


class TestVectorMembershipHooks:
    @pytest.mark.asyncio
    async def test_duplicate_attach_syncs_membership(self):
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-p",
            "virtualRecordId": "vr-shared",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "summaryDocumentId": "sum-p",
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"_key": "r-new", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
                result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        sync.assert_awaited_once_with("vr-shared")

    @pytest.mark.asyncio
    async def test_n1_vrid_split_rewrites_old_vrid(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "shared-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1", "rec-2"])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            with patch.object(ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock) as rewrite:
                event_data = _make_event_payload(
                    mime_type=MimeTypes.SQL_TABLE.value,
                    extension=ExtensionTypes.SQL_TABLE.value,
                    event_type=EventTypes.UPDATE_RECORD.value,
                    virtual_record_id="shared-vrid",
                )
                await _drain(ep.on_event(event_data))

        rewrite.assert_awaited_once_with("shared-vrid")
        call_kwargs = processor.process_sql_structured_data.call_args[1]
        assert call_kwargs["virtual_record_id"] != "shared-vrid"
        assert call_kwargs["prev_virtual_record_id"] == "shared-vrid"

    @pytest.mark.asyncio
    async def test_duplicate_attach_skips_sync_when_vrid_missing(self):
        ep, _, _, gp = _make_event_processor()
        processed = {
            "_key": "dup-empty",
            "indexingStatus": ProgressStatus.EMPTY.value,
        }
        gp.find_duplicate_records.return_value = [processed]
        doc = {"_key": "r-new", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            with patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
                result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_to_one_reconciliation_does_not_rewrite(self):
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "recordType": "SQL_TABLE",
            "virtualRecordId": "solo-vrid",
        }
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
        processor.process_sql_structured_data = MagicMock(side_effect=_mock_processor_gen)

        with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
             return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
            with patch.object(ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock) as rewrite:
                event_data = _make_event_payload(
                    mime_type=MimeTypes.SQL_TABLE.value,
                    extension=ExtensionTypes.SQL_TABLE.value,
                    event_type=EventTypes.UPDATE_RECORD.value,
                    virtual_record_id="solo-vrid",
                )
                await _drain(ep.on_event(event_data))

        rewrite.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_delegates_to_indexing_pipeline(self):
        ep, _, processor, _ = _make_event_processor()
        processor.indexing_pipeline.sync_vector_membership = AsyncMock()

        await ep.sync_vector_membership("vr-1")

        processor.indexing_pipeline.sync_vector_membership.assert_awaited_once_with("vr-1")

    @pytest.mark.asyncio
    async def test_sync_without_pipeline_fails_the_event(self):
        """Must not acknowledge work that never happened.

        The handler yields INDEXING_COMPLETE straight after this call, so
        returning quietly would ack a syncVectorMembership event whose update was
        never applied, with nothing to revisit it. IndexingError is transient, so
        an event arriving before the pipeline is wired succeeds on retry and a
        real misconfiguration dead-letters visibly.
        """
        from app.exceptions.indexing_exceptions import IndexingError
        from app.services.messaging.error_classifier import (
            MessageErrorClassifier,
            MessageErrorType,
        )

        ep, _, processor, _ = _make_event_processor()
        processor.indexing_pipeline = None

        with pytest.raises(IndexingError) as exc:
            await ep.sync_vector_membership("vr-1")

        assert (
            MessageErrorClassifier.classify_by_exception(exc.value)
            == MessageErrorType.TRANSIENT
        )

    @pytest.mark.asyncio
    async def test_sync_ignores_blank_vrid(self):
        ep, _, processor, _ = _make_event_processor()
        processor.indexing_pipeline.sync_vector_membership = AsyncMock()

        await ep.sync_vector_membership("")

        processor.indexing_pipeline.sync_vector_membership.assert_not_awaited()



from app.exceptions.indexing_exceptions import IndexingError  # noqa: E402


def _fail_every_write_except_md5():
    """`_check_duplicate_by_md5` persists md5Checksum before it reaches any of
    the writes under test, so a blanket failure would trip that one instead."""
    async def _update(record_id, collection, fields):
        return "md5Checksum" in fields

    return AsyncMock(side_effect=_update)



class TestFailedGraphWritesAreNotReportedAsSuccess:
    """A failed write must not be turned into "skip indexing".

    `on_event` answers `skip_indexing=True` by emitting PARSING_COMPLETE and
    INDEXING_COMPLETE and consuming the message, and the reconciliation sweep in
    `indexing_main` only revisits QUEUED/IN_PROGRESS records. So a write that
    quietly failed here strands a record that is neither indexed nor ever looked
    at again. `IndexingError` classifies as transient: the consumer redelivers,
    and a persistent failure dead-letters visibly.
    """

    @pytest.mark.asyncio
    async def test_failed_md5_write_raises(self):
        ep, _, _, gp = _make_event_processor()
        gp.update_node = AsyncMock(return_value=False)
        doc = {"_key": "r1", "recordType": "FILE", "sizeInBytes": 10}

        with pytest.raises(IndexingError, match="md5Checksum"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_failed_duplicate_field_write_raises(self):
        """Without this the record is marked complete with no virtualRecordId,
        so it has neither vectors of its own nor a share of the duplicate's."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }]

        async def _fail_the_identity_write(record_id, collection, fields):
            return "virtualRecordId" not in fields

        gp.update_node = AsyncMock(side_effect=_fail_the_identity_write)
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with pytest.raises(IndexingError, match="duplicate record fields"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_failed_relationship_copy_raises(self):
        """The record would otherwise be marked COMPLETED while missing the
        departments/categories/topics edges it inherited the content of."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }]
        gp.copy_document_relationships = AsyncMock(return_value=False)
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with pytest.raises(IndexingError, match="relationships"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_failed_mention_copy_raises_before_the_record_is_completed(self):
        """A same-collection duplicate is never indexed, so a mention copy lost
        here would never be retried once the record is COMPLETED."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }]
        gp.copy_named_entity_mentions = AsyncMock(side_effect=RuntimeError("graph down"))
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with patch(
            "app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
            AsyncMock(return_value=True),
        ), pytest.raises(IndexingError, match="mentions"):
            await ep._check_duplicate_by_md5(b"payload", doc)
        written = [c.args[2] for c in gp.update_node.await_args_list]
        assert not any(w.get("indexingStatus") == ProgressStatus.COMPLETED.value for w in written)

    @pytest.mark.asyncio
    async def test_with_the_flag_off_no_mention_copy_can_fail_a_duplicate(self):
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }]
        gp.copy_named_entity_mentions = AsyncMock(side_effect=RuntimeError("graph down"))
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with patch(
            "app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
            AsyncMock(return_value=False),
        ):
            await ep._check_duplicate_by_md5(b"payload", doc)
        gp.copy_named_entity_mentions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_queued_write_raises(self):
        """The in-flight branch: QUEUED is the only handle the sweeper has, so
        losing that write is what strands the record for good."""
        ep, gp = _make_multi_collection_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }]
        gp.update_node = _fail_every_write_except_md5()
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }

        with pytest.raises(IndexingError, match="QUEUED"):
            await ep._check_duplicate_by_md5(b"payload", doc)

    @pytest.mark.asyncio
    async def test_a_successful_in_flight_duplicate_still_skips(self):
        """The happy path is unchanged: raising is reserved for real failures."""
        ep, gp = _make_multi_collection_event_processor()
        twin = {
            "_key": "dup-1",
            "connectorName": "GOOGLE_DRIVE",
            "indexingStatus": ProgressStatus.IN_PROGRESS.value,
        }
        gp.find_duplicate_records.return_value = [twin]
        doc = {
            "_key": "r1", "md5Checksum": "abc", "connectorName": "GOOGLE_DRIVE",
            "recordType": "FILE", "sizeInBytes": 10,
        }
        gp.get_document = _rereads_as_still_in_flight(twin, doc)

        result = await ep._check_duplicate_by_md5(b"payload", doc)

        assert result.skip_indexing is True
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value


# ===========================================================================
# Duplicate attach: the record's status is written last
# ===========================================================================

_TWIN = {
    "_key": "twin",
    "orgId": "org-1",
    "md5Checksum": "abc",
    "recordType": "FILE",
    "sizeInBytes": 10,
    "virtualRecordId": "vr-1",
    "summaryDocumentId": "sum-1",
    "indexingStatus": ProgressStatus.COMPLETED.value,
    "extractionStatus": ProgressStatus.COMPLETED.value,
}


def _twin(**overrides):
    return {**_TWIN, **overrides}


def _copy(key="r1", **overrides):
    return {
        "_key": key,
        "orgId": "org-1",
        "md5Checksum": "abc",
        "recordType": "FILE",
        "sizeInBytes": 10,
        "indexingStatus": ProgressStatus.QUEUED.value,
        "virtualRecordId": None,
        "summaryDocumentId": None,
        **overrides,
    }


def _step(key, fields):
    """Name one graph write by what it does to the record."""
    if "md5Checksum" in fields:
        return None
    if fields.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value:
        return ("claim", key)
    if "indexingStatus" in fields:
        return ("status", key)
    if "virtualRecordId" in fields:
        return ("identity", key)
    return None


def _record_attach(ep, gp, finds):
    """Wire the graph so every attach step is recorded, in order, with its fields.

    ``finds`` is what each duplicate lookup returns, in call order.
    """
    steps, fields_of = [], {}
    answers = list(finds)

    async def _find(**kwargs):
        steps.append(("find",))
        return answers.pop(0)

    async def _edges(src, dst):
        steps.append(("edges", f"{src}->{dst}"))
        return True

    async def _update(key, collection, fields):
        step = _step(key, fields)
        if step:
            steps.append(step)
            fields_of.setdefault(step, fields)
        return True

    async def _sync(vrid):
        steps.append(("sync", vrid))

    gp.find_duplicate_records = AsyncMock(side_effect=_find)
    gp.copy_document_relationships = AsyncMock(side_effect=_edges)
    gp.update_node = AsyncMock(side_effect=_update)
    gp.get_document = AsyncMock(return_value={"indexingStatus": ProgressStatus.IN_PROGRESS.value})
    return steps, fields_of, patch.object(ep, "sync_vector_membership", side_effect=_sync)


class TestProcessedTwinAttachOrdering:
    """The same-collection attach writes the record's status last.

    The record handler acknowledges a redelivered newRecord for a COMPLETED record.
    Writing COMPLETED first turned any failure in the edge copy or the
    membership sync into a permanent one: the record claimed to be indexed
    while its connectorId was missing from the VRID's points.
    """

    @pytest.mark.asyncio
    async def test_the_record_is_claimed_first_and_completed_last(self):
        ep, _, _, gp = _make_event_processor()
        steps, fields_of, sync = _record_attach(ep, gp, [[_twin()]])
        doc = _copy()

        with sync, patch("app.events.events.get_epoch_timestamp_in_ms", return_value=100):
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert steps == [
            ("find",),
            ("claim", "r1"),
            ("edges", "twin->r1"),
            ("identity", "r1"),
            ("sync", "vr-1"),
            ("status", "r1"),
        ]
        assert fields_of[("identity", "r1")] == {
            "virtualRecordId": "vr-1", "summaryDocumentId": "sum-1",
        }
        assert fields_of[("status", "r1")] == {
            "isDirty": False,
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "hasIndexedCopy": True,
            "processingStartedAt": None,
            "lastIndexTimestamp": 100,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "lastExtractionTimestamp": 100,
        }

    @pytest.mark.asyncio
    async def test_a_failed_sync_leaves_status_unwritten_and_restores_identity(self):
        ep, _, _, gp = _make_event_processor()
        steps, fields_of, _ = _record_attach(ep, gp, [[_twin()]])
        doc = _copy(virtualRecordId="vr-own", summaryDocumentId="sum-own")

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=IndexingError("qdrant down"),
        ) as sync:
            with pytest.raises(IndexingError, match="qdrant down"):
                await ep._check_duplicate_by_md5(b"x", doc)

        assert ("status", "r1") not in steps
        assert gp.update_node.await_args.args == (
            "r1", "records", {"virtualRecordId": "vr-own", "summaryDocumentId": "sum-own"},
        )
        # A sync that raised may already have written some points, so the
        # VRID is recomputed once the identity is back.
        assert [c.args for c in sync.await_args_list] == [("vr-1",), ("vr-1",)]

    @pytest.mark.asyncio
    async def test_an_identity_write_whose_answer_is_lost_is_restored(self):
        """The write may have landed. Left alone, the record would keep the
        twin's VRID, and a later reindex of it would overwrite the twin's vectors."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.get_document = AsyncMock(return_value={"indexingStatus": ProgressStatus.IN_PROGRESS.value})
        writes = []

        async def _update(key, collection, fields):
            writes.append(fields)
            if fields.get("virtualRecordId") == "vr-1":
                raise RuntimeError("connection reset")
            return True

        gp.update_node = AsyncMock(side_effect=_update)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            with pytest.raises(RuntimeError, match="connection reset"):
                await ep._check_duplicate_by_md5(b"x", _copy(virtualRecordId="vr-own"))

        assert writes[-1] == {"virtualRecordId": "vr-own", "summaryDocumentId": None}
        # The sync never started, so no point can carry this record.
        sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_resync_is_reported_apart_from_the_restored_identity(self):
        """With the vector store down the identity is still put back; the log
        must not say it was not."""
        ep, logger, _, gp = _make_event_processor()
        steps, _, _ = _record_attach(ep, gp, [[_twin()]])

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=IndexingError("qdrant down"),
        ):
            with pytest.raises(IndexingError, match="qdrant down"):
                await ep._check_duplicate_by_md5(b"x", _copy())

        assert gp.update_node.await_args.args[2] == {"virtualRecordId": None, "summaryDocumentId": None}
        assert logger.error.call_count == 1
        assert "Could not re-sync VRID" in logger.error.call_args.args[0]

    @pytest.mark.asyncio
    async def test_a_failed_status_write_restores_identity_and_membership(self):
        """By then the record's connectorId is on the VRID's points."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.get_document = AsyncMock(return_value={"indexingStatus": ProgressStatus.IN_PROGRESS.value})

        async def _update(key, collection, fields):
            return fields.get("indexingStatus") != ProgressStatus.COMPLETED.value

        gp.update_node = AsyncMock(side_effect=_update)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock) as sync:
            with pytest.raises(IndexingError, match="duplicate record fields"):
                await ep._check_duplicate_by_md5(b"x", _copy())

        assert gp.update_node.await_args.args[2] == {"virtualRecordId": None, "summaryDocumentId": None}
        assert sync.await_count == 2

    @pytest.mark.asyncio
    async def test_a_status_write_that_landed_is_not_undone(self):
        """Only its answer was lost. Taking the identity away would leave a
        COMPLETED record with no VRID, which no redelivery ever revisits."""
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.get_document = AsyncMock(return_value={"indexingStatus": ProgressStatus.COMPLETED.value})

        async def _update(key, collection, fields):
            if fields.get("indexingStatus") == ProgressStatus.COMPLETED.value:
                raise RuntimeError("connection reset")
            return True

        gp.update_node = AsyncMock(side_effect=_update)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="connection reset"):
                await ep._check_duplicate_by_md5(b"x", _copy())

        assert gp.update_node.await_args.args[2]["indexingStatus"] == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_cancellation_mid_attach_restores_identity(self):
        import asyncio

        ep, _, _, gp = _make_event_processor()
        steps, _, _ = _record_attach(ep, gp, [[_twin()]])

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ):
            with pytest.raises(asyncio.CancelledError):
                await ep._check_duplicate_by_md5(b"x", _copy())

        assert steps[-1] == ("identity", "r1")
        assert gp.update_node.await_args.args[2] == {"virtualRecordId": None, "summaryDocumentId": None}

    @pytest.mark.asyncio
    async def test_a_failed_restore_is_reported_and_keeps_the_original_error(self):
        ep, logger, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        gp.get_document = AsyncMock(side_effect=RuntimeError("graph down"))

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            side_effect=IndexingError("qdrant down"),
        ):
            with pytest.raises(IndexingError, match="qdrant down"):
                await ep._check_duplicate_by_md5(b"x", _copy())

        assert "Could not restore" in logger.error.call_args.args[0]

    @pytest.mark.asyncio
    async def test_empty_twin_without_vrid_is_attached_without_a_sync(self):
        ep, _, _, gp = _make_event_processor()
        empty = _twin(virtualRecordId=None, indexingStatus=ProgressStatus.EMPTY.value,
                      extractionStatus=None)
        steps, fields_of, sync = _record_attach(ep, gp, [[empty]])

        with sync:
            result = await ep._check_duplicate_by_md5(b"x", _copy())

        assert result.skip_indexing is True
        assert [s[0] for s in steps] == ["find", "claim", "edges", "identity", "status"]
        assert fields_of[("status", "r1")]["indexingStatus"] == ProgressStatus.EMPTY.value
        assert fields_of[("status", "r1")]["extractionStatus"] == ProgressStatus.NOT_STARTED.value

    @pytest.mark.asyncio
    async def test_entities_are_synced_once_the_record_is_attached(self):
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [_twin()]
        seen = {}

        async def _sync_entities(doc):
            seen["status"] = doc.get("indexingStatus")

        ep.sink_orchestrator = MagicMock()
        ep.sink_orchestrator.sync_entities_for_duplicate = AsyncMock(side_effect=_sync_entities)

        with patch.object(ep, "sync_vector_membership", new_callable=AsyncMock):
            await ep._check_duplicate_by_md5(b"x", _copy())

        assert seen == {"status": ProgressStatus.COMPLETED.value}

    @pytest.mark.asyncio
    async def test_a_redelivery_after_a_failed_sync_attaches(self):
        """The failed attempt left nothing COMPLETED behind, so the retry is
        not acknowledged as already done and runs the whole attach again."""
        ep, _, _, gp = _make_event_processor()
        steps, fields_of, _ = _record_attach(ep, gp, [[_twin()], [_twin()]])
        doc = _copy()

        with patch.object(
            ep, "sync_vector_membership", new_callable=AsyncMock,
            # Fails, the restore's re-sync, then the retry's.
            side_effect=[IndexingError("qdrant down"), None, None],
        ):
            with pytest.raises(IndexingError, match="qdrant down"):
                await ep._check_duplicate_by_md5(b"x", doc)
            assert doc["indexingStatus"] != ProgressStatus.COMPLETED.value
            result = await ep._check_duplicate_by_md5(b"x", doc)

        assert result.skip_indexing is True
        assert steps[-1] == ("status", "r1")

    @pytest.mark.asyncio
    async def test_a_twin_that_finished_while_this_record_was_parked_is_attached_the_same_way(self):
        ep, _, _, gp = _make_event_processor()
        enriching = _twin(
            extractionStatus=ProgressStatus.IN_PROGRESS.value,
            processingStartedAt=10**15,
        )
        steps, _, sync = _record_attach(ep, gp, [[enriching]])
        # The re-read twin must still carry this content's checksum.
        gp.get_document = AsyncMock(
            return_value=_twin(md5Checksum=ep._hash_for_dedup(b"x", "FILE", None))
        )

        with sync:
            result = await ep._check_duplicate_by_md5(b"x", _copy())

        assert result.skip_indexing is True
        assert steps == [
            ("find",),
            ("status", "r1"),  # parked QUEUED
            ("claim", "r1"),
            ("edges", "twin->r1"),
            ("identity", "r1"),
            ("sync", "vr-1"),
            ("status", "r1"),
        ]

# ===========================================================================
# Code-graph projection on the duplicate path
# ===========================================================================


def _finished_duplicate(calls: list | None = None) -> Callable[..., Awaitable[DedupDecision]]:
    """Stand-in for a same-collection finished duplicate: runs the hook where
    the real helper does, just before the duplicate's status is copied."""

    async def _check(
        content: object, doc: dict,
        before_reusing_status: Callable[[], Awaitable[None]] | None = None,
        **_: object,
    ) -> DedupDecision:
        if before_reusing_status is not None:
            await before_reusing_status()
        if calls is not None:
            calls.append("status copied")
        return DedupDecision(virtual_record_id=None, skip_indexing=True)

    return _check


class TestDuplicateCodeFileProjection:
    """A deduped code file must still get its own block nodes.

    The blob and the vectors are shared through the duplicate's
    virtualRecordId, but block nodes are keyed by record -- so without this the
    file is searchable and yet completely absent from the code graph.
    """

    @staticmethod
    def _setup(record_type: str = "CODE_FILE") -> tuple:
        ep, _, processor, gp = _make_event_processor()
        gp.get_document.return_value = {
            "_key": "rec-1",
            "orgId": "org-1",
            "recordType": record_type,
            "recordGroupId": "repo-1",
            "connectorId": "conn-1",
            "virtualRecordId": "vr-original",
        }
        from app.events.processor import Processor

        # Autospec'd: a caller passing a kwarg the real processor does not
        # accept fails here instead of at runtime.
        processor.project_code_blocks_to_graph = create_autospec(
            Processor, instance=True
        ).project_code_blocks_to_graph
        return ep, processor

    @pytest.mark.asyncio
    async def test_duplicate_code_file_projects_under_the_new_record(self) -> None:
        ep, processor = self._setup()

        with patch.object(ep, "_check_duplicate_by_md5", side_effect=_finished_duplicate()):
            event_data = _make_event_payload(
                extension="py", mime_type="text/plain", record_name="client.py",
                buffer=b"def helper():\n    return 1\n",
            )
            event_data["payload"]["filePath"] = "src/client.py"
            await _drain(ep.on_event(event_data))

        processor.project_code_blocks_to_graph.assert_awaited_once()
        kwargs = processor.project_code_blocks_to_graph.await_args.kwargs
        assert kwargs["record_id"] == "rec-1"
        assert kwargs["org_id"] == "org-1"
        assert kwargs["record_group_id"] == "repo-1"
        assert kwargs["connector_id"] == "conn-1"
        assert kwargs["file_path"] == "src/client.py"
        assert kwargs["content"] == b"def helper():\n    return 1\n"

    @pytest.mark.asyncio
    async def test_duplicate_code_file_still_completes(self) -> None:
        """Projection is additive: the two terminal events are unchanged."""
        ep, processor = self._setup()

        with patch.object(ep, "_check_duplicate_by_md5", side_effect=_finished_duplicate()):
            event_data = _make_event_payload(extension="py", mime_type="text/plain")
            events = await _drain(ep.on_event(event_data))

        assert [e.event for e in events] == [
            IndexingEvent.PARSING_COMPLETE,
            IndexingEvent.INDEXING_COMPLETE,
        ]

    @pytest.mark.asyncio
    async def test_duplicate_non_code_file_is_not_projected(self) -> None:
        ep, processor = self._setup(record_type="FILE")

        with patch.object(ep, "_check_duplicate_by_md5", side_effect=_finished_duplicate()):
            event_data = _make_event_payload(extension=ExtensionTypes.DOCX.value)
            await _drain(ep.on_event(event_data))

        processor.project_code_blocks_to_graph.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_code_detected_from_record_name_when_extension_unknown(self) -> None:
        """Connectors walking a git tree often send text/plain and no extension."""
        ep, processor = self._setup()

        with patch.object(ep, "_check_duplicate_by_md5", side_effect=_finished_duplicate()):
            event_data = _make_event_payload(
                extension="unknown", mime_type="text/plain", record_name="main.ts",
            )
            await _drain(ep.on_event(event_data))

        processor.project_code_blocks_to_graph.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_str_buffer_is_encoded_before_projection(self) -> None:
        """`decode_source` indexes by byte offset and rejects a str."""
        ep, processor = self._setup()

        with patch.object(ep, "_check_duplicate_by_md5", side_effect=_finished_duplicate()):
            event_data = _make_event_payload(
                extension="py", mime_type="text/plain", record_name="client.py",
                buffer="def helper():\n    return 1\n",
            )
            await _drain(ep.on_event(event_data))

        content = processor.project_code_blocks_to_graph.await_args.kwargs["content"]
        assert content == b"def helper():\n    return 1\n"

    @pytest.mark.asyncio
    async def test_projection_lands_before_the_status_is_copied(self) -> None:
        """The copied COMPLETED status is what tells the edge builder the repo
        has drained, so it must not precede this file's blocks."""
        ep, processor = self._setup()
        calls: list[str] = []
        async def _project(**_: object) -> None:
            calls.append("projected")

        processor.project_code_blocks_to_graph.side_effect = _project

        with patch.object(ep, "_check_duplicate_by_md5",
                          side_effect=_finished_duplicate(calls)):
            await _drain(ep.on_event(_make_event_payload(extension="py", mime_type="text/plain")))

        assert calls == ["projected", "status copied"]
        assert processor.project_code_blocks_to_graph.await_args.kwargs["propagate_failure"] is True

    @pytest.mark.asyncio
    async def test_a_failed_projection_stops_the_record(self) -> None:
        ep, processor = self._setup()
        calls: list[str] = []
        processor.project_code_blocks_to_graph.side_effect = RuntimeError("graph down")

        with patch.object(ep, "_check_duplicate_by_md5",
                          side_effect=_finished_duplicate(calls)), \
             pytest.raises(RuntimeError, match="graph down"):
            await _drain(ep.on_event(_make_event_payload(extension="py", mime_type="text/plain")))

        assert calls == [], "the duplicate's status must not be copied"



class TestDedupHookBeforeReusedStatus:
    """`before_reusing_status` runs only where a finished duplicate's status is
    copied, and before that write."""

    @staticmethod
    def _doc() -> dict:
        return {"_key": "r1", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}

    @pytest.mark.asyncio
    async def test_runs_before_the_status_write(self) -> None:
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1", "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
        }]
        calls: list[str] = []
        real_update = ep.update_record_fields

        async def _update(doc: dict, fields: dict) -> bool:
            if "indexingStatus" in fields:
                calls.append("status")
            return await real_update(doc, fields)

        async def _hook() -> None:
            calls.append("hook")

        with patch.object(ep, "update_record_fields", side_effect=_update):
            result = await ep._check_duplicate_by_md5(b"x", self._doc(), before_reusing_status=_hook)

        assert result.skip_indexing is True
        # The attach writes IN_PROGRESS and then the twin's status; both after the hook.
        assert calls[0] == "hook"
        assert calls.count("status") >= 1

    @pytest.mark.asyncio
    async def test_a_raising_hook_leaves_the_status_alone(self) -> None:
        ep, _, _, gp = _make_event_processor()
        gp.find_duplicate_records.return_value = [{
            "_key": "dup-1", "virtualRecordId": "vr-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
        }]

        async def _hook() -> None:
            raise RuntimeError("graph down")

        doc = self._doc()
        with pytest.raises(RuntimeError, match="graph down"):
            await ep._check_duplicate_by_md5(b"x", doc, before_reusing_status=_hook)
        assert doc.get("indexingStatus") is None
        gp.copy_document_relationships.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_before_an_in_flight_duplicate_is_queued(self) -> None:
        """The twin's completion promotes a QUEUED copy straight to COMPLETED
        without processing it again, so its blocks must exist first (R-5)."""
        ep, _, _, gp = _make_event_processor()
        twin = {"_key": "dup-ip", "indexingStatus": ProgressStatus.IN_PROGRESS.value}
        gp.find_duplicate_records.return_value = [twin]
        doc = self._doc()
        gp.get_document = _rereads_as_still_in_flight(twin, doc)
        calls: list[str] = []
        real_update = ep.update_record_fields

        async def _update(doc: dict, fields: dict) -> bool:
            calls.append(fields.get("indexingStatus", "other"))
            return await real_update(doc, fields)

        async def _hook() -> None:
            calls.append("hook")

        with patch.object(ep, "update_record_fields", side_effect=_update):
            result = await ep._check_duplicate_by_md5(b"x", doc, before_reusing_status=_hook)

        assert result.skip_indexing is True
        assert calls.index("hook") < calls.index(ProgressStatus.QUEUED.value)

    @pytest.mark.asyncio
    async def test_runs_once_when_the_twin_finishes_while_queueing(self) -> None:
        ep, _, _, gp = _make_event_processor()
        twin = {"_key": "dup-ip", "indexingStatus": ProgressStatus.IN_PROGRESS.value}
        gp.find_duplicate_records.return_value = [twin]
        doc = self._doc()
        finished = {**twin, "virtualRecordId": "vr-1",
                    "indexingStatus": ProgressStatus.COMPLETED.value,
                    "extractionStatus": ProgressStatus.COMPLETED.value}
        gp.get_document = _rereads_as_still_in_flight(finished, doc)
        hook = AsyncMock()

        result = await ep._check_duplicate_by_md5(b"x", doc, before_reusing_status=hook)

        assert result.skip_indexing is True
        hook.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_code_file_emptied_to_zero_bytes_loses_its_old_blocks() -> None:
    """B3: a zero-byte file is marked EMPTY before parsing, so its last version's
    blocks, and their calls and imports, stayed in the code graph."""
    ep, _, processor, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    processor._drop_code_blocks = AsyncMock()
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "mark_record_status", new_callable=AsyncMock) as mark:
        await _drain(ep.on_event(_make_event_payload(
            mime_type="text/x-python", extension="py", buffer=b"",
            event_type=EventTypes.UPDATE_RECORD.value, record_name="m.py")))

    processor._drop_code_blocks.assert_awaited_once_with("rec-1")
    assert mark.await_args.args[1] == ProgressStatus.EMPTY
