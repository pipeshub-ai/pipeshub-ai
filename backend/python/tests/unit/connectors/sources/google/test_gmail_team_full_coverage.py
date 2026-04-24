"""Full coverage tests for GoogleGmailTeamConnector."""

import asyncio
import base64
import logging
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    MailRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


def _make_logger():
    log = logging.getLogger("test_gmail_team_fc")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None, user_with_perm=None, user_by_id=None,
                        attachment_records=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user_with_perm)
    tx.get_user_by_user_id = AsyncMock(return_value=user_by_id)
    tx.get_records_by_parent = AsyncMock(return_value=attachment_records or [])
    return tx


def _make_mock_data_store_provider(existing_record=None, user_with_perm=None,
                                    user_by_id=None, attachment_records=None):
    tx = _make_mock_tx_store(existing_record, user_with_perm, user_by_id, attachment_records)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_gmail_message(
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


def _make_google_user(email="user@example.com", user_id="guser-1", full_name="Test User",
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
def connector():
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
        conn.config = {"credentials": {"auth": {}}}
        yield conn


class TestInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_auth(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_no_admin_email(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"someKey": "someValue"},
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector.init()


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_orchestrates(self, connector):
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

    @pytest.mark.asyncio
    async def test_run_sync_error_propagates(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector._sync_users = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(RuntimeError, match="boom"):
                await connector.run_sync()


class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_no_admin_source(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_users()

    @pytest.mark.asyncio
    async def test_empty(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": []})
        await connector._sync_users()
        assert connector.synced_users == []

    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        page1 = [_make_google_user(email="u1@t.com", user_id="u1")]
        page2 = [_make_google_user(email="u2@t.com", user_id="u2")]
        connector.admin_data_source.users_list = AsyncMock(side_effect=[
            {"users": page1, "nextPageToken": "tok2"},
            {"users": page2},
        ])
        await connector._sync_users()
        assert len(connector.synced_users) == 2

    @pytest.mark.asyncio
    async def test_skip_no_email(self, connector):
        no_email = {"id": "u1", "name": {"fullName": "No Email"}}
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [no_email]})
        await connector._sync_users()
        assert connector.synced_users == []

    @pytest.mark.asyncio
    async def test_suspended_user(self, connector):
        user = _make_google_user(suspended=True)
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].is_active is False

    @pytest.mark.asyncio
    async def test_name_fallback_given_family(self, connector):
        user = {"id": "u1", "primaryEmail": "u@t.com",
                "name": {"givenName": "First", "familyName": "Last"},
                "creationTime": "2024-01-01T00:00:00.000Z"}
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].full_name == "First Last"

    @pytest.mark.asyncio
    async def test_name_fallback_email(self, connector):
        user = {"id": "u1", "primaryEmail": "u@t.com", "name": {},
                "creationTime": "2024-01-01T00:00:00.000Z"}
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].full_name == "u@t.com"

    @pytest.mark.asyncio
    async def test_bad_creation_time(self, connector):
        user = _make_google_user()
        user["creationTime"] = "not-a-date"
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": [user]})
        await connector._sync_users()
        assert connector.synced_users[0].source_created_at is None


class TestSyncUserGroups:
    @pytest.mark.asyncio
    async def test_no_admin(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_empty(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={"groups": []})
        await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_with_groups(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "grp@t.com", "name": "Grp"}]
        })
        connector._process_group = AsyncMock()
        await connector._sync_user_groups()
        connector._process_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_continues_on_group_error(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "g1@t.com"}, {"email": "g2@t.com"}]
        })
        connector._process_group = AsyncMock(side_effect=[RuntimeError("fail"), None])
        await connector._sync_user_groups()
        assert connector._process_group.await_count == 2

    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(side_effect=[
            {"groups": [{"email": "g1@t.com"}], "nextPageToken": "tok"},
            {"groups": [{"email": "g2@t.com"}]},
        ])
        connector._process_group = AsyncMock()
        await connector._sync_user_groups()
        assert connector._process_group.await_count == 2


