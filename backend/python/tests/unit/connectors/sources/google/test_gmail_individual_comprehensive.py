"""Comprehensive coverage tests for Gmail Individual connector - additional untested methods."""

import base64
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    SyncFilterKey,
)
from app.connectors.sources.google.common.gmail_received_date_query import (
    build_gmail_received_date_threads_query,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    MailRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx.create_record_group_relation = AsyncMock()
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


def _make_connector():
    with patch(
        "app.connectors.sources.google.gmail.individual.connector.GmailIndividualApp"
    ):
        from app.connectors.sources.google.gmail.individual.connector import (
            GoogleGmailIndividualConnector,
        )

        logger = logging.getLogger("test.gmail.comp")
        data_entities_processor = MagicMock()
        data_entities_processor.org_id = "org-gmail-comp"
        data_entities_processor.on_new_app_users = AsyncMock()
        data_entities_processor.on_new_record_groups = AsyncMock()
        data_entities_processor.on_new_records = AsyncMock()
        data_entities_processor.on_record_deleted = AsyncMock()
        data_entities_processor.reindex_existing_records = AsyncMock()

        data_store_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()

        connector = GoogleGmailIndividualConnector(
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id="gmail-comp-1",
            scope="personal",
            created_by="test-user-id",
        )
        connector.connector_name = Connectors.GOOGLE_MAIL
        return connector


def _make_gmail_message(
    msg_id="msg-1",
    thread_id="thread-1",
    label_ids=None,
    subject="Test Subject",
    from_email="sender@test.com",
    to_email="recipient@test.com",
    internal_date="1700000000000",
    has_attachment=False,
    body_data=None,
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
    if body_data:
        parts.append({
            "partId": "0",
            "mimeType": "text/html",
            "body": {"data": body_data, "size": len(body_data)},
        })
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


# ===========================================================================
# _extract_body_from_payload - extended
# ===========================================================================
class TestExtractBodyFromPayloadExtended:
    def test_no_parts_no_body(self):
        connector = _make_connector()
        assert connector._extract_body_from_payload({}) == ""

    def test_nested_multipart(self):
        connector = _make_connector()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": base64.urlsafe_b64encode(b"Plain text").decode()},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": base64.urlsafe_b64encode(b"<b>HTML</b>").decode()},
                        },
                    ],
                },
            ],
        }
        result = connector._extract_body_from_payload(payload)
        # Should prefer HTML
        assert result == base64.urlsafe_b64encode(b"<b>HTML</b>").decode()

    def test_plain_text_only(self):
        connector = _make_connector()
        encoded = base64.urlsafe_b64encode(b"Just plain text").decode()
        payload = {
            "mimeType": "text/plain",
            "body": {"data": encoded},
        }
        result = connector._extract_body_from_payload(payload)
        assert result == encoded


