"""Tests for GoogleGmailTeamConnector (app/connectors/sources/google/gmail/team/connector.py)."""

import base64
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, ProgressStatus
from app.models.permission import EntityType, Permission, PermissionType
from fastapi import HTTPException
from googleapiclient.errors import HttpError
from app.config.constants.arangodb import (
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import (
    DatetimeOperator,
    Filter,
    FilterCollection,
    FilterType,
    SyncFilterKey,
)
from app.connectors.sources.google.common.gmail_received_date_query import (
    build_gmail_received_date_threads_query,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    Record,
    RecordType,
)

if TYPE_CHECKING:
    from app.connectors.sources.google.gmail.team.connector import (
        GoogleGmailTeamConnector,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_gmail_team")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None, attachment_records=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_records_by_parent = AsyncMock(return_value=attachment_records or [])
    return tx


def _make_mock_data_store_provider(existing_record=None, attachment_records=None):
    tx = _make_mock_tx_store(existing_record, attachment_records=attachment_records)
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
    subject="Team Subject",
    from_email="sender@example.com",
    to_emails="team@example.com",
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
            "filename": "attachment.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "body": {
                "attachmentId": "att-team-1",
                "size": 10000,
            },
        })
    if has_drive_attachment:
        parts.append({
            "partId": "2",
            "filename": "large_file.zip",
            "mimeType": "application/zip",
            "body": {
                "driveFileId": "drive-file-1",
                "size": 50000000,
            },
        })

    body_data = ""
    if body_html:
        body_data = base64.urlsafe_b64encode(body_html.encode()).decode()

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": label_ids,
        "snippet": "Team snippet...",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": body_data},
            "parts": parts,
        },
    }


def _make_google_user(email="user@example.com", user_id="guser-1", full_name="Team User"):
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name},
        "suspended": False,
        "creationTime": "2024-01-01T00:00:00.000Z",
    }


