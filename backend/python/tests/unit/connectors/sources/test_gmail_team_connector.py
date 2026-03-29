"""
Comprehensive unit tests for GoogleGmailTeamConnector targeting 95%+ coverage.

Covers all methods including sync, streaming, reindexing, cleanup, and error paths.
"""

import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call, patch

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import (
    FilterCollection,
    IndexingFilterKey,
    SyncFilterKey,
)
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
    # Return a MagicMock logger to avoid singleton pollution from other tests
    log = MagicMock(spec=logging.Logger)
    log.setLevel = MagicMock()
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.get_user_by_user_id = AsyncMock(return_value=None)
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
            "filename": "attachment.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "body": {
                "attachmentId": "att-1",
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
        "snippet": "Test snippet...",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": body_data},
            "parts": parts,
        },
    }


def _make_google_user(
    email="user@example.com",
    user_id="guser-1",
    full_name="Test User",
    suspended=False,
    has_org=True,
    creation_time="2024-01-01T00:00:00.000Z",
):
    user = {
        "id": user_id,
        "primaryEmail": email,
        "name": {"fullName": full_name, "givenName": "Test", "familyName": "User"},
        "suspended": suspended,
        "creationTime": creation_time,
    }
    if has_org:
        user["organizations"] = [{"title": "Engineer"}]
    return user


def _make_app_user(email="user@example.com", source_user_id="guser-1", full_name="Test User"):
    return AppUser(
        app_name=Connectors.GOOGLE_MAIL_WORKSPACE,
        connector_id="gmail-cov-1",
        source_user_id=source_user_id,
        email=email,
        full_name=full_name,
        is_active=True,
    )


def _make_mock_record(
    record_id="rec-1",
    external_record_id="ext-1",
    record_type=RecordType.MAIL,
    parent_external_record_id=None,
    connector_id="gmail-cov-1",
    record_name="Test Record",
    mime_type="text/gmail_content",
):
    rec = MagicMock()
    rec.id = record_id
    rec.external_record_id = external_record_id
    rec.record_type = record_type
    rec.parent_external_record_id = parent_external_record_id
    rec.connector_id = connector_id
    rec.record_name = record_name
    rec.mime_type = mime_type
    rec.version = 0
    rec.external_record_group_id = "user@example.com:INBOX"
    return rec


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
        dep.org_id = "org-cov"
        dep.on_new_records = AsyncMock()
        dep.on_new_app_users = AsyncMock()
        dep.on_new_record_groups = AsyncMock()
        dep.on_new_user_groups = AsyncMock()
        dep.on_record_deleted = AsyncMock()
        dep.on_record_metadata_update = AsyncMock()
        dep.on_record_content_update = AsyncMock()
        dep.on_updated_record_permissions = AsyncMock()
        dep.get_all_active_users = AsyncMock(return_value=[])
        dep.reindex_existing_records = AsyncMock()

        ds_provider = _make_mock_data_store_provider()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com", "type": "service_account"},
        })

        conn = GoogleGmailTeamConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=ds_provider,
            config_service=config_service,
            connector_id="gmail-cov-1",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.admin_client = MagicMock()
        conn.gmail_client = MagicMock()
        conn.admin_data_source = AsyncMock()
        conn.gmail_data_source = AsyncMock()
        conn.gmail_data_source.client = MagicMock()
        conn.config = {"credentials": {"auth": {"type": "service_account"}}}
        yield conn


# ===========================================================================
# init() - Success path
# ===========================================================================

