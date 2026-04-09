"""Comprehensive tests for GoogleGmailTeamConnector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import FilterCollection, IndexingFilterKey, SyncFilterKey
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    MailRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_logger():
    log = logging.getLogger("test_gmail_team_comp")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_records_by_parent = AsyncMock(return_value=[])
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


def _make_google_user(email="user@example.com", user_id="guser-1", full_name="Test User"):
    return {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name, "givenName": "Test", "familyName": "User"},
        "suspended": False,
        "creationTime": "2024-01-01T00:00:00.000Z",
        "organizations": [{"title": "Engineer"}],
    }


def _make_gmail_message(
    message_id="msg-1", thread_id="thread-1", label_ids=None,
    subject="Test Subject", from_email="sender@test.com",
    to_emails="recipient@test.com", internal_date="1700000000000",
    has_attachment=False
):
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_email},
        {"name": "To", "value": to_emails},
    ]
    parts = []
    if has_attachment:
        parts.append({
            "partId": "1",
            "filename": "test.pdf",
            "mimeType": "application/pdf",
            "body": {"attachmentId": "att-1", "size": 2048},
        })

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": label_ids or ["INBOX"],
        "snippet": "Test snippet",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "parts": parts,
        },
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
        dep = MagicMock()
        dep.org_id = "org-1"
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_records = AsyncMock()
        dep.on_new_user_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()
        provider = _make_mock_data_store_provider()

        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={
            "auth": {
                "adminEmail": "admin@example.com",
                "type": "service_account",
            }
        })

        c = GoogleGmailTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=provider,
            config_service=config_svc,
            connector_id="gmail-comp-1",
            scope="personal",
            created_by="test-user-id",
        )
        c.admin_data_source = AsyncMock()
        c.gmail_data_source = AsyncMock()
        c.sync_filters = FilterCollection()
        c.indexing_filters = FilterCollection()
        yield c


# ===========================================================================
# Init
# ===========================================================================
class TestGmailInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_auth_credentials(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_no_admin_email(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"type": "service_account"}
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector.init()


# ===========================================================================
# _parse_gmail_headers
# ===========================================================================
class TestParseGmailHeaders:
    def test_extracts_known_headers(self, connector):
        headers = [
            {"name": "Subject", "value": "Test Subject"},
            {"name": "From", "value": "sender@test.com"},
            {"name": "To", "value": "recipient@test.com"},
            {"name": "Cc", "value": "cc@test.com"},
            {"name": "Bcc", "value": "bcc@test.com"},
            {"name": "Message-ID", "value": "<abc@test.com>"},
            {"name": "Date", "value": "Mon, 1 Jan 2025 00:00:00 +0000"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert result["subject"] == "Test Subject"
        assert result["from"] == "sender@test.com"
        assert result["to"] == "recipient@test.com"
        assert result["cc"] == "cc@test.com"
        assert result["bcc"] == "bcc@test.com"
        assert result["message-id"] == "<abc@test.com>"
        assert result["date"] == "Mon, 1 Jan 2025 00:00:00 +0000"

    def test_ignores_unknown_headers(self, connector):
        headers = [
            {"name": "X-Custom", "value": "custom"},
            {"name": "Subject", "value": "Test"},
        ]
        result = connector._parse_gmail_headers(headers)
        assert "x-custom" not in result
        assert result["subject"] == "Test"

    def test_empty_headers(self, connector):
        result = connector._parse_gmail_headers([])
        assert result == {}


# ===========================================================================
# _create_owner_permission
# ===========================================================================
class TestCreateOwnerPermission:
    def test_creates_owner_permission(self, connector):
        perm = connector._create_owner_permission("user@test.com")
        assert perm.email == "user@test.com"
        assert perm.type == PermissionType.OWNER
        assert perm.entity_type == EntityType.USER


# ===========================================================================
# _parse_email_list
# ===========================================================================
class TestParseEmailList:
    def test_single_email(self, connector):
        result = connector._parse_email_list("a@test.com")
        assert result == ["a@test.com"]

    def test_multiple_emails(self, connector):
        result = connector._parse_email_list("a@test.com, b@test.com, c@test.com")
        assert result == ["a@test.com", "b@test.com", "c@test.com"]

    def test_empty_string(self, connector):
        result = connector._parse_email_list("")
        assert result == []

    def test_none(self, connector):
        result = connector._parse_email_list(None)
        assert result == []

    def test_trailing_commas(self, connector):
        result = connector._parse_email_list("a@test.com, ,")
        assert result == ["a@test.com"]


# ===========================================================================
# _extract_email_from_header
# ===========================================================================
class TestExtractEmailFromHeader:
    def test_name_and_email(self, connector):
        result = connector._extract_email_from_header("John Doe <john@test.com>")
        assert result == "john@test.com"

    def test_plain_email(self, connector):
        result = connector._extract_email_from_header("john@test.com")
        assert result == "john@test.com"

    def test_empty_string(self, connector):
        result = connector._extract_email_from_header("")
        assert result == ""

    def test_none(self, connector):
        result = connector._extract_email_from_header(None)
        assert result == ""

    def test_malformed_brackets(self, connector):
        result = connector._extract_email_from_header("Name <")
        assert result == "Name <"

    def test_with_spaces(self, connector):
        result = connector._extract_email_from_header("  Test <  test@example.com  >  ")
        assert result == "test@example.com"


# ===========================================================================
# _pass_date_filter
# ===========================================================================
class TestPassDateFilter:
    def test_no_filter_passes(self, connector):
        assert connector._pass_date_filter({"internalDate": "1700000000000"}) is True

    def test_with_filter_within_range(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1600000000000
        mock_filter.get_datetime_end.return_value = 1800000000000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_date_filter({"internalDate": "1700000000000"}) is True

    def test_with_filter_before_start(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1750000000000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_date_filter({"internalDate": "1700000000000"}) is False

    def test_with_filter_after_end(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = None
        mock_filter.get_datetime_end.return_value = 1600000000000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_date_filter({"internalDate": "1700000000000"}) is False

    def test_invalid_internal_date(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1600000000000
        mock_filter.get_datetime_end.return_value = 1800000000000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_date_filter({"internalDate": "not-a-number"}) is True

    def test_no_internal_date(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1600000000000
        mock_filter.get_datetime_end.return_value = 1800000000000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_date_filter({}) is True


# ===========================================================================
# _process_gmail_message
# ===========================================================================
class TestProcessGmailMessage:
    @pytest.mark.asyncio
    async def test_new_inbox_message(self, connector):
        msg = _make_gmail_message(label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            "user@test.com", msg, "thread-1", None
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL

    @pytest.mark.asyncio
    async def test_sent_message(self, connector):
        msg = _make_gmail_message(
            label_ids=["SENT"],
            from_email="user@test.com"
        )
        result = await connector._process_gmail_message(
            "user@test.com", msg, "thread-1", None
        )
        assert result is not None
        # Sender gets OWNER permission
        assert any(p.type == PermissionType.OWNER for p in result.new_permissions)

    @pytest.mark.asyncio
    async def test_message_without_id(self, connector):
        msg = {"payload": {"headers": []}}
        result = await connector._process_gmail_message(
            "user@test.com", msg, "thread-1", None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_message_filtered_by_date(self, connector):
        msg = _make_gmail_message(internal_date="1000000000000")
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1500000000000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        result = await connector._process_gmail_message(
            "user@test.com", msg, "thread-1", None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_message_with_other_labels(self, connector):
        msg = _make_gmail_message(label_ids=["CATEGORY_UPDATES"])
        result = await connector._process_gmail_message(
            "user@test.com", msg, "thread-1", None
        )
        assert result is not None
        # Should fall through to OTHERS group
        assert "OTHERS" in result.record.external_record_group_id

    @pytest.mark.asyncio
    async def test_receiver_gets_read_permission(self, connector):
        msg = _make_gmail_message(
            from_email="other@test.com",
            to_emails="user@test.com",
        )
        result = await connector._process_gmail_message(
            "user@test.com", msg, "thread-1", None
        )
        assert result is not None
        assert any(p.type == PermissionType.READ for p in result.new_permissions)

    @pytest.mark.asyncio
    async def test_no_subject_defaults(self, connector):
        msg = _make_gmail_message(subject="")
        msg["payload"]["headers"] = [
            {"name": "From", "value": "sender@test.com"},
        ]
        result = await connector._process_gmail_message(
            "user@test.com", msg, "thread-1", None
        )
        assert result is not None
        assert result.record.record_name == "(No Subject)"


# ===========================================================================
# _extract_attachment_infos
# ===========================================================================
class TestExtractAttachmentInfos:
    def test_regular_attachment(self, connector):
        msg = _make_gmail_message(has_attachment=True)
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["filename"] == "test.pdf"
        assert infos[0]["isDriveFile"] is False

    def test_drive_attachment(self, connector):
        msg = {
            "id": "msg-1",
            "payload": {
                "headers": [],
                "parts": [{
                    "partId": "1",
                    "filename": "large_file.zip",
                    "mimeType": "application/zip",
                    "body": {"driveFileId": "drive-file-1", "size": 50000000},
                }],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["isDriveFile"] is True
        assert infos[0]["driveFileId"] == "drive-file-1"

    def test_no_attachments(self, connector):
        msg = {
            "id": "msg-1",
            "payload": {"headers": [], "parts": []},
        }
        infos = connector._extract_attachment_infos(msg)
        assert infos == []

    def test_drive_links_in_body(self, connector):
        import base64
        html = '<a href="https://drive.google.com/file/d/FILEID123/view?usp=drive_web">link</a>'
        encoded = base64.urlsafe_b64encode(html.encode("utf-8")).decode("utf-8")
        msg = {
            "id": "msg-1",
            "payload": {
                "headers": [],
                "parts": [{
                    "partId": "0",
                    "mimeType": "text/html",
                    "body": {"data": encoded},
                }],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert any(info.get("driveFileId") == "FILEID123" for info in infos)


# ===========================================================================
# _merge_history_changes
# ===========================================================================
class TestMergeHistoryChanges:
    def test_deduplicates_by_id(self, connector):
        inbox = {"history": [{"id": "1"}, {"id": "2"}]}
        sent = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3

    def test_sorts_by_id(self, connector):
        inbox = {"history": [{"id": "3"}, {"id": "1"}]}
        sent = {"history": [{"id": "2"}]}
        result = connector._merge_history_changes(inbox, sent)
        ids = [h["id"] for h in result["history"]]
        assert ids == ["1", "2", "3"]

    def test_empty_inputs(self, connector):
        result = connector._merge_history_changes({"history": []}, {"history": []})
        assert result == {"history": []}


# ===========================================================================
# _get_existing_record
# ===========================================================================
class TestGetExistingRecord:
    @pytest.mark.asyncio
    async def test_returns_record_if_exists(self, connector):
        mock_record = MagicMock()
        connector.data_store_provider = _make_mock_data_store_provider(mock_record)
        result = await connector._get_existing_record("ext-1")
        assert result == mock_record

    @pytest.mark.asyncio
    async def test_returns_none_if_not_exists(self, connector):
        result = await connector._get_existing_record("ext-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, connector):
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(side_effect=Exception("db error"))
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx

        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._get_existing_record("ext-1")
        assert result is None


# ===========================================================================
# run_sync
# ===========================================================================
class TestGmailRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_orchestrates_steps(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector._sync_users = AsyncMock()
            connector._sync_user_groups = AsyncMock()
            connector._sync_record_groups = AsyncMock()
            connector._process_users_in_batches = AsyncMock()
            connector.synced_users = []

            await connector.run_sync()

            connector._sync_users.assert_awaited_once()
            connector._sync_user_groups.assert_awaited_once()
            connector._sync_record_groups.assert_awaited_once()
            connector._process_users_in_batches.assert_awaited_once()


# ===========================================================================
# Misc methods
# ===========================================================================
class TestGmailMisc:
    def test_get_signed_url_raises_not_implemented(self, connector):
        record = MagicMock()
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(record)

    def test_handle_webhook_notification_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


# ===========================================================================
# _sync_users (Gmail-specific)
# ===========================================================================
class TestGmailSyncUsers:
    @pytest.mark.asyncio
    async def test_no_admin_source_raises(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_sync_users_with_users(self, connector):
        user = _make_google_user()
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert len(connector.synced_users) == 1
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_users_no_users(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": []})
        await connector._sync_users()
        assert connector.synced_users == []


# ===========================================================================
# _sync_user_groups (Gmail-specific)
# ===========================================================================
class TestGmailSyncUserGroups:
    @pytest.mark.asyncio
    async def test_no_admin_source_raises(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_sync_groups_with_groups(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "group@test.com", "name": "Test Group", "id": "g1"}]
        })
        connector._process_group = AsyncMock()
        await connector._sync_user_groups()
        connector._process_group.assert_awaited_once()


# ===========================================================================
# _process_group (Gmail-specific)
# ===========================================================================
class TestGmailProcessGroup:
    @pytest.mark.asyncio
    async def test_group_without_email_skipped(self, connector):
        group = {"id": "g1", "name": "NoEmail"}
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_with_user_members(self, connector):
        group = {"email": "group@test.com", "name": "TestGroup", "creationTime": "2024-01-01T00:00:00Z"}
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "u@test.com", "id": "u1"},
        ])
        connector.synced_users = []
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_group_with_no_user_members(self, connector):
        group = {"email": "group@test.com", "name": "EmptyGroup"}
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "GROUP", "email": "subgroup@test.com"},
        ])
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_not_called()


# ===========================================================================
# _fetch_group_members
# ===========================================================================
class TestGmailFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.admin_data_source.members_list = AsyncMock(side_effect=[
            {"members": [{"id": "m1"}], "nextPageToken": "tok2"},
            {"members": [{"id": "m2"}]},
        ])
        members = await connector._fetch_group_members("group@test.com")
        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_empty_members(self, connector):
        connector.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        members = await connector._fetch_group_members("group@test.com")
        assert members == []

    @pytest.mark.asyncio
    async def test_error_raises(self, connector):
        connector.admin_data_source.members_list = AsyncMock(side_effect=Exception("API error"))
        with pytest.raises(Exception, match="API error"):
            await connector._fetch_group_members("group@test.com")
