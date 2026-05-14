import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import Filter, FilterCollection, SyncFilterKey
from app.connectors.sources.google.common.connector_google_exceptions import GoogleMailError
from app.models.entities import (
    FileRecord,
    MailRecord,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


def _make_logger():
    log = logging.getLogger("test_gmail_individual_95")
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


@pytest.fixture
def connector():
    with patch(
        "app.connectors.sources.google.gmail.individual.connector.GoogleClient"
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
        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.reindex_existing_records = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {
                "access_token": "test-token",
                "refresh_token": "test-refresh",
            },
        })

        conn = GoogleGmailIndividualConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="gmail-ind-1",
            scope="personal",
            created_by="test-user-id",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.gmail_client = MagicMock()
        conn.gmail_data_source = AsyncMock()
        conn.config = {"credentials": {"access_token": "t", "refresh_token": "r"}}
        yield conn


class TestInit:
    @pytest.mark.asyncio
    async def test_init_success(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {"access_token": "t", "refresh_token": "r"},
        })
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value={"config": {"clientId": "cid", "clientSecret": "cs"}},
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            result = await connector.init()
        assert result is True

    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {},
            "credentials": {},
        })
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_oauth_config_not_found(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {},
        })
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_incomplete_credentials(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {},
        })
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value={"config": {"clientId": None, "clientSecret": None}},
        ):
            with pytest.raises(ValueError, match="Incomplete"):
                await connector.init()

    @pytest.mark.asyncio
    async def test_init_client_build_failure(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {"access_token": "t", "refresh_token": "r"},
        })
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value={"config": {"clientId": "cid", "clientSecret": "cs"}},
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("build failed"),
        ):
            with pytest.raises(ValueError, match="Failed to initialize"):
                await connector.init()

    @pytest.mark.asyncio
    async def test_init_no_tokens_warning(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {},
        })
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value={"config": {"clientId": "cid", "clientSecret": "cs"}},
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            result = await connector.init()
        assert result is True


class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_raises_when_no_client(self, connector):
        connector.gmail_client = None
        with pytest.raises(GoogleMailError):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_raises_when_no_datasource(self, connector):
        connector.gmail_data_source = None
        with pytest.raises(GoogleMailError):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_calls_refresh(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.refresh_google_datasource_credentials",
            new_callable=AsyncMock,
        ) as mock_refresh:
            await connector._get_fresh_datasource()
            mock_refresh.assert_called_once()


class TestExtractEmailFromHeaderEdge:
    def test_malformed_angle_brackets(self, connector):
        result = connector._extract_email_from_header(">alice@e.com<")
        assert result == ">alice@e.com<"


class TestProcessGmailMessageException:
    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, connector):
        connector._parse_gmail_headers = MagicMock(side_effect=Exception("parse fail"))
        msg = _make_gmail_message()
        result = await connector._process_gmail_message("u@e.com", msg, "t1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_email_skips_permissions(self, connector):
        msg = _make_gmail_message()
        result = await connector._process_gmail_message("", msg, "t1", None)
        assert result is not None
        assert result.new_permissions == []

    @pytest.mark.asyncio
    async def test_received_date_filter_does_not_skip_message(self, connector):
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": 99999999999999, "end": None},
            "type": "datetime",
            "operator": "is_after",
        })
        connector.sync_filters = FilterCollection(filters=[date_filter])
        msg = _make_gmail_message()
        result = await connector._process_gmail_message("u@e.com", msg, "t1", None)
        assert result is not None


class TestExtractAttachmentInfosEdgeCases:
    def test_extract_drive_ids_decode_error(self, connector):
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": "!!!invalid-base64!!!"},
                "headers": [],
                "parts": [],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 0

    def test_process_part_non_dict(self, connector):
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "headers": [],
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": ["not-a-dict"],
                    }
                ],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert isinstance(infos, list)

    def test_empty_body_data(self, connector):
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": ""},
                "headers": [],
                "parts": [],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 0

    def test_text_plain_body_with_drive_links(self, connector):
        html = '<a href="https://drive.google.com/file/d/ABC123/view?usp=drive_web">Link</a>'
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/plain",
                "body": {"data": encoded},
                "headers": [],
                "parts": [],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "ABC123"

    def test_nested_parts_with_drive_links(self, connector):
        html = '<a href="https://drive.google.com/file/d/NESTED_ID/view?usp=drive_web">Link</a>'
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "headers": [],
                "parts": [
                    {
                        "mimeType": "text/html",
                        "body": {"data": encoded},
                        "parts": [],
                    },
                ],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert any(i["driveFileId"] == "NESTED_ID" for i in infos)