class TestProcessGroup:
    @pytest.mark.asyncio
    async def test_no_email(self, connector):
        await connector._process_group({"name": "No Email"})
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_members(self, connector):
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m@t.com", "id": "m1"},
        ])
        group = {"email": "grp@t.com", "name": "Grp", "creationTime": "2024-01-01T00:00:00.000Z"}
        await connector._process_group(group)
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_user_members(self, connector):
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "GROUP", "email": "sub@t.com"},
        ])
        await connector._process_group({"email": "grp@t.com", "name": "Grp"})
        connector.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_member_lookup_in_synced_users(self, connector):
        connector.synced_users = [
            AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="m1",
                    email="m@t.com", full_name="Member One", source_created_at=1000)
        ]
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m@t.com", "id": "m1"},
        ])
        await connector._process_group({"email": "grp@t.com", "name": "Grp"})
        args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        _, members = args[0]
        assert members[0].full_name == "Member One"

    @pytest.mark.asyncio
    async def test_name_fallback_email(self, connector):
        connector._fetch_group_members = AsyncMock(return_value=[
            {"type": "USER", "email": "m@t.com", "id": "m1"},
        ])
        await connector._process_group({"email": "grp@t.com", "name": ""})
        args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        ug, _ = args[0]
        assert ug.name == "grp@t.com"


class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.admin_data_source.members_list = AsyncMock(side_effect=[
            {"members": [{"id": "m1"}], "nextPageToken": "tok"},
            {"members": [{"id": "m2"}]},
        ])
        result = await connector._fetch_group_members("grp@t.com")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self, connector):
        connector.admin_data_source.members_list = AsyncMock(return_value={"members": []})
        result = await connector._fetch_group_members("grp@t.com")
        assert result == []

    @pytest.mark.asyncio
    async def test_error(self, connector):
        connector.admin_data_source.members_list = AsyncMock(side_effect=RuntimeError("err"))
        with pytest.raises(RuntimeError):
            await connector._fetch_group_members("grp@t.com")


class TestSyncRecordGroups:
    @pytest.mark.asyncio
    async def test_empty_users(self, connector):
        await connector._sync_record_groups([])

    @pytest.mark.asyncio
    async def test_creates_inbox_sent_others(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="User One")
        await connector._sync_record_groups([user])
        assert connector.data_entities_processor.on_new_record_groups.await_count == 3

    @pytest.mark.asyncio
    async def test_skips_user_without_email(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                       email="", full_name="No Email")
        await connector._sync_record_groups([user])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_on_error(self, connector):
        user = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                       email="u@t.com", full_name="User One")
        connector.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=[RuntimeError("fail"), None, None]
        )
        await connector._sync_record_groups([user])


class TestPassDateFilter:
    def test_no_filter_passes(self, connector):
        msg = _make_gmail_message()
        assert connector._pass_date_filter(msg) is True

    def test_before_start_fails(self, connector):
        msg = _make_gmail_message(internal_date="1000")
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 2000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.RECEIVED_DATE else None
        )
        assert connector._pass_date_filter(msg) is False

    def test_after_end_fails(self, connector):
        msg = _make_gmail_message(internal_date="5000")
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = None
        mock_filter.get_datetime_end.return_value = 3000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.RECEIVED_DATE else None
        )
        assert connector._pass_date_filter(msg) is False

    def test_within_range_passes(self, connector):
        msg = _make_gmail_message(internal_date="2500")
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 2000
        mock_filter.get_datetime_end.return_value = 3000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.RECEIVED_DATE else None
        )
        assert connector._pass_date_filter(msg) is True

    def test_invalid_date_passes(self, connector):
        msg = _make_gmail_message()
        msg["internalDate"] = "not-a-number"
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == SyncFilterKey.RECEIVED_DATE else None
        )
        assert connector._pass_date_filter(msg) is True