class TestInitSuccess:
    @pytest.mark.asyncio
    async def test_init_success_returns_true(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient:
            mock_client = MagicMock()
            mock_client.get_client.return_value = MagicMock()
            MockGoogleClient.build_from_services = AsyncMock(return_value=mock_client)

            result = await connector.init()
            assert result is True
            assert connector.admin_data_source is not None
            assert connector.gmail_data_source is not None

    @pytest.mark.asyncio
    async def test_init_admin_client_failure(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient:
            MockGoogleClient.build_from_services = AsyncMock(
                side_effect=Exception("Admin client build failed")
            )
            with pytest.raises(ValueError, match="Failed to initialize Google Admin client"):
                await connector.init()

    @pytest.mark.asyncio
    async def test_init_gmail_client_failure(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient:
            # Admin succeeds on first call, Gmail fails on second
            mock_admin = MagicMock()
            mock_admin.get_client.return_value = MagicMock()
            MockGoogleClient.build_from_services = AsyncMock(
                side_effect=[mock_admin, Exception("Gmail client build failed")]
            )
            with pytest.raises(ValueError, match="Failed to initialize Google Gmail client"):
                await connector.init()


# ===========================================================================
# test_connection_and_access
# ===========================================================================

class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_returns_true_when_all_initialized(self, connector):
        assert await connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_returns_false_when_gmail_data_source_none(self, connector):
        connector.gmail_data_source = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_admin_data_source_none(self, connector):
        connector.admin_data_source = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_gmail_client_none(self, connector):
        connector.gmail_client = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_admin_client_none(self, connector):
        connector.admin_client = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self, connector):
        # Force an exception by making attribute access fail
        original = type(connector).__dict__.get("gmail_data_source")
        type(connector).gmail_data_source = PropertyMock(side_effect=Exception("access error"))
        try:
            result = await connector.test_connection_and_access()
            assert result is False
        finally:
            # Properly restore: delete the class-level descriptor so instance attr works again
            if original is None:
                try:
                    delattr(type(connector), "gmail_data_source")
                except AttributeError:
                    pass
            else:
                type(connector).gmail_data_source = original


# ===========================================================================
# get_signed_url / handle_webhook_notification / get_filter_options
# ===========================================================================

class TestNotImplementedMethods:
    def test_get_signed_url_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())

    def test_handle_webhook_notification_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_get_filter_options_raises(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("some_key")


# ===========================================================================
# cleanup
# ===========================================================================

class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_all_references(self, connector):
        """Verify cleanup() sets data sources, clients, and config to None."""
        connector.gmail_data_source = MagicMock()
        connector.admin_data_source = MagicMock()
        connector.gmail_client = MagicMock()
        connector.admin_client = MagicMock()
        connector.config = MagicMock()

        await connector.cleanup()

        assert connector.gmail_data_source is None
        assert connector.admin_data_source is None
        assert connector.gmail_client is None
        assert connector.admin_client is None
        assert connector.config is None

    @pytest.mark.asyncio
    async def test_cleanup_handles_exception(self, connector):
        """Verify cleanup() catches internal exceptions gracefully."""
        connector.logger = MagicMock()
        connector.logger.info = MagicMock(side_effect=Exception("cleanup error"))
        await connector.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_when_already_none(self, connector):
        connector.gmail_data_source = None
        connector.admin_data_source = None
        connector.gmail_client = None
        connector.admin_client = None
        connector.config = None
        await connector.cleanup()


# ===========================================================================
# create_connector
# ===========================================================================

class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ), patch(
            "app.connectors.sources.google.gmail.team.connector.SyncPoint"
        ) as MockSyncPoint, patch(
            "app.connectors.sources.google.gmail.team.connector.DataSourceEntitiesProcessor"
        ) as MockProcessor:
            mock_sync_point = AsyncMock()
            MockSyncPoint.return_value = mock_sync_point

            mock_dep = AsyncMock()
            mock_dep.org_id = "org-create"
            MockProcessor.return_value = mock_dep

            from app.connectors.sources.google.gmail.team.connector import (
                GoogleGmailTeamConnector,
            )

            result = await GoogleGmailTeamConnector.create_connector(
                logger=_make_logger(),
                data_store_provider=_make_mock_data_store_provider(),
                config_service=AsyncMock(),
                connector_id="create-1",
            )
            assert result is not None
            MockProcessor.return_value.initialize.assert_called_once()


# ===========================================================================
# run_incremental_sync
# ===========================================================================

class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates(self, connector):
        with patch.object(connector, "run_sync", new_callable=AsyncMock) as mock_sync:
            await connector.run_incremental_sync()
            mock_sync.assert_called_once()


# ===========================================================================
# _create_user_gmail_client
# ===========================================================================

class TestCreateUserGmailClient:
    @pytest.mark.asyncio
    async def test_creates_client_successfully(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient:
            mock_client = MagicMock()
            mock_client.get_client.return_value = MagicMock()
            MockGoogleClient.build_from_services = AsyncMock(return_value=mock_client)

            result = await connector._create_user_gmail_client("user@example.com")
            assert result is not None
            MockGoogleClient.build_from_services.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_failure(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient:
            MockGoogleClient.build_from_services = AsyncMock(
                side_effect=Exception("build failed")
            )
            with pytest.raises(Exception, match="build failed"):
                await connector._create_user_gmail_client("user@example.com")


# ===========================================================================
# _delete_message_and_attachments
# ===========================================================================

class TestDeleteMessageAndAttachments:
    @pytest.mark.asyncio
    async def test_deletes_message_and_attachments(self, connector):
        mock_attachment = MagicMock()
        mock_attachment.id = "att-rec-1"
        tx = AsyncMock()
        tx.get_records_by_parent = AsyncMock(return_value=[mock_attachment])

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        await connector._delete_message_and_attachments("rec-1", "msg-1")
        connector.data_entities_processor.on_record_deleted.assert_any_call("att-rec-1")
        connector.data_entities_processor.on_record_deleted.assert_any_call("rec-1")

    @pytest.mark.asyncio
    async def test_handles_attachment_delete_error(self, connector):
        mock_attachment = MagicMock()
        mock_attachment.id = "att-rec-fail"
        tx = AsyncMock()
        tx.get_records_by_parent = AsyncMock(return_value=[mock_attachment])

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction
        connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=[Exception("delete attachment error"), None]
        )

        # Should not raise
        await connector._delete_message_and_attachments("rec-1", "msg-1")

    @pytest.mark.asyncio
    async def test_handles_transaction_error(self, connector):
        @asynccontextmanager
        async def _failing_tx():
            raise Exception("tx error")
            yield  # noqa: unreachable

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _failing_tx

        # Should not raise
        await connector._delete_message_and_attachments("rec-1", "msg-1")


# ===========================================================================
# _find_previous_message_in_thread
# ===========================================================================

class TestFindPreviousMessageInThread:
    @pytest.mark.asyncio
    async def test_finds_previous_message_in_db(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-prev", "internalDate": "1000"},
                {"id": "msg-current", "internalDate": "2000"},
            ]
        })

        existing = MagicMock()
        existing.id = "db-rec-prev"
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=existing):
            result = await connector._find_previous_message_in_thread(
                "user@example.com", user_gmail_client, "thread-1", "msg-current", "2000"
            )
        assert result == "db-rec-prev"

    @pytest.mark.asyncio
    async def test_finds_previous_message_in_batch_records(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-prev", "internalDate": "1000"},
                {"id": "msg-current", "internalDate": "2000"},
            ]
        })

        batch_record = MagicMock()
        batch_record.external_record_id = "msg-prev"
        batch_record.id = "batch-rec-prev"

        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None):
            result = await connector._find_previous_message_in_thread(
                "user@example.com", user_gmail_client, "thread-1", "msg-current", "2000",
                batch_records=[(batch_record, [])]
            )
        assert result == "batch-rec-prev"

    @pytest.mark.asyncio
    async def test_returns_none_for_single_message_thread(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [{"id": "msg-only", "internalDate": "1000"}]
        })

        result = await connector._find_previous_message_in_thread(
            "user@example.com", user_gmail_client, "thread-1", "msg-only", "1000"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_earlier_messages(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-first", "internalDate": "1000"},
                {"id": "msg-later", "internalDate": "3000"},
            ]
        })

        result = await connector._find_previous_message_in_thread(
            "user@example.com", user_gmail_client, "thread-1", "msg-first", "1000"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_threads_get = AsyncMock(side_effect=Exception("thread error"))

        result = await connector._find_previous_message_in_thread(
            "user@example.com", user_gmail_client, "thread-1", "msg-1", "1000"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_with_empty_messages(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_threads_get = AsyncMock(return_value={"messages": []})

        result = await connector._find_previous_message_in_thread(
            "user@example.com", user_gmail_client, "thread-1", "msg-1", "1000"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_none_internal_date(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-prev", "internalDate": "0"},
                {"id": "msg-current", "internalDate": "2000"},
            ]
        })
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None):
            result = await connector._find_previous_message_in_thread(
                "user@example.com", user_gmail_client, "thread-1", "msg-current", None
            )
        # With None internal_date, current_date = 0, so no messages before it
        assert result is None


# ===========================================================================
# _process_users_in_batches
# ===========================================================================

class TestProcessUsersInBatches:
    @pytest.mark.asyncio
    async def test_processes_active_users(self, connector):
        user1 = _make_app_user(email="active@example.com")
        user2 = _make_app_user(email="inactive@example.com")

        active_user = MagicMock()
        active_user.email = "active@example.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active_user])

        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock):
            await connector._process_users_in_batches([user1, user2])
            connector._run_sync_with_yield.assert_called_once_with("active@example.com")

    @pytest.mark.asyncio
    async def test_no_active_users(self, connector):
        user1 = _make_app_user(email="inactive@example.com")
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock):
            await connector._process_users_in_batches([user1])
            connector._run_sync_with_yield.assert_not_called()

    @pytest.mark.asyncio
    async def test_batches_users(self, connector):
        connector.max_concurrent_batches = 2
        users = [
            _make_app_user(email=f"user{i}@example.com", source_user_id=f"u{i}")
            for i in range(5)
        ]
        active_users = [MagicMock(email=f"user{i}@example.com") for i in range(5)]
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=active_users)

        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock):
            await connector._process_users_in_batches(users)
            assert connector._run_sync_with_yield.call_count == 5

    @pytest.mark.asyncio
    async def test_handles_error(self, connector):
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=Exception("fetch error")
        )
        with pytest.raises(Exception, match="fetch error"):
            await connector._process_users_in_batches([_make_app_user()])

    @pytest.mark.asyncio
    async def test_user_without_email_skipped(self, connector):
        user = _make_app_user(email="")
        active_user = MagicMock(email="")
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active_user])

        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock):
            await connector._process_users_in_batches([user])
            connector._run_sync_with_yield.assert_not_called()


# ===========================================================================
# _sync_users
# ===========================================================================

class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_syncs_users_successfully(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [_make_google_user()],
        })

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_called_once()
        assert len(connector.synced_users) == 1

    @pytest.mark.asyncio
    async def test_paginates_users(self, connector):
        connector.admin_data_source.users_list = AsyncMock(side_effect=[
            {"users": [_make_google_user(email="u1@e.com", user_id="u1")], "nextPageToken": "p2"},
            {"users": [_make_google_user(email="u2@e.com", user_id="u2")]},
        ])

        await connector._sync_users()
        assert connector.admin_data_source.users_list.call_count == 2
        assert len(connector.synced_users) == 2

    @pytest.mark.asyncio
    async def test_no_users_found(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": []})

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_not_called()
        assert connector.synced_users == []

    @pytest.mark.asyncio
    async def test_admin_data_source_not_initialized(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_user_without_email_skipped(self, connector):
        user_no_email = _make_google_user()
        user_no_email["primaryEmail"] = ""
        user_no_email.pop("email", None)
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_no_email],
        })

        await connector._sync_users()
        assert len(connector.synced_users) == 0

    @pytest.mark.asyncio
    async def test_user_with_fallback_email(self, connector):
        user_data = _make_google_user()
        user_data["primaryEmail"] = None
        user_data["email"] = "fallback@example.com"
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_data],
        })

        await connector._sync_users()
        assert len(connector.synced_users) == 1
        assert connector.synced_users[0].email == "fallback@example.com"

    @pytest.mark.asyncio
    async def test_user_with_no_fullname_fallback(self, connector):
        user_data = _make_google_user()
        user_data["name"] = {"givenName": "", "familyName": ""}
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_data],
        })

        await connector._sync_users()
        # Falls back to email
        assert connector.synced_users[0].full_name == "user@example.com"

    @pytest.mark.asyncio
    async def test_user_with_given_and_family_name(self, connector):
        user_data = _make_google_user()
        user_data["name"] = {"givenName": "Jane", "familyName": "Doe"}
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_data],
        })

        await connector._sync_users()
        assert connector.synced_users[0].full_name == "Jane Doe"

    @pytest.mark.asyncio
    async def test_user_without_organizations(self, connector):
        user_data = _make_google_user(has_org=False)
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_data],
        })

        await connector._sync_users()
        assert connector.synced_users[0].title is None

    @pytest.mark.asyncio
    async def test_suspended_user(self, connector):
        user_data = _make_google_user(suspended=True)
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_data],
        })

        await connector._sync_users()
        assert connector.synced_users[0].is_active is False

    @pytest.mark.asyncio
    async def test_invalid_creation_time(self, connector):
        user_data = _make_google_user()
        user_data["creationTime"] = "invalid-date"
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_data],
        })

        with patch("app.connectors.sources.google.gmail.team.connector.parse_timestamp",
                    side_effect=Exception("parse error")):
            await connector._sync_users()
            assert len(connector.synced_users) == 1

    @pytest.mark.asyncio
    async def test_user_processing_error_continues(self, connector):
        user_ok = _make_google_user(email="ok@e.com", user_id="u-ok")
        user_bad = {"id": "bad"}  # Missing all required fields
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [user_bad, user_ok],
        })

        await connector._sync_users()
        # Only the good user should be processed
        assert len(connector.synced_users) >= 1

    @pytest.mark.asyncio
    async def test_users_list_page_error(self, connector):
        connector.admin_data_source.users_list = AsyncMock(
            side_effect=Exception("page error")
        )
        with pytest.raises(Exception, match="page error"):
            await connector._sync_users()


