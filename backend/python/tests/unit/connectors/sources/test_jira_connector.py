"""Tests for app.connectors.sources.atlassian.jira_cloud.connector."""

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.connectors.sources.atlassian.jira_cloud.connector import (
    BATCH_PROCESSING_SIZE,
    DEFAULT_MAX_RESULTS,
    ISSUE_SEARCH_FIELDS,
    JiraConnector,
    adf_to_text,
    extract_media_from_adf,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus, RecordRelations
from app.connectors.sources.atlassian.jira_cloud.connector import (
    BATCH_PROCESSING_SIZE,
    DEFAULT_MAX_RESULTS,
    ISSUE_SEARCH_FIELDS,
    JiraConnector,
    adf_to_text,
    adf_to_text_with_images,
    extract_media_from_adf,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    RecordGroupType,
    RecordType,
    TicketRecord,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.jira")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-jira-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="active@example.com"),
    ])
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

    data_store_provider = MagicMock()
    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return JiraConnector(logger, dep, dsp, cs, "conn-jira-1", "team", "test-user-id")


def _make_mock_response(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


# ===========================================================================
# Constants
# ===========================================================================


class TestJiraConstants:

    def test_default_max_results(self):
        assert DEFAULT_MAX_RESULTS == 50

    def test_batch_processing_size(self):
        assert BATCH_PROCESSING_SIZE == 100

    def test_issue_search_fields(self):
        assert "summary" in ISSUE_SEARCH_FIELDS
        assert "description" in ISSUE_SEARCH_FIELDS
        assert "status" in ISSUE_SEARCH_FIELDS
        assert "priority" in ISSUE_SEARCH_FIELDS
        assert "assignee" in ISSUE_SEARCH_FIELDS


# ===========================================================================
# extract_media_from_adf
# ===========================================================================


class TestExtractMediaFromAdf:

    def test_empty_content(self):
        assert extract_media_from_adf(None) == []
        assert extract_media_from_adf({}) == []
        assert extract_media_from_adf("not a dict") == []

    def test_no_media_nodes(self):
        adf = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}
            ],
        }
        result = extract_media_from_adf(adf)
        assert result == []

    def test_media_node_extracted(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "mediaSingle",
                    "content": [
                        {
                            "type": "media",
                            "attrs": {
                                "id": "media-123",
                                "alt": "screenshot.png",
                                "type": "file",
                            },
                        }
                    ],
                }
            ],
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["id"] == "media-123"
        assert result[0]["filename"] == "screenshot.png"

    def test_media_with_internal_filename(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "media",
                    "attrs": {
                        "id": "media-456",
                        "alt": "alt-text",
                        "__fileName": "document.pdf",
                        "type": "file",
                    },
                }
            ],
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["filename"] == "document.pdf"

    def test_media_without_id_excluded(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "media",
                    "attrs": {"alt": "no-id-media"},
                }
            ],
        }
        result = extract_media_from_adf(adf)
        assert result == []


# ===========================================================================
# adf_to_text
# ===========================================================================


class TestAdfToText:

    def test_empty_content(self):
        assert adf_to_text(None) == ""
        assert adf_to_text({}) == ""

    def test_simple_paragraph(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello World"}],
                }
            ],
        }
        result = adf_to_text(adf)
        assert "Hello World" in result

    def test_heading(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "My Heading"}],
                }
            ],
        }
        result = adf_to_text(adf)
        assert "## My Heading" in result

    def test_bold_text(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "bold",
                            "marks": [{"type": "strong"}],
                        }
                    ],
                }
            ],
        }
        result = adf_to_text(adf)
        assert "**bold**" in result

    def test_bullet_list(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item 1"}],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item 2"}],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        result = adf_to_text(adf)
        assert "- Item 1" in result
        assert "- Item 2" in result

    def test_code_block(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": "print('hello')"}],
                }
            ],
        }
        result = adf_to_text(adf)
        assert "```python" in result
        assert "print('hello')" in result

    def test_mention(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "mention", "attrs": {"text": "John Doe"}},
                    ],
                }
            ],
        }
        result = adf_to_text(adf)
        assert "@John Doe" in result

    def test_inline_card(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "inlineCard", "attrs": {"url": "https://example.com"}},
                    ],
                }
            ],
        }
        result = adf_to_text(adf)
        assert "https://example.com" in result

    def test_rule(self):
        adf = {
            "type": "doc",
            "content": [{"type": "rule"}],
        }
        result = adf_to_text(adf)
        assert "---" in result


# ===========================================================================
# JiraConnector.__init__
# ===========================================================================


class TestJiraConnectorInit:

    def test_connector_initializes(self):
        connector = _make_connector()
        assert connector.connector_name == Connectors.JIRA
        assert connector.connector_id == "conn-jira-1"
        assert connector.external_client is None
        assert connector.data_source is None
        assert connector.cloud_id is None
        assert connector.site_url is None

    def test_sync_points_initialized(self):
        connector = _make_connector()
        assert connector.issues_sync_point is not None

    def test_value_mapper_initialized(self):
        connector = _make_connector()
        assert connector.value_mapper is not None


# ===========================================================================
# JiraConnector.init
# ===========================================================================


class TestJiraConnectorInitMethod:

    @pytest.mark.asyncio
    async def test_init_with_api_token(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "baseUrl": "https://company.atlassian.net"},
            "credentials": {"access_token": "token-123"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_jira_client:
            mock_jira_client.build_from_services = AsyncMock(return_value=MagicMock())
            result = await connector.init()

        assert result is True
        assert connector.site_url == "https://company.atlassian.net"

    @pytest.mark.asyncio
    async def test_init_failure(self):
        connector = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_jira_client:
            mock_jira_client.build_from_services = AsyncMock(side_effect=Exception("Auth error"))
            result = await connector.init()

        assert result is False


# ===========================================================================
# JiraConnector._get_access_token
# ===========================================================================


class TestGetAccessToken:

    @pytest.mark.asyncio
    async def test_returns_token(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "my-token"},
        })

        token = await connector._get_access_token()
        assert token == "my-token"

    @pytest.mark.asyncio
    async def test_raises_when_no_token(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {},
        })

        with pytest.raises(ValueError, match="not found"):
            await connector._get_access_token()


# ===========================================================================
# JiraConnector._get_fresh_datasource
# ===========================================================================


class TestGetFreshDatasource:

    @pytest.mark.asyncio
    async def test_raises_when_client_not_initialized(self):
        connector = _make_connector()
        connector.external_client = None

        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_api_token_returns_existing_datasource(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource") as mock_ds:
            result = await connector._get_fresh_datasource()
            mock_ds.assert_called_once_with(connector.external_client)

    @pytest.mark.asyncio
    async def test_raises_when_no_config(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()


# ===========================================================================
# JiraConnector._get_issues_sync_checkpoint
# ===========================================================================


class TestGetIssuesSyncCheckpoint:

    @pytest.mark.asyncio
    async def test_returns_last_sync_time(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1700000000}
        )

        result = await connector._get_issues_sync_checkpoint()
        assert result == 1700000000

    @pytest.mark.asyncio
    async def test_returns_none_when_no_checkpoint(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)

        result = await connector._get_issues_sync_checkpoint()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("DB error")
        )

        result = await connector._get_issues_sync_checkpoint()
        assert result is None