class TestProcessGmailMessage:
    @pytest.mark.asyncio
    async def test_no_message_id(self, connector):
        result = await connector._process_gmail_message("u@t.com", {}, "t1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_new_inbox_message(self, connector):
        msg = _make_gmail_message(from_email="other@t.com")
        result = await connector._process_gmail_message("u@t.com", msg, "thread-1", None)
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL
        assert "INBOX" in result.record.external_record_group_id

    @pytest.mark.asyncio
    async def test_sent_message_owner_perm(self, connector):
        msg = _make_gmail_message(from_email="u@t.com", label_ids=["SENT"])
        result = await connector._process_gmail_message("u@t.com", msg, "thread-1", None)
        assert result.record.external_record_group_id == "u@t.com:SENT"
        assert result.new_permissions[0].type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_received_message_read_perm(self, connector):
        msg = _make_gmail_message(from_email="other@t.com", label_ids=["INBOX"])
        result = await connector._process_gmail_message("u@t.com", msg, "thread-1", None)
        assert result.new_permissions[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_date_filter_skip(self, connector):
        connector._pass_date_filter = MagicMock(return_value=False)
        msg = _make_gmail_message()
        result = await connector._process_gmail_message("u@t.com", msg, "t1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_other_label(self, connector):
        msg = _make_gmail_message(label_ids=["CATEGORY_UPDATES"])
        result = await connector._process_gmail_message("u@t.com", msg, "t1", None)
        assert "OTHERS" in result.record.external_record_group_id

    @pytest.mark.asyncio
    async def test_existing_message_metadata_change(self, connector):
        existing = _make_record(external_record_group_id="u@t.com:INBOX")
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider
        msg = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message("u@t.com", msg, "t1", None)
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_with_cc_bcc(self, connector):
        msg = _make_gmail_message(cc_emails="cc@t.com", bcc_emails="bcc@t.com")
        result = await connector._process_gmail_message("u@t.com", msg, "t1", None)
        assert result.record.cc_emails == ["cc@t.com"]
        assert result.record.bcc_emails == ["bcc@t.com"]

    @pytest.mark.asyncio
    async def test_from_header_with_name(self, connector):
        msg = _make_gmail_message(from_email="Sender Name <sender@t.com>")
        result = await connector._process_gmail_message("u@t.com", msg, "t1", None)
        assert result is not None


class TestExtractAttachmentInfos:
    def test_regular_attachment(self, connector):
        msg = _make_gmail_message(has_attachments=True)
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["filename"] == "attachment.xlsx"
        assert not infos[0]["isDriveFile"]

    def test_drive_attachment(self, connector):
        msg = _make_gmail_message(has_drive_attachment=True)
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["isDriveFile"] is True
        assert infos[0]["driveFileId"] == "drive-file-1"

    def test_both_attachment_types(self, connector):
        msg = _make_gmail_message(has_attachments=True, has_drive_attachment=True)
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 2

    def test_no_attachments(self, connector):
        msg = _make_gmail_message()
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 0

    def test_drive_file_in_body_link(self, connector):
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
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["driveFileId"] == "abc123"

    def test_nested_parts(self, connector):
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
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) == 1
        assert infos[0]["filename"] == "nested.pdf"


class TestProcessGmailAttachment:
    @pytest.mark.asyncio
    async def test_regular_attachment(self, connector):
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
        result = await connector._process_gmail_attachment("u@t.com", "msg-1", attach_info, perms, "u@t.com:OTHERS")
        assert result is not None
        assert result.record.record_name == "file.pdf"
        assert result.record.extension == "pdf"

    @pytest.mark.asyncio
    async def test_no_stable_id(self, connector):
        attach_info = {"attachmentId": "att-1", "stableAttachmentId": None, "isDriveFile": False}
        result = await connector._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
        assert result is None

    @pytest.mark.asyncio
    async def test_regular_no_attachment_id(self, connector):
        attach_info = {
            "attachmentId": None, "stableAttachmentId": "msg-1~1",
            "isDriveFile": False, "driveFileId": None,
        }
        result = await connector._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
        assert result is None

    @pytest.mark.asyncio
    async def test_drive_attachment_fetches_metadata(self, connector):
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
            result = await connector._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
            assert result is not None
            assert result.record.record_name == "drive_file.docx"

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self, connector):
        attach_info = {
            "attachmentId": "att-1", "driveFileId": None,
            "stableAttachmentId": "msg-1~1", "partId": "1",
            "filename": "file.pdf", "mimeType": "application/pdf",
            "size": 1024, "isDriveFile": False,
        }
        mock_filter = MagicMock()
        mock_filter.is_enabled = MagicMock(return_value=False)
        connector.indexing_filters = mock_filter
        result = await connector._process_gmail_attachment("u@t.com", "msg-1", attach_info, [], "u@t.com:OTHERS")
        assert result.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestProcessGmailMessageGenerator:
    @pytest.mark.asyncio
    async def test_yields_updates(self, connector):
        msg = _make_gmail_message()
        record = MagicMock()
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[], external_record_id="msg-1"
        )
        connector._process_gmail_message = AsyncMock(return_value=update)
        results = []
        async for item in connector._process_gmail_message_generator([msg], "u@t.com", "t1"):
            results.append(item)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_skips_none(self, connector):
        connector._process_gmail_message = AsyncMock(return_value=None)
        results = []
        async for item in connector._process_gmail_message_generator([{}], "u@t.com", "t1"):
            results.append(item)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_index_off_filter(self, connector):
        record = MagicMock()
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[], external_record_id="msg-1"
        )
        connector._process_gmail_message = AsyncMock(return_value=update)
        mock_filter = MagicMock()
        mock_filter.is_enabled = MagicMock(return_value=False)
        connector.indexing_filters = mock_filter
        results = []
        async for item in connector._process_gmail_message_generator([_make_gmail_message()], "u@t.com", "t1"):
            results.append(item)
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestProcessGmailAttachmentGenerator:
    @pytest.mark.asyncio
    async def test_yields_updates(self, connector):
        record = MagicMock()
        update = RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[], external_record_id="msg-1~1"
        )
        connector._process_gmail_attachment = AsyncMock(return_value=update)
        attach_info = {"stableAttachmentId": "msg-1~1"}
        results = []
        async for item in connector._process_gmail_attachment_generator("u@t.com", "msg-1", [attach_info], [], "u@t.com:OTHERS"):
            results.append(item)
        assert len(results) == 1