class TestProcessGmailAttachmentException:
    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        connector._get_existing_record = AsyncMock(side_effect=Exception("db fail"))
        info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "f.pdf",
            "mimeType": "application/pdf",
            "size": 100,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment("u@e.com", "msg-1", info, [], "u@e.com:OTHERS")
        assert result is None

    @pytest.mark.asyncio
    async def test_existing_attachment_increments_version(self, connector):
        existing = MagicMock()
        existing.id = "existing-att-id"
        existing.version = 2
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "file.txt",
            "mimeType": "text/plain",
            "size": 100,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment("u@e.com", "msg-1", info, [], "u@e.com:OTHERS")
        assert result.record.version == 3

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False
        info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "file.txt",
            "mimeType": "text/plain",
            "size": 100,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment("u@e.com", "msg-1", info, [], "u@e.com:OTHERS")
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestConvertToPdf:
    @pytest.mark.asyncio
    async def test_successful_conversion(self, connector):
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("os.path.exists", return_value=True):
            result = await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert result == "/tmp/test.pdf"

    @pytest.mark.asyncio
    async def test_conversion_timeout(self, connector):
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_conversion_nonzero_return(self, connector):
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error msg"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("asyncio.wait_for", return_value=(b"", b"error msg")):
            mock_process.returncode = 1
            with pytest.raises(HTTPException):
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_conversion_output_not_found(self, connector):
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("os.path.exists", return_value=False):
            with pytest.raises(HTTPException):
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_conversion_general_exception(self, connector):
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("exec fail")):
            with pytest.raises(HTTPException):
                await connector._convert_to_pdf("/tmp/test.docx", "/tmp")


class TestStreamFromDrive:
    @pytest.mark.asyncio
    async def test_stream_from_drive_success(self, connector):
        mock_drive_client = MagicMock()
        mock_service = MagicMock()
        mock_drive_client.get_client.return_value = mock_service

        mock_status = MagicMock()
        mock_status.progress.return_value = 1.0
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.return_value = (mock_status, True)

        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_drive_client,
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_from_drive_with_pdf_conversion(self, connector):
        mock_drive_client = MagicMock()
        mock_service = MagicMock()
        mock_drive_client.get_client.return_value = mock_service

        mock_status = MagicMock()
        mock_status.progress.return_value = 1.0
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.return_value = (mock_status, True)

        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_drive_client,
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.MediaIoBaseDownload",
            return_value=mock_downloader,
        ), patch.object(
            connector, "_convert_to_pdf", new_callable=AsyncMock, return_value="/tmp/test.pdf"
        ), patch(
            "builtins.open", MagicMock()
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector._stream_from_drive(
                "drive-id", record, "file.docx", "application/msword",
                convertTo=MimeTypes.PDF.value,
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_from_drive_client_failure_no_credentials(self, connector):
        connector.config = None
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("auth fail"),
        ):
            with pytest.raises(HTTPException):
                await connector._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain"
                )

    @pytest.mark.asyncio
    async def test_stream_from_drive_general_exception(self, connector):
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected"),
        ):
            connector.config = None
            with pytest.raises(HTTPException):
                await connector._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain"
                )

    @pytest.mark.asyncio
    async def test_stream_from_drive_client_failure_with_service_account(self, connector):
        connector.config = {"credentials": {"auth": {"type": "service_account"}}}
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("auth fail"),
        ), patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
            return_value=MagicMock(),
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.build",
            return_value=MagicMock(),
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_from_drive_no_service_account_creds(self, connector):
        connector.config = {"credentials": {"auth": {}}}
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("auth fail"),
        ):
            with pytest.raises(HTTPException):
                await connector._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain"
                )


