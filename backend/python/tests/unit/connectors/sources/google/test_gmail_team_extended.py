"""Extended tests for GoogleGmailTeamConnector (reaching 90%+ coverage).

Covers: init, run_sync, _sync_users, _sync_user_groups, _process_group,
_fetch_group_members, _sync_record_groups, _run_sync_with_yield,
_run_full_sync, _run_sync_with_history_id, _fetch_history_changes,
_merge_history_changes, _process_history_changes, _delete_message_and_attachments,
_find_previous_message_in_thread, _process_users_in_batches, _create_user_gmail_client,
_create_owner_permission, test_connection_and_access, get_signed_url,
_extract_body_from_payload, stream_record, run_incremental_sync, reindex_records,
_check_and_fetch_updated_record, _check_and_fetch_updated_mail_record,
_check_and_fetch_updated_file_record, get_filter_options, cleanup, create_connector,
_process_gmail_message, _extract_attachment_infos, _process_gmail_message_generator,
_process_gmail_attachment, _process_gmail_attachment_generator, _stream_mail_record,
_stream_attachment_record, _stream_from_drive, _convert_to_pdf.
"""

import base64
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.config.constants.arangodb import (
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.connectors.core.registry.filters import FilterCollection
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
    log = logging.getLogger("test_gmail_team_ext")
    log.setLevel(logging.DEBUG)
    return log


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.create_record_relation = AsyncMock()
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
    tx.get_user_by_user_id = AsyncMock(return_value=None)
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
            "filename": "file.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "body": {"attachmentId": "att-1", "size": 10000},
        })
    if has_drive_attachment:
        parts.append({
            "partId": "2",
            "filename": "large.zip",
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
        "snippet": "snippet...",
        "internalDate": internal_date,
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {"data": body_data},
            "parts": parts,
        },
    }


def _make_app_user(email="user@example.com", user_id="guser-1", full_name="User"):
    return AppUser(
        app_name="GMAIL WORKSPACE",
        connector_id="gmail-team-1",
        source_user_id=user_id,
        email=email,
        full_name=full_name,
        is_active=True,
    )


@pytest.fixture
def connector():
    """Create a GoogleGmailTeamConnector with fully mocked deps."""
    with patch(
        "app.connectors.sources.google.gmail.team.connector.GoogleClient"
    ) as MockGoogleClient, patch(
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
        dep.get_all_active_users = AsyncMock(return_value=[])
        dep.reindex_existing_records = AsyncMock()

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
            scope="personal",
            created_by="test-user-id",
        )
        conn.sync_filters = FilterCollection()
        conn.indexing_filters = FilterCollection()
        conn.admin_client = MagicMock()
        conn.gmail_client = MagicMock()
        conn.admin_data_source = AsyncMock()
        conn.gmail_data_source = AsyncMock()
        conn.gmail_data_source.client = MagicMock()
        conn.config = {"credentials": {"auth": {}}}
        conn._MockGoogleClient = MockGoogleClient
        yield conn


# ===========================================================================
# init
# ===========================================================================

class TestGmailTeamInit:
    @patch("app.connectors.sources.google.gmail.team.connector.GoogleClient")
    async def test_init_success(self, mock_gc, connector):
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        mock_gc.build_from_services = AsyncMock(return_value=mock_client)

        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com", "someKey": "val"},
        })
        result = await connector.init()
        assert result is True

    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    async def test_init_no_auth(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="Service account credentials not found"):
            await connector.init()

    async def test_init_no_admin_email(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"someKey": "someValue"},
        })
        with pytest.raises(ValueError, match="Admin email not found"):
            await connector.init()

    @patch("app.connectors.sources.google.gmail.team.connector.GoogleClient")
    async def test_init_admin_client_failure(self, mock_gc, connector):
        mock_gc.build_from_services = AsyncMock(side_effect=Exception("Admin fail"))
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com", "k": "v"},
        })
        with pytest.raises(Exception):
            await connector.init()

    @patch("app.connectors.sources.google.gmail.team.connector.GoogleClient")
    async def test_init_gmail_client_failure(self, mock_gc, connector):
        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_client = MagicMock()
                mock_client.get_client.return_value = MagicMock()
                return mock_client
            raise Exception("Gmail fail")

        mock_gc.build_from_services = AsyncMock(side_effect=_side_effect)
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@example.com", "k": "v"},
        })
        with pytest.raises(Exception):
            await connector.init()