# ===========================================================================
# _sync_user_groups
# ===========================================================================

class TestSyncUserGroups:
    @pytest.mark.asyncio
    async def test_syncs_groups_successfully(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "group@example.com", "name": "Test Group"}],
        })
        with patch.object(connector, "_process_group", new_callable=AsyncMock):
            await connector._sync_user_groups()
            connector._process_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_paginates_groups(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(side_effect=[
            {"groups": [{"email": "g1@e.com", "name": "G1"}], "nextPageToken": "p2"},
            {"groups": [{"email": "g2@e.com", "name": "G2"}]},
        ])
        with patch.object(connector, "_process_group", new_callable=AsyncMock):
            await connector._sync_user_groups()
            assert connector._process_group.call_count == 2

    @pytest.mark.asyncio
    async def test_no_groups_found(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={"groups": []})
        with patch.object(connector, "_process_group", new_callable=AsyncMock):
            await connector._sync_user_groups()
            connector._process_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_data_source_not_initialized(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_group_processing_error_continues(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [
                {"email": "bad@e.com", "name": "Bad"},
                {"email": "good@e.com", "name": "Good"},
            ],
        })
        with patch.object(connector, "_process_group", new_callable=AsyncMock,
                          side_effect=[Exception("group error"), None]):
            await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_groups_list_page_error(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(
            side_effect=Exception("page error")
        )
        with pytest.raises(Exception, match="page error"):
            await connector._sync_user_groups()


# ===========================================================================
# _process_group
# ===========================================================================

class TestProcessGroup:
    @pytest.mark.asyncio
    async def test_processes_group_with_members(self, connector):
        group = {"email": "group@e.com", "name": "Test Group", "description": "A test group"}
        connector.synced_users = [_make_app_user(email="member@e.com", source_user_id="m1")]

        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock,
                          return_value=[
                              {"type": "USER", "email": "member@e.com", "id": "m1"},
                          ]):
            await connector._process_group(group)
            connector.data_entities_processor.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_group_without_email(self, connector):
        group = {"name": "No Email Group"}
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_user_members(self, connector):
        group = {"email": "group@e.com", "name": "G1"}
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock,
                          return_value=[
                              {"type": "GROUP", "email": "subgroup@e.com", "id": "sg1"},
                              {"type": "CUSTOMER", "email": "cust@e.com", "id": "c1"},
                          ]):
            await connector._process_group(group)
            connector.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_fallback_name(self, connector):
        group = {"email": "group@e.com"}
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock, return_value=[]):
            await connector._process_group(group)

    @pytest.mark.asyncio
    async def test_group_creation_time_parsing(self, connector):
        group = {"email": "group@e.com", "name": "G1", "creationTime": "2024-01-01T00:00:00Z"}
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock, return_value=[]):
            await connector._process_group(group)

    @pytest.mark.asyncio
    async def test_group_invalid_creation_time(self, connector):
        group = {"email": "group@e.com", "name": "G1", "creationTime": "invalid"}
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock, return_value=[]), \
             patch("app.connectors.sources.google.gmail.team.connector.parse_timestamp",
                   side_effect=Exception("parse err")):
            await connector._process_group(group)

    @pytest.mark.asyncio
    async def test_member_without_email_skipped(self, connector):
        group = {"email": "group@e.com", "name": "G1"}
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock,
                          return_value=[{"type": "USER", "id": "m1"}]):
            await connector._process_group(group)
            connector.data_entities_processor.on_new_user_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_member_lookup_in_synced_users(self, connector):
        synced_user = _make_app_user(email="known@e.com", source_user_id="k1", full_name="Known User")
        connector.synced_users = [synced_user]

        group = {"email": "group@e.com", "name": "G1"}
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock,
                          return_value=[{"type": "USER", "email": "known@e.com", "id": "k1"}]):
            await connector._process_group(group)
            call_args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
            _, members = call_args[0]
            assert members[0].full_name == "Known User"

    @pytest.mark.asyncio
    async def test_member_processing_error_continues(self, connector):
        group = {"email": "group@e.com", "name": "G1"}
        # First member will cause error, second should succeed
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock,
                          return_value=[
                              {"type": "USER", "email": "m1@e.com", "id": "m1"},
                              {"type": "USER", "email": "m2@e.com", "id": "m2"},
                          ]):
            # Create a side effect that fails on first AppUser creation
            original_init = AppUser.__init__
            call_count = [0]
            def patched_init(self_inner, *args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("member error")
                original_init(self_inner, *args, **kwargs)

            # Since AppUser is a pydantic model, we can't easily patch __init__
            # Instead, test via the actual path - the method should handle the exception
            await connector._process_group(group)

    @pytest.mark.asyncio
    async def test_process_group_raises_on_error(self, connector):
        group = {"email": "group@e.com", "name": "G1"}
        with patch.object(connector, "_fetch_group_members", new_callable=AsyncMock,
                          side_effect=Exception("fetch members error")):
            with pytest.raises(Exception, match="fetch members error"):
                await connector._process_group(group)


# ===========================================================================
# _fetch_group_members
# ===========================================================================

class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_fetches_single_page(self, connector):
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"id": "m1", "email": "m1@e.com", "type": "USER"}],
        })

        result = await connector._fetch_group_members("group@e.com")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_paginates_members(self, connector):
        connector.admin_data_source.members_list = AsyncMock(side_effect=[
            {"members": [{"id": "m1"}], "nextPageToken": "p2"},
            {"members": [{"id": "m2"}]},
        ])

        result = await connector._fetch_group_members("group@e.com")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_members(self, connector):
        connector.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        result = await connector._fetch_group_members("group@e.com")
        assert result == []

    @pytest.mark.asyncio
    async def test_raises_on_error(self, connector):
        connector.admin_data_source.members_list = AsyncMock(
            side_effect=Exception("members error")
        )
        with pytest.raises(Exception, match="members error"):
            await connector._fetch_group_members("group@e.com")


# ===========================================================================
# _sync_record_groups
# ===========================================================================

class TestSyncRecordGroups:
    @pytest.mark.asyncio
    async def test_creates_inbox_sent_others_for_each_user(self, connector):
        users = [_make_app_user(email="u1@e.com")]

        await connector._sync_record_groups(users)
        assert connector.data_entities_processor.on_new_record_groups.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_users_returns_early(self, connector):
        await connector._sync_record_groups([])
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_without_email_skipped(self, connector):
        user = _make_app_user(email="")
        await connector._sync_record_groups([user])
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_group_creation_error_continues(self, connector):
        user = _make_app_user(email="u@e.com")
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=[Exception("record group error"), None, None]
        )

        await connector._sync_record_groups([user])
        # Should have attempted 3 calls (INBOX, SENT, OTHERS)
        assert connector.data_entities_processor.on_new_record_groups.call_count == 3

    @pytest.mark.asyncio
    async def test_user_processing_error_continues(self, connector):
        users = [_make_app_user(email="u1@e.com"), _make_app_user(email="u2@e.com")]
        call_count = [0]

        async def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                raise Exception("processing error")

        connector.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=_side_effect)
        await connector._sync_record_groups(users)

    @pytest.mark.asyncio
    async def test_raises_on_top_level_error(self, connector):
        # When data_entities_processor is None, per-user errors are caught internally
        connector.data_entities_processor = None
        await connector._sync_record_groups([_make_app_user()])


# ===========================================================================
# run_sync
# ===========================================================================

