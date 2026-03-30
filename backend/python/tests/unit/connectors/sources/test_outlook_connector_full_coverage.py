"""Comprehensive tests for app.connectors.sources.microsoft.outlook.connector."""

import base64
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.sources.microsoft.outlook.connector import (
    STANDARD_OUTLOOK_FOLDERS,
    THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH,
    OutlookConnector,
    OutlookCredentials,
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


def _make_mock_deps():
    logger = logging.getLogger("test.outlook.full")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-outlook-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_user_group_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_updated_record_permissions = AsyncMock()
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    data_entities_processor.reindex_existing_records = AsyncMock()

    data_store_provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_conversation_index = AsyncMock(return_value=None)
    mock_tx.batch_create_edges = AsyncMock()
    mock_tx.get_record_owner_source_user_email = AsyncMock(return_value=None)
    mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.delete_record_by_external_id = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return OutlookConnector(logger, dep, dsp, cs, "conn-outlook-1", "team", "test-user-id")


def _make_graph_response(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    return resp


def _make_user(email="user@test.com", source_user_id="su1", user_id="uid1"):
    user = MagicMock()
    user.email = email
    user.source_user_id = source_user_id
    user.user_id = user_id
    user.full_name = "Test User"
    return user


def _make_mail_record(**kwargs):
    defaults = dict(
        id=str(uuid.uuid4()),
        org_id="org-outlook-1",
        record_name="Test Email",
        record_type=RecordType.MAIL,
        external_record_id="ext-mail-1",
        external_revision_id="etag-1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.OUTLOOK,
        connector_id="conn-outlook-1",
        source_created_at=None,
        source_updated_at=None,
        weburl="https://outlook.com/mail/1",
        mime_type=MimeTypes.HTML.value,
        external_record_group_id="folder-1",
        record_group_type=RecordGroupType.MAILBOX,
        subject="Test Email",
        from_email="sender@test.com",
        to_emails=["to@test.com"],
        cc_emails=[],
        bcc_emails=[],
        thread_id="conv-1",
        is_parent=False,
        internet_message_id="imid-1",
        conversation_index="ci-1",
    )
    defaults.update(kwargs)
    return MailRecord(**defaults)


def _make_file_record(**kwargs):
    defaults = dict(
        id=str(uuid.uuid4()),
        org_id="org-outlook-1",
        record_name="attachment.pdf",
        record_type=RecordType.FILE,
        external_record_id="ext-att-1",
        external_revision_id="att-etag-1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.OUTLOOK,
        connector_id="conn-outlook-1",
        source_created_at=None,
        source_updated_at=None,
        mime_type=MimeTypes.PDF,
        parent_external_record_id="ext-mail-1",
        parent_record_type=RecordType.MAIL,
        external_record_group_id="folder-1",
        record_group_type=RecordGroupType.MAILBOX,
        weburl="https://outlook.com/mail/1",
        is_file=True,
        size_in_bytes=1024,
        extension="pdf",
    )
    defaults.update(kwargs)
    return FileRecord(**defaults)


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_stream_group_mail_success(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id="group-1",
            thread_id="thread-1",
            external_record_id="post-1",
        )

        body_obj = MagicMock()
        body_obj.content = "<p>Group post body</p>"
        post_data = MagicMock()
        post_data.body = body_obj

        connector.external_outlook_client.groups_threads_get_post = AsyncMock(
            return_value=_make_graph_response(success=True, data=post_data)
        )

        resp = await connector.stream_record(record)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_stream_group_mail_no_group_id_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id=None,
            thread_id="thread-1",
        )

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500
        assert "Missing group_id" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_stream_group_mail_no_thread_id_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id="group-1",
            thread_id=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500
        assert "Missing thread_id" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_stream_group_mail_api_failure_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id="group-1",
            thread_id="thread-1",
            external_record_id="post-1",
        )

        connector.external_outlook_client.groups_threads_get_post = AsyncMock(
            return_value=_make_graph_response(success=False, error="Not found")
        )

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_group_post_attachment(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        parent_record = MagicMock()
        parent_record.record_type = RecordType.GROUP_MAIL
        parent_record.external_record_group_id = "group-1"
        parent_record.thread_id = "thread-1"

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=parent_record)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = _make_file_record(
            external_record_group_id="group-1",
            parent_external_record_id="post-1",
            record_group_type=RecordGroupType.GROUP_MAILBOX,
        )

        connector._download_group_post_attachment = AsyncMock(return_value=b"file data")

        with patch("app.connectors.sources.microsoft.outlook.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            resp = await connector.stream_record(record)
            assert resp is not None

    @pytest.mark.asyncio
    async def test_stream_user_mail_success(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_tx = MagicMock()
        mock_tx.get_record_owner_source_user_email = AsyncMock(return_value="user@test.com")
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        connector._get_user_id_from_email = AsyncMock(return_value="su1")

        body_obj = MagicMock()
        body_obj.content = "<p>Email body</p>"
        msg_data = MagicMock()
        msg_data.body = body_obj

        connector._get_message_by_id_external = AsyncMock(return_value=msg_data)

        record = _make_mail_record()

        resp = await connector.stream_record(record)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_stream_user_mail_no_user_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_tx = MagicMock()
        mock_tx.get_record_owner_source_user_email = AsyncMock(return_value=None)
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = _make_mail_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_user_attachment(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_tx = MagicMock()
        mock_tx.get_record_owner_source_user_email = AsyncMock(return_value="user@test.com")
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        connector._get_user_id_from_email = AsyncMock(return_value="su1")
        connector._download_attachment_external = AsyncMock(return_value=b"pdf bytes")

        record = _make_file_record()

        with patch("app.connectors.sources.microsoft.outlook.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            resp = await connector.stream_record(record)
            assert resp is not None

    @pytest.mark.asyncio
    async def test_stream_file_no_parent_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_tx = MagicMock()
        mock_tx.get_record_owner_source_user_email = AsyncMock(return_value="user@test.com")
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        connector._get_user_id_from_email = AsyncMock(return_value="su1")

        record = _make_file_record(parent_external_record_id=None)

        with pytest.raises(HTTPException):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_no_client_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        record = _make_mail_record()

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_unsupported_record_type_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_tx = MagicMock()
        mock_tx.get_record_owner_source_user_email = AsyncMock(return_value="user@test.com")
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        connector._get_user_id_from_email = AsyncMock(return_value="su1")

        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"
        record.external_record_group_id = None
        record.parent_external_record_id = None
        record.id = "rec-1"

        with pytest.raises(HTTPException):
            await connector.stream_record(record)


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_no_clients_raises(self):
        connector = _make_connector()
        connector.external_outlook_client = None
        connector.external_users_client = None

        record = _make_mail_record()
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([record])

    @pytest.mark.asyncio
    async def test_reindex_user_mail_records(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        connector._populate_user_cache = AsyncMock()

        record = _make_mail_record()

        connector._reindex_user_mailbox_records = AsyncMock(
            return_value=([(record, [])], [])
        )
        connector._reindex_group_mailbox_records = AsyncMock(
            return_value=([], [])
        )

        await connector.reindex_records([record])

        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_group_mail_records(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        connector._populate_user_cache = AsyncMock()

        record = _make_mail_record(record_type=RecordType.GROUP_MAIL)

        connector._reindex_user_mailbox_records = AsyncMock(
            return_value=([], [])
        )
        connector._reindex_group_mailbox_records = AsyncMock(
            return_value=([], [record])
        )

        await connector.reindex_records([record])

        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_file_with_group_mail_parent(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        connector._populate_user_cache = AsyncMock()

        parent_record = MagicMock()
        parent_record.record_type = RecordType.GROUP_MAIL

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=parent_record)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        file_record = _make_file_record()

        connector._reindex_user_mailbox_records = AsyncMock(return_value=([], []))
        connector._reindex_group_mailbox_records = AsyncMock(return_value=([], []))

        await connector.reindex_records([file_record])

        connector._reindex_group_mailbox_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_combined_updated_and_non_updated(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        connector._populate_user_cache = AsyncMock()

        updated_record = _make_mail_record()
        non_updated_record = _make_mail_record(external_record_id="ext-2")

        connector._reindex_user_mailbox_records = AsyncMock(
            return_value=([(updated_record, [])], [non_updated_record])
        )
        connector._reindex_group_mailbox_records = AsyncMock(
            return_value=([], [])
        )

        await connector.reindex_records([updated_record, non_updated_record])

        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()


# ===========================================================================
# _reindex_user_mailbox_records
# ===========================================================================


class TestReindexUserMailboxRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        connector = _make_connector()
        updated, non_updated = await connector._reindex_user_mailbox_records([])
        assert updated == []
        assert non_updated == []

    @pytest.mark.asyncio
    async def test_groups_by_owner_email(self):
        connector = _make_connector()

        mock_tx = MagicMock()
        mock_tx.get_record_owner_source_user_email = AsyncMock(return_value="user@test.com")
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = _make_mail_record()
        connector._reindex_single_user_records = AsyncMock(return_value=([], [record]))

        updated, non_updated = await connector._reindex_user_mailbox_records([record])
        assert len(non_updated) == 1

    @pytest.mark.asyncio
    async def test_skips_records_without_owner(self):
        connector = _make_connector()

        mock_tx = MagicMock()
        mock_tx.get_record_owner_source_user_email = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = _make_mail_record()
        updated, non_updated = await connector._reindex_user_mailbox_records([record])
        assert updated == []
        assert non_updated == []


# ===========================================================================
# _reindex_single_user_records
# ===========================================================================


class TestReindexSingleUserRecords:

    @pytest.mark.asyncio
    async def test_no_user_id_returns_all_as_non_updated(self):
        connector = _make_connector()
        connector._get_user_id_from_email = AsyncMock(return_value=None)

        record = _make_mail_record()
        updated, non_updated = await connector._reindex_single_user_records("user@test.com", [record])
        assert updated == []
        assert non_updated == [record]

    @pytest.mark.asyncio
    async def test_record_updated_at_source(self):
        connector = _make_connector()
        connector._get_user_id_from_email = AsyncMock(return_value="su1")

        record = _make_mail_record()
        updated_record = _make_mail_record()
        perm = Permission(email="user@test.com", type=PermissionType.OWNER, entity_type=EntityType.USER)

        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(updated_record, [perm])
        )

        updated, non_updated = await connector._reindex_single_user_records("user@test.com", [record])
        assert len(updated) == 1
        assert len(non_updated) == 0

    @pytest.mark.asyncio
    async def test_record_not_updated_at_source(self):
        connector = _make_connector()
        connector._get_user_id_from_email = AsyncMock(return_value="su1")

        record = _make_mail_record()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)

        updated, non_updated = await connector._reindex_single_user_records("user@test.com", [record])
        assert len(updated) == 0
        assert len(non_updated) == 1

    @pytest.mark.asyncio
    async def test_record_check_error_continues(self):
        connector = _make_connector()
        connector._get_user_id_from_email = AsyncMock(return_value="su1")

        record1 = _make_mail_record()
        record2 = _make_mail_record(external_record_id="ext-2")

        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[Exception("API error"), None]
        )

        updated, non_updated = await connector._reindex_single_user_records("user@test.com", [record1, record2])
        assert len(non_updated) == 1


# ===========================================================================
# _reindex_group_mailbox_records
# ===========================================================================


class TestReindexGroupMailboxRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        connector = _make_connector()
        updated, non_updated = await connector._reindex_group_mailbox_records([])
        assert updated == []
        assert non_updated == []

    @pytest.mark.asyncio
    async def test_updated_group_record(self):
        connector = _make_connector()

        record = _make_mail_record(record_type=RecordType.GROUP_MAIL)
        updated_record = _make_mail_record(record_type=RecordType.GROUP_MAIL)
        perm = Permission(external_id="g1", type=PermissionType.READ, entity_type=EntityType.GROUP)

        connector._check_and_fetch_updated_group_mail_record = AsyncMock(
            return_value=(updated_record, [perm])
        )

        updated, non_updated = await connector._reindex_group_mailbox_records([record])
        assert len(updated) == 1

    @pytest.mark.asyncio
    async def test_non_updated_group_record(self):
        connector = _make_connector()

        record = _make_mail_record(record_type=RecordType.GROUP_MAIL)
        connector._check_and_fetch_updated_group_mail_record = AsyncMock(return_value=None)

        updated, non_updated = await connector._reindex_group_mailbox_records([record])
        assert len(non_updated) == 1

    @pytest.mark.asyncio
    async def test_error_continues(self):
        connector = _make_connector()

        record = _make_mail_record(record_type=RecordType.GROUP_MAIL)
        connector._check_and_fetch_updated_group_mail_record = AsyncMock(
            side_effect=Exception("API error")
        )

        updated, non_updated = await connector._reindex_group_mailbox_records([record])
        assert len(updated) == 0
        assert len(non_updated) == 0


# ===========================================================================
# _check_and_fetch_updated_group_mail_record
# ===========================================================================


class TestCheckAndFetchUpdatedGroupMailRecord:

    @pytest.mark.asyncio
    async def test_group_mail_delegates_to_group_post(self):
        connector = _make_connector()
        record = _make_mail_record(record_type=RecordType.GROUP_MAIL)

        connector._check_and_fetch_updated_group_post = AsyncMock(return_value=None)

        result = await connector._check_and_fetch_updated_group_mail_record("org-1", record)
        connector._check_and_fetch_updated_group_post.assert_awaited_once()
        assert result is None

    @pytest.mark.asyncio
    async def test_file_delegates_to_group_post_attachment(self):
        connector = _make_connector()
        record = _make_file_record(record_type=RecordType.FILE)

        connector._check_and_fetch_updated_group_post_attachment = AsyncMock(return_value=None)

        result = await connector._check_and_fetch_updated_group_mail_record("org-1", record)
        connector._check_and_fetch_updated_group_post_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unexpected_record_type(self):
        connector = _make_connector()
        record = MagicMock()
        record.record_type = "UNEXPECTED"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_group_mail_record("org-1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_group_post
# ===========================================================================


class TestCheckAndFetchUpdatedGroupPost:

    @pytest.mark.asyncio
    async def test_missing_group_id_returns_none(self):
        connector = _make_connector()
        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id=None,
        )

        result = await connector._check_and_fetch_updated_group_post("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_thread_id_returns_none(self):
        connector = _make_connector()
        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id="g1",
            thread_id=None,
        )

        result = await connector._check_and_fetch_updated_group_post("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure_returns_none(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_threads_get_post = AsyncMock(
            return_value=_make_graph_response(success=False, error="Not found")
        )

        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id="g1",
            thread_id="t1",
        )

        result = await connector._check_and_fetch_updated_group_post("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_thread_not_found_returns_none(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_threads_get_post = AsyncMock(
            return_value=_make_graph_response(success=True, data=MagicMock())
        )
        connector._get_group_threads = AsyncMock(return_value=[])

        record = _make_mail_record(
            record_type=RecordType.GROUP_MAIL,
            external_record_group_id="g1",
            thread_id="t1",
        )

        result = await connector._check_and_fetch_updated_group_post("org-1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_group_post_attachment
# ===========================================================================


class TestCheckAndFetchUpdatedGroupPostAttachment:

    @pytest.mark.asyncio
    async def test_no_parent_post_id(self):
        connector = _make_connector()
        record = _make_file_record(parent_external_record_id=None)

        result = await connector._check_and_fetch_updated_group_post_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_parent_not_group_mail(self):
        connector = _make_connector()

        parent = MagicMock()
        parent.record_type = RecordType.MAIL

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=parent)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_group_post_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_attachment_not_found_returns_none(self):
        connector = _make_connector()

        parent = MagicMock()
        parent.record_type = RecordType.GROUP_MAIL
        parent.external_record_group_id = "g1"
        parent.thread_id = "t1"

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=parent)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        connector._get_group_post_attachments = AsyncMock(return_value=[])

        record = _make_file_record(
            external_record_group_id="g1",
            parent_external_record_id="post-1",
        )

        result = await connector._check_and_fetch_updated_group_post_attachment("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_attachment_not_updated_returns_none(self):
        connector = _make_connector()

        parent = MagicMock()
        parent.record_type = RecordType.GROUP_MAIL
        parent.external_record_group_id = "g1"
        parent.thread_id = "t1"

        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=parent)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        att = MagicMock()
        att.id = "ext-att-1"
        att.last_modified_date_time = None
        att.name = "file.pdf"
        att.content_type = "application/pdf"
        att.size = 1024

        connector._get_group_post_attachments = AsyncMock(return_value=[att])

        record = _make_file_record(
            external_record_group_id="g1",
            parent_external_record_id="post-1",
            source_updated_at=None,
        )

        result = await connector._check_and_fetch_updated_group_post_attachment("org-1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:

    @pytest.mark.asyncio
    async def test_mail_delegates_to_email(self):
        connector = _make_connector()
        record = _make_mail_record()
        connector._check_and_fetch_updated_email = AsyncMock(return_value=None)

        result = await connector._check_and_fetch_updated_record("org-1", "su1", "u@test.com", record)
        connector._check_and_fetch_updated_email.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file_delegates_to_attachment(self):
        connector = _make_connector()
        record = _make_file_record()
        connector._check_and_fetch_updated_attachment = AsyncMock(return_value=None)

        result = await connector._check_and_fetch_updated_record("org-1", "su1", "u@test.com", record)
        connector._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        connector = _make_connector()
        record = MagicMock()
        record.record_type = "UNSUPPORTED"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record("org-1", "su1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        record = _make_mail_record()
        connector._check_and_fetch_updated_email = AsyncMock(side_effect=Exception("API error"))

        result = await connector._check_and_fetch_updated_record("org-1", "su1", "u@test.com", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_email
# ===========================================================================


class TestCheckAndFetchUpdatedEmail:

    @pytest.mark.asyncio
    async def test_email_not_found_at_source(self):
        connector = _make_connector()
        connector._get_message_by_id_external = AsyncMock(return_value={})

        record = _make_mail_record()
        result = await connector._check_and_fetch_updated_email("org-1", "su1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_email_not_updated(self):
        connector = _make_connector()

        msg = MagicMock()
        msg.id = "ext-mail-1"
        msg.subject = "Test"
        msg.e_tag = "etag-1"
        msg.created_date_time = None
        msg.last_modified_date_time = None
        msg.web_link = ""
        msg.from_ = None
        msg.to_recipients = []
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.conversation_id = "conv-1"
        msg.internet_message_id = ""
        msg.conversation_index = ""

        connector._get_message_by_id_external = AsyncMock(return_value=msg)

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[], external_record_id="ext-mail-1",
        )
        connector._process_single_email_with_folder = AsyncMock(return_value=mock_update)

        record = _make_mail_record()
        result = await connector._check_and_fetch_updated_email("org-1", "su1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_email_updated_returns_tuple(self):
        connector = _make_connector()

        msg = MagicMock()
        connector._get_message_by_id_external = AsyncMock(return_value=msg)

        updated_record = _make_mail_record()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_update = RecordUpdate(
            record=updated_record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=True, permissions_changed=True,
            new_permissions=[Permission(email="u@test.com", type=PermissionType.OWNER, entity_type=EntityType.USER)],
            external_record_id="ext-mail-1",
        )
        connector._process_single_email_with_folder = AsyncMock(return_value=mock_update)

        record = _make_mail_record()
        result = await connector._check_and_fetch_updated_email("org-1", "su1", "u@test.com", record)
        assert result is not None
        assert len(result) == 2


# ===========================================================================
# _check_and_fetch_updated_attachment
# ===========================================================================


class TestCheckAndFetchUpdatedAttachment:

    @pytest.mark.asyncio
    async def test_no_parent_message_id(self):
        connector = _make_connector()
        record = _make_file_record(parent_external_record_id=None)

        result = await connector._check_and_fetch_updated_attachment("org-1", "su1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_parent_message_not_found(self):
        connector = _make_connector()
        connector._get_message_by_id_external = AsyncMock(return_value={})

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_attachment("org-1", "su1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_attachment_not_found_in_message(self):
        connector = _make_connector()
        connector._get_message_by_id_external = AsyncMock(return_value=MagicMock())
        connector._get_message_attachments_external = AsyncMock(return_value=[])

        record = _make_file_record()
        result = await connector._check_and_fetch_updated_attachment("org-1", "su1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_attachment_not_updated(self):
        connector = _make_connector()
        connector._get_message_by_id_external = AsyncMock(return_value=MagicMock())

        att = MagicMock()
        att.id = "ext-att-1"
        att.e_tag = "att-etag-1"
        connector._get_message_attachments_external = AsyncMock(return_value=[att])

        record = _make_file_record(external_revision_id="att-etag-1")
        result = await connector._check_and_fetch_updated_attachment("org-1", "su1", "u@test.com", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_attachment_updated(self):
        connector = _make_connector()
        msg = MagicMock()
        msg.web_link = "https://outlook.com/mail/1"
        connector._get_message_by_id_external = AsyncMock(return_value=msg)

        att = MagicMock()
        att.id = "ext-att-1"
        att.e_tag = "new-etag"
        att.name = "file.pdf"
        att.content_type = "application/pdf"
        att.last_modified_date_time = None
        att.size = 2048
        connector._get_message_attachments_external = AsyncMock(return_value=[att])
        connector._extract_email_permissions = AsyncMock(return_value=[])

        mock_attachment_record = _make_file_record()
        connector._create_attachment_record = AsyncMock(return_value=mock_attachment_record)

        record = _make_file_record(external_revision_id="old-etag")
        result = await connector._check_and_fetch_updated_attachment("org-1", "su1", "u@test.com", record)
        assert result is not None
        assert len(result) == 2


# ===========================================================================
# _get_message_by_id_external
# ===========================================================================


class TestGetMessageByIdExternal:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        mock_data = {"id": "m1", "subject": "Test"}
        connector.external_outlook_client.users_get_messages = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._get_message_by_id_external("su1", "m1")
        assert result == mock_data

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.users_get_messages = AsyncMock(
            return_value=_make_graph_response(success=False, error="Not found")
        )

        result = await connector._get_message_by_id_external("su1", "m1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._get_message_by_id_external("su1", "m1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.users_get_messages = AsyncMock(
            side_effect=Exception("Network error")
        )

        result = await connector._get_message_by_id_external("su1", "m1")
        assert result == {}


# ===========================================================================
# _download_attachment_external
# ===========================================================================


class TestDownloadAttachmentExternal:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        content = b"PDF content"
        b64 = base64.b64encode(content).decode()
        mock_data = MagicMock()
        mock_data.content_bytes = b64
        mock_data.contentBytes = None

        connector.external_outlook_client.users_messages_get_attachments = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._download_attachment_external("su1", "m1", "a1")
        assert result == content

    @pytest.mark.asyncio
    async def test_fallback_to_contentBytes(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        content = b"File data"
        b64 = base64.b64encode(content).decode()
        mock_data = MagicMock()
        mock_data.content_bytes = None
        mock_data.contentBytes = b64

        connector.external_outlook_client.users_messages_get_attachments = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._download_attachment_external("su1", "m1", "a1")
        assert result == content

    @pytest.mark.asyncio
    async def test_failure_returns_empty_bytes(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.users_messages_get_attachments = AsyncMock(
            return_value=_make_graph_response(success=False)
        )

        result = await connector._download_attachment_external("su1", "m1", "a1")
        assert result == b''

    @pytest.mark.asyncio
    async def test_no_content_returns_empty_bytes(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_data = MagicMock()
        mock_data.content_bytes = None
        mock_data.contentBytes = None

        connector.external_outlook_client.users_messages_get_attachments = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._download_attachment_external("su1", "m1", "a1")
        assert result == b''

    @pytest.mark.asyncio
    async def test_no_client(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._download_attachment_external("su1", "m1", "a1")
        assert result == b''


# ===========================================================================
# _get_message_attachments_external
# ===========================================================================


class TestGetMessageAttachmentsExternal:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_data = MagicMock()
        mock_data.value = [{"id": "a1"}, {"id": "a2"}]

        connector.external_outlook_client.users_messages_list_attachments = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._get_message_attachments_external("su1", "m1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.users_messages_list_attachments = AsyncMock(
            return_value=_make_graph_response(success=False, error="Err")
        )

        result = await connector._get_message_attachments_external("su1", "m1")
        assert result == []


# ===========================================================================
# handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    @pytest.mark.asyncio
    async def test_returns_true(self):
        connector = _make_connector()
        result = await connector.handle_webhook_notification("org-1", {"type": "update"})
        assert result is True


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_clears_resources(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_users_client = MagicMock()
        connector.credentials = MagicMock()
        connector._user_cache = {"a@b.com": "id1"}
        connector._user_cache_timestamp = 12345

        mock_underlying = MagicMock()
        mock_underlying.close = AsyncMock()
        mock_external = MagicMock()
        mock_external.get_client.return_value = mock_underlying
        connector.external_client = mock_external

        await connector.cleanup()

        assert connector.external_outlook_client is None
        assert connector.external_users_client is None
        assert connector.credentials is None
        assert connector._user_cache == {}
        assert connector._user_cache_timestamp is None

    @pytest.mark.asyncio
    async def test_cleanup_handles_close_error(self):
        connector = _make_connector()
        mock_underlying = MagicMock()
        mock_underlying.close = AsyncMock(side_effect=Exception("Close failed"))
        mock_external = MagicMock()
        mock_external.get_client.return_value = mock_underlying
        connector.external_client = mock_external

        await connector.cleanup()
        assert connector.external_client is None

    @pytest.mark.asyncio
    async def test_cleanup_no_external_client(self):
        connector = _make_connector()
        await connector.cleanup()


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    def test_returns_none(self):
        connector = _make_connector()
        result = connector.get_signed_url(MagicMock())
        assert result is None


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:

    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("some_key")


# ===========================================================================
# _extract_email_from_recipient
# ===========================================================================


class TestExtractEmailFromRecipient:

    def test_none_returns_empty(self):
        connector = _make_connector()
        assert connector._extract_email_from_recipient(None) == ''

    def test_email_address_attribute(self):
        connector = _make_connector()
        recipient = MagicMock()
        email_addr = MagicMock()
        email_addr.address = "user@test.com"
        recipient.email_address = email_addr
        recipient.emailAddress = None

        result = connector._extract_email_from_recipient(recipient)
        assert result == "user@test.com"

    def test_emailAddress_fallback(self):
        connector = _make_connector()
        recipient = MagicMock(spec=[])
        email_addr = MagicMock()
        email_addr.address = "user@test.com"
        recipient.emailAddress = email_addr

        result = connector._extract_email_from_recipient(recipient)
        assert result == "user@test.com"

    def test_fallback_to_string(self):
        connector = _make_connector()
        recipient = MagicMock(spec=[])

        result = connector._extract_email_from_recipient(recipient)
        assert isinstance(result, str)


# ===========================================================================
# _get_mime_type_enum
# ===========================================================================


class TestGetMimeTypeEnum:

    def test_known_types(self):
        connector = _make_connector()
        assert connector._get_mime_type_enum("text/plain") == MimeTypes.PLAIN_TEXT
        assert connector._get_mime_type_enum("text/html") == MimeTypes.HTML
        assert connector._get_mime_type_enum("text/csv") == MimeTypes.CSV
        assert connector._get_mime_type_enum("application/pdf") == MimeTypes.PDF
        assert connector._get_mime_type_enum("application/msword") == MimeTypes.DOC
        assert connector._get_mime_type_enum("application/vnd.openxmlformats-officedocument.wordprocessingml.document") == MimeTypes.DOCX
        assert connector._get_mime_type_enum("application/vnd.ms-excel") == MimeTypes.XLS
        assert connector._get_mime_type_enum("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") == MimeTypes.XLSX
        assert connector._get_mime_type_enum("application/vnd.ms-powerpoint") == MimeTypes.PPT
        assert connector._get_mime_type_enum("application/vnd.openxmlformats-officedocument.presentationml.presentation") == MimeTypes.PPTX

    def test_unknown_type_returns_bin(self):
        connector = _make_connector()
        assert connector._get_mime_type_enum("application/octet-stream") == MimeTypes.BIN

    def test_case_insensitive(self):
        connector = _make_connector()
        assert connector._get_mime_type_enum("TEXT/HTML") == MimeTypes.HTML


# ===========================================================================
# _parse_datetime
# ===========================================================================


class TestParseDatetime:

    def test_none_returns_none(self):
        connector = _make_connector()
        assert connector._parse_datetime(None) is None

    def test_datetime_object(self):
        connector = _make_connector()
        dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert result == int(dt.timestamp() * 1000)

    def test_iso_string(self):
        connector = _make_connector()
        result = connector._parse_datetime("2024-06-15T12:00:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_invalid_string_returns_none(self):
        connector = _make_connector()
        assert connector._parse_datetime("not-a-date") is None

    def test_empty_string_returns_none(self):
        connector = _make_connector()
        assert connector._parse_datetime("") is None


# ===========================================================================
# _format_datetime_string
# ===========================================================================


class TestFormatDatetimeString:

    def test_none_returns_empty(self):
        connector = _make_connector()
        assert connector._format_datetime_string(None) == ""

    def test_string_returned_as_is(self):
        connector = _make_connector()
        assert connector._format_datetime_string("2024-06-15T12:00:00Z") == "2024-06-15T12:00:00Z"

    def test_datetime_to_isoformat(self):
        connector = _make_connector()
        dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = connector._format_datetime_string(dt)
        assert "2024-06-15" in result


# ===========================================================================
# _construct_group_mail_weburl
# ===========================================================================


class TestConstructGroupMailWeburl:

    @pytest.mark.asyncio
    async def test_from_cache(self):
        connector = _make_connector()
        connector._group_cache = {
            "g1": {"mail": "eng@contoso.com", "mailNickname": "eng"}
        }

        result = await connector._construct_group_mail_weburl("g1")
        assert result == "https://outlook.office365.com/groups/contoso.com/eng/mail"

    @pytest.mark.asyncio
    async def test_from_api_and_caches(self):
        connector = _make_connector()
        connector._group_cache = {}
        connector.external_users_client = MagicMock()

        mock_data = MagicMock()
        mock_data.mail = "team@contoso.com"
        mock_data.mail_nickname = "team"
        mock_data.mailNickname = None

        connector.external_users_client.groups_group_get_group = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._construct_group_mail_weburl("g2")
        assert result == "https://outlook.office365.com/groups/contoso.com/team/mail"
        assert "g2" in connector._group_cache

    @pytest.mark.asyncio
    async def test_no_mail_returns_none(self):
        connector = _make_connector()
        connector._group_cache = {
            "g1": {"mail": None, "mailNickname": "eng"}
        }

        result = await connector._construct_group_mail_weburl("g1")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_client_returns_none(self):
        connector = _make_connector()
        connector._group_cache = {}
        connector.external_users_client = None

        result = await connector._construct_group_mail_weburl("g1")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure_returns_none(self):
        connector = _make_connector()
        connector._group_cache = {}
        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_group_get_group = AsyncMock(
            return_value=_make_graph_response(success=False)
        )

        result = await connector._construct_group_mail_weburl("g1")
        assert result is None

    @pytest.mark.asyncio
    async def test_mail_without_at_returns_none(self):
        connector = _make_connector()
        connector._group_cache = {
            "g1": {"mail": "noemail", "mailNickname": "eng"}
        }

        result = await connector._construct_group_mail_weburl("g1")
        assert result is None


# ===========================================================================
# _create_attachment_record
# ===========================================================================


class TestCreateAttachmentRecord:

    @pytest.mark.asyncio
    async def test_creates_new_attachment(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        attachment = MagicMock()
        attachment.id = "att-1"
        attachment.name = "report.pdf"
        attachment.content_type = "application/pdf"
        attachment.e_tag = "att-etag"
        attachment.last_modified_date_time = None
        attachment.size = 5000

        result = await connector._create_attachment_record(
            "org-1", attachment, "msg-1", "f1", None, "https://outlook.com/msg-1"
        )

        assert result is not None
        assert result.record_name == "report.pdf"
        assert result.extension == "pdf"
        assert result.is_file is True

    @pytest.mark.asyncio
    async def test_no_content_type_returns_none(self):
        connector = _make_connector()
        attachment = MagicMock()
        attachment.id = "att-1"
        attachment.name = "file.bin"
        attachment.content_type = None

        result = await connector._create_attachment_record(
            "org-1", attachment, "msg-1", "f1"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_existing_record_increments_version(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        existing = MagicMock()
        existing.id = "existing-att-id"
        existing.version = 2

        attachment = MagicMock()
        attachment.id = "att-1"
        attachment.name = "report.docx"
        attachment.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        attachment.e_tag = "att-etag-2"
        attachment.last_modified_date_time = None
        attachment.size = 3000

        result = await connector._create_attachment_record(
            "org-1", attachment, "msg-1", "f1", existing, "https://outlook.com/msg-1"
        )

        assert result.id == "existing-att-id"
        assert result.version == 3

    @pytest.mark.asyncio
    async def test_indexing_filter_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        attachment = MagicMock()
        attachment.id = "att-1"
        attachment.name = "file.txt"
        attachment.content_type = "text/plain"
        attachment.e_tag = "etag"
        attachment.last_modified_date_time = None
        attachment.size = 100

        result = await connector._create_attachment_record(
            "org-1", attachment, "msg-1", "f1", None, "https://outlook.com/msg-1"
        )

        assert result.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# ===========================================================================
# _process_email_attachments_with_folder
# ===========================================================================


class TestProcessEmailAttachmentsWithFolder:

    @pytest.mark.asyncio
    async def test_processes_attachments(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        att = MagicMock()
        att.id = "att-1"
        att.e_tag = "att-etag"
        att.name = "file.pdf"
        att.content_type = "application/pdf"
        att.last_modified_date_time = None
        att.size = 1024

        connector._get_message_attachments_external = AsyncMock(return_value=[att])
        connector._get_existing_record = AsyncMock(return_value=None)

        mock_att_record = _make_file_record()
        connector._create_attachment_record = AsyncMock(return_value=mock_att_record)

        msg = MagicMock()
        msg.id = "m1"
        msg.web_link = "https://outlook.com/m1"
        user = _make_user()

        updates = await connector._process_email_attachments_with_folder(
            "org-1", user, msg, [], "f1", "Inbox"
        )

        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_attachment_skipped_when_no_content_type(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        att = MagicMock()
        att.id = "att-1"
        att.e_tag = "att-etag"

        connector._get_message_attachments_external = AsyncMock(return_value=[att])
        connector._get_existing_record = AsyncMock(return_value=None)
        connector._create_attachment_record = AsyncMock(return_value=None)

        msg = MagicMock()
        msg.id = "m1"
        msg.web_link = ""
        user = _make_user()

        updates = await connector._process_email_attachments_with_folder(
            "org-1", user, msg, [], "f1", "Inbox"
        )

        assert len(updates) == 0

    @pytest.mark.asyncio
    async def test_existing_attachment_content_changed(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        att = MagicMock()
        att.id = "att-1"
        att.e_tag = "new-etag"
        att.name = "file.pdf"

        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "old-etag"
        existing.external_record_group_id = "f1"
        existing.version = 1

        connector._get_message_attachments_external = AsyncMock(return_value=[att])
        connector._get_existing_record = AsyncMock(return_value=existing)

        mock_att_record = _make_file_record()
        connector._create_attachment_record = AsyncMock(return_value=mock_att_record)

        msg = MagicMock()
        msg.id = "m1"
        msg.web_link = ""
        user = _make_user()

        updates = await connector._process_email_attachments_with_folder(
            "org-1", user, msg, [], "f1", "Inbox"
        )

        assert len(updates) == 1
        assert updates[0].content_changed is True


# ===========================================================================
# _process_group_post
# ===========================================================================


class TestProcessGroupPost:

    @pytest.mark.asyncio
    async def test_new_post(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)
        connector._get_existing_record = AsyncMock(return_value=None)
        connector._construct_group_mail_weburl = AsyncMock(return_value="https://outlook.office365.com/groups/contoso.com/eng/mail")

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Engineering",
        )

        thread = MagicMock()
        thread.id = "t1"
        thread.topic = "Discussion"

        post = MagicMock()
        post.id = "p1"
        post.from_ = None
        post.received_date_time = "2024-06-15T12:00:00Z"

        result = await connector._process_group_post("org-1", group, thread, post)
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.GROUP_MAIL

    @pytest.mark.asyncio
    async def test_indexing_filter_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        connector._get_existing_record = AsyncMock(return_value=None)
        connector._construct_group_mail_weburl = AsyncMock(return_value=None)

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        post = MagicMock()
        post.id = "p1"
        post.from_ = None
        post.received_date_time = None

        thread = MagicMock()
        thread.id = "t1"
        thread.topic = "Topic"

        result = await connector._process_group_post("org-1", group, thread, post)
        assert result is not None
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector._get_existing_record = AsyncMock(side_effect=Exception("DB error"))

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        post = MagicMock()
        post.id = "p1"

        thread = MagicMock()
        thread.id = "t1"

        result = await connector._process_group_post("org-1", group, thread, post)
        assert result is None


# ===========================================================================
# _process_group_post_attachments
# ===========================================================================


class TestProcessGroupPostAttachments:

    @pytest.mark.asyncio
    async def test_no_thread_id_returns_empty(self):
        connector = _make_connector()

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        post = MagicMock()
        post.id = "p1"
        post.conversation_thread_id = None

        result = await connector._process_group_post_attachments(
            "org-1", group, MagicMock(), post, []
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_no_attachments_returns_empty(self):
        connector = _make_connector()
        connector._get_group_post_attachments = AsyncMock(return_value=[])

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        post = MagicMock()
        post.id = "p1"
        post.conversation_thread_id = "t1"

        result = await connector._process_group_post_attachments(
            "org-1", group, MagicMock(), post, []
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_processes_attachments(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        att = MagicMock()
        att.id = "att-1"
        att.name = "report.pdf"
        att.content_type = "application/pdf"
        att.last_modified_date_time = None
        att.size = 2048

        connector._get_group_post_attachments = AsyncMock(return_value=[att])
        connector._get_existing_record = AsyncMock(return_value=None)

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        post = MagicMock()
        post.id = "p1"
        post.conversation_thread_id = "t1"

        result = await connector._process_group_post_attachments(
            "org-1", group, MagicMock(), post, []
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_attachment_without_content_type(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        att = MagicMock()
        att.id = "att-1"
        att.name = "unknown"
        att.content_type = None

        connector._get_group_post_attachments = AsyncMock(return_value=[att])
        connector._get_existing_record = AsyncMock(return_value=None)

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        post = MagicMock()
        post.id = "p1"
        post.conversation_thread_id = "t1"

        result = await connector._process_group_post_attachments(
            "org-1", group, MagicMock(), post, []
        )
        assert len(result) == 0


# ===========================================================================
# _get_group_post_attachments
# ===========================================================================


class TestGetGroupPostAttachments:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_data = MagicMock()
        mock_data.value = [{"id": "a1"}]

        connector.external_outlook_client.groups_threads_posts_list_attachments = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._get_group_post_attachments("g1", "t1", "p1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.groups_threads_posts_list_attachments = AsyncMock(
            return_value=_make_graph_response(success=False, error="Err")
        )

        result = await connector._get_group_post_attachments("g1", "t1", "p1")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_client(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._get_group_post_attachments("g1", "t1", "p1")
        assert result == []


# ===========================================================================
# _sync_user_folders
# ===========================================================================


class TestSyncUserFolders:

    @pytest.mark.asyncio
    async def test_no_folders(self):
        connector = _make_connector()
        connector._determine_folder_filter_strategy = MagicMock(return_value=(None, None))
        connector._get_all_folders_for_user = AsyncMock(return_value=[])

        user = _make_user()
        result = await connector._sync_user_folders(user)
        assert result == []

    @pytest.mark.asyncio
    async def test_syncs_folders_as_record_groups(self):
        connector = _make_connector()
        connector._determine_folder_filter_strategy = MagicMock(return_value=(None, None))

        folder = {"id": "f1", "display_name": "Inbox"}
        connector._get_all_folders_for_user = AsyncMock(return_value=[folder])

        rg = RecordGroup(
            org_id="org-1", name="Inbox", short_name="Inbox",
            description="desc", external_group_id="f1",
            parent_external_group_id=None, connector_name=Connectors.OUTLOOK,
            connector_id="conn-1", group_type=RecordGroupType.MAILBOX,
        )
        connector._transform_folder_to_record_group = MagicMock(return_value=rg)

        user = _make_user()
        result = await connector._sync_user_folders(user)
        assert len(result) == 1
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        connector = _make_connector()
        connector._determine_folder_filter_strategy = MagicMock(side_effect=Exception("Err"))

        user = _make_user()
        result = await connector._sync_user_folders(user)
        assert result == []


# ===========================================================================
# _get_all_messages_delta_external
# ===========================================================================


class TestGetAllMessagesDeltaExternal:

    @pytest.mark.asyncio
    async def test_basic_delta_sync(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)

        messages = [MagicMock(), MagicMock()]
        connector.external_outlook_client.fetch_all_messages_delta = AsyncMock(
            return_value=(messages, "delta-link-new")
        )

        result = await connector._get_all_messages_delta_external("su1", "f1", None)
        assert len(result['messages']) == 2
        assert result['delta_link'] == "delta-link-new"

    @pytest.mark.asyncio
    async def test_with_received_date_filter(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        date_filter = MagicMock()
        date_filter.is_empty.return_value = False
        date_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00", None)

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.RECEIVED_DATE:
                return date_filter
            return None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=get_filter)

        connector.external_outlook_client.fetch_all_messages_delta = AsyncMock(
            return_value=([MagicMock()], "delta")
        )

        result = await connector._get_all_messages_delta_external("su1", "f1", None)
        assert len(result['messages']) == 1

    @pytest.mark.asyncio
    async def test_client_side_before_filter(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        date_filter = MagicMock()
        date_filter.is_empty.return_value = False
        date_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00")

        def get_filter(key):
            from app.connectors.core.registry.filters import SyncFilterKey
            if key == SyncFilterKey.RECEIVED_DATE:
                return date_filter
            return None

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=get_filter)

        msg_old = MagicMock()
        msg_old.received_date_time = datetime(2024, 5, 1, tzinfo=timezone.utc)

        msg_new = MagicMock()
        msg_new.received_date_time = datetime(2024, 7, 1, tzinfo=timezone.utc)

        connector.external_outlook_client.fetch_all_messages_delta = AsyncMock(
            return_value=([msg_old, msg_new], "delta")
        )

        result = await connector._get_all_messages_delta_external("su1", "f1", None)
        assert len(result['messages']) == 1

    @pytest.mark.asyncio
    async def test_no_client_returns_empty(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._get_all_messages_delta_external("su1", "f1", None)
        assert result['messages'] == []

    @pytest.mark.asyncio
    async def test_with_delta_link(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)

        connector.external_outlook_client.fetch_all_messages_delta = AsyncMock(
            return_value=([MagicMock()], "new-delta")
        )

        result = await connector._get_all_messages_delta_external("su1", "f1", "old-delta")
        assert result['delta_link'] == "new-delta"


# ===========================================================================
# _process_single_email_with_folder (indexing filter off)
# ===========================================================================


class TestProcessSingleEmailWithFolderIndexing:

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        connector._get_existing_record = AsyncMock(return_value=None)
        connector._extract_email_permissions = AsyncMock(return_value=[])

        msg = MagicMock()
        msg.id = "m1"
        msg.subject = "Test"
        msg.e_tag = "etag"
        msg.created_date_time = None
        msg.last_modified_date_time = None
        msg.web_link = ""
        msg.from_ = None
        msg.to_recipients = []
        msg.cc_recipients = []
        msg.bcc_recipients = []
        msg.conversation_id = ""
        msg.internet_message_id = ""
        msg.conversation_index = ""

        result = await connector._process_single_email_with_folder(
            "org-1", "user@test.com", msg, "f1", "Inbox"
        )

        assert result is not None
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        connector = _make_connector()
        connector._get_existing_record = AsyncMock(side_effect=Exception("DB error"))

        msg = MagicMock()
        msg.id = "m1"

        result = await connector._process_single_email_with_folder(
            "org-1", "user@test.com", msg, "f1", "Inbox"
        )

        assert result is None


# ===========================================================================
# _get_all_folders_for_user (additional)
# ===========================================================================


class TestGetAllFoldersForUserAdditional:

    @pytest.mark.asyncio
    async def test_with_filter_params(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        folder = {"id": "f1", "display_name": "Inbox", "child_folder_count": 0}
        connector.external_outlook_client.users_list_mail_folders = AsyncMock(
            return_value=_make_graph_response(success=True, data={"value": [folder]})
        )
        connector._get_child_folders_recursive = AsyncMock(return_value=[])

        result = await connector._get_all_folders_for_user(
            "su1", folder_names=["Inbox"], folder_filter_mode="include"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_failure(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.external_outlook_client.users_list_mail_folders = AsyncMock(
            return_value=_make_graph_response(success=False, error="Err")
        )

        result = await connector._get_all_folders_for_user("su1")
        assert result == []

    @pytest.mark.asyncio
    async def test_with_nested_folders(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        folder = {"id": "f1", "display_name": "Inbox", "child_folder_count": 1}
        child = {"id": "f2", "display_name": "Subfolder", "child_folder_count": 0}

        connector.external_outlook_client.users_list_mail_folders = AsyncMock(
            return_value=_make_graph_response(success=True, data={"value": [folder]})
        )
        connector._get_child_folders_recursive = AsyncMock(return_value=[child])

        result = await connector._get_all_folders_for_user("su1")
        assert len(result) == 2


# ===========================================================================
# _get_child_folders_recursive (additional)
# ===========================================================================


class TestGetChildFoldersRecursiveAdditional:

    @pytest.mark.asyncio
    async def test_no_folder_id(self):
        connector = _make_connector()
        folder = MagicMock(spec=[])
        result = await connector._get_child_folders_recursive("su1", folder)
        assert result == []

    @pytest.mark.asyncio
    async def test_api_failure(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Parent"
        folder.child_folder_count = 2

        connector.external_outlook_client.users_mail_folders_list_child_folders = AsyncMock(
            return_value=_make_graph_response(success=False, error="Forbidden")
        )

        result = await connector._get_child_folders_recursive("su1", folder)
        assert result == []


# ===========================================================================
# _get_user_groups (additional paths)
# ===========================================================================


class TestGetUserGroupsAdditional:

    @pytest.mark.asyncio
    async def test_dict_data_response(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()

        mock_data = {"value": [{"id": "g1"}]}
        connector.external_users_client.groups_list_member_of = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._get_user_groups("u1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_list_member_of = AsyncMock(
            return_value=_make_graph_response(success=False)
        )

        result = await connector._get_user_groups("u1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_list_member_of = AsyncMock(
            side_effect=Exception("API error")
        )

        result = await connector._get_user_groups("u1")
        assert result == []


# ===========================================================================
# _download_group_post_attachment (additional)
# ===========================================================================


class TestDownloadGroupPostAttachmentAdditional:

    @pytest.mark.asyncio
    async def test_no_client(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._download_group_post_attachment("g1", "t1", "p1", "a1")
        assert result == b''

    @pytest.mark.asyncio
    async def test_no_content_bytes(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_data = MagicMock()
        mock_data.content_bytes = None
        mock_data.contentBytes = None

        connector.external_outlook_client.groups_threads_posts_get_attachments = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._download_group_post_attachment("g1", "t1", "p1", "a1")
        assert result == b''


# ===========================================================================
# _process_users (additional edge cases)
# ===========================================================================


class TestProcessUsersAdditional:

    @pytest.mark.asyncio
    async def test_multiple_users(self):
        connector = _make_connector()
        user1 = _make_user(email="u1@test.com")
        user2 = _make_user(email="u2@test.com")

        connector._process_user_emails = AsyncMock(return_value="Processed 3 items")

        results = []
        async for status in connector._process_users("org-1", [user1, user2]):
            results.append(status)

        assert len(results) == 2


# ===========================================================================
# _process_user_emails (additional)
# ===========================================================================


class TestProcessUserEmailsAdditional:

    @pytest.mark.asyncio
    async def test_folder_processing_error_continues(self):
        connector = _make_connector()

        folder1 = MagicMock()
        folder1.id = "f1"
        folder1.display_name = "Inbox"

        folder2 = MagicMock()
        folder2.id = "f2"
        folder2.display_name = "Sent"

        connector._sync_user_folders = AsyncMock(return_value=[folder1, folder2])
        connector._process_single_folder_messages = AsyncMock(
            side_effect=[Exception("Err"), (3, [])]
        )
        connector._create_all_thread_edges_for_user = AsyncMock(return_value=0)

        user = _make_user()
        result = await connector._process_user_emails("org-1", user)
        assert "3" in result
        assert "Failed" in result

    @pytest.mark.asyncio
    async def test_exception_returns_error_string(self):
        connector = _make_connector()
        connector._sync_user_folders = AsyncMock(side_effect=Exception("Fatal"))

        user = _make_user()
        result = await connector._process_user_emails("org-1", user)
        assert "Failed" in result


# ===========================================================================
# _get_all_users_external (additional)
# ===========================================================================


class TestGetAllUsersExternalAdditional:

    @pytest.mark.asyncio
    async def test_user_with_no_display_name_uses_mail(self):
        connector = _make_connector()

        mock_user = MagicMock()
        mock_user.display_name = ""
        mock_user.given_name = ""
        mock_user.surname = ""
        mock_user.mail = "fallback@test.com"
        mock_user.user_principal_name = "fallback@test.com"
        mock_user.id = "u1"

        mock_data = MagicMock()
        mock_data.value = [mock_user]
        mock_data.odata_next_link = None

        connector.external_users_client = MagicMock()
        connector.external_users_client.users_user_list_user = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        users = await connector._get_all_users_external()
        assert len(users) == 1
        assert users[0].full_name == "fallback@test.com"

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()

        user1 = MagicMock()
        user1.display_name = "User 1"
        user1.given_name = "U"
        user1.surname = "1"
        user1.mail = "u1@test.com"
        user1.user_principal_name = "u1@test.com"
        user1.id = "id1"

        user2 = MagicMock()
        user2.display_name = "User 2"
        user2.given_name = "U"
        user2.surname = "2"
        user2.mail = "u2@test.com"
        user2.user_principal_name = "u2@test.com"
        user2.id = "id2"

        page1_data = MagicMock()
        page1_data.value = [user1]
        page1_data.odata_next_link = "https://graph.microsoft.com/next"

        page2_data = MagicMock()
        page2_data.value = [user2]
        page2_data.odata_next_link = None

        connector.external_users_client = MagicMock()
        connector.external_users_client.users_user_list_user = AsyncMock(
            side_effect=[
                _make_graph_response(success=True, data=page1_data),
                _make_graph_response(success=True, data=page2_data),
            ]
        )

        users = await connector._get_all_users_external()
        assert len(users) == 2


# ===========================================================================
# _get_all_microsoft_365_groups (additional)
# ===========================================================================


class TestGetAllMicrosoft365GroupsAdditional:

    @pytest.mark.asyncio
    async def test_api_failure(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector.external_users_client.groups_list_groups = AsyncMock(
            return_value=_make_graph_response(success=False, error="Err")
        )

        result = await connector._get_all_microsoft_365_groups()
        assert result == []


# ===========================================================================
# _get_group_threads (additional - dict data)
# ===========================================================================


class TestGetGroupThreadsAdditional:

    @pytest.mark.asyncio
    async def test_dict_data_response(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_data = {"value": [{"id": "t1"}]}
        connector.external_outlook_client.groups_list_threads = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._get_group_threads("g1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_client(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._get_group_threads("g1")
        assert result == []


# ===========================================================================
# _get_thread_posts (additional - dict data)
# ===========================================================================


class TestGetThreadPostsAdditional:

    @pytest.mark.asyncio
    async def test_dict_data_response(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()

        mock_data = {"value": [{"id": "p1"}]}
        connector.external_outlook_client.groups_threads_list_posts = AsyncMock(
            return_value=_make_graph_response(success=True, data=mock_data)
        )

        result = await connector._get_thread_posts("g1", "t1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_client(self):
        connector = _make_connector()
        connector.external_outlook_client = None

        result = await connector._get_thread_posts("g1", "t1")
        assert result == []


# ===========================================================================
# create_connector (factory method)
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.microsoft.outlook.connector.DataSourceEntitiesProcessor") as mock_dep:
            instance = MagicMock()
            instance.initialize = AsyncMock()
            instance.org_id = "org-1"
            mock_dep.return_value = instance

            logger = logging.getLogger("test")
            dsp = MagicMock()
            cs = MagicMock()

            connector = await OutlookConnector.create_connector(logger, dsp, cs, "conn-1")
            assert isinstance(connector, OutlookConnector)
            instance.initialize.assert_awaited_once()


# ===========================================================================
# _process_single_folder_messages (exception handling)
# ===========================================================================


class TestProcessSingleFolderMessagesAdditional:

    @pytest.mark.asyncio
    async def test_exception_returns_zero(self):
        connector = _make_connector()
        connector.email_delta_sync_point = MagicMock()
        connector.email_delta_sync_point.read_sync_point = AsyncMock(side_effect=Exception("DB err"))

        user = _make_user()
        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"

        count, records = await connector._process_single_folder_messages("org-1", user, folder)
        assert count == 0
        assert records == []


# ===========================================================================
# _augment_email_html_with_metadata (additional)
# ===========================================================================


class TestAugmentEmailHtmlAdditional:

    def test_cc_and_bcc_included(self):
        connector = _make_connector()

        record = MagicMock()
        record.from_email = "sender@test.com"
        record.to_emails = ["to@test.com"]
        record.cc_emails = ["cc@test.com"]
        record.bcc_emails = ["bcc@test.com"]
        record.subject = "Subject"

        result = connector._augment_email_html_with_metadata("<p>Body</p>", record)
        assert "cc@test.com" in result
        assert "bcc@test.com" in result

    def test_html_escaping(self):
        connector = _make_connector()

        record = MagicMock()
        record.from_email = "sender@test.com"
        record.to_emails = []
        record.cc_emails = []
        record.bcc_emails = []
        record.subject = "<script>alert('xss')</script>"

        result = connector._augment_email_html_with_metadata("<p>Body</p>", record)
        assert "&lt;script&gt;" in result


# ===========================================================================
# _transform_group_to_record_group (additional)
# ===========================================================================


class TestTransformGroupToRecordGroupAdditional:

    def test_with_created_date(self):
        connector = _make_connector()
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Team"
        group.mail = "team@test.com"
        group.created_date_time = "2024-01-15T12:00:00Z"

        result = connector._transform_group_to_record_group(group)
        assert result is not None
        assert result.source_created_at is not None

    def test_no_mail(self):
        connector = _make_connector()
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Team"
        group.mail = None
        group.created_date_time = None

        result = connector._transform_group_to_record_group(group)
        assert result is not None
        assert "Team group mailbox" in result.description


# ===========================================================================
# _sync_single_group_conversations exception path
# ===========================================================================


class TestSyncSingleGroupConversationsAdditional:

    @pytest.mark.asyncio
    async def test_outer_exception_returns_zero(self):
        connector = _make_connector()
        connector.group_conversations_sync_point = MagicMock()
        connector.group_conversations_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("DB error")
        )

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        result = await connector._sync_single_group_conversations(group)
        assert result == 0


# ===========================================================================
# _process_group_thread (additional - posts with no received_date_time)
# ===========================================================================


class TestProcessGroupThreadAdditional:

    @pytest.mark.asyncio
    async def test_posts_with_datetime_object_not_string(self):
        connector = _make_connector()
        connector.external_outlook_client = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        thread = MagicMock()
        thread.id = "t1"
        thread.topic = "Topic"

        post = MagicMock()
        post.id = "p1"
        post.received_date_time = datetime(2024, 7, 1, tzinfo=timezone.utc)
        post.has_attachments = False

        connector._get_thread_posts = AsyncMock(return_value=[post])

        update = MagicMock()
        update.record = MagicMock()
        update.new_permissions = []
        connector._process_group_post = AsyncMock(return_value=update)

        group = AppUserGroup(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_group_id="g1", name="Eng",
        )

        result = await connector._process_group_thread(
            "org-1", group, thread, "2024-06-01T00:00:00Z"
        )
        assert result == 1


# ===========================================================================
# _create_all_thread_edges_for_user (batch error)
# ===========================================================================


class TestCreateAllThreadEdgesAdditional:

    @pytest.mark.asyncio
    async def test_batch_create_error_returns_zero(self):
        connector = _make_connector()
        user = MagicMock(email="u@test.com")

        record = MagicMock()
        record.conversation_index = "some_index"
        record.thread_id = "t1"
        record.id = "r1"

        connector._find_parent_by_conversation_index_from_db = AsyncMock(return_value="parent-id")

        mock_tx = MagicMock()
        mock_tx.batch_create_edges = AsyncMock(side_effect=Exception("DB batch error"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        result = await connector._create_all_thread_edges_for_user("org-1", user, [record])
        assert result == 0

    @pytest.mark.asyncio
    async def test_outer_exception_returns_zero(self):
        connector = _make_connector()
        user = MagicMock(email="u@test.com")

        connector._find_parent_by_conversation_index_from_db = AsyncMock(
            side_effect=Exception("Unexpected")
        )

        record = MagicMock()
        record.conversation_index = "idx"
        record.thread_id = "t1"
        record.id = "r1"

        result = await connector._create_all_thread_edges_for_user("org-1", user, [record])
        assert result == 0


# ===========================================================================
# _find_parent_by_conversation_index_from_db (error path)
# ===========================================================================


class TestFindParentByConversationIndexAdditional:

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        user = MagicMock(user_id="u1")

        mock_tx = MagicMock()
        mock_tx.get_record_by_conversation_index = AsyncMock(side_effect=Exception("DB err"))
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        child_index = base64.b64encode(b"A" * 27).decode()
        result = await connector._find_parent_by_conversation_index_from_db(child_index, "t1", "org-1", user)
        assert result is None

    @pytest.mark.asyncio
    async def test_parent_not_found_returns_none(self):
        connector = _make_connector()
        user = MagicMock(user_id="u1")

        mock_tx = MagicMock()
        mock_tx.get_record_by_conversation_index = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value = mock_tx

        child_index = base64.b64encode(b"A" * 27).decode()
        result = await connector._find_parent_by_conversation_index_from_db(child_index, "t1", "org-1", user)
        assert result is None


# ===========================================================================
# _sync_users (additional)
# ===========================================================================


class TestSyncUsersAdditional:

    @pytest.mark.asyncio
    async def test_users_without_matching_enterprise_not_synced(self):
        connector = _make_connector()

        enterprise_user = AppUser(
            app_name=Connectors.OUTLOOK, connector_id="conn-1",
            source_user_id="su1", email="enterprise@test.com", full_name="Ent User",
        )
        connector._get_all_users_external = AsyncMock(return_value=[enterprise_user])

        active_user = MagicMock()
        active_user.email = "other@test.com"
        active_user.source_user_id = None
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active_user])
        connector._populate_user_cache = AsyncMock()

        users = await connector._sync_users()
        assert len(users) == 0

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        connector = _make_connector()
        connector._get_all_users_external = AsyncMock(side_effect=Exception("API down"))

        with pytest.raises(Exception, match="API down"):
            await connector._sync_users()


# ===========================================================================
# _safe_get_attr (dict path)
# ===========================================================================


class TestSafeGetAttrDict:

    def test_dict_get(self):
        connector = _make_connector()
        d = {"key": "value"}
        assert connector._safe_get_attr(d, "key") == "value"

    def test_dict_missing_returns_default(self):
        connector = _make_connector()
        d = {"key": "value"}
        assert connector._safe_get_attr(d, "missing", "default") == "default"

    def test_none_obj_returns_default(self):
        connector = _make_connector()
        assert connector._safe_get_attr(42, "attr", "default") == "default"


# ===========================================================================
# _sync_user_groups error in group processing continues
# ===========================================================================


class TestSyncUserGroupsErrorHandling:

    @pytest.mark.asyncio
    async def test_group_processing_error_continues(self):
        connector = _make_connector()
        connector.external_users_client = MagicMock()
        connector._user_cache = {}

        group1 = MagicMock()
        group1.id = "g1"
        group1.display_name = "Bad Group"
        group1.additional_data = {}
        group1.mail = None
        group1.mail_nickname = None
        group1.description = None

        group2 = MagicMock()
        group2.id = "g2"
        group2.display_name = "Good Group"
        group2.additional_data = {}
        group2.description = "desc"
        group2.mail = "good@test.com"
        group2.mail_nickname = "good"

        connector._get_all_microsoft_365_groups = AsyncMock(return_value=[group1, group2])
        connector._get_group_members = AsyncMock(side_effect=[Exception("Err"), []])
        connector._transform_group_to_record_group = MagicMock(return_value=None)

        result = await connector._sync_user_groups()
        assert len(result) >= 1


# ===========================================================================
# _transform_folder_to_record_group nested folder
# ===========================================================================


class TestTransformFolderNestedFolder:

    def test_nested_folder_has_parent_id(self):
        connector = _make_connector()

        folder = MagicMock()
        folder.id = "f2"
        folder.display_name = "Subfolder"
        folder._is_top_level = False
        folder.parent_folder_id = "f1"

        user = MagicMock()
        user.email = "u@test.com"

        result = connector._transform_folder_to_record_group(folder, user)
        assert result is not None
        assert result.parent_external_group_id == "f1"

    def test_top_level_folder_no_parent_id(self):
        connector = _make_connector()

        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"
        folder._is_top_level = True
        folder.parent_folder_id = "some-parent"

        user = MagicMock()
        user.email = "u@test.com"

        result = connector._transform_folder_to_record_group(folder, user)
        assert result is not None
        assert result.parent_external_group_id is None
