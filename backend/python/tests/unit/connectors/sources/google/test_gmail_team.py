"""Tests for GoogleGmailTeamConnector (app/connectors/sources/google/gmail/team/connector.py)."""

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
    label_ids=None,
    internal_date="1704067200000",
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

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": label_ids,
        "snippet": "Team snippet...",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": ""},
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
    """Tests for the team Gmail connector's init method."""

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
# _process_gmail_message (team)
# ---------------------------------------------------------------------------

class TestTeamProcessGmailMessage:
    """Tests for the team connector's _process_gmail_message."""

    async def test_new_message_creates_mail_record(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL
        assert result.record.record_name == "Team Subject"

    async def test_inbox_label_routing(self, connector):
        message = _make_gmail_message(label_ids=["INBOX"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:INBOX"

    async def test_sent_label_routing(self, connector):
        message = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.record.external_record_group_id == "user@example.com:SENT"

    async def test_sender_is_owner(self, connector):
        message = _make_gmail_message(from_email="user@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.OWNER

    async def test_non_sender_gets_read(self, connector):
        message = _make_gmail_message(from_email="other@example.com")
        result = await connector._process_gmail_message(
            user_email="user@example.com",
            message=message,
            thread_id="thread-1",
            previous_message_id=None,
        )
        assert result.new_permissions[0].type == PermissionType.READ


# ---------------------------------------------------------------------------
# Date filter (team)
# ---------------------------------------------------------------------------

class TestTeamGmailDateFilter:
    """Tests for _pass_date_filter in team connector."""

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


# ---------------------------------------------------------------------------
# Attachment extraction (team)
# ---------------------------------------------------------------------------

class TestTeamExtractAttachments:
    """Tests for _extract_attachment_infos in team connector."""

    def test_regular_attachment_extracted(self, connector):
        message = _make_gmail_message(has_attachments=True)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 1
        assert infos[0]["filename"] == "attachment.xlsx"
        assert infos[0]["isDriveFile"] is False

    def test_no_attachments(self, connector):
        message = _make_gmail_message(has_attachments=False)
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 0


# ---------------------------------------------------------------------------
# _process_gmail_attachment (team)
# ---------------------------------------------------------------------------

class TestTeamProcessAttachment:
    """Tests for _process_gmail_attachment in team connector."""

    async def test_creates_file_record_for_attachment(self, connector):
        attachment_info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "data.csv",
            "mimeType": "text/csv",
            "size": 500,
            "isDriveFile": False,
        }
        parent_perms = [Permission(email="u@example.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]

        result = await connector._process_gmail_attachment(
            user_email="u@example.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=parent_perms,
        )
        assert result is not None
        assert result.record.record_name == "data.csv"
        assert result.record.record_type == RecordType.FILE
        assert result.record.is_dependent_node is True
        assert result.record.parent_external_record_id == "msg-1"

    async def test_returns_none_for_missing_stable_id(self, connector):
        attachment_info = {
            "attachmentId": "att-1",
            "stableAttachmentId": None,
            "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            user_email="u@example.com",
            message_id="msg-1",
            attachment_info=attachment_info,
            parent_mail_permissions=[],
        )
        assert result is None


# ---------------------------------------------------------------------------
# _run_full_sync
# ---------------------------------------------------------------------------

class TestTeamFullSync:
    """Tests for the team Gmail full sync flow."""

    async def test_full_sync_processes_threads(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "thread-1"}],
        })
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })

        await connector._run_full_sync(
            user_email="user@example.com",
            user_gmail_client=user_gmail_client,
            sync_point_key="test-key",
        )

        # Should process records
        connector.data_entities_processor.on_new_records.assert_called()
        # Should update sync point
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    async def test_full_sync_handles_empty_threads(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={"threads": []})

        await connector._run_full_sync(
            user_email="user@example.com",
            user_gmail_client=user_gmail_client,
            sync_point_key="test-key",
        )

        # No records to process
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_full_sync_paginates_threads(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "hist-1"})
        user_gmail_client.users_threads_list = AsyncMock(side_effect=[
            {
                "threads": [{"id": "thread-1"}],
                "nextPageToken": "page2",
            },
            {
                "threads": [{"id": "thread-2"}],
            },
        ])
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()],
        })

        await connector._run_full_sync(
            user_email="user@example.com",
            user_gmail_client=user_gmail_client,
            sync_point_key="test-key",
        )

        assert user_gmail_client.users_threads_list.call_count == 2


# ---------------------------------------------------------------------------
# _run_sync_with_yield
# ---------------------------------------------------------------------------

class TestTeamRunSyncWithYield:
    """Tests for the team connector's _run_sync_with_yield routing."""

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
             patch.object(
                 connector, "_run_sync_with_history_id", new_callable=AsyncMock,
                 side_effect=HttpError(mock_resp, b"not found"),
             ) as mock_inc, \
             patch.object(connector, "_run_full_sync", new_callable=AsyncMock) as mock_full:
            mock_create.return_value = AsyncMock()
            await connector._run_sync_with_yield("user@example.com")
            mock_full.assert_called_once()


# ---------------------------------------------------------------------------
# Message generator with indexing filter
# ---------------------------------------------------------------------------

class TestMessageGeneratorWithFilters:
    """Tests for _process_gmail_message_generator with indexing filters."""

    async def test_generator_applies_mail_indexing_filter(self, connector):
        from app.connectors.core.registry.filters import Filter

        # Disable mail indexing
        mock_filter = MagicMock()
        mock_filter.key = "mails"
        mock_filter.value = False
        mock_filter.is_empty.return_value = False

        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        messages = [_make_gmail_message()]
        results = []
        async for update in connector._process_gmail_message_generator(
            messages, "user@example.com", "thread-1"
        ):
            if update:
                results.append(update)

        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_generator_skips_messages_with_no_id(self, connector):
        messages = [{"threadId": "t1", "payload": {"headers": []}}]
        results = []
        async for update in connector._process_gmail_message_generator(
            messages, "user@example.com", "thread-1"
        ):
            if update:
                results.append(update)

        assert len(results) == 0


# ---------------------------------------------------------------------------
# Attachment generator with indexing filter
# ---------------------------------------------------------------------------

class TestAttachmentGeneratorWithFilters:
    """Tests for _process_gmail_attachment_generator with indexing filters."""

    async def test_generator_applies_attachment_indexing_filter(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        attachment_infos = [{
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "file.txt",
            "mimeType": "text/plain",
            "size": 100,
            "isDriveFile": False,
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
    """Tests for _get_existing_record helper."""

    async def test_returns_existing_record(self, connector):
        existing = MagicMock()
        existing.id = "found-id"
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)

        result = await connector._get_existing_record("ext-id-1")
        assert result is not None
        assert result.id == "found-id"

    async def test_returns_none_when_not_found(self, connector):
        connector.data_store_provider = _make_mock_data_store_provider(existing_record=None)
        result = await connector._get_existing_record("nonexistent")
        assert result is None

    async def test_returns_none_on_error(self, connector):
        provider = MagicMock()

        @asynccontextmanager
        async def _failing_tx():
            raise Exception("DB error")
            yield  # noqa: unreachable

        provider.transaction = _failing_tx
        connector.data_store_provider = provider

        result = await connector._get_existing_record("ext-id-1")
        assert result is None