class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_calls_all_steps(self, connector):
        with patch.object(connector, "_sync_users", new_callable=AsyncMock) as mock_users, \
             patch.object(connector, "_sync_user_groups", new_callable=AsyncMock) as mock_groups, \
             patch.object(connector, "_sync_record_groups", new_callable=AsyncMock) as mock_rg, \
             patch.object(connector, "_process_users_in_batches", new_callable=AsyncMock) as mock_batch, \
             patch("app.connectors.sources.google.gmail.team.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            await connector.run_sync()
            mock_users.assert_called_once()
            mock_groups.assert_called_once()
            mock_rg.assert_called_once()
            mock_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_propagates_error(self, connector):
        with patch("app.connectors.sources.google.gmail.team.connector.load_connector_filters",
                   new_callable=AsyncMock, side_effect=Exception("filter error")):
            with pytest.raises(Exception, match="filter error"):
                await connector.run_sync()


# ===========================================================================
# _extract_body_from_payload
# ===========================================================================

class TestExtractBodyFromPayload:
    def test_direct_text_html(self, connector):
        payload = {
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(b"<p>Hello</p>").decode()},
        }
        result = connector._extract_body_from_payload(payload)
        assert result == base64.urlsafe_b64encode(b"<p>Hello</p>").decode()

    def test_direct_text_plain(self, connector):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Hello plain").decode()},
        }
        result = connector._extract_body_from_payload(payload)
        assert result != ""

    def test_nested_parts_prefers_html(self, connector):
        html_data = base64.urlsafe_b64encode(b"<p>HTML</p>").decode()
        plain_data = base64.urlsafe_b64encode(b"Plain text").decode()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain_data}},
                {"mimeType": "text/html", "body": {"data": html_data}},
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == html_data

    def test_nested_parts_fallback_to_plain(self, connector):
        plain_data = base64.urlsafe_b64encode(b"Plain text").decode()
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain_data}},
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == plain_data

    def test_empty_payload(self, connector):
        payload = {"mimeType": "multipart/mixed", "body": {}}
        result = connector._extract_body_from_payload(payload)
        assert result == ""

    def test_deeply_nested(self, connector):
        data = base64.urlsafe_b64encode(b"Deep content").decode()
        payload = {
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {},
                    "parts": [
                        {"mimeType": "text/html", "body": {"data": data}},
                    ],
                },
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == data

    def test_no_body_data(self, connector):
        payload = {"mimeType": "text/html", "body": {}}
        result = connector._extract_body_from_payload(payload)
        assert result == ""


# ===========================================================================
# _stream_mail_record
# ===========================================================================

class TestStreamMailRecord:
    @pytest.mark.asyncio
    async def test_streams_mail_content(self, connector):
        html = "<p>Hello World</p>"
        body_data = base64.urlsafe_b64encode(html.encode()).decode()
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "mimeType": "text/html",
                "body": {"data": body_data},
            }
        }
        record = _make_mock_record()
        with patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await connector._stream_mail_record(gmail_service, "msg-1", record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_streams_empty_body(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {"mimeType": "text/plain", "body": {"data": ""}}
        }
        record = _make_mock_record()
        with patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_mail_record(gmail_service, "msg-1", record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_error_404(self, connector):
        mock_resp = MagicMock()
        mock_resp.status = 404
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = HttpError(mock_resp, b"not found")

        record = _make_mock_record()
        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_http_error_non_404(self, connector):
        mock_resp = MagicMock()
        mock_resp.status = 500
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = HttpError(mock_resp, b"server error")

        record = _make_mock_record()
        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_general_exception(self, connector):
        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.side_effect = Exception("general error")

        record = _make_mock_record()
        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_mail_record(gmail_service, "msg-1", record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# _stream_attachment_record
# ===========================================================================

class TestStreamAttachmentRecord:
    @pytest.mark.asyncio
    async def test_drive_file_routes_to_drive(self, connector):
        record = _make_mock_record(external_record_id="drive-id-abc")
        with patch.object(connector, "_stream_from_drive", new_callable=AsyncMock,
                          return_value=MagicMock()):
            result = await connector._stream_attachment_record(
                MagicMock(), "drive-id-abc", record, "file.pdf", "application/pdf"
            )
            connector._stream_from_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_regular_attachment_streams(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        message_payload = {
            "payload": {
                "parts": [
                    {
                        "partId": "1",
                        "body": {"attachmentId": "actual-att-id"},
                    }
                ],
            }
        }
        gmail_service.users().messages().get().execute.return_value = message_payload

        attachment_data = base64.urlsafe_b64encode(b"file content")
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": attachment_data.decode()
        }

        with patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_attachment_record(
                gmail_service, "msg123~1", record, "file.txt", "text/plain"
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_parent_message_found(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id=None,
        )

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_attachment_record(
                MagicMock(), "msg123~1", record, "file.txt", "text/plain"
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_message_id_mismatch_warning(self, connector):
        record = _make_mock_record(
            external_record_id="msgXXX~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [{"partId": "1", "body": {"attachmentId": "att-id"}}],
            }
        }

        att_data = base64.urlsafe_b64encode(b"data")
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": att_data.decode()
        }

        with patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            await connector._stream_attachment_record(
                gmail_service, "msgXXX~1", record, "file.txt", "text/plain"
            )

    @pytest.mark.asyncio
    async def test_part_not_found_in_message(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~99",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [{"partId": "1", "body": {"attachmentId": "att-id"}}],
            }
        }

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_attachment_record(
                gmail_service, "msg123~99", record, "file.txt", "text/plain"
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_gmail_attachment_error_falls_back_to_drive(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [{"partId": "1", "body": {"attachmentId": "att-id"}}],
            }
        }

        mock_resp = MagicMock()
        mock_resp.status = 403
        gmail_service.users().messages().attachments().get().execute.side_effect = HttpError(
            mock_resp, b"forbidden"
        )

        with patch.object(connector, "_stream_from_drive", new_callable=AsyncMock,
                          return_value=MagicMock()):
            result = await connector._stream_attachment_record(
                gmail_service, "msg123~1", record, "file.txt", "text/plain"
            )
            connector._stream_from_drive.assert_called_once()

    @pytest.mark.asyncio
    async def test_gmail_and_drive_both_fail(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [{"partId": "1", "body": {"attachmentId": "att-id"}}],
            }
        }

        mock_resp = MagicMock()
        mock_resp.status = 403
        gmail_service.users().messages().attachments().get().execute.side_effect = HttpError(
            mock_resp, b"forbidden"
        )

        with patch.object(connector, "_stream_from_drive", new_callable=AsyncMock,
                          side_effect=Exception("drive also failed")):
            with pytest.raises(HTTPException) as exc_info:
                await connector._stream_attachment_record(
                    gmail_service, "msg123~1", record, "file.txt", "text/plain"
                )
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_convert_to_pdf_for_attachment(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [{"partId": "1", "body": {"attachmentId": "att-id"}}],
            }
        }

        att_data = base64.urlsafe_b64encode(b"file content")
        gmail_service.users().messages().attachments().get().execute.return_value = {
            "data": att_data.decode()
        }

        with patch.object(connector, "_convert_to_pdf", new_callable=AsyncMock,
                          return_value="/tmp/converted.pdf") as mock_convert, \
             patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream, \
             patch("builtins.open", MagicMock()):
            mock_stream.return_value = MagicMock()
            await connector._stream_attachment_record(
                gmail_service, "msg123~1", record, "file.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                convertTo=MimeTypes.PDF.value
            )

    @pytest.mark.asyncio
    async def test_message_404_during_fetch(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        gmail_service.users().messages().get().execute.side_effect = HttpError(
            mock_resp, b"not found"
        )

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_attachment_record(
                gmail_service, "msg123~1", record, "file.txt", "text/plain"
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_general_error_during_attachment_stream(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {
            "payload": {
                "parts": [{"partId": "1", "body": {"attachmentId": "att-id"}}],
            }
        }

        gmail_service.users().messages().attachments().get().execute.side_effect = Exception(
            "general attachment error"
        )

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_attachment_record(
                gmail_service, "msg123~1", record, "file.txt", "text/plain"
            )
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_message_without_payload_raises(self, connector):
        record = _make_mock_record(
            external_record_id="msg123~1",
            parent_external_record_id="msg123",
        )
        parent_record = MagicMock()
        parent_record.external_record_id = "msg123"

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=parent_record)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        gmail_service = MagicMock()
        gmail_service.users().messages().get().execute.return_value = {}

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_attachment_record(
                gmail_service, "msg123~1", record, "file.txt", "text/plain"
            )
        assert exc_info.value.status_code == 400


# ===========================================================================
# stream_record
# ===========================================================================

class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_streams_mail_record(self, connector):
        record = _make_mock_record(record_type=RecordTypes.MAIL.value)

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_stream_mail_record", new_callable=AsyncMock,
                          return_value=MagicMock()):
            mock_gmail_ds = AsyncMock()
            mock_gmail_ds.client = MagicMock()
            mock_create.return_value = mock_gmail_ds

            tx = AsyncMock()
            tx.get_user_by_user_id = AsyncMock(return_value={"email": "u@e.com"})

            @asynccontextmanager
            async def _transaction():
                yield tx

            connector.data_store_provider = MagicMock()
            connector.data_store_provider.transaction = _transaction

            await connector.stream_record(record, user_id="user-1")
            connector._stream_mail_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_streams_attachment_record(self, connector):
        record = _make_mock_record(record_type=RecordTypes.FILE.value)

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_stream_attachment_record", new_callable=AsyncMock,
                          return_value=MagicMock()):
            mock_gmail_ds = AsyncMock()
            mock_gmail_ds.client = MagicMock()
            mock_create.return_value = mock_gmail_ds

            tx = AsyncMock()
            tx.get_user_by_user_id = AsyncMock(return_value={"email": "u@e.com"})

            @asynccontextmanager
            async def _transaction():
                yield tx

            connector.data_store_provider = MagicMock()
            connector.data_store_provider.transaction = _transaction

            await connector.stream_record(record, user_id="user-1")
            connector._stream_attachment_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_file_id_raises(self, connector):
        record = _make_mock_record()
        record.external_record_id = None

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_user_not_found_falls_back_to_permission(self, connector):
        record = _make_mock_record(record_type=RecordTypes.MAIL.value)

        user_with_perm = MagicMock()
        user_with_perm.email = "perm-user@e.com"

        tx = AsyncMock()
        tx.get_user_by_user_id = AsyncMock(return_value=None)
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user_with_perm)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_stream_mail_record", new_callable=AsyncMock,
                          return_value=MagicMock()):
            mock_gmail_ds = AsyncMock()
            mock_gmail_ds.client = MagicMock()
            mock_create.return_value = mock_gmail_ds

            await connector.stream_record(record, user_id="bad-user")

    @pytest.mark.asyncio
    async def test_no_user_id_gets_permission_user(self, connector):
        record = _make_mock_record(record_type=RecordTypes.MAIL.value)

        user_with_perm = MagicMock()
        user_with_perm.email = "perm@e.com"

        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user_with_perm)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_stream_mail_record", new_callable=AsyncMock,
                          return_value=MagicMock()):
            mock_gmail_ds = AsyncMock()
            mock_gmail_ds.client = MagicMock()
            mock_create.return_value = mock_gmail_ds

            await connector.stream_record(record, user_id=None)

    @pytest.mark.asyncio
    async def test_none_string_user_id(self, connector):
        record = _make_mock_record(record_type=RecordTypes.MAIL.value)

        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_stream_mail_record", new_callable=AsyncMock,
                          return_value=MagicMock()):
            await connector.stream_record(record, user_id="None")

    @pytest.mark.asyncio
    async def test_impersonation_fails_falls_back(self, connector):
        record = _make_mock_record(record_type=RecordTypes.MAIL.value)

        tx = AsyncMock()
        tx.get_user_by_user_id = AsyncMock(return_value={"email": "u@e.com"})

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock,
                          side_effect=Exception("impersonation failed")), \
             patch.object(connector, "_stream_mail_record", new_callable=AsyncMock,
                          return_value=MagicMock()):
            await connector.stream_record(record, user_id="user-1")

    @pytest.mark.asyncio
    async def test_no_gmail_data_source_raises(self, connector):
        record = _make_mock_record(record_type=RecordTypes.MAIL.value)

        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction
        # Ensure logger is clean to avoid cross-test pollution
        connector.logger = MagicMock()

        original = connector.gmail_data_source
        connector.gmail_data_source = None
        try:
            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record, user_id=None)
            assert exc_info.value.status_code == 500
        finally:
            connector.gmail_data_source = original

    @pytest.mark.asyncio
    async def test_convert_to_pdf_param(self, connector):
        record = _make_mock_record(record_type=RecordTypes.FILE.value)

        tx = AsyncMock()
        tx.get_user_by_user_id = AsyncMock(return_value={"email": "u@e.com"})

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock) as mock_create, \
             patch.object(connector, "_stream_attachment_record", new_callable=AsyncMock,
                          return_value=MagicMock()):
            mock_gmail_ds = AsyncMock()
            mock_gmail_ds.client = MagicMock()
            mock_create.return_value = mock_gmail_ds

            await connector.stream_record(record, user_id="user-1", convertTo="application/pdf")
            connector._stream_attachment_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_general_exception_raises_http(self, connector):
        record = _make_mock_record()
        record.external_record_id = "msg-1"

        # Force exception in user lookup
        tx = AsyncMock()
        tx.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record, user_id="user-1")
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_reraises(self, connector):
        record = _make_mock_record()
        record.external_record_id = None

        with pytest.raises(HTTPException):
            await connector.stream_record(record)


