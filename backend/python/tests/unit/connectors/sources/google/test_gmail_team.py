"""Tests for GoogleGmailTeamConnector (app/connectors/sources/google/gmail/team/connector.py)."""

import base64
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import AppUser, MailRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger():
    log = logging.getLogger("test_gmail_team")
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
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.admin_client = MagicMock()
        conn.gmail_client = MagicMock()
        conn.admin_data_source = AsyncMock()
        conn.gmail_data_source = AsyncMock()
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
        assert result.new_permissions[0].type == PermissionType.OWNER

    async def test_non_sender_gets_read(self, connector):
        message = _make_gmail_message(from_email="other@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.READ

    async def test_case_insensitive_sender_comparison(self, connector):
        message = _make_gmail_message(from_email="User@Example.COM")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.OWNER

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

    async def test_weburl_includes_user_email(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert "authuser=user@example.com" in result.record.weburl

    async def test_existing_record_detected(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "user@example.com:INBOX"
        connector.data_store_provider = _make_mock_data_store_provider(existing)

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
        connector.data_store_provider = _make_mock_data_store_provider(existing)

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

    async def test_date_filter_skips_message(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1704067200001
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        message = _make_gmail_message(internal_date="1704067200000")
        result = await connector._process_gmail_message(
            user_email="user@example.com", message=message,
            thread_id="thread-1", previous_message_id=None,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Date filter (team)
# ---------------------------------------------------------------------------

class TestTeamGmailDateFilter:
    def test_no_filter_passes(self, connector):
        message = _make_gmail_message()
        assert connector._pass_date_filter(message) is True

    def test_rejects_old_message(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1704067200001
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        message = _make_gmail_message(internal_date="1704067200000")
        assert connector._pass_date_filter(message) is False

    def test_rejects_future_message(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = None
        mock_filter.get_datetime_end.return_value = 1704067199999
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        message = _make_gmail_message(internal_date="1704067200000")
        assert connector._pass_date_filter(message) is False

    def test_within_range_passes(self, connector):
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
        assert connector._pass_date_filter(message) is True


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
        infos = connector._extract_attachment_infos(message)
        drive_infos = [i for i in infos if i["isDriveFile"]]
        assert len(drive_infos) == 1
        assert drive_infos[0]["driveFileId"] == "drive-file-1"

    def test_drive_file_ids_from_body_content(self, connector):
        html = '<a href="https://drive.google.com/file/d/DRIVE_ID_1/view?usp=drive_web">Link</a>'
        message = _make_gmail_message(body_html=html)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "DRIVE_ID_1"

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
        )
        assert result.new_permissions == parent_perms

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
            "user@example.com", "msg-1", attachment_infos, parent_perms
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
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        result = await connector._get_existing_record("ext-id-1")
        assert result is not None

    async def test_returns_none_when_not_found(self, connector):
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)
        result = await connector._get_existing_record("nonexistent")
        assert result is None

    async def test_returns_none_on_error(self, connector):
        provider = MagicMock()
        @asynccontextmanager
        async def _failing_tx():
            raise Exception("DB error")
            yield
        provider.transaction = _failing_tx
        connector.data_store_provider = provider
        result = await connector._get_existing_record("ext-id-1")
        assert result is None


# ---------------------------------------------------------------------------
# _merge_history_changes
# ---------------------------------------------------------------------------

class TestMergeHistoryChanges:
    def test_merges_and_deduplicates(self, connector):
        inbox = {"history": [{"id": "1"}, {"id": "2"}]}
        sent = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3
        ids = [h["id"] for h in result["history"]]
        assert ids == ["1", "2", "3"]

    def test_empty_changes(self, connector):
        result = connector._merge_history_changes({"history": []}, {"history": []})
        assert result["history"] == []


# ---------------------------------------------------------------------------
# _run_full_sync
# ---------------------------------------------------------------------------

class TestTeamFullSync:
    async def test_full_sync_processes_threads(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "thread-1"}],
        })
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        connector.data_entities_processor.on_new_records.assert_called()
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    async def test_full_sync_handles_empty_threads(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={"threads": []})
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_full_sync_paginates_threads(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(side_effect=[
            {"threads": [{"id": "thread-1"}], "nextPageToken": "page2"},
            {"threads": [{"id": "thread-2"}]},
        ])
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        assert user_gmail_client.users_threads_list.call_count == 2

    async def test_full_sync_handles_profile_error(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(side_effect=Exception("Profile error"))
        user_gmail_client.users_threads_list = AsyncMock(return_value={"threads": []})
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")


# ---------------------------------------------------------------------------
# _run_sync_with_yield
# ---------------------------------------------------------------------------

class TestTeamRunSyncWithYield:
    async def test_routes_to_full_sync_when_no_history_id(self, connector):
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_run_full_sync", new_callable=AsyncMock) as mock_full:
            mock_create.return_value = AsyncMock()
            await connector._run_sync_with_yield("user@example.com")
            mock_full.assert_called_once()

    async def test_routes_to_incremental_sync_when_history_id_exists(self, connector):
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "hist-123"}
        )
        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_run_sync_with_history_id", new_callable=AsyncMock) as mock_inc:
            mock_create.return_value = AsyncMock()
            await connector._run_sync_with_yield("user@example.com")
            mock_inc.assert_called_once()

    async def test_falls_back_to_full_sync_on_404(self, connector):
        from googleapiclient.errors import HttpError
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "expired-hist"}
        )
        mock_resp = MagicMock()
        mock_resp.status = 404
        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_run_sync_with_history_id", new_callable=AsyncMock,
                          side_effect=HttpError(mock_resp, b"not found")) as mock_inc, \
             patch.object(connector, "_run_full_sync", new_callable=AsyncMock) as mock_full:
            mock_create.return_value = AsyncMock()
            await connector._run_sync_with_yield("user@example.com")
            mock_full.assert_called_once()


