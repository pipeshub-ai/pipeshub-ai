"""Comprehensive coverage tests for Gmail Individual connector."""

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def _make_gmail_message(
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

class TestParseGmailHeaders:
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

class TestParseEmailList:
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

class TestExtractEmailFromHeader:
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

class TestExtractBodyFromPayload:
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

class TestProcessGmailMessage:
    @pytest.mark.asyncio
    async def test_new_message_inbox(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        message = _make_gmail_message(
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

        message = _make_gmail_message(
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

        message = _make_gmail_message(
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

        message = _make_gmail_message(
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

        message = _make_gmail_message(
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

        message = _make_gmail_message(
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

        message = _make_gmail_message(msg_id="msg-existing", label_ids=["INBOX"])
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

class TestExtractAttachmentInfos:
    def test_regular_attachment(self):
        connector = _make_connector()
        message = _make_gmail_message(msg_id="msg-att", has_attachment=True)
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

class TestProcessGmailAttachment:
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
            _make_gmail_message(msg_id="m1"),
            _make_gmail_message(msg_id="m2"),
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
            _make_gmail_message(msg_id="m-good"),
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

class TestGetSignedUrl:
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