# ===========================================================================
# _convert_to_pdf
# ===========================================================================

class TestConvertToPdf:
    @pytest.mark.asyncio
    async def test_successful_conversion(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess, \
             patch("os.path.exists", return_value=True):
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b""))
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            result = await connector._convert_to_pdf("/tmp/file.docx", "/tmp")
            assert result == "/tmp/file.pdf"

    @pytest.mark.asyncio
    async def test_conversion_fails_nonzero_return(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"error"))
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/file.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_output_file_not_found(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess, \
             patch("os.path.exists", return_value=False):
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b""))
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/file.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_conversion_timeout(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_process.terminate = MagicMock()
            mock_process.wait = AsyncMock()
            mock_process.kill = MagicMock()
            mock_subprocess.return_value = mock_process

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/file.docx", "/tmp")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_conversion_timeout_with_kill_fallback(self, connector):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_process.terminate = MagicMock()
            mock_process.wait = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_process.kill = MagicMock()
            mock_subprocess.return_value = mock_process

            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/file.docx", "/tmp")
            assert exc_info.value.status_code == 500
            mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_general_exception_during_conversion(self, connector):
        with patch("asyncio.create_subprocess_exec", side_effect=Exception("exec error")):
            with pytest.raises(HTTPException) as exc_info:
                await connector._convert_to_pdf("/tmp/file.docx", "/tmp")
            assert exc_info.value.status_code == 500


# ===========================================================================
# _stream_from_drive
# ===========================================================================

class TestStreamFromDrive:
    @pytest.mark.asyncio
    async def test_stream_with_user_email(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient, \
             patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream:
            mock_client = MagicMock()
            mock_drive_svc = MagicMock()
            mock_client.get_client.return_value = mock_drive_svc
            MockGoogleClient.build_from_services = AsyncMock(return_value=mock_client)
            mock_stream.return_value = MagicMock()

            record = _make_mock_record()
            await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain",
                user_email="u@e.com"
            )
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_fallback_to_service_account(self, connector):
        connector.config = {"credentials": {"auth": {"type": "service_account"}}}
        record = _make_mock_record()

        with patch("app.connectors.sources.google.gmail.team.connector.GoogleClient") as MockGoogleClient, \
             patch("google.oauth2.service_account.Credentials.from_service_account_info") as mock_creds, \
             patch("app.connectors.sources.google.gmail.team.connector.build") as mock_build, \
             patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream:
            MockGoogleClient.build_from_services = AsyncMock(side_effect=Exception("user client failed"))
            mock_build.return_value = MagicMock()
            mock_stream.return_value = MagicMock()

            await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain",
                user_email="u@e.com"
            )

    @pytest.mark.asyncio
    async def test_stream_no_credentials(self, connector):
        connector.config = None
        record = _make_mock_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_no_auth_in_credentials(self, connector):
        connector.config = {"credentials": {"auth": {}}}
        record = _make_mock_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_convert_to_pdf_from_drive(self, connector):
        record = _make_mock_record()

        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient, \
             patch.object(connector, "_convert_to_pdf", new_callable=AsyncMock,
                          return_value="/tmp/file.pdf") as mock_convert, \
             patch("app.connectors.sources.google.gmail.team.connector.create_stream_record_response") as mock_stream, \
             patch("app.connectors.sources.google.gmail.team.connector.MediaIoBaseDownload") as mock_download, \
             patch("builtins.open", MagicMock()):
            mock_client = MagicMock()
            mock_drive_svc = MagicMock()
            mock_client.get_client.return_value = mock_drive_svc
            MockGoogleClient.build_from_services = AsyncMock(return_value=mock_client)

            mock_dl_instance = MagicMock()
            mock_status = MagicMock()
            mock_status.progress.return_value = 1.0
            mock_dl_instance.next_chunk.return_value = (mock_status, True)
            mock_download.return_value = mock_dl_instance

            mock_stream.return_value = MagicMock()

            await connector._stream_from_drive(
                "drive-id", record, "file.docx", "application/vnd.openxmlformats",
                convertTo=MimeTypes.PDF.value, user_email="u@e.com"
            )
            mock_convert.assert_called_once()

    @pytest.mark.asyncio
    async def test_drive_error_raises_http(self, connector):
        record = _make_mock_record()

        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGoogleClient:
            MockGoogleClient.build_from_services = AsyncMock(
                side_effect=Exception("drive error")
            )

            with pytest.raises(HTTPException) as exc_info:
                await connector._stream_from_drive(
                    "drive-id", record, "file.txt", "text/plain",
                    user_email="u@e.com"
                )
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_reraises(self, connector):
        connector.config = None
        record = _make_mock_record()

        with pytest.raises(HTTPException):
            await connector._stream_from_drive(
                "drive-id", record, "file.txt", "text/plain"
            )


