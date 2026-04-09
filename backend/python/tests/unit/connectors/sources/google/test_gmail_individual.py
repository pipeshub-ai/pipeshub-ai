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
import asyncio
from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import (
    DatetimeOperator,
    Filter,
    FilterCollection,
    FilterType,
)
from app.models.entities import (
    AppUser,
    MailRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
import os
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
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
from app.connectors.sources.google.common.connector_google_exceptions import GoogleMailError
from app.models.entities import (
    FileRecord,
    MailRecord,
    RecordGroupType,
    RecordType,
)


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
            scope="personal",
            created_by="test-user-id",
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

# =============================================================================
# Merged from test_gmail_individual_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_tx_store_cov(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx.create_record_group_relation = AsyncMock()
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store_cov(existing_record)
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
    ):
        from app.connectors.sources.google.gmail.individual.connector import (
            GoogleGmailIndividualConnector,
        )

        logger = logging.getLogger("test.gmail.individual")
        data_entities_processor = MagicMock()
        data_entities_processor.org_id = "org-gmail-1"
        data_entities_processor.on_new_app_users = AsyncMock()
        data_entities_processor.on_new_record_groups = AsyncMock()
        data_entities_processor.on_new_records = AsyncMock()
        data_entities_processor.on_record_deleted = AsyncMock()

        data_store_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()
        connector_id = "gmail-ind-1"

        connector = GoogleGmailIndividualConnector(
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope="personal",
            created_by="test-user-id",
        )
        # The mocked GmailIndividualApp causes connector_name to be a MagicMock.
        # Set it to the real enum value so Pydantic models validate correctly.
        connector.connector_name = Connectors.GOOGLE_MAIL
        return connector


def _make_gmail_message_cov(
    msg_id="msg-1",
    thread_id="thread-1",
    label_ids=None,
    subject="Test Subject",
    from_email="sender@test.com",
    to_email="recipient@test.com",
    internal_date="1700000000000",
    has_attachment=False,
):
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_email},
        {"name": "To", "value": to_email},
        {"name": "Cc", "value": ""},
        {"name": "Bcc", "value": ""},
        {"name": "Message-ID", "value": f"<{msg_id}@mail.gmail.com>"},
        {"name": "Date", "value": "Mon, 1 Jan 2024 00:00:00 +0000"},
    ]
    parts = []
    if has_attachment:
        parts.append({
            "partId": "1",
            "filename": "attachment.pdf",
            "mimeType": "application/pdf",
            "body": {"attachmentId": "att-1", "size": 1024},
        })
    message = {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": label_ids or ["INBOX"],
        "snippet": "Test snippet",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "multipart/mixed",
            "parts": parts,
        },
    }
    return message


# ---------------------------------------------------------------------------
# Tests: _parse_gmail_headers
# ---------------------------------------------------------------------------

