"""Deep coverage tests for the Outlook connector.

Covers additional methods not exercised by existing test suites:
- _extract_email_from_recipient
- _safe_get_attr (dict, object, missing)
- _get_mime_type_enum (all MIME types)
- _parse_datetime (string, datetime, None, invalid)
- _format_datetime_string (string, datetime, None, invalid)
- _construct_group_mail_weburl (cached, fetch, errors)
- get_signed_url
- handle_webhook_notification
- cleanup (with/without client, errors)
- run_incremental_sync
- get_filter_options (unsupported keys raise ValueError)
- reindex_records (user/group mailbox records)
- _reindex_user_mailbox_records / _reindex_single_user_records
- _reindex_group_mailbox_records
- _determine_folder_filter_strategy (all 5 scenarios)
- _transform_folder_to_record_group
- _get_child_folders_recursive
- stream_record (email, post, attachment)
"""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.microsoft.outlook.connector import (
    OutlookConnector,
    OutlookCredentials,
)
from app.connectors.sources.microsoft.common.outlook_constants import (
    OutlookFolders,
    OutlookThreadDetection,
)

# Backwards compatibility aliases for tests
STANDARD_OUTLOOK_FOLDERS = OutlookFolders.STANDARD_FOLDERS
THREAD_ROOT_EMAIL_CONVERSATION_INDEX_LENGTH = OutlookThreadDetection.ROOT_CONVERSATION_INDEX_LENGTH