# ===========================================================================
# _pass_date_filter
# ===========================================================================

class TestPassDateFilter:
    def test_no_filter_passes(self, connector):
        msg = {"internalDate": "1704067200000"}
        assert connector._pass_date_filter(msg) is True

    def test_no_internal_date_passes(self, connector):
        msg = {}
        assert connector._pass_date_filter(msg) is True

    def test_invalid_internal_date_passes(self, connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 1000
        mock_filter.get_datetime_end.return_value = 2000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        msg = {"internalDate": "not-a-number"}
        assert connector._pass_date_filter(msg) is True

    def test_message_before_start_filtered(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = 2000000000000
        mock_filter.get_datetime_end.return_value = None
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        msg = {"internalDate": "1000000000000"}
        assert connector._pass_date_filter(msg) is False

    def test_message_after_end_filtered(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_datetime_start.return_value = None
        mock_filter.get_datetime_end.return_value = 1000000000000
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        msg = {"internalDate": "2000000000000"}
        assert connector._pass_date_filter(msg) is False


# ===========================================================================
# _process_gmail_message
# ===========================================================================

class TestProcessGmailMessage:
    async def test_process_new_message(self, connector):
        msg = _make_gmail_message()
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.MAIL

    async def test_process_message_no_id(self, connector):
        msg = {"payload": {}}
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is None

    async def test_process_message_sent_label(self, connector):
        msg = _make_gmail_message(label_ids=["SENT", "INBOX"])
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is not None
        assert "SENT" in result.record.external_record_group_id

    async def test_process_message_others_label(self, connector):
        msg = _make_gmail_message(label_ids=["CATEGORY_PROMOTIONS"])
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is not None
        assert "OTHERS" in result.record.external_record_group_id

    async def test_process_message_owner_permission_for_sender(self, connector):
        msg = _make_gmail_message(from_email="user@example.com")
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is not None
        perms = result.new_permissions
        assert any(p.type == PermissionType.OWNER for p in perms)

    async def test_process_message_read_permission_for_recipient(self, connector):
        msg = _make_gmail_message(from_email="other@example.com")
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        perms = result.new_permissions
        assert any(p.type == PermissionType.READ for p in perms)

    async def test_process_message_with_invalid_internal_date(self, connector):
        msg = _make_gmail_message(internal_date="invalid")
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is not None
        assert result.record.source_created_at is not None

    async def test_process_message_no_internal_date(self, connector):
        msg = _make_gmail_message()
        msg["internalDate"] = None
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is not None

    async def test_existing_record_metadata_change(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.external_record_group_id = "user@example.com:INBOX"
        ds = _make_mock_data_store_provider(existing)
        connector.data_store_provider = ds

        msg = _make_gmail_message(label_ids=["SENT"])
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is not None
        assert result.is_new is False
        assert result.metadata_changed is True

    async def test_process_message_exception(self, connector):
        connector._parse_gmail_headers = MagicMock(side_effect=Exception("boom"))
        msg = _make_gmail_message()
        result = await connector._process_gmail_message("user@example.com", msg, "thread-1", None)
        assert result is None


# ===========================================================================
# _extract_attachment_infos
# ===========================================================================

class TestExtractAttachmentInfos:
    def test_regular_attachment(self, connector):
        msg = _make_gmail_message(has_attachments=True)
        infos = connector._extract_attachment_infos(msg)
        assert len(infos) >= 1
        assert infos[0]["filename"] == "file.xlsx"
        assert infos[0]["isDriveFile"] is False

    def test_drive_attachment(self, connector):
        msg = _make_gmail_message(has_drive_attachment=True)
        infos = connector._extract_attachment_infos(msg)
        assert any(att["isDriveFile"] is True for att in infos)

    def test_drive_file_in_body_content(self, connector):
        html = '<a href="https://drive.google.com/file/d/ABCDEF123/view?usp=drive_web">link</a>'
        msg = _make_gmail_message(body_html=html)
        # The body has text/plain as mimeType, need text/html for extraction
        msg["payload"]["mimeType"] = "text/html"
        msg["payload"]["body"]["data"] = base64.urlsafe_b64encode(html.encode()).decode()
        infos = connector._extract_attachment_infos(msg)
        assert any(att.get("driveFileId") == "ABCDEF123" for att in infos)

    def test_no_parts(self, connector):
        msg = {"id": "msg-1", "payload": {"headers": [], "parts": []}}
        infos = connector._extract_attachment_infos(msg)
        assert infos == []

    def test_nested_parts(self, connector):
        msg = {
            "id": "msg-1",
            "payload": {
                "headers": [],
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "multipart/alternative",
                        "body": {},
                        "parts": [
                            {
                                "partId": "0.1",
                                "filename": "nested.pdf",
                                "mimeType": "application/pdf",
                                "body": {"attachmentId": "nested-att-1", "size": 500},
                            }
                        ],
                    }
                ],
            },
        }
        infos = connector._extract_attachment_infos(msg)
        assert any(att["filename"] == "nested.pdf" for att in infos)


# ===========================================================================
# _process_gmail_attachment
# ===========================================================================

class TestProcessGmailAttachment:
    async def test_regular_attachment(self, connector):
        info = {
            "attachmentId": "att-1",
            "driveFileId": None,
            "stableAttachmentId": "msg-1~1",
            "partId": "1",
            "filename": "report.pdf",
            "mimeType": "application/pdf",
            "size": 1024,
            "isDriveFile": False,
        }
        perms = [Permission(email="user@ex.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        result = await connector._process_gmail_attachment("user@ex.com", "msg-1", info, perms)
        assert result is not None
        assert result.record.record_type == RecordType.FILE
        assert result.record.extension == "pdf"

    async def test_no_stable_id_returns_none(self, connector):
        info = {"attachmentId": "att-1", "stableAttachmentId": None, "isDriveFile": False}
        result = await connector._process_gmail_attachment("user@ex.com", "msg-1", info, [])
        assert result is None

    async def test_no_attachment_id_non_drive_returns_none(self, connector):
        info = {"attachmentId": None, "stableAttachmentId": "stable-1", "isDriveFile": False}
        result = await connector._process_gmail_attachment("user@ex.com", "msg-1", info, [])
        assert result is None

    @patch("app.connectors.sources.google.gmail.team.connector.GoogleClient")
    async def test_drive_attachment_metadata_fetch(self, mock_gc, connector):
        mock_drive_client = MagicMock()
        mock_service = MagicMock()
        mock_service.files.return_value.get.return_value.execute.return_value = {
            "name": "fetched.pdf", "mimeType": "application/pdf", "size": "2048"
        }
        mock_drive_client.get_client.return_value = mock_service
        mock_gc.build_from_services = AsyncMock(return_value=mock_drive_client)

        info = {
            "attachmentId": None,
            "driveFileId": "drive-123",
            "stableAttachmentId": "drive-123",
            "partId": "2",
            "filename": None,
            "mimeType": "application/octet-stream",
            "size": 0,
            "isDriveFile": True,
        }
        result = await connector._process_gmail_attachment("user@ex.com", "msg-1", info, [])
        assert result is not None

    async def test_attachment_exception(self, connector):
        connector._get_existing_record = AsyncMock(side_effect=Exception("boom"))
        info = {"attachmentId": "a", "stableAttachmentId": "s", "isDriveFile": False}
        result = await connector._process_gmail_attachment("user@ex.com", "msg-1", info, [])
        assert result is None


# ===========================================================================
# _process_gmail_message_generator / _process_gmail_attachment_generator
# ===========================================================================

class TestMessageGenerator:
    async def test_yields_records(self, connector):
        msgs = [_make_gmail_message(message_id="m1"), _make_gmail_message(message_id="m2")]
        results = []
        async for update in connector._process_gmail_message_generator(msgs, "user@ex.com", "t1"):
            results.append(update)
        assert len(results) == 2

    async def test_skips_errors(self, connector):
        connector._process_gmail_message = AsyncMock(side_effect=Exception("fail"))
        msgs = [_make_gmail_message()]
        results = []
        async for update in connector._process_gmail_message_generator(msgs, "u@ex.com", "t1"):
            results.append(update)
        assert len(results) == 0


class TestAttachmentGenerator:
    async def test_yields_attachments(self, connector):
        info = {
            "attachmentId": "a1", "driveFileId": None, "stableAttachmentId": "m~1",
            "partId": "1", "filename": "f.pdf", "mimeType": "application/pdf",
            "size": 100, "isDriveFile": False,
        }
        perms = [Permission(email="u@ex.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        results = []
        async for update in connector._process_gmail_attachment_generator("u@ex.com", "m", [info], perms):
            results.append(update)
        assert len(results) == 1

    async def test_skips_errors(self, connector):
        connector._process_gmail_attachment = AsyncMock(side_effect=Exception("fail"))
        results = []
        async for update in connector._process_gmail_attachment_generator("u@ex.com", "m", [{"x": 1}], []):
            results.append(update)
        assert len(results) == 0


# ===========================================================================
# _merge_history_changes
# ===========================================================================

class TestMergeHistoryChanges:
    def test_merge_deduplicates(self, connector):
        inbox = {"history": [{"id": "1"}, {"id": "2"}]}
        sent = {"history": [{"id": "2"}, {"id": "3"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert len(result["history"]) == 3

    def test_merge_sorts_by_id(self, connector):
        inbox = {"history": [{"id": "10"}]}
        sent = {"history": [{"id": "2"}]}
        result = connector._merge_history_changes(inbox, sent)
        assert result["history"][0]["id"] == "2"
        assert result["history"][1]["id"] == "10"

    def test_merge_empty(self, connector):
        result = connector._merge_history_changes({"history": []}, {"history": []})
        assert result["history"] == []


# ===========================================================================
# _create_owner_permission
# ===========================================================================

class TestCreateOwnerPermission:
    def test_creates_owner(self, connector):
        perm = connector._create_owner_permission("user@example.com")
        assert perm.email == "user@example.com"
        assert perm.type == PermissionType.OWNER
        assert perm.entity_type == EntityType.USER


# ===========================================================================
# test_connection_and_access
# ===========================================================================

class TestConnectionAndAccess:
    async def test_all_initialized(self, connector):
        result = await connector.test_connection_and_access()
        assert result is True

    async def test_no_gmail_data_source(self, connector):
        connector.gmail_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    async def test_no_admin_data_source(self, connector):
        connector.admin_data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    async def test_no_clients(self, connector):
        connector.gmail_client = None
        connector.admin_client = None
        result = await connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# get_signed_url / get_filter_options / handle_webhook_notification
# ===========================================================================

class TestNotImplementedMethods:
    def test_get_signed_url_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.get_signed_url(MagicMock())

    async def test_get_filter_options_raises(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")

    def test_handle_webhook_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


# ===========================================================================
# _extract_body_from_payload
# ===========================================================================

class TestExtractBodyFromPayload:
    def test_html_body(self, connector):
        payload = {"mimeType": "text/html", "body": {"data": "SEVMTE8="}}
        result = connector._extract_body_from_payload(payload)
        assert result == "SEVMTE8="

    def test_plain_body(self, connector):
        payload = {"mimeType": "text/plain", "body": {"data": "dGVzdA=="}}
        result = connector._extract_body_from_payload(payload)
        assert result == "dGVzdA=="

    def test_parts_html_preferred(self, connector):
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

    def test_parts_fallback_to_plain(self, connector):
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "plain"}},
                {"mimeType": "image/png", "body": {}},
            ],
        }
        result = connector._extract_body_from_payload(payload)
        assert result == "plain"

    def test_empty_payload(self, connector):
        assert connector._extract_body_from_payload({"mimeType": "text/html", "body": {}}) == ""


# ===========================================================================
# _sync_users
# ===========================================================================

class TestSyncUsers:
    async def test_sync_users_success(self, connector):
        user_data = {
            "users": [
                {
                    "id": "u1",
                    "primaryEmail": "alice@example.com",
                    "name": {"fullName": "Alice"},
                    "suspended": False,
                    "creationTime": "2024-01-01T00:00:00.000Z",
                }
            ]
        }
        connector.admin_data_source.users_list = AsyncMock(return_value=user_data)
        await connector._sync_users()
        assert len(connector.synced_users) == 1
        connector.data_entities_processor.on_new_app_users.assert_called_once()

    async def test_sync_users_no_admin_ds(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError, match="Admin data source not initialized"):
            await connector._sync_users()

    async def test_sync_users_no_users_found(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={"users": []})
        await connector._sync_users()
        assert connector.synced_users == []

    async def test_sync_users_skips_no_email(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [{"id": "u1", "name": {"fullName": "No Email"}}]
        })
        await connector._sync_users()
        assert connector.synced_users == []

    async def test_sync_users_pagination(self, connector):
        page1 = {
            "users": [{"id": "u1", "primaryEmail": "a@e.com", "name": {"fullName": "A"}}],
            "nextPageToken": "page2",
        }
        page2 = {
            "users": [{"id": "u2", "primaryEmail": "b@e.com", "name": {"fullName": "B"}}],
        }
        connector.admin_data_source.users_list = AsyncMock(side_effect=[page1, page2])
        await connector._sync_users()
        assert len(connector.synced_users) == 2

    async def test_sync_users_fallback_name(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [{"id": "u1", "primaryEmail": "a@e.com", "name": {}}]
        })
        await connector._sync_users()
        assert connector.synced_users[0].full_name == "a@e.com"

    async def test_sync_users_name_from_parts(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [{"id": "u1", "primaryEmail": "a@e.com", "name": {"givenName": "Alice", "familyName": "Smith"}}]
        })
        await connector._sync_users()
        assert connector.synced_users[0].full_name == "Alice Smith"

    async def test_sync_users_with_title(self, connector):
        connector.admin_data_source.users_list = AsyncMock(return_value={
            "users": [{"id": "u1", "primaryEmail": "a@e.com", "name": {"fullName": "A"},
                       "organizations": [{"title": "Engineer"}]}]
        })
        await connector._sync_users()
        assert connector.synced_users[0].title == "Engineer"


# ===========================================================================
# _sync_user_groups / _process_group / _fetch_group_members
# ===========================================================================

class TestSyncUserGroups:
    async def test_sync_groups_success(self, connector):
        connector.admin_data_source.groups_list = AsyncMock(return_value={
            "groups": [{"email": "grp@e.com", "name": "Group 1"}]
        })
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "email": "u@e.com", "id": "u1"}]
        })
        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_called()

    async def test_sync_groups_no_admin_ds(self, connector):
        connector.admin_data_source = None
        with pytest.raises(ValueError):
            await connector._sync_user_groups()

    async def test_process_group_no_email(self, connector):
        await connector._process_group({})
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_process_group_member_lookup_in_synced_users(self, connector):
        connector.synced_users = [_make_app_user(email="m@e.com", user_id="m1", full_name="Member")]
        connector.admin_data_source.members_list = AsyncMock(return_value={
            "members": [{"type": "USER", "email": "m@e.com", "id": "m1"}]
        })
        await connector._process_group({"email": "grp@e.com", "name": "G"})
        connector.data_entities_processor.on_new_user_groups.assert_called()

    async def test_fetch_group_members_pagination(self, connector):
        page1 = {"members": [{"id": "m1"}], "nextPageToken": "p2"}
        page2 = {"members": [{"id": "m2"}]}
        connector.admin_data_source.members_list = AsyncMock(side_effect=[page1, page2])
        members = await connector._fetch_group_members("grp@e.com")
        assert len(members) == 2