class TestStreamMailRecord:
    @pytest.mark.asyncio
    async def test_stream_mail_success(self, connector):
        gmail_service = MagicMock()
        html = "<html><body>Hello world</body></html>"
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
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_mail_record(gmail_service, "msg-1", record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_mail_not_found(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = _make_http_error(404)
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test"

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_stream_mail_http_error_non_404(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = _make_http_error(500, "Server Error")
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test"

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_stream_mail_general_exception(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = RuntimeError("fail")
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test"

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_stream_mail_preserves_links(self, connector):
        """markdownify keeps <a> href as [text](url) in the streamed output."""
        html = '<p>See the <a href="https://example.com/doc">document</a> here.</p>'
        body_data = base64.urlsafe_b64encode(html.encode()).decode()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {"mimeType": "text/html", "body": {"data": body_data}}
        }
        record = MagicMock()
        record.id = "rec-links"
        record.record_name = "Links Email"

        streamed_chunks: list[bytes] = []

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response",
            side_effect=lambda gen, **kwargs: gen,
        ):
            stream_gen = await connector._stream_mail_record(gmail_service, "msg-1", record)
            async for chunk in stream_gen:
                streamed_chunks.append(chunk)

        combined = b"".join(streamed_chunks).decode()
        assert "[document](https://example.com/doc)" in combined

    @pytest.mark.asyncio
    async def test_stream_mail_preserves_images(self, connector):
        """markdownify keeps <img> as ![alt](src) in the streamed output."""
        html = '<p>Logo: <img src="https://example.com/logo.png" alt="Logo" /></p>'
        body_data = base64.urlsafe_b64encode(html.encode()).decode()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {"mimeType": "text/html", "body": {"data": body_data}}
        }
        record = MagicMock()
        record.id = "rec-img"
        record.record_name = "Image Email"

        streamed_chunks: list[bytes] = []

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response",
            side_effect=lambda gen, **kwargs: gen,
        ):
            stream_gen = await connector._stream_mail_record(gmail_service, "msg-1", record)
            async for chunk in stream_gen:
                streamed_chunks.append(chunk)

        combined = b"".join(streamed_chunks).decode()
        assert "![Logo](https://example.com/logo.png)" in combined

    @pytest.mark.asyncio
    async def test_stream_mail_reply_extraction_strips_quoted_content(self, connector):
        """EmailReplyParser latest_reply is used when it finds a quoted block."""
        reply_text = "Thanks for your message!"
        html = (
            f"<p>{reply_text}</p>"
            "<blockquote>"
            "On Mon, Jan 1, 2024, Sender wrote:<br>Original message here."
            "</blockquote>"
        )
        body_data = base64.urlsafe_b64encode(html.encode()).decode()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {"mimeType": "text/html", "body": {"data": body_data}}
        }
        record = MagicMock()
        record.id = "rec-reply"
        record.record_name = "Reply Email"

        streamed_chunks: list[bytes] = []

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response",
            side_effect=lambda gen, **kwargs: gen,
        ):
            stream_gen = await connector._stream_mail_record(gmail_service, "msg-1", record)
            async for chunk in stream_gen:
                streamed_chunks.append(chunk)

        combined = b"".join(streamed_chunks).decode()
        assert reply_text in combined

    @pytest.mark.asyncio
    async def test_stream_mail_empty_html_body_streams_empty(self, connector):
        """An empty HTML body results in an empty streamed payload."""
        body_data = base64.urlsafe_b64encode(b"").decode()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {"mimeType": "text/html", "body": {"data": body_data}}
        }
        record = MagicMock()
        record.id = "rec-empty"
        record.record_name = "Empty Email"

        streamed_chunks: list[bytes] = []

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response",
            side_effect=lambda gen, **kwargs: gen,
        ):
            stream_gen = await connector._stream_mail_record(gmail_service, "msg-1", record)
            async for chunk in stream_gen:
                streamed_chunks.append(chunk)

        combined = b"".join(streamed_chunks).decode()
        assert combined == ""

    @pytest.mark.asyncio
    async def test_stream_mail_falls_back_to_full_text_when_no_reply_extracted(self, connector):
        """When EmailReplyParser returns no latest_reply, the full clean_text is streamed."""
        html = "<p>A standalone message with no quoted reply.</p>"
        body_data = base64.urlsafe_b64encode(html.encode()).decode()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {"mimeType": "text/html", "body": {"data": body_data}}
        }
        record = MagicMock()
        record.id = "rec-fallback"
        record.record_name = "Standalone Email"

        mock_parsed = MagicMock()
        mock_parsed.latest_reply = ""

        streamed_chunks: list[bytes] = []

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.EmailReplyParser"
        ) as mock_parser_cls, patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response",
            side_effect=lambda gen, **kwargs: gen,
        ):
            mock_parser_cls.return_value.read.return_value = mock_parsed
            stream_gen = await connector._stream_mail_record(gmail_service, "msg-1", record)
            async for chunk in stream_gen:
                streamed_chunks.append(chunk)

        combined = b"".join(streamed_chunks).decode()
        assert "standalone message" in combined


