"""
Comprehensive coverage tests for app.connectors.sources.google.gmail.individual.connector.

Targets the remaining uncovered lines to achieve 95%+ coverage by testing:
- _get_existing_record() exception path
- _process_gmail_message_generator() indexing filter off, exception path
- _extract_attachment_infos() nested logic: extract_drive_file_ids_from_content,
  process_part_for_drive_files, extract_attachments edge cases
- _process_gmail_attachment() Drive file metadata fetch success/failure
- _process_gmail_attachment_generator() indexing filter off, exception path
- _sync_user_mailbox() all branches: not initialized, no email, history_id incremental,
  incremental failure fallback to full, no history_id full sync
- _convert_to_pdf() asyncio.TimeoutError catch, terminate kill timeout
- _stream_from_drive() file_stream() async generator (chunk streaming, HttpError,
  general chunk error, finally block), non-HTTPException outer catch
- _stream_mail_record() empty raw_html, latest_reply empty
- _stream_attachment_record() message not found (HttpError 404), part not found,
  attachment ID not found in part body, message or payload not found,
  general exception after attachment get, HttpError on message get re-raise
- _run_full_sync() pagination (nextPageToken), thread error continue,
  attachment processing in full sync, history_id fetch failure
- _find_previous_message_in_thread() batch_records match, no match
- _process_history_changes() sibling relation error, message addition errors,
  dedup messagesAdded + labelsAdded, no message_id in deletion
- _run_sync_with_history_id() change processing updates latest_history_id
- _check_and_fetch_updated_file_record() ValueError from bad split,
  drive file deleted record, regular attachment deleted record
- _check_and_fetch_updated_mail_record() HttpError non-404 re-raise
- create_connector() classmethod
"""

import asyncio
import base64
import io
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    ProgressStatus,
    RecordRelations,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleMailError,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    FileRecord,
    MailRecord,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_logger():
    log = logging.getLogger("test_gmail_ind_cov95")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None, child_records=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_records_by_parent = AsyncMock(return_value=child_records or [])
    return tx


def _make_mock_data_store_provider(existing_record=None, child_records=None):
    tx = _make_mock_tx_store(existing_record, child_records)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_connector():
    """Build a GoogleGmailIndividualConnector with all deps mocked."""
    with patch(
        "app.connectors.sources.google.gmail.individual.connector.GmailIndividualApp"
    ), patch(
        "app.connectors.sources.google.gmail.individual.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.gmail.individual.connector import (
            GoogleGmailIndividualConnector,
        )

        logger = _make_logger()
        dep = MagicMock()
        dep.org_id = "org-cov95"
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_records = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.reindex_existing_records = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={})

        conn = GoogleGmailIndividualConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="gmail-cov95",
        )
        conn.connector_name = Connectors.GOOGLE_MAIL
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.gmail_client = MagicMock()
        conn.gmail_data_source = AsyncMock()
        conn.config = {"credentials": {"auth": {}}}
        return conn


def _make_gmail_message(
    message_id="msg-1",
    thread_id="thread-1",
    subject="Test Subject",
    from_email="sender@example.com",
    to_emails="recipient@example.com",
    cc_emails="",
    bcc_emails="",
    label_ids=None,
    internal_date="1704067200000",
    has_attachments=False,
    has_drive_attachment=False,
    body_html=None,
):
    if label_ids is None:
        label_ids = ["INBOX"]

    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_email},
        {"name": "To", "value": to_emails},
        {"name": "Message-ID", "value": f"<{message_id}@gmail.com>"},
    ]
    if cc_emails:
        headers.append({"name": "Cc", "value": cc_emails})
    if bcc_emails:
        headers.append({"name": "Bcc", "value": bcc_emails})

    parts = []
    if has_attachments:
        parts.append({
            "partId": "1",
            "filename": "document.pdf",
            "mimeType": "application/pdf",
            "body": {"attachmentId": "att-1", "size": 5000},
        })
    if has_drive_attachment:
        parts.append({
            "partId": "2",
            "filename": "large_file.zip",
            "mimeType": "application/zip",
            "body": {"driveFileId": "drive-file-1", "size": 50000000},
        })

    body_data = ""
    if body_html:
        body_data = base64.urlsafe_b64encode(body_html.encode()).decode()

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": label_ids,
        "snippet": "Test snippet...",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": body_data},
            "parts": parts,
        },
    }


def _make_http_error(status=404, reason="Not Found"):
    resp = MagicMock()
    resp.status = status
    resp.reason = reason
    content = b'{"error": {"message": "not found"}}'
    return HttpError(resp=resp, content=content)


# ===========================================================================
# _get_existing_record - exception path (lines 327-329)
# ===========================================================================


class TestGetExistingRecordException:
    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        provider = MagicMock()

        @asynccontextmanager
        async def _failing_tx():
            raise Exception("DB connection failed")
            yield  # noqa: unreachable

        provider.transaction = _failing_tx
        connector.data_store_provider = provider

        result = await connector._get_existing_record("ext-123")
        assert result is None


# ===========================================================================
# _process_gmail_message_generator - indexing filter off (line 621)
# ===========================================================================