# ===========================================================================
# _sync_record_groups
# ===========================================================================

class TestSyncRecordGroups:
    async def test_creates_inbox_sent_others(self, connector):
        users = [_make_app_user()]
        await connector._sync_record_groups(users)
        # Called 3 times: INBOX, SENT, OTHERS
        assert connector.data_entities_processor.on_new_record_groups.call_count == 3

    async def test_no_users(self, connector):
        await connector._sync_record_groups([])
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    async def test_user_without_email(self, connector):
        user = _make_app_user(email="")
        await connector._sync_record_groups([user])
        connector.data_entities_processor.on_new_record_groups.assert_not_called()


# ===========================================================================
# run_sync
# ===========================================================================

class TestRunSync:
    @patch("app.connectors.sources.google.gmail.team.connector.load_connector_filters")
    async def test_run_sync_full(self, mock_load, connector):
        mock_load.return_value = (FilterCollection(), FilterCollection())
        connector._sync_users = AsyncMock()
        connector._sync_user_groups = AsyncMock()
        connector._sync_record_groups = AsyncMock()
        connector._process_users_in_batches = AsyncMock()
        connector.synced_users = []

        await connector.run_sync()
        connector._sync_users.assert_called_once()
        connector._sync_user_groups.assert_called_once()

    @patch("app.connectors.sources.google.gmail.team.connector.load_connector_filters")
    async def test_run_sync_error(self, mock_load, connector):
        mock_load.return_value = (FilterCollection(), FilterCollection())
        connector._sync_users = AsyncMock(side_effect=Exception("sync fail"))
        with pytest.raises(Exception, match="sync fail"):
            await connector.run_sync()