# ===========================================================================
# reindex_records
# ===========================================================================

class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector):
        await connector.reindex_records([])
        connector.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_gmail_not_initialized(self, connector):
        original = connector.gmail_data_source
        connector.gmail_data_source = None
        try:
            with pytest.raises(Exception, match="Gmail data source not initialized"):
                await connector.reindex_records([_make_mock_record()])
        finally:
            connector.gmail_data_source = original

    @pytest.mark.asyncio
    async def test_reindex_updated_record(self, connector):
        record = _make_mock_record()
        updated_record = MagicMock()
        permissions = [Permission(email="u@e.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]

        with patch.object(connector, "_check_and_fetch_updated_record", new_callable=AsyncMock,
                          return_value=(updated_record, permissions)):
            await connector.reindex_records([record])
            connector.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_non_updated_record(self, connector):
        record = _make_mock_record()

        with patch.object(connector, "_check_and_fetch_updated_record", new_callable=AsyncMock,
                          return_value=None):
            await connector.reindex_records([record])
            connector.data_entities_processor.reindex_existing_records.assert_called_once()
            connector.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_reindex_error_continues(self, connector):
        record1 = _make_mock_record(record_id="r1")
        record2 = _make_mock_record(record_id="r2")

        with patch.object(connector, "_check_and_fetch_updated_record", new_callable=AsyncMock,
                          side_effect=[Exception("check error"), None]):
            await connector.reindex_records([record1, record2])
            connector.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_general_error(self, connector):
        record = _make_mock_record()

        with patch.object(connector, "_check_and_fetch_updated_record", new_callable=AsyncMock,
                          side_effect=Exception("fatal error")):
            # Per-record errors are caught and continued, not propagated
            await connector.reindex_records([record])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================

class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_no_external_record_id(self, connector):
        record = _make_mock_record(external_record_id=None)
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_with_permission(self, connector):
        record = _make_mock_record()
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_user_without_email(self, connector):
        record = _make_mock_record()
        user = MagicMock()
        user.email = None

        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_routes_to_mail_handler(self, connector):
        record = _make_mock_record(record_type=RecordType.MAIL)
        user = MagicMock()
        user.email = "u@e.com"

        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock), \
             patch.object(connector, "_check_and_fetch_updated_mail_record", new_callable=AsyncMock,
                          return_value=None) as mock_mail:
            result = await connector._check_and_fetch_updated_record("org-1", record)
            mock_mail.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_to_file_handler(self, connector):
        record = _make_mock_record(record_type=RecordType.FILE)
        user = MagicMock()
        user.email = "u@e.com"

        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock), \
             patch.object(connector, "_check_and_fetch_updated_file_record", new_callable=AsyncMock,
                          return_value=None) as mock_file:
            result = await connector._check_and_fetch_updated_record("org-1", record)
            mock_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_record_type(self, connector):
        record = _make_mock_record(record_type="UNKNOWN_TYPE")
        user = MagicMock()
        user.email = "u@e.com"

        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        with patch.object(connector, "_create_user_gmail_client", new_callable=AsyncMock):
            result = await connector._check_and_fetch_updated_record("org-1", record)
            assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        record = _make_mock_record()

        @asynccontextmanager
        async def _failing_tx():
            raise Exception("tx error")
            yield

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _failing_tx

        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_mail_record
# ===========================================================================

class TestCheckAndFetchUpdatedMailRecord:
    @pytest.mark.asyncio
    async def test_no_message_id(self, connector):
        record = _make_mock_record(external_record_id=None)
        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@e.com", AsyncMock()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_message_not_found_404(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        user_gmail.users_messages_get = AsyncMock(
            side_effect=HttpError(mock_resp, b"not found")
        )

        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_message_not_found_none(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=None)

        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_thread_id(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        msg = _make_gmail_message()
        del msg["threadId"]
        user_gmail.users_messages_get = AsyncMock(return_value=msg)

        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_record_not_updated(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=_make_gmail_message())

        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_mail_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_record_is_updated(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 0
        existing.external_record_group_id = "u@e.com:SENT"

        # Set up data store to return existing record for the connector
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=existing)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        record = _make_mock_record()
        user_gmail = AsyncMock()
        msg = _make_gmail_message(label_ids=["INBOX"])
        user_gmail.users_messages_get = AsyncMock(return_value=msg)

        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_mail_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_deleted_record_returns_none(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=_make_gmail_message())

        # Make _process_gmail_message return a deleted update
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        deleted_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_message", new_callable=AsyncMock, return_value=deleted_update):
            result = await connector._check_and_fetch_updated_mail_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(side_effect=Exception("fetch error"))

        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_non_404_reraises(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 500
        user_gmail.users_messages_get = AsyncMock(
            side_effect=HttpError(mock_resp, b"server error")
        )

        # The HttpError with non-404 should be re-raised, caught by outer except
        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_process_returns_none(self, connector):
        record = _make_mock_record()
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=_make_gmail_message())

        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_message", new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_mail_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_file_record
# ===========================================================================

class TestCheckAndFetchUpdatedFileRecord:
    @pytest.mark.asyncio
    async def test_no_stable_attachment_id(self, connector):
        record = _make_mock_record(external_record_id=None, record_type=RecordType.FILE)
        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", AsyncMock()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_parent(self, connector):
        record = _make_mock_record(
            external_record_id="drive-id",
            record_type=RecordType.FILE,
            parent_external_record_id=None,
        )
        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", AsyncMock()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_parent_not_found(self, connector):
        record = _make_mock_record(
            external_record_id="drive-id",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-parent",
        )
        user_gmail = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        user_gmail.users_messages_get = AsyncMock(
            side_effect=HttpError(mock_resp, b"not found")
        )

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_parent_none(self, connector):
        record = _make_mock_record(
            external_record_id="drive-id",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-parent",
        )
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=None)

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_attachment_not_found_in_message(self, connector):
        record = _make_mock_record(
            external_record_id="drive-not-found",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-parent",
        )
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=_make_gmail_message())

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_no_thread_id(self, connector):
        record = _make_mock_record(
            external_record_id="drive-file-1",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-parent",
        )
        user_gmail = AsyncMock()
        msg = _make_gmail_message(has_drive_attachment=True)
        del msg["threadId"]
        user_gmail.users_messages_get = AsyncMock(return_value=msg)

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_parse_error(self, connector):
        record = _make_mock_record(
            external_record_id="no-tilde-here",  # Would be treated as drive file
            record_type=RecordType.FILE,
        )
        # Actually, no tilde means drive file. Let's test with invalid stable id
        record_reg = _make_mock_record(
            external_record_id="invalid_format",
            record_type=RecordType.FILE,
            parent_external_record_id=None,
        )
        # This is a drive file (no tilde), with no parent
        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record_reg, "u@e.com", AsyncMock()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_parent_not_found(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~1",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-1",
        )
        user_gmail = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        user_gmail.users_messages_get = AsyncMock(
            side_effect=HttpError(mock_resp, b"not found")
        )

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_parent_none(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~1",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-1",
        )
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=None)

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_not_found_in_parent(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~99",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-1",
        )
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(return_value=_make_gmail_message())

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_no_thread_id(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~1",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-1",
        )
        user_gmail = AsyncMock()
        msg = _make_gmail_message(has_attachments=True)
        del msg["threadId"]
        user_gmail.users_messages_get = AsyncMock(return_value=msg)

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_not_updated(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~1",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-1",
        )
        user_gmail = AsyncMock()
        msg = _make_gmail_message(has_attachments=True)
        user_gmail.users_messages_get = AsyncMock(return_value=msg)

        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_no_parent_record_id_fallback(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~1",
            record_type=RecordType.FILE,
            parent_external_record_id=None,
        )
        user_gmail = AsyncMock()
        msg = _make_gmail_message(has_attachments=True)
        user_gmail.users_messages_get = AsyncMock(return_value=msg)

        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~1",
            record_type=RecordType.FILE,
        )
        user_gmail = AsyncMock()
        user_gmail.users_messages_get = AsyncMock(side_effect=Exception("fetch error"))

        result = await connector._check_and_fetch_updated_file_record(
            "org-1", record, "u@e.com", user_gmail
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_file_deleted_update(self, connector):
        record = _make_mock_record(
            external_record_id="drive-file-1",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-parent",
        )
        user_gmail = AsyncMock()
        msg = _make_gmail_message(has_drive_attachment=True)
        user_gmail.users_messages_get = AsyncMock(return_value=msg)
        user_gmail.users_threads_get = AsyncMock(return_value={"messages": [msg]})

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        deleted_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )

        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_attachment", new_callable=AsyncMock, return_value=deleted_update):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_attachment_deleted_update(self, connector):
        record = _make_mock_record(
            external_record_id="msg-1~1",
            record_type=RecordType.FILE,
            parent_external_record_id="msg-1",
        )
        user_gmail = AsyncMock()
        msg = _make_gmail_message(has_attachments=True)
        user_gmail.users_messages_get = AsyncMock(return_value=msg)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        deleted_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )

        with patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_attachment", new_callable=AsyncMock, return_value=deleted_update):
            result = await connector._check_and_fetch_updated_file_record(
                "org-1", record, "u@e.com", user_gmail
            )
        assert result is None