@pytest.fixture
def connector():
    """Create a GoogleGmailTeamConnector with fully mocked dependencies."""
    with patch(
        "app.connectors.sources.google.gmail.team.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.gmail.team.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.gmail.team.connector import (
            GoogleGmailTeamConnector,
        )

        logger = _make_logger()
        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_user_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.get_record_by_external_id = AsyncMock(return_value=None)
        dep.get_records_by_parent = AsyncMock(return_value=[])
        dep.create_record_relation = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com"},
            "credentials": {},
        })

        conn = GoogleGmailTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="gmail-team-1",
            scope="team",
            created_by="test",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.admin_client = MagicMock()
        conn.gmail_client = MagicMock()
        conn.admin_data_source = AsyncMock()
        conn.gmail_data_source = AsyncMock()
        async def execute(operation):
            return operation()
        conn.gmail_data_source.execute = AsyncMock(side_effect=execute)
        conn.config = {"credentials": {}}
        yield conn


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestTeamGmailInit:
    async def test_init_returns_false_when_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    async def test_init_raises_when_no_auth(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector.init()

    async def test_init_raises_when_no_admin_email(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"someKey": "someValue"},
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector.init()


# ---------------------------------------------------------------------------
# _parse_gmail_headers (team connector has same method)
# ---------------------------------------------------------------------------

class TestTeamParseHeaders:
    def test_extracts_relevant_headers(self, connector):
        headers = [
            {"name": "Subject", "value": "Hello"},
            {"name": "From", "value": "alice@example.com"},
            {"name": "To", "value": "bob@example.com"},
            {"name": "Cc", "value": "carol@example.com"},
            {"name": "Message-ID", "value": "<abc@gmail.com>"},
            {"name": "Date", "value": "Mon, 1 Jan 2024"},
            {"name": "X-Custom-Header", "value": "ignored"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert result["subject"] == "Hello"
        assert result["from"] == "alice@example.com"
        assert result["to"] == "bob@example.com"
        assert result["cc"] == "carol@example.com"
        assert result["message-id"] == "<abc@gmail.com>"
        assert result["date"] == "Mon, 1 Jan 2024"
        assert "x-custom-header" not in result

    def test_empty_headers(self, connector):
        assert connector._parse_gmail_headers([]) == {}


# ---------------------------------------------------------------------------
# _extract_email_from_header
# ---------------------------------------------------------------------------

class TestTeamExtractEmailFromHeader:
    def test_plain_email(self, connector):
        assert connector._extract_email_from_header("alice@example.com") == "alice@example.com"

    def test_name_and_email_format(self, connector):
        assert connector._extract_email_from_header("Alice <alice@example.com>") == "alice@example.com"

    def test_empty_string(self, connector):
        assert connector._extract_email_from_header("") == ""

    def test_none_returns_empty(self, connector):
        assert connector._extract_email_from_header(None) == ""


# ---------------------------------------------------------------------------
# _parse_email_list
# ---------------------------------------------------------------------------

class TestTeamParseEmailList:
    def test_single_email(self, connector):
        assert connector._parse_email_list("alice@example.com") == ["alice@example.com"]

    def test_multiple_emails(self, connector):
        result = connector._parse_email_list("a@e.com, b@e.com, c@e.com")
        assert len(result) == 3

    def test_empty_string(self, connector):
        assert connector._parse_email_list("") == []

    def test_none_returns_empty(self, connector):
        assert connector._parse_email_list(None) == []


# ---------------------------------------------------------------------------
# _process_gmail_message (team)
# ---------------------------------------------------------------------------

class TestTeamProcessGmailMessage:
    async def test_new_message_creates_mail_record(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL
        assert result.record.record_name == "Team Subject"

    async def test_inbox_label_routing(self, connector):
        message = _make_gmail_message(label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:INBOX"

    async def test_sent_label_routing(self, connector):
        message = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:SENT"

    async def test_other_label_routing(self, connector):
        message = _make_gmail_message(label_ids=["CATEGORY_PROMOTIONS"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:OTHERS"

    async def test_sent_takes_priority_over_inbox(self, connector):
        message = _make_gmail_message(label_ids=["INBOX", "SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:SENT"

    async def test_sender_is_owner(self, connector):
        message = _make_gmail_message(from_email="user@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions == []

    async def test_non_sender_gets_read(self, connector):
        message = _make_gmail_message(from_email="other@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions == []

    async def test_case_insensitive_sender_comparison(self, connector):
        message = _make_gmail_message(from_email="User@Example.COM")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions == []

    async def test_no_message_id_returns_none(self, connector):
        message = {"threadId": "t1", "payload": {"headers": []}}
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is None

    async def test_no_subject_defaults(self, connector):
        message = _make_gmail_message()
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

    async def test_weburl_uses_viewer_placeholder_not_synced_user_email(self, connector):
        """weburl must carry the {user.email} placeholder, not the synced mailbox owner's
        email, so retrieval_service can substitute the *viewing* user's email later.
        Baking the synced owner's email in here defeats that substitution (see #3002)."""
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert "authuser={user.email}" in result.record.weburl
        assert "user@example.com" not in result.record.weburl

    async def test_existing_record_detected(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "user@example.com:INBOX"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

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
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

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
        assert result.record.source_created_at is not None

    async def test_no_internal_date(self, connector):
        message = _make_gmail_message()
        del message["internalDate"]
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is not None

    async def test_cc_bcc_parsed(self, connector):
        message = _make_gmail_message(cc_emails="cc@e.com", bcc_emails="bcc@e.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert "cc@e.com" in result.record.cc_emails
        assert "bcc@e.com" in result.record.bcc_emails

    async def test_received_date_filter_does_not_skip_message(self, connector):
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": 1704067200001, "end": None},
            "type": "datetime",
            "operator": "is_after",
        })
        connector.sync_filters = FilterCollection(filters=[date_filter])
        message = _make_gmail_message(internal_date="1704067200000")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# RECEIVED_DATE query string (team)
# ---------------------------------------------------------------------------

class TestTeamGmailDateFilter:
    def test_no_filter_empty_query(self, connector):
        message = _make_gmail_message()
        assert message["internalDate"]
        assert (
            build_gmail_received_date_threads_query(
                connector.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
            )
            is None
        )

    def test_is_after_query(self, connector):
        date_filter = Filter(
            key=SyncFilterKey.RECEIVED_DATE.value,
            value={"start": 1704067200001, "end": None},
            type=FilterType.DATETIME,
            operator=DatetimeOperator.IS_AFTER,
        )
        connector.sync_filters = FilterCollection(filters=[date_filter])
        q = build_gmail_received_date_threads_query(
            connector.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        )
        assert q == "after:1704067200"


# ---------------------------------------------------------------------------
# Attachment extraction (team)
# ---------------------------------------------------------------------------

class TestTeamExtractAttachments:
    def test_regular_attachment_extracted(self, connector):
        message = _make_gmail_message(has_attachments=True)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["filename"] == "attachment.xlsx"
        assert infos[0]["isDriveFile"] is False
        assert infos[0]["stableAttachmentId"] == "msg-1~1"

    def test_no_attachments(self, connector):
        message = _make_gmail_message(has_attachments=False)
        assert len(connector._extract_attachment_infos(message)) == 0

    def test_drive_attachment(self, connector):
        message = _make_gmail_message(has_drive_attachment=True)
        infos = connector._extract_attachment_infos(
            message, "user@example.com", google_user_id="g-user-1"
        )
        drive_infos = [i for i in infos if i["isDriveFile"]]
        assert len(drive_infos) == 1
        assert drive_infos[0]["driveFileId"] == "drive-file-1"
        assert drive_infos[0]["stableAttachmentId"] == "gdrive:g-user-1:drive-file-1"

    def test_drive_attachment_id_from_synced_mailbox_owner(self, connector: "GoogleGmailTeamConnector") -> None:
        connector._google_user_id_by_email = {"user@example.com": "g-user-1"}
        message = _make_gmail_message(has_drive_attachment=True)
        infos = connector._extract_attachment_infos(message, "User@Example.com")
        assert [i["stableAttachmentId"] for i in infos if i["isDriveFile"]] == [
            "gdrive:g-user-1:drive-file-1"
        ]

    def test_drive_attachment_skipped_without_google_user_id(self, connector: "GoogleGmailTeamConnector") -> None:
        message = _make_gmail_message(has_attachments=True, has_drive_attachment=True)
        infos = connector._extract_attachment_infos(message, "user@example.com")
        assert [i["isDriveFile"] for i in infos] == [False]

    def test_drive_file_ids_from_body_content(self, connector):
        html = '<a href="https://drive.google.com/file/d/DRIVE_ID_1/view?usp=drive_web">Link</a>'
        message = _make_gmail_message(body_html=html)
        infos = connector._extract_attachment_infos(
            message, "user@example.com", google_user_id="g-user-1"
        )
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "DRIVE_ID_1"
        assert infos[0]["stableAttachmentId"] == "gdrive:g-user-1:DRIVE_ID_1"

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
                            {
                                "partId": "0.0",
                                "mimeType": "text/plain",
                                "body": {"data": ""},
                            }
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
# _process_gmail_attachment (team)
# ---------------------------------------------------------------------------

class TestTeamProcessAttachment:
    async def test_creates_file_record(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "data.csv", "mimeType": "text/csv",
            "size": 500, "isDriveFile": False,
        }
        parent_perms = [Permission(email="u@e.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        result = await connector._process_gmail_attachment(
            user_email="u@e.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=parent_perms,
            external_record_group_id="u@e.com:OTHERS",
        )
        assert result is not None
        assert result.record.record_name == "data.csv"
        assert result.record.record_type == RecordType.FILE
        assert result.record.is_dependent_node is True

    async def test_returns_none_for_missing_stable_id(self, connector):
        attachment_info = {"attachmentId": "att-1", "stableAttachmentId": None, "isDriveFile": False}
        result = await connector._process_gmail_attachment(
            user_email="u@e.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=[],
            external_record_group_id="u@e.com:OTHERS",
        )
        assert result is None

    async def test_returns_none_for_missing_attachment_id(self, connector):
        attachment_info = {
            "attachmentId": None, "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="u@e.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=[],
            external_record_group_id="u@e.com:OTHERS",
        )
        assert result is None

    async def test_inherits_parent_permissions(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "file.txt", "mimeType": "text/plain",
            "size": 100, "isDriveFile": False,
        }
        parent_perms = [Permission(email="u@e.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        result = await connector._process_gmail_attachment(
            user_email="u@e.com", message_id="msg-1",
            attachment_info=attachment_info, parent_mail_permissions=parent_perms,
            external_record_group_id="u@e.com:OTHERS",
        )
        assert result.new_permissions == []

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

        with patch("app.connectors.sources.google.gmail.team.connector.GoogleClient.build_from_services",
                    new_callable=AsyncMock, return_value=mock_drive_client):
            result = await connector._process_gmail_attachment(
                user_email="u@e.com", message_id="msg-1",
                attachment_info=attachment_info, parent_mail_permissions=[],
                external_record_group_id="u@e.com:OTHERS",
            )
        assert result is not None
        assert result.record.record_name == "report.xlsx"

    async def test_drive_file_metadata_fetch_failure(self, connector):
        attachment_info = {
            "attachmentId": None, "driveFileId": "drive-1",
            "stableAttachmentId": "drive-1", "partId": "1",
            "filename": "fallback.bin", "mimeType": "application/octet-stream",
            "size": 100, "isDriveFile": True,
        }
        with patch("app.connectors.sources.google.gmail.team.connector.GoogleClient.build_from_services",
                    new_callable=AsyncMock, side_effect=Exception("Drive error")):
            result = await connector._process_gmail_attachment(
                user_email="u@e.com", message_id="msg-1",
                attachment_info=attachment_info, parent_mail_permissions=[],
                external_record_group_id="u@e.com:OTHERS",
            )
        assert result is not None
        assert result.record.record_name == "fallback.bin"


# ---------------------------------------------------------------------------
# _process_gmail_message_generator
# ---------------------------------------------------------------------------

class TestMessageGeneratorWithFilters:
    async def test_generator_applies_mail_indexing_filter(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False
        messages = [_make_gmail_message()]
        results = []
        async for update in connector._process_gmail_message_generator(messages, "user@example.com", "thread-1"):
            if update:
                results.append(update)
        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_generator_skips_messages_with_no_id(self, connector):
        messages = [{"threadId": "t1", "payload": {"headers": []}}]
        results = []
        async for update in connector._process_gmail_message_generator(messages, "user@example.com", "thread-1"):
            if update:
                results.append(update)
        assert len(results) == 0

    async def test_generator_handles_exception(self, connector):
        with patch.object(connector, "_process_gmail_message", new_callable=AsyncMock,
                          side_effect=Exception("process error")):
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

class TestAttachmentGeneratorWithFilters:
    async def test_generator_applies_attachment_indexing_filter(self, connector):
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
            "user@example.com", "msg-1", attachment_infos, parent_perms, "user@example.com:OTHERS"
        ):
            if update:
                results.append(update)
        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# ---------------------------------------------------------------------------
# _get_existing_record
# ---------------------------------------------------------------------------

class TestGetExistingRecord:
    async def test_returns_existing_record(self, connector):
        existing = MagicMock()
        existing.id = "found-id"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        result = await connector._get_existing_record("ext-id-1")
        assert result is not None

    async def test_returns_none_when_not_found(self, connector):
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        result = await connector._get_existing_record("nonexistent")
        assert result is None

    async def test_returns_none_on_error(self, connector):
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=Exception("DB error")
        )
        result = await connector._get_existing_record("ext-id-1")
        assert result is None


# ---------------------------------------------------------------------------
# _merge_history_changes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _run_full_sync
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _run_sync_with_yield
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deep sync: _run_sync_with_history_id (incremental)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deep sync: _fetch_history_changes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deep sync: _process_history_changes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deep sync: full sync thread pagination
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Deep sync: _run_sync_with_yield error handling
# ---------------------------------------------------------------------------

class TestTeamRunSyncWithYieldErrors:
    async def test_propagates_general_exception(self, connector):
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock,
                          side_effect=Exception("client error")):
            with pytest.raises(Exception, match="client error"):
                await connector._run_sync_with_yield("user@example.com")

# =============================================================================
# Merged from test_gmail_team_full_coverage.py
# =============================================================================

def _make_logger_fullcov():
    log = logging.getLogger("test_gmail_team_fc")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store_fullcov(existing_record=None, user_with_perm=None, user_by_id=None,
                        attachment_records=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user_with_perm)
    tx.get_users_with_permission_to_node = AsyncMock(
        return_value=[user_with_perm] if user_with_perm else []
    )
    tx.get_user_by_user_id = AsyncMock(return_value=user_by_id)
    tx.get_records_by_parent = AsyncMock(return_value=attachment_records or [])
    return tx


def _make_mock_data_store_provider_fullcov(existing_record=None, user_with_perm=None,
                                    user_by_id=None, attachment_records=None):
    tx = _make_mock_tx_store_fullcov(existing_record, user_with_perm, user_by_id, attachment_records)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_gmail_message_fullcov(
    message_id="msg-1", thread_id="thread-1", subject="Test Subject",
    from_email="sender@example.com", to_emails="receiver@example.com",
    cc_emails="", bcc_emails="", label_ids=None, internal_date="1704067200000",
    has_attachments=False, has_drive_attachment=False, body_html=None,
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
            "filename": "attachment.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "body": {"attachmentId": "att-1", "size": 10000},
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
        "snippet": "Snippet...",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": body_data},
            "parts": parts,
        },
    }


def _make_google_user_fullcov(email="user@example.com", user_id="guser-1", full_name="Test User",
                      suspended=False, creation_time="2024-01-01T00:00:00.000Z"):
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name},
        "suspended": suspended,
        "creationTime": creation_time,
    }


def _make_record(record_id="rec-1", external_id="msg-1", record_name="Test",
                 record_type=RecordType.MAIL, version=0,
                 external_record_group_id="u@t.com:INBOX",
                 parent_external_record_id=None, connector_id="gmail-fc-1"):
    r = MagicMock(spec=Record)
    r.id = record_id
    r.external_record_id = external_id
    r.record_name = record_name
    r.record_type = record_type
    r.version = version
    r.external_record_group_id = external_record_group_id
    r.parent_external_record_id = parent_external_record_id
    r.connector_id = connector_id
    r.mime_type = MimeTypes.GMAIL.value
    return r


@pytest.fixture
def connector_fullcov():
    with patch(
        "app.connectors.sources.google.gmail.team.connector.GoogleClient"
    ), patch(
        "app.connectors.sources.google.gmail.team.connector.SyncPoint"
    ) as MockSyncPoint:
        mock_sync_point = AsyncMock()
        mock_sync_point.read_sync_point = AsyncMock(return_value=None)
        mock_sync_point.update_sync_point = AsyncMock()
        MockSyncPoint.return_value = mock_sync_point

        from app.connectors.sources.google.gmail.team.connector import (
            GoogleGmailTeamConnector,
        )

        logger = _make_logger_fullcov()
        dep = AsyncMock()
        dep.org_id = "org-1"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_user_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.get_all_active_users = AsyncMock(return_value=[])
        dep.reindex_existing_records = AsyncMock()
        dep.delete_permission_from_record = AsyncMock()
        dep.get_users_with_permission_to_node = AsyncMock(return_value=[])
        dep.get_record_by_external_id = AsyncMock(return_value=None)
        dep.get_records_by_parent = AsyncMock(return_value=[])
        dep.create_record_relation = AsyncMock()

        ds_provider = _make_mock_data_store_provider_fullcov()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com"},
            "credentials": {},
        })

        conn = GoogleGmailTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="gmail-fc-1",
            scope="personal",
            created_by="test-user-id",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.admin_client = MagicMock()
        conn.gmail_client = MagicMock()
        conn.admin_data_source = AsyncMock()
        conn.gmail_data_source = AsyncMock()
        async def execute(operation):
            return operation()
        conn.gmail_data_source.execute = AsyncMock(side_effect=execute)
        conn.config = {"credentials": {"auth": {}}}
        yield conn


class TestInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value=None)
        result = await connector_fullcov.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_auth(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector_fullcov.init()

    @pytest.mark.asyncio
    async def test_init_no_admin_email(self, connector_fullcov):
        connector_fullcov.config_service.get_config = AsyncMock(return_value={
            "auth": {"someKey": "someValue"},
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector_fullcov.init()


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_orchestrates(self, connector_fullcov):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector_fullcov._sync_users = AsyncMock()
            connector_fullcov._sync_user_groups = AsyncMock()
            connector_fullcov._sync_record_groups = AsyncMock()
            connector_fullcov._process_users_in_batches = AsyncMock()
            connector_fullcov.synced_users = []
            await connector_fullcov.run_sync()
            connector_fullcov._sync_users.assert_awaited_once()
            connector_fullcov._sync_user_groups.assert_awaited_once()
            connector_fullcov._sync_record_groups.assert_awaited_once()
            connector_fullcov._process_users_in_batches.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagates(self, connector_fullcov):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector_fullcov._sync_users = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(RuntimeError, match="boom"):
                await connector_fullcov.run_sync()


class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_no_admin_source(self, connector_fullcov):
        connector_fullcov.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector_fullcov._sync_users()

    @pytest.mark.asyncio
    async def test_empty(self, connector_fullcov):
        connector_fullcov.admin_data_source.users_list = AsyncMock(return_value={"users": []})
        await connector_fullcov._sync_users()
        assert connector_fullcov.synced_users == []

    @pytest.mark.asyncio
    async def test_pagination(self, connector_fullcov):
        page1 = [_make_google_user_fullcov(email="u1@t.com", user_id="u1")]
        page2 = [_make_google_user_fullcov(email="u2@t.com", user_id="u2")]
        connector_fullcov.admin_data_source.users_list = AsyncMock(side_effect=[
            {"users": page1, "nextPageToken": "tok2"},
            {"users": page2},
        ])
        await connector_fullcov._sync_users()
        assert len(connector_fullcov.synced_users) == 2

    @pytest.mark.asyncio
    async def test_skip_no_email(self, connector_fullcov):
        no_email = {"id": "u1", "name": {"fullName": "No Email"}}
        connector_fullcov.admin_data_source.users_list = AsyncMock(return_value={"users": [no_email]})
        await connector_fullcov._sync_users()
        assert connector_fullcov.synced_users == []

    @pytest.mark.asyncio
    async def test_suspended_user(self, connector_fullcov):
        user = _make_google_user_fullcov(suspended=True)
        connector_fullcov.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector_fullcov._sync_users()
        assert connector_fullcov.synced_users[0].is_active is False

    @pytest.mark.asyncio
    async def test_name_fallback_given_family(self, connector_fullcov):
        user = {"id": "u1", "primaryEmail": "u@t.com",
                "name": {"givenName": "First", "familyName": "Last"},
                "creationTime": "2024-01-01T00:00:00.000Z"}
        connector_fullcov.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector_fullcov._sync_users()
        assert connector_fullcov.synced_users[0].full_name == "First Last"

    @pytest.mark.asyncio
    async def test_name_fallback_email(self, connector_fullcov):
        user = {"id": "u1", "primaryEmail": "u@t.com", "name": {},
                "creationTime": "2024-01-01T00:00:00.000Z"}
        connector_fullcov.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector_fullcov._sync_users()
        assert connector_fullcov.synced_users[0].full_name == "u@t.com"

    @pytest.mark.asyncio
    async def test_bad_creation_time(self, connector_fullcov):
        user = _make_google_user_fullcov()
        user["creationTime"] = "not-a-date"
        connector_fullcov.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector_fullcov._sync_users()
        assert connector_fullcov.synced_users[0].source_created_at is None


class TestSyncUserGroups:
    @pytest.mark.asyncio
    async def test_no_admin(self, connector_fullcov):
        connector_fullcov.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector_fullcov._sync_user_groups()

    @pytest.mark.asyncio
    async def test_empty(self, connector_fullcov):
        connector_fullcov.admin_data_source.groups_list = AsyncMock(return_value={"groups": []})
        await connector_fullcov._sync_user_groups()

    @pytest.mark.asyncio
    async def test_with_groups(self, connector_fullcov):
        connector_fullcov.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "grp@t.com", "name": "Grp"}]
        })
        connector_fullcov._process_group = AsyncMock()
        await connector_fullcov._sync_user_groups()
        connector_fullcov._process_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_continues_on_group_error(self, connector_fullcov):
        connector_fullcov.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "g1@t.com"}, {"email": "g2@t.com"}]
        })
        connector_fullcov._process_group = AsyncMock(side_effect=[RuntimeError("fail"), None])
        await connector_fullcov._sync_user_groups()
        assert connector_fullcov._process_group.await_count == 2

    @pytest.mark.asyncio
    async def test_pagination(self, connector_fullcov):
        connector_fullcov.admin_data_source.groups_list = AsyncMock(side_effect=[
            {"groups": [{"email": "g1@t.com"}], "nextPageToken": "tok"},
            {"groups": [{"email": "g2@t.com"}]},
        ])
        connector_fullcov._process_group = AsyncMock()
        await connector_fullcov._sync_user_groups()
        assert connector_fullcov._process_group.await_count == 2