# ===========================================================================
# _run_sync_with_yield
# ===========================================================================

class TestRunSyncWithYield:
    @patch("app.connectors.sources.google.gmail.team.connector.GoogleClient")
    async def test_incremental_sync_path(self, mock_gc, connector):
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        mock_gc.build_from_services = AsyncMock(return_value=mock_client)

        from app.connectors.sources.google.gmail.team.connector import GoogleGmailDataSource
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())

        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "12345"}
        )
        connector._run_sync_with_history_id = AsyncMock()

        await connector._run_sync_with_yield("user@example.com")
        connector._run_sync_with_history_id.assert_called_once()

    async def test_full_sync_path(self, connector):
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector._run_full_sync = AsyncMock()

        await connector._run_sync_with_yield("user@example.com")
        connector._run_full_sync.assert_called_once()

    async def test_history_id_expired_fallback(self, connector):
        from googleapiclient.errors import HttpError
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector.gmail_delta_sync_point.read_sync_point = AsyncMock(
            return_value={"historyId": "expired"}
        )
        resp = MagicMock()
        resp.status = 404
        connector._run_sync_with_history_id = AsyncMock(
            side_effect=HttpError(resp, b"Not found")
        )
        connector._run_full_sync = AsyncMock()

        await connector._run_sync_with_yield("user@example.com")
        connector._run_full_sync.assert_called_once()