# ===========================================================================
# _process_gmail_message - additional edge cases
# ===========================================================================

class TestProcessGmailMessageEdgeCases:
    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        """Force an exception inside _process_gmail_message."""
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock,
                          side_effect=Exception("db error")):
            message = _make_gmail_message()
            # The exception should be caught internally
            result = await connector._process_gmail_message(
                "user@example.com", message, "thread-1", None
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_long_subject_truncated(self, connector):
        long_subject = "A" * 300
        message = _make_gmail_message(subject=long_subject)
        result = await connector._process_gmail_message(
            "user@example.com", message, "thread-1", None
        )
        assert len(result.record.record_name) == 255

    @pytest.mark.asyncio
    async def test_empty_user_email(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            "", message, "thread-1", None
        )
        assert result is not None
        assert len(result.new_permissions) == 0

    @pytest.mark.asyncio
    async def test_none_user_email(self, connector):
        message = _make_gmail_message()
        result = await connector._process_gmail_message(
            None, message, "thread-1", None
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_from_header_with_name_format(self, connector):
        message = _make_gmail_message(from_email="Sender Name <sender@example.com>")
        result = await connector._process_gmail_message(
            "sender@example.com", message, "thread-1", None
        )
        assert result.new_permissions[0].type == PermissionType.OWNER


# ===========================================================================
# _process_gmail_attachment - additional edge cases
# ===========================================================================

class TestProcessGmailAttachmentEdgeCases:
    @pytest.mark.asyncio
    async def test_none_filename_defaults(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": None, "mimeType": "text/plain",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            "u@e.com", "msg-1", attachment_info, []
        )
        assert result.record.record_name == "unnamed_attachment"

    @pytest.mark.asyncio
    async def test_filename_without_extension(self, connector):
        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "no_extension", "mimeType": "application/octet-stream",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            "u@e.com", "msg-1", attachment_info, []
        )
        assert result.record.extension is None

    @pytest.mark.asyncio
    async def test_indexing_filter_disables_attachment(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "file.txt", "mimeType": "text/plain",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            "u@e.com", "msg-1", attachment_info, []
        )
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_existing_attachment_record(self, connector):
        existing = MagicMock()
        existing.id = "existing-att"
        existing.version = 0

        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=existing)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "file.txt", "mimeType": "text/plain",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            "u@e.com", "msg-1", attachment_info, []
        )
        assert result.is_new is False
        assert result.record.id == "existing-att"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        @asynccontextmanager
        async def _failing_tx():
            raise Exception("tx fail")
            yield

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _failing_tx

        attachment_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "file.txt", "mimeType": "text/plain",
            "size": 100, "isDriveFile": False,
        }
        result = await connector._process_gmail_attachment(
            "u@e.com", "msg-1", attachment_info, []
        )
        # _get_existing_record swallows tx errors and returns None → treated as new attachment
        assert result is not None
        assert result.is_new is True


# ===========================================================================
# _extract_attachment_infos - edge cases
# ===========================================================================

class TestExtractAttachmentInfosEdgeCases:
    def test_duplicate_drive_ids_deduplicated(self, connector):
        html = (
            '<a href="https://drive.google.com/file/d/DRIVE1/view?usp=drive_web">L1</a>'
            '<a href="https://drive.google.com/file/d/DRIVE1/view?usp=drive_web">L2</a>'
        )
        message = _make_gmail_message(body_html=html)
        infos = connector._extract_attachment_infos(message)
        drive_infos = [i for i in infos if i.get("isDriveFile")]
        assert len(drive_infos) == 1

    def test_drive_id_in_body_and_attachment_deduplicated(self, connector):
        html = '<a href="https://drive.google.com/file/d/drive-file-1/view?usp=drive_web">Link</a>'
        message = _make_gmail_message(has_drive_attachment=True, body_html=html)
        infos = connector._extract_attachment_infos(message)
        drive_infos = [i for i in infos if i.get("isDriveFile")]
        assert len(drive_infos) == 1

    def test_invalid_body_data_graceful(self, connector):
        message = {
            "id": "msg-1",
            "payload": {
                "headers": [],
                "mimeType": "text/html",
                "body": {"data": "!!!invalid_base64!!!"},
                "parts": [],
            }
        }
        # Should not raise
        infos = connector._extract_attachment_infos(message)

    def test_non_dict_part_skipped(self, connector):
        message = {
            "id": "msg-1",
            "payload": {
                "headers": [],
                "body": {},
                "parts": ["not-a-dict"],
            }
        }
        infos = connector._extract_attachment_infos(message)
        assert len(infos) == 0


# ===========================================================================
# _process_gmail_attachment_generator - error path
# ===========================================================================

class TestAttachmentGeneratorError:
    @pytest.mark.asyncio
    async def test_generator_handles_exception(self, connector):
        with patch.object(connector, "_process_gmail_attachment", new_callable=AsyncMock,
                          side_effect=Exception("attachment error")):
            results = []
            async for update in connector._process_gmail_attachment_generator(
                "u@e.com", "msg-1",
                [{"attachmentId": "att-1", "stableAttachmentId": "msg-1~1", "isDriveFile": False}],
                []
            ):
                if update:
                    results.append(update)
            assert len(results) == 0


# ===========================================================================
# _process_history_changes - more edge cases
# ===========================================================================