class TestProcessGroup:
    @pytest.mark.asyncio
    async def test_no_email(self, connector_fullcov):
        await connector_fullcov._process_group({"name": "No Email"})
        connector_fullcov.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_members(self, connector_fullcov):
        connector_fullcov._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m@t.com", "id": "m1"},
        ])
        group = {"email": "grp@t.com", "name": "Grp", "creationTime": "2024-01-01T00:00:00.000Z"}
        await connector_fullcov._process_group(group)
        connector_fullcov.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_user_members(self, connector_fullcov):
        connector_fullcov._fetch_group_members = AsyncMock(return_value=[
            {"type": "GROUP", "email": "sub@t.com"},
        ])
        await connector_fullcov._process_group({"email": "grp@t.com", "name": "Grp"})
        # A group with no user members is written empty, so people who left lose it.
        written = connector_fullcov.data_entities_processor.on_new_user_groups.call_args[0][0]
        assert len(written) == 1 and written[0][1] == []

    @pytest.mark.asyncio
    async def test_member_lookup_in_synced_users(self, connector_fullcov):
        connector_fullcov.synced_users = [
            AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="m1",
                    email="m@t.com", full_name="Member One", source_created_at=1000)
        ]
        connector_fullcov._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m@t.com", "id": "m1"},
        ])
        await connector_fullcov._process_group({"email": "grp@t.com", "name": "Grp"})
        args = connector_fullcov.data_entities_processor.on_new_user_groups.call_args[0][0]
        _, members = args[0]
        assert members[0].full_name == "Member One"

    @pytest.mark.asyncio
    async def test_name_fallback_email(self, connector_fullcov):
        connector_fullcov._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m@t.com", "id": "m1"},
        ])
        await connector_fullcov._process_group({"email": "grp@t.com", "name": ""})
        args = connector_fullcov.data_entities_processor.on_new_user_groups.call_args[0][0]
        ug, _ = args[0]
        assert ug.name == "grp@t.com"