# ===========================================================================
# JiraConnector._update_issues_sync_checkpoint
# ===========================================================================


class TestUpdateIssuesSyncCheckpoint:

    @pytest.mark.asyncio
    async def test_updates_when_issues_synced(self):
        connector = _make_connector()
        connector.issues_sync_point.update_sync_point = AsyncMock()

        stats = {"total_synced": 10, "new_count": 5, "updated_count": 5}
        await connector._update_issues_sync_checkpoint(stats, 3)

        connector.issues_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_when_projects_found(self):
        connector = _make_connector()
        connector.issues_sync_point.update_sync_point = AsyncMock()

        stats = {"total_synced": 0, "new_count": 0, "updated_count": 0}
        await connector._update_issues_sync_checkpoint(stats, 5)

        connector.issues_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_update_when_nothing_synced(self):
        connector = _make_connector()
        connector.issues_sync_point.update_sync_point = AsyncMock()

        stats = {"total_synced": 0, "new_count": 0, "updated_count": 0}
        await connector._update_issues_sync_checkpoint(stats, 0)

        connector.issues_sync_point.update_sync_point.assert_not_awaited()


# ===========================================================================
# JiraConnector._fetch_users
# ===========================================================================


class TestFetchUsers:

    @pytest.mark.asyncio
    async def test_raises_when_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None

        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_users()

    @pytest.mark.asyncio
    async def test_fetches_users_single_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        users_data = [
            {"accountId": "u1", "emailAddress": "user1@example.com", "displayName": "User 1", "active": True},
            {"accountId": "u2", "emailAddress": "user2@example.com", "displayName": "User 2", "active": True},
            {"accountId": "u3", "active": False, "emailAddress": "inactive@example.com", "displayName": "Inactive"},
        ]
        mock_ds.get_all_users = AsyncMock(return_value=_make_mock_response(200, users_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users = await connector._fetch_users()
        # Only active users with email are returned
        assert len(users) == 2
        assert users[0].email == "user1@example.com"
        assert users[1].email == "user2@example.com"

    @pytest.mark.asyncio
    async def test_skips_users_without_email(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        users_data = [
            {"accountId": "u1", "displayName": "No Email", "active": True},
        ]
        mock_ds.get_all_users = AsyncMock(return_value=_make_mock_response(200, users_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        users = await connector._fetch_users()
        assert len(users) == 0


# ===========================================================================
# JiraConnector._fetch_groups / _fetch_group_members
# ===========================================================================


class TestFetchGroups:

    @pytest.mark.asyncio
    async def test_fetches_groups(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        groups_data = {
            "values": [
                {"groupId": "g1", "name": "developers"},
                {"groupId": "g2", "name": "admins"},
            ],
            "isLast": True,
        }
        mock_ds.bulk_get_groups = AsyncMock(return_value=_make_mock_response(200, groups_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        groups = await connector._fetch_groups()
        assert len(groups) == 2
        assert groups[0]["name"] == "developers"

    @pytest.mark.asyncio
    async def test_fetches_group_members(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        members_data = {
            "values": [
                {"emailAddress": "dev1@example.com"},
                {"emailAddress": "dev2@example.com"},
            ],
            "isLast": True,
        }
        mock_ds.get_users_from_group = AsyncMock(return_value=_make_mock_response(200, members_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        emails = await connector._fetch_group_members("g1", "developers")
        assert len(emails) == 2
        assert "dev1@example.com" in emails


# ===========================================================================
# JiraConnector._handle_deleted_issue
# ===========================================================================


class TestHandleDeletedIssue:

    @pytest.mark.asyncio
    async def test_issue_still_exists_in_jira(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # Should log warning and return (issue not actually deleted)
        await connector._handle_deleted_issue("PROJ-123")

    @pytest.mark.asyncio
    async def test_issue_not_in_database(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(404, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        # Should warn and return - issue not in DB
        await connector._handle_deleted_issue("PROJ-404")


# ===========================================================================
# JiraConnector._delete_issue_children
# ===========================================================================


class TestDeleteIssueChildren:

    @pytest.mark.asyncio
    async def test_no_children_to_delete(self):
        connector = _make_connector()
        mock_tx_store = AsyncMock()
        mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])

        count = await connector._delete_issue_children("issue-1", RecordType.FILE, mock_tx_store)
        assert count == 0

    @pytest.mark.asyncio
    async def test_deletes_file_children(self):
        connector = _make_connector()
        mock_tx_store = AsyncMock()
        child_record = MagicMock()
        child_record.id = "file-1"
        child_record.external_record_id = "ext-file-1"
        mock_tx_store.get_records_by_parent = AsyncMock(return_value=[child_record])
        mock_tx_store.delete_records_and_relations = AsyncMock()

        count = await connector._delete_issue_children("issue-1", RecordType.FILE, mock_tx_store)
        assert count == 1
        mock_tx_store.delete_records_and_relations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_zero_on_error(self):
        connector = _make_connector()
        mock_tx_store = AsyncMock()
        mock_tx_store.get_records_by_parent = AsyncMock(side_effect=Exception("DB error"))

        count = await connector._delete_issue_children("issue-1", RecordType.TICKET, mock_tx_store)
        assert count == 0


# ===========================================================================
# JiraConnector.get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_unsupported_filter_key_raises(self):
        connector = _make_connector()

        with pytest.raises(ValueError, match="Unsupported"):
            await connector.get_filter_options("unsupported_key")

    @pytest.mark.asyncio
    async def test_project_keys_filter(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(200, {
            "values": [
                {"key": "PROJ1", "name": "Project One"},
                {"key": "PROJ2", "name": "Project Two"},
            ],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.get_filter_options("project_keys", page=1, limit=20)
        assert result.success is True
        assert len(result.options) == 2
        assert result.options[0].id == "PROJ1"

# =============================================================================
# Merged from test_jira_connector_full_coverage.py
# =============================================================================

def _make_mock_deps_fullcov():
    logger = logging.getLogger("test.jira.full")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-jira-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_new_app_roles = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="active@example.com"),
    ])
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

    data_store_provider = MagicMock()
    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps_fullcov()
    return JiraConnector(logger, dep, dsp, cs, "conn-jira-1", "team", "test-user-id")


def _make_mock_response_fullcov(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    resp.bytes = MagicMock(return_value=b"file-bytes")
    return resp


def _make_app_user(email="user@example.com", account_id="acc-1", name="User One"):
    return AppUser(
        app_name=Connectors.JIRA,
        connector_id="conn-jira-1",
        source_user_id=account_id,
        org_id="org-jira-1",
        email=email,
        full_name=name,
        is_active=True,
    )


def _make_ticket_record(external_id="12345", issue_key="PROJ-1", version=1, **kwargs):
    defaults = dict(
        id=str(uuid4()),
        org_id="org-jira-1",
        record_name=f"[{issue_key}] Test Issue",
        record_type=RecordType.TICKET,
        external_record_id=external_id,
        version=version,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="conn-jira-1",
        mime_type=MimeTypes.BLOCKS.value,
        source_created_at=1700000000000,
        source_updated_at=1700000000000,
        weburl="https://company.atlassian.net/browse/PROJ-1",
        external_record_group_id="proj-id-1",
        parent_external_record_id=None,
        record_group_type=RecordGroupType.PROJECT,
    )
    defaults.update(kwargs)
    return TicketRecord(**defaults)


def _make_file_record(attachment_id="99", issue_id="12345", version=0, **kwargs):
    defaults = dict(
        id=str(uuid4()),
        org_id="org-jira-1",
        record_name="screenshot.png",
        record_type=RecordType.FILE,
        external_record_id=f"attachment_{attachment_id}",
        version=version,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="conn-jira-1",
        mime_type="image/png",
        parent_external_record_id=issue_id,
        parent_record_type=RecordType.TICKET,
        external_record_group_id="proj-id-1",
        record_group_type=RecordGroupType.PROJECT,
        is_file=True,
        source_updated_at=1700000000000,
    )
    defaults.update(kwargs)
    return FileRecord(**defaults)


class TestAdfToTextAdvanced:

    def test_non_dict_input(self):
        assert adf_to_text("not a dict") == ""
        assert adf_to_text(42) == ""

    def test_italic_text(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "italic", "marks": [{"type": "em"}]}],
            }],
        }
        assert "*italic*" in adf_to_text(adf)

    def test_strikethrough_text(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "deleted", "marks": [{"type": "strike"}]}],
            }],
        }
        assert "~~deleted~~" in adf_to_text(adf)

    def test_code_mark(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "code", "marks": [{"type": "code"}]}],
            }],
        }
        assert "`code`" in adf_to_text(adf)

    def test_link_mark(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "click",
                    "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "[click](https://example.com)" in result

    def test_underline_mark(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "underline", "marks": [{"type": "underline"}]}],
            }],
        }
        assert "*underline*" in adf_to_text(adf)

    def test_ordered_list(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "orderedList",
                "content": [
                    {"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "First"}]}
                    ]},
                    {"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]}
                    ]},
                ],
            }],
        }
        result = adf_to_text(adf)
        assert "1. First" in result
        assert "2. Second" in result

    def test_blockquote(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "blockquote",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "quoted text"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "> quoted text" in result

    def test_hard_break(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "before"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "after"},
                ],
            }],
        }
        result = adf_to_text(adf)
        assert "before" in result
        assert "after" in result

    def test_media_with_cache(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "media",
                "attrs": {"id": "m1", "alt": "img.png"},
            }],
        }
        cache = {"m1": "data:image/png;base64,AAAA"}
        result = adf_to_text(adf, media_cache=cache)
        assert "![img.png](data:image/png;base64,AAAA)" in result

    def test_media_without_cache(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "media",
                "attrs": {"id": "m1", "alt": "img.png"},
            }],
        }
        result = adf_to_text(adf)
        assert "![img.png]" in result

    def test_emoji(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "emoji", "attrs": {"shortName": "smile"}}],
            }],
        }
        result = adf_to_text(adf)
        assert ":smile:" in result

    def test_table(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [
                        {"type": "tableHeader", "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Header"}]}
                        ]},
                    ],
                }, {
                    "type": "tableRow",
                    "content": [
                        {"type": "tableCell", "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Cell"}]}
                        ]},
                    ],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "Header" in result
        assert "Cell" in result

    def test_status_node(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "status", "attrs": {"text": "IN PROGRESS"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "[IN PROGRESS]" in result

    def test_date_node(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "date", "attrs": {"timestamp": "1700000000000"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "2023-11-14" in result

    def test_date_node_invalid_timestamp(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "date", "attrs": {"timestamp": "not-a-number"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "not-a-number" in result

    def test_expand_node(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "expand",
                "attrs": {"title": "Details"},
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "hidden text"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "**Details**" in result
        assert "hidden text" in result

    def test_panel_node(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "panel",
                "attrs": {"panelType": "warning"},
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "warning text"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "WARNING" in result
        assert "warning text" in result

    def test_task_list(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "taskList",
                "content": [{
                    "type": "taskItem",
                    "attrs": {"state": "DONE"},
                    "content": [{"type": "text", "text": "done task"}],
                }, {
                    "type": "taskItem",
                    "attrs": {"state": "TODO"},
                    "content": [{"type": "text", "text": "todo task"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "[x] done task" in result
        assert "[ ] todo task" in result

    def test_decision_list(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "decisionList",
                "content": [{
                    "type": "decisionItem",
                    "attrs": {"state": "DECIDED"},
                    "content": [{"type": "text", "text": "decided"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "✓ decided" in result

    def test_layout_section(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "layoutSection",
                "content": [{
                    "type": "layoutColumn",
                    "content": [{
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "col1"}],
                    }],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "col1" in result

    def test_placeholder(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "placeholder", "attrs": {"text": "Type here..."}}],
            }],
        }
        result = adf_to_text(adf)
        assert "Type here..." in result

    def test_media_single_wrapper(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{"type": "media", "attrs": {"id": "x", "alt": "file.png"}}],
            }],
        }
        result = adf_to_text(adf)
        assert "![file.png]" in result

    def test_inline_code(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "inlineCode", "text": "var x"}],
            }],
        }
        result = adf_to_text(adf)
        assert "`var x`" in result

    def test_fallback_content_node(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "unknownType",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "fallback"}],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "fallback" in result

    def test_non_dict_node(self):
        adf = {
            "type": "doc",
            "content": [42, None, "string"],
        }
        result = adf_to_text(adf)
        assert result == ""

    def test_nested_bullet_list(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Parent"}]},
                        {"type": "bulletList", "content": [{
                            "type": "listItem",
                            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Child"}]}],
                        }]},
                    ],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "Parent" in result
        assert "Child" in result

    def test_doc_without_content_key(self):
        adf = {"type": "paragraph", "content": [{"type": "text", "text": "no doc wrapper"}]}
        result = adf_to_text(adf)
        assert "no doc wrapper" in result


class TestAdfToTextWithImages:

    @pytest.mark.asyncio
    async def test_empty_input(self):
        async def fetcher(mid, alt):
            return None
        result = await adf_to_text_with_images(None, fetcher)
        assert result == ""

    @pytest.mark.asyncio
    async def test_fetches_media_and_embeds(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{
                    "type": "media",
                    "attrs": {"id": "m1", "alt": "img.png", "type": "file"},
                }],
            }],
        }

        async def fetcher(mid, alt):
            return "data:image/png;base64,AAAA"

        result = await adf_to_text_with_images(adf, fetcher)
        assert "data:image/png;base64,AAAA" in result

    @pytest.mark.asyncio
    async def test_handles_fetch_failure(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{
                    "type": "media",
                    "attrs": {"id": "m1", "alt": "img.png", "type": "file"},
                }],
            }],
        }

        async def fetcher(mid, alt):
            raise Exception("network error")

        result = await adf_to_text_with_images(adf, fetcher)
        assert "![img.png]" in result


class TestFetchApplicationRolesToGroupsMapping:

    @pytest.mark.asyncio
    async def test_returns_cached(self):
        connector = _make_connector()
        connector._app_roles_cache = {"role1": [{"groupId": "g1", "name": "devs"}]}

        result = await connector._fetch_application_roles_to_groups_mapping()
        assert result == {"role1": [{"groupId": "g1", "name": "devs"}]}

    @pytest.mark.asyncio
    async def test_fetches_and_caches(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        roles_data = [
            {
                "key": "jira-software",
                "groupDetails": [
                    {"groupId": "g1", "name": "jira-software-users"},
                    {"groupId": "g2", "name": "devs"},
                ],
            },
            {"key": "empty-role", "groupDetails": []},
            {"key": None, "groupDetails": [{"groupId": "g3", "name": "orphan"}]},
        ]
        mock_ds.get_all_application_roles = AsyncMock(return_value=_make_mock_response_fullcov(200, roles_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_application_roles_to_groups_mapping()
        assert "jira-software" in result
        assert len(result["jira-software"]) == 2
        assert "empty-role" not in result

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_all_application_roles = AsyncMock(return_value=_make_mock_response_fullcov(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_application_roles_to_groups_mapping()
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_all_application_roles = AsyncMock(side_effect=Exception("timeout"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_application_roles_to_groups_mapping()
        assert result == {}


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_no_groups(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[])
        jira_users = [_make_app_user()]

        result = await connector._sync_user_groups(jira_users)
        assert result == {}

    @pytest.mark.asyncio
    async def test_groups_with_members(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[
            {"groupId": "g1", "name": "developers"},
        ])
        connector._fetch_group_members = AsyncMock(return_value=["user@example.com"])
        user = _make_app_user(email="user@example.com")

        result = await connector._sync_user_groups([user])
        assert "g1" in result
        assert "developers" in result
        assert len(result["g1"]) == 1
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_group_without_id_skipped(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[
            {"groupId": None, "name": "bad-group"},
            {"groupId": "g1", "name": None},
        ])
        connector._fetch_group_members = AsyncMock(return_value=[])

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_group_error_continues(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=[
            {"groupId": "g1", "name": "devs"},
        ])
        connector._fetch_group_members = AsyncMock(side_effect=Exception("API error"))

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(side_effect=Exception("total failure"))

        result = await connector._sync_user_groups([])
        assert result == {}


class TestSyncProjectRoles:

    @pytest.mark.asyncio
    async def test_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None

        with pytest.raises(ValueError, match="not initialized"):
            await connector._sync_project_roles(["PROJ"], [], {})

    @pytest.mark.asyncio
    async def test_syncs_user_actor_roles(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "Developers": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "name": "Developers",
            "actors": [{
                "type": "atlassian-user-role-actor",
                "actorUser": {"accountId": "acc-1", "emailAddress": "user@example.com"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        user = _make_app_user()
        await connector._sync_project_roles(["PROJ"], [user], {})
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_syncs_group_actor_roles(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "Developers": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "name": "Developers",
            "actors": [{
                "type": "atlassian-group-role-actor",
                "name": "devs",
                "groupId": "g1",
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        user = _make_app_user()
        groups_map = {"g1": [user], "devs": [user]}
        await connector._sync_project_roles(["PROJ"], [user], groups_map)
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_addon_role(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "atlassian-addons-project-access": "https://jira/rest/api/3/project/PROJ/role/99999",
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_role_fetch_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response_fullcov(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_project_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(side_effect=Exception("network"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()


class TestParseIssueLinks:

    def test_empty_issue(self):
        connector = _make_connector()
        assert connector._parse_issue_links(None) == []
        assert connector._parse_issue_links({}) == []

    def test_no_issuelinks(self):
        connector = _make_connector()
        issue = {"fields": {"issuelinks": []}}
        assert connector._parse_issue_links(issue) == []

    def test_outward_link(self):
        connector = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [{
                    "type": {"outward": "blocks", "name": "Blocks"},
                    "outwardIssue": {"id": "99"},
                }],
            },
        }
        result = connector._parse_issue_links(issue)
        assert len(result) == 1
        assert result[0].external_record_id == "99"

    def test_skips_inward_link(self):
        connector = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [{
                    "type": {"inward": "is blocked by", "name": "Blocks"},
                    "inwardIssue": {"id": "99"},
                }],
            },
        }
        result = connector._parse_issue_links(issue)
        assert len(result) == 0

    def test_link_without_id(self):
        connector = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [{
                    "type": {"outward": "blocks"},
                    "outwardIssue": {},
                }],
            },
        }
        result = connector._parse_issue_links(issue)
        assert len(result) == 0

    def test_issuelinks_not_list(self):
        connector = _make_connector()
        issue = {"fields": {"issuelinks": "not-a-list"}}
        result = connector._parse_issue_links(issue)
        assert result == []

    def test_non_dict_link_item(self):
        connector = _make_connector()
        issue = {"fields": {"issuelinks": ["not-a-dict"]}}
        result = connector._parse_issue_links(issue)
        assert result == []

    def test_fields_not_dict(self):
        connector = _make_connector()
        issue = {"fields": "not-a-dict"}
        result = connector._parse_issue_links(issue)
        assert result == []


class TestExtractIssueData:

    def test_basic_extraction(self):
        connector = _make_connector()
        issue = {
            "id": "123",
            "key": "PROJ-1",
            "fields": {
                "summary": "Test Issue",
                "description": None,
                "issuetype": {"name": "Task", "hierarchyLevel": 0},
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "creator": {"accountId": "acc-1", "displayName": "Creator"},
                "reporter": {"accountId": "acc-2", "displayName": "Reporter"},
                "assignee": {"accountId": "acc-3", "displayName": "Assignee"},
                "created": "2024-01-15T10:30:45.000+0000",
                "updated": "2024-01-16T10:30:45.000+0000",
                "parent": None,
            },
        }
        user_map = {
            "acc-1": _make_app_user(email="creator@test.com", account_id="acc-1"),
            "acc-2": _make_app_user(email="reporter@test.com", account_id="acc-2"),
        }

        result = connector._extract_issue_data(issue, user_map)
        assert result["issue_id"] == "123"
        assert result["issue_key"] == "PROJ-1"
        assert "[PROJ-1]" in result["issue_name"]
        assert result["creator_email"] == "creator@test.com"
        assert result["reporter_email"] == "reporter@test.com"
        assert result["assignee_email"] is None  # not in user_map

    def test_epic_detection(self):
        connector = _make_connector()
        issue = {
            "id": "1",
            "key": "E-1",
            "fields": {
                "summary": "Epic",
                "description": None,
                "issuetype": {"name": "Epic", "hierarchyLevel": 1},
                "status": None,
                "priority": None,
                "creator": None,
                "reporter": None,
                "assignee": None,
                "created": None,
                "updated": None,
                "parent": None,
            },
        }
        result = connector._extract_issue_data(issue, {})
        assert result["is_epic"] is True
        assert result["is_subtask"] is False

    def test_subtask_detection(self):
        connector = _make_connector()
        issue = {
            "id": "2",
            "key": "S-1",
            "fields": {
                "summary": "Sub",
                "description": None,
                "issuetype": {"name": "Sub-task", "hierarchyLevel": -1},
                "status": None,
                "priority": None,
                "creator": None,
                "reporter": None,
                "assignee": None,
                "created": None,
                "updated": None,
                "parent": {"id": "1", "key": "P-1"},
            },
        }
        result = connector._extract_issue_data(issue, {})
        assert result["is_subtask"] is True
        assert result["parent_external_id"] == "1"

    def test_description_with_adf(self):
        connector = _make_connector()
        issue = {
            "id": "3",
            "key": "D-1",
            "fields": {
                "summary": "Desc",
                "description": {
                    "type": "doc",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}],
                },
                "issuetype": {"name": "Task", "hierarchyLevel": 0},
                "status": None,
                "priority": None,
                "creator": None,
                "reporter": None,
                "assignee": None,
                "created": None,
                "updated": None,
                "parent": None,
            },
        }
        result = connector._extract_issue_data(issue, {})
        assert "Hello" in result["description"]


class TestParseJiraTimestamp:

    def test_none_returns_zero(self):
        connector = _make_connector()
        assert connector._parse_jira_timestamp(None) == 0

    def test_empty_returns_zero(self):
        connector = _make_connector()
        assert connector._parse_jira_timestamp("") == 0

    def test_standard_format(self):
        connector = _make_connector()
        result = connector._parse_jira_timestamp("2024-01-15T10:30:45.000+0000")
        assert result > 0

    def test_z_suffix(self):
        connector = _make_connector()
        result = connector._parse_jira_timestamp("2024-01-15T10:30:45.000Z")
        assert result > 0

    def test_colon_timezone(self):
        connector = _make_connector()
        result = connector._parse_jira_timestamp("2024-01-15T10:30:45.000+00:00")
        assert result > 0

    def test_without_milliseconds(self):
        connector = _make_connector()
        result = connector._parse_jira_timestamp("2024-01-15T10:30:45+0000")
        assert result > 0

    def test_invalid_returns_zero(self):
        connector = _make_connector()
        result = connector._parse_jira_timestamp("not-a-timestamp")
        assert result == 0


class TestSafeJsonParse:

    def test_success(self):
        connector = _make_connector()
        resp = MagicMock()
        resp.json.return_value = {"key": "value"}
        assert connector._safe_json_parse(resp) == {"key": "value"}

    def test_failure(self):
        connector = _make_connector()
        resp = MagicMock()
        resp.json.side_effect = ValueError("bad json")
        assert connector._safe_json_parse(resp, "test") is None


class TestCreateAttachmentFileRecord:

    def test_creates_record(self):
        connector = _make_connector()
        record = connector._create_attachment_file_record(
            attachment_id="100",
            filename="report.pdf",
            mime_type="application/pdf",
            file_size=1024,
            created_at=1700000000000,
            parent_issue_id="issue-1",
            parent_node_id="node-1",
            project_id="proj-1",
            weburl="https://jira/browse/PROJ-1",
        )

        assert record.record_name == "report.pdf"
        assert record.external_record_id == "attachment_100"
        assert record.parent_external_record_id == "issue-1"
        assert record.extension == "pdf"
        assert record.is_file is True
        assert record.is_dependent_node is True
        assert record.parent_node_id == "node-1"

    def test_no_extension(self):
        connector = _make_connector()
        record = connector._create_attachment_file_record(
            attachment_id="101",
            filename="Makefile",
            mime_type="text/plain",
            file_size=100,
            created_at=0,
            parent_issue_id="issue-1",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
        )
        assert record.extension is None

    def test_indexing_filter_off(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        record = connector._create_attachment_file_record(
            attachment_id="102",
            filename="file.txt",
            mime_type="text/plain",
            file_size=50,
            created_at=0,
            parent_issue_id="issue-1",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
        )
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    def test_skip_filter_check(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        record = connector._create_attachment_file_record(
            attachment_id="103",
            filename="file.txt",
            mime_type="text/plain",
            file_size=50,
            created_at=0,
            parent_issue_id="issue-1",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
            skip_filter_check=True,
        )
        assert record.indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


class TestExtractAttachmentFilenamesFromWiki:

    def test_extracts_filenames(self):
        connector = _make_connector()
        text = "See !screenshot.png|thumbnail! and !report.pdf|border=1!"
        result = connector._extract_attachment_filenames_from_wiki(text)
        assert "screenshot.png" in result
        assert "report.pdf" in result

    def test_empty_text(self):
        connector = _make_connector()
        assert connector._extract_attachment_filenames_from_wiki("") == set()


class TestOrganizeIssueCommentsToThreads:

    def test_empty(self):
        connector = _make_connector()
        assert connector._organize_issue_comments_to_threads([]) == []

    def test_single_thread(self):
        connector = _make_connector()
        comments = [
            {"id": "c1", "created": "2024-01-01T00:00:00.000Z"},
            {"id": "c2", "parent": {"id": "c1"}, "created": "2024-01-01T01:00:00.000Z"},
        ]
        threads = connector._organize_issue_comments_to_threads(comments)
        assert len(threads) == 1
        assert len(threads[0]) == 2

    def test_multiple_threads(self):
        connector = _make_connector()
        comments = [
            {"id": "c1", "created": "2024-01-01T00:00:00.000Z"},
            {"id": "c2", "created": "2024-01-02T00:00:00.000Z"},
        ]
        threads = connector._organize_issue_comments_to_threads(comments)
        assert len(threads) == 2

    def test_comment_without_id_skipped(self):
        connector = _make_connector()
        comments = [{"created": "2024-01-01T00:00:00.000Z"}]
        threads = connector._organize_issue_comments_to_threads(comments)
        assert len(threads) == 0


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_stream_ticket(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.site_url = "https://company.atlassian.net"

        record = _make_ticket_record()
        mock_blocks = b'{"blocks": [], "block_groups": []}'
        connector._process_issue_blockgroups_for_streaming = AsyncMock(return_value=mock_blocks)

        result = await connector.stream_record(record)
        assert result is not None
        assert result.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_file(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = _make_file_record()
        mock_ds = MagicMock()
        mock_ds.get_attachment_content = AsyncMock(
            return_value=_make_mock_response_fullcov(200, b"file-content")
        )
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock()
        record.record_type = RecordType.MESSAGE
        record.external_record_id = "ext-1"

        with pytest.raises(ValueError, match="Unsupported"):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_initializes_if_needed(self):
        connector = _make_connector()
        connector.data_source = None
        connector.init = AsyncMock(return_value=True)
        connector.data_source = MagicMock()

        record = _make_ticket_record()
        connector._process_issue_blockgroups_for_streaming = AsyncMock(return_value=b'{}')

        await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_raises_on_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = _make_ticket_record()
        connector._process_issue_blockgroups_for_streaming = AsyncMock(
            side_effect=Exception("fetch failed")
        )

        with pytest.raises(Exception, match="fetch failed"):
            await connector.stream_record(record)


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_list(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_updated_records_saved(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = _make_ticket_record()
        updated = (_make_ticket_record(version=2), [])
        connector._check_and_fetch_updated_record = AsyncMock(return_value=updated)

        await connector.reindex_records([record])
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_updated_reindexed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = _make_ticket_record()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)

        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_base_record_class(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        from app.models.entities import Record
        record = Record(
            record_name="test",
            record_type=RecordType.TICKET,
            external_record_id="ext-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.JIRA,
            connector_id="conn-jira-1",
            scope="personal",
            created_by="test-user-id",
        )
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)

        await connector.reindex_records([record])
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None

        record = _make_ticket_record()
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([record])

    @pytest.mark.asyncio
    async def test_check_error_continues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = _make_ticket_record()
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("api fail"))

        await connector.reindex_records([record])

    @pytest.mark.asyncio
    async def test_reindex_not_implemented(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = _make_ticket_record()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        connector.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=NotImplementedError("to_kafka_record not implemented")
        )

        await connector.reindex_records([record])


class TestCheckAndFetchUpdatedRecord:

    @pytest.mark.asyncio
    async def test_dispatches_ticket(self):
        connector = _make_connector()
        connector._check_and_fetch_updated_issue = AsyncMock(return_value=None)
        record = _make_ticket_record()

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None
        connector._check_and_fetch_updated_issue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_file(self):
        connector = _make_connector()
        connector._check_and_fetch_updated_attachment = AsyncMock(return_value=None)
        record = _make_file_record()

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None
        connector._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        connector = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.MESSAGE
        record.id = "r1"
        record.external_record_id = "ext-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        connector = _make_connector()
        connector._check_and_fetch_updated_issue = AsyncMock(side_effect=Exception("err"))
        record = _make_ticket_record()

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None


class TestCheckAndFetchUpdatedIssue:

    @pytest.mark.asyncio
    async def test_issue_not_changed(self):
        connector = _make_connector()
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "fields": {"updated": "2023-11-14T22:13:20.000+0000"},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_ticket_record(source_updated_at=1700000000000)
            result = await connector._check_and_fetch_updated_issue(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_issue_changed_returns_updated(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.site_url = "https://company.atlassian.net"

        issue_response = {
            "id": "12345",
            "key": "PROJ-1",
            "fields": {
                "summary": "Updated Issue",
                "description": None,
                "updated": "2024-06-01T00:00:00.000+0000",
                "created": "2024-01-01T00:00:00.000+0000",
                "issuetype": {"name": "Task", "hierarchyLevel": 0},
                "status": {"name": "Done"},
                "priority": {"name": "Low"},
                "creator": {"accountId": "acc-1", "displayName": "Creator", "emailAddress": "c@test.com"},
                "reporter": {"accountId": "acc-2", "displayName": "Reporter"},
                "assignee": {"accountId": "acc-3", "displayName": "Assignee"},
                "project": {"id": "proj-1"},
                "parent": None,
            },
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(200, issue_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record = _make_ticket_record(source_updated_at=1700000000000)
        result = await connector._check_and_fetch_updated_issue(record)
        assert result is not None
        updated_record, permissions = result
        assert updated_record.record_name == "[PROJ-1] Updated Issue"

    @pytest.mark.asyncio
    async def test_issue_gone(self):
        connector = _make_connector()
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(410))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_ticket_record()
            result = await connector._check_and_fetch_updated_issue(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_issue_fetch_error(self):
        connector = _make_connector()
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(side_effect=Exception("network"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_ticket_record()
            result = await connector._check_and_fetch_updated_issue(record)
        assert result is None


class TestCheckAndFetchUpdatedAttachment:

    @pytest.mark.asyncio
    async def test_attachment_not_changed(self):
        connector = _make_connector()
        connector.indexing_filters = None
        connector.site_url = "https://company.atlassian.net"

        issue_response = {
            "key": "PROJ-1",
            "fields": {
                "attachment": [{
                    "id": "99",
                    "filename": "screenshot.png",
                    "size": 1024,
                    "mimeType": "image/png",
                    "created": "2023-11-14T22:13:20.000+0000",
                }],
            },
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(200, issue_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="parent-node"))
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_file_record(source_updated_at=1700000000000)
            result = await connector._check_and_fetch_updated_attachment(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_attachment_changed(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.site_url = "https://company.atlassian.net"

        issue_response = {
            "key": "PROJ-1",
            "fields": {
                "attachment": [{
                    "id": "99",
                    "filename": "screenshot.png",
                    "size": 2048,
                    "mimeType": "image/png",
                    "created": "2024-06-01T00:00:00.000+0000",
                }],
            },
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(200, issue_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="parent-node"))
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        record = _make_file_record(source_updated_at=1700000000000)
        result = await connector._check_and_fetch_updated_attachment(record)
        assert result is not None
        updated_record, permissions = result
        assert updated_record.record_name == "screenshot.png"

    @pytest.mark.asyncio
    async def test_attachment_not_found_in_issue(self):
        connector = _make_connector()
        connector.indexing_filters = None
        connector.site_url = "https://company.atlassian.net"

        issue_response = {
            "key": "PROJ-1",
            "fields": {"attachment": []},
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(200, issue_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="parent-node"))
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_file_record()
            result = await connector._check_and_fetch_updated_attachment(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_parent_issue_id(self):
        connector = _make_connector()
        connector.indexing_filters = None

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_file_record(parent_external_record_id=None)
            result = await connector._check_and_fetch_updated_attachment(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_parent_issue_gone(self):
        connector = _make_connector()
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(410))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="p"))
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_file_record()
            result = await connector._check_and_fetch_updated_attachment(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_external_id_without_prefix(self):
        connector = _make_connector()
        connector.indexing_filters = None
        connector.site_url = "https://company.atlassian.net"

        issue_response = {
            "key": "PROJ-1",
            "fields": {
                "attachment": [{
                    "id": "99",
                    "filename": "file.txt",
                    "size": 10,
                    "mimeType": "text/plain",
                    "created": "2024-06-01T00:00:00.000+0000",
                }],
            },
        }

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(200, issue_response))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=MagicMock(id="p"))
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                   new_callable=AsyncMock, return_value=(None, None)):
            record = _make_file_record()
            record.external_record_id = "99"
            record.source_updated_at = 0
            result = await connector._check_and_fetch_updated_attachment(record)
        assert result is not None


class TestHandleAttachmentDeletionsFromChangelog:

    @pytest.mark.asyncio
    async def test_no_changelog(self):
        connector = _make_connector()
        tx_store = AsyncMock()
        await connector._handle_attachment_deletions_from_changelog({"key": "P-1"}, tx_store)

    @pytest.mark.asyncio
    async def test_explicit_attachment_deletion(self):
        connector = _make_connector()
        issue = {
            "id": "1",
            "key": "P-1",
            "fields": {"attachment": []},
            "changelog": {
                "histories": [{
                    "items": [{
                        "field": "Attachment",
                        "fieldId": "attachment",
                        "from": "100",
                        "to": None,
                    }],
                }],
            },
        }

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.external_record_id = "attachment_100"
        mock_record.record_name = "file.txt"

        tx_store = AsyncMock()
        tx_store.delete_records_and_relations = AsyncMock()
        connector._find_attachment_record_by_id = AsyncMock(return_value=mock_record)

        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        tx_store.delete_records_and_relations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_description_change_with_removed_attachment(self):
        connector = _make_connector()
        issue = {
            "id": "1",
            "key": "P-1",
            "fields": {"attachment": [{"id": "200", "filename": "kept.png"}]},
            "changelog": {
                "histories": [{
                    "items": [{
                        "field": "description",
                        "fieldId": "description",
                        "fromString": "!removed.png|thumbnail!",
                        "toString": "",
                    }],
                }],
            },
        }

        mock_record = MagicMock()
        mock_record.id = "rec-1"
        mock_record.external_record_id = "attachment_300"
        mock_record.record_name = "removed.png"

        tx_store = AsyncMock()
        tx_store.delete_records_and_relations = AsyncMock()
        tx_store.get_records_by_parent = AsyncMock(return_value=[mock_record])
        connector._find_attachment_record_by_id = AsyncMock(return_value=None)

        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        tx_store.delete_records_and_relations.assert_awaited()

    @pytest.mark.asyncio
    async def test_error_is_caught(self):
        connector = _make_connector()
        issue = {
            "key": "P-1",
            "changelog": {"histories": [{"items": [{"field": "Attachment", "from": "x", "to": None}]}]},
        }
        tx_store = AsyncMock()
        connector._find_attachment_record_by_id = AsyncMock(side_effect=Exception("db err"))

        await connector._handle_attachment_deletions_from_changelog(issue, tx_store)


class TestHandleIssueDeletions:

    @pytest.mark.asyncio
    async def test_no_sync_time(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)

        await connector._handle_issue_deletions(None)

    @pytest.mark.asyncio
    async def test_with_sync_time(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.issues_sync_point.update_sync_point = AsyncMock()
        connector._detect_and_handle_deletions = AsyncMock(return_value=0)

        await connector._handle_issue_deletions(1700000000000)
        connector._detect_and_handle_deletions.assert_awaited_once()
        connector.issues_sync_point.update_sync_point.assert_awaited()


class TestGetSignedUrl:

    @pytest.mark.asyncio
    async def test_returns_empty(self):
        connector = _make_connector()
        record = _make_ticket_record()
        result = await connector.get_signed_url(record)
        assert result == ""


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_current_user = AsyncMock(return_value=_make_mock_response_fullcov(200))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_current_user = AsyncMock(return_value=_make_mock_response_fullcov(401))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("err"))

        result = await connector.test_connection_and_access()
        assert result is False


class TestCleanup:

    @pytest.mark.asyncio
    async def test_cleanup(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.close = AsyncMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client
        connector.data_source = MagicMock()
        connector._issue_attachments_cache = {"k": "v"}

        await connector.cleanup()
        assert connector.external_client is None
        assert connector.data_source is None
        assert connector._issue_attachments_cache == {}

    @pytest.mark.asyncio
    async def test_cleanup_error_handled(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.side_effect = Exception("already closed")
        connector.external_client = mock_client
        connector._issue_attachments_cache = {}

        await connector.cleanup()
        assert connector.external_client is None


class TestHandleWebhookNotification:

    @pytest.mark.asyncio
    async def test_noop(self):
        connector = _make_connector()
        await connector.handle_webhook_notification({})


class TestRunIncrementalSync:

    @pytest.mark.asyncio
    async def test_calls_run_sync(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


class TestSyncProjectLeadRoles:

    @pytest.mark.asyncio
    async def test_syncs_lead(self):
        connector = _make_connector()
        user = _make_app_user(account_id="lead-acc")
        projects = [{"key": "PROJ", "lead": {"accountId": "lead-acc", "displayName": "Lead"}}]

        await connector._sync_project_lead_roles(projects, [user])
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()
        args = connector.data_entities_processor.on_new_app_roles.call_args[0][0]
        role, members = args[0]
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_no_lead(self):
        connector = _make_connector()
        projects = [{"key": "PROJ", "lead": None}]

        await connector._sync_project_lead_roles(projects, [])
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()
        args = connector.data_entities_processor.on_new_app_roles.call_args[0][0]
        role, members = args[0]
        assert len(members) == 0

    @pytest.mark.asyncio
    async def test_lead_not_in_users(self):
        connector = _make_connector()
        projects = [{"key": "PROJ", "lead": {"accountId": "unknown", "displayName": "Unknown"}}]

        await connector._sync_project_lead_roles(projects, [])
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_continues(self):
        connector = _make_connector()
        projects = [
            {"key": None},
            {"key": "PROJ", "lead": None},
        ]

        await connector._sync_project_lead_roles(projects, [])
        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()


class TestGetIssueAttachmentsCached:

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        connector = _make_connector()
        connector._issue_attachments_cache = {"issue-1": [{"id": "a1"}]}

        result = await connector._get_issue_attachments_cached("issue-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_cache_miss_fetches(self):
        connector = _make_connector()
        connector._issue_attachments_cache = {}

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "fields": {"attachment": [{"id": "a1"}]},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_issue_attachments_cached("issue-1")
        assert len(result) == 1
        assert "issue-1" in connector._issue_attachments_cache

    @pytest.mark.asyncio
    async def test_cache_miss_api_failure(self):
        connector = _make_connector()
        connector._issue_attachments_cache = {}

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response_fullcov(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._get_issue_attachments_cached("issue-1")
        assert result == []


class TestCreateMediaFetcher:

    @pytest.mark.asyncio
    async def test_creates_bound_fetcher(self):
        connector = _make_connector()
        connector._fetch_media_as_base64 = AsyncMock(return_value="data:image/png;base64,AAAA")

        fetcher = connector._create_media_fetcher("issue-1")
        result = await fetcher("m1", "alt")
        assert result == "data:image/png;base64,AAAA"
        connector._fetch_media_as_base64.assert_awaited_once_with("issue-1", "m1", "alt")


class TestFetchMediaAsBase64:

    @pytest.mark.asyncio
    async def test_by_media_id(self):
        connector = _make_connector()
        connector._get_issue_attachments_cached = AsyncMock(return_value=[
            {"id": "42", "filename": "img.png", "mimeType": "image/png"},
        ])

        mock_ds = MagicMock()
        mock_ds.get_attachment_content = AsyncMock(return_value=_make_mock_response_fullcov(200))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_media_as_base64("issue-1", "42", "")
        assert result.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_fallback_by_filename(self):
        connector = _make_connector()
        connector._get_issue_attachments_cached = AsyncMock(return_value=[
            {"id": "42", "filename": "img.png", "mimeType": "image/png"},
        ])

        mock_ds = MagicMock()
        mock_ds.get_attachment_content = AsyncMock(return_value=_make_mock_response_fullcov(200))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_media_as_base64("issue-1", "no-match", "img.png")
        assert result is not None

    @pytest.mark.asyncio
    async def test_fallback_partial_filename(self):
        connector = _make_connector()
        connector._get_issue_attachments_cached = AsyncMock(return_value=[
            {"id": "42", "filename": "screenshot-2024.png", "mimeType": "image/png"},
        ])

        mock_ds = MagicMock()
        mock_ds.get_attachment_content = AsyncMock(return_value=_make_mock_response_fullcov(200))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_media_as_base64("issue-1", "no-match", "screenshot-2024")
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_matching_attachment(self):
        connector = _make_connector()
        connector._get_issue_attachments_cached = AsyncMock(return_value=[
            {"id": "42", "filename": "other.png", "mimeType": "image/png"},
        ])

        result = await connector._fetch_media_as_base64("issue-1", "no-match", "no-match-alt")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_attachments(self):
        connector = _make_connector()
        connector._get_issue_attachments_cached = AsyncMock(return_value=[])

        result = await connector._fetch_media_as_base64("issue-1", "m1", "alt")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_content_failure(self):
        connector = _make_connector()
        connector._get_issue_attachments_cached = AsyncMock(return_value=[
            {"id": "42", "filename": "img.png", "mimeType": "image/png"},
        ])

        mock_ds = MagicMock()
        mock_ds.get_attachment_content = AsyncMock(return_value=_make_mock_response_fullcov(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_media_as_base64("issue-1", "42", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector._get_issue_attachments_cached = AsyncMock(side_effect=Exception("err"))

        result = await connector._fetch_media_as_base64("issue-1", "m1", "alt")
        assert result is None


class TestProcessNewRecords:

    @pytest.mark.asyncio
    async def test_sorts_and_processes(self):
        connector = _make_connector()

        r1 = (_make_ticket_record(external_id="1", version=0), [])
        r2 = (_make_ticket_record(external_id="2", version=1, parent_external_record_id="1"), [])

        stats = {"new_count": 0, "updated_count": 0}
        await connector._process_new_records([r2, r1], "PROJ", stats)

        connector.data_entities_processor.on_new_records.assert_awaited()
        assert stats["new_count"] == 1
        assert stats["updated_count"] == 1


class TestFetchDeletedIssuesFromAudit:

    @pytest.mark.asyncio
    async def test_fetches_deletions(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "records": [{
                "objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-99"},
                "created": "2024-01-01",
            }],
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert "PROJ-99" in result

    @pytest.mark.asyncio
    async def test_empty_audit(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "records": [],
            "total": 0,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert result == []

    @pytest.mark.asyncio
    async def test_audit_api_failure(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response_fullcov(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert result == []


class TestDetectAndHandleDeletions:

    @pytest.mark.asyncio
    async def test_no_deletions(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=[])

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert count == 0

    @pytest.mark.asyncio
    async def test_with_deletions(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=["PROJ-1"])
        connector._handle_deleted_issue = AsyncMock()

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert count == 1

    @pytest.mark.asyncio
    async def test_deletion_error_continues(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=["PROJ-1", "PROJ-2"])
        connector._handle_deleted_issue = AsyncMock(
            side_effect=[Exception("err"), None]
        )

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert count == 1

    @pytest.mark.asyncio
    async def test_overall_exception(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(side_effect=Exception("total fail"))

        count = await connector._detect_and_handle_deletions(1700000000000)
        assert count == 0


class TestDeleteIssueChildrenRecursive:

    @pytest.mark.asyncio
    async def test_deletes_ticket_children_recursively(self):
        connector = _make_connector()
        tx_store = AsyncMock()

        child = MagicMock()
        child.id = "child-1"
        child.external_record_id = "ext-child-1"

        tx_store.get_records_by_parent = AsyncMock(side_effect=[
            [child],
            [],
            [],
        ])
        tx_store.delete_records_and_relations = AsyncMock()

        count = await connector._delete_issue_children("parent-1", RecordType.TICKET, tx_store)
        assert count == 1


class TestGetProjectSyncCheckpoint:

    @pytest.mark.asyncio
    async def test_reads_checkpoint(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 100, "last_issue_updated": 200}
        )
        result = await connector._get_project_sync_checkpoint("PROJ")
        assert result["last_issue_updated"] == 200


class TestUpdateProjectSyncCheckpoint:

    @pytest.mark.asyncio
    async def test_updates_preserving_existing(self):
        connector = _make_connector()
        connector._get_project_sync_checkpoint = AsyncMock(
            return_value={"last_sync_time": 100, "last_issue_updated": 200}
        )
        connector.issues_sync_point.update_sync_point = AsyncMock()

        await connector._update_project_sync_checkpoint("PROJ", last_sync_time=300)
        args = connector.issues_sync_point.update_sync_point.call_args
        assert args[0][1]["last_sync_time"] == 300
        assert args[0][1]["last_issue_updated"] == 200


class TestFetchProjectPermissionScheme:

    @pytest.mark.asyncio
    async def test_group_permission(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "group", "value": "g1"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_anyone_permission(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "anyone"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_user_permission(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {
                    "type": "user",
                    "parameter": "acc-1",
                    "user": {"emailAddress": "user@test.com"},
                },
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_project_role_permission(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {
                    "type": "projectRole",
                    "parameter": "10001",
                    "projectRole": {"name": "Developers", "id": 10001},
                },
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ROLE

    @pytest.mark.asyncio
    async def test_project_lead_permission(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "projectLead"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert len(permissions) == 1
        assert permissions[0].external_id == "PROJ_projectLead"

    @pytest.mark.asyncio
    async def test_application_role_with_mapping(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "applicationRole", "parameter": "jira-software"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mapping = {"jira-software": [{"groupId": "g1", "name": "devs"}]}
        permissions = await connector._fetch_project_permission_scheme("PROJ", mapping)
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_application_role_without_mapping(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "applicationRole", "parameter": "unknown-role"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ", {})
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_scheme_fetch_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_grants_fetch_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("err"))

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_skips_sd_customer_and_custom_fields(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "sd.customer.portal.only"},
                },
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "groupCustomField", "parameter": "cf_10001"},
                },
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "userCustomField", "parameter": "cf_10002"},
                },
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_skips_addon_project_role(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response_fullcov(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response_fullcov(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {
                    "type": "projectRole",
                    "parameter": "10001",
                    "projectRole": {"name": "atlassian-addons-project-access", "id": 10001},
                },
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []


class TestFindAttachmentRecordById:

    @pytest.mark.asyncio
    async def test_finds_record(self):
        connector = _make_connector()
        tx_store = AsyncMock()
        mock_record = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=mock_record)

        result = await connector._find_attachment_record_by_id("100", tx_store)
        assert result == mock_record
        tx_store.get_record_by_external_id.assert_awaited_once_with(
            connector_id="conn-jira-1",
            external_id="attachment_100",
        )


class TestDeleteAttachmentRecord:

    @pytest.mark.asyncio
    async def test_deletes_and_logs(self):
        connector = _make_connector()
        tx_store = AsyncMock()
        tx_store.delete_records_and_relations = AsyncMock()

        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "attachment_100"
        record.record_name = "file.txt"

        await connector._delete_attachment_record(record, "PROJ-1", tx_store)
        tx_store.delete_records_and_relations.assert_awaited_once_with(record_key="r1", hard_delete=True)


class TestGetFreshDatasourceOAuth:

    @pytest.mark.asyncio
    async def test_oauth_updates_token(self):
        connector = _make_connector()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_internal.set_token = MagicMock()
        mock_client = MagicMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "new-token"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
            await connector._get_fresh_datasource()
        mock_internal.set_token.assert_called_once_with("new-token")

    @pytest.mark.asyncio
    async def test_oauth_same_token_no_update(self):
        connector = _make_connector()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "same-token"
        mock_internal.set_token = MagicMock()
        mock_client = MagicMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "same-token"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
            await connector._get_fresh_datasource()
        mock_internal.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_oauth_no_token_raises(self):
        connector = _make_connector()
        connector.external_client = MagicMock()

        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {},
        })

        with pytest.raises(Exception, match="No OAuth access token"):
            await connector._get_fresh_datasource()