class TestParseGmailHeadersCoverage:
    def test_basic_headers(self):
        connector = _make_connector()
        headers = [
            {"name": "Subject", "value": "Hello"},
            {"name": "From", "value": "a@test.com"},
            {"name": "To", "value": "b@test.com"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert result["subject"] == "Hello"
        assert result["from"] == "a@test.com"
        assert result["to"] == "b@test.com"

    def test_empty_headers(self):
        connector = _make_connector()
        result = connector._parse_gmail_headers([])
        assert result == {}

    def test_ignores_unknown_headers(self):
        connector = _make_connector()
        headers = [
            {"name": "X-Custom-Header", "value": "custom"},
            {"name": "Subject", "value": "Kept"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert "x-custom-header" not in result
        assert result["subject"] == "Kept"

    def test_all_known_headers(self):
        connector = _make_connector()
        headers = [
            {"name": "Subject", "value": "S"},
            {"name": "From", "value": "F"},
            {"name": "To", "value": "T"},
            {"name": "Cc", "value": "C"},
            {"name": "Bcc", "value": "B"},
            {"name": "Message-ID", "value": "M"},
            {"name": "Date", "value": "D"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert len(result) == 7


# ---------------------------------------------------------------------------
# Tests: _parse_email_list
# ---------------------------------------------------------------------------

class TestParseEmailListCoverage:
    def test_single_email(self):
        connector = _make_connector()
        result = connector._parse_email_list("a@test.com")
        assert result == ["a@test.com"]

    def test_multiple_emails(self):
        connector = _make_connector()
        result = connector._parse_email_list("a@test.com, b@test.com, c@test.com")
        assert len(result) == 3

    def test_empty_string(self):
        connector = _make_connector()
        assert connector._parse_email_list("") == []

    def test_none(self):
        connector = _make_connector()
        assert connector._parse_email_list(None) == []

    def test_trailing_comma(self):
        connector = _make_connector()
        result = connector._parse_email_list("a@test.com, ")
        assert result == ["a@test.com"]


# ---------------------------------------------------------------------------
# Tests: _extract_email_from_header
# ---------------------------------------------------------------------------

class TestExtractEmailFromHeaderCoverage:
    def test_name_and_email(self):
        connector = _make_connector()
        result = connector._extract_email_from_header("John Doe <john@test.com>")
        assert result == "john@test.com"

    def test_bare_email(self):
        connector = _make_connector()
        result = connector._extract_email_from_header("john@test.com")
        assert result == "john@test.com"

    def test_empty_string(self):
        connector = _make_connector()
        assert connector._extract_email_from_header("") == ""

    def test_none(self):
        connector = _make_connector()
        assert connector._extract_email_from_header(None) == ""

    def test_whitespace_handling(self):
        connector = _make_connector()
        result = connector._extract_email_from_header("  John < john@test.com > ")
        assert result == "john@test.com"


# ---------------------------------------------------------------------------
# Tests: _pass_date_filter
# ---------------------------------------------------------------------------

class TestPassDateFilter:
    def test_no_filter_always_passes(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        msg = {"internalDate": "1700000000000"}
        assert connector._pass_date_filter(msg) is True

    def test_invalid_internal_date_passes(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        msg = {"internalDate": "not-a-number"}
        assert connector._pass_date_filter(msg) is True

    def test_no_internal_date_passes(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        msg = {}
        assert connector._pass_date_filter(msg) is True

    def test_with_start_filter_passes(self):
        connector = _make_connector()
        # Create a real Filter with start=1000000000000, end=None
        # Value must be a dict so the model_validator converts it to a tuple
        date_filter = Filter(
            key="received_date",
            value={"start": 1000000000000, "end": None},
            type=FilterType.DATETIME,
            operator=DatetimeOperator.IS_AFTER,
        )
        connector.sync_filters = FilterCollection(filters=[date_filter])
        msg = {"internalDate": "1700000000000"}
        assert connector._pass_date_filter(msg) is True

    def test_with_start_filter_fails(self):
        connector = _make_connector()
        # Create a real Filter with start=1800000000000, end=None
        date_filter = Filter(
            key="received_date",
            value={"start": 1800000000000, "end": None},
            type=FilterType.DATETIME,
            operator=DatetimeOperator.IS_AFTER,
        )
        connector.sync_filters = FilterCollection(filters=[date_filter])
        msg = {"internalDate": "1700000000000"}
        assert connector._pass_date_filter(msg) is False

    def test_with_end_filter_fails(self):
        connector = _make_connector()
        # Create a real Filter with start=None, end=1600000000000
        date_filter = Filter(
            key="received_date",
            value={"start": None, "end": 1600000000000},
            type=FilterType.DATETIME,
            operator=DatetimeOperator.IS_BEFORE,
        )
        connector.sync_filters = FilterCollection(filters=[date_filter])
        msg = {"internalDate": "1700000000000"}
        assert connector._pass_date_filter(msg) is False


# ---------------------------------------------------------------------------
# Tests: _extract_body_from_payload
# ---------------------------------------------------------------------------

class TestExtractBodyFromPayloadCoverage:
    def test_html_body(self):
        connector = _make_connector()
        payload = {
            "mimeType": "text/html",
            "body": {"data": "SGVsbG8gV29ybGQ="},
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "SGVsbG8gV29ybGQ="

    def test_plain_text_body(self):
        connector = _make_connector()
        payload = {
            "mimeType": "text/plain",
            "body": {"data": "dGVzdA=="},
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "dGVzdA=="

    def test_multipart_prefers_html(self):
        connector = _make_connector()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "plain"}},
                {"mimeType": "text/html", "body": {"data": "html"}},
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "html"

    def test_empty_payload(self):
        connector = _make_connector()
        payload = {"mimeType": "multipart/mixed", "body": {}}
        result = connector._extract_body_from_payload(payload)
        assert result == ""

    def test_nested_parts(self):
        connector = _make_connector()
        payload = {
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {},
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": "nested-plain"}},
                    ],
                }
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "nested-plain"


# ---------------------------------------------------------------------------
# Tests: _process_gmail_message
# ---------------------------------------------------------------------------

class TestProcessGmailMessageCoverage:
    @pytest.mark.asyncio
    async def test_new_message_inbox(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = _make_gmail_message_cov(
            msg_id="msg-new",
            label_ids=["INBOX"],
            from_email="sender@test.com",
            to_email="user@test.com",
        )

        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )

        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL
        assert result.record.subject == "Test Subject"
        assert result.record.external_record_group_id == "user@test.com:INBOX"

    @pytest.mark.asyncio
    async def test_new_message_sent(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = _make_gmail_message_cov(
            msg_id="msg-sent",
            label_ids=["SENT"],
            from_email="user@test.com",
            to_email="recipient@test.com",
        )

        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )

        assert result is not None
        assert result.record.external_record_group_id == "user@test.com:SENT"

    @pytest.mark.asyncio
    async def test_message_with_both_sent_and_inbox(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = _make_gmail_message_cov(
            msg_id="msg-both",
            label_ids=["INBOX", "SENT"],
        )

        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        # SENT should take precedence
        assert result.record.external_record_group_id == "user@test.com:SENT"

    @pytest.mark.asyncio
    async def test_message_with_other_labels(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = _make_gmail_message_cov(
            msg_id="msg-other",
            label_ids=["CATEGORY_SOCIAL"],
        )

        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@test.com:OTHERS"

    @pytest.mark.asyncio
    async def test_message_no_id(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = {"labelIds": ["INBOX"], "payload": {"headers": []}}
        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_sender_is_user_gets_owner_permission(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = _make_gmail_message_cov(
            msg_id="msg-owner",
            from_email="user@test.com",
        )

        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_sender_is_not_user_gets_read_permission(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = _make_gmail_message_cov(
            msg_id="msg-read",
            from_email="other@test.com",
        )

        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_existing_record_no_change(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.external_record_group_id = "user@test.com:INBOX"
        connector.data_store_provider = _make_mock_data_store_provider(existing)

        message = _make_gmail_message_cov(msg_id="msg-existing", label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            user_email="user@test.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result is not None
        assert result.is_new is False
        assert result.record.version == 4


# ---------------------------------------------------------------------------
# Tests: _extract_attachment_infos
# ---------------------------------------------------------------------------

class TestExtractAttachmentInfosCoverage:
    def test_regular_attachment(self):
        connector = _make_connector()
        message = _make_gmail_message_cov(msg_id="msg-att", has_attachment=True)
        result = connector._extract_attachment_infos(message)
        assert len(result) == 1
        assert result[0]["filename"] == "attachment.pdf"
        assert result[0]["isDriveFile"] is False

    def test_drive_attachment(self):
        connector = _make_connector()
        message = {
            "id": "msg-drive",
            "payload": {
                "parts": [
                    {
                        "partId": "1",
                        "filename": "large-file.zip",
                        "mimeType": "application/zip",
                        "body": {"driveFileId": "drive-123", "size": 50000000},
                    }
                ],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(message)
        assert len(result) == 1
        assert result[0]["isDriveFile"] is True
        assert result[0]["driveFileId"] == "drive-123"

    def test_no_attachments(self):
        connector = _make_connector()
        message = {
            "id": "msg-no-att",
            "payload": {"parts": [], "headers": []},
        }
        result = connector._extract_attachment_infos(message)
        assert result == []

    def test_drive_file_ids_from_body_content(self):
        connector = _make_connector()
        import base64

        html_content = '<a href="https://drive.google.com/file/d/abc123/view?usp=drive_web">file</a>'
        encoded = base64.urlsafe_b64encode(html_content.encode()).decode()
        message = {
            "id": "msg-inline-drive",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": encoded},
                "parts": [],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(message)
        assert len(result) == 1
        assert result[0]["driveFileId"] == "abc123"
        assert result[0]["isDriveFile"] is True

    def test_nested_parts(self):
        connector = _make_connector()
        message = {
            "id": "msg-nested",
            "payload": {
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": [
                            {
                                "partId": "0.1",
                                "filename": "nested.txt",
                                "mimeType": "text/plain",
                                "body": {"attachmentId": "att-nested", "size": 100},
                            }
                        ],
                    }
                ],
                "headers": [],
            },
        }
        result = connector._extract_attachment_infos(message)
        assert len(result) == 1
        assert result[0]["filename"] == "nested.txt"


# ---------------------------------------------------------------------------
# Tests: _process_gmail_attachment
# ---------------------------------------------------------------------------

class TestProcessGmailAttachmentCoverage:
    @pytest.mark.asyncio
    async def test_regular_attachment(self):
        connector = _make_connector()
        connector.indexing_filters = FilterCollection()

        attach_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "report.pdf",
            "mimeType": "application/pdf",
            "size": 2048,
            "isDriveFile": False,
        }
        permissions = [Permission(email="user@test.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]

        result = await connector._process_gmail_attachment(
            user_email="user@test.com",
            message_id="msg-1",
            attachment_info=attach_info,
            parent_mail_permissions=permissions,
        )
        assert result is not None
        assert result.record.record_type == RecordType.FILE
        assert result.record.record_name == "report.pdf"
        assert result.record.extension == "pdf"
        assert result.is_new is True

    @pytest.mark.asyncio
    async def test_attachment_no_stable_id_returns_none(self):
        connector = _make_connector()
        connector.indexing_filters = FilterCollection()

        attach_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": None,
            "partId": "1",
            "filename": "report.pdf",
            "mimeType": "application/pdf",
            "size": 2048,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@test.com",
            message_id="msg-1",
            attachment_info=attach_info,
            parent_mail_permissions=[],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_non_drive_no_attachment_id_returns_none(self):
        connector = _make_connector()
        connector.indexing_filters = FilterCollection()

        attach_info = {
            "attachmentId": None,
            "driveFileId": None,
            "stableAttachmentId": "stable-1",
            "partId": "1",
            "filename": "report.pdf",
            "mimeType": "application/pdf",
            "size": 2048,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@test.com",
            message_id="msg-1",
            attachment_info=attach_info,
            parent_mail_permissions=[],
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _process_gmail_message_generator
# ---------------------------------------------------------------------------

class TestProcessGmailMessageGenerator:
    @pytest.mark.asyncio
    async def test_yields_record_updates(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        messages = [
            _make_gmail_message_cov(msg_id="m1"),
            _make_gmail_message_cov(msg_id="m2"),
        ]

        results = []
        async for update in connector._process_gmail_message_generator(
            messages=messages,
            user_email="user@test.com",
            thread_id="t1",
        ):
            if update:
                results.append(update)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_skips_failed_messages(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        messages = [
            {},  # Missing id - will return None
            _make_gmail_message_cov(msg_id="m-good"),
        ]

        results = []
        async for update in connector._process_gmail_message_generator(
            messages=messages,
            user_email="user@test.com",
            thread_id="t1",
        ):
            if update:
                results.append(update)

        assert len(results) == 1


# ---------------------------------------------------------------------------
# Tests: _create_app_user
# ---------------------------------------------------------------------------

class TestCreateAppUser:
    @pytest.mark.asyncio
    async def test_create_user(self):
        connector = _make_connector()
        profile = {"emailAddress": "user@test.com"}
        await connector._create_app_user(profile)
        connector.data_entities_processor.on_new_app_users.assert_called_once()
        args = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(args) == 1
        assert args[0].email == "user@test.com"

    @pytest.mark.asyncio
    async def test_create_user_no_email_raises(self):
        connector = _make_connector()
        profile = {}
        with pytest.raises(ValueError):
            await connector._create_app_user(profile)


# ---------------------------------------------------------------------------
# Tests: _create_personal_record_group
# ---------------------------------------------------------------------------

class TestCreatePersonalRecordGroup:
    @pytest.mark.asyncio
    async def test_creates_three_groups(self):
        connector = _make_connector()
        await connector._create_personal_record_group("user@test.com")
        assert connector.data_entities_processor.on_new_record_groups.call_count == 3

    @pytest.mark.asyncio
    async def test_no_email_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError):
            await connector._create_personal_record_group("")


# ---------------------------------------------------------------------------
# Tests: _merge_history_changes
# ---------------------------------------------------------------------------

class TestMergeHistoryChanges:
    def test_merge_dedup(self):
        connector = _make_connector()
        inbox = {"history": [{"id": "1"}, {"id": "2"}]}
        sent = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3

    def test_empty_histories(self):
        connector = _make_connector()
        inbox = {"history": []}
        sent = {"history": []}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 0

    def test_sorted_by_id(self):
        connector = _make_connector()
        inbox = {"history": [{"id": "3"}, {"id": "1"}]}
        sent = {"history": [{"id": "2"}]}
        result = connector._merge_history_changes(inbox, sent)
        ids = [h["id"] for h in result["history"]]
        assert ids == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# Tests: test_connection_and_access
# ---------------------------------------------------------------------------

class TestTestConnectionAndAccess:
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


# ---------------------------------------------------------------------------
# Tests: get_signed_url
# ---------------------------------------------------------------------------

class TestGetSignedUrlCoverage:
    def test_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())


# ---------------------------------------------------------------------------
# Tests: _get_fresh_datasource
# ---------------------------------------------------------------------------

class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self):
        connector = _make_connector()
        connector.gmail_client = None
        connector.gmail_data_source = None
        with pytest.raises(Exception):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_calls_refresh(self):
        connector = _make_connector()
        connector.gmail_client = MagicMock()
        connector.gmail_data_source = MagicMock()
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.refresh_google_datasource_credentials",
            new_callable=AsyncMock,
        ) as mock_refresh:
            await connector._get_fresh_datasource()
            mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _get_existing_record
# ---------------------------------------------------------------------------

class TestGetExistingRecord:
    @pytest.mark.asyncio
    async def test_returns_record(self):
        existing = MagicMock()
        existing.id = "rec-1"
        connector = _make_connector()
        connector.data_store_provider = _make_mock_data_store_provider(existing)
        result = await connector._get_existing_record("ext-1")
        assert result is not None
        assert result.id == "rec-1"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        connector = _make_connector()
        connector.data_store_provider = _make_mock_data_store_provider(None)
        result = await connector._get_existing_record("ext-999")
        assert result is None

# =============================================================================
# Merged from test_gmail_individual_full_coverage.py
# =============================================================================

def _make_logger_fullcov():
    log = logging.getLogger("test_gmail_individual_95")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store_fullcov(existing_record=None, child_records=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_records_by_parent = AsyncMock(return_value=child_records or [])
    return tx


def _make_mock_data_store_provider_fullcov(existing_record=None, child_records=None):
    tx = _make_mock_tx_store_fullcov(existing_record, child_records)
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
def connector_fullcov():
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

        logger = _make_logger_fullcov()
        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.reindex_existing_records = AsyncMock()

        ds_provider = _make_mock_data_store_provider_fullcov()
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
    async def test_init_success(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={
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
            result = await connector_fullcov.init()
        assert result is True

    @pytest.mark.asyncio
    async def test_init_no_config(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value=None)
        result = await connector_fullcov.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={
            "auth": {},
            "credentials": {},
        })
        result = await connector_fullcov.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_oauth_config_not_found(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {},
        })
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await connector_fullcov.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_incomplete_credentials(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {},
        })
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.fetch_oauth_config_by_id",
            new_callable=AsyncMock,
            return_value={"config": {"clientId": None, "clientSecret": None}},
        ):
            with pytest.raises(ValueError, match="Incomplete"):
                await connector_fullcov.init()

    @pytest.mark.asyncio
    async def test_init_client_build_failure(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={
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
                await connector_fullcov.init()

    @pytest.mark.asyncio
    async def test_init_no_tokens_warning(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={
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
            result = await connector_fullcov.init()
        assert result is True


class TestGetFreshDatasourceFullCoverage:
    @pytest.mark.asyncio
    async def test_raises_when_no_client(self, connector_fullcov):
        connector_fullcov.gmail_client = None
        with pytest.raises(GoogleMailError):
            await connector_fullcov._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_raises_when_no_datasource(self, connector_fullcov):
        connector_fullcov.gmail_data_source = None
        with pytest.raises(GoogleMailError):
            await connector_fullcov._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_calls_refresh(self, connector_fullcov):
        with patch(
            "app.connectors.sources.google.gmail.individual.connector.refresh_google_datasource_credentials",
            new_callable=AsyncMock,
        ) as mock_refresh:
            await connector_fullcov._get_fresh_datasource()
            mock_refresh.assert_called_once()


class TestExtractEmailFromHeaderEdge:
    def test_malformed_angle_brackets(self, connector_fullcov):
        result = connector_fullcov._extract_email_from_header(">alice@e.com<")
        assert result == ">alice@e.com<"


class TestProcessGmailMessageException:
    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self, connector_fullcov):
        connector_fullcov._parse_gmail_headers = MagicMock(side_effect=Exception("parse fail"))
        msg = _make_gmail_message()
        result = await connector_fullcov._process_gmail_message("u@e.com", msg, "t1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_email_skips_permissions(self, connector_fullcov):
        msg = _make_gmail_message()
        result = await connector_fullcov._process_gmail_message("", msg, "t1", None)
        assert result is not None
        assert result.new_permissions == []

    @pytest.mark.asyncio
    async def test_date_filter_rejects_message(self, connector_fullcov):
        connector_fullcov._pass_date_filter = MagicMock(return_value=False)
        msg = _make_gmail_message()
        result = await connector_fullcov._process_gmail_message("u@e.com", msg, "t1", None)
        assert result is None


class TestExtractAttachmentInfosEdgeCases:
    def test_extract_drive_ids_decode_error(self, connector_fullcov):
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": "!!!invalid-base64!!!"},
                "headers": [],
                "parts": [],
            },
        }
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert len(infos) == 0

    def test_process_part_non_dict(self, connector_fullcov):
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
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert isinstance(infos, list)

    def test_empty_body_data(self, connector_fullcov):
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": ""},
                "headers": [],
                "parts": [],
            },
        }
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert len(infos) == 0

    def test_text_plain_body_with_drive_links(self, connector_fullcov):
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
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "ABC123"

    def test_nested_parts_with_drive_links(self, connector_fullcov):
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
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert any(i["driveFileId"] == "NESTED_ID" for i in infos)


class TestProcessGmailAttachmentException:
    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector_fullcov):
        connector_fullcov._get_existing_record = AsyncMock(side_effect=Exception("db fail"))
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
        result = await connector_fullcov._process_gmail_attachment("u@e.com", "msg-1", info, [])
        assert result is None

    @pytest.mark.asyncio
    async def test_existing_attachment_increments_version(self, connector_fullcov):
        existing = MagicMock()
        existing.id = "existing-att-id"
        existing.version = 2
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)
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
        result = await connector_fullcov._process_gmail_attachment("u@e.com", "msg-1", info, [])
        assert result.record.version == 3

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self, connector_fullcov):
        connector_fullcov.indexing_filters = MagicMock()
        connector_fullcov.indexing_filters.is_enabled.return_value = False
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
        result = await connector_fullcov._process_gmail_attachment("u@e.com", "msg-1", info, [])
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestConvertToPdf:
    @pytest.mark.asyncio
    async def test_successful_conversion(self, connector_fullcov):
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("os.path.exists", return_value=True):
            result = await connector_fullcov._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert result == "/tmp/test.pdf"

    @pytest.mark.asyncio
    async def test_conversion_timeout(self, connector_fullcov):
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            with pytest.raises(HTTPException) as exc_info:
                await connector_fullcov._convert_to_pdf("/tmp/test.docx", "/tmp")
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_conversion_nonzero_return(self, connector_fullcov):
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error msg"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("asyncio.wait_for", return_value=(b"", b"error msg")):
            mock_process.returncode = 1
            with pytest.raises(HTTPException):
                await connector_fullcov._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_conversion_output_not_found(self, connector_fullcov):
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("os.path.exists", return_value=False):
            with pytest.raises(HTTPException):
                await connector_fullcov._convert_to_pdf("/tmp/test.docx", "/tmp")

    @pytest.mark.asyncio
    async def test_conversion_general_exception(self, connector_fullcov):
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("exec fail")):
            with pytest.raises(HTTPException):
                await connector_fullcov._convert_to_pdf("/tmp/test.docx", "/tmp")


class TestStreamFromDrive:
    @pytest.mark.asyncio
    async def test_stream_from_drive_success(self, connector_fullcov):
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
            result = await connector_fullcov._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_from_drive_with_pdf_conversion(self, connector_fullcov):
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
            connector_fullcov, "_convert_to_pdf", new_callable=AsyncMock, return_value="/tmp/test.pdf"
        ), patch(
            "builtins.open", MagicMock()
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector_fullcov._stream_from_drive(
                "drive-id", record, "file.docx", "application/msword",
                convertTo=MimeTypes.PDF.value,
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_from_drive_client_failure_no_credentials(self, connector_fullcov):
        connector_fullcov.config = None
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("auth fail"),
        ):
            with pytest.raises(HTTPException):
                await connector_fullcov._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain"
                )

    @pytest.mark.asyncio
    async def test_stream_from_drive_general_exception(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected"),
        ):
            connector_fullcov.config = None
            with pytest.raises(HTTPException):
                await connector_fullcov._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain"
                )

    @pytest.mark.asyncio
    async def test_stream_from_drive_client_failure_with_service_account(self, connector_fullcov):
        connector_fullcov.config = {"credentials": {"auth": {"type": "service_account"}}}
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
            result = await connector_fullcov._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_from_drive_no_service_account_creds(self, connector_fullcov):
        connector_fullcov.config = {"credentials": {"auth": {}}}
        record = MagicMock()
        record.id = "rec-1"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.GoogleClient.build_from_services",
            new_callable=AsyncMock,
            side_effect=Exception("auth fail"),
        ):
            with pytest.raises(HTTPException):
                await connector_fullcov._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain"
                )


class TestStreamMailRecord:
    @pytest.mark.asyncio
    async def test_stream_mail_success(self, connector_fullcov):
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
            await connector_fullcov._stream_mail_record(gmail_service, "msg-1", record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_mail_not_found(self, connector_fullcov):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = _make_http_error(404)
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test"

        with pytest.raises(HTTPException) as exc_info:
            await connector_fullcov._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_stream_mail_http_error_non_404(self, connector_fullcov):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = _make_http_error(500, "Server Error")
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test"

        with pytest.raises(HTTPException) as exc_info:
            await connector_fullcov._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value

    @pytest.mark.asyncio
    async def test_stream_mail_general_exception(self, connector_fullcov):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = RuntimeError("fail")
        record = MagicMock()
        record.id = "rec-1"
        record.record_name = "Test"

        with pytest.raises(HTTPException) as exc_info:
            await connector_fullcov._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


class TestStreamAttachmentRecord:
    @pytest.mark.asyncio
    async def test_drive_file_delegates_to_stream_from_drive(self, connector_fullcov):
        gmail_service = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"

        with patch.object(
            connector_fullcov, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector_fullcov._stream_attachment_record(
                gmail_service, "drive-file-id", record, "file.txt", "text/plain"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_attachment_success(self, connector_fullcov):
        gmail_service = MagicMock()
        file_data = base64.urlsafe_b64encode(b"file content").decode()
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": file_data,
        }

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        tx = _make_mock_tx_store_fullcov(existing_record=parent_record)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

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
            await connector_fullcov._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )
            mock_stream.assert_called()

    @pytest.mark.asyncio
    async def test_no_parent_message_raises(self, connector_fullcov):
        gmail_service = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = None
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        with pytest.raises(HTTPException) as exc_info:
            await connector_fullcov._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )
        assert exc_info.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_gmail_attachment_failure_fallback_to_drive(self, connector_fullcov):
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
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector_fullcov, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector_fullcov._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_and_drive_both_fail(self, connector_fullcov):
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
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector_fullcov, "_stream_from_drive", new_callable=AsyncMock,
            side_effect=Exception("drive fail"),
        ):
            with pytest.raises(HTTPException):
                await connector_fullcov._stream_attachment_record(
                    gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
                )

    @pytest.mark.asyncio
    async def test_general_exception(self, connector_fullcov):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = RuntimeError("boom")
        gmail_service.users().messages().attachments().get().execute.side_effect = RuntimeError("boom")

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector_fullcov, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ):
            await connector_fullcov._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.pdf", "application/pdf"
            )

    @pytest.mark.asyncio
    async def test_attachment_with_pdf_conversion(self, connector_fullcov):
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
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector_fullcov, "_convert_to_pdf", new_callable=AsyncMock, return_value="/tmp/file.pdf"
        ), patch(
            "builtins.open", MagicMock()
        ), patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector_fullcov._stream_attachment_record(
                gmail_service, "msg-1~1", record, "file.docx", "application/msword",
                convertTo=MimeTypes.PDF.value,
            )
            mock_stream.assert_called()

    @pytest.mark.asyncio
    async def test_message_id_mismatch_warning(self, connector_fullcov):
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
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "actual-msg-id"

        with patch(
            "app.connectors.sources.google.gmail.individual.connector.create_stream_record_response"
        ) as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector_fullcov._stream_attachment_record(
                gmail_service, "different-msg~1", record, "f.pdf", "application/pdf"
            )

    @pytest.mark.asyncio
    async def test_message_not_found_during_part_lookup(self, connector_fullcov):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = _make_http_error(404)

        parent_record = MagicMock()
        parent_record.external_record_id = "msg-1"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector_fullcov, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector_fullcov._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_part_id_not_found(self, connector_fullcov):
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
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=parent_record)

        record = MagicMock()
        record.id = "rec-1"
        record.connector_id = "gmail-ind-1"
        record.parent_external_record_id = "msg-1"

        with patch.object(
            connector_fullcov, "_stream_from_drive", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_drive:
            await connector_fullcov._stream_attachment_record(
                gmail_service, "msg-1~1", record, "f.pdf", "application/pdf"
            )
            mock_drive.assert_called_once()


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_mail_record_type(self, connector_fullcov):
        record = MagicMock()
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        connector_fullcov.gmail_data_source = MagicMock()
        connector_fullcov.gmail_data_source.client = MagicMock()

        with patch.object(
            connector_fullcov, "_stream_mail_record", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_mail:
            await connector_fullcov.stream_record(record)
            mock_mail.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_file_record_type(self, connector_fullcov):
        record = MagicMock()
        record.external_record_id = "msg-1~1"
        record.record_type = RecordTypes.FILE.value
        record.record_name = "file.pdf"
        record.mime_type = "application/pdf"
        connector_fullcov.gmail_data_source = MagicMock()
        connector_fullcov.gmail_data_source.client = MagicMock()

        with patch.object(
            connector_fullcov, "_stream_attachment_record", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_attach:
            await connector_fullcov.stream_record(record)
            mock_attach.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_file_id_raises(self, connector_fullcov):
        record = MagicMock()
        record.external_record_id = None
        connector_fullcov.gmail_data_source = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await connector_fullcov.stream_record(record)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_no_datasource_raises(self, connector_fullcov):
        record = MagicMock()
        record.external_record_id = "msg-1"
        connector_fullcov.gmail_data_source = None

        with pytest.raises(HTTPException):
            await connector_fullcov.stream_record(record)

    @pytest.mark.asyncio
    async def test_reraises_http_exception(self, connector_fullcov):
        record = MagicMock()
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        connector_fullcov.gmail_data_source = MagicMock()
        connector_fullcov.gmail_data_source.client = MagicMock()

        with patch.object(
            connector_fullcov, "_stream_mail_record", new_callable=AsyncMock,
            side_effect=HTTPException(status_code=404, detail="Not found"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await connector_fullcov.stream_record(record)
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_general_exception(self, connector_fullcov):
        record = MagicMock()
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        connector_fullcov.gmail_data_source = MagicMock()
        connector_fullcov.gmail_data_source.client = MagicMock()

        with patch.object(
            connector_fullcov, "_stream_mail_record", new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await connector_fullcov.stream_record(record)
            assert exc_info.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value


class TestCreateAppUserFullCoverage:
    @pytest.mark.asyncio
    async def test_creates_user_successfully(self, connector_fullcov):
        await connector_fullcov._create_app_user({"emailAddress": "user@example.com"})
        connector_fullcov.data_entities_processor.on_new_app_users.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_email_raises(self, connector_fullcov):
        with pytest.raises(ValueError):
            await connector_fullcov._create_app_user({})


class TestCreatePersonalRecordGroupFullCoverage:
    @pytest.mark.asyncio
    async def test_creates_three_groups(self, connector_fullcov):
        await connector_fullcov._create_personal_record_group("user@example.com")
        assert connector_fullcov.data_entities_processor.on_new_record_groups.call_count == 3

    @pytest.mark.asyncio
    async def test_no_email_raises(self, connector_fullcov):
        with pytest.raises(ValueError):
            await connector_fullcov._create_personal_record_group("")

    @pytest.mark.asyncio
    async def test_group_creation_error_continues(self, connector_fullcov):
        connector_fullcov.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=[Exception("fail"), None, None]
        )
        await connector_fullcov._create_personal_record_group("user@example.com")


class TestSyncUserMailboxException:
    @pytest.mark.asyncio
    async def test_exception_propagates(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("profile fail")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch(
                 "app.connectors.sources.google.gmail.individual.connector.load_connector_filters",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), MagicMock()),
             ):
            with pytest.raises(Exception, match="profile fail"):
                await connector_fullcov._sync_user_mailbox()


class TestRunFullSyncEdgeCases:
    @pytest.mark.asyncio
    async def test_skip_thread_with_no_id(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector_fullcov.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": None}],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector_fullcov._run_full_sync("u@e.com", "key")
        connector_fullcov.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_thread_with_no_messages(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector_fullcov.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector_fullcov._run_full_sync("u@e.com", "key")

    @pytest.mark.asyncio
    async def test_sibling_relation_error_continues(self, connector_fullcov):
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
        connector_fullcov.data_store_provider = provider

        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector_fullcov.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [msg1, msg2],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector_fullcov._run_full_sync("u@e.com", "key")
        connector_fullcov.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_page_error_raises(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector_fullcov.gmail_data_source.users_threads_list = AsyncMock(
            side_effect=Exception("page error")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(Exception, match="page error"):
                await connector_fullcov._run_full_sync("u@e.com", "key")

    @pytest.mark.asyncio
    async def test_overall_exception_propagates(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("fatal")
        )
        connector_fullcov.gmail_data_source.users_threads_list = AsyncMock(
            side_effect=Exception("fatal")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(Exception, match="fatal"):
                await connector_fullcov._run_full_sync("u@e.com", "key")

    @pytest.mark.asyncio
    async def test_batch_processing_with_many_messages(self, connector_fullcov):
        connector_fullcov.batch_size = 2
        msgs = [_make_gmail_message(message_id=f"m{i}") for i in range(5)]
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector_fullcov.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": msgs,
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            await connector_fullcov._run_full_sync("u@e.com", "key")
        assert connector_fullcov.data_entities_processor.on_new_records.call_count >= 2

    @pytest.mark.asyncio
    async def test_message_processing_error_continues(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h1"}
        )
        connector_fullcov.gmail_data_source.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}],
        })
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_process_gmail_message", new_callable=AsyncMock,
                          side_effect=Exception("process fail")):
            await connector_fullcov._run_full_sync("u@e.com", "key")


class TestMergeHistoryChangesFullCoverage:
    def test_merges_and_deduplicates(self, connector_fullcov):
        inbox = {"history": [{"id": "1"}, {"id": "2"}]}
        sent = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector_fullcov._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3

    def test_sorts_by_id(self, connector_fullcov):
        inbox = {"history": [{"id": "3"}]}
        sent = {"history": [{"id": "1"}]}
        result = connector_fullcov._merge_history_changes(inbox, sent)
        assert result["history"][0]["id"] == "1"
        assert result["history"][1]["id"] == "3"

    def test_empty_histories(self, connector_fullcov):
        result = connector_fullcov._merge_history_changes({"history": []}, {"history": []})
        assert result["history"] == []


class TestFetchHistoryChanges:
    @pytest.mark.asyncio
    async def test_single_page(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_history_list = AsyncMock(return_value={
            "history": [{"id": "1"}],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._fetch_history_changes("100", "INBOX")
        assert len(result["history"]) == 1

    @pytest.mark.asyncio
    async def test_paginated(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_history_list = AsyncMock(side_effect=[
            {"history": [{"id": "1"}], "nextPageToken": "page2"},
            {"history": [{"id": "2"}]},
        ])
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._fetch_history_changes("100", "INBOX")
        assert len(result["history"]) == 2

    @pytest.mark.asyncio
    async def test_empty_history(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_history_list = AsyncMock(return_value={})
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._fetch_history_changes("100", "INBOX")
        assert result["history"] == []

    @pytest.mark.asyncio
    async def test_http_error_reraise(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_history_list = AsyncMock(
            side_effect=_make_http_error(404)
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(HttpError):
                await connector_fullcov._fetch_history_changes("100", "INBOX")

    @pytest.mark.asyncio
    async def test_general_exception(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_history_list = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(RuntimeError):
                await connector_fullcov._fetch_history_changes("100", "INBOX")


class TestFindPreviousMessageInThread:
    @pytest.mark.asyncio
    async def test_finds_previous_message(self, connector_fullcov):
        existing = MagicMock()
        existing.id = "prev-record-id"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)

        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._find_previous_message_in_thread(
                "t1", "msg-2", "2000"
            )
        assert result == "prev-record-id"

    @pytest.mark.asyncio
    async def test_single_message_returns_none(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [{"id": "msg-1", "internalDate": "1000"}],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._find_previous_message_in_thread(
                "t1", "msg-1", "1000"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_earlier_messages(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._find_previous_message_in_thread(
                "t1", "msg-1", "1000"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_finds_in_batch_records(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        batch_record = MagicMock()
        batch_record.external_record_id = "msg-1"
        batch_record.id = "batch-rec-id"
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._find_previous_message_in_thread(
                "t1", "msg-2", "2000", [(batch_record, [])]
            )
        assert result == "batch-rec-id"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(
            side_effect=Exception("fail")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._find_previous_message_in_thread(
                "t1", "msg-1", "1000"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_previous_record_in_db(self, connector_fullcov):
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)
        connector_fullcov.gmail_data_source.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-1", "internalDate": "1000"},
                {"id": "msg-2", "internalDate": "2000"},
            ],
        })
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._find_previous_message_in_thread(
                "t1", "msg-2", "2000"
            )
        assert result is None


class TestDeleteMessageAndAttachments:
    @pytest.mark.asyncio
    async def test_deletes_message_and_attachments(self, connector_fullcov):
        attachment = MagicMock()
        attachment.id = "att-rec-id"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(child_records=[attachment])
        await connector_fullcov._delete_message_and_attachments("rec-1", "msg-1")
        connector_fullcov.data_entities_processor.on_record_deleted.assert_any_call("att-rec-id")
        connector_fullcov.data_entities_processor.on_record_deleted.assert_any_call("rec-1")

    @pytest.mark.asyncio
    async def test_handles_attachment_deletion_error(self, connector_fullcov):
        attachment = MagicMock()
        attachment.id = "att-rec-id"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(child_records=[attachment])
        connector_fullcov.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=[Exception("fail"), None]
        )
        await connector_fullcov._delete_message_and_attachments("rec-1", "msg-1")

    @pytest.mark.asyncio
    async def test_handles_general_exception(self, connector_fullcov):
        provider = MagicMock()

        @asynccontextmanager
        async def _failing_tx():
            raise Exception("DB error")
            yield

        provider.transaction = _failing_tx
        connector_fullcov.data_store_provider = provider
        await connector_fullcov._delete_message_and_attachments("rec-1", "msg-1")


class TestProcessHistoryChanges:
    @pytest.mark.asyncio
    async def test_process_message_addition(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="new-msg", thread_id="t1")
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "new-msg", "threadId": "t1"}}
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count >= 1
        assert len(batch) >= 1

    @pytest.mark.asyncio
    async def test_process_message_deletion(self, connector_fullcov):
        existing = MagicMock()
        existing.id = "rec-1"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)

        history = {
            "messagesDeleted": [
                {"message": {"id": "del-msg"}}
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_delete_message_and_attachments", new_callable=AsyncMock):
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_labels_added_inbox(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="label-msg", thread_id="t1")
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "labelsAdded": [
                {"message": {"id": "label-msg"}, "labelIds": ["INBOX"]}
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_labels_added_trash_deletes(self, connector_fullcov):
        existing = MagicMock()
        existing.id = "rec-1"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)

        history = {
            "labelsAdded": [
                {"message": {"id": "trash-msg"}, "labelIds": ["TRASH"]}
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_delete_message_and_attachments", new_callable=AsyncMock) as mock_del:
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        mock_del.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_existing_messages(self, connector_fullcov):
        existing = MagicMock()
        existing.id = "existing-id"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)

        history = {
            "messagesAdded": [
                {"message": {"id": "existing-msg"}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_not_found_at_source(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "gone-msg"}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_fetch_error(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=RuntimeError("network fail")
        )
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "fail-msg"}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_no_thread_id_skips(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        msg = _make_gmail_message(message_id="no-thread", thread_id="t1")
        del msg["threadId"]
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "no-thread"}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_null_message_from_api(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "null-msg"}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_sibling_relation_created(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="sibling-msg", thread_id="t1")
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "sibling-msg"}}
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value="prev-rec-id"):
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_general_exception_returns_zero(self, connector_fullcov):
        history = {
            "messagesAdded": "not-a-list"
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletion_not_found_skips(self, connector_fullcov):
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)
        history = {
            "messagesDeleted": [
                {"message": {"id": "not-in-db"}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletion_error_continues(self, connector_fullcov):
        existing = MagicMock()
        existing.id = "rec-1"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)
        history = {
            "messagesDeleted": [
                {"message": {"id": "del-msg-1"}},
                {"message": {"id": "del-msg-2"}},
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_delete_message_and_attachments",
                          new_callable=AsyncMock,
                          side_effect=[Exception("fail"), None]):
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count >= 1

    @pytest.mark.asyncio
    async def test_process_attachments_in_addition(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(
            message_id="att-msg", thread_id="t1", has_attachments=True
        )
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "att-msg"}}
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count >= 2

    @pytest.mark.asyncio
    async def test_http_error_non_404_continues(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(500, "Server Error")
        )
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "err-msg"}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0

    @pytest.mark.asyncio
    async def test_indexing_filter_applied(self, connector_fullcov):
        connector_fullcov.indexing_filters = MagicMock()
        connector_fullcov.indexing_filters.is_enabled.return_value = False

        connector_fullcov.gmail_data_source = AsyncMock()
        full_msg = _make_gmail_message(message_id="filter-msg", thread_id="t1")
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=full_msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        history = {
            "messagesAdded": [
                {"message": {"id": "filter-msg"}}
            ]
        }
        batch = []
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count >= 1
        assert batch[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_no_message_id_in_addition_skips(self, connector_fullcov):
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)
        history = {
            "messagesAdded": [
                {"message": {}}
            ]
        }
        batch = []
        count = await connector_fullcov._process_history_changes("u@e.com", history, batch)
        assert count == 0


class TestRunSyncWithHistoryId:
    @pytest.mark.asyncio
    async def test_inbox_error_handled(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          side_effect=[Exception("inbox fail"), {"history": []}]):
            await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_sent_error_handled(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          side_effect=[{"history": []}, Exception("sent fail")]):
            await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_processes_changes_and_batches(self, connector_fullcov):
        connector_fullcov.batch_size = 1
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}), \
             patch.object(connector_fullcov, "_merge_history_changes", return_value={
                 "history": [{"id": "1"}]
             }), \
             patch.object(connector_fullcov, "_process_history_changes", new_callable=AsyncMock,
                          return_value=2):
            await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")
        connector_fullcov.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_final_batch_error_handled(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": [{"id": "1"}]}), \
             patch.object(connector_fullcov, "_process_history_changes", new_callable=AsyncMock,
                          return_value=1) as mock_ph:
            async def side_effect(user_email, entry, batch):
                batch.append((MagicMock(), []))
                return 1
            mock_ph.side_effect = side_effect
            connector_fullcov.data_entities_processor.on_new_records = AsyncMock(
                side_effect=Exception("batch fail")
            )
            await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_profile_error_keeps_latest_history_id(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("profile fail")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}):
            await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")
        connector_fullcov.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_sync_point_update_error(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        connector_fullcov.gmail_delta_sync_point.update_sync_point = AsyncMock(
            side_effect=Exception("sync fail")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}):
            await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_http_error_propagates(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}), \
             patch.object(connector_fullcov, "_merge_history_changes",
                          side_effect=_make_http_error(404)):
            with pytest.raises(HttpError):
                await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")

    @pytest.mark.asyncio
    async def test_general_exception_updates_sync_point(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}), \
             patch.object(connector_fullcov, "_merge_history_changes",
                          side_effect=RuntimeError("merge fail")):
            with pytest.raises(RuntimeError):
                await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")
        connector_fullcov.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_change_processing_error_continues(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"historyId": "h200"}
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": [{"id": "1"}, {"id": "2"}]}), \
             patch.object(connector_fullcov, "_process_history_changes", new_callable=AsyncMock,
                          side_effect=[Exception("fail"), 1]):
            await connector_fullcov._run_sync_with_history_id("u@e.com", "h100", "key")


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_success(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_create_app_user", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_create_personal_record_group", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_sync_user_mailbox", new_callable=AsyncMock):
            await connector_fullcov.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_no_email_raises(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={}
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_create_app_user", new_callable=AsyncMock):
            with pytest.raises(ValueError, match="Email address not found"):
                await connector_fullcov.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagates(self, connector_fullcov):
        connector_fullcov.gmail_data_source = AsyncMock()
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("fail")
        )
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            with pytest.raises(Exception):
                await connector_fullcov.run_sync()


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self, connector_fullcov):
        with patch.object(connector_fullcov, "run_sync", new_callable=AsyncMock) as mock_sync:
            await connector_fullcov.run_incremental_sync()
            mock_sync.assert_called_once()


class TestHandleWebhookNotification:
    def test_raises_not_implemented(self, connector_fullcov):
        with pytest.raises(NotImplementedError):
            connector_fullcov.handle_webhook_notification({})


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_references(self, connector_fullcov):
        await connector_fullcov.cleanup()
        assert connector_fullcov.gmail_data_source is None
        assert connector_fullcov.gmail_client is None
        assert connector_fullcov.config is None

    @pytest.mark.asyncio
    async def test_cleanup_handles_exception(self, connector_fullcov):
        type(connector_fullcov).gmail_data_source = PropertyMock(side_effect=Exception("fail"))
        try:
            await connector_fullcov.cleanup()
        except Exception:
            pass
        finally:
            del type(connector_fullcov).gmail_data_source


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector_fullcov):
        with pytest.raises(NotImplementedError):
            await connector_fullcov.get_filter_options("key")


class TestConnectionTestException:
    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connector_fullcov):
        connector_fullcov.gmail_client.get_client.side_effect = Exception("fail")
        result = await connector_fullcov.test_connection_and_access()
        assert result is False


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector_fullcov):
        await connector_fullcov.reindex_records([])
        connector_fullcov.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_datasource_raises(self, connector_fullcov):
        connector_fullcov.gmail_data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector_fullcov.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_records(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL

        updated_record = MagicMock()
        perms = [Permission(email="u@e.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]

        with patch.object(connector_fullcov, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock, return_value=(updated_record, perms)):
            await connector_fullcov.reindex_records([record])
        connector_fullcov.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_updated_records(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"

        with patch.object(connector_fullcov, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock, return_value=None):
            await connector_fullcov.reindex_records([record])
        connector_fullcov.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_error_continues(self, connector_fullcov):
        rec1 = MagicMock()
        rec1.id = "rec-1"
        rec2 = MagicMock()
        rec2.id = "rec-2"

        with patch.object(connector_fullcov, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock,
                          side_effect=[Exception("fail"), None]):
            await connector_fullcov.reindex_records([rec1, rec2])
        connector_fullcov.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_general_error_propagates(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        connector_fullcov.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=Exception("fatal")
        )
        with patch.object(connector_fullcov, "_check_and_fetch_updated_record",
                          new_callable=AsyncMock, return_value=None):
            with pytest.raises(Exception):
                await connector_fullcov.reindex_records([record])


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_no_external_id(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = None
        result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_datasource(self, connector_fullcov):
        connector_fullcov.gmail_data_source = None
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_profile_error_returns_none(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            side_effect=Exception("profile fail")
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_email_returns_none(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_routes_to_mail_handler(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL

        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_check_and_fetch_updated_mail_record",
                          new_callable=AsyncMock, return_value=None) as mock_mail:
            result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
            mock_mail.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_to_file_handler(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.FILE

        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_check_and_fetch_updated_file_record",
                          new_callable=AsyncMock, return_value=None) as mock_file:
            result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
            mock_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_type_returns_none(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = "UNKNOWN"

        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock):
            result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_general_exception_returns_none(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "u@e.com"}
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL

        with patch.object(connector_fullcov, "_get_fresh_datasource", new_callable=AsyncMock), \
             patch.object(connector_fullcov, "_check_and_fetch_updated_mail_record",
                          new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None


class TestCheckAndFetchUpdatedMailRecord:
    @pytest.mark.asyncio
    async def test_no_message_id(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = None
        result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_message_not_found(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_null_message(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_thread_id(self, connector_fullcov):
        msg = _make_gmail_message()
        del msg["threadId"]
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_updated_record(self, connector_fullcov):
        msg = _make_gmail_message()
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)

        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "u@e.com:SENT"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_updated_returns_none(self, connector_fullcov):
        msg = _make_gmail_message()
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_record_returns_none(self, connector_fullcov):
        msg = _make_gmail_message()
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(connector_fullcov, "_process_gmail_message",
                          new_callable=AsyncMock, return_value=None):
            result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_general_exception(self, connector_fullcov):
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=RuntimeError("fail")
        )
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1"
        result = await connector_fullcov._check_and_fetch_updated_mail_record("org-1", record, "u@e.com")
        assert result is None


class TestCheckAndFetchUpdatedFileRecord:
    @pytest.mark.asyncio
    async def test_no_stable_attachment_id(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = None
        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_success(self, connector_fullcov):
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

        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        existing = MagicMock()
        existing.id = "existing-att"
        existing.version = 0
        existing.external_record_group_id = "u@e.com:INBOX"
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=existing)

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_parent(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = None
        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_parent_not_found(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_matching_attachment(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "nonexistent-drive-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1")
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_thread_id(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_drive_attachment=True)
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"
        del parent_msg["threadId"]
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_success(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_bad_id_format(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "no-tilde-here"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1")
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_parent_not_found(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=_make_http_error(404)
        )
        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_no_parent_id_uses_from_stable(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = None

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)
        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None):
            result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_not_found_in_parent(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~99"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_no_thread_id(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        del parent_msg["threadId"]
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_parent_null_message(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_general_exception_returns_none(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(
            side_effect=RuntimeError("fatal")
        )
        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_parent_null(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=None)
        result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_updated_returns_data(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "drive-file-id"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_drive_attachment=True)
        parent_msg["payload"]["parts"][0]["body"]["driveFileId"] = "drive-file-id"
        parent_msg["payload"]["parts"][0]["filename"] = "file.zip"
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = MagicMock(spec=RecordUpdate)
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(connector_fullcov, "_process_gmail_attachment",
                          new_callable=AsyncMock, return_value=mock_update):
            result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is not None

    @pytest.mark.asyncio
    async def test_regular_attachment_updated_returns_data(self, connector_fullcov):
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "msg-1~1"
        record.parent_external_record_id = "msg-1"

        parent_msg = _make_gmail_message(message_id="msg-1", has_attachments=True)
        connector_fullcov.gmail_data_source.users_messages_get = AsyncMock(return_value=parent_msg)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = MagicMock(spec=RecordUpdate)
        mock_update.is_deleted = False
        mock_update.is_updated = True
        mock_update.record = MagicMock()
        mock_update.new_permissions = []

        connector_fullcov.data_store_provider = _make_mock_data_store_provider_fullcov(existing_record=None)

        with patch.object(connector_fullcov, "_find_previous_message_in_thread",
                          new_callable=AsyncMock, return_value=None), \
             patch.object(connector_fullcov, "_process_gmail_attachment",
                          new_callable=AsyncMock, return_value=mock_update):
            result = await connector_fullcov._check_and_fetch_updated_file_record("org-1", record, "u@e.com")
        assert result is not None