class TestStreamAttachmentRecord:
    @pytest.mark.asyncio
    async def test_drive_file_delegates_to_stream_from_drive(self, connector):
        gmail_service = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"

        with patch.object(
            connector, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "drive-file-id", record, "file.txt", "text/plain"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_attachment_success(self, connector):
        gmail_service = MagicMock()
        file_data = base64.urlsafe_b64encode(b"file content").decode()
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": file_data,
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        tx = _make_mock_tx_store(existing_record=parent_record)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "1", "body": {"attachmentId": "real-att-id"}}
                ]
            }
        }

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )
            mock_stream.assert_called()

    @pytest.mark.asyncio
    async def test_no_parent_message_raises(self, connector):
        gmail_service = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = None
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_gmail_attachment_failure_fallback_to_drive(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().attachments().get().execute.side_effect = _make_http_error(403)
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "1", "body": {"attachmentId": "att-id"}}
                ]
            }
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_and_drive_both_fail(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().attachments().get().execute.side_effect = _make_http_error(403)
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "1", "body": {"attachmentId": "att-id"}}
                ]
            }
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector, "_stream_from_drive", new_callable=AsyncMock,
            side_effect=Exception("drive fail"),
        ):
            with pytest.raises(HTTPException):
                await connector._stream_attachment_record(
                    gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
                )

    @pytest.mark.asyncio
    async def test_general_exception(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = RuntimeError("boom")
        gmail_service.users().messages().attachments().get().execute.side_effect = RuntimeError("boom")

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ):
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )

    @pytest.mark.asyncio
    async def test_attachment_with_pdf_conversion(self, connector):
        gmail_service = MagicMock()
        file_data = base64.urlsafe_b64encode(b"file content").decode()
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": file_data,
        }
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "1", "body": {"attachmentId": "att-id"}}
                ]
            }
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector, "_convert_to_pdf", new_callable=AsyncMock, return_value="/tmp/file.pdf"
        ), patch(
            "builtins.open", MagicMock()
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.docx", "application/msword",
                convertTo=MimeTypes.PDF.value,
            )
            mock_stream.assert_called()

    @pytest.mark.asyncio
    async def test_message_id_mismatch_warning(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "1", "body": {"attachmentId": "att-id"}}
                ]
            }
        }
        file_data = base64.urlsafe_b64encode(b"data").decode()
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": file_data,
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "actual-msg-id"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "actual-msg-id"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_attachment_record(
                gmail_service, "different-msg~1", record, "f.pdf", "application/pdf"
            )

    @pytest.mark.asyncio
    async def test_message_not_found_during_part_lookup(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = _make_http_error(404)

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_part_id_not_found(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [
                    {"partId": "99", "body": {"attachmentId": "att-id"}}
                ]
            }
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_mail_record_type(self, connector):
        record = MagicMock()
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        connector.gmail_data_source = MagicMock()
        connector.gmail_data_source.client = MagicMock()

        with patch.object(
            connector, "_stream_mail_record", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_mail:
            await connector.stream_record(record)
            mock_mail.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_file_record_type(self, connector):
        record = MagicMock()
        record.external_record_id = "msg-1~1"
        record.record_type = RecordTypes.FILE.value
        record.record_name = "file.pdf"
        record.mime_type = "application/pdf"
        connector.gmail_data_source = MagicMock()
        connector.gmail_data_source.client = MagicMock()

        with patch.object(
            connector, "_stream_attachment_record", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_attach:
            await connector.stream_record(record)
            mock_attach.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_file_id_raises(self, connector):
        record = MagicMock()
        record.external_record_id = None
        connector.gmail_data_source = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_no_datasource_raises(self, connector):
        record = MagicMock()
        record.external_record_id = "msg-1"
        connector.gmail_data_source = None

        with pytest.raises(HTTPException):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_reraises_http_exception(self, connector):
        record = MagicMock()
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        connector.gmail_data_source = MagicMock()
        connector.gmail_data_source.client = MagicMock()

        with patch.object(
            connector, "_stream_mail_record", new_callable=AsyncMock,
            side_effect=HTTPException(status_code=404, detail="Not found"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_general_exception(self, connector):
        record = MagicMock()
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        connector.gmail_data_source = MagicMock()
        connector.gmail_data_source.client = MagicMock()

        with patch.object(
            connector, "_stream_mail_record", new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record)
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


class TestCreateAppUser:
    @pytest.mark.asyncio
    async def test_creates_user_successfully(self, connector):
        await connector._create_app_user({"emailAddress": "user@example.com"})
        connector.data_entities_processor.on_new_app_users.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_email_raises(self, connector):
        with pytest.raises(ValueError):
            await connector._create_app_user({})


class TestCreatePersonalRecordGroup:
    @pytest.mark.asyncio
    async def test_creates_three_groups(self, connector):
        await connector._create_personal_record_group("user@example.com")
        assert connector.data_entities_processor.on_new_record_groups.call_count == 3

    @pytest.mark.asyncio
    async def test_no_email_raises(self, connector):
        with pytest.raises(ValueError):
            await connector._create_personal_record_group("")

    @pytest.mark.asyncio
    async def test_group_creation_error_continues(self, connector):
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=[Exception("fail"), None, None]
        )
        await connector._create_personal_record_group("user@example.com")


class TestSyncUserMailboxException:
    @pytest.mark.asyncio
    async def test_exception_propagates(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("profile fail")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch(
                 "app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), MagicMock()),
             ):
            with pytest.raises(Exception, match="profile fail"):
                await connector._sync_user_mailbox()


class TestRunFullSyncEdgeCases:
    @pytest.mark.asyncio
    async def test_skip_thread_with_no_id(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": None}],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")
        connector.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_thread_with_no_messages(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")

    @pytest.mark.asyncio
    async def test_sibling_relation_error_continues(self, connector):
        msg1 = _make_gmail_message(message_id="m1")
        msg2 = _make_gmail_message(message_id="m2")

        provider = MagicMock()
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        tx.create_record_relation = AsyncMock(side_effect=Exception("relation fail"))

        @asynccontextmanager
        async def _transaction():
            yield tx

        provider.transaction = _transaction
        connector.data_store_provider = provider

        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [msg1, msg2],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")
        connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_page_error_raises(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(
            side_effect=Exception("page error")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(Exception, match="page error"):
                await connector._run_full_sync("u@e.com", "key")

    @pytest.mark.asyncio
    async def test_overall_exception_propagates(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("fatal")
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(
            side_effect=Exception("fatal")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(Exception, match="fatal"):
                await connector._run_full_sync("u@e.com", "key")

    @pytest.mark.asyncio
    async def test_batch_processing_with_many_messages(self, connector):
        connector.batch_size = 2
        msgs = [_make_gmail_message(message_id=f"m{i}") for i in range(5)]
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": msgs,
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("u@e.com", "key")
        assert connector.data_entities_processor.on_new_records.call_count >= 2

    @pytest.mark.asyncio
    async def test_message_processing_error_continues(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_process_gmail_message", new_callable=AsyncMock,
                          side_effect=Exception("process fail")):
            await connector._run_full_sync("u@e.com", "key")


class TestMergeHistoryChanges:
    def test_merges_and_deduplicates(self, connector):
        inbox = {"history": [{"id": "1"}, {"id": "2"}]}
        sent = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3

    def test_sorts_by_id(self, connector):
        inbox = {"history": [{"id": "3"}]}
        sent = {"history": [{"id": "1"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert result["history"][0]["id"] == "1"
        assert result["history"][1]["id"] == "3"

    def test_empty_histories(self, connector):
        result = connector._merge_history_changes({"history": []}, {"history": []})
        assert result["history"] == []


class TestFetchHistoryChanges:
    @pytest.mark.asyncio
    async def test_single_page(self, connector):
        connector.gmail_data_source.users_history_list = AsyncMock(return_value={
            "history": [{"id": "1"}],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._fetch_history_changes("100", "INBOX")
        assert len(result["history"]) == 1

    @pytest.mark.asyncio
    async def test_paginated(self, connector):
        connector.gmail_data_source.users_history_list = AsyncMock(side_effect=[
            {"history": [{"id": "1"}], "nextPageToken": "page2"},
            {"history": [{"id": "2"}]},
        ])
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._fetch_history_changes("100", "INBOX")
        assert len(result["history"]) == 2

    @pytest.mark.asyncio
    async def test_empty_history(self, connector):
        connector.gmail_data_source.users_history_list = AsyncMock(return_value={})
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._fetch_history_changes("100", "INBOX")
        assert result["history"] == []

    @pytest.mark.asyncio
    async def test_http_error_reraise(self, connector):
        connector.gmail_data_source.users_history_list = AsyncMock(
            side_effect=_make_http_error(404)
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(HttpError):
                await connector._fetch_history_changes("100", "INBOX")

    @pytest.mark.asyncio
    async def test_general_exception(self, connector):
        connector.gmail_data_source.users_history_list = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(RuntimeError):
                await connector._fetch_history_changes("100", "INBOX")


class TestFindPreviousMessageInThread:
    @pytest.mark.asyncio
    async def test_finds_previous_message(self, connector):
        existing = MagicMock()
        existing.id = "prev-record-id"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-2", "2000"
            )
        assert result == "prev-record-id"

    @pytest.mark.asyncio
    async def test_single_message_returns_none(self, connector):
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [{"id": "msg-1", "internalDate": "1000"}],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-1", "1000"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_earlier_messages(self, connector):
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-1", "1000"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_finds_in_batch_records(self, connector):
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        batch_record = MagicMock()
        batch_record.external_record_id = "msg-1"
        batch_record.id = "batch-rec-id"
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-2", "2000", [(batch_record, [])]
            )
        assert result == "batch-rec-id"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        connector.gmail_data_source.users_threads_get = AsyncMock(
            side_effect=Exception("fail")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-1", "1000"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_previous_record_in_db(self, connector):
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._find_previous_message_in_thread(
                "t1", "msg-2", "2000"
            )
        assert result is None


class TestDeleteMessageAndAttachments:
    @pytest.mark.asyncio
    async def test_deletes_message_and_attachments(self, connector):
        attachment = MagicMock()
        attachment.id = "att-rec-id"
        connector.data_store_provider = _make_mock_data_store_provider(child_records=[attachment])
        await connector._delete_message_and_attachments("rec-1", "msg-1")
        connector.data_entities_processor.on_record_deleted.assert_any_call("att-rec-id")
        connector.data_entities_processor.on_record_deleted.assert_any_call("rec-1")

    @pytest.mark.asyncio
    async def test_handles_attachment_deletion_error(self, connector):
        attachment = MagicMock()
        attachment.id = "att-rec-id"
        connector.data_store_provider = _make_mock_data_store_provider(child_records=[attachment])
        connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=[Exception("fail"), None]
        )
        await connector._delete_message_and_attachments("rec-1", "msg-1")

    @pytest.mark.asyncio
    async def test_handles_general_exception(self, connector):
        provider = MagicMock()

        @asynccontextmanager
        async def _failing_tx():
            raise Exception("DB error")
            yield

        provider.transaction = _failing_tx
        connector.data_store_provider = provider
        await connector._delete_message_and_attachments("rec-1", "msg-1")


class TestProcessHistoryChanges:
    @pytest.mark.asyncio
    async def test_process_message_addition(self, connector):
        connector.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="new-msg", thread_id="t1")
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "new-msg", "threadId": "t1"}}
            ]
        }
        batch = []
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes("u@e.com", history, batch)
        assert count >= 1
        assert len(batch) >= 1

    @pytest.mark.asyncio
    async def test_process_message_deletion(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        history = {
            "messagesDeleted": [
                {"message": {"id": "del-msg"}}
            ]
        }
        batch = []
        with patch.object(connector, "_delete_message_and_attachments", new_callable=AsyncMock):
            count = await connector._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_labels_added_inbox(self, connector):
        connector.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="label-msg", thread_id="t1")
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "labelsAdded": [
                {"message": {"id": "label-msg"}, "labelIds": ["INBOX"]}
            ]
        }
        batch = []
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_labels_added_trash_deletes(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        history = {
            "labelsAdded": [
                {"message": {"id": "trash-msg"}, "labelIds": ["TRASH"]}
            ]
        }
        batch = []
        with patch.object(connector, "_delete_message_and_attachments", new_callable=AsyncMock) as mock_del:
            count = await connector._process_history_changes("u@e.com", history, batch)
        mock_del.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_existing_messages(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        history = {
            "messagesAdded": [
                {"message": {"id": "existing-msg"}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_not_found_at_source(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "gone-msg"}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_fetch_error(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=RuntimeError("network fail")
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "fail-msg"}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_no_thread_id_skips(self, connector):
        connector.gmail_data_source = AsyncMock()
        msg = _make_gmail_message(message_id="no-thread", thread_id="t1")
        del msg["threadId"]
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "no-thread"}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_null_message_from_api(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "null-msg"}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_sibling_relation_created(self, connector):
        connector.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="sibling-msg", thread_id="t1")
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "sibling-msg"}}
            ]
        }
        batch = []
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value="prev-rec-id"):
            count = await connector._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_general_exception_returns_zero(self, connector):
        history = {
            "messagesAdded": "not-a-list"
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletion_not_found_skips(self, connector):
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)
        history = {
            "messagesDeleted": [
                {"message": {"id": "not-in-db"}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletion_error_continues(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        history = {
            "messagesDeleted": [
                {"message": {"id": "del-msg-1"}},
                {"message": {"id": "del-msg-2"}},
            ]
        }
        batch = []
        with patch.object(connector, "_delete_message_and_attachments",
                          new_callable=AsyncMock,
                          side_effect=[Exception("fail"), None]):
            count = await connector._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_process_attachments_in_addition(self, connector):
        connector.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(
            message_id="att-msg", thread_id="t1", has_attachments=True
        )
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "att-msg"}}
            ]
        }
        batch = []
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes("u@e.com", history, batch)
        assert count >= 2

    @pytest.mark.asyncio
    async def test_http_error_non_404_continues(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(500, "Server Error")
        )
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "err-msg"}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_indexing_filter_applied(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        connector.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="filter-msg", thread_id="t1")
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "filter-msg"}}
            ]
        }
        batch = []
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes("u@e.com", history, batch)
        assert count >= 1
        assert batch[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_no_message_id_in_addition_skips(self, connector):
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)
        history = {
            "messagesAdded": [
                {"message": {}}
            ]
        }
        batch = []
        count = await connector._process_history_changes("u@e.com", history, batch)
        assert count == 0


class TestRunSyncWithHistoryId:
    @pytest.mark.asyncio
    async def test_inbox_error_handled(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          side_effect=[Exception("inbox fail"), {"history": []}]):
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_sent_error_handled(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          side_effect=[{"history": []}, Exception("sent fail")]):
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_processes_changes_and_batches(self, connector):
        connector.batch_size = 1
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}), \
             patch.object(connector, "_merge_history_changes", return_value={
                 "history": [{"id": "1"}]
             }), \
             patch.object(connector, "_process_history_changes", new_callable=AsyncMock,
                          return_value=2):
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")
        connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_final_batch_error_handled(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": [{"id": "1"}]}), \
             patch.object(connector, "_process_history_changes", new_callable=AsyncMock,
                          return_value=1) as mock_ph:
            async def side_effect(user_email, entry, batch):
                batch.append((MagicMock(), []))
                return 1
            mock_ph.side_effect = side_effect
            connector.data_entities_processor.on_new_records = AsyncMock(
                side_effect=Exception("batch fail")
            )
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_profile_error_keeps_latest_history_id(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("profile fail")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}):
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_sync_point_update_error(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        connector.gmail_delta_sync_point.update_sync_point = AsyncMock(
            side_effect=Exception("sync fail")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}):
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_http_error_propagates(self, connector):
        connector.gmail_data_source = AsyncMock()
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}), \
             patch.object(connector, "_merge_history_changes",
                          side_effect=_make_http_error(404)):
            with pytest.raises(HttpError):
                await connector._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_general_exception_updates_sync_point(self, connector):
        connector.gmail_data_source = AsyncMock()
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}), \
             patch.object(connector, "_merge_history_changes",
                          side_effect=RuntimeError("merge fail")):
            with pytest.raises(RuntimeError):
                await connector._run_sync_with_history_id("u@e.com", "h100", "key")
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_change_processing_error_continues(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": [{"id": "1"}, {"id": "2"}]}), \
             patch.object(connector, "_process_history_changes", new_callable=AsyncMock,
                          side_effect=[Exception("fail"), 1]):
            await connector._run_sync_with_history_id("u@e.com", "h100", "key")


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_success(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_create_app_user", new_callable=AsyncMock), \
             patch.object(connector, "_create_personal_record_group", new_callable=AsyncMock), \
             patch.object(connector, "_sync_user_mailbox", new_callable=AsyncMock):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_no_email_raises(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_create_app_user", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="Email address not found"):
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagates(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("fail")
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(Exception):
                await connector.run_sync()


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self, connector):
        with patch.object(connector, "run_sync", new_callable=AsyncMock) as mock_sync:
            await connector.run_incremental_sync()
            mock_sync.assert_called_once()