# ===========================================================================
# _merge_history_changes - extended
# ===========================================================================
class TestMergeHistoryChangesExtended:
    def test_empty_inputs(self):
        connector = _make_connector()
        result = connector._merge_history_changes({"history": []}, {"history": []})
        assert result == {"history": []}

    def test_dedup_by_id(self):
        connector = _make_connector()
        inbox = {"history": [{"id": "1", "type": "inbox"}, {"id": "2", "type": "inbox"}]}
        sent = {"history": [{"id": "2", "type": "sent"}, {"id": "3", "type": "sent"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3

    def test_sorted_by_id(self):
        connector = _make_connector()
        inbox = {"history": [{"id": "3"}, {"id": "1"}]}
        sent = {"history": [{"id": "2"}]}
        result = connector._merge_history_changes(inbox, sent)
        ids = [h["id"] for h in result["history"]]
        assert ids == ["1", "2", "3"]

    def test_missing_history_key(self):
        connector = _make_connector()
        result = connector._merge_history_changes({}, {})
        assert result == {"history": []}

    def test_single_source(self):
        connector = _make_connector()
        inbox = {"history": [{"id": "10"}, {"id": "20"}]}
        result = connector._merge_history_changes(inbox, {"history": []})
        assert len(result["history"]) == 2


# ===========================================================================
# _parse_gmail_headers - extended
# ===========================================================================
class TestParseGmailHeadersExtended:
    def test_case_insensitive(self):
        connector = _make_connector()
        headers = [
            {"name": "SUBJECT", "value": "Test"},
            {"name": "FROM", "value": "sender@test.com"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert result.get("subject") == "Test"
        assert result.get("from") == "sender@test.com"

    def test_date_header(self):
        connector = _make_connector()
        headers = [{"name": "Date", "value": "Mon, 1 Jan 2024 00:00:00 +0000"}]
        result = connector._parse_gmail_headers(headers)
        assert "date" in result

    def test_message_id_header(self):
        connector = _make_connector()
        headers = [{"name": "Message-ID", "value": "<abc@mail.com>"}]
        result = connector._parse_gmail_headers(headers)
        assert result.get("message-id") == "<abc@mail.com>"

    def test_unknown_header_ignored(self):
        connector = _make_connector()
        headers = [{"name": "X-Custom", "value": "custom-value"}]
        result = connector._parse_gmail_headers(headers)
        assert "x-custom" not in result


# ===========================================================================
# _parse_email_list - extended
# ===========================================================================
class TestParseEmailListExtended:
    def test_whitespace_handling(self):
        connector = _make_connector()
        result = connector._parse_email_list("  a@test.com , b@test.com  ")
        assert result == ["a@test.com", "b@test.com"]

    def test_single_email(self):
        connector = _make_connector()
        result = connector._parse_email_list("test@example.com")
        assert result == ["test@example.com"]


# ===========================================================================
# _extract_email_from_header - extended
# ===========================================================================
class TestExtractEmailFromHeaderExtended:
    def test_complex_name(self):
        connector = _make_connector()
        result = connector._extract_email_from_header('"Smith, John" <john@test.com>')
        assert result == "john@test.com"

    def test_angle_brackets_only(self):
        connector = _make_connector()
        result = connector._extract_email_from_header("<user@test.com>")
        assert result == "user@test.com"

    def test_no_angle_brackets(self):
        connector = _make_connector()
        result = connector._extract_email_from_header("plain@test.com")
        assert result == "plain@test.com"


# ===========================================================================
# build_gmail_received_date_threads_query - extended
# ===========================================================================
class TestPassDateFilterExtended:
    def test_is_between_builds_both_clauses(self):
        connector = _make_connector()
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": 1000000000000, "end": 2000000000000},
            "type": "datetime",
            "operator": "is_between",
        })
        connector.sync_filters = FilterCollection(filters=[date_filter])
        q = build_gmail_received_date_threads_query(
            connector.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        )
        assert q == "after:1000000000 before:2000000000"

    def test_is_after_builds_after_clause(self):
        connector = _make_connector()
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": 1500000000000, "end": None},
            "type": "datetime",
            "operator": "is_after",
        })
        connector.sync_filters = FilterCollection(filters=[date_filter])
        q = build_gmail_received_date_threads_query(
            connector.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        )
        assert q == "after:1500000000"

    def test_is_before_builds_before_clause(self):
        connector = _make_connector()
        date_filter = Filter.model_validate({
            "key": SyncFilterKey.RECEIVED_DATE.value,
            "value": {"start": None, "end": 1500000000000},
            "type": "datetime",
            "operator": "is_before",
        })
        connector.sync_filters = FilterCollection(filters=[date_filter])
        q = build_gmail_received_date_threads_query(
            connector.sync_filters.get(SyncFilterKey.RECEIVED_DATE)
        )
        assert q == "before:1500000000"


# ===========================================================================
# _extract_attachment_infos - extended
# ===========================================================================
class TestExtractAttachmentInfosExtended:
    def test_drive_file_in_body_content(self):
        connector = _make_connector()
        body_html = '<a href="https://drive.google.com/file/d/FILE_ID_123/view?usp=drive_web">File</a>'
        encoded = base64.urlsafe_b64encode(body_html.encode()).decode()
        message = {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/html",
                "body": {"data": encoded},
                "parts": [],
            },
        }
        result = connector._extract_attachment_infos(message)
        # Should extract Drive file ID
        drive_files = [a for a in result if a.get("isDriveFile")]
        assert len(drive_files) == 1
        assert drive_files[0]["driveFileId"] == "FILE_ID_123"

    def test_empty_message(self):
        connector = _make_connector()
        message = {"id": "msg-2", "payload": {"parts": []}}
        result = connector._extract_attachment_infos(message)
        assert result == []

    def test_nested_parts_attachment(self):
        connector = _make_connector()
        message = {
            "id": "msg-3",
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "multipart/alternative",
                        "parts": [
                            {
                                "partId": "0.0",
                                "mimeType": "text/plain",
                                "body": {"data": "", "size": 0},
                            },
                        ],
                    },
                    {
                        "partId": "1",
                        "filename": "nested.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att-nested", "size": 512},
                    },
                ],
            },
        }
        result = connector._extract_attachment_infos(message)
        assert len(result) == 1
        assert result[0]["filename"] == "nested.pdf"
        assert result[0]["stableAttachmentId"] == "msg-3~1"


