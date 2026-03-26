"""Tests for GoogleGmailIndividualConnector (app/connectors/sources/google/gmail/individual/connector.py)."""

import base64
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import MailRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_gmail_individual")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store(existing_record)
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


@pytest.fixture
def connector():
    """Create a GoogleGmailIndividualConnector with fully mocked dependencies."""
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
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.gmail_client = MagicMock()
        conn.gmail_data_source = AsyncMock()
        conn.config = {"credentials": {"access_token": "t", "refresh_token": "r"}}
        yield conn


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

class TestParseGmailHeaders:
    def test_extracts_relevant_headers(self, connector):
        headers = [
            {"name": "Subject", "value": "Hello"},
            {"name": "From", "value": "alice@example.com"},
            {"name": "To", "value": "bob@example.com"},
            {"name": "Cc", "value": "carol@example.com"},
            {"name": "Bcc", "value": "secret@example.com"},
            {"name": "Message-ID", "value": "<abc@gmail.com>"},
            {"name": "Date", "value": "Mon, 1 Jan 2024"},
            {"name": "X-Custom-Header", "value": "ignored"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert result["subject"] == "Hello"
        assert result["from"] == "alice@example.com"
        assert result["to"] == "bob@example.com"
        assert result["cc"] == "carol@example.com"
        assert result["bcc"] == "secret@example.com"
        assert result["message-id"] == "<abc@gmail.com>"
        assert result["date"] == "Mon, 1 Jan 2024"
        assert "x-custom-header" not in result

    def test_empty_headers(self, connector):
        result = connector._parse_gmail_headers([])
        assert result == {}


# ---------------------------------------------------------------------------
# Email list parsing
# ---------------------------------------------------------------------------

class TestParseEmailList:
    def test_single_email(self, connector):
        result = connector._parse_email_list("alice@example.com")
        assert result == ["alice@example.com"]

    def test_multiple_emails(self, connector):
        result = connector._parse_email_list("a@example.com, b@example.com, c@example.com")
        assert len(result) == 3

    def test_empty_string(self, connector):
        assert connector._parse_email_list("") == []

    def test_none_returns_empty(self, connector):
        assert connector._parse_email_list(None) == []


# ---------------------------------------------------------------------------
# Email extraction from header
# ---------------------------------------------------------------------------

class TestExtractEmailFromHeader:
    def test_plain_email(self, connector):
        assert connector._extract_email_from_header("alice@example.com") == "alice@example.com"

    def test_name_and_email_format(self, connector):
        assert connector._extract_email_from_header("Alice Smith <alice@example.com>") == "alice@example.com"

    def test_empty_string(self, connector):
        assert connector._extract_email_from_header("") == ""

    def test_none_returns_empty(self, connector):
        assert connector._extract_email_from_header(None) == ""

    def test_angle_brackets_only(self, connector):
        assert connector._extract_email_from_header("<alice@e.com>") == "alice@e.com"


# ---------------------------------------------------------------------------
# _process_gmail_message
# ---------------------------------------------------------------------------

class TestProcessGmailMessage:
    async def test_new_inbox_message(self, connector):
        message = _make_gmail_message(label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL
        assert result.record.external_record_group_id == "user@example.com:INBOX"
        assert result.record.thread_id == "thread-1"

    async def test_new_sent_message(self, connector):
        message = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:SENT"

    async def test_other_label_message(self, connector):
        message = _make_gmail_message(label_ids=["CATEGORY_PROMOTIONS"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:OTHERS"

    async def test_sent_label_takes_priority(self, connector):
        message = _make_gmail_message(label_ids=["INBOX", "SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:SENT"

    async def test_sender_gets_owner_permission(self, connector):
        message = _make_gmail_message(from_email="user@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.OWNER

    async def test_recipient_gets_read_permission(self, connector):
        message = _make_gmail_message(from_email="other@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.READ

    async def test_returns_none_for_no_message_id(self, connector):
        message = {"threadId": "t1", "payload": {"headers": []}}
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is None

    async def test_no_subject_defaults(self, connector):
        message = _make_gmail_message(subject="")
        message["payload"]["headers"] = [
            h for h in message["payload"]["headers"] if h["name"] != "Subject"
        ]
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.record_name == "(No Subject)"

    async def test_mime_type_is_gmail(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.mime_type == MimeTypes.GMAIL.value

    async def test_weburl_includes_user_email(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert "authuser=user@example.com" in result.record.weburl

    async def test_case_insensitive_sender_comparison(self, connector):
        message = _make_gmail_message(from_email="User@Example.COM")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.OWNER

    async def test_existing_message_detected(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "user@example.com:INBOX"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        message = _make_gmail_message(label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.is_new is False

    async def test_label_change_detected_as_metadata_update(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "user@example.com:INBOX"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        message = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_invalid_internal_date(self, connector):
        message = _make_gmail_message(internal_date="not-a-number")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is not None

    async def test_no_internal_date(self, connector):
        message = _make_gmail_message()
        del message["internalDate"]
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is not None

    async def test_cc_bcc_parsed(self, connector):
        message = _make_gmail_message(cc_emails="cc1@e.com, cc2@e.com", bcc_emails="bcc@e.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert len(result.record.cc_emails) == 2
        assert len(result.record.bcc_emails) == 1


# ---------------------------------------------------------------------------
# Date filter
# ---------------------------------------------------------------------------

class TestGmailDateFilter:
    def test_no_filter_passes(self, connector):
        assert connector._pass_date_filter(_make_gmail_message()) is True

    def test_message_before_start_rejected(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1704067200001
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_date_filter(_make_gmail_message(internal_date="1704067200000")) is False

    def test_message_after_end_rejected(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = None
        mock_filter.get_datetime_end.return_value = 1704067199999
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_date_filter(_make_gmail_message(internal_date="1704067200000")) is False

    def test_message_within_range_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1704067100000
        mock_filter.get_datetime_end.return_value = 1704067300000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_date_filter(_make_gmail_message(internal_date="1704067200000")) is True

    def test_invalid_internal_date_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_date_filter(_make_gmail_message(internal_date="not-a-number")) is True


# ---------------------------------------------------------------------------
# Attachment extraction
# ---------------------------------------------------------------------------

class TestExtractAttachmentInfos:
    def test_regular_attachment(self, connector):
        message = _make_gmail_message(has_attachments=True)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["filename"] == "document.pdf"
        assert infos[0]["isDriveFile"] is False
        assert infos[0]["stableAttachmentId"] == "msg-1~1"

    def test_drive_attachment(self, connector):
        message = _make_gmail_message(has_drive_attachment=True)
        infos = connector._extract_attachment_infos(message)
        drive_infos = [i for i in infos if i["isDriveFile"]]
        assert len(drive_infos) == 1
        assert drive_infos[0]["driveFileId"] == "drive-file-1"

    def test_no_attachments(self, connector):
        message = _make_gmail_message(has_attachments=False)
        assert len(connector._extract_attachment_infos(message)) == 0

    def test_drive_file_ids_from_body_content(self, connector):
        html = '<a href="https://drive.google.com/file/d/DRIVE_ID_1/view?usp=drive_web">Link</a>'
        message = _make_gmail_message(body_html=html)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "DRIVE_ID_1"

    def test_duplicate_drive_ids_deduplicated(self, connector):
        html = (
            '<a href="https://drive.google.com/file/d/DUP_ID/view?usp=drive_web">1</a>'
            '<a href="https://drive.google.com/file/d/DUP_ID/view?usp=drive_web">2</a>'
        )
        message = _make_gmail_message(body_html=html)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1

    def test_mixed_attachments(self, connector):
        message = _make_gmail_message(has_attachments=True, has_drive_attachment=True)
        infos = connector._extract_attachment_infos(message)
        regular = [i for i in infos if not i["isDriveFile"]]
        drive = [i for i in infos if i["isDriveFile"]]
        assert len(regular) == 1
        assert len(drive) == 1

    def test_nested_parts(self, connector):
        message = {
            "id": "msg-1",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "headers": [],
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": [
                            {"partId": "0.0", "mimeType": "text/plain", "body": {"data": ""}},
                        ]
                    },
                    {
                        "partId": "1",
                        "filename": "nested.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att-nested", "size": 999},
                    }
                ]
            }
        }
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["filename"] == "nested.pdf"


# ---------------------------------------------------------------------------
# _process_gmail_attachment
# ---------------------------------------------------------------------------

class TestProcessGmailAttachment:
    async def test_regular_attachment_creates_file_record(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "report.pdf", "mimeType": "application/pdf",
            "size": 5000, "isDriveFile": False,
        }
        parent_perms = [Permission(email="user@example.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        result = await connector._process_gmail_attachment(
            user_email="user@example.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=parent_perms,
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "report.pdf"
        assert result.record.extension == "pdf"
        assert result.record.is_dependent_node is True

    async def test_returns_none_when_no_stable_id(self, connector):
        attachment_info = {"attachmentId": "att-1", "stableAttachmentId": None, "isDriveFile": False}
        result = await connector._process_gmail_attachment(
            user_email="user@example.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=[],
        )
        assert result is None

    async def test_returns_none_when_no_attachment_id_for_regular(self, connector):
        attachment_info = {
            "attachmentId": None, "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@example.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=[],
        )
        assert result is None

    async def test_attachment_inherits_parent_permissions(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "data.csv", "mimeType": "text/csv",
            "size": 100, "isDriveFile": False,
        }
        parent_perms = [Permission(email="user@example.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        result = await connector._process_gmail_attachment(
            user_email="user@example.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=parent_perms,
        )
        assert result.new_permissions == parent_perms

    async def test_unnamed_attachment_gets_default_name(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": None, "mimeType": "application/octet-stream",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@example.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=[],
        )
        assert result.record.record_name == "unnamed_attachment"

    async def test_drive_file_fetches_metadata(self, connector):
        attachment_info = {
            "attachmentId": None, "driveFileId": "drive-1",
            "stableAttachmentId": "drive-1", "partId": "1",
            "filename": "unknown", "mimeType": "application/octet-stream",
            "size": 0, "isDriveFile": True,
        }
        mock_drive_client = MagicMock()
        mock_service = MagicMock()
        mock_service.files().get().execute.return_value = {
            "id": "drive-1", "name": "report.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size": "5000"
        }
        mock_drive_client.get_client.return_value = mock_service

        with patch("app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
                    new_callable=AsyncMock, return_value=mock_drive_client):
            result = await connector._process_gmail_attachment(
                user_email="user@example.com", message_id="msg-1",
                attachment_info=attachment_info, parent_mail_permissions=[],
            )
        assert result.record.record_name == "report.xlsx"


# ---------------------------------------------------------------------------
# test_connection_and_access
# ---------------------------------------------------------------------------

class TestGmailConnectionTest:
    async def test_returns_true_when_initialized(self, connector):
        connector.gmail_client.get_client.return_value = MagicMock()
        assert await connector.test_connection_and_access() is True

    async def test_returns_false_when_no_data_source(self, connector):
        connector.gmail_data_source = None
        assert await connector.test_connection_and_access() is False

    async def test_returns_false_when_no_client(self, connector):
        connector.gmail_client = None
        assert await connector.test_connection_and_access() is False

    async def test_returns_false_when_no_api_client(self, connector):
        connector.gmail_client.get_client.return_value = None
        assert await connector.test_connection_and_access() is False


# ---------------------------------------------------------------------------
# _extract_body_from_payload
# ---------------------------------------------------------------------------

class TestExtractBodyFromPayload:
    def test_plain_text_body(self, connector):
        content = base64.urlsafe_b64encode(b"Hello world").decode()
        payload = {"mimeType": "text/plain", "body": {"data": content}}
        assert connector._extract_body_from_payload(payload) == content

    def test_html_body(self, connector):
        html = "<html><body>Hello</body></html>"
        content = base64.urlsafe_b64encode(html.encode()).decode()
        payload = {"mimeType": "text/html", "body": {"data": content}}
        assert connector._extract_body_from_payload(payload) == content

    def test_multipart_prefers_html(self, connector):
        plain = base64.urlsafe_b64encode(b"plain").decode()
        html = base64.urlsafe_b64encode(b"<b>html</b>").decode()
        payload = {
            "mimeType": "multipart/alternative", "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain}},
                {"mimeType": "text/html", "body": {"data": html}},
            ],
        }
        assert connector._extract_body_from_payload(payload) == html

    def test_empty_payload(self, connector):
        payload = {"mimeType": "multipart/alternative", "body": {}, "parts": []}
        assert connector._extract_body_from_payload(payload) == ""

    def test_nested_multipart(self, connector):
        html = base64.urlsafe_b64encode(b"<b>nested html</b>").decode()
        payload = {
            "mimeType": "multipart/mixed", "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative", "body": {},
                    "parts": [
                        {"mimeType": "text/html", "body": {"data": html}},
                    ]
                }
            ],
        }
        assert connector._extract_body_from_payload(payload) == html


# ---------------------------------------------------------------------------
# get_signed_url
# ---------------------------------------------------------------------------

class TestGetSignedUrl:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())


# ---------------------------------------------------------------------------
# _get_existing_record
# ---------------------------------------------------------------------------

class TestExistingRecordDetection:
    async def test_returns_existing_record(self, connector):
        existing = MagicMock()
        existing.id = "found-id"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        result = await connector._get_existing_record("ext-id-1")
        assert result is not None

    async def test_returns_none_when_not_found(self, connector):
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)
        assert await connector._get_existing_record("nonexistent") is None

    async def test_returns_none_on_error(self, connector):
        provider = MagicMock()
        @asynccontextmanager
        async def _failing_tx():
            raise Exception("DB error")
            yield
        provider.transaction = _failing_tx
        connector.data_store_provider = provider
        assert await connector._get_existing_record("ext-id-1") is None


# ---------------------------------------------------------------------------
# _process_gmail_message_generator
# ---------------------------------------------------------------------------

class TestIndividualMessageGenerator:
    async def test_applies_indexing_filter(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False
        messages = [_make_gmail_message()]
        results = []
        async for update in connector._process_gmail_message_generator(messages, "user@example.com", "thread-1"):
            if update:
                results.append(update)
        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_handles_exception(self, connector):
        with patch.object(connector, "_process_gmail_message", new_callable=AsyncMock,
                          side_effect=Exception("error")):
            results = []
            async for update in connector._process_gmail_message_generator(
                [_make_gmail_message()], "user@example.com", "thread-1"
            ):
                if update:
                    results.append(update)
            assert len(results) == 0


# ---------------------------------------------------------------------------
# _process_gmail_attachment_generator
# ---------------------------------------------------------------------------

class TestIndividualAttachmentGenerator:
    async def test_applies_indexing_filter(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False
        attachment_infos = [{
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "file.txt", "mimeType": "text/plain",
            "size": 100, "isDriveFile": False,
        }]
        parent_perms = [Permission(email="u@e.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        results = []
        async for update in connector._process_gmail_attachment_generator(
            "user@example.com", "msg-1", attachment_infos, parent_perms
        ):
            if update:
                results.append(update)
        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_handles_exception(self, connector):
        with patch.object(connector, "_process_gmail_attachment", new_callable=AsyncMock,
                          side_effect=Exception("error")):
            results = []
            async for update in connector._process_gmail_attachment_generator(
                "user@example.com", "msg-1",
                [{"stableAttachmentId": "id", "isDriveFile": False, "attachmentId": "att"}],
                []
            ):
                if update:
                    results.append(update)
            assert len(results) == 0


# ---------------------------------------------------------------------------
# Deep sync: _sync_user_mailbox
# ---------------------------------------------------------------------------

class TestIndividualSyncUserMailbox:
    async def test_routes_to_full_sync_when_no_history_id(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@example.com"}
        )
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch("app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
                   new_callable=AsyncMock,
                   return_value=(MagicMock(), MagicMock())), \
             patch.object(connector, "_run_full_sync", new_callable=AsyncMock) as mock_full:
            await connector._sync_user_mailbox()
            mock_full.assert_called_once()

    async def test_routes_to_incremental_sync_when_history_id_exists(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@example.com"}
        )
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "hist-123"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch("app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
                   new_callable=AsyncMock,
                   return_value=(MagicMock(), MagicMock())), \
             patch.object(connector, "_run_sync_with_history_id", new_callable=AsyncMock) as mock_inc:
            await connector._sync_user_mailbox()
            mock_inc.assert_called_once()

    async def test_falls_back_to_full_on_incremental_failure(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@example.com"}
        )
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "hist-123"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch("app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
                   new_callable=AsyncMock,
                   return_value=(MagicMock(), MagicMock())), \
             patch.object(connector, "_run_sync_with_history_id", new_callable=AsyncMock,
                          side_effect=Exception("history expired")), \
             patch.object(connector, "_run_full_sync", new_callable=AsyncMock) as mock_full:
            await connector._sync_user_mailbox()
            mock_full.assert_called_once()

    async def test_returns_early_when_no_datasource(self, connector):
        connector.gmail_data_source = None
        await connector._sync_user_mailbox()
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_returns_early_when_no_email(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(return_value={})
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch("app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
                   new_callable=AsyncMock,
                   return_value=(MagicMock(), MagicMock())):
            await connector._sync_user_mailbox()
        connector.data_entities_processor.on_new_records.assert_not_called()


# ---------------------------------------------------------------------------
# Deep sync: _run_full_sync (individual)
# ---------------------------------------------------------------------------

class TestIndividualRunFullSync:
    async def test_full_sync_processes_threads(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "hist-1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "thread-1"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("user@example.com", "test-key")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_full_sync_handles_empty_threads(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "hist-1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={"threads": []})
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("user@example.com", "test-key")
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_full_sync_paginates_threads(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "hist-1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(side_effect=[
            {"threads": [{"id": "t1"}], "nextPageToken": "page2"},
            {"threads": [{"id": "t2"}]},
        ])
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("user@example.com", "test-key")
        assert connector.gmail_data_source.users_threads_list.call_count == 2

    async def test_full_sync_processes_attachments(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "hist-1"}
        )
        msg = _make_gmail_message(has_attachments=True)
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "thread-1"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [msg],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("user@example.com", "test-key")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_full_sync_profile_error_continues(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(side_effect=Exception("Profile error"))
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={"threads": []})
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("user@example.com", "test-key")

    async def test_full_sync_thread_error_continues(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "hist-1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "bad-thread"}, {"id": "good-thread"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(side_effect=[
            Exception("thread error"),
            {"messages": [_make_gmail_message()]},
        ])
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("user@example.com", "test-key")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_full_sync_multiple_messages_in_thread(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "hist-1"}
        )
        connector.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "thread-multi"}],
        })
        connector.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                _make_gmail_message(message_id="msg-1"),
                _make_gmail_message(message_id="msg-2"),
            ],
        })
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_full_sync("user@example.com", "test-key")
        connector.data_entities_processor.on_new_records.assert_called()


# ---------------------------------------------------------------------------
# Deep sync: drive attachment fallback
# ---------------------------------------------------------------------------

class TestIndividualDriveAttachmentFallback:
    async def test_drive_attachment_metadata_failure_uses_fallback(self, connector):
        attachment_info = {
            "attachmentId": None, "driveFileId": "drive-fail",
            "stableAttachmentId": "drive-fail", "partId": "1",
            "filename": "fallback.bin", "mimeType": "application/octet-stream",
            "size": 100, "isDriveFile": True,
        }
        with patch("app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
                    new_callable=AsyncMock, side_effect=Exception("Drive error")):
            result = await connector._process_gmail_attachment(
                user_email="user@example.com", message_id="msg-1",
                attachment_info=attachment_info, parent_mail_permissions=[],
            )
        assert result is not None
        assert result.record.record_name == "fallback.bin"

    async def test_unnamed_attachment_default_name(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": None, "mimeType": "application/octet-stream",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@example.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=[],
        )
        assert result.record.record_name == "unnamed_attachment"


# ---------------------------------------------------------------------------
# Deep sync: _extract_body_from_payload edge cases
# ---------------------------------------------------------------------------

class TestIndividualExtractBodyEdgeCases:
    def test_multipart_no_html_returns_plain(self, connector):
        import base64
        plain = base64.urlsafe_b64encode(b"plain text").decode()
        payload = {
            "mimeType": "multipart/alternative", "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain}},
            ],
        }
        assert connector._extract_body_from_payload(payload) == plain

    def test_deeply_nested_multipart(self, connector):
        import base64
        html = base64.urlsafe_b64encode(b"<b>deep</b>").decode()
        payload = {
            "mimeType": "multipart/mixed", "body": {},
            "parts": [
                {
                    "mimeType": "multipart/related", "body": {},
                    "parts": [
                        {
                            "mimeType": "multipart/alternative", "body": {},
                            "parts": [
                                {"mimeType": "text/html", "body": {"data": html}},
                            ]
                        }
                    ]
                }
            ],
        }
        assert connector._extract_body_from_payload(payload) == html


# ---------------------------------------------------------------------------
# Deep sync: _run_sync_with_history_id (individual)
# ---------------------------------------------------------------------------

class TestIndividualRunSyncWithHistoryId:
    async def test_incremental_sync_basic(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(return_value={"historyId": "hist-200"})
        connector.gmail_data_source.users_history_list = AsyncMock(return_value={"history": []})
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector._run_sync_with_history_id(
                "user@example.com", "hist-100", "test-key"
            )
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    async def test_incremental_sync_falls_back_on_error(self, connector):
        connector.gmail_data_source = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@example.com"}
        )
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "hist-123"}
        )
        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch("app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
                   new_callable=AsyncMock,
                   return_value=(MagicMock(), MagicMock())), \
             patch.object(connector, "_run_sync_with_history_id", new_callable=AsyncMock,
                          side_effect=Exception("history error")), \
             patch.object(connector, "_run_full_sync", new_callable=AsyncMock) as mock_full:
            await connector._sync_user_mailbox()
            mock_full.assert_called_once()