class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_pagination(self, connector_fullcov):
        connector_fullcov.admin_data_source.members_list = AsyncMock(side_effect=[
            {"members": [{"id": "m1"}], "nextPageToken": "tok"},
            {"members": [{"id": "m2"}]},
        ])
        result = await connector_fullcov._fetch_group_members("grp@t.com")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self, connector_fullcov):
        connector_fullcov.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        result = await connector_fullcov._fetch_group_members("grp@t.com")
        assert result == []

    @pytest.mark.asyncio
    async def test_error(self, connector_fullcov):
        connector_fullcov.admin_data_source.members_list = AsyncMock(side_effect=RuntimeError("err"))
        with pytest.raises(RuntimeError):
            await connector_fullcov._fetch_group_members("grp@t.com")


class TestSyncRecordGroups:
    @pytest.mark.asyncio
    async def test_empty_users(self, connector_fullcov):
        await connector_fullcov._sync_record_groups([])

    @pytest.mark.asyncio
    async def test_creates_inbox_sent_others(self, connector_fullcov):
        user = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="User One")
        await connector_fullcov._sync_record_groups([user])
        assert connector_fullcov.data_entities_processor.on_new_record_groups.await_count == 1

    @pytest.mark.asyncio
    async def test_skips_user_without_email(self, connector_fullcov):
        user = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                       email="", full_name="No Email")
        await connector_fullcov._sync_record_groups([user])
        connector_fullcov.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_on_error(self, connector_fullcov):
        user = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="User One")
        connector_fullcov.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=[RuntimeError("fail"), None, None]
        )
        await connector_fullcov._sync_record_groups([user])