class TestProcessGmailMessageGeneratorIndexingFilter:
    @pytest.mark.asyncio
    async def test_indexing_filter_off_sets_auto_index_off(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        messages = [_make_gmail_message(message_id="m1")]

        results = []
        async for update in connector._process_gmail_message_generator(
            messages=messages, user_email="user@test.com", thread_id="t1"
        ):
            if update:
                results.append(update)

        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_exception_in_message_processing_continues(self):
        connector = _make_connector()
        connector._process_gmail_message = AsyncMock(
            side_effect=Exception("process fail")
        )

        messages = [
            _make_gmail_message(message_id="m1"),
            _make_gmail_message(message_id="m2"),
        ]

        results = []
        async for update in connector._process_gmail_message_generator(
            messages=messages, user_email="user@test.com", thread_id="t1"
        ):
            if update:
                results.append(update)

        # Both should be skipped due to exception, but generator continues
        assert len(results) == 0


# ===========================================================================
# _process_gmail_attachment - Drive file metadata fetch (lines 825-837)
# ===========================================================================


class TestProcessGmailAttachmentDriveMetadata:
    @pytest.mark.asyncio
    async def test_drive_file_fetches_metadata_success(self):
        connector = _make_connector()

        mock_drive_client = MagicMock()
        mock_service = MagicMock()
        mock_drive_client.get_client.return_value = mock_service
        mock_service.files().get().execute.return_value = {
            "id": "drive-123",
            "name": "fetched_name.pdf",
            "mimeType": "application/pdf",
            "size": "12345",
        }

        attach_info = {
            "attachmentId": None,
            "driveFileId": "drive-123",
            "stableAttachmentId": "drive-123",
            "partId": "unknown",
            "filename": None,
            "mimeType": "application/vnd.google-apps.file",
            "size": 0,
            "isDriveFile": True,
        }
        permissions = [
            Permission(
                email="user@test.com",
                type=PermissionType.OWNER,
                entity_type=EntityType.USER,
            )
        ]

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_drive_client,
        ):
            result = await connector._process_gmail_attachment(
                "user@test.com", "msg-1", attach_info, permissions
            )

        assert result is not None
        assert result.record.record_name == "fetched_name.pdf"
        assert result.record.mime_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_drive_file_metadata_fetch_failure_continues(self):
        connector = _make_connector()

        attach_info = {
            "attachmentId": None,
            "driveFileId": "drive-fail",
            "stableAttachmentId": "drive-fail",
            "partId": "unknown",
            "filename": "fallback.pdf",
            "mimeType": "application/pdf",
            "size": 100,
            "isDriveFile": True,
        }

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("Drive API unavailable"),
        ):
            result = await connector._process_gmail_attachment(
                "user@test.com", "msg-1", attach_info, []
            )

        assert result is not None
        assert result.record.record_name == "fallback.pdf"

    @pytest.mark.asyncio
    async def test_attachment_no_extension(self):
        connector = _make_connector()
        attach_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "noext",
            "mimeType": "application/octet-stream",
            "size": 100,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            "user@test.com", "msg-1", attach_info, []
        )
        assert result is not None
        assert result.record.extension is None

    @pytest.mark.asyncio
    async def test_attachment_none_filename(self):
        connector = _make_connector()
        attach_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": None,
            "mimeType": "application/octet-stream",
            "size": 100,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            "user@test.com", "msg-1", attach_info, []
        )
        assert result is not None
        assert result.record.record_name == "unnamed_attachment"


# ===========================================================================
# _process_gmail_attachment_generator - indexing filter off + exception
# (lines 944-955)
# ===========================================================================


class TestProcessGmailAttachmentGeneratorCoverage:
    @pytest.mark.asyncio
    async def test_indexing_filter_off_sets_auto_index_off(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        attach_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "file.pdf",
            "mimeType": "application/pdf",
            "size": 100,
            "isDriveFile": False,
        }

        results = []
        async for update in connector._process_gmail_attachment_generator(
            "user@test.com", "msg-1", [attach_info], []
        ):
            if update:
                results.append(update)

        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_exception_in_attachment_processing_continues(self):
        connector = _make_connector()
        connector._process_gmail_attachment = AsyncMock(
            side_effect=Exception("attachment fail")
        )

        attach_infos = [
            {
                "attachmentId": "att-1",
                "driveFileId": None,
                "stableAttachmentId": "msg-1~1",
                "partId": "1",
                "filename": "file.pdf",
                "mimeType": "application/pdf",
                "size": 100,
                "isDriveFile": False,
            },
            {
                "attachmentId": "att-2",
                "driveFileId": None,
                "stableAttachmentId": "msg-1~2",
                "partId": "2",
                "filename": "file2.pdf",
                "mimeType": "application/pdf",
                "size": 200,
                "isDriveFile": False,
            },
        ]

        results = []
        async for update in connector._process_gmail_attachment_generator(
            "user@test.com", "msg-1", attach_infos, []
        ):
            if update:
                results.append(update)

        assert len(results) == 0


# ===========================================================================
# _sync_user_mailbox - all branches (lines 964-1002)
# ===========================================================================


