"""Tests for app.connectors.sources.microsoft.outlook_individual.connector."""

import base64
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.core.registry.filters import SyncFilterKey
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.microsoft.outlook_individual.connector import (
    OutlookCredentials,
    OutlookIndividualConnector,
)
from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.models.entities import RecordType

@pytest.fixture
def mock_data_entities_processor():
    processor = MagicMock()
    processor.org_id = "org-1"
    processor.get_app_creator_user = AsyncMock()
    processor.on_new_app_users = AsyncMock()
    processor.on_new_record_groups = AsyncMock()
    processor.on_new_records = AsyncMock()
    processor.reindex_existing_records = AsyncMock()
    return processor


@pytest.fixture
def mock_data_store_provider():
    provider = MagicMock()
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    tx.delete_record_by_external_id = AsyncMock()
    provider.transaction.return_value = tx
    return provider


@pytest.fixture
def connector(logger, mock_data_entities_processor, mock_data_store_provider, mock_config_service):
    return OutlookIndividualConnector(
        logger=logger,
        data_entities_processor=mock_data_entities_processor,
        data_store_provider=mock_data_store_provider,
        config_service=mock_config_service,
        connector_id="conn-outlook-individual-1",
        scope="PERSONAL",
        created_by="test-user-id",
    )


class TestInit:
    @pytest.mark.asyncio
    async def test_init_success(self, connector, mock_data_entities_processor, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "scope": "PERSONAL",
                "credentials": {"access_token": "access-token"},
            }
        )
        mock_data_entities_processor.get_app_creator_user = AsyncMock(
            return_value=MagicMock(user_id="u-1", email="user@test.com")
        )
        connector._get_credentials = AsyncMock(
            return_value=OutlookCredentials("tenant", "client", "secret")
        )

        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.MSGraphClientWithDelegatedAuth"
        ) as mock_delegated, patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.ExternalMSGraphClient"
        ) as mock_external, patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.OutlookCalendarContactsDataSource"
        ) as mock_data_source:
            delegated_instance = MagicMock()
            external_instance = MagicMock()
            data_source_instance = MagicMock()
            mock_delegated.return_value = delegated_instance
            mock_external.return_value = external_instance
            mock_data_source.return_value = data_source_instance

            result = await connector.init()

        assert result is True
        assert connector.created_by == "u-1"
        assert connector.creator_email == "user@test.com"
        assert connector.external_client == external_instance
        assert connector.external_outlook_client == data_source_instance

    @pytest.mark.asyncio
    async def test_init_returns_false_when_config_missing(self, connector, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)

        result = await connector.init()

        assert result is False

    @pytest.mark.asyncio
    async def test_init_returns_false_when_access_token_missing(
        self, connector, mock_data_entities_processor, mock_config_service
    ):
        mock_config_service.get_config = AsyncMock(
            return_value={"scope": "PERSONAL", "credentials": {}}
        )
        mock_data_entities_processor.get_app_creator_user = AsyncMock(
            return_value=MagicMock(user_id="u-1", email="user@test.com")
        )
        connector._get_credentials = AsyncMock(
            return_value=OutlookCredentials("tenant", "client", "secret")
        )

        result = await connector.init()

        assert result is False


class TestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_returns_false_when_client_not_initialized(self, connector):
        connector.external_outlook_client = None

        result = await connector.test_connection_and_access()

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_folder_listing_succeeds(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=True, data={"value": []}, error=None)
        )
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)

        result = await connector.test_connection_and_access()

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_folder_listing_fails(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=False, data=None, error="Forbidden")
        )

        result = await connector.test_connection_and_access()

        assert result is False


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_raises_when_client_not_initialized(self, connector):
        connector.external_outlook_client = None

        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            with pytest.raises(Exception, match="not initialized"):
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_happy_path_calls_internal_steps(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.creator_email = "user@test.com"
        connector._sync_users = AsyncMock(return_value=MagicMock(email="user@test.com"))
        connector._process_user_emails = AsyncMock()

        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector.run_sync()

        connector._sync_users.assert_awaited_once()
        connector._process_user_emails.assert_awaited_once()


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_record_mail_returns_streaming_response(self, connector):
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        
        # Mock Message Pydantic object with body.content
        message = MagicMock()
        message.body = MagicMock()
        message.body.content = "<html>hello</html>"
        
        connector._get_message_by_id_external = AsyncMock(return_value=message)
        record = MagicMock(
            record_type=RecordType.MAIL,
            external_record_id="msg-1",
        )

        response = await connector.stream_record(record)

        assert response is not None
        assert response.media_type == "text/html"

    @pytest.mark.asyncio
    async def test_stream_record_file_uses_stream_response_builder(self, connector):
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        connector._download_attachment_external = AsyncMock(return_value=b"file-bytes")
        record = MagicMock(
            id="rec-1",
            record_type=RecordType.FILE,
            external_record_id="att-1",
            parent_external_record_id="msg-1",
            record_name="test.pdf",
            mime_type="application/pdf",
        )

        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.create_stream_record_response"
        ) as mock_create_stream_response:
            mock_create_stream_response.return_value = MagicMock()
            response = await connector.stream_record(record)

        assert response == mock_create_stream_response.return_value
        mock_create_stream_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_record_file_without_parent_raises_http_error(self, connector):
        connector.external_outlook_client = MagicMock()
        record = MagicMock(
            record_type=RecordType.FILE,
            external_record_id="att-1",
            parent_external_record_id=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)

        assert exc_info.value.status_code == 500


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_reindex_records_returns_early_on_empty_records(self, connector):
        await connector.reindex_records([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reindex_records_raises_when_client_not_initialized(self, connector):
        connector.external_outlook_client = None

        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_reindex_records_processes_updated_and_non_updated_records(self, connector):
        connector.external_outlook_client = MagicMock()
        updated_record = MagicMock()
        unchanged_record = MagicMock()
        updated_with_permissions = [(updated_record, [])]
        connector._reindex_user_mailbox_records = AsyncMock(
            return_value=(updated_with_permissions, [unchanged_record])
        )

        await connector.reindex_records([updated_record, unchanged_record])

        connector.data_entities_processor.on_new_records.assert_awaited_once_with(updated_with_permissions)
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with([unchanged_record])

    @pytest.mark.asyncio
    async def test_reindex_records_raises_on_internal_error(self, connector):
        connector.external_outlook_client = MagicMock()
        connector._reindex_user_mailbox_records = AsyncMock(side_effect=Exception("boom"))

        with pytest.raises(Exception, match="boom"):
            await connector.reindex_records([MagicMock()])


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_get_filter_options_folders_returns_paginated_response(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.creator_email = "user@test.com"
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        
        folder = MagicMock(id="f-1", display_name="Inbox")
        data = MagicMock()
        data.value = [folder]
        data.odata_next_link = "next-cursor"
        
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=True, error=None, data=data)
        )

        response = await connector.get_filter_options(
            filter_key=SyncFilterKey.FOLDERS.value,
            page=1,
            limit=20,
            search=None,
            cursor=None,
        )

        assert response.success is True
        assert len(response.options) == 1
        assert response.has_more is True
        assert response.cursor == "next-cursor"

    @pytest.mark.asyncio
    async def test_get_filter_options_uses_cursor_for_next_page(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.creator_email = "user@test.com"
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        
        data = MagicMock()
        data.value = []
        data.odata_next_link = None
        
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=True, error=None, data=data)
        )

        await connector.get_filter_options(
            filter_key=SyncFilterKey.FOLDERS.value,
            page=2,
            limit=10,
            search=None,
            cursor="next-cursor",
        )

        _, kwargs = connector.external_outlook_client.me_list_mail_folders.call_args
        assert kwargs.get("next_url") == "next-cursor"

    @pytest.mark.asyncio
    async def test_get_filter_options_returns_error_when_not_initialized(self, connector):
        connector.external_outlook_client = None

        response = await connector.get_filter_options(
            filter_key=SyncFilterKey.FOLDERS.value,
            page=1,
            limit=20,
            search=None,
            cursor=None,
        )

        assert response.success is False
        assert "not initialized" in (response.message or "").lower()

    @pytest.mark.asyncio
    async def test_get_filter_options_raises_for_unsupported_key(self, connector):
        with pytest.raises(ValueError, match="Unsupported filter key"):
            await connector.get_filter_options("unsupported-key")

    @pytest.mark.asyncio
    async def test_get_filter_options_caps_limit_at_100(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.creator_email = "user@test.com"
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        
        data = MagicMock()
        data.value = []
        data.odata_next_link = None
        
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=True, error=None, data=data)
        )

        response = await connector.get_filter_options(
            filter_key=SyncFilterKey.FOLDERS.value,
            page=1,
            limit=999,
            search=None,
            cursor=None,
        )

        assert response.limit == 100


