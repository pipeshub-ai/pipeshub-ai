"""A record deleted while it is being indexed is an expected outcome, not a failure.

Nothing is lost and there is nothing left to write: the handler logs it once at
INFO, writes no status and leaves the record's queued duplicates alone, and no
later layer logs it at error.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.events.events import DedupDecision, EventProcessor
from app.events.processor import Processor
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    RecordDeletedError,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
    is_expected_outcome,
    is_record_deleted,
)
from tests.unit.services.messaging.test_record_handler import (
    _collect_events,
    _make_handler,
)


def _wrapped(inner: Exception) -> Exception:
    try:
        raise Exception(str(inner)) from inner
    except Exception as wrapped:
        return wrapped


class TestClassification:
    def test_it_is_terminal(self) -> None:
        assert MessageErrorClassifier.classify_by_exception(RecordDeletedError("r1")) == MessageErrorType.TERMINAL

    def test_a_wrapped_deletion_is_terminal(self) -> None:
        exc = _wrapped(RecordDeletedError("r1"))
        assert MessageErrorClassifier.classify_by_exception(exc) == MessageErrorType.TERMINAL

    def test_it_is_an_expected_outcome_bare_or_wrapped(self) -> None:
        assert is_record_deleted(RecordDeletedError("r1"))
        assert is_expected_outcome(RecordDeletedError("r1"))
        assert is_expected_outcome(_wrapped(RecordDeletedError("r1")))

    def test_an_error_raised_while_a_deletion_propagates_is_a_fault(self) -> None:
        try:
            try:
                raise RecordDeletedError("r1")
            finally:
                raise RuntimeError("cleanup failed")
        except RuntimeError as during:
            assert not is_record_deleted(during)
            assert not is_expected_outcome(during)

    def test_a_cleanup_fault_during_a_deletion_is_classified_as_itself(self) -> None:
        try:
            try:
                raise RecordDeletedError("r1")
            finally:
                raise ConnectionError("cleanup failed")
        except ConnectionError as during:
            assert MessageErrorClassifier.classify_by_exception(during) == MessageErrorType.TRANSIENT

    def test_other_failures_are_not(self) -> None:
        assert not is_expected_outcome(DocumentProcessingError("boom"))
        assert not is_expected_outcome(RuntimeError("boom"))
        assert not is_expected_outcome(None)
        assert not is_record_deleted(None)


class TestHandler:
    """Driven through the real event processor and Markdown processor: the record
    is deleted while it is parsed, so the processor's own read finds it gone."""

    async def _index(self, parse_fails_with: Exception | None = None) -> tuple[MagicMock, MagicMock, MagicMock]:
        logger = MagicMock()
        gp = AsyncMock()
        gp.update_node = AsyncMock(return_value=True)
        record = {"_key": "r1", "virtualRecordId": "vr1", "orgId": "org-1", "origin": "UPLOAD",
                  "recordType": "FILE", "recordName": "a.md", "indexingStatus": ProgressStatus.NOT_STARTED.value,
                  "mimeType": "text/markdown", "extension": "md", "version": 1,
                  "createdAtTimestamp": 1, "updatedAtTimestamp": 1}
        deleted = False
        gp.get_document = AsyncMock(side_effect=lambda *_a, **_k: None if deleted else dict(record))

        async def parse(*_a: object, **_k: object) -> MagicMock:
            nonlocal deleted
            if parse_fails_with is not None:
                raise parse_fails_with
            deleted = True
            return MagicMock(blocks=[], block_groups=[])

        parser = MagicMock()
        parser.extract_and_replace_images.return_value = ("# Hello", [])
        parser.parse_to_blocks = AsyncMock(side_effect=parse)
        with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
            processor = Processor(
                logger=logger, config_service=MagicMock(), indexing_pipeline=MagicMock(), graph_provider=gp,
                parsers={"md": parser}, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
            )
        event_processor = EventProcessor(logger, processor, gp, MagicMock())
        handler = _make_handler(logger=logger, event_processor=event_processor)
        handler._propagate_primary_failure_to_queued_duplicates = AsyncMock()
        handler._trigger_next_queued_duplicate = AsyncMock()
        payload = {"recordId": "r1", "virtualRecordId": "vr1", "orgId": "org-1", "recordName": "a.md",
                   "mimeType": "text/markdown", "extension": "md", "is_final_failure": True,
                   "signedUrl": "https://example.com/a.md"}
        with patch.object(event_processor, "_check_duplicate_by_md5", new_callable=AsyncMock,
                          return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"# Hello"):
            with pytest.raises(DocumentProcessingError if parse_fails_with else RecordDeletedError):
                await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)
        return logger, gp, handler

    @staticmethod
    def _failed_writes(gp: MagicMock) -> list:
        return [c for c in gp.update_node.call_args_list
                if c.args[2].get("indexingStatus") == ProgressStatus.FAILED.value]

    @pytest.mark.asyncio
    async def test_a_deletion_logs_one_info_and_writes_nothing(self) -> None:
        logger, gp, handler = await self._index()
        logger.error.assert_not_called()
        deleted = [c for c in logger.info.call_args_list if "deleted while it was being indexed" in str(c.args[0])]
        assert len(deleted) == 1
        assert self._failed_writes(gp) == []
        handler._propagate_primary_failure_to_queued_duplicates.assert_not_called()
        handler._trigger_next_queued_duplicate.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_processing_failure_is_still_a_final_failure(self) -> None:
        logger, gp, handler = await self._index(parse_fails_with=ValueError("bad file"))
        errors = [c for c in logger.error.call_args_list if "Final failure" in str(c.args[0])]
        assert len(errors) == 1
        assert len(self._failed_writes(gp)) == 1
        handler._propagate_primary_failure_to_queued_duplicates.assert_awaited_once()