class TestPassDateFilter:
    def test_no_filter_empty_query(self, connector_fullcov):
        assert (
            build_gmail_received_date_threads_query(
                connector_fullcov.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
            )
            is None
        )

    def test_is_between_query(self, connector_fullcov):
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": 2000, "end": 5000},
            "type": "datetime",
            "operator": "is_between",
        })
        connector_fullcov.sync_filters = FilterCollection(filters=[date_filter])
        q = build_gmail_received_date_threads_query(
            connector_fullcov.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        )
        assert q == "after:2 before:5"

    def test_is_before_query(self, connector_fullcov):
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": None, "end": 3000},
            "type": "datetime",
            "operator": "is_before",
        })
        connector_fullcov.sync_filters = FilterCollection(filters=[date_filter])
        q = build_gmail_received_date_threads_query(
            connector_fullcov.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        )
        assert q == "before:3"


class TestProcessGmailMessage:
    @pytest.mark.asyncio
    async def test_no_message_id(self, connector_fullcov):
        result = await connector_fullcov._process_gmail_message("u@t.com", {}, "t1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_new_inbox_message(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(from_email="other@t.com")
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "thread-1", None)
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL
        assert "INBOX" in result.record.external_record_group_id

    @pytest.mark.asyncio
    async def test_sent_message_owner_perm(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(from_email="u@t.com", label_ids=["SENT"])
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "thread-1", None)
        assert result.record.external_record_group_id == "u@t.com:SENT"
        assert result.new_permissions == []

    @pytest.mark.asyncio
    async def test_received_message_read_perm(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(from_email="other@t.com", label_ids=["INBOX"])
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "thread-1", None)
        assert result.new_permissions == []

    @pytest.mark.asyncio
    async def test_received_date_does_not_skip_message(self, connector_fullcov):
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": 99999999999999, "end": None},
            "type": "datetime",
            "operator": "is_after",
        })
        connector_fullcov.sync_filters = FilterCollection(filters=[date_filter])
        msg = _make_gmail_message_fullcov()
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "t1", None)
        assert result is not None

    @pytest.mark.asyncio
    async def test_other_label(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(label_ids=["CATEGORY_UPDATES"])
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "t1", None)
        assert "OTHERS" in result.record.external_record_group_id

    @pytest.mark.asyncio
    async def test_existing_message_metadata_change(self, connector_fullcov):
        existing = _make_record(external_record_group_id="u@t.com:INBOX")
        connector_fullcov.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing
        )
        msg = _make_gmail_message_fullcov(label_ids=["SENT"])
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "t1", None)
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_with_cc_bcc(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(cc_emails="cc@t.com", bcc_emails="bcc@t.com")
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "t1", None)
        assert result.record.cc_emails == ["cc@t.com"]
        assert result.record.bcc_emails == ["bcc@t.com"]

    @pytest.mark.asyncio
    async def test_from_header_with_name(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(from_email="Sender Name <sender@t.com>")
        result = await connector_fullcov._process_gmail_message("u@t.com", msg, "t1", None)
        assert result is not None


class TestExtractAttachmentInfos:
    def test_regular_attachment(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(has_attachments=True)
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["filename"] == "attachment.xlsx"
        assert not infos[0]["isDriveFile"]

    def test_drive_attachment(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(has_drive_attachment=True)
        infos = connector_fullcov._extract_attachment_infos(msg, "u@t.com", google_user_id="g-1")
        assert len(infos) == 1
        assert infos[0]["isDriveFile"] is True
        assert infos[0]["driveFileId"] == "drive-file-1"
        assert infos[0]["stableAttachmentId"] == "gdrive:g-1:drive-file-1"

    def test_both_attachment_types(self, connector_fullcov):
        msg = _make_gmail_message_fullcov(has_attachments=True, has_drive_attachment=True)
        infos = connector_fullcov._extract_attachment_infos(msg, "u@t.com", google_user_id="g-1")
        assert len(infos) == 2

    def test_no_attachments(self, connector_fullcov):
        msg = _make_gmail_message_fullcov()
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert len(infos) == 0

    def test_drive_file_in_body_link(self, connector_fullcov):
        html = '<a href="https://drive.google.com/file/d/abc123/view?usp=drive_web">link</a>'
        body_data = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": body_data},
                "parts": [],
            }
        }
        infos = connector_fullcov._extract_attachment_infos(msg, "u@t.com", google_user_id="g-1")
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "abc123"
        assert infos[0]["stableAttachmentId"] == "gdrive:g-1:abc123"

    def test_nested_parts(self, connector_fullcov):
        msg = {
            "id": "msg-1",
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": [
                            {
                                "partId": "0.0",
                                "filename": "nested.pdf",
                                "mimeType": "application/pdf",
                                "body": {"attachmentId": "att-nested", "size": 500},
                            }
                        ]
                    }
                ],
            }
        }
        infos = connector_fullcov._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["filename"] == "nested.pdf"


