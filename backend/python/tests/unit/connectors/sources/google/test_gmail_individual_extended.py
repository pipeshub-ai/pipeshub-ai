"""Extended tests for GoogleGmailIndividualConnector to reach 90%+ coverage."""

import base64
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import (
    AppUser,
    FileRecord,
    MailRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


def _make_logger():
    log = logging.getLogger("test_gmail_ind_ext")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
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
    message_id="msg-1", thread_id="thread-1", subject="Test Subject",
    from_email="sender@example.com", to_emails="recipient@example.com",
    label_ids=None, internal_date="1704067200000", has_attachments=False,
):
    if label_ids is None:
        label_ids = ["INBOX"]
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_email},
        {"name": "To", "value": to_emails},
        {"name": "Message-ID", "value": f"<{message_id}@gmail.com>"},
    ]
    parts = []
    if has_attachments:
        parts.append({
            "partId": "1", "filename": "att.pdf",
            "mimeType": "application/pdf",
            "body": {"attachmentId": "att-1", "size": 1024},
        })
    return {
        "id": message_id, "threadId": thread_id, "labelIds": label_ids,
        "snippet": "snippet...", "internalDate": internal_date,
        "payload": {"headers": headers, "mimeType": "text/plain",
                    "body": {"data": ""}, "parts": parts},
    }


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

        dep = AsyncMock()
        dep.org_id = "org-123"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.reindex_existing_records = AsyncMock()

        conn = GoogleGmailIndividualConnector(
            logger=_make_logger(),
            data_entities_processor=dep,
            data_store_provider=_make_mock_data_store_provider(),
            config_service=AsyncMock(),
            connector_id="gmail-ind-1",
            scope="personal",
            created_by="test-user-id",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.gmail_data_source = AsyncMock()
        conn.gmail_data_source.client = MagicMock()
        conn.gmail_client = MagicMock()
        conn.config = {"credentials": {"auth": {}}}
        yield conn


class TestParseHeaders:
    def test_extracts_relevant(self, connector):
        headers = [
            {"name": "Subject", "value": "Hi"},
            {"name": "From", "value": "alice@ex.com"},
            {"name": "To", "value": "bob@ex.com"},
            {"name": "X-Custom", "value": "ignored"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert result["subject"] == "Hi"
        assert "x-custom" not in result

    def test_empty(self, connector):
        assert connector._parse_gmail_headers([]) == {}


class TestParseEmailList:
    def test_multiple(self, connector):
        assert len(connector._parse_email_list("a@e.com, b@e.com")) == 2

    def test_empty(self, connector):
        assert connector._parse_email_list("") == []


class TestExtractEmail:
    def test_plain(self, connector):
        assert connector._extract_email_from_header("a@b.com") == "a@b.com"

    def test_with_name(self, connector):
        assert connector._extract_email_from_header("Alice <a@b.com>") == "a@b.com"

    def test_empty(self, connector):
        assert connector._extract_email_from_header("") == ""

    def test_none(self, connector):
        assert connector._extract_email_from_header(None) == ""


class TestPassDateFilter:
    def test_no_filter(self, connector):
        assert connector._pass_date_filter({"internalDate": "1704067200000"}) is True

    def test_before_start(self, connector):
        mock_f = MagicMock()
        mock_f.get_datetime_start.return_value = 2000000000000
        mock_f.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_f
        assert connector._pass_date_filter({"internalDate": "1000000000000"}) is False


class TestProcessMessage:
    async def test_new_message(self, connector):
        msg = _make_gmail_message()
        result = await connector._process_gmail_message("u@e.com", msg, "t1", None)
        assert result is not None
        assert result.is_new is True

    async def test_no_id(self, connector):
        result = await connector._process_gmail_message("u@e.com", {"payload": {}}, "t1", None)
        assert result is None

    async def test_sent_label(self, connector):
        msg = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message("u@e.com", msg, "t1", None)
        assert "SENT" in result.record.external_record_group_id

    async def test_sender_gets_owner_permission(self, connector):
        msg = _make_gmail_message(from_email="user@e.com")
        result = await connector._process_gmail_message("user@e.com", msg, "t1", None)
        assert any(p.type == PermissionType.OWNER for p in result.new_permissions)


class TestExtractAttachments:
    def test_regular(self, connector):
        msg = _make_gmail_message(has_attachments=True)
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) >= 1

    def test_no_parts(self, connector):
        msg = {"id": "m1", "payload": {"headers": [], "parts": []}}
        assert connector._extract_attachment_infos(msg) == []

    def test_drive_file_in_body(self, connector):
        html = 'https://drive.google.com/file/d/DRIVE123/view?usp=drive_web'
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {
            "id": "m1",
            "payload": {"headers": [], "mimeType": "text/html",
                        "body": {"data": encoded}, "parts": []},
        }
        infos = connector._extract_attachment_infos(msg)
        assert any(a.get("driveFileId") == "DRIVE123" for a in infos)


class TestProcessAttachment:
    async def test_regular(self, connector):
        info = {
            "attachmentId": "a1", "driveFileId": None, "stableAttachmentId": "m~1",
            "partId": "1", "filename": "f.pdf", "mimeType": "application/pdf",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment("u@e.com", "m", info, [])
        assert result is not None

    async def test_no_stable_id(self, connector):
        info = {"attachmentId": "a", "stableAttachmentId": None, "isDriveFile": False}
        result = await connector._process_gmail_attachment("u@e.com", "m", info, [])
        assert result is None


class TestExtractBody:
    def test_html(self, connector):
        payload = {"mimeType": "text/html", "body": {"data": "SEVMTE8="}}
        assert connector._extract_body_from_payload(payload) == "SEVMTE8="

    def test_parts_html_first(self, connector):
        payload = {
            "mimeType": "multipart/alternative", "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "p"}},
                {"mimeType": "text/html", "body": {"data": "h"}},
            ],
        }
        assert connector._extract_body_from_payload(payload) == "h"

    def test_empty(self, connector):
        assert connector._extract_body_from_payload({"mimeType": "text/html", "body": {}}) == ""


class TestMergeHistoryChanges:
    def test_deduplicates(self, connector):
        i = {"history": [{"id": "1"}, {"id": "2"}]}
        s = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector._merge_history_changes(i, s)
        assert len(result["history"]) == 3


class TestNotImplementedMethods:
    def test_get_signed_url_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())

    def test_handle_webhook_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})

    async def test_get_filter_options_raises(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


class TestCreateAppUser:
    async def test_creates_user(self, connector):
        connector.gmail_data_source.users_get_profile = AsyncMock(return_value={
            "emailAddress": "u@e.com", "messagesTotal": 100,
        })
        await connector._create_app_user({"emailAddress": "u@e.com"})
        connector.data_entities_processor.on_new_app_users.assert_called()


class TestCreatePersonalRecordGroup:
    async def test_creates_groups(self, connector):
        await connector._create_personal_record_group("u@e.com")
        assert connector.data_entities_processor.on_new_record_groups.call_count >= 1


class TestRunSync:
    async def test_sync_flow(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        connector.gmail_data_source.users_get_profile = AsyncMock(
            return_value={"emailAddress": "user@test.com"}
        )
        connector._create_app_user = AsyncMock()
        connector._create_personal_record_group = AsyncMock()
        connector._sync_user_mailbox = AsyncMock()
        await connector.run_sync()

    async def test_sync_error(self, connector):
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connector.run_sync()


class TestRunIncrementalSync:
    async def test_delegates(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_called_once()


class TestCleanup:
    async def test_cleanup(self, connector):
        await connector.cleanup()
        assert connector.gmail_data_source is None


class TestReindexRecords:
    async def test_empty(self, connector):
        await connector.reindex_records([])

    async def test_no_datasource(self, connector):
        connector.gmail_data_source = None
        with pytest.raises(Exception):
            await connector.reindex_records([MagicMock()])


class TestCheckUpdated:
    async def test_no_external_id(self, connector):
        record = MagicMock()
        record.external_record_id = None
        record.id = "r1"
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    async def test_no_user_permission(self, connector):
        record = MagicMock()
        record.external_record_id = "ext-1"
        record.id = "r1"
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


class TestCreateConnector:
    @patch("app.connectors.sources.google.gmail.individual.connector.DataSourceEntitiesProcessor")
    @patch("app.connectors.sources.google.gmail.individual.connector.SyncPoint")
    @patch("app.connectors.sources.google.gmail.individual.connector.GoogleClient")
    async def test_create(self, mock_gc, mock_sp, mock_dep_cls):
        from app.connectors.sources.google.gmail.individual.connector import (
            GoogleGmailIndividualConnector,
        )
        mock_dep = AsyncMock()
        mock_dep.org_id = "org-1"
        mock_dep.initialize = AsyncMock()
        mock_dep_cls.return_value = mock_dep
        mock_sp.return_value = AsyncMock()
        result = await GoogleGmailIndividualConnector.create_connector(
            logger=_make_logger(),
            data_store_provider=_make_mock_data_store_provider(),
            config_service=AsyncMock(),
            connector_id="test-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
        assert result is not None