class TestCleanupAndDelegates:
    @pytest.mark.asyncio
    async def test_cleanup_closes_underlying_client_and_resets_state(self, connector):
        underlying = MagicMock()
        underlying.close = AsyncMock()
        external_client = MagicMock()
        external_client.get_client.return_value = underlying
        connector.external_client = external_client
        connector.external_outlook_client = MagicMock()
        connector.credentials = OutlookCredentials("tenant", "client", "secret")

        await connector.cleanup()

        underlying.close.assert_awaited_once()
        assert connector.external_client is None
        assert connector.external_outlook_client is None
        assert connector.credentials is None

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates_to_run_sync(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_webhook_notification_returns_true(self, connector):
        result = await connector.handle_webhook_notification("org-1", {"id": "notif-1"})
        assert result is True

    def test_get_signed_url_returns_none(self, connector):
        result = connector.get_signed_url(MagicMock())
        assert result is None


class TestDeltaAndMessageProcessing:
    @pytest.mark.asyncio
    async def test_get_all_messages_delta_external_returns_empty_on_exception(self, connector):
        connector.external_outlook_client = None
        result = await connector._get_all_messages_delta_external("folder-1")
        assert result.messages == []
        assert result.delta_link is None

    @pytest.mark.asyncio
    async def test_get_all_messages_delta_external_applies_server_and_client_side_date_filter(self, connector):
        received_filter = MagicMock()
        received_filter.is_empty.return_value = False
        received_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00", "2024-02-01T00:00:00")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = received_filter
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        msg1 = MagicMock(id="msg-1", received_date_time=datetime(2024, 1, 15, tzinfo=timezone.utc))
        msg2 = MagicMock(id="msg-2", received_date_time=datetime(2024, 2, 15, tzinfo=timezone.utc))
        connector.external_outlook_client.fetch_all_messages_delta_me = AsyncMock(
            return_value=([msg1, msg2], "new-delta-link")
        )

        result = await connector._get_all_messages_delta_external("folder-1")

        assert result.delta_link == "new-delta-link"
        assert len(result.messages) == 1
        assert result.messages[0].id == "msg-1"

    @pytest.mark.asyncio
    async def test_process_single_folder_messages_returns_zero_when_no_messages(self, connector):
        connector.email_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        from app.connectors.sources.microsoft.outlook_individual.connector import MessagesDeltaResult
        connector._get_all_messages_delta_external = AsyncMock(
            return_value=MessagesDeltaResult(messages=[], delta_link=None)
        )
        user = MagicMock(email="user@test.com", source_user_id="u-1")
        folder = MagicMock(id="folder-1", display_name="Inbox")

        count, mail_records = await connector._process_single_folder_messages(
            "org-1",
            user,
            folder,
        )

        assert count == 0
        assert mail_records == []

    @pytest.mark.asyncio
    async def test_process_single_folder_messages_processes_updates_and_sync_point(self, connector):
        connector.email_delta_sync_point.read_sync_point = AsyncMock(return_value={"delta_link": "old-link"})
        connector.email_delta_sync_point.update_sync_point = AsyncMock()
        from app.connectors.sources.microsoft.outlook_individual.connector import MessagesDeltaResult
        msg = MagicMock(id="msg-1")
        connector._get_all_messages_delta_external = AsyncMock(
            return_value=MessagesDeltaResult(messages=[msg], delta_link="new-link")
        )
        mail_record = MagicMock(record_type=RecordType.MAIL)
        connector._process_single_message = AsyncMock(
            return_value=([
                RecordUpdate(
                    record=mail_record,
                    is_new=True,
                    is_updated=False,
                    is_deleted=False,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=True,
                    new_permissions=[],
                    external_record_id="msg-1",
                )
            ], True)
        )
        user = MagicMock(email="user@test.com", source_user_id="u-1")
        folder = MagicMock(id="folder-1", display_name="Inbox")

        count, mail_records = await connector._process_single_folder_messages(
            "org-1",
            user,
            folder,
        )

        assert count == 1
        assert len(mail_records) == 1
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.email_delta_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_single_message_handles_deleted_message(self, connector, mock_data_store_provider):
        tx = mock_data_store_provider.transaction.return_value
        user = MagicMock(source_user_id="u-1", email="user@test.com")

        message = MagicMock()
        message.id = "msg-1"
        message.additional_data = {"@removed": {"reason": "deleted"}}

        updates, success = await connector._process_single_message(
            "org-1",
            user,
            message,
            "folder-1",
            "Inbox",
        )

        assert updates == []
        assert success is True
        tx.delete_record_by_external_id.assert_awaited_once_with(
            "conn-outlook-individual-1", "msg-1", "u-1"
        )

    @pytest.mark.asyncio
    async def test_process_single_message_processes_email_and_attachments(self, connector):
        user = MagicMock(source_user_id="u-1", email="user@test.com")
        email_update = MagicMock(record=MagicMock())
        attachment_update = MagicMock(record=MagicMock())
        connector._process_single_email_with_folder = AsyncMock(return_value=email_update)
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector._process_email_attachments_with_folder = AsyncMock(return_value=[attachment_update])

        message = MagicMock()
        message.id = "msg-1"
        message.additional_data = {}
        message.has_attachments = True

        updates, success = await connector._process_single_message(
            "org-1",
            user,
            message,
            "folder-1",
            "Inbox",
        )

        assert updates == [email_update, attachment_update]
        assert success is True

    @pytest.mark.asyncio
    async def test_process_single_email_with_folder_marks_updated_when_etag_changes(self, connector):
        existing = MagicMock(
            id="rec-1",
            version=3,
            external_revision_id="etag-old",
            external_record_group_id="folder-1",
        )
        connector._get_existing_record = AsyncMock(return_value=existing)
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector.indexing_filters = FilterCollection()

        result = await connector._process_single_email_with_folder(
            "org-1",
            "user@test.com",
            MagicMock(
                id="msg-1",
                subject="subject",
                change_key="etag-new",
                created_date_time=datetime.now(timezone.utc),
                last_modified_date_time=datetime.now(timezone.utc),
                web_link="https://x",
                from_=None,
                to_recipients=[],
                cc_recipients=[],
                bcc_recipients=[],
                conversation_id="thread-1",
                internet_message_id="imid-1",
                conversation_index="idx",
            ),
            "folder-1",
        )

        assert result is not None
        assert result.is_new is False
        assert result.is_updated is True
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_process_single_email_with_folder_respects_mail_indexing_filter(self, connector):
        connector._get_existing_record = AsyncMock(return_value=None)
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        result = await connector._process_single_email_with_folder(
            "org-1",
            "user@test.com",
            MagicMock(
                id="msg-1",
                subject="subject",
                change_key="etag",
                created_date_time=datetime.now(timezone.utc),
                last_modified_date_time=datetime.now(timezone.utc),
                web_link="https://x",
                from_=None,
                to_recipients=[],
                cc_recipients=[],
                bcc_recipients=[],
                conversation_id="thread-1",
                internet_message_id="imid-1",
                conversation_index="idx",
            ),
            "folder-1",
        )

        assert result is not None
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_create_attachment_record_skips_when_no_content_type(self, connector):
        attachment = MagicMock()
        attachment.id = "att-1"
        attachment.name = "file"
        attachment.content_type = None
        
        result = await connector._create_attachment_record(
            org_id="org-1",
            attachment=attachment,
            message_id="msg-1",
            folder_id="folder-1",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_create_attachment_record_sets_attachment_indexing_off(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        attachment = MagicMock()
        attachment.id = "att-1"
        attachment.name = "report.pdf"
        attachment.content_type = "application/pdf"
        attachment.size = 123
        attachment.last_modified_date_time = datetime.now(timezone.utc)
        attachment.is_inline = False
        
        record = await connector._create_attachment_record(
            org_id="org-1",
            attachment=attachment,
            message_id="msg-1",
            folder_id="folder-1",
            parent_weburl="https://outlook/message",
        )

        assert record is not None
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert record.extension == "pdf"

    @pytest.mark.asyncio
    async def test_process_email_attachments_with_folder_skips_filtered_attachments(self, connector):
        attachment = MagicMock(id="att-1")
        connector._get_message_attachments_external = AsyncMock(return_value=[attachment])
        connector._get_existing_record = AsyncMock(return_value=None)
        connector._create_attachment_record = AsyncMock(return_value=None)
        user = MagicMock(email="user@test.com")
        message = MagicMock(id="msg-1", web_link="https://x")

        updates = await connector._process_email_attachments_with_folder(
            "org-1", user, message, [], "folder-1", "Inbox"
        )

        assert updates == []

    @pytest.mark.asyncio
    async def test_get_message_attachments_external_returns_empty_on_api_failure(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_messages_list_attachments = AsyncMock(
            return_value=MagicMock(success=False, error="fail", data=None)
        )

        attachments = await connector._get_message_attachments_external("msg-1")
        assert attachments == []


class TestFolderAndThreadProcessing:
    @pytest.mark.asyncio
    async def test_sync_users_creates_creator_app_user(self, connector):
        connector.created_by = "u-1"
        connector.creator_email = "first.last@test.com"

        user = await connector._sync_users()

        assert user.email == "first.last@test.com"
        assert user.source_user_id == "u-1"
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_users_raises_when_creator_missing(self, connector):
        connector.created_by = None
        connector.creator_email = None
        with pytest.raises(ValueError, match="required"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_process_user_emails_handles_no_folders(self, connector):
        user = MagicMock(email="user@test.com")
        connector._sync_user_folders = AsyncMock(return_value=[])
        result = await connector._process_user_emails("org-1", user)
        assert "No folders found" in result

    @pytest.mark.asyncio
    async def test_process_user_emails_aggregates_folder_results(self, connector):
        user = MagicMock(email="user@test.com")
        folder1 = MagicMock(id="f-1", display_name="Inbox")
        folder2 = MagicMock(id="f-2", display_name="Archive")
        connector._sync_user_folders = AsyncMock(return_value=[folder1, folder2])
        connector._process_single_folder_messages = AsyncMock(
            side_effect=[(2, [MagicMock()]), (3, [MagicMock()])]
        )
        connector._create_all_thread_edges_for_user = AsyncMock(return_value=1)

        result = await connector._process_user_emails("org-1", user)

        assert "Processed 5 items across 2 folders" in result

    @pytest.mark.asyncio
    async def test_get_all_folders_for_user_handles_pagination_and_in_filter(self, connector):
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        
        folder_a = MagicMock(id="a", display_name="Inbox", child_folder_count=0)
        folder_b = MagicMock(id="b", display_name="Archive", child_folder_count=0)
        
        data1 = MagicMock()
        data1.value = [folder_a]
        data1.odata_next_link = "next"
        
        data2 = MagicMock()
        data2.value = [folder_b]
        data2.odata_next_link = None
        
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            side_effect=[
                MagicMock(success=True, data=data1),
                MagicMock(success=True, data=data2),
            ]
        )
        connector._get_child_folders_recursive = AsyncMock(return_value=[])

        folders, top_level_ids = await connector._get_all_folders_for_user(
            selected_folder_ids=["a"],
            filter_operator=MagicMock(value="in"),
        )

        assert len(folders) == 1
        assert folders[0].id == "a"

    @pytest.mark.asyncio
    async def test_get_all_folders_for_user_not_in_filter_excludes_selected(self, connector):
        connector.external_outlook_client = MagicMock()
        
        folder_a = MagicMock(id="a", display_name="Inbox", child_folder_count=0)
        data = MagicMock()
        data.value = [folder_a]
        data.odata_next_link = None
        
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=True, data=data)
        )
        connector._get_child_folders_recursive = AsyncMock(return_value=[])

        folders, top_level_ids = await connector._get_all_folders_for_user(
            selected_folder_ids=["a"],
            filter_operator=MagicMock(value="not_in"),
        )

        # Folder "a" should be excluded with not_in filter
        assert len(folders) == 0

    def test_transform_folder_to_record_group_returns_none_without_id(self, connector):
        user = MagicMock(email="user@test.com")
        assert connector._transform_folder_to_record_group({}, user) is None

    @pytest.mark.asyncio
    async def test_sync_user_folders_pushes_record_groups(self, connector):
        user = MagicMock(email="user@test.com")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = None
        
        folder = MagicMock(id="f-1", display_name="Inbox", parent_folder_id=None)
        top_level_ids = {"f-1"}
        connector._get_all_folders_for_user = AsyncMock(
            return_value=([folder], top_level_ids)
        )

        folders = await connector._sync_user_folders(user)

        assert len(folders) == 1
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_find_parent_by_conversation_index_returns_none_for_root(self, connector):
        # 22-byte root index
        import base64

        root = base64.b64encode(b"A" * 22).decode("utf-8")
        parent = await connector._find_parent_by_conversation_index_from_db(
            root,
            "thread-1",
            "org-1",
            MagicMock(source_user_id="u-1"),
        )

        assert parent is None

    @pytest.mark.asyncio
    async def test_create_all_thread_edges_for_user_creates_edges(self, connector, mock_data_store_provider):
        record = MagicMock(conversation_index="idx", thread_id="thread-1", id="child-1")
        connector._find_parent_by_conversation_index_from_db = AsyncMock(return_value="parent-1")
        mock_data_store_provider.transaction.return_value.batch_create_edges = AsyncMock()
        user = MagicMock(email="user@test.com", source_user_id="u-1")

        created = await connector._create_all_thread_edges_for_user("org-1", user, [record])

        assert created == 1
        mock_data_store_provider.transaction.return_value.batch_create_edges.assert_awaited_once()


class TestReindexInternalsAndCredentials:
    @pytest.mark.asyncio
    async def test_reindex_user_mailbox_records_returns_empty_for_empty_input(self, connector):
        updated, non_updated = await connector._reindex_user_mailbox_records([])
        assert updated == []
        assert non_updated == []

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_record_routes_by_type(self, connector):
        connector._check_and_fetch_updated_email = AsyncMock(return_value=("email-rec", []))
        connector._check_and_fetch_updated_attachment = AsyncMock(return_value=("file-rec", []))

        email_record = MagicMock(record_type=RecordType.MAIL)
        file_record = MagicMock(record_type=RecordType.FILE)

        email_result = await connector._check_and_fetch_updated_record("org-1", "u@test.com", email_record)
        file_result = await connector._check_and_fetch_updated_record("org-1", "u@test.com", file_record)

        assert email_result == ("email-rec", [])
        assert file_result == ("file-rec", [])

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_email_returns_none_when_unchanged(self, connector):
        record = MagicMock(external_record_id="msg-1", external_record_group_id="folder-1")
        connector._get_message_by_id_external = AsyncMock(return_value={"id": "msg-1"})
        connector._process_single_email_with_folder = AsyncMock(
            return_value=MagicMock(is_new=False, is_updated=False, record=MagicMock())
        )

        result = await connector._check_and_fetch_updated_email("org-1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_attachment_returns_none_without_parent(self, connector):
        record = MagicMock(external_record_id="att-1", parent_external_record_id=None)
        result = await connector._check_and_fetch_updated_attachment("org-1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_attachment_returns_tuple_when_changed(self, connector):
        record = MagicMock(
            external_record_id="att-1",
            parent_external_record_id="msg-1",
            external_record_group_id="folder-1",
            external_revision_id="old-etag",
        )
        connector._get_message_by_id_external = AsyncMock(return_value=MagicMock(id="msg-1", web_link="https://x"))
        connector._get_message_attachments_external = AsyncMock(
            return_value=[MagicMock(id="att-1", last_modified_date_time=datetime.now(timezone.utc), content_type="application/pdf")]
        )
        connector._extract_email_permissions = AsyncMock(return_value=[])
        connector._create_attachment_record = AsyncMock(return_value=MagicMock())

        result = await connector._check_and_fetch_updated_attachment("org-1", "u@test.com", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_credentials_success(self, connector, mock_config_service):
        config = {"auth": {"oauthConfigId": "oauth-1"}}
        mock_config_service.get_config = AsyncMock(return_value=config)
        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.fetch_oauth_config_by_id",
            new=AsyncMock(
                return_value={
                    "config": {
                        "tenantId": "tenant",
                        "clientId": "client",
                        "clientSecret": "secret",
                    }
                }
            ),
        ):
            creds = await connector._get_credentials("conn-outlook-individual-1", config)

        assert creds.tenant_id == "tenant"
        assert creds.client_id == "client"
        assert creds.client_secret == "secret"

    @pytest.mark.asyncio
    async def test_get_credentials_raises_when_oauth_config_id_missing(self, connector, mock_config_service):
        config = {"auth": {}}
        mock_config_service.get_config = AsyncMock(return_value=config)
        with pytest.raises(ValueError, match="oauthConfigId"):
            await connector._get_credentials("conn-outlook-individual-1", config)

    @pytest.mark.asyncio
    async def test_get_credentials_raises_when_oauth_config_incomplete(self, connector, mock_config_service):
        config = {"auth": {"oauthConfigId": "oauth-1"}}
        mock_config_service.get_config = AsyncMock(return_value=config)
        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.fetch_oauth_config_by_id",
            new=AsyncMock(return_value={"config": {"tenantId": "tenant"}}),
        ):
            with pytest.raises(ValueError, match="Incomplete Outlook Personal credentials"):
                await connector._get_credentials("conn-outlook-individual-1", config)


class TestUtilityMethods:
    def test_safe_get_attr_from_object_and_dict(self, connector):
        # _safe_get_attr method was removed - test removed
        pass

    def test_extract_email_from_recipient_variants(self, connector):
        # Recipient Pydantic object with email_address (snake_case)
        recipient = MagicMock()
        recipient.email_address = MagicMock(address="test@example.com")
        
        # Recipient with email_address but no address attribute
        recipient_no_address = MagicMock()
        recipient_no_address.email_address = MagicMock(spec=[])
        
        assert connector._extract_email_from_recipient(recipient) == "test@example.com"
        assert connector._extract_email_from_recipient(recipient_no_address) == ""
        assert connector._extract_email_from_recipient(None) == ""

    def test_get_mime_type_enum_maps_known_and_unknown(self, connector):
        assert connector._get_mime_type_enum("application/pdf") == MimeTypes.PDF
        assert connector._get_mime_type_enum("application/unknown-type") == MimeTypes.BIN

    def test_parse_datetime_and_format_datetime_string(self, connector):
        # _parse_datetime was replaced with datetime_to_epoch_ms utility - test removed
        # _format_datetime_string was also removed from individual connector - test skipped
        pass

    def test_augment_email_html_with_metadata(self, connector):
        record = MagicMock(
            from_email="sender@test.com",
            to_emails=["to@test.com"],
            cc_emails=[],
            bcc_emails=[],
            subject="Subject",
        )
        html_content = connector._augment_email_html_with_metadata("<p>body</p>", record)
        assert "email-metadata" in html_content
        assert "From: sender@test.com" in html_content

    def test_is_descendant_of(self, connector):
        folder_parent_map = {"child": "parent", "grandchild": "child"}
        assert connector._is_descendant_of("grandchild", {"parent"}, folder_parent_map) is True
        assert connector._is_descendant_of("orphan", {"parent"}, folder_parent_map) is False


class TestApiHelpersAndFactory:
    @pytest.mark.asyncio
    async def test_get_message_by_id_external_success_and_failure(self, connector):
        connector.external_outlook_client = MagicMock()
        msg_data = MagicMock(id="m1")
        connector.external_outlook_client.me_get_message = AsyncMock(
            side_effect=[
                MagicMock(success=True, data=msg_data, error=None),
                MagicMock(success=False, data=None, error="not-found"),
            ]
        )

        found = await connector._get_message_by_id_external("m1")
        missing = await connector._get_message_by_id_external("m2")

        assert found.id == "m1"
        assert missing is None

    @pytest.mark.asyncio
    async def test_download_attachment_external_success_and_empty(self, connector):
        connector.external_outlook_client = MagicMock()
        b64 = base64.b64encode(b"hello").decode("utf-8")
        connector.external_outlook_client.me_messages_get_attachments = AsyncMock(
            side_effect=[
                MagicMock(success=True, data={"content_bytes": b64}),
                MagicMock(success=False, data=None),
            ]
        )

        downloaded = await connector._download_attachment_external("m1", "a1")
        missing = await connector._download_attachment_external("m1", "a2")

        assert downloaded == b"hello"
        assert missing == b""

    @pytest.mark.asyncio
    async def test_get_child_folders_recursive_handles_no_children_and_errors(self, connector):
        connector.external_outlook_client = MagicMock()
        parent_no_children = MagicMock(id="p1", display_name="Parent", child_folder_count=0)
        no_children = await connector._get_child_folders_recursive(parent_no_children)
        assert no_children == []

        connector.external_outlook_client.me_mail_folders_list_child_folders = AsyncMock(
            return_value=MagicMock(success=False, data=None, error="boom")
        )
        parent_with_children = MagicMock(id="p1", display_name="Parent", child_folder_count=1)
        errored = await connector._get_child_folders_recursive(parent_with_children)
        assert errored == []

    @pytest.mark.asyncio
    async def test_create_connector_factory_initializes_data_processor(self):
        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.DataSourceEntitiesProcessor"
        ) as mock_processor_cls:
            processor = MagicMock()
            processor.initialize = AsyncMock()
            processor.org_id = "org-1"
            mock_processor_cls.return_value = processor

            connector = await OutlookIndividualConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=MagicMock(),
                connector_id="conn-factory-1",
                scope="PERSONAL",
                created_by="test-user-id",
            )

        processor.initialize.assert_awaited_once()
        assert isinstance(connector, OutlookIndividualConnector)


class TestAdditionalBranchCoverage:
    @pytest.mark.asyncio
    async def test_get_all_folders_for_user_returns_all_on_unknown_operator(self, connector):
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        
        folder = MagicMock(id="f-1", display_name="Inbox", child_folder_count=0)
        data = MagicMock()
        data.value = [folder]
        data.odata_next_link = None
        
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=True, data=data)
        )
        connector._get_child_folders_recursive = AsyncMock(return_value=[])

        folders, top_level_ids = await connector._get_all_folders_for_user(
            selected_folder_ids=["f-1"],
            filter_operator=MagicMock(value="UNKNOWN"),
            display_name_filter="In",
        )

        assert len(folders) == 1
        _, kwargs = connector.external_outlook_client.me_list_mail_folders.call_args
        assert "startsWith(displayName, 'In')" in kwargs.get("filter", "")

    @pytest.mark.asyncio
    async def test_sync_user_folders_applies_folder_filter_values(self, connector):
        filter_obj = MagicMock()
        filter_obj.is_empty.return_value = False
        filter_obj.get_value.return_value = ["f-1"]
        filter_obj.get_operator.return_value = MagicMock(value="IN")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filter_obj
        
        folder = MagicMock(id="f-1", display_name="Inbox", parent_folder_id=None)
        top_level_ids = {"f-1"}
        connector._get_all_folders_for_user = AsyncMock(
            return_value=([folder], top_level_ids)
        )

        user = MagicMock(email="u@test.com")
        await connector._sync_user_folders(user)

        _, kwargs = connector._get_all_folders_for_user.call_args
        assert kwargs["selected_folder_ids"] == ["f-1"]

    @pytest.mark.asyncio
    async def test_find_parent_by_conversation_index_returns_none_on_db_error(
        self, connector, mock_data_store_provider
    ):
        tx = mock_data_store_provider.transaction.return_value
        tx.get_record_by_conversation_index = AsyncMock(side_effect=Exception("db down"))
        non_root = base64.b64encode(b"A" * 27).decode("utf-8")

        result = await connector._find_parent_by_conversation_index_from_db(
            non_root, "thread-1", "org-1", MagicMock(source_user_id="u-1")
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_create_all_thread_edges_for_user_returns_zero_on_batch_write_error(
        self, connector, mock_data_store_provider
    ):
        connector._find_parent_by_conversation_index_from_db = AsyncMock(return_value="parent-1")
        mock_data_store_provider.transaction.return_value.batch_create_edges = AsyncMock(
            side_effect=Exception("write failed")
        )
        user = MagicMock(email="u@test.com", source_user_id="u-1")
        record = MagicMock(conversation_index="idx", thread_id="t-1", id="child-1")

        created = await connector._create_all_thread_edges_for_user("org-1", user, [record])
        assert created == 0

    @pytest.mark.asyncio
    async def test_process_email_attachments_with_folder_marks_updated_flags(self, connector):
        existing = MagicMock(
            id="rec-1",
            version=1,
            external_revision_id="old-etag",
            external_record_group_id="old-folder",
        )
        connector._get_message_attachments_external = AsyncMock(
            return_value=[MagicMock(id="att-1", last_modified_date_time=datetime.now(timezone.utc), content_type="application/pdf")]
        )
        connector._get_existing_record = AsyncMock(return_value=existing)
        connector._create_attachment_record = AsyncMock(return_value=MagicMock())

        user = MagicMock(email="u@test.com")
        message = MagicMock(id="msg-1", web_link="https://x")
        updates = await connector._process_email_attachments_with_folder(
            "org-1",
            user,
            message,
            [],
            "new-folder",
            "Inbox",
        )

        assert len(updates) == 1
        assert updates[0].is_updated is True
        assert updates[0].content_changed is True
        assert updates[0].metadata_changed is True

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_record_returns_none_for_unsupported_type(self, connector):
        unsupported = MagicMock(record_type="UNSUPPORTED")
        result = await connector._check_and_fetch_updated_record("org-1", "u@test.com", unsupported)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_email_returns_none_when_not_found(self, connector):
        connector._get_message_by_id_external = AsyncMock(return_value={})
        record = MagicMock(external_record_id="msg-404", external_record_group_id="folder-1")

        result = await connector._check_and_fetch_updated_email("org-1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_attachment_returns_none_when_parent_not_found(self, connector):
        connector._get_message_by_id_external = AsyncMock(return_value={})
        record = MagicMock(external_record_id="att-1", parent_external_record_id="msg-404")

        result = await connector._check_and_fetch_updated_attachment("org-1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_attachment_returns_none_when_attachment_missing(self, connector):
        connector._get_message_by_id_external = AsyncMock(return_value={"id": "msg-1"})
        connector._get_message_attachments_external = AsyncMock(return_value=[])
        record = MagicMock(external_record_id="att-missing", parent_external_record_id="msg-1")

        result = await connector._check_and_fetch_updated_attachment("org-1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_attachment_returns_none_when_unchanged(self, connector):
        connector._get_message_by_id_external = AsyncMock(return_value=MagicMock(id="msg-1"))
        att_mock = MagicMock(id="att-1", last_modified_date_time=datetime(2024, 1, 1, tzinfo=timezone.utc))
        connector._get_message_attachments_external = AsyncMock(return_value=[att_mock])
        record = MagicMock(
            external_record_id="att-1",
            parent_external_record_id="msg-1",
            external_revision_id="same",
            external_record_group_id="folder-1",
        )

        result = await connector._check_and_fetch_updated_attachment("org-1", "u@test.com", record)
        assert result is None


class TestCoverageBoostBranches:
    @pytest.mark.asyncio
    async def test_test_connection_and_access_handles_exception(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(side_effect=Exception("network"))
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_get_credentials_oauth_not_found_raises(self, connector, mock_config_service):
        config = {"auth": {"oauthConfigId": "x"}}
        mock_config_service.get_config = AsyncMock(return_value=config)
        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.fetch_oauth_config_by_id",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(ValueError, match="not found"):
                await connector._get_credentials("conn-outlook-individual-1", config)

    @pytest.mark.asyncio
    async def test_process_user_emails_handles_folder_processing_exception(self, connector):
        user = MagicMock(email="user@test.com")
        connector._sync_user_folders = AsyncMock(
            return_value=[{"id": "f1", "display_name": "Inbox"}]
        )
        connector._process_single_folder_messages = AsyncMock(side_effect=Exception("bad folder"))
        connector._create_all_thread_edges_for_user = AsyncMock(return_value=0)

        result = await connector._process_user_emails("org-1", user)
        assert "Failed" in result

    @pytest.mark.asyncio
    async def test_get_child_folders_recursive_covers_conversion_branches(self, connector):
        class WithModelDump:
            def __init__(self):
                self.id = "c1"
                self.display_name = "Child1"
                self.child_folder_count = 0

            def model_dump(self):
                return {"id": self.id, "display_name": self.display_name, "child_folder_count": 0}

        class WithDict:
            def __init__(self):
                self.id = "c2"
                self.display_name = "Child2"
                self.child_folder_count = 0

            def dict(self):
                return {"id": self.id, "display_name": self.display_name, "child_folder_count": 0}

        class WithDunder:
            def __init__(self):
                self.id = "c3"
                self.display_name = "Child3"
                self.child_folder_count = 0

        connector.external_outlook_client = MagicMock()
        data = MagicMock()
        data.value = [WithModelDump(), WithDict(), WithDunder()]
        data.odata_next_link = None
        connector.external_outlook_client.me_mail_folders_list_child_folders = AsyncMock(
            return_value=MagicMock(success=True, data=data)
        )

        parent = MagicMock(id="p1", display_name="Parent", child_folder_count=3)
        result = await connector._get_child_folders_recursive(parent)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_all_folders_for_user_returns_empty_on_api_failure(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=False, error="nope", data=None)
        )
        folders, top_level_ids = await connector._get_all_folders_for_user()
        assert folders == []
        assert top_level_ids == set()

    @pytest.mark.asyncio
    async def test_get_all_folders_for_user_returns_empty_on_exception(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(side_effect=Exception("boom"))
        folders, top_level_ids = await connector._get_all_folders_for_user()
        assert folders == []
        assert top_level_ids == set()

    def test_transform_folder_to_record_group_handles_exception(self, connector):
        bad_folder = object()
        user = MagicMock(email="u@test.com")
        assert connector._transform_folder_to_record_group(bad_folder, user) is None

    @pytest.mark.asyncio
    async def test_sync_user_folders_when_all_transforms_fail(self, connector):
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = None
        
        folder_no_id = MagicMock(display_name="NoId", id=None)
        connector._get_all_folders_for_user = AsyncMock(return_value=([folder_no_id], set()))
        user = MagicMock(email="u@test.com")
        result = await connector._sync_user_folders(user)
        assert result == [folder_no_id]
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_single_folder_messages_covers_batch_boundaries(self, connector):
        connector.email_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.email_delta_sync_point.update_sync_point = AsyncMock()
        from app.connectors.sources.microsoft.outlook_individual.connector import MessagesDeltaResult
        msg1 = MagicMock(id="m1")
        msg2 = MagicMock(id="m2")
        connector._get_all_messages_delta_external = AsyncMock(
            return_value=MessagesDeltaResult(messages=[msg1, msg2], delta_link="d")
        )

        updates = [
            RecordUpdate(
                record=MagicMock(record_type=RecordType.FILE),
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                new_permissions=[],
                external_record_id="x1",
            ),
            RecordUpdate(
                record=MagicMock(record_type=RecordType.MAIL),
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                new_permissions=[],
                external_record_id="x2",
            ),
        ]
        connector._process_single_message = AsyncMock(side_effect=[(updates[:1], True), (updates[1:], True)])
        user = MagicMock(email="u@test.com")
        folder = MagicMock(id="f1", display_name="Inbox")

        processed, mails = await connector._process_single_folder_messages(
            "org-1", user, folder
        )
        assert processed == 2
        assert len(mails) == 1

    @pytest.mark.asyncio
    async def test_process_single_folder_messages_handles_exception(self, connector):
        connector.email_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector._get_all_messages_delta_external = AsyncMock(side_effect=Exception("delta error"))
        user = MagicMock(email="u@test.com")
        folder = MagicMock(id="f1", display_name="Inbox")
        count, records = await connector._process_single_folder_messages(
            "org-1", user, folder
        )
        assert count == 0 and records == []

    @pytest.mark.asyncio
    async def test_process_single_message_skips_attachments_when_email_not_updated(self, connector):
        connector._process_single_email_with_folder = AsyncMock(return_value=None)
        user = MagicMock(source_user_id="u1", email="u@test.com")
        
        message = MagicMock()
        message.id = "m1"
        message.additional_data = {}
        message.has_attachments = True
        
        updates, success = await connector._process_single_message(
            "org-1",
            user,
            message,
            "f1",
            "Inbox",
        )
        assert updates == []
        assert success is True

    @pytest.mark.asyncio
    async def test_extract_email_permissions_error_path(self, connector):
        # Force constructor to fail inside method try/except
        with patch(
            "app.connectors.sources.microsoft.outlook_individual.connector.Permission",
            side_effect=Exception("perm fail"),
        ):
            result = await connector._extract_email_permissions({}, None, "u@test.com")
        assert result == []

    @pytest.mark.asyncio
    async def test_process_email_attachments_with_folder_handles_exception(self, connector):
        connector._get_message_attachments_external = AsyncMock(side_effect=Exception("att fail"))
        user = MagicMock(email="u@test.com")
        message = MagicMock(id="m1", web_link="x")
        updates = await connector._process_email_attachments_with_folder(
            "org-1", user, message, [], "f1", "Inbox"
        )
        assert updates == []

    @pytest.mark.asyncio
    async def test_get_message_attachments_external_handles_all_conversion_paths(self, connector):
        # Connector now expects Pydantic Attachment objects directly (no conversion)
        a = MagicMock(id="a")
        b = MagicMock(id="b")
        c = MagicMock(id="c")
        d = MagicMock(id="d")

        connector.external_outlook_client = MagicMock()
        data = MagicMock()
        data.value = [a, b, d, c]
        connector.external_outlook_client.me_messages_list_attachments = AsyncMock(
            return_value=MagicMock(success=True, data=data, error=None)
        )
        result = await connector._get_message_attachments_external("m1")
        assert [x.id for x in result] == ["a", "b", "d", "c"]

    @pytest.mark.asyncio
    async def test_get_existing_record_handles_exception(self, connector, mock_data_store_provider):
        mock_data_store_provider.transaction.return_value.get_record_by_external_id = AsyncMock(
            side_effect=Exception("db")
        )
        assert await connector._get_existing_record("org", "ext") is None

    @pytest.mark.asyncio
    async def test_stream_record_unsupported_type_raises_http_500(self, connector):
        connector.external_outlook_client = MagicMock()
        record = MagicMock(record_type="UNKNOWN")
        with pytest.raises(HTTPException) as exc:
            await connector.stream_record(record)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_record_fails_when_client_not_initialized(self, connector):
        connector.external_outlook_client = None
        with pytest.raises(HTTPException) as exc:
            await connector.stream_record(MagicMock(record_type=RecordType.MAIL))
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_get_message_by_id_external_conversion_and_error_paths(self, connector):
        # Connector now expects Pydantic Message objects directly (no conversion)
        m1 = MagicMock(id="m")
        m2 = MagicMock(id="d")
        m3 = MagicMock(id="x")

        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_get_message = AsyncMock(
            side_effect=[
                MagicMock(success=True, data=m1, error=None),
                MagicMock(success=True, data=m2, error=None),
                MagicMock(success=True, data=m3, error=None),
                MagicMock(success=True, data=None, error=None),
                MagicMock(success=False, data=None, error="e"),
            ]
        )
        assert (await connector._get_message_by_id_external("1")).id == "m"
        assert (await connector._get_message_by_id_external("2")).id == "d"
        assert (await connector._get_message_by_id_external("3")).id == "x"
        assert await connector._get_message_by_id_external("4") is None
        assert await connector._get_message_by_id_external("5") is None

    @pytest.mark.asyncio
    async def test_download_attachment_external_additional_paths(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_messages_get_attachments = AsyncMock(
            side_effect=[
                MagicMock(success=True, data={"contentBytes": base64.b64encode(b"x").decode("utf-8")}),
                MagicMock(success=True, data={"content_bytes": None}),
            ]
        )
        assert await connector._download_attachment_external("m", "a") == b"x"
        assert await connector._download_attachment_external("m", "b") == b""

    @pytest.mark.asyncio
    async def test_cleanup_additional_branches(self, connector):
        # No close method branch
        client_no_close = MagicMock()
        connector.external_client = MagicMock(get_client=MagicMock(return_value=client_no_close))
        await connector.cleanup()
        assert connector.external_client is None

        # close raises branch
        bad_underlying = MagicMock(close=AsyncMock(side_effect=Exception("close fail")))
        connector.external_client = MagicMock(get_client=MagicMock(return_value=bad_underlying))
        await connector.cleanup()
        assert connector.external_client is None

    @pytest.mark.asyncio
    async def test_reindex_user_mailbox_records_mixed_results(self, connector):
        connector.creator_email = "u@test.com"
        rec1, rec2 = MagicMock(id="1"), MagicMock(id="2")
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=[("r1", []), None])
        updated, unchanged = await connector._reindex_user_mailbox_records([rec1, rec2])
        assert updated == [("r1", [])]
        assert unchanged == [rec2]

    @pytest.mark.asyncio
    async def test_reindex_user_mailbox_records_handles_inner_exception(self, connector):
        connector.creator_email = "u@test.com"
        rec1 = MagicMock(id="1")
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("bad record"))
        updated, unchanged = await connector._reindex_user_mailbox_records([rec1])
        assert updated == []
        assert unchanged == []

    @pytest.mark.asyncio
    async def test_check_and_fetch_updated_record_exception_returns_none(self, connector):
        rec = MagicMock(id="r1", record_type=RecordType.MAIL)
        connector._check_and_fetch_updated_email = AsyncMock(side_effect=Exception("oops"))
        assert await connector._check_and_fetch_updated_record("org", "u@test.com", rec) is None

    @pytest.mark.asyncio
    async def test_get_folder_options_additional_paths(self, connector):
        # not initialized
        connector.external_outlook_client = None
        resp = await connector._get_folder_options(page=1, limit=20, search=None)
        assert resp.success is False

        # creator missing
        connector.external_outlook_client = MagicMock()
        connector.creator_email = None
        resp = await connector._get_folder_options(page=1, limit=20, search=None)
        assert resp.success is False

        # api failure
        connector.creator_email = "u@test.com"
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=False, error="fail", data=None)
        )
        resp = await connector._get_folder_options(page=1, limit=20, search=None)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_folder_options_conversion_and_cursor(self, connector):
        # Connector now expects Pydantic MailFolder objects directly (no conversion)
        a = MagicMock(id="a", display_name="A")
        b = MagicMock(id="b", display_name="B")
        c = MagicMock(id="c", display_name="C")
        d = MagicMock(id="d", display_name="D")

        connector.external_outlook_client = MagicMock()
        connector.creator_email = "u@test.com"
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        
        data = MagicMock()
        data.value = [a, b, d, c]
        data.odata_next_link = "next"
        
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(
            return_value=MagicMock(success=True, error=None, data=data)
        )
        resp = await connector._get_folder_options(page=2, limit=250, search=" In ", cursor="next-in")
        assert resp.success is True
        assert resp.has_more is True
        assert resp.limit == 100
        assert [o.id for o in resp.options] == ["a", "b", "d", "c"]

    @pytest.mark.asyncio
    async def test_get_folder_options_exception_path(self, connector):
        connector.external_outlook_client = MagicMock()
        connector.creator_email = "u@test.com"
        connector.external_outlook_client.me_list_mail_folders = AsyncMock(side_effect=Exception("boom"))
        resp = await connector._get_folder_options(page=1, limit=20, search=None)
        assert resp.success is False


class TestCoverageBoostDeltaAndFolders:
    @pytest.mark.asyncio
    async def test_get_all_messages_delta_external_conversion_paths(self, connector):
        # Connector now expects Pydantic Message objects directly (no conversion)
        a = MagicMock(id="a")
        b = MagicMock(id="b")
        c = MagicMock(id="c")
        d = MagicMock(id="d")

        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = None
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        connector.external_outlook_client.fetch_all_messages_delta_me = AsyncMock(
            return_value=([a, b, d, c], "dlink")
        )

        result = await connector._get_all_messages_delta_external("f1")
        assert [m.id for m in result.messages] == ["a", "b", "d", "c"]
        assert result.delta_link == "dlink"

    @pytest.mark.asyncio
    async def test_get_all_messages_delta_external_client_side_filter_variants(self, connector):
        filter_obj = MagicMock()
        filter_obj.is_empty.return_value = False
        filter_obj.get_datetime_iso.return_value = (None, "2024-02-01T00:00:00")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filter_obj
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        m1 = MagicMock(id="m1", received_date_time=None)
        m2 = MagicMock(id="m2", received_date_time=datetime(2024, 1, 1, 0, 0, 0))  # naive
        m3 = MagicMock(id="m3", received_date_time="not-datetime")
        m4 = MagicMock(id="m4", received_date_time=datetime(2024, 3, 1, tzinfo=timezone.utc))
        
        connector.external_outlook_client.fetch_all_messages_delta_me = AsyncMock(
            return_value=([m1, m2, m3, m4], "dl")
        )

        result = await connector._get_all_messages_delta_external("f1")
        # m4 should be filtered out as after cutoff; others remain
        assert [m.id for m in result.messages] == ["m1", "m2", "m3"]

    @pytest.mark.asyncio
    async def test_get_all_messages_delta_external_server_side_ge_filter(self, connector):
        filter_obj = MagicMock()
        filter_obj.is_empty.return_value = False
        filter_obj.get_datetime_iso.return_value = ("2024-01-01T00:00:00", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filter_obj
        connector.external_outlook_client = MagicMock()
        connector._get_fresh_graph_client = AsyncMock(return_value=connector.external_outlook_client)
        connector.external_outlook_client.fetch_all_messages_delta_me = AsyncMock(
            return_value=([], "dl")
        )

        await connector._get_all_messages_delta_external("f1")
        _, kwargs = connector.external_outlook_client.fetch_all_messages_delta_me.call_args
        assert "receivedDateTime ge 2024-01-01T00:00:00Z" == kwargs["filter"]

    @pytest.mark.asyncio
    async def test_get_child_folders_recursive_additional_paths(self, connector):
        # parent without id
        parent_no_id = MagicMock(display_name="x", id=None)
        assert await connector._get_child_folders_recursive(parent_no_id) == []

        # external client not initialized branch
        parent_with_id = MagicMock(id="p", display_name="x", child_folder_count=1)
        assert await connector._get_child_folders_recursive(parent_with_id) == []

        # API success but no values
        connector.external_outlook_client = MagicMock()
        data = MagicMock()
        data.value = []
        data.odata_next_link = None
        connector.external_outlook_client.me_mail_folders_list_child_folders = AsyncMock(
            return_value=MagicMock(success=True, data=data)
        )
        assert await connector._get_child_folders_recursive(parent_with_id) == []

    @pytest.mark.asyncio
    async def test_get_message_by_id_external_not_initialized_and_exception(self, connector):
        connector.external_outlook_client = None
        assert await connector._get_message_by_id_external("m1") is None

        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_get_message = AsyncMock(side_effect=Exception("boom"))
        assert await connector._get_message_by_id_external("m2") is None

    @pytest.mark.asyncio
    async def test_download_attachment_external_not_initialized_and_exception(self, connector):
        connector.external_outlook_client = None
        assert await connector._download_attachment_external("m1", "a1") == b""

        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_messages_get_attachments = AsyncMock(
            side_effect=Exception("boom")
        )
        assert await connector._download_attachment_external("m1", "a1") == b""

    @pytest.mark.asyncio
    async def test_get_message_attachments_external_not_initialized_and_exception(self, connector):
        connector.external_outlook_client = None
        assert await connector._get_message_attachments_external("m1") == []

        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.me_messages_list_attachments = AsyncMock(
            side_effect=Exception("boom")
        )
        assert await connector._get_message_attachments_external("m1") == []

