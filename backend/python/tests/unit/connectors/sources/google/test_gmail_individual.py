"""Tests for GoogleGmailIndividualConnector (app/connectors/sources/google/gmail/individual/connector.py)."""

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
    internal_date="1704067200000",  # 2024-01-01
    has_attachments=False,
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
            "body": {
                "attachmentId": "att-1",
                "size": 5000,
            },
        })

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": label_ids,
        "snippet": "Test snippet...",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": ""},
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
    """Tests for _parse_gmail_headers."""

    def test_extracts_relevant_headers(self, connector):
        headers = [
            {"name": "Subject", "value": "Hello"},
            {"name": "From", "value": "alice@example.com"},
            {"name": "To", "value": "bob@example.com"},
            {"name": "Cc", "value": "carol@example.com"},
            {"name": "Message-ID", "value": "<abc@gmail.com>"},
            {"name": "X-Custom-Header", "value": "ignored"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert result["subject"] == "Hello"
        assert result["from"] == "alice@example.com"
        assert result["to"] == "bob@example.com"
        assert result["cc"] == "carol@example.com"
        assert result["message-id"] == "<abc@gmail.com>"
        assert "x-custom-header" not in result

    def test_empty_headers(self, connector):
        result = connector._parse_gmail_headers([])
        assert result == {}


# ---------------------------------------------------------------------------
# Email list parsing
# ---------------------------------------------------------------------------

class TestParseEmailList:
    """Tests for _parse_email_list."""

    def test_single_email(self, connector):
        result = connector._parse_email_list("alice@example.com")
        assert result == ["alice@example.com"]

    def test_multiple_emails(self, connector):
        result = connector._parse_email_list("a@example.com, b@example.com, c@example.com")
        assert len(result) == 3

    def test_empty_string(self, connector):
        result = connector._parse_email_list("")
        assert result == []

    def test_none_returns_empty(self, connector):
        result = connector._parse_email_list(None)
        assert result == []


# ---------------------------------------------------------------------------
# Email extraction from header
# ---------------------------------------------------------------------------

class TestExtractEmailFromHeader:
    """Tests for _extract_email_from_header."""

    def test_plain_email(self, connector):
        result = connector._extract_email_from_header("alice@example.com")
        assert result == "alice@example.com"

    def test_name_and_email_format(self, connector):
        result = connector._extract_email_from_header("Alice Smith <alice@example.com>")
        assert result == "alice@example.com"

    def test_empty_string(self, connector):
        result = connector._extract_email_from_header("")
        assert result == ""

    def test_none_returns_empty(self, connector):
        result = connector._extract_email_from_header(None)
        assert result == ""


# ---------------------------------------------------------------------------
# _process_gmail_message
# ---------------------------------------------------------------------------

class TestProcessGmailMessage:
    """Tests for _process_gmail_message."""

    async def test_new_inbox_message(self, connector):
        message = _make_gmail_message(label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL
        assert result.record.external_record_group_id == "user@example.com:INBOX"
        assert result.record.record_name == "Test Subject"
        assert result.record.thread_id == "thread-1"

    async def test_new_sent_message(self, connector):
        message = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:SENT"

    async def test_other_label_message(self, connector):
        message = _make_gmail_message(label_ids=["CATEGORY_PROMOTIONS"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:OTHERS"

    async def test_sent_label_takes_priority(self, connector):
        message = _make_gmail_message(label_ids=["INBOX", "SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:SENT"

    async def test_sender_gets_owner_permission(self, connector):
        message = _make_gmail_message(from_email="user@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].type == PermissionType.OWNER

    async def test_recipient_gets_read_permission(self, connector):
        message = _make_gmail_message(from_email="other@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].type == PermissionType.READ

    async def test_returns_none_for_no_message_id(self, connector):
        message = {"threadId": "t1", "payload": {"headers": []}}
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result is None

    async def test_no_subject_defaults(self, connector):
        message = _make_gmail_message(subject="")
        # Remove subject header
        message["payload"]["headers"] = [
            h for h in message["payload"]["headers"] if h["name"] != "Subject"
        ]
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.record_name == "(No Subject)"

    async def test_mime_type_is_gmail(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.mime_type == MimeTypes.GMAIL.value

    async def test_weburl_includes_user_email(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert "authuser=user@example.com" in result.record.weburl

    async def test_case_insensitive_sender_comparison(self, connector):
        message = _make_gmail_message(from_email="User@Example.COM")
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.OWNER


# ---------------------------------------------------------------------------
# Date filter
# ---------------------------------------------------------------------------

class TestGmailDateFilter:
    """Tests for _pass_date_filter."""

    def test_no_filter_passes(self, connector):
        message = _make_gmail_message()
        assert connector._pass_date_filter(message) is True

    def test_message_before_start_rejected(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1704067200001  # Just after 2024-01-01 00:00
        mock_filter.get_datetime_end.return_value = None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        message = _make_gmail_message(internal_date="1704067200000")
        assert connector._pass_date_filter(message) is False

    def test_message_after_end_rejected(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = None
        mock_filter.get_datetime_end.return_value = 1704067199999  # Just before 2024-01-01

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        message = _make_gmail_message(internal_date="1704067200000")
        assert connector._pass_date_filter(message) is False

    def test_message_within_range_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1704067100000
        mock_filter.get_datetime_end.return_value = 1704067300000

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        message = _make_gmail_message(internal_date="1704067200000")
        assert connector._pass_date_filter(message) is True

    def test_invalid_internal_date_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1000
        mock_filter.get_datetime_end.return_value = None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        message = _make_gmail_message(internal_date="not-a-number")
        # Should return True because parsing fails and filter is skipped
        assert connector._pass_date_filter(message) is True


# ---------------------------------------------------------------------------
# Attachment extraction
# ---------------------------------------------------------------------------

class TestExtractAttachmentInfos:
    """Tests for _extract_attachment_infos."""

    def test_regular_attachment(self, connector):
        message = _make_gmail_message(has_attachments=True)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["filename"] == "document.pdf"
        assert infos[0]["isDriveFile"] is False
        assert infos[0]["stableAttachmentId"] == "msg-1~1"

    def test_drive_attachment(self, connector):
        message = {
            "id": "msg-1",
            "payload": {
                "parts": [
                    {
                        "partId": "1",
                        "filename": "large_file.zip",
                        "mimeType": "application/zip",
                        "body": {
                            "driveFileId": "drive-file-1",
                            "size": 50000000,
                        },
                    }
                ],
                "headers": [],
                "body": {},
            },
        }
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["isDriveFile"] is True
        assert infos[0]["driveFileId"] == "drive-file-1"
        assert infos[0]["stableAttachmentId"] == "drive-file-1"

    def test_no_attachments(self, connector):
        message = _make_gmail_message(has_attachments=False)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 0

    def test_drive_file_ids_from_body_content(self, connector):
        import base64

        html_content = '<a href="https://drive.google.com/file/d/DRIVE_ID_1/view?usp=drive_web">Link</a>'
        encoded = base64.urlsafe_b64encode(html_content.encode()).decode()

        message = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": encoded},
                "parts": [],
                "headers": [],
            },
        }
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "DRIVE_ID_1"


# ---------------------------------------------------------------------------
# _process_gmail_attachment
# ---------------------------------------------------------------------------

class TestProcessGmailAttachment:
    """Tests for _process_gmail_attachment."""

    async def test_regular_attachment_creates_file_record(self, connector):
        attachment_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "report.pdf",
            "mimeType": "application/pdf",
            "size": 5000,
            "isDriveFile": False,
        }
        parent_perms = [Permission(email="user@example.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]

        result = await connector._process_gmail_attachment(
            user_email="user@example.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=parent_perms,
        )

        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "report.pdf"
        assert result.record.record_type == RecordType.FILE
        assert result.record.extension == "pdf"
        assert result.record.parent_external_record_id == "msg-1"
        assert result.record.is_file is True
        assert result.record.is_dependent_node is True

    async def test_returns_none_when_no_stable_id(self, connector):
        attachment_info = {
            "attachmentId": "att-1",
            "stableAttachmentId": None,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@example.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=[],
        )
        assert result is None

    async def test_returns_none_when_no_attachment_id_for_regular(self, connector):
        attachment_info = {
            "attachmentId": None,
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@example.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=[],
        )
        assert result is None

    async def test_attachment_inherits_parent_permissions(self, connector):
        attachment_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "data.csv",
            "mimeType": "text/csv",
            "size": 100,
            "isDriveFile": False,
        }
        parent_perms = [Permission(email="user@example.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]

        result = await connector._process_gmail_attachment(
            user_email="user@example.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=parent_perms,
        )

        assert result.new_permissions == parent_perms


# ---------------------------------------------------------------------------
# test_connection_and_access
# ---------------------------------------------------------------------------

class TestGmailConnectionTest:
    """Tests for test_connection_and_access."""

    async def test_returns_true_when_initialized(self, connector):
        connector.gmail_client.get_client.return_value = MagicMock()
        result = await connector.test_connection_and_access()
        assert result is True

    async def test_returns_false_when_no_data_source(self, connector):
        connector.gmail_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    async def test_returns_false_when_no_client(self, connector):
        connector.gmail_client = None
        result = await connector.test_connection_and_access()
        assert result is False


# ---------------------------------------------------------------------------
# _extract_body_from_payload
# ---------------------------------------------------------------------------

class TestExtractBodyFromPayload:
    """Tests for _extract_body_from_payload."""

    def test_plain_text_body(self, connector):
        import base64

        content = base64.urlsafe_b64encode(b"Hello world").decode()
        payload = {"mimeType": "text/plain", "body": {"data": content}}
        result = connector._extract_body_from_payload(payload)
        assert result == content

    def test_html_body(self, connector):
        import base64

        html = "<html><body>Hello</body></html>"
        content = base64.urlsafe_b64encode(html.encode()).decode()
        payload = {"mimeType": "text/html", "body": {"data": content}}
        result = connector._extract_body_from_payload(payload)
        assert result == content

    def test_multipart_prefers_html(self, connector):
        import base64

        plain = base64.urlsafe_b64encode(b"plain").decode()
        html = base64.urlsafe_b64encode(b"<b>html</b>").decode()

        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain}},
                {"mimeType": "text/html", "body": {"data": html}},
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == html

    def test_empty_payload(self, connector):
        payload = {"mimeType": "multipart/alternative", "body": {}, "parts": []}
        result = connector._extract_body_from_payload(payload)
        assert result == ""


# ---------------------------------------------------------------------------
# Existing record detection
# ---------------------------------------------------------------------------

class TestExistingRecordDetection:
    """Tests for detecting existing records and metadata changes."""

    async def test_existing_message_detected(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "user@example.com:INBOX"

        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        message = _make_gmail_message(label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result is not None
        assert result.is_new is False
        assert result.record.id == "existing-id"

    async def test_label_change_detected_as_metadata_update(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "user@example.com:INBOX"

        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        # Message moved from INBOX to SENT
        message = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.is_updated is True
        assert result.metadata_changed is True