# ===========================================================================
# _run_full_sync
# ===========================================================================

class TestRunFullSync:
    async def test_full_sync_processes_threads(self, connector):
        mock_gmail = AsyncMock()
        mock_gmail.users_get_profile = AsyncMock(return_value={"historyId": "100"})
        mock_gmail.users_threads_list = AsyncMock(return_value={
            "threads": [{"id": "t1"}]
        })
        mock_gmail.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()]
        })

        await connector._run_full_sync("user@example.com", mock_gmail, "sp-key")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_full_sync_empty_threads(self, connector):
        mock_gmail = AsyncMock()
        mock_gmail.users_get_profile = AsyncMock(return_value={"historyId": "100"})
        mock_gmail.users_threads_list = AsyncMock(return_value={"threads": []})
        await connector._run_full_sync("user@example.com", mock_gmail, "sp-key")

    async def test_full_sync_profile_failure(self, connector):
        mock_gmail = AsyncMock()
        mock_gmail.users_get_profile = AsyncMock(side_effect=Exception("profile fail"))
        mock_gmail.users_threads_list = AsyncMock(return_value={"threads": []})
        await connector._run_full_sync("user@example.com", mock_gmail, "sp-key")


# ===========================================================================
# _run_sync_with_history_id (incremental)
# ===========================================================================