class TestProcessGmailAttachment:
    @pytest.mark.asyncio
    async def test_regular_attachment(self, connector_fullcov):
        attach_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "file.pdf",
            "mimeType": "application/pdf",
            "size": 1024,
            "isDriveFile": False,
        }
        perms = [Permission(email="u@t.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        result = await connector_fullcov._process_gmail_attachment("u@t.com", "msg-1", attach_info, perms, "u@t.com:OTHERS")
        assert result is not None
        assert result.record.record_name == "file.pdf"
        assert result.record.extension == "pdf"

    @pytest.mark.asyncio
    async def test_no_stable_id(self, connector_fullcov):
        attach_info = {"attachmentId": "att-1", "stableAttachmentId": None, "isDriveFile": False}
        result = await connector_fullcov._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_no_attachment_id(self, connector_fullcov):
        attach_info = {
            "attachmentId": None, "stableAttachmentId": "msg-1~1",
            "isDriveFile": False, "driveFileId": None,
        }
        result = await connector_fullcov._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_attachment_fetches_metadata(self, connector_fullcov):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = MagicMock()
            mock_service = MagicMock()
            mock_files = MagicMock()
            mock_get = MagicMock()
            mock_get.execute.return_value = {
                "id": "df1", "name": "drive_file.docx",
                "mimeType": "application/docx", "size": "2048"
            }
            mock_files.get.return_value = mock_get
            mock_service.files.return_value = mock_files
            mock_client.get_client.return_value = mock_service
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            attach_info = {
                "attachmentId": None,
                "driveFileId": "df1",
                "stableAttachmentId": "df1",
                "partId": "2",
                "filename": None,
                "mimeType": "application/vnd.google-apps.file",
                "size": 0,
                "isDriveFile": True,
            }
            result = await connector_fullcov._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
            assert result is not None
            assert result.record.record_name == "drive_file.docx"

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self, connector_fullcov):
        attach_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "file.pdf", "mimeType": "application/pdf",
            "size": 1024, "isDriveFile": False,
        }
        mock_filter = MagicMock()
        mock_filter.is_enabled = MagicMock(return_value=False)
        connector_fullcov.indexing_filters = mock_filter
        result = await connector_fullcov._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestProcessGmailMessageGenerator:
    @pytest.mark.asyncio
    async def test_yields_updates(self, connector_fullcov):
        msg = _make_gmail_message_fullcov()
        record = MagicMock()
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[], external_record_id="msg-1"
        )
        connector_fullcov._process_gmail_message = AsyncMock(return_value=update)
        results = []
        async for item in connector_fullcov._process_gmail_message_generator([msg], "u@t.com", "t1"):
            results.append(item)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_skips_none(self, connector_fullcov):
        connector_fullcov._process_gmail_message = AsyncMock(return_value=None)
        results = []
        async for item in connector_fullcov._process_gmail_message_generator([{}], "u@t.com", "t1"):
            results.append(item)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_index_off_filter(self, connector_fullcov):
        record = MagicMock()
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[], external_record_id="msg-1"
        )
        connector_fullcov._process_gmail_message = AsyncMock(return_value=update)
        mock_filter = MagicMock()
        mock_filter.is_enabled = MagicMock(return_value=False)
        connector_fullcov.indexing_filters = mock_filter
        results = []
        async for item in connector_fullcov._process_gmail_message_generator([_make_gmail_message_fullcov()], "u@t.com", "t1"):
            results.append(item)
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestProcessGmailAttachmentGenerator:
    @pytest.mark.asyncio
    async def test_yields_updates(self, connector_fullcov):
        record = MagicMock()
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[], external_record_id="msg-1~1"
        )
        connector_fullcov._process_gmail_attachment = AsyncMock(return_value=update)
        attach_info = {"stableAttachmentId": "msg-1~1"}
        results = []
        async for item in connector_fullcov._process_gmail_attachment_generator("u@t.com", "msg-1", [attach_info], [], "u@t.com:OTHERS"):
            results.append(item)
        assert len(results) == 1


class TestParseGmailHeaders:
    def test_parses_relevant_headers(self, connector_fullcov):
        headers = [
            {"name": "Subject", "value": "Test"},
            {"name": "From", "value": "from@t.com"},
            {"name": "To", "value": "to@t.com"},
            {"name": "Cc", "value": "cc@t.com"},
            {"name": "Bcc", "value": "bcc@t.com"},
            {"name": "Message-ID", "value": "<mid>"},
            {"name": "Date", "value": "Mon, 01 Jan 2024"},
            {"name": "X-Custom", "value": "ignored"},
        ]
        result = connector_fullcov._parse_gmail_headers(headers)
        assert result["subject"] == "Test"
        assert result["from"] == "from@t.com"
        assert "x-custom" not in result

    def test_empty_headers(self, connector_fullcov):
        result = connector_fullcov._parse_gmail_headers([])
        assert result == {}


class TestCreateOwnerPermission:
    def test_creates_owner(self, connector_fullcov):
        perm = connector_fullcov._create_owner_permission("u@t.com")
        assert perm.email == "u@t.com"
        assert perm.type == PermissionType.OWNER
        assert perm.entity_type == EntityType.USER


class TestParseEmailList:
    def test_comma_separated(self, connector_fullcov):
        result = connector_fullcov._parse_email_list("a@t.com, b@t.com, c@t.com")
        assert result == ["a@t.com", "b@t.com", "c@t.com"]

    def test_empty_string(self, connector_fullcov):
        assert connector_fullcov._parse_email_list("") == []

    def test_single(self, connector_fullcov):
        assert connector_fullcov._parse_email_list("a@t.com") == ["a@t.com"]

    def test_filters_empty_parts(self, connector_fullcov):
        result = connector_fullcov._parse_email_list("a@t.com,,")
        assert result == ["a@t.com"]


class TestExtractEmailFromHeader:
    def test_name_and_email(self, connector_fullcov):
        assert connector_fullcov._extract_email_from_header("John <john@t.com>") == "john@t.com"

    def test_just_email(self, connector_fullcov):
        assert connector_fullcov._extract_email_from_header("john@t.com") == "john@t.com"

    def test_empty(self, connector_fullcov):
        assert connector_fullcov._extract_email_from_header("") == ""

    def test_none(self, connector_fullcov):
        assert connector_fullcov._extract_email_from_header(None) == ""

    def test_with_spaces(self, connector_fullcov):
        assert connector_fullcov._extract_email_from_header("  John Doe < john@t.com >  ") == "john@t.com"