class TestParseGmailHeaders:
    def test_parses_relevant_headers(self, connector):
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
        result = connector._parse_gmail_headers(headers)
        assert result["subject"] == "Test"
        assert result["from"] == "from@t.com"
        assert "x-custom" not in result

    def test_empty_headers(self, connector):
        result = connector._parse_gmail_headers([])
        assert result == {}


class TestCreateOwnerPermission:
    def test_creates_owner(self, connector):
        perm = connector._create_owner_permission("u@t.com")
        assert perm.email == "u@t.com"
        assert perm.type == PermissionType.OWNER
        assert perm.entity_type == EntityType.USER


class TestParseEmailList:
    def test_comma_separated(self, connector):
        result = connector._parse_email_list("a@t.com, b@t.com, c@t.com")
        assert result == ["a@t.com", "b@t.com", "c@t.com"]

    def test_empty_string(self, connector):
        assert connector._parse_email_list("") == []

    def test_single(self, connector):
        assert connector._parse_email_list("a@t.com") == ["a@t.com"]

    def test_filters_empty_parts(self, connector):
        result = connector._parse_email_list("a@t.com,,")
        assert result == ["a@t.com"]


class TestExtractEmailFromHeader:
    def test_name_and_email(self, connector):
        assert connector._extract_email_from_header("John <john@t.com>") == "john@t.com"

    def test_just_email(self, connector):
        assert connector._extract_email_from_header("john@t.com") == "john@t.com"

    def test_empty(self, connector):
        assert connector._extract_email_from_header("") == ""

    def test_none(self, connector):
        assert connector._extract_email_from_header(None) == ""

    def test_with_spaces(self, connector):
        assert connector._extract_email_from_header("  John Doe < john@t.com >  ") == "john@t.com"


class TestExtractBodyFromPayload:
    def test_html_body(self, connector):
        body_data = base64.urlsafe_b64encode(b"<p>Hello</p>").decode()
        payload = {"mimeType": "text/html", "body": {"data": body_data}}
        assert connector._extract_body_from_payload(payload) == body_data

    def test_plain_body(self, connector):
        body_data = base64.urlsafe_b64encode(b"Hello").decode()
        payload = {"mimeType": "text/plain", "body": {"data": body_data}}
        assert connector._extract_body_from_payload(payload) == body_data

    def test_nested_parts_html_preferred(self, connector):
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
        assert connector._extract_body_from_payload(payload) == html_data

    def test_empty_payload(self, connector):
        assert connector._extract_body_from_payload({"mimeType": "multipart/mixed", "body": {}}) == ""


class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_returns_true(self, connector):
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_gmail_source(self, connector):
        connector.gmail_data_source = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_no_admin_source(self, connector):
        connector.admin_data_source = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_no_clients(self, connector):
        connector.gmail_client = None
        connector.admin_client = None
        assert await connector.test_connection_and_access() is False