# ===========================================================================
# _process_gmail_message - extended
# ===========================================================================
class TestProcessGmailMessageExtended:
    async def test_message_in_others_label(self):
        connector = _make_connector()
        msg = _make_gmail_message(label_ids=["CATEGORY_PROMOTIONS"])
        result = await connector._process_gmail_message("user@test.com", msg, "thread-1", None)
        assert result is not None
        assert ":OTHERS" in result.record.external_record_group_id

    async def test_message_date_filtered_out(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 2000000000000  # future
        mock_filter.get_datetime_end.return_value = None
        mock_filters = MagicMock()
        mock_filters.get.return_value = mock_filter
        connector.sync_filters = mock_filters

        msg = _make_gmail_message(internal_date="1000000000000")
        result = await connector._process_gmail_message("user@test.com", msg, "thread-1", None)
        assert result is None

    async def test_sent_message_group_id(self):
        connector = _make_connector()
        msg = _make_gmail_message(label_ids=["SENT", "INBOX"])
        result = await connector._process_gmail_message("user@test.com", msg, "thread-1", None)
        assert result is not None
        # SENT takes priority over INBOX
        assert ":SENT" in result.record.external_record_group_id

    async def test_invalid_internal_date(self):
        connector = _make_connector()
        msg = _make_gmail_message(internal_date="not-a-number")
        result = await connector._process_gmail_message("user@test.com", msg, "thread-1", None)
        assert result is not None
        # source_created_at should fallback to current time
        assert result.record.source_created_at > 0

    async def test_no_internal_date(self):
        connector = _make_connector()
        msg = _make_gmail_message(internal_date=None)
        result = await connector._process_gmail_message("user@test.com", msg, "thread-1", None)
        assert result is not None
        assert result.record.source_created_at > 0


# ===========================================================================
# _process_gmail_attachment - extended
# ===========================================================================
class TestProcessGmailAttachmentExtended:
    async def test_no_stable_id_returns_none(self):
        connector = _make_connector()
        attachment_info = {"filename": "doc.pdf", "mimeType": "application/pdf", "size": 100}
        result = await connector._process_gmail_attachment(
            user_email="user@test.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=[],
            external_record_group_id="user@test.com:OTHERS",
        )
        assert result is None

    async def test_no_attachment_id_no_drive_id_returns_none(self):
        connector = _make_connector()
        attachment_info = {
            "stableAttachmentId": "msg-1~1",
            "attachmentId": None,
            "driveFileId": None,
            "filename": "doc.pdf",
            "mimeType": "application/pdf",
            "size": 100,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@test.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=[],
            external_record_group_id="user@test.com:OTHERS",
        )
        assert result is None

    async def test_regular_attachment(self):
        connector = _make_connector()
        attachment_info = {
            "stableAttachmentId": "msg-1~1",
            "attachmentId": "att-1",
            "filename": "report.pdf",
            "mimeType": "application/pdf",
            "size": 2048,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="user@test.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=[
                Permission(email="user@test.com", type=PermissionType.OWNER, entity_type=EntityType.USER)
            ],
            external_record_group_id="user@test.com:OTHERS",
        )
        assert result is not None
        assert result.record.record_name == "report.pdf"
        assert result.record.parent_external_record_id == "msg-1"


# ===========================================================================
# _create_app_user
# ===========================================================================
class TestCreateAppUserExtended:
    async def test_creates_user(self):
        connector = _make_connector()
        user_profile = {"emailAddress": "user@test.com"}
        await connector._create_app_user(user_profile)
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    async def test_no_email_raises(self):
        connector = _make_connector()
        with pytest.raises(Exception):
            await connector._create_app_user({})


# ===========================================================================
# _create_personal_record_group
# ===========================================================================
class TestCreatePersonalRecordGroupExtended:
    async def test_creates_groups(self):
        connector = _make_connector()
        await connector._create_personal_record_group("user@test.com")
        # Should create INBOX, SENT, OTHERS groups (one call per group)
        assert connector.data_entities_processor.on_new_record_groups.await_count == 3

    async def test_no_email_raises(self):
        connector = _make_connector()
        with pytest.raises(Exception):
            await connector._create_personal_record_group("")


# ===========================================================================
# cleanup
# ===========================================================================
class TestCleanup:
    async def test_cleanup_with_client(self):
        connector = _make_connector()
        connector.gmail_client = MagicMock()
        connector.gmail_data_source = MagicMock()
        await connector.cleanup()
        # Should not raise

    async def test_cleanup_no_client(self):
        connector = _make_connector()
        connector.gmail_client = None
        connector.gmail_data_source = None
        await connector.cleanup()
        # Should not raise


# ===========================================================================
# handle_webhook_notification
# ===========================================================================
class TestHandleWebhookNotification:
    def test_raises_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


# ===========================================================================
# get_filter_options
# ===========================================================================
class TestGetFilterOptions:
    async def test_raises_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("some_key")