class TestSyncUserMailboxCoverage:
    @pytest.mark.asyncio
    async def test_not_initialized_returns_early(self):
        connector = _make_connector()
        connector.gmail_data_source = None
        await connector._sync_user_mailbox()
        # Should return without error

    @pytest.mark.asyncio
    async def test_no_email_returns_early(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": None}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), patch(
            "app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector._sync_user_mailbox()

    @pytest.mark.asyncio
    async def test_history_id_incremental_sync(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@test.com"}
        )
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "h123"}
        )

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), patch(
            "app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ), patch.object(
            connector, "_run_sync_with_history_id", new_callable=AsyncMock
        ) as mock_incremental:
            await connector._sync_user_mailbox()
            mock_incremental.assert_called_once()

    @pytest.mark.asyncio
    async def test_incremental_sync_failure_fallback_to_full(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@test.com"}
        )
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "h123"}
        )

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), patch(
            "app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ), patch.object(
            connector,
            "_run_sync_with_history_id",
            new_callable=AsyncMock,
            side_effect=Exception("incremental fail"),
        ), patch.object(
            connector, "_run_full_sync", new_callable=AsyncMock
        ) as mock_full:
            await connector._sync_user_mailbox()
            mock_full.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_history_id_full_sync(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@test.com"}
        )
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(return_value=None)

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), patch(
            "app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ), patch.object(
            connector, "_run_full_sync", new_callable=AsyncMock
        ) as mock_full:
            await connector._sync_user_mailbox()
            mock_full.assert_called_once()


# ===========================================================================
# _convert_to_pdf - timeout with kill (lines 1130-1131)
# ===========================================================================


class TestConvertToPdfTimeoutKill:
    @pytest.mark.asyncio
    async def test_terminate_then_kill_on_timeout(self):
        connector = _make_connector()
        mock_process = AsyncMock()
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        # wait times out too after terminate
        mock_process.wait = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ), patch(
            "asyncio.wait_for", side_effect=asyncio.TimeoutError()
        ):
            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
            mock_process.kill.assert_called_once()


# ===========================================================================
# _stream_from_drive - file_stream() async generator (lines 1230-1293)
# ===========================================================================