class TestGetSignedUrl:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())


class TestHandleWebhookNotification:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


class TestMergeHistoryChanges:
    def test_merges_and_deduplicates(self, connector):
        inbox = {"history": [{"id": "1"}, {"id": "2"}]}
        sent = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3
        ids = [h["id"] for h in result["history"]]
        assert ids == ["1", "2", "3"]

    def test_empty_inputs(self, connector):
        result = connector._merge_history_changes({"history": []}, {"history": []})
        assert result["history"] == []


class TestDeleteMessageAndAttachments:
    @pytest.mark.asyncio
    async def test_deletes_attachments_and_message(self, connector):
        attachment = MagicMock()
        attachment.id = "att-rec-1"
        provider = _make_mock_data_store_provider(attachment_records=[attachment])
        connector.data_store_provider = provider
        await connector._delete_message_and_attachments("rec-1", "msg-1")
        assert connector.data_entities_processor.on_record_deleted.await_count == 2


class TestFindPreviousMessageInThread:
    @pytest.mark.asyncio
    async def test_finds_previous(self, connector):
        existing = _make_record(record_id="prev-rec", external_id="msg-prev")
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider

        gmail_client = AsyncMock()
        gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [
                {"id": "msg-prev", "internalDate": "1000"},
                {"id": "msg-current", "internalDate": "2000"},
            ]
        })
        result = await connector._find_previous_message_in_thread(
            "u@t.com", gmail_client, "t1", "msg-current", "2000"
        )
        assert result == "prev-rec"

    @pytest.mark.asyncio
    async def test_single_message_returns_none(self, connector):
        gmail_client = AsyncMock()
        gmail_client.users_threads_get = AsyncMock(return_value={
            "messages": [{"id": "msg-1", "internalDate": "1000"}]
        })
        result = await connector._find_previous_message_in_thread(
            "u@t.com", gmail_client, "t1", "msg-1", "1000"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_checks_batch_records(self, connector):
        provider = _make_mock_data_store_provider(existing_record=None)
        connector.data_store_provider = provider

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
        result = await connector._find_previous_message_in_thread(
            "u@t.com", gmail_client, "t1", "msg-current", "2000",
            batch_records=[(batch_record, [])]
        )
        assert result == "batch-rec-id"


class TestProcessUsersInBatches:
    @pytest.mark.asyncio
    async def test_filters_active_users(self, connector):
        active = AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                         email="u1@t.com", full_name="U1")
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[active])
        connector._run_sync_with_yield = AsyncMock()
        await connector._process_users_in_batches([active])
        connector._run_sync_with_yield.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_active_users(self, connector):
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        connector._run_sync_with_yield = AsyncMock()
        await connector._process_users_in_batches([
            AppUser(app_name=Connectors.GOOGLE_MAIL, connector_id="c", source_user_id="u1",
                    email="u1@t.com", full_name="U1")
        ])
        connector._run_sync_with_yield.assert_not_awaited()


class TestCreateUserGmailClient:
    @pytest.mark.asyncio
    async def test_creates_client(self, connector):
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
                result = await connector._create_user_gmail_client("u@t.com")
                assert result is not None

    @pytest.mark.asyncio
    async def test_error_propagates(self, connector):
        with patch(
            "app.connectors.sources.google.gmail.team.connector.GoogleClient"
        ) as MockGC:
            MockGC.build_from_services = AsyncMock(side_effect=RuntimeError("fail"))
            with pytest.raises(RuntimeError):
                await connector._create_user_gmail_client("u@t.com")


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector):
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_no_gmail_source(self, connector):
        connector.gmail_data_source = None
        with pytest.raises(Exception, match="Gmail data source not initialized"):
            await connector.reindex_records([_make_record()])

    @pytest.mark.asyncio
    async def test_reindex_updated(self, connector):
        record = _make_record()
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(MagicMock(), [])
        )
        await connector.reindex_records([record])
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_non_updated(self, connector):
        record = _make_record()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_continues_on_error(self, connector):
        r1 = _make_record(record_id="r1")
        r2 = _make_record(record_id="r2")
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[RuntimeError("fail"), None]
        )
        await connector.reindex_records([r1, r2])


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_external_id(self, connector):
        record = _make_record(external_id=None)
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_user_with_permission(self, connector):
        record = _make_record()
        provider = _make_mock_data_store_provider(user_with_perm=None)
        connector.data_store_provider = provider
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_user_without_email(self, connector):
        user_perm = MagicMock()
        user_perm.email = None
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider
        record = _make_record()
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_routes_mail_type(self, connector):
        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider

        record = _make_record(record_type=RecordType.MAIL)
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector._check_and_fetch_updated_mail_record = AsyncMock(return_value=None)
        await connector._check_and_fetch_updated_record("org-1", record)
        connector._check_and_fetch_updated_mail_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_file_type(self, connector):
        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider

        record = _make_record(record_type=RecordType.FILE)
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector._check_and_fetch_updated_file_record = AsyncMock(return_value=None)
        await connector._check_and_fetch_updated_record("org-1", record)
        connector._check_and_fetch_updated_file_record.assert_awaited_once()