class TestExtractBodyFromPayload:
    def test_html_body(self, connector_fullcov):
        body_data = base64.urlsafe_b64encode(b"<p>Hello</p>").decode()
        payload = {"mimeType": "text/html", "body": {"data": body_data}}
        assert connector_fullcov._extract_body_from_payload(payload) == body_data

    def test_plain_body(self, connector_fullcov):
        body_data = base64.urlsafe_b64encode(b"Hello").decode()
        payload = {"mimeType": "text/plain", "body": {"data": body_data}}
        assert connector_fullcov._extract_body_from_payload(payload) == body_data

    def test_nested_parts_html_preferred(self, connector_fullcov):
        html_data = base64.urlsafe_b64encode(b"<p>Hi</p>").decode()
        text_data = base64.urlsafe_b64encode(b"Hi").decode()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": text_data}},
                {"mimeType": "text/html", "body": {"data": html_data}},
            ]
        }
        assert connector_fullcov._extract_body_from_payload(payload) == html_data

    def test_empty_payload(self, connector_fullcov):
        assert connector_fullcov._extract_body_from_payload({"mimeType": "multipart/mixed", "body": {}}) == ""


class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_returns_true(self, connector_fullcov):
        result = await connector_fullcov.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_gmail_source(self, connector_fullcov):
        connector_fullcov.gmail_data_source = None
        assert await connector_fullcov.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_no_admin_source(self, connector_fullcov):
        connector_fullcov.admin_data_source = None
        assert await connector_fullcov.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_no_clients(self, connector_fullcov):
        connector_fullcov.gmail_client = None
        connector_fullcov.admin_client = None
        assert await connector_fullcov.test_connection_and_access() is False


class TestGetSignedUrl:
    def test_raises_not_implemented(self, connector_fullcov):
        with pytest.raises(NotImplementedError):
            connector_fullcov.get_signed_url(MagicMock())


class TestHandleWebhookNotification:
    def test_raises_not_implemented(self, connector_fullcov):
        with pytest.raises(NotImplementedError):
            connector_fullcov.handle_webhook_notification({})


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector_fullcov):
        with pytest.raises(NotImplementedError):
            await connector_fullcov.get_filter_options("key")


class TestFindPreviousMessageInThread:
    @pytest.mark.asyncio
    async def test_finds_previous(self, connector_fullcov):
        existing = _make_record(record_id="prev-rec", external_id="msg-prev")
        connector_fullcov.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing
        )

        gmail_client = AsyncMock()
        gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-prev", "internalDate": "1000"},
                {"id": "msg-current", "internalDate": "2000"},
            ]
        })
        result = await connector_fullcov._find_previous_message_in_thread(
            "u@t.com", gmail_client, "t1", "msg-current", "2000"
        )
        assert result == "prev-rec"

    @pytest.mark.asyncio
    async def test_single_message_returns_none(self, connector_fullcov):
        gmail_client = AsyncMock()
        gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [{"id": "msg-1", "internalDate": "1000"}]
        })
        result = await connector_fullcov._find_previous_message_in_thread(
            "u@t.com", gmail_client, "t1", "msg-1", "1000"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_checks_batch_records(self, connector_fullcov):
        provider = _make_mock_data_store_provider_fullcov(existing_record=None)
        connector_fullcov.data_store_provider = provider

        gmail_client = AsyncMock()
        gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-prev", "internalDate": "1000"},
                {"id": "msg-current", "internalDate": "2000"},
            ]
        })
        batch_record = MagicMock()
        batch_record.external_record_id = "msg-prev"
        batch_record.id = "batch-rec-id"
        result = await connector_fullcov._find_previous_message_in_thread(
            "u@t.com", gmail_client, "t1", "msg-current", "2000",
            batch_records=[(batch_record, [])]
        )
        assert result == "batch-rec-id"


class TestProcessUsersInBatches:
    @pytest.mark.asyncio
    async def test_filters_active_users(self, connector_fullcov):
        # As _sync_users builds them: is_active comes from the directory's suspended / archived flags.
        active = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                         email="u1@t.com", full_name="U1", is_active=True)
        connector_fullcov.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active])
        connector_fullcov._run_sync_with_yield = AsyncMock()
        await connector_fullcov._process_users_in_batches([active])
        connector_fullcov._run_sync_with_yield.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_suspended_google_account_is_not_read(self, connector_fullcov):
        suspended = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u2",
                            email="u2@t.com", full_name="U2", is_active=False)
        connector_fullcov.data_entities_processor.get_all_active_users = AsyncMock(return_value=[suspended])
        connector_fullcov._run_sync_with_yield = AsyncMock()
        await connector_fullcov._process_users_in_batches([suspended])
        connector_fullcov._run_sync_with_yield.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_active_users(self, connector_fullcov):
        connector_fullcov.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        connector_fullcov._run_sync_with_yield = AsyncMock()
        await connector_fullcov._process_users_in_batches([
            AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                    email="u1@t.com", full_name="U1")
        ])
        connector_fullcov._run_sync_with_yield.assert_not_awaited()