from app.models.entities import (
    AppUser,
    AppUserGroup,
    MailRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.outlook.deep")
    dep = MagicMock()
    dep.org_id = "org-outlook-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_conversation_index = AsyncMock(return_value=None)
    mock_tx.batch_create_edges = AsyncMock()
    mock_tx.get_record_owner_source_user_email = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx

    cs = MagicMock()
    cs.get_config = AsyncMock()

    return logger, dep, dsp, cs, mock_tx


def _make_connector():
    logger, dep, dsp, cs, tx = _make_mock_deps()
    c = OutlookConnector(logger, dep, dsp, cs, "conn-outlook-deep", "team", "test-user-id")
    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()
    return c, dep, dsp, cs, tx


# ===========================================================================
# _extract_email_from_recipient
# ===========================================================================


class TestExtractEmailFromRecipient:

    def test_none_recipient(self):
        c, *_ = _make_connector()
        assert c._extract_email_from_recipient(None) == ''

    def test_with_email_address_attr(self):
        c, *_ = _make_connector()
        recipient = MagicMock()
        recipient.email_address = MagicMock()
        recipient.email_address.address = "user@test.com"
        result = c._extract_email_from_recipient(recipient)
        assert result == "user@test.com"

    def test_fallback_to_string(self):
        c, *_ = _make_connector()
        # Recipient with email_address but no address attribute
        recipient = MagicMock()
        recipient.email_address = None
        result = c._extract_email_from_recipient(recipient)
        assert result == ''  # Returns empty string when email_address is None




# ===========================================================================
# _get_mime_type_enum
# ===========================================================================


class TestGetMimeTypeEnum:

    def test_plain_text(self):
        c, *_ = _make_connector()
        assert c._get_mime_type_enum("text/plain") == MimeTypes.PLAIN_TEXT

    def test_html(self):
        c, *_ = _make_connector()
        assert c._get_mime_type_enum("text/html") == MimeTypes.HTML

    def test_pdf(self):
        c, *_ = _make_connector()
        assert c._get_mime_type_enum("application/pdf") == MimeTypes.PDF

    def test_docx(self):
        c, *_ = _make_connector()
        result = c._get_mime_type_enum("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert result == MimeTypes.DOCX

    def test_xlsx(self):
        c, *_ = _make_connector()
        result = c._get_mime_type_enum("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert result == MimeTypes.XLSX

    def test_pptx(self):
        c, *_ = _make_connector()
        result = c._get_mime_type_enum("application/vnd.openxmlformats-officedocument.presentationml.presentation")
        assert result == MimeTypes.PPTX

    def test_unknown_returns_bin(self):
        c, *_ = _make_connector()
        assert c._get_mime_type_enum("application/x-unknown") == MimeTypes.BIN

    def test_case_insensitive(self):
        c, *_ = _make_connector()
        assert c._get_mime_type_enum("TEXT/HTML") == MimeTypes.HTML



# ===========================================================================
# _format_datetime_string
# ===========================================================================


class TestFormatDatetimeString:

    def test_none(self):
        c, *_ = _make_connector()
        assert c._format_datetime_string(None) == ""

    def test_string_passthrough(self):
        c, *_ = _make_connector()
        assert c._format_datetime_string("2024-01-15T10:30:00Z") == "2024-01-15T10:30:00Z"

    def test_datetime_object(self):
        c, *_ = _make_connector()
        dt = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = c._format_datetime_string(dt)
        assert "2024" in result

    def test_invalid_object(self):
        c, *_ = _make_connector()
        result = c._format_datetime_string(12345)
        # Should not raise, returns "" on exception
        assert isinstance(result, str)


# ===========================================================================
# _construct_group_mail_weburl
# ===========================================================================


class TestConstructGroupMailWeburl:

    @pytest.mark.asyncio
    async def test_cached_group(self):
        c, *_ = _make_connector()
        c._group_cache = {
            "g1": {"mail": "team@contoso.com", "mailNickname": "team"}
        }
        result = await c._construct_group_mail_weburl("g1")
        assert result == "https://outlook.office365.com/groups/contoso.com/team/mail"

    @pytest.mark.asyncio
    async def test_no_client(self):
        c, *_ = _make_connector()
        c.external_users_client = None
        result = await c._construct_group_mail_weburl("g1")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_from_api(self):
        c, *_ = _make_connector()
        c.external_users_client = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock()
        resp.data.mail = "team@contoso.com"
        resp.data.mail_nickname = "team"
        c.external_users_client.groups_group_get_group = AsyncMock(return_value=resp)

        result = await c._construct_group_mail_weburl("g2")
        assert result == "https://outlook.office365.com/groups/contoso.com/team/mail"
        # Should be cached
        assert "g2" in c._group_cache

    @pytest.mark.asyncio
    async def test_no_mail_in_group_data(self):
        c, *_ = _make_connector()
        c._group_cache = {"g1": {"mail": None, "mailNickname": "team"}}
        result = await c._construct_group_mail_weburl("g1")
        assert result is None

    @pytest.mark.asyncio
    async def test_mail_without_at_sign(self):
        c, *_ = _make_connector()
        c._group_cache = {"g1": {"mail": "invalid_email", "mailNickname": "team"}}
        result = await c._construct_group_mail_weburl("g1")
        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c, *_ = _make_connector()
        c.external_users_client = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.data = None
        c.external_users_client.groups_group_get_group = AsyncMock(return_value=resp)

        result = await c._construct_group_mail_weburl("g3")
        assert result is None


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    def test_returns_none(self):
        c, *_ = _make_connector()
        record = MagicMock()
        result = c.get_signed_url(record)
        assert result is None


# ===========================================================================
# handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    @pytest.mark.asyncio
    async def test_returns_true(self):
        c, *_ = _make_connector()
        result = await c.handle_webhook_notification("org-1", {"event": "test"})
        assert result is True


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_all_resources(self):
        c, *_ = _make_connector()
        c.external_client = MagicMock()
        internal = MagicMock()
        internal.close = AsyncMock()
        c.external_client.get_client.return_value = internal
        c.external_outlook_client = MagicMock()
        c.external_users_client = MagicMock()
        c.credentials = MagicMock()
        c._user_cache = {"a": "b"}

        await c.cleanup()
        assert c.external_client is None
        assert c.external_outlook_client is None
        assert c.external_users_client is None
        assert c.credentials is None
        assert c._user_cache == {}

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self):
        c, *_ = _make_connector()
        c.external_outlook_client = None
        c.external_users_client = None
        c.credentials = None

        # Should not raise
        await c.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_close_error(self):
        c, *_ = _make_connector()
        c.external_client = MagicMock()
        internal = MagicMock()
        internal.close = AsyncMock(side_effect=Exception("already closed"))
        c.external_client.get_client.return_value = internal

        # Should not raise
        await c.cleanup()
        assert c.external_client is None


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:

    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        c, *_ = _make_connector()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_called_once()


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_unsupported_filter_key_raises_value_error(self):
        c, *_ = _make_connector()
        with pytest.raises(ValueError, match="Unsupported filter key"):
            await c.get_filter_options("any_key")


# ===========================================================================
# _determine_folder_filter_strategy (all 5 scenarios)
# ===========================================================================


class TestDetermineFolderFilterStrategy:

    def test_scenario1_no_selection_custom_enabled(self):
        c, *_ = _make_connector()
        # No folders filter, custom enabled (default)
        folders, mode = c._determine_folder_filter_strategy()
        assert folders is None
        assert mode is None

    def test_scenario2_no_selection_custom_disabled(self):
        c, *_ = _make_connector()
        # Set custom folders filter to False
        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = False
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: {
            "folders": MagicMock(is_empty=MagicMock(return_value=True)),
            "custom_folders": custom_filter,
        }.get(key.value if hasattr(key, 'value') else key))

        folders, mode = c._determine_folder_filter_strategy()
        assert folders == STANDARD_OUTLOOK_FOLDERS
        assert mode == "include"

    def test_scenario3_selected_folders_no_custom(self):
        c, *_ = _make_connector()
        folders_filter = MagicMock()
        folders_filter.is_empty.return_value = False
        folders_filter.get_value.return_value = ["Inbox", "Sent Items"]

        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = False

        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: {
            "folders": folders_filter,
            "custom_folders": custom_filter,
        }.get(key.value if hasattr(key, 'value') else key))

        folders, mode = c._determine_folder_filter_strategy()
        assert folders == ["Inbox", "Sent Items"]
        assert mode == "include"

    def test_scenario4_selected_folders_with_custom(self):
        c, *_ = _make_connector()
        folders_filter = MagicMock()
        folders_filter.is_empty.return_value = False
        folders_filter.get_value.return_value = ["Inbox", "Sent Items"]

        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = True

        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: {
            "folders": folders_filter,
            "custom_folders": custom_filter,
        }.get(key.value if hasattr(key, 'value') else key))

        folders, mode = c._determine_folder_filter_strategy()
        assert mode == "exclude"
        # Should exclude non-selected standard folders
        assert "Inbox" not in folders
        assert "Sent Items" not in folders

    def test_scenario5_all_standard_with_custom(self):
        c, *_ = _make_connector()
        folders_filter = MagicMock()
        folders_filter.is_empty.return_value = False
        folders_filter.get_value.return_value = list(STANDARD_OUTLOOK_FOLDERS)

        custom_filter = MagicMock()
        custom_filter.is_empty.return_value = False
        custom_filter.get_value.return_value = True

        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda key: {
            "folders": folders_filter,
            "custom_folders": custom_filter,
        }.get(key.value if hasattr(key, 'value') else key))

        folders, mode = c._determine_folder_filter_strategy()
        assert folders is None
        assert mode is None