class TestCheckAndFetchUpdatedMailRecord:
    @pytest.mark.asyncio
    async def test_missing_message_id(self, connector):
        record = _make_record(external_id=None)
        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@t.com", AsyncMock()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_message_not_found(self, connector):
        record = _make_record()
        client = AsyncMock()
        resp = MagicMock()
        resp.status = HttpStatusCode.NOT_FOUND.value
        client.users_messages_get = AsyncMock(side_effect=HttpError(resp, b"Not Found"))
        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@t.com", client
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_updated_message(self, connector):
        record = _make_record()
        client = AsyncMock()
        msg = _make_gmail_message(label_ids=["SENT"])
        client.users_messages_get = AsyncMock(return_value=msg)
        client.users_threads_get = AsyncMock(return_value={
            "messages": [{"id": "msg-1", "internalDate": "1000"}]
        })

        existing = _make_record(external_record_group_id="u@t.com:INBOX")
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider

        result = await connector._check_and_fetch_updated_mail_record(
            "org-1", record, "u@t.com", client
        )
        if result:
            assert result[0].id == record.id


class TestFetchHistoryChanges:
    @pytest.mark.asyncio
    async def test_basic(self, connector):
        client = AsyncMock()
        client.users_history_list = AsyncMock(return_value={
            "history": [{"id": "1"}]
        })
        result = await connector._fetch_history_changes(client, "u@t.com", "100", "INBOX")
        assert len(result["history"]) == 1

    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        client = AsyncMock()
        client.users_history_list = AsyncMock(side_effect=[
            {"history": [{"id": "1"}], "nextPageToken": "tok"},
            {"history": [{"id": "2"}]},
        ])
        result = await connector._fetch_history_changes(client, "u@t.com", "100", "INBOX")
        assert len(result["history"]) == 2

    @pytest.mark.asyncio
    async def test_http_error_re_raises(self, connector):
        client = AsyncMock()
        resp = MagicMock()
        resp.status = 404
        client.users_history_list = AsyncMock(side_effect=HttpError(resp, b"Not Found"))
        with pytest.raises(HttpError):
            await connector._fetch_history_changes(client, "u@t.com", "100", "INBOX")


class TestRunSyncWithYield:
    @pytest.mark.asyncio
    async def test_full_sync_no_history(self, connector):
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector.gmail_delta_sync_point = AsyncMock()
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector._run_full_sync = AsyncMock()
        await connector._run_sync_with_yield("u@t.com")
        connector._run_full_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_sync_with_history(self, connector):
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector.gmail_delta_sync_point = AsyncMock()
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "12345"}
        )
        connector._run_sync_with_history_id = AsyncMock()
        await connector._run_sync_with_yield("u@t.com")
        connector._run_sync_with_history_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_fallback_on_404(self, connector):
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector.gmail_delta_sync_point = AsyncMock()
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "12345"}
        )
        resp = MagicMock()
        resp.status = HttpStatusCode.NOT_FOUND.value
        connector._run_sync_with_history_id = AsyncMock(
            side_effect=HttpError(resp, b"Not Found")
        )
        connector._run_full_sync = AsyncMock()
        await connector._run_sync_with_yield("u@t.com")
        connector._run_full_sync.assert_awaited_once()