class TestCreateUserGmailClient:
    @pytest.mark.asyncio
    async def test_creates_client(self, connector_fullcov):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGC:
            mock_client = AsyncMock()
            mock_client.get_client.return_value = MagicMock()
            MockGC.build_from_services = AsyncMock(return_value=mock_client)

            with patch(
                "app.connectors.sources.google.gmail.team.connector.GoogleGmailDataSource"
            ) as MockGDS:
                MockGDS.return_value = MagicMock()
                result = await connector_fullcov._create_user_gmail_client("u@t.com")
                assert result is not None

    @pytest.mark.asyncio
    async def test_error_propagates(self, connector_fullcov):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=RuntimeError("fail"))
            with pytest.raises(RuntimeError):
                await connector_fullcov._create_user_gmail_client("u@t.com")


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector_fullcov):
        await connector_fullcov.reindex_records([])

    @pytest.mark.asyncio
    async def test_no_gmail_source(self, connector_fullcov):
        connector_fullcov.gmail_data_source = None
        with pytest.raises(Exception, match="Gmail data source not initialized"):
            await connector_fullcov.reindex_records([_make_record()])

    @pytest.mark.asyncio
    async def test_reindex_updated(self, connector_fullcov):
        record = _make_record()
        connector_fullcov._check_and_fetch_updated_record = AsyncMock(
            return_value=(MagicMock(), [])
        )
        await connector_fullcov.reindex_records([record])
        connector_fullcov.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_non_updated(self, connector_fullcov):
        record = _make_record()
        connector_fullcov._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await connector_fullcov.reindex_records([record])
        connector_fullcov.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_continues_on_error(self, connector_fullcov):
        r1 = _make_record(record_id="r1")
        r2 = _make_record(record_id="r2")
        connector_fullcov._check_and_fetch_updated_record = AsyncMock(
            side_effect=[RuntimeError("fail"), None]
        )
        await connector_fullcov.reindex_records([r1, r2])


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_external_id(self, connector_fullcov):
        record = _make_record(external_id=None)
        result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_with_permission(self, connector_fullcov):
        record = _make_record()
        provider = _make_mock_data_store_provider_fullcov(user_with_perm=None)
        connector_fullcov.data_store_provider = provider
        result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_user_without_email(self, connector_fullcov):
        user_perm = MagicMock()
        user_perm.email = None
        provider = _make_mock_data_store_provider_fullcov(user_with_perm=user_perm)
        connector_fullcov.data_store_provider = provider
        connector_fullcov.data_entities_processor.get_users_with_permission_to_node = AsyncMock(
            return_value=[user_perm]
        )
        record = _make_record()
        result = await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_routes_mail_type(self, connector_fullcov):
        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider_fullcov(user_with_perm=user_perm)
        connector_fullcov.data_store_provider = provider
        connector_fullcov.data_entities_processor.get_users_with_permission_to_node = AsyncMock(
            return_value=[user_perm]
        )

        record = _make_record(record_type=RecordType.MAIL)
        connector_fullcov._get_gmail_client_for_user = AsyncMock(return_value=AsyncMock())
        connector_fullcov._check_and_fetch_updated_mail_record = AsyncMock(return_value=None)
        await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        connector_fullcov._check_and_fetch_updated_mail_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_file_type(self, connector_fullcov):
        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider_fullcov(user_with_perm=user_perm)
        connector_fullcov.data_store_provider = provider
        connector_fullcov.data_entities_processor.get_users_with_permission_to_node = AsyncMock(
            return_value=[user_perm]
        )

        record = _make_record(record_type=RecordType.FILE)
        connector_fullcov._get_gmail_client_for_user = AsyncMock(return_value=AsyncMock())
        connector_fullcov._check_and_fetch_updated_file_record = AsyncMock(return_value=None)
        await connector_fullcov._check_and_fetch_updated_record("org-1", record)
        connector_fullcov._check_and_fetch_updated_file_record.assert_awaited_once()


class TestCheckAndFetchUpdatedMailRecord:
    @pytest.mark.asyncio
    async def test_missing_message_id(self, connector_fullcov):
        record = _make_record(external_id=None)
        result = await connector_fullcov._check_and_fetch_updated_mail_record(
            "org-1", record, "u@t.com", AsyncMock()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_message_not_found(self, connector_fullcov):
        record = _make_record()
        client = AsyncMock()
        resp = MagicMock()
        resp.status = HttpStatusCode.NOT_FOUND.value
        client.users_messages_get = AsyncMock(side_effect=HttpError(resp, b"Not Found"))
        result = await connector_fullcov._check_and_fetch_updated_mail_record(
            "org-1", record, "u@t.com", client
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_updated_message(self, connector_fullcov):
        record = _make_record()
        client = AsyncMock()
        msg = _make_gmail_message_fullcov(label_ids=["SENT"])
        client.users_messages_get = AsyncMock(return_value=msg)
        client.users_threads_get = AsyncMock(return_value={
            "messages": [{"id": "msg-1", "internalDate": "1000"}]
        })

        existing = _make_record(external_record_group_id="u@t.com:INBOX")
        connector_fullcov.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing
        )

        result = await connector_fullcov._check_and_fetch_updated_mail_record(
            "org-1", record, "u@t.com", client
        )
        if result:
            assert result[0].id == record.id


class TestCleanup:
    @pytest.mark.asyncio
    async def test_clears_resources(self, connector_fullcov):
        await connector_fullcov.cleanup()
        assert connector_fullcov.gmail_data_source is None
        assert connector_fullcov.admin_data_source is None
        assert connector_fullcov.gmail_client is None
        assert connector_fullcov.admin_client is None
        assert connector_fullcov.config is None


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_file_id_raises(self, connector_fullcov):
        record = MagicMock(spec=Record)
        record.external_record_id = None
        record.record_type = RecordTypes.MAIL.value
        with pytest.raises(HTTPException) as exc_info:
            await connector_fullcov.stream_record(record)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_mail_record_routes_correctly(self, connector_fullcov):
        record = MagicMock(spec=Record)
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        record.id = "rec-1"
        record.record_name = "Test"

        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider_fullcov(user_with_perm=user_perm)
        connector_fullcov.data_store_provider = provider
        connector_fullcov.data_entities_processor.get_users_with_permission_to_node = AsyncMock(
            return_value=[user_perm]
        )

        connector_fullcov._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector_fullcov._stream_mail_record = AsyncMock(return_value=MagicMock())
        result = await connector_fullcov.stream_record(record)
        connector_fullcov._stream_mail_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file_record_routes_correctly(self, connector_fullcov):
        record = MagicMock(spec=Record)
        record.external_record_id = "msg-1~1"
        record.record_type = "file"
        record.id = "rec-1"
        record.record_name = "file.pdf"
        record.mime_type = "application/pdf"

        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider_fullcov(user_with_perm=user_perm)
        connector_fullcov.data_store_provider = provider
        connector_fullcov.data_entities_processor.get_users_with_permission_to_node = AsyncMock(
            return_value=[user_perm]
        )

        connector_fullcov._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector_fullcov._stream_attachment_record = AsyncMock(return_value=MagicMock())
        result = await connector_fullcov.stream_record(record)
        connector_fullcov._stream_attachment_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_id_lookup(self, connector_fullcov):
        record = MagicMock(spec=Record)
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        record.id = "rec-1"
        record.record_name = "Test"

        provider = _make_mock_data_store_provider_fullcov(user_by_id={"email": "u@t.com"})
        connector_fullcov.data_store_provider = provider
        connector_fullcov._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector_fullcov._stream_mail_record = AsyncMock(return_value=MagicMock())
        await connector_fullcov.stream_record(record, user_id="user-1")
        connector_fullcov._stream_mail_record.assert_awaited_once()


class TestGetExistingRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_found(self, connector_fullcov):
        existing = _make_record()
        connector_fullcov.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing
        )
        result = await connector_fullcov._get_existing_record("msg-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connector_fullcov):
        connector_fullcov.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=None
        )
        result = await connector_fullcov._get_existing_record("msg-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self, connector_fullcov):
        connector_fullcov.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=RuntimeError("db err")
        )
        result = await connector_fullcov._get_existing_record("msg-1")
        assert result is None