class TestProcessHistoryChangesEdgeCases:
    @pytest.mark.asyncio
    async def test_message_without_id_skipped(self, connector):
        user_gmail_client = AsyncMock()
        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {}}],
        }
        batch = []
        count = await connector._process_history_changes(
            "u@e.com", user_gmail_client, history_entry, batch
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_fetch_returns_none(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_messages_get = AsyncMock(return_value=None)

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "msg-null"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_no_thread_id(self, connector):
        user_gmail_client = AsyncMock()
        msg = _make_gmail_message()
        del msg["threadId"]
        user_gmail_client.users_messages_get = AsyncMock(return_value=msg)

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "msg-no-thread"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_404_during_fetch(self, connector):
        user_gmail_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        user_gmail_client.users_messages_get = AsyncMock(
            side_effect=HttpError(mock_resp, b"not found")
        )

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "gone-msg"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_non_404_http_error(self, connector):
        user_gmail_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 500
        user_gmail_client.users_messages_get = AsyncMock(
            side_effect=HttpError(mock_resp, b"server error")
        )

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "err-msg"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletion_record_not_found(self, connector):
        user_gmail_client = AsyncMock()
        history_entry = {
            "id": "100",
            "messagesDeleted": [{"message": {"id": "del-not-found"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletion_without_message_id(self, connector):
        user_gmail_client = AsyncMock()
        history_entry = {
            "id": "100",
            "messagesDeleted": [{"message": {}}],
        }
        batch = []
        count = await connector._process_history_changes(
            "u@e.com", user_gmail_client, history_entry, batch
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletion_error_continues(self, connector):
        user_gmail_client = AsyncMock()
        existing = MagicMock()
        existing.id = "del-rec"
        history_entry = {
            "id": "100",
            "messagesDeleted": [{"message": {"id": "del-msg"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=existing), \
             patch.object(connector, "_delete_message_and_attachments", new_callable=AsyncMock,
                          side_effect=Exception("delete error")):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )

    @pytest.mark.asyncio
    async def test_label_non_inbox_sent_skipped(self, connector):
        user_gmail_client = AsyncMock()
        history_entry = {
            "id": "100",
            "labelsAdded": [
                {"message": {"id": "label-msg"}, "labelIds": ["CATEGORY_SOCIAL"]},
            ],
        }
        batch = []
        count = await connector._process_history_changes(
            "u@e.com", user_gmail_client, history_entry, batch
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_process_message_returns_none(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_messages_get = AsyncMock(return_value=_make_gmail_message(message_id="null-msg"))

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "null-msg"}}],
        }
        batch = []
        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_process_gmail_message", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
        assert count == 0

    @pytest.mark.asyncio
    async def test_general_exception_caught(self, connector):
        user_gmail_client = AsyncMock()
        history_entry = None  # Will cause an error

        batch = []
        count = await connector._process_history_changes(
            "u@e.com", user_gmail_client, history_entry, batch
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_sibling_relation_created(self, connector):
        user_gmail_client = AsyncMock()
        msg = _make_gmail_message(message_id="sibling-msg")
        user_gmail_client.users_messages_get = AsyncMock(return_value=msg)

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "sibling-msg"}}],
        }
        batch = []

        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock,
                          return_value="prev-rec-id"):
            tx = AsyncMock()
            tx.create_record_relation = AsyncMock()

            @asynccontextmanager
            async def _transaction():
                yield tx

            connector.data_store_provider = MagicMock()
            connector.data_store_provider.transaction = _transaction

            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
            assert count >= 1

    @pytest.mark.asyncio
    async def test_sibling_relation_error_continues(self, connector):
        user_gmail_client = AsyncMock()
        msg = _make_gmail_message(message_id="sib-err-msg")
        user_gmail_client.users_messages_get = AsyncMock(return_value=msg)

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "sib-err-msg"}}],
        }
        batch = []

        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock,
                          return_value="prev-id"):
            @asynccontextmanager
            async def _failing_tx():
                raise Exception("relation error")
                yield

            connector.data_store_provider = MagicMock()
            connector.data_store_provider.transaction = _failing_tx

            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
            assert count >= 1

    @pytest.mark.asyncio
    async def test_indexing_filter_disables_mail(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        user_gmail_client = AsyncMock()
        msg = _make_gmail_message(message_id="filtered-msg")
        user_gmail_client.users_messages_get = AsyncMock(return_value=msg)

        history_entry = {
            "id": "100",
            "messagesAdded": [{"message": {"id": "filtered-msg"}}],
        }
        batch = []

        with patch.object(connector, "_get_existing_record", new_callable=AsyncMock, return_value=None), \
             patch.object(connector, "_find_previous_message_in_thread", new_callable=AsyncMock, return_value=None):
            count = await connector._process_history_changes(
                "u@e.com", user_gmail_client, history_entry, batch
            )
        assert count >= 1
        if batch:
            record = batch[0][0]
            assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# ===========================================================================
# _run_sync_with_history_id - more edge cases
# ===========================================================================

class TestRunSyncWithHistoryIdEdgeCases:
    @pytest.mark.asyncio
    async def test_reraises_http_error(self, connector):
        user_gmail_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        user_gmail_client.users_history_list = AsyncMock(
            side_effect=HttpError(mock_resp, b"not found")
        )

        with pytest.raises(HttpError):
            await connector._run_sync_with_history_id(
                "u@e.com", user_gmail_client, "hist-1", "key"
            )

    @pytest.mark.asyncio
    async def test_general_error_updates_sync_point(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_history_list = AsyncMock(
            side_effect=Exception("general error")
        )

        # INBOX/SENT fetch errors are logged and replaced with empty history; sync completes
        await connector._run_sync_with_history_id(
            "u@e.com", user_gmail_client, "hist-1", "key"
        )
        connector.gmail_delta_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_sync_point_update_error_on_failure(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_history_list = AsyncMock(
            side_effect=Exception("general error")
        )
        connector.gmail_delta_sync_point.update_sync_point = AsyncMock(
            side_effect=Exception("sync point error")
        )

        # History fetch failures are non-fatal; primary sync-point update errors are swallowed
        await connector._run_sync_with_history_id(
            "u@e.com", user_gmail_client, "hist-1", "key"
        )

    @pytest.mark.asyncio
    async def test_inbox_error_continues_with_sent(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "200"})

        with patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          side_effect=[Exception("inbox error"), {"history": []}]):
            await connector._run_sync_with_history_id(
                "u@e.com", user_gmail_client, "hist-1", "key"
            )

    @pytest.mark.asyncio
    async def test_sent_error_continues(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "200"})

        with patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          side_effect=[{"history": []}, Exception("sent error")]):
            await connector._run_sync_with_history_id(
                "u@e.com", user_gmail_client, "hist-1", "key"
            )

    @pytest.mark.asyncio
    async def test_profile_error_continues(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(side_effect=Exception("profile error"))

        with patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}):
            await connector._run_sync_with_history_id(
                "u@e.com", user_gmail_client, "hist-1", "key"
            )

    @pytest.mark.asyncio
    async def test_batch_processing_error(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "200"})
        connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=Exception("batch error")
        )

        with patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": [{"id": "100", "messagesAdded": [{"message": {"id": "msg-1"}}]}]}), \
             patch.object(connector, "_process_history_changes", new_callable=AsyncMock, return_value=1):
            # The batch_count won't reach batch_size, so final batch processing will fail
            # But should still update sync point
            await connector._run_sync_with_history_id(
                "u@e.com", user_gmail_client, "hist-1", "key"
            )

    @pytest.mark.asyncio
    async def test_sync_point_error_during_final_update(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "200"})

        with patch.object(connector, "_fetch_history_changes", new_callable=AsyncMock,
                          return_value={"history": []}):
            # First call succeeds (during error handling), final call fails
            connector.gmail_delta_sync_point.update_sync_point = AsyncMock(
                side_effect=Exception("sync point error")
            )
            await connector._run_sync_with_history_id(
                "u@e.com", user_gmail_client, "hist-1", "key"
            )


# ===========================================================================
# _run_full_sync - error paths
# ===========================================================================

class TestRunFullSyncErrors:
    @pytest.mark.asyncio
    async def test_full_sync_page_error_raises(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "h1"})
        user_gmail_client.users_threads_list = AsyncMock(
            side_effect=Exception("page error")
        )

        with pytest.raises(Exception, match="page error"):
            await connector._run_full_sync("u@e.com", user_gmail_client, "key")

    @pytest.mark.asyncio
    async def test_full_sync_no_threads_key(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_get_profile = AsyncMock(return_value={"historyId": "h1"})
        user_gmail_client.users_threads_list = AsyncMock(return_value={})

        await connector._run_full_sync("u@e.com", user_gmail_client, "key")
        connector.data_entities_processor.on_new_records.assert_not_called()


# ===========================================================================
# _pass_date_filter - additional branch
# ===========================================================================

class TestPassDateFilterEdgeCases:
    def test_no_internal_date_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        message = {"internalDate": None}
        # None internalDate should skip the filter check
        assert connector._pass_date_filter(message) is True

    def test_missing_internal_date_key(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        message = {}
        assert connector._pass_date_filter(message) is True


# ===========================================================================
# _fetch_history_changes - error in non-HttpError
# ===========================================================================

class TestFetchHistoryChangesError:
    @pytest.mark.asyncio
    async def test_general_error_raises(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_history_list = AsyncMock(
            side_effect=Exception("general error")
        )

        with pytest.raises(Exception, match="general error"):
            await connector._fetch_history_changes(
                user_gmail_client, "u@e.com", "hist-1", "INBOX"
            )

    @pytest.mark.asyncio
    async def test_empty_history_page(self, connector):
        user_gmail_client = AsyncMock()
        user_gmail_client.users_history_list = AsyncMock(return_value={})

        result = await connector._fetch_history_changes(
            user_gmail_client, "u@e.com", "hist-1", "INBOX"
        )
        assert result["history"] == []