# ---------------------------------------------------------------------------
# Deep sync: _run_sync_with_history_id (incremental)
# ---------------------------------------------------------------------------

class TestTeamRunSyncWithHistoryId:
    async def test_incremental_sync_processes_changes(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-200"})
        user_gmail_client.users_history_list = AsyncMock(return_value={
            "history": [
                {"id": "100", "messagesAdded": [{"message": {"id": "new-msg-1"}}]},
            ],
        })
        user_gmail_client.users_messages_get = AsyncMock(return_value=_make_gmail_message(message_id="new-msg-1"))
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            await connector._run_sync_with_history_id(
                "user@example.com", user_gmail_client, "hist-100", "test-key"
            )
        connector.data_entities_processor.on_new_records.assert_called()
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    async def test_incremental_sync_handles_empty_history(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-200"})
        user_gmail_client.users_history_list = AsyncMock(return_value={"history": []})
        await connector._run_sync_with_history_id(
            "user@example.com", user_gmail_client, "hist-100", "test-key"
        )
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_incremental_sync_handles_deleted_messages(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-200"})
        user_gmail_client.users_history_list = AsyncMock(return_value={
            "history": [
                {"id": "100", "messagesDeleted": [{"message": {"id": "del-msg-1"}}]},
            ],
        })
        existing = MagicMock()
        existing.id = "rec-del-1"
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=existing), \
             patch.object(connector, "_delete_message_and_attachments", new_callable=AsyncMock):
            await connector._run_sync_with_history_id(
                "user@example.com", user_gmail_client, "hist-100", "test-key"
            )
            connector._delete_message_and_attachments.assert_called_once()

    async def test_incremental_sync_processes_label_changes(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-200"})
        user_gmail_client.users_history_list = AsyncMock(return_value={
            "history": [
                {"id": "100", "labelsAdded": [
                    {"message": {"id": "label-msg-1"}, "labelIds": ["INBOX"]}
                ]},
            ],
        })
        user_gmail_client.users_messages_get = AsyncMock(
            return_value=_make_gmail_message(message_id="label-msg-1")
        )
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            await connector._run_sync_with_history_id(
                "user@example.com", user_gmail_client, "hist-100", "test-key"
            )
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_incremental_trash_label_triggers_delete(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-200"})
        user_gmail_client.users_history_list = AsyncMock(return_value={
            "history": [
                {"id": "100", "labelsAdded": [
                    {"message": {"id": "trash-msg-1"}, "labelIds": ["TRASH"]}
                ]},
            ],
        })
        existing = MagicMock()
        existing.id = "rec-trash-1"
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=existing), \
             patch.object(connector, "_delete_message_and_attachments", new_callable=AsyncMock):
            await connector._run_sync_with_history_id(
                "user@example.com", user_gmail_client, "hist-100", "test-key"
            )
            connector._delete_message_and_attachments.assert_called_once()


# ---------------------------------------------------------------------------
# Deep sync: _fetch_history_changes
# ---------------------------------------------------------------------------

class TestTeamFetchHistoryChanges:
    async def test_single_page(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_history_list = AsyncMock(return_value={
            "history": [{"id": "1"}, {"id": "2"}],
        })
        result = await connector._fetch_history_changes(
            user_gmail_client, "user@example.com", "hist-1", "INBOX"
        )
        assert len(result["history"]) == 2

    async def test_paginates(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_history_list = AsyncMock(side_effect=[
            {"history": [{"id": "1"}], "nextPageToken": "page2"},
            {"history": [{"id": "2"}]},
        ])
        result = await connector._fetch_history_changes(
            user_gmail_client, "user@example.com", "hist-1", "INBOX"
        )
        assert len(result["history"]) == 2
        assert user_gmail_client.users_history_list.call_count == 2

    async def test_reraises_http_error(self, connector):
        from googleapiclient.errors import HttpError
        user_gmail_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        user_gmail_client.users_history_list = AsyncMock(
            side_effect=HttpError(mock_resp, b"not found")
        )
        with pytest.raises(HttpError):
            await connector._fetch_history_changes(
                user_gmail_client, "user@example.com", "hist-1", "INBOX"
            )


# ---------------------------------------------------------------------------
# Deep sync: _process_history_changes
# ---------------------------------------------------------------------------

class TestTeamProcessHistoryChanges:
    async def test_processes_message_additions(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_messages_get = AsyncMock(
            return_value=_make_gmail_message(message_id="hist-add-1")
        )
        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "hist-add-1"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "user@example.com", user_gmail_client, history_entry, batch
            )
        assert count >= 1
        assert len(batch) >= 1

    async def test_skips_existing_messages(self, connector):
        user_gmail_client = AsyncMock()
        existing = MagicMock()
        existing.id = "existing-rec"
        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "existing-msg"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=existing):
            count = await connector._process_history_changes(
                "user@example.com", user_gmail_client, history_entry, batch
            )
        assert count == 0
        assert len(batch) == 0

    async def test_handles_message_deletions(self, connector):
        user_gmail_client = AsyncMock()
        existing = MagicMock()
        existing.id = "del-rec"
        history_entry = {
            "id": "100",
            "messagesDeleted": [{"message": {"id": "del-msg"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=existing), \
             patch.object(connector, "_delete_message_and_attachments", new_callable=AsyncMock):
            count = await connector._process_history_changes(
                "user@example.com", user_gmail_client, history_entry, batch
            )
        assert count >= 1

    async def test_deduplicates_messages(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_messages_get = AsyncMock(
            return_value=_make_gmail_message(message_id="dup-msg")
        )
        history_entry = {
            "id": "100",
            "messagesAdded": [
                {"message": {"id": "dup-msg"}},
                {"message": {"id": "dup-msg"}},
            ],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "user@example.com", user_gmail_client, history_entry, batch
            )
        assert count == 1

    async def test_processes_attachments_in_history(self, connector):
        user_gmail_client = AsyncMock()
        msg = _make_gmail_message(message_id="att-msg", has_attachments=True)
        user_gmail_client.users_messages_get = AsyncMock(return_value=msg)
        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "att-msg"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "user@example.com", user_gmail_client, history_entry, batch
            )
        # Should have the message + the attachment
        assert count >= 2


# ---------------------------------------------------------------------------
# Deep sync: full sync thread pagination
# ---------------------------------------------------------------------------

class TestTeamFullSyncDeep:
    async def test_full_sync_processes_attachments(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        msg_with_att = _make_gmail_message(has_attachments=True)
        user_gmail_client.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "thread-1"}],
        })
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [msg_with_att],
        })
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_full_sync_handles_thread_error(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "bad-thread"}, {"id": "good-thread"}],
        })
        user_gmail_client.users_threads_get = AsyncMock(side_effect=[
            Exception("thread error"),
            {"messages": [_make_gmail_message()]},
        ])
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_full_sync_multiple_messages_in_thread(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "thread-multi"}],
        })
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                _make_gmail_message(message_id="msg-1", thread_id="thread-multi"),
                _make_gmail_message(message_id="msg-2", thread_id="thread-multi"),
                _make_gmail_message(message_id="msg-3", thread_id="thread-multi"),
            ],
        })
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_full_sync_saves_page_token(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(side_effect=[
            {"threads": [{"id": "t1"}], "nextPageToken": "page2"},
            {"threads": [{"id": "t2"}]},
        ])
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        assert user_gmail_client.users_threads_list.call_count == 2
        # Verify sync point was updated with pageToken
        calls = connector.gmail_delta_sync_point.update_sync_point.call_args_list
        assert len(calls) >= 2  # intermediate + final

    async def test_full_sync_skips_threadless_entries(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": None}, {"id": "thread-valid"}],
        })
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        # Only the valid thread should be fetched
        assert user_gmail_client.users_threads_get.call_count == 1

    async def test_full_sync_skips_empty_message_threads(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "empty-thread"}],
        })
        user_gmail_client.users_threads_get = AsyncMock(return_value={"messages": []})
        await connector._run_full_sync("user@example.com", user_gmail_client, "test-key")
        connector.data_entities_processor.on_new_records.assert_not_called()


# ---------------------------------------------------------------------------
# Deep sync: _run_sync_with_yield error handling
# ---------------------------------------------------------------------------

class TestTeamRunSyncWithYieldErrors:
    async def test_reraises_non_404_http_error(self, connector):
        from googleapiclient.errors import HttpError
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "hist-123"}
        )
        mock_resp = MagicMock()
        mock_resp.status = 403
        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock), \
             patch.object(connector, "_run_sync_with_history_id", new_callable=AsyncMock,
                          side_effect=HttpError(mock_resp, b"forbidden")):
            with pytest.raises(HttpError):
                await connector._run_sync_with_yield("user@example.com")

    async def test_propagates_general_exception(self, connector):
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock,
                          side_effect=Exception("client error")):
            with pytest.raises(Exception, match="client error"):
                await connector._run_sync_with_yield("user@example.com")