class TestHandleWebhookNotification:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_references(self, connector):
        await connector.cleanup()
        assert connector.gmail_data_source is None
        assert connector.gmail_client is None
        assert connector.config is None

    @pytest.mark.asyncio
    async def test_cleanup_handles_exception(self, connector):
        type(connector).gmail_data_source = PropertyMock(side_effect=Exception("fail"))
        try:
            await connector.cleanup()
        except Exception:
            pass
        finally:
            del type(connector).gmail_data_source


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


class TestConnectionTestException:
    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connector):
        connector.gmail_client.get_client.side_effect = Exception("fail")
        result = await connector.test_connection_and_access()
        assert result is False


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector):
        await connector.reindex_records([])
        connector.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_datasource_raises(self, connector):
        connector.gmail_data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_records(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL

        updated_record = MagicMock()
        perms = [Permission(email="u@e.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]

        with patch.object(connector, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock, return_value=(updated_record, perms)):
            await connector.reindex_records([record])
        connector.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_updated_records(self, connector):
        record = MagicMock()
        record.id = "rec-1"

        with patch.object(connector, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock, return_value=None):
            await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_error_continues(self, connector):
        rec1 = MagicMock()
        rec1.id = "rec-1"
        rec2 = MagicMock()
        rec2.id = "rec-2"

        with patch.object(connector, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock,
                          side_effect=[Exception("fail"), None]):
            await connector.reindex_records([rec1, rec2])
        connector.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_general_error_propagates(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        connector.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=Exception("fatal")
        )
        with patch.object(connector, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock, return_value=None):
            with pytest.raises(Exception):
                await connector.reindex_records([record])


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_no_external_id(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = None
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_datasource(self, connector):
        connector.gmail_data_source = None
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_profile_error_returns_none(self, connector):
        connector.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("profile fail")
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_email_returns_none(self, connector):
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_routes_to_mail_handler(self, connector):
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_check_and_fetch_updated_mail_record",
                          new_callable=AsyncMock, return_value=None) as mock_mail:
            result = await connector._check_and_fetch_updated_record("org-1", record)
            mock_mail.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_to_file_handler(self, connector):
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.FILE

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_check_and_fetch_updated_file_record",
                          new_callable=AsyncMock, return_value=None) as mock_file:
            result = await connector._check_and_fetch_updated_record("org-1", record)
            mock_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_type_returns_none(self, connector):
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = "UNKNOWN"

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_general_exception_returns_none(self, connector):
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector, "_check_and_fetch_updated_mail_record",
                          new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


class TestCheckAndFetchUpdatedMailRecord:
    @pytest.mark.asyncio
    async def test_no_message_id(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = None
        result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_message_not_found(self, connector):
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_null_message(self, connector):
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_thread_id(self, connector):
        msg = _make_gmail_message()
        del msg["threadId"]
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_updated_record(self, connector):
        msg = _make_gmail_message()
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)

        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "u@e.com:SENT"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_updated_returns_none(self, connector):
        msg = _make_gmail_message()
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_record_returns_none(self, connector):
        msg = _make_gmail_message()
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_message",
                          new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_general_exception(self, connector):
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None


class TestCheckAndFetchUpdatedFileRecord:
    @pytest.mark.asyncio
    async def test_no_stable_attachment_id(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = None
        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_success(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(
            message_id="msg-1",
            has_drive_attachment=True,
        )
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"

        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        existing = MagicMock()
        existing.id = "existing-att"
        existing.version = 0
        existing.external_record_group_id = "u@e.com:INBOX"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_parent(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = None
        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_parent_not_found(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_matching_attachment(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "nonexistent-drive-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1")
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_thread_id(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_drive_attachment=True)
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"
        del parent_msg["threadId"]
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_success(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_bad_id_format(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "no-tilde-here"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1")
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_parent_not_found(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_no_parent_id_uses_from_stable(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = None

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_not_found_in_parent(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~99"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_no_thread_id(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        del parent_msg["threadId"]
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_parent_null_message(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_general_exception_returns_none(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"
        connector.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=RuntimeError("fatal")
        )
        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_parent_null(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_updated_returns_data(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_drive_attachment=True)
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = MagicMock(spec=RecordUpdate)
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_attachment",
                          new_callable=AsyncMock, return_value=mock_update):
            result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is not None

    @pytest.mark.asyncio
    async def test_regular_attachment_updated_returns_data(self, connector):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = MagicMock(spec=RecordUpdate)
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)

        with patch.object(connector, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_attachment",
                          new_callable=AsyncMock, return_value=mock_update):
            result = await connector._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is not None