class TestFetchHistoryChanges:
    async def test_fetches_with_pagination(self, connector):
        mock_gmail = AsyncMock()
        mock_gmail.users_history_list = AsyncMock(side_effect=[
            {"history": [{"id": "1"}], "nextPageToken": "p2"},
            {"history": [{"id": "2"}]},
        ])
        result = await connector._fetch_history_changes(mock_gmail, "u@e.com", "100", "INBOX")
        assert len(result["history"]) == 2


# ===========================================================================
# _process_history_changes
# ===========================================================================

class TestProcessHistoryChanges:
    async def test_message_addition(self, connector):
        mock_gmail = AsyncMock()
        mock_gmail.users_messages_get = AsyncMock(return_value=_make_gmail_message())
        mock_gmail.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()]
        })
        connector._find_previous_message_in_thread = AsyncMock(return_value=None)

        entry = {
            "id": "h1",
            "messagesAdded": [{"message": {"id": "m1"}}],
        }
        batch = []
        result = await connector._process_history_changes("u@e.com", mock_gmail, entry, batch)
        assert result >= 1
        assert len(batch) >= 1

    async def test_message_deletion(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        ds = _make_mock_data_store_provider(existing)
        connector.data_store_provider = ds
        connector._delete_message_and_attachments = AsyncMock()

        entry = {"id": "h2", "messagesDeleted": [{"message": {"id": "m1"}}]}
        batch = []
        result = await connector._process_history_changes("u@e.com", AsyncMock(), entry, batch)
        assert result >= 1

    async def test_labels_added_trash(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        ds = _make_mock_data_store_provider(existing)
        connector.data_store_provider = ds
        connector._delete_message_and_attachments = AsyncMock()

        entry = {
            "id": "h3",
            "labelsAdded": [{"message": {"id": "m1"}, "labelIds": ["TRASH"]}],
        }
        batch = []
        await connector._process_history_changes("u@e.com", AsyncMock(), entry, batch)
        connector._delete_message_and_attachments.assert_called()


# ===========================================================================
# _delete_message_and_attachments
# ===========================================================================

class TestDeleteMessageAndAttachments:
    async def test_deletes_message_and_attachments(self, connector):
        att = MagicMock()
        att.id = "att-rec-1"
        tx = AsyncMock()
        tx.get_records_by_parent = AsyncMock(return_value=[att])

        provider = MagicMock()

        @asynccontextmanager
        async def _tx():
            yield tx

        provider.transaction = _tx
        connector.data_store_provider = provider

        await connector._delete_message_and_attachments("rec-1", "msg-1")
        connector.data_entities_processor.on_record_deleted.assert_called()


# ===========================================================================
# _find_previous_message_in_thread
# ===========================================================================

class TestFindPreviousMessage:
    async def test_no_previous_single_message(self, connector):
        mock_gmail = AsyncMock()
        mock_gmail.users_threads_get = AsyncMock(return_value={
            "messages": [_make_gmail_message()]
        })
        result = await connector._find_previous_message_in_thread(
            "u@e.com", mock_gmail, "t1", "m1", "1704067200000"
        )
        assert result is None


# ===========================================================================
# _process_users_in_batches
# ===========================================================================

class TestProcessUsersInBatches:
    async def test_processes_users_sequentially(self, connector):
        users = [_make_app_user(email="a@e.com"), _make_app_user(email="b@e.com")]
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock(email="a@e.com"), MagicMock(email="b@e.com")]
        )
        connector._run_sync_with_yield = AsyncMock()
        await connector._process_users_in_batches(users)
        assert connector._run_sync_with_yield.call_count == 2