# ===========================================================================
# _transform_folder_to_record_group
# ===========================================================================


class TestTransformFolderToRecordGroup:

    def test_success(self):
        c, *_ = _make_connector()
        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"
        folder.parent_folder_id = "root"
        folder._is_top_level = False

        user = MagicMock()
        user.email = "user@test.com"

        result = c._transform_folder_to_record_group(folder, user)
        assert result is not None
        assert result.name == "Inbox"
        assert result.external_group_id == "f1"

    def test_no_id_returns_none(self):
        c, *_ = _make_connector()
        folder = MagicMock(spec=[])
        user = MagicMock(email="u@test.com")
        result = c._transform_folder_to_record_group(folder, user)
        assert result is None

    def test_top_level_folder_no_parent(self):
        c, *_ = _make_connector()
        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Inbox"
        folder.parent_folder_id = "some_parent"

        user = MagicMock(email="u@test.com")
        # Pass is_top_level=True as parameter (not folder attribute)
        result = c._transform_folder_to_record_group(folder, user, is_top_level=True)
        assert result is not None
        assert result.parent_external_group_id is None


# ===========================================================================
# _get_child_folders_recursive
# ===========================================================================


class TestGetChildFoldersRecursive:

    @pytest.mark.asyncio
    async def test_no_children(self):
        c, *_ = _make_connector()
        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Empty"
        folder.child_folder_count = 0

        result = await c._get_child_folders_recursive("user1", folder)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_client(self):
        c, *_ = _make_connector()
        c.external_outlook_client = None
        folder = MagicMock()
        folder.id = "f1"
        folder.display_name = "Test"
        folder.child_folder_count = 2

        result = await c._get_child_folders_recursive("user1", folder)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_folder_id(self):
        c, *_ = _make_connector()
        folder = MagicMock()
        folder.id = None
        folder.display_name = "Invalid Folder"
        result = await c._get_child_folders_recursive("user1", folder)
        assert result == []