class TestCleanup:
    @pytest.mark.asyncio
    async def test_clears_resources(self, connector):
        await connector.cleanup()
        assert connector.gmail_data_source is None
        assert connector.admin_data_source is None
        assert connector.gmail_client is None
        assert connector.admin_client is None
        assert connector.config is None


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_file_id_raises(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = None
        record.record_type = RecordTypes.MAIL.value
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_mail_record_routes_correctly(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        record.id = "rec-1"
        record.record_name = "Test"

        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider

        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector._stream_mail_record = AsyncMock(return_value=MagicMock())
        result = await connector.stream_record(record)
        connector._stream_mail_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file_record_routes_correctly(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = "msg-1~1"
        record.record_type = "file"
        record.id = "rec-1"
        record.record_name = "file.pdf"
        record.mime_type = "application/pdf"

        user_perm = MagicMock()
        user_perm.email = "u@t.com"
        provider = _make_mock_data_store_provider(user_with_perm=user_perm)
        connector.data_store_provider = provider

        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector._stream_attachment_record = AsyncMock(return_value=MagicMock())
        result = await connector.stream_record(record)
        connector._stream_attachment_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_id_lookup(self, connector):
        record = MagicMock(spec=Record)
        record.external_record_id = "msg-1"
        record.record_type = RecordTypes.MAIL.value
        record.id = "rec-1"
        record.record_name = "Test"

        provider = _make_mock_data_store_provider(user_by_id={"email": "u@t.com"})
        connector.data_store_provider = provider
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector._stream_mail_record = AsyncMock(return_value=MagicMock())
        await connector.stream_record(record, user_id="user-1")
        connector._stream_mail_record.assert_awaited_once()


class TestRunFullSync:
    @pytest.mark.asyncio
    async def test_basic_flow(self, connector):
        client = AsyncMock()
        client.users_get_profile = AsyncMock(return_value={"historyId": "100"})
        client.users_threads_list = AsyncMock(return_value={"threads": []})

        connector.gmail_delta_sync_point = AsyncMock()
        connector.gmail_delta_sync_point.update_sync_point = AsyncMock()

        await connector._run_full_sync("u@t.com", client, "sync-key")
        connector.gmail_delta_sync_point.update_sync_point.assert_awaited()


class TestRunSyncWithHistoryId:
    @pytest.mark.asyncio
    async def test_basic_flow(self, connector):
        client = AsyncMock()
        client.users_get_profile = AsyncMock(return_value={"historyId": "200"})

        connector._fetch_history_changes = AsyncMock(return_value={"history": []})
        connector._merge_history_changes = MagicMock(return_value={"history": []})
        connector.gmail_delta_sync_point = AsyncMock()
        connector.gmail_delta_sync_point.update_sync_point = AsyncMock()

        await connector._run_sync_with_history_id("u@t.com", client, "100", "sync-key")
        connector.gmail_delta_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_handles_http_error_gracefully(self, connector):
        """HttpError from _fetch_history_changes is re-raised by _run_sync_with_history_id."""
        client = AsyncMock()
        client.users_get_profile = AsyncMock(return_value={"historyId": "200"})
        resp = MagicMock()
        resp.status = 404
        connector._fetch_history_changes = AsyncMock(
            side_effect=HttpError(resp, b"Not Found")
        )
        connector._merge_history_changes = MagicMock(return_value={"history": []})
        connector.gmail_delta_sync_point = AsyncMock()
        connector.gmail_delta_sync_point.update_sync_point = AsyncMock()
        with pytest.raises(HttpError):
            await connector._run_sync_with_history_id("u@t.com", client, "100", "sync-key")


class TestGetExistingRecord:
    @pytest.mark.asyncio
    async def test_found(self, connector):
        existing = _make_record()
        provider = _make_mock_data_store_provider(existing_record=existing)
        connector.data_store_provider = provider
        result = await connector._get_existing_record("msg-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connector):
        provider = _make_mock_data_store_provider(existing_record=None)
        connector.data_store_provider = provider
        result = await connector._get_existing_record("msg-999")
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self, connector):
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("db err"))
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx

        provider.transaction = _transaction
        connector.data_store_provider = provider
        result = await connector._get_existing_record("msg-1")
        assert result is None