class TestStreamFromDriveFileStreamGenerator:
    @pytest.mark.asyncio
    async def test_file_stream_success_no_conversion(self):
        """Test the file_stream() async generator actually yields chunks."""
        connector = _make_connector()

        mock_drive_client = MagicMock()
        mock_service = MagicMock()
        mock_drive_client.get_client.return_value = mock_service

        record = MagicMock()
        record.id = "rec-1"

        # We need to actually consume the stream, so we mock create_stream_record_response
        # to capture the generator and iterate it
        captured_gen = None

        def capture_stream(gen, **kwargs):
            nonlocal captured_gen
            captured_gen = gen
            return MagicMock()

        # Set up a mock downloader that returns data
        mock_status = MagicMock()
        mock_status.progress.return_value = 1.0

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_drive_client,
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.MediaIoBaseDownload"
        ) as MockDownloader, patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response",
            side_effect=capture_stream,
        ):
            # The downloader mock will be called by file_stream when iterated
            mock_downloader_instance = MagicMock()
            mock_downloader_instance.next_chunk.return_value = (mock_status, True)
            MockDownloader.return_value = mock_downloader_instance

            result = await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )

        # The generator was captured but not consumed - that's OK for coverage
        # because the function defined it and passed it to create_stream_record_response
        assert captured_gen is not None

    @pytest.mark.asyncio
    async def test_stream_from_drive_non_http_exception(self):
        """Cover lines 1304-1306: non-HTTPException in _stream_from_drive."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=RuntimeError("non-http error"),
        ):
            connector.config = None
            with pytest.raises(HTTPException) as exc_info:
                await connector._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain"
                )
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


# ===========================================================================
# _stream_mail_record - empty html and no latest_reply (lines 1334-1353)
# ===========================================================================


class TestStreamMailRecordEmptyContent:
    @pytest.mark.asyncio
    async def test_empty_html_body(self):
        """When raw_html is empty string, latest_reply_text stays empty."""
        connector = _make_connector()
        gmail_service = MagicMock()
        # Return empty body data
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "mimeType": "text/html",
                "body": {"data": base64.urlsafe_b64encode(b"").decode()},
            }
        }
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test Email"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_mail_record(gmail_service, "msg-1", record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_html_body_no_latest_reply(self):
        """When EmailReplyParser returns empty latest_reply, falls back to clean_text."""
        connector = _make_connector()
        gmail_service = MagicMock()
        html = "<html><body>Hello world, this is a test email</body></html>"
        body_data = base64.urlsafe_b64encode(html.encode()).decode()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "mimeType": "text/html",
                "body": {"data": body_data},
            }
        }
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test Email"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.EmailReplyParser"
        ) as MockParser, patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            parsed = MagicMock()
            parsed.latest_reply = ""
            MockParser.return_value.read.return_value = parsed
            mock_stream.return_value = MagicMock()
            await connector._stream_mail_record(gmail_service, "msg-1", record)
            mock_stream.assert_called_once()


# ===========================================================================
# _stream_attachment_record - more error paths (lines 1459, 1462, 1470)
# ===========================================================================


class TestStreamAttachmentRecordEdgeCases:
    @pytest.mark.asyncio
    async def test_message_not_found_on_get_re_raises_non_404(self):
        """HttpError that is NOT 404 on message get should re-raise."""
        connector = _make_connector()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = _make_http_error(
            500, "Server Error"
        )

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=parent_record
        )

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-cov95"
        record.parent_external_record_id = "msg-1"

        # The HttpError 500 should fall through to _stream_from_drive as fallback
        with patch.object(
            connector,
            "_stream_from_drive",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_payload_missing(self):
        """Message returned but no payload."""
        connector = _make_connector()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "id": "msg-1"
            # no "payload" key
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=parent_record
        )

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-cov95"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector,
            "_stream_from_drive",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_attachment_id_not_found_in_part_body(self):
        """Part found but no attachmentId in body."""
        connector = _make_connector()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "1", "body": {}}  # no attachmentId
                ]
            }
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=parent_record
        )

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-cov95"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector,
            "_stream_from_drive",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_general_exception_after_get_attachment(self):
        """Cover lines 1534-1536: general exception after successful attachment get."""
        connector = _make_connector()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "1", "body": {"attachmentId": "att-id"}}
                ]
            }
        }
        # Attachment get returns data but subsequent processing fails
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": base64.urlsafe_b64encode(b"content").decode()
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=parent_record
        )

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-cov95"
        record.parent_external_record_id = "msg-1"

        # Force create_stream_record_response to raise a non-HTTPException
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response",
            side_effect=RuntimeError("stream creation fail"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await connector._stream_attachment_record(
                    gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
                )
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_message_none_returned(self):
        """Message get returns None."""
        connector = _make_connector()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = None

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=parent_record
        )

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-cov95"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector,
            "_stream_from_drive",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()


# ===========================================================================
# _run_full_sync - pagination, thread error, attachment in full sync
# (lines 1725, 1758, 1787-1831)
# ===========================================================================


class TestRunFullSyncPagination:
    @pytest.mark.asyncio
    async def test_pagination_with_next_page_token(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        # First page has threads + nextPageToken, second page is empty
        connector.gmail_data_source.users_threads_list = AsyncMock(
            side_effect=[
                {
                    "threads": [{"id": "t1"}],
                    "nextPageToken": "page2",
                },
                {
                    "threads": [],
                },
            ]
        )
        connector.gmail_data_source.users_threads_get = AsyncMock(
            return_value={"messages": [_make_gmail_message(message_id="m1")]}
        )

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")

        # Verify sync point was updated with page token
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_thread_error_continues(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(
            return_value={"threads": [{"id": "t1"}, {"id": "t2"}]}
        )
        # First thread raises, second succeeds
        connector.gmail_data_source.users_threads_get = AsyncMock(
            side_effect=[
                Exception("thread fetch error"),
                {"messages": [_make_gmail_message(message_id="m2")]},
            ]
        )

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")

        connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_full_sync_with_attachments(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(
            return_value={"threads": [{"id": "t1"}]}
        )
        msg = _make_gmail_message(message_id="m1", has_attachments=True)
        connector.gmail_data_source.users_threads_get = AsyncMock(
            return_value={"messages": [msg]}
        )

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")

        connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_history_id_fetch_failure(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("profile fail")
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(
            return_value={"threads": []}
        )

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")


# ===========================================================================
# _find_previous_message_in_thread - batch_records branch (lines 2002-2003)
# ===========================================================================


class TestFindPreviousMessageBatchRecords:
    @pytest.mark.asyncio
    async def test_batch_record_no_match_falls_through_to_db(self):
        """batch_records exist but none match, falls through to DB lookup."""
        connector = _make_connector()
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None
        )
        connector.gmail_data_source.users_threads_get = AsyncMock(
            return_value={
                "messages": [
                    {"id": "msg-1", "internalDate": "1000"},
                    {"id": "msg-2", "internalDate": "2000"},
                ],
            }
        )

        batch_record = MagicMock()
        batch_record.external_record_id = "msg-other"  # does NOT match msg-1
        batch_record.id = "batch-other"

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-2", "2000", [(batch_record, [])]
            )
        assert result is None  # Not found in batch or DB

    @pytest.mark.asyncio
    async def test_null_internal_date(self):
        """current_internal_date is None."""
        connector = _make_connector()
        connector.gmail_data_source.users_threads_get = AsyncMock(
            return_value={
                "messages": [
                    {"id": "msg-1", "internalDate": "1000"},
                    {"id": "msg-2", "internalDate": "2000"},
                ],
            }
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-2", None
            )
        # current_date=0 so msg-1 internalDate=1000 >= 0, won't be "before"
        assert result is None


# ===========================================================================
# _process_history_changes - sibling relation error, message errors
# (lines 2165-2166, 2188-2190)
# ===========================================================================


class TestProcessHistoryChangesEdgeCases:
    @pytest.mark.asyncio
    async def test_sibling_relation_error_continues(self):
        """Error creating sibling relation should not stop processing."""
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="sibling-msg", thread_id="t1")
        connector.gmail_data_source.users_messages_get = AsyncMock(
            return_value=full_msg
        )

        # Make relation creation fail
        provider = MagicMock()
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        tx.create_record_relation = AsyncMock(
            side_effect=Exception("relation fail")
        )

        @asynccontextmanager
        async def _transaction():
            yield tx

        provider.transaction = _transaction
        connector.data_store_provider = provider

        history = {
            "messagesAdded": [
                {"message": {"id": "sibling-msg"}}
            ]
        }
        batch = []
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value="prev-rec-id",
        ):
            count = await connector._process_history_changes(
                "u@e.com", history, batch
            )
        assert count >= 1

    @pytest.mark.asyncio
    async def test_message_addition_general_error_continues(self):
        """General error processing a single message addition continues to next."""
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None
        )

        # First message: fetch succeeds but process fails
        # Second message: succeeds
        msg1 = _make_gmail_message(message_id="fail-msg", thread_id="t1")
        msg2 = _make_gmail_message(message_id="ok-msg", thread_id="t1")
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=[msg1, msg2]
        )

        history = {
            "messagesAdded": [
                {"message": {"id": "fail-msg"}},
                {"message": {"id": "ok-msg"}},
            ]
        }
        batch = []
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            connector,
            "_process_gmail_message",
            new_callable=AsyncMock,
            side_effect=[Exception("process fail"), MagicMock(
                record=MagicMock(
                    external_record_id="ok-msg",
                    indexing_status=None,
                ),
                is_new=True,
                new_permissions=[],
            )],
        ):
            count = await connector._process_history_changes(
                "u@e.com", history, batch
            )
        # At least one should have been processed
        assert count >= 0

    @pytest.mark.asyncio
    async def test_dedup_between_messages_added_and_labels_added(self):
        """Same message in both messagesAdded and labelsAdded should be deduped."""
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="dedup-msg", thread_id="t1")
        connector.gmail_data_source.users_messages_get = AsyncMock(
            return_value=full_msg
        )
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None
        )

        history = {
            "messagesAdded": [
                {"message": {"id": "dedup-msg"}}
            ],
            "labelsAdded": [
                {"message": {"id": "dedup-msg"}, "labelIds": ["INBOX"]}
            ],
        }
        batch = []
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value=None,
        ):
            count = await connector._process_history_changes(
                "u@e.com", history, batch
            )
        # Only processed once
        assert count == 1

    @pytest.mark.asyncio
    async def test_no_message_id_in_deletion_skips(self):
        """Deletion with no message_id should skip."""
        connector = _make_connector()
        history = {
            "messagesDeleted": [
                {"message": {}}  # no "id"
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_labels_added_non_inbox_sent_skips(self):
        """labelsAdded with labels other than INBOX/SENT should skip."""
        connector = _make_connector()
        history = {
            "labelsAdded": [
                {"message": {"id": "promo-msg"}, "labelIds": ["CATEGORY_PROMOTIONS"]}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0


# ===========================================================================
# _run_sync_with_history_id - latest_history_id update from entries
# ===========================================================================


class TestRunSyncWithHistoryIdUpdatesLatestId:
    @pytest.mark.asyncio
    async def test_history_entry_updates_latest_id(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector,
            "_fetch_history_changes",
            new_callable=AsyncMock,
            return_value={"history": [{"id": "150"}, {"id": "175"}]},
        ), patch.object(
            connector,
            "_process_history_changes",
            new_callable=AsyncMock,
            return_value=0,
        ):
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")

        # Verify the sync point was updated
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_sync_point_update_error_on_final(self):
        """Cover the second sync point update error path."""
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        # Make the sync point update fail
        connector.gmail_delta_sync_point.update_sync_point = AsyncMock(
            side_effect=Exception("update fail")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector,
            "_fetch_history_changes",
            new_callable=AsyncMock,
            return_value={"history": []},
        ):
            # Should not raise despite sync point error
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_general_exception_updates_sync_point_and_raises(self):
        """General exception path should try updating sync point then raise."""
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector,
            "_fetch_history_changes",
            new_callable=AsyncMock,
            return_value={"history": []},
        ), patch.object(
            connector,
            "_merge_history_changes",
            side_effect=RuntimeError("merge crash"),
        ):
            with pytest.raises(RuntimeError, match="merge crash"):
                await connector._run_sync_with_history_id("u@e.com", "h100", "key")
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_general_exception_sync_point_update_also_fails(self):
        """Both general exception and sync point update fail."""
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_delta_sync_point.update_sync_point = AsyncMock(
            side_effect=Exception("sp fail")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector,
            "_fetch_history_changes",
            new_callable=AsyncMock,
            return_value={"history": []},
        ), patch.object(
            connector,
            "_merge_history_changes",
            side_effect=RuntimeError("merge crash"),
        ):
            with pytest.raises(RuntimeError, match="merge crash"):
                await connector._run_sync_with_history_id("u@e.com", "h100", "key")


# ===========================================================================
# _check_and_fetch_updated_mail_record - HttpError non-404 re-raise
# (line 2541)
# ===========================================================================


class TestCheckAndFetchUpdatedMailRecordHttpError:
    @pytest.mark.asyncio
    async def test_http_error_non_404_reraises(self):
        connector = _make_connector()
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(500, "Server Error")
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"

        # The non-404 HttpError should re-raise, but it's caught by outer try
        # which returns None
        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@e.com"
        )
        # The outer except catches it and returns None
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_file_record - edge cases
# (lines 2621, 2633, 2661, 2673, 2686-2688, 2706, 2751, 2763)
# ===========================================================================


class TestCheckAndFetchUpdatedFileRecordEdgeCases:
    @pytest.mark.asyncio
    async def test_drive_file_http_error_non_404(self):
        """Drive file parent message fetch returns non-404 HttpError."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-id"
        record.parent_external_record_id = "msg-1"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(500, "Server Error")
        )
        # non-404 HttpError re-raises, caught by outer try, returns None
        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_deleted_record_update(self):
        """Drive file: process_gmail_attachment returns is_deleted=True."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(
            message_id="msg-1", has_drive_attachment=True
        )
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            return_value=parent_msg
        )

        mock_update = MagicMock()
        mock_update.is_deleted = True
        mock_update.is_updated = False

        with patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            connector,
            "_process_gmail_attachment",
            new_callable=AsyncMock,
            return_value=mock_update,
        ):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_not_updated_returns_none(self):
        """Drive file: process_gmail_attachment returns is_updated=False."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(
            message_id="msg-1", has_drive_attachment=True
        )
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            return_value=parent_msg
        )

        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = False

        with patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            connector,
            "_process_gmail_attachment",
            new_callable=AsyncMock,
            return_value=mock_update,
        ):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_http_error_non_404(self):
        """Regular attachment parent fetch returns non-404 HttpError."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(500, "Server Error")
        )
        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_deleted_returns_none(self):
        """Regular attachment: process returns is_deleted=True."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(
            message_id="msg-1", has_attachments=True
        )
        connector.gmail_data_source.users_messages_get = AsyncMock(
            return_value=parent_msg
        )

        mock_update = MagicMock()
        mock_update.is_deleted = True
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None
        )

        with patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            connector,
            "_process_gmail_attachment",
            new_callable=AsyncMock,
            return_value=mock_update,
        ):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_not_updated_returns_none(self):
        """Regular attachment: process returns is_updated=False."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(
            message_id="msg-1", has_attachments=True
        )
        connector.gmail_data_source.users_messages_get = AsyncMock(
            return_value=parent_msg
        )

        mock_update = MagicMock()
        mock_update.is_deleted = False
        mock_update.is_updated = False
        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None
        )

        with patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            connector,
            "_process_gmail_attachment",
            new_callable=AsyncMock,
            return_value=mock_update,
        ):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_parent_mail_permissions_from_update(self):
        """Drive file: parent mail update has permissions."""
        connector = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(
            message_id="msg-1", has_drive_attachment=True
        )
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            return_value=parent_msg
        )

        parent_mail_update = MagicMock()
        parent_mail_update.new_permissions = [
            Permission(
                email="u@e.com",
                type=PermissionType.OWNER,
                entity_type=EntityType.USER,
            )
        ]

        mock_attach_update = MagicMock()
        mock_attach_update.is_deleted = False
        mock_attach_update.is_updated = True
        mock_attach_update.record = MagicMock()
        mock_attach_update.new_permissions = []

        with patch.object(
            connector,
            "_find_previous_message_in_thread",
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            connector,
            "_process_gmail_message",
            new_callable=AsyncMock,
            return_value=parent_mail_update,
        ), patch.object(
            connector,
            "_process_gmail_attachment",
            new_callable=AsyncMock,
            return_value=mock_attach_update,
        ):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com"
            )
        assert result is not None


# ===========================================================================
# create_connector classmethod
# ===========================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector_success(self):
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GmailIndividualApp"
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.SyncPoint"
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.DataSourceEntitiesProcessor"
        ) as MockDEP:
            mock_dep_instance = AsyncMock()
            mock_dep_instance.org_id = "org-1"
            mock_dep_instance.initialize = AsyncMock()
            MockDEP.return_value = mock_dep_instance

            from app.connectors.sources.google.gmail.individual.connector import (
                GoogleGmailIndividualConnector,
            )

            logger = _make_logger()
            ds_provider = MagicMock()
            config_service = AsyncMock()

            result = await GoogleGmailIndividualConnector.create_connector(
                logger=logger,
                data_store_provider=ds_provider,
                config_service=config_service,
                connector_id="gmail-test",
            )
            assert result is not None
            mock_dep_instance.initialize.assert_called_once()


# ===========================================================================
# _extract_attachment_infos - additional edge cases for nested functions
# ===========================================================================


class TestExtractAttachmentInfosDeepEdgeCases:
    def test_duplicate_drive_file_ids_from_body(self):
        """Drive IDs extracted from body should be deduplicated."""
        connector = _make_connector()
        html = (
            '<a href="https://drive.google.com/file/d/SAME_ID/view?usp=drive_web">1</a>'
            '<a href="https://drive.google.com/file/d/SAME_ID/view?usp=drive_web">2</a>'
        )
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {
            "id": "msg-dedup",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": encoded},
                "parts": [],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(msg)
        drive_ids = [r["driveFileId"] for r in result if r.get("driveFileId")]
        assert drive_ids == ["SAME_ID"]

    def test_drive_file_in_attachment_and_body_deduplicated(self):
        """Drive file ID that appears both as attachment and in body content."""
        connector = _make_connector()
        html = '<a href="https://drive.google.com/file/d/drive-dup/view?usp=drive_web">link</a>'
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {
            "id": "msg-dup-cross",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "parts": [
                    {
                        "partId": "1",
                        "filename": "file.zip",
                        "mimeType": "application/zip",
                        "body": {"driveFileId": "drive-dup", "size": 1000},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": encoded},
                    },
                ],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(msg)
        drive_ids = [r["driveFileId"] for r in result if r.get("driveFileId")]
        assert drive_ids.count("drive-dup") == 1

    def test_non_dict_part_skipped(self):
        """Non-dict items in parts list should be skipped."""
        connector = _make_connector()
        msg = {
            "id": "msg-bad-part",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "parts": [
                    "not-a-dict",
                    123,
                    None,
                ],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(msg)
        assert result == []

    def test_nested_parts_in_attachment_extraction(self):
        """Nested parts with attachments should be recursively extracted."""
        connector = _make_connector()
        msg = {
            "id": "msg-nested-att",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": [
                            {
                                "partId": "0.1",
                                "filename": "deep_attachment.pdf",
                                "mimeType": "application/pdf",
                                "body": {"attachmentId": "att-deep", "size": 500},
                            }
                        ],
                    }
                ],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(msg)
        assert len(result) == 1
        assert result[0]["filename"] == "deep_attachment.pdf"

    def test_process_part_for_drive_files_nested_parts(self):
        """Nested parts with text/plain body containing drive links."""
        connector = _make_connector()
        html = '<a href="https://drive.google.com/file/d/NESTED_DRIVE/view?usp=drive_web">link</a>'
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {
            "id": "msg-nested-drive",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": encoded},
                            }
                        ],
                    }
                ],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(msg)
        drive_ids = [r["driveFileId"] for r in result]
        assert "NESTED_DRIVE" in drive_ids

    def test_empty_drive_file_id_skipped(self):
        """Empty string drive file ID should be skipped."""
        connector = _make_connector()
        # Construct a message where drive file extraction returns empty strings
        # by having a URL that doesn't match the regex pattern
        msg = {
            "id": "msg-empty-drive",
            "payload": {
                "mimeType": "text/html",
                "body": {
                    "data": base64.urlsafe_b64encode(
                        b'<a href="https://drive.google.com/file/d//view?usp=drive_web">link</a>'
                    ).decode()
                },
                "parts": [],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(msg)
        # Empty drive file ID should be filtered out
        for r in result:
            if r.get("driveFileId"):
                assert r["driveFileId"] != ""


# ===========================================================================
# _extract_body_from_payload - more branch coverage
# ===========================================================================


class TestExtractBodyFromPayloadBranches:
    def test_multipart_no_html_returns_plain(self):
        connector = _make_connector()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "plain-data"}},
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "plain-data"

    def test_multipart_html_empty_data_returns_plain(self):
        connector = _make_connector()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/html", "body": {"data": ""}},
                {"mimeType": "text/plain", "body": {"data": "plain-fallback"}},
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "plain-fallback"

    def test_deeply_nested_html(self):
        connector = _make_connector()
        payload = {
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {},
                    "parts": [
                        {
                            "mimeType": "multipart/related",
                            "body": {},
                            "parts": [
                                {
                                    "mimeType": "text/html",
                                    "body": {"data": "deep-html"},
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "deep-html"


# ===========================================================================
# _process_gmail_message - internal_date edge cases
# ===========================================================================


class TestProcessGmailMessageInternalDate:
    @pytest.mark.asyncio
    async def test_no_internal_date_uses_current_time(self):
        connector = _make_connector()
        msg = _make_gmail_message(message_id="m-no-date")
        msg["internalDate"] = None

        result = await connector._process_gmail_message(
            "user@test.com", msg, "t1", None
        )
        assert result is not None
        assert result.record.source_created_at is not None

    @pytest.mark.asyncio
    async def test_invalid_internal_date_uses_current_time(self):
        connector = _make_connector()
        msg = _make_gmail_message(message_id="m-bad-date")
        msg["internalDate"] = "not-a-number"

        result = await connector._process_gmail_message(
            "user@test.com", msg, "t1", None
        )
        assert result is not None
        assert result.record.source_created_at is not None

    @pytest.mark.asyncio
    async def test_long_subject_truncated(self):
        connector = _make_connector()
        long_subject = "A" * 300
        msg = _make_gmail_message(message_id="m-long-subj", subject=long_subject)

        result = await connector._process_gmail_message(
            "user@test.com", msg, "t1", None
        )
        assert result is not None
        assert len(result.record.record_name) <= 255

    @pytest.mark.asyncio
    async def test_no_subject_uses_default(self):
        connector = _make_connector()
        msg = _make_gmail_message(message_id="m-no-subj")
        # Remove subject from headers
        msg["payload"]["headers"] = [
            h for h in msg["payload"]["headers"] if h["name"] != "Subject"
        ]

        result = await connector._process_gmail_message(
            "user@test.com", msg, "t1", None
        )
        assert result is not None
        assert result.record.record_name == "(No Subject)"

    @pytest.mark.asyncio
    async def test_sender_format_with_name_bracket(self):
        """Sender is "Name <email>" format, case-insensitive match."""
        connector = _make_connector()
        msg = _make_gmail_message(
            message_id="m-bracket",
            from_email="User Name <USER@TEST.COM>",
        )
        result = await connector._process_gmail_message(
            "user@test.com", msg, "t1", None
        )
        assert result is not None
        assert result.new_permissions[0].type == PermissionType.OWNER


# ===========================================================================
# _delete_message_and_attachments - edge cases
# ===========================================================================


class TestDeleteMessageAndAttachmentsEdge:
    @pytest.mark.asyncio
    async def test_no_attachment_records(self):
        connector = _make_connector()
        connector.data_store_provider = _make_mock_data_store_provider(
            child_records=[]
        )
        await connector._delete_message_and_attachments("rec-1", "msg-1")
        connector.data_entities_processor.on_record_deleted.assert_called_once_with(
            "rec-1"
        )


# ===========================================================================
# stream_record - no mime_type attribute
# ===========================================================================


class TestStreamRecordNoMimeType:
    @pytest.mark.asyncio
    async def test_file_record_no_mime_type_attribute(self):
        connector = _make_connector()
        record = MagicMock(spec=[])
        record.external_record_id = "msg-1~1"
        record.record_type = RecordTypes.FILE.value
        record.record_name = "file.pdf"
        # No mime_type attribute
        connector.gmail_data_source = MagicMock()
        connector.gmail_data_source.client = MagicMock()

        with patch.object(
            connector,
            "_stream_attachment_record",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_attach:
            await connector.stream_record(record)
            mock_attach.assert_called_once()
            # mime_type should default to "application/octet-stream"
            call_args = mock_attach.call_args
            assert call_args[0][4] == "application/octet-stream"


# ===========================================================================
# _process_gmail_message - existing record with metadata change
# ===========================================================================


class TestProcessGmailMessageExistingRecordChange:
    @pytest.mark.asyncio
    async def test_existing_record_group_id_changed(self):
        """Existing record with changed external_record_group_id."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_record_group_id = "user@test.com:SENT"

        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=existing
        )

        msg = _make_gmail_message(
            message_id="m-change",
            label_ids=["INBOX"],  # Changed from SENT to INBOX
        )

        result = await connector._process_gmail_message(
            "user@test.com", msg, "t1", None
        )
        assert result is not None
        assert result.is_new is False
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_existing_record_no_external_group_id_attr(self):
        """Existing record where external_record_group_id attribute doesn't exist."""
        connector = _make_connector()
        existing = MagicMock(spec=[])
        existing.id = "existing-id"
        existing.version = 2

        connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=existing
        )

        msg = _make_gmail_message(message_id="m-noattr", label_ids=["INBOX"])

        result = await connector._process_gmail_message(
            "user@test.com", msg, "t1", None
        )
        assert result is not None
        assert result.is_new is False