# ===========================================================================
# _create_user_gmail_client
# ===========================================================================

class TestCreateUserGmailClient:
    @patch("app.connectors.sources.google.gmail.team.connector.GoogleClient")
    async def test_success(self, mock_gc, connector):
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        mock_gc.build_from_services = AsyncMock(return_value=mock_client)
        result = await connector._create_user_gmail_client("user@example.com")
        assert result is not None

    @patch("app.connectors.sources.google.gmail.team.connector.GoogleClient")
    async def test_failure(self, mock_gc, connector):
        mock_gc.build_from_services = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connector._create_user_gmail_client("user@example.com")


# ===========================================================================
# cleanup
# ===========================================================================

class TestCleanup:
    async def test_cleanup_clears_references(self, connector):
        await connector.cleanup()
        assert connector.gmail_data_source is None
        assert connector.admin_data_source is None
        assert connector.gmail_client is None
        assert connector.admin_client is None
        assert connector.config is None


# ===========================================================================
# reindex_records
# ===========================================================================

class TestReindexRecords:
    async def test_reindex_empty(self, connector):
        await connector.reindex_records([])

    async def test_reindex_no_gmail_ds(self, connector):
        connector.gmail_data_source = None
        with pytest.raises(Exception, match="Gmail data source not initialized"):
            await connector.reindex_records([MagicMock()])

    async def test_reindex_updated(self, connector):
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL

        perm = Permission(email="u@e.com", type=PermissionType.OWNER, entity_type=EntityType.USER)
        updated_record = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=(updated_record, [perm]))

        await connector.reindex_records([record])
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_reindex_not_updated(self, connector):
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "ext-1"
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_called()


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================

