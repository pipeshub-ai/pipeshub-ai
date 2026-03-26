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
    return JiraConnector(logger, dep, dsp, cs, "conn-jira-1")


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