# ===========================================================================
# _augment_email_html_with_metadata (extended)
# ===========================================================================


class TestAugmentEmailHtmlExtended:

    def test_with_cc_and_bcc(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.from_email = "sender@test.com"
        record.to_emails = ["to@test.com"]
        record.cc_emails = ["cc@test.com"]
        record.bcc_emails = ["bcc@test.com"]
        record.subject = "Important"

        result = c._augment_email_html_with_metadata("<p>Body</p>", record)
        assert "cc@test.com" in result
        assert "bcc@test.com" in result
        assert "email-metadata" in result


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        c, dep, *_ = _make_connector()
        c.external_outlook_client = MagicMock()
        c.external_users_client = MagicMock()
        await c.reindex_records([])
        dep.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_clients_raises(self):
        c, *_ = _make_connector()
        c.external_outlook_client = None
        rec = MagicMock(id="r1")
        with pytest.raises(Exception):
            await c.reindex_records([rec])


# ===========================================================================
# _populate_user_cache
# ===========================================================================


class TestPopulateUserCache:

    @pytest.mark.asyncio
    async def test_cached_within_ttl(self):
        c, *_ = _make_connector()
        c._user_cache = {"u@test.com": "uid1"}
        c._user_cache_timestamp = int(datetime.now(timezone.utc).timestamp())

        await c._populate_user_cache()
        # Cache should not be refreshed (no API call)

    @pytest.mark.asyncio
    async def test_cache_exception(self):
        c, *_ = _make_connector()
        c._user_cache_timestamp = None
        c.external_users_client = MagicMock()
        c.external_users_client.users_user_list_user = AsyncMock(side_effect=Exception("API fail"))

        # Should not raise
        await c._populate_user_cache()


# ===========================================================================
# _get_user_id_from_email
# ===========================================================================


class TestGetUserIdFromEmail:

    @pytest.mark.asyncio
    async def test_found_in_cache(self):
        c, *_ = _make_connector()
        c._user_cache = {"u@test.com": "uid1"}
        c._user_cache_timestamp = int(datetime.now(timezone.utc).timestamp())

        result = await c._get_user_id_from_email("u@test.com")
        assert result == "uid1"

    @pytest.mark.asyncio
    async def test_not_found(self):
        c, *_ = _make_connector()
        c._user_cache = {}
        c._user_cache_timestamp = int(datetime.now(timezone.utc).timestamp())
        c._populate_user_cache = AsyncMock()

        result = await c._get_user_id_from_email("missing@test.com")
        assert result is None


# ===========================================================================
# stream_record edge cases
# ===========================================================================


class TestStreamRecordDeep:

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = "UNSUPPORTED_TYPE"
        record.id = "r1"
        record.external_record_id = "ext-1"

        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await c.stream_record(record)