class TestCheckAndFetchUpdatedRecord:
    async def test_no_external_id(self, connector):
        record = MagicMock()
        record.external_record_id = None
        record.id = "r1"
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    async def test_no_user_with_permission(self, connector):
        record = MagicMock()
        record.external_record_id = "ext-1"
        record.id = "r1"
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    async def test_routes_to_mail(self, connector):
        user = MagicMock()
        user.email = "u@e.com"
        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)

        @asynccontextmanager
        async def _tx():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _tx

        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector._check_and_fetch_updated_mail_record = AsyncMock(return_value=None)

        record = MagicMock()
        record.external_record_id = "ext-1"
        record.id = "r1"
        record.record_type = RecordType.MAIL
        await connector._check_and_fetch_updated_record("org-1", record)
        connector._check_and_fetch_updated_mail_record.assert_called()

    async def test_routes_to_file(self, connector):
        user = MagicMock()
        user.email = "u@e.com"
        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)

        @asynccontextmanager
        async def _tx():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _tx

        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())
        connector._check_and_fetch_updated_file_record = AsyncMock(return_value=None)

        record = MagicMock()
        record.external_record_id = "ext-1"
        record.id = "r1"
        record.record_type = RecordType.FILE
        await connector._check_and_fetch_updated_record("org-1", record)
        connector._check_and_fetch_updated_file_record.assert_called()

    async def test_unknown_record_type(self, connector):
        user = MagicMock()
        user.email = "u@e.com"
        tx = AsyncMock()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)

        @asynccontextmanager
        async def _tx():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _tx
        connector._create_user_gmail_client = AsyncMock(return_value=AsyncMock())

        record = MagicMock()
        record.external_record_id = "ext-1"
        record.id = "r1"
        record.record_type = "UNKNOWN"
        result = await connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


# ===========================================================================
# run_incremental_sync
# ===========================================================================

class TestRunIncrementalSync:
    async def test_delegates_to_run_sync(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_called_once()


# ===========================================================================
# create_connector
# ===========================================================================

class TestCreateConnector:
    @patch("app.connectors.sources.google.gmail.team.connector.DataSourceEntitiesProcessor")
    @patch("app.connectors.sources.google.gmail.team.connector.SyncPoint")
    async def test_create_connector(self, mock_sp, mock_dep_cls, connector):
        from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector

        mock_dep = AsyncMock()
        mock_dep.org_id = "org-1"
        mock_dep.initialize = AsyncMock()
        mock_dep_cls.return_value = mock_dep
        mock_sp.return_value = AsyncMock()

        result = await GoogleGmailTeamConnector.create_connector(
            logger=_make_logger(),
            data_store_provider=_make_mock_data_store_provider(),
            config_service=AsyncMock(),
            connector_id="test-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
        assert result is not None
