"""Unit tests for gitlab StreamingHelper and _stream_with_eager_first_chunk.

Covers:
- _stream_with_eager_first_chunk: empty source, first-chunk error, normal yield
- stream_record: TICKET dispatch, PULL_REQUEST dispatch, FILE download, CODE_FILE download,
  unsupported type raises
- reindex_records: source-changed triggers on_new_records, unchanged skips,
  folder records (no extension) skipped, base-Record-class skipped
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.gitlab.streaming import (
    StreamingHelper,
    _stream_with_eager_first_chunk,
)

from .conftest import make_mock_connector

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _gen(*chunks: bytes) -> AsyncGenerator[bytes, None]:
    for chunk in chunks:
        yield chunk


async def _failing_gen() -> AsyncGenerator[bytes, None]:
    raise RuntimeError("stream error")
    yield b""  # noqa: unreachable


def _make_record(record_type: str, record_name: str = "file.py") -> MagicMock:
    r = MagicMock()
    r.id = "rec-1"
    r.record_type = record_type
    r.record_name = record_name
    r.weburl = "https://gitlab.com/ns/proj/-/issues/1"
    r.external_record_group_id = "42-work-items"
    r.external_revision_id = "1000"
    r.mime_type = "text/plain"
    r.external_record_id = "ext-1"
    return r


# ===========================================================================
# _stream_with_eager_first_chunk
# ===========================================================================


class TestStreamWithEagerFirstChunk:
    async def test_empty_generator_yields_nothing(self) -> None:
        async def empty() -> AsyncGenerator[bytes, None]:
            return
            yield b""

        result_gen = await _stream_with_eager_first_chunk(empty())
        chunks = [chunk async for chunk in result_gen]
        assert chunks == []

    async def test_single_chunk_yielded(self) -> None:
        result_gen = await _stream_with_eager_first_chunk(_gen(b"hello"))
        chunks = [chunk async for chunk in result_gen]
        assert chunks == [b"hello"]

    async def test_multiple_chunks_all_yielded(self) -> None:
        result_gen = await _stream_with_eager_first_chunk(_gen(b"a", b"b", b"c"))
        chunks = [chunk async for chunk in result_gen]
        assert chunks == [b"a", b"b", b"c"]

    async def test_error_in_first_chunk_raised_before_return(self) -> None:
        with pytest.raises(RuntimeError, match="stream error"):
            await _stream_with_eager_first_chunk(_failing_gen())


# ===========================================================================
# StreamingHelper.stream_record
# ===========================================================================


class TestStreamRecord:
    async def test_ticket_record_returns_streaming_response(self) -> None:
        c = make_mock_connector()
        c.issues = MagicMock()
        c.issues.build_ticket_blocks = AsyncMock(return_value=b'{"blocks":[]}')
        helper = StreamingHelper(c)

        record = _make_record("TICKET")
        from fastapi.responses import StreamingResponse
        result = await helper.stream_record(record)
        assert isinstance(result, StreamingResponse)

    async def test_pull_request_record_returns_streaming_response(self) -> None:
        c = make_mock_connector()
        c.merge_requests = MagicMock()
        c.merge_requests.build_pull_request_blocks = AsyncMock(return_value=b'{"blocks":[]}')
        helper = StreamingHelper(c)

        record = _make_record("PULL_REQUEST")
        from fastapi.responses import StreamingResponse
        result = await helper.stream_record(record)
        assert isinstance(result, StreamingResponse)

    async def test_file_record_returns_streaming_response(self) -> None:
        c = make_mock_connector()
        c.attachments = MagicMock()
        c.attachments.fetch_attachment_content = MagicMock(return_value=_gen(b"bytes"))
        helper = StreamingHelper(c)

        with patch("app.connectors.sources.gitlab.streaming.create_stream_record_response") as mock_csr:
            mock_csr.return_value = MagicMock()
            record = _make_record("FILE", "report.pdf")
            record.mime_type = "application/pdf"
            await helper.stream_record(record)
            mock_csr.assert_called_once()

    async def test_code_file_record_returns_streaming_response(self) -> None:
        from app.models.entities import CodeFileRecord
        c = make_mock_connector()
        c.repos = MagicMock()
        c.repos._fetch_code_file_content = MagicMock(return_value=_gen(b"code"))

        helper = StreamingHelper(c)

        code_record = MagicMock(spec=CodeFileRecord)
        code_record.record_type = "CODE_FILE"
        code_record.record_name = "main.py"
        code_record.mime_type = "text/plain"
        code_record.external_record_id = "ext-1"
        code_record.id = "rec-1"

        with patch("app.connectors.sources.gitlab.streaming.create_stream_record_response") as mock_csr:
            mock_csr.return_value = MagicMock()
            await helper.stream_record(code_record)
            mock_csr.assert_called_once()

    async def test_code_file_non_code_file_record_raises(self) -> None:
        c = make_mock_connector()
        helper = StreamingHelper(c)

        record = _make_record("CODE_FILE")
        # Not a CodeFileRecord instance (is a generic MagicMock)
        with pytest.raises(ValueError, match="CodeFileRecord"):
            await helper.stream_record(record)

    async def test_unsupported_type_raises(self) -> None:
        c = make_mock_connector()
        helper = StreamingHelper(c)

        record = _make_record("UNKNOWN_TYPE")
        with pytest.raises(ValueError, match="Unsupported record type"):
            await helper.stream_record(record)


# ===========================================================================
# StreamingHelper.reindex_records
# ===========================================================================


class TestReindexRecords:
    async def test_empty_list_is_noop(self) -> None:
        c = make_mock_connector()
        helper = StreamingHelper(c)

        await helper.reindex_records([])
        c.data_entities_processor.on_new_records.assert_not_called()

    async def test_source_changed_calls_on_new_records(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = StreamingHelper(c)

        fresh_record = MagicMock()
        fresh_permissions: list = []
        c.merge_requests = MagicMock()
        c.merge_requests.check_and_fetch_updated_record_for_reindex = AsyncMock(
            return_value=(fresh_record, fresh_permissions)
        )
        c.data_entities_processor.on_new_records = AsyncMock()

        record = _make_record("PULL_REQUEST")
        await helper.reindex_records([record])
        c.data_entities_processor.on_new_records.assert_called_once()

    async def test_unchanged_record_queued_for_reindex(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = StreamingHelper(c)

        c.merge_requests = MagicMock()
        c.merge_requests.check_and_fetch_updated_record_for_reindex = AsyncMock(return_value=None)
        c.data_entities_processor.reindex_existing_records = AsyncMock()

        record = _make_record("PULL_REQUEST")
        # Give it a proper subtype so it's not skipped
        from app.models.entities import PullRequestRecord
        typed_record = MagicMock(spec=PullRequestRecord)
        typed_record.id = "rec-1"
        typed_record.record_type = "PULL_REQUEST"

        await helper.reindex_records([typed_record])
        c.data_entities_processor.reindex_existing_records.assert_called_once()

    async def test_folder_record_without_extension_skipped(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = StreamingHelper(c)

        c.merge_requests = MagicMock()
        c.merge_requests.check_and_fetch_updated_record_for_reindex = AsyncMock(return_value=None)
        c.data_entities_processor.reindex_existing_records = AsyncMock()

        # FILE record type with no extension = folder
        from app.models.entities import FileRecord
        folder_record = MagicMock(spec=FileRecord)
        folder_record.id = "folder-1"
        folder_record.record_type = "FILE"
        folder_record.extension = ""  # no extension = folder

        await helper.reindex_records([folder_record])
        c.data_entities_processor.reindex_existing_records.assert_not_called()


# ===========================================================================
# reindex_records — additional uncovered paths
# ===========================================================================


class TestReindexRecordsAdditional:
    async def test_data_source_not_initialized_raises(self) -> None:
        """reindex_records raises when data_source is None."""
        c = make_mock_connector()
        c.data_source = None
        helper = StreamingHelper(c)

        record = _make_record("PULL_REQUEST")
        with pytest.raises(Exception):
            await helper.reindex_records([record])

    async def test_per_record_exception_continues_others(self) -> None:
        """Exception for one record does not abort processing of others."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = StreamingHelper(c)

        fresh_record = MagicMock()
        fresh_permissions: list = []

        call_count = 0

        async def check_side(record):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("transient error")
            return (fresh_record, fresh_permissions)

        c.merge_requests = MagicMock()
        c.merge_requests.check_and_fetch_updated_record_for_reindex = AsyncMock(side_effect=check_side)
        c.data_entities_processor.on_new_records = AsyncMock()
        c.data_entities_processor.reindex_existing_records = AsyncMock()

        r1 = _make_record("PULL_REQUEST")
        r2 = _make_record("PULL_REQUEST")
        r2.id = "rec-2"

        # Should not raise even though r1 causes exception
        await helper.reindex_records([r1, r2])
        # r2 was processed successfully → on_new_records called
        c.data_entities_processor.on_new_records.assert_called()

    async def test_skip_base_record_class(self) -> None:
        """Records that are base Record type (not subclass) are skipped for reindex."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = StreamingHelper(c)

        c.merge_requests = MagicMock()
        c.merge_requests.check_and_fetch_updated_record_for_reindex = AsyncMock(return_value=None)
        c.data_entities_processor.reindex_existing_records = AsyncMock()

        # Dynamically create a class literally named "Record" (as the guard checks)
        RecordBase = type("Record", (), {})
        base_record = RecordBase()
        base_record.id = "base-1"
        base_record.record_type = "TICKET"

        await helper.reindex_records([base_record])
        c.data_entities_processor.reindex_existing_records.assert_not_called()

    async def test_not_implemented_error_from_reindex_existing_records(self) -> None:
        """NotImplementedError from reindex_existing_records is caught and logged."""
        from app.models.entities import PullRequestRecord
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = StreamingHelper(c)

        c.merge_requests = MagicMock()
        c.merge_requests.check_and_fetch_updated_record_for_reindex = AsyncMock(return_value=None)
        c.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=NotImplementedError("not implemented")
        )

        typed_record = MagicMock(spec=PullRequestRecord)
        typed_record.id = "rec-1"
        typed_record.record_type = "PULL_REQUEST"

        # Should not raise
        await helper.reindex_records([typed_record])
        c.logger.warning.assert_called()

    async def test_outer_exception_handler_raises(self) -> None:
        """Outer exception in reindex_records is logged and re-raised."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        helper = StreamingHelper(c)

        # Make refresh_token_if_needed raise to trigger outer handler
        c.runtime.refresh_token_if_needed = AsyncMock(side_effect=RuntimeError("outer error"))

        record = _make_record("PULL_REQUEST")
        with pytest.raises(RuntimeError):
            await helper.reindex_records([record])
        c.logger.error.assert_called()