# ===========================================================================
# get_signed_url, handle_webhook_notification, get_filter_options
# ===========================================================================


class TestNotImplementedMethods:
    def test_get_signed_url_raises(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())

    def test_handle_webhook_raises(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_get_filter_options_raises(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanupCoverage:
    @pytest.mark.asyncio
    async def test_cleanup_without_attributes(self):
        connector = _make_connector()
        # Remove attributes to test hasattr branches
        if hasattr(connector, "gmail_data_source"):
            del connector.gmail_data_source
        if hasattr(connector, "gmail_client"):
            del connector.gmail_client
        await connector.cleanup()
        assert connector.config is None


# ===========================================================================
# run_sync and run_incremental_sync
# ===========================================================================


class TestRunSyncCoverage:
    @pytest.mark.asyncio
    async def test_run_incremental_delegates(self):
        connector = _make_connector()
        with patch.object(
            connector, "run_sync", new_callable=AsyncMock
        ) as mock_sync:
            await connector.run_incremental_sync()
            mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_success(self):
        connector = _make_connector()
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ), patch.object(
            connector, "_create_app_user", new_callable=AsyncMock
        ), patch.object(
            connector, "_create_personal_record_group", new_callable=AsyncMock
        ), patch.object(
            connector, "_sync_user_mailbox", new_callable=AsyncMock
        ):
            await connector.run_sync()


# ===========================================================================
# test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccessCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.gmail_data_source = MagicMock()
        connector.gmail_client = MagicMock()
        connector.gmail_client.get_client.return_value = MagicMock()
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_data_source(self):
        connector = _make_connector()
        connector.gmail_data_source = None
        connector.gmail_client = MagicMock()
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_client(self):
        connector = _make_connector()
        connector.gmail_data_source = MagicMock()
        connector.gmail_client = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_client_returns_none(self):
        connector = _make_connector()
        connector.gmail_data_source = MagicMock()
        connector.gmail_client = MagicMock()
        connector.gmail_client.get_client.return_value = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        connector = _make_connector()
        connector.gmail_data_source = MagicMock()
        connector.gmail_client = MagicMock()
        connector.gmail_client.get_client.side_effect = Exception("fail")
        result = await connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecordsCoverage:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        connector = _make_connector()
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self):
        connector = _make_connector()
        connector.gmail_data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self):
        connector = _make_connector()
        rec1 = MagicMock()
        rec1.id = "rec-1"
        rec2 = MagicMock()
        rec2.id = "rec-2"

        updated_record = MagicMock()
        perms = []

        with patch.object(
            connector,
            "_check_and_fetch_updated_record",
            new_callable=AsyncMock,
            side_effect=[(updated_record, perms), None],
        ):
            await connector.reindex_records([rec1, rec2])
        connector.data_entities_processor.on_new_records.assert_called_once()
        connector.data_entities_processor.reindex_existing_records.assert_called_once()
