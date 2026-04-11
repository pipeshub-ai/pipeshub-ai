"""Comprehensive workflow tests for Jira Cloud connector.

These tests exercise full sync workflows covering initialization, user/group sync,
project sync, issue sync, deletion handling, permission schemes, ADF parsing,
and error handling. Each test covers many code paths to maximize statement coverage.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
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
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.jira.workflow")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-jira-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_new_app_roles = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="active@example.com"),
    ])

    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_issue_key = AsyncMock(return_value=None)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()

    class FakeTxContext:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTxContext())

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service, mock_tx_store


def _make_connector():
    logger, dep, dsp, cs, tx = _make_mock_deps()
    connector = JiraConnector(logger, dep, dsp, cs, "conn-jira-1", "team", "test-user-id")
    return connector, dep, dsp, cs, tx


def _make_mock_response(status=200, data=None, text_val=""):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value=text_val)
    return resp


# ===========================================================================
# ADF Parsing Tests — covers extract_media_from_adf and adf_to_text
# ===========================================================================


class TestADFParsing:

    def test_extract_media_empty(self):
        assert extract_media_from_adf(None) == []
        assert extract_media_from_adf({}) == []
        assert extract_media_from_adf("string") == []

    def test_extract_media_with_nodes(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{
                    "type": "media",
                    "attrs": {
                        "id": "media-1",
                        "alt": "screenshot.png",
                        "type": "file",
                        "width": 800,
                        "height": 600,
                        "collection": "col-1",
                        "__fileName": "screenshot.png",
                    }
                }]
            }]
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 1
        assert result[0]["id"] == "media-1"
        assert result[0]["filename"] == "screenshot.png"

    def test_extract_media_no_id_skipped(self):
        adf = {
            "type": "doc",
            "content": [{"type": "media", "attrs": {"id": "", "alt": "no-id"}}]
        }
        result = extract_media_from_adf(adf)
        assert len(result) == 0

    def test_adf_to_text_empty(self):
        assert adf_to_text(None) == ""
        assert adf_to_text({}) == ""

    def test_adf_to_text_paragraph(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello World"}]
            }]
        }
        result = adf_to_text(adf)
        assert "Hello World" in result

    def test_adf_to_text_heading(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Title"}]
            }]
        }
        result = adf_to_text(adf)
        assert "## Title" in result

    def test_adf_to_text_marks(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "bold",
                    "marks": [{"type": "strong"}]
                }, {
                    "type": "text",
                    "text": "italic",
                    "marks": [{"type": "em"}]
                }, {
                    "type": "text",
                    "text": "code",
                    "marks": [{"type": "code"}]
                }, {
                    "type": "text",
                    "text": "strike",
                    "marks": [{"type": "strike"}]
                }, {
                    "type": "text",
                    "text": "link",
                    "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}]
                }, {
                    "type": "text",
                    "text": "underline",
                    "marks": [{"type": "underline"}]
                }]
            }]
        }
        result = adf_to_text(adf)
        assert "**bold**" in result
        assert "*italic*" in result
        assert "`code`" in result
        assert "~~strike~~" in result
        assert "[link](https://example.com)" in result

    def test_adf_to_text_bullet_list(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Item 1"}]}
                    ]},
                    {"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Item 2"}]}
                    ]},
                ]
            }]
        }
        result = adf_to_text(adf)
        assert "- Item 1" in result
        assert "- Item 2" in result

    def test_adf_to_text_ordered_list(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "orderedList",
                "content": [
                    {"type": "listItem", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "First"}]}
                    ]},
                ]
            }]
        }
        result = adf_to_text(adf)
        assert "1. First" in result

    def test_adf_to_text_code_block(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "print('hello')"}]
            }]
        }
        result = adf_to_text(adf)
        assert "```python" in result
        assert "print('hello')" in result

    def test_adf_to_text_blockquote(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "blockquote",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Quote"}]}]
            }]
        }
        result = adf_to_text(adf)
        assert "> Quote" in result

    def test_adf_to_text_table(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "table",
                "content": [{
                    "type": "tableRow",
                    "content": [
                        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Name"}]}]},
                        {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Value"}]}]},
                    ]
                }, {
                    "type": "tableRow",
                    "content": [
                        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "A"}]}]},
                        {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "1"}]}]},
                    ]
                }]
            }]
        }
        result = adf_to_text(adf)
        assert "| Name | Value |" in result
        assert "| --- | --- |" in result
        assert "| A | 1 |" in result

    def test_adf_to_text_mention(self):
        adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "mention", "attrs": {"text": "John"}}
        ]}]}
        assert "@John" in adf_to_text(adf)

    def test_adf_to_text_emoji(self):
        adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "emoji", "attrs": {"shortName": "thumbsup"}}
        ]}]}
        assert ":thumbsup:" in adf_to_text(adf)

    def test_adf_to_text_rule(self):
        adf = {"type": "doc", "content": [{"type": "rule"}]}
        assert "---" in adf_to_text(adf)

    def test_adf_to_text_hard_break(self):
        adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "before"},
            {"type": "hardBreak"},
            {"type": "text", "text": "after"},
        ]}]}
        result = adf_to_text(adf)
        assert "before" in result
        assert "after" in result

    def test_adf_to_text_inline_card(self):
        adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "inlineCard", "attrs": {"url": "https://jira.example.com/browse/PROJ-1"}}
        ]}]}
        assert "https://jira.example.com" in adf_to_text(adf)

    def test_adf_to_text_task_list(self):
        adf = {"type": "doc", "content": [{
            "type": "taskList",
            "content": [{
                "type": "taskItem",
                "attrs": {"state": "DONE"},
                "content": [{"type": "text", "text": "Done task"}]
            }, {
                "type": "taskItem",
                "attrs": {"state": "TODO"},
                "content": [{"type": "text", "text": "Todo task"}]
            }]
        }]}
        result = adf_to_text(adf)
        assert "[x]" in result
        assert "[ ]" in result

    def test_adf_to_text_decision_list(self):
        adf = {"type": "doc", "content": [{
            "type": "decisionList",
            "content": [{
                "type": "decisionItem",
                "attrs": {"state": "DECIDED"},
                "content": [{"type": "text", "text": "Decision made"}]
            }]
        }]}
        assert "Decision made" in adf_to_text(adf)

    def test_adf_to_text_status(self):
        adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "status", "attrs": {"text": "In Progress"}}
        ]}]}
        assert "[In Progress]" in adf_to_text(adf)

    def test_adf_to_text_date(self):
        adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "date", "attrs": {"timestamp": "1704067200000"}}
        ]}]}
        result = adf_to_text(adf)
        assert "2024-01-01" in result

    def test_adf_to_text_expand(self):
        adf = {"type": "doc", "content": [{
            "type": "expand",
            "attrs": {"title": "More Details"},
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Details here"}]}]
        }]}
        result = adf_to_text(adf)
        assert "More Details" in result
        assert "Details here" in result

    def test_adf_to_text_panel(self):
        adf = {"type": "doc", "content": [{
            "type": "panel",
            "attrs": {"panelType": "info"},
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Notice"}]}]
        }]}
        result = adf_to_text(adf)
        assert "INFO" in result
        assert "Notice" in result

    def test_adf_to_text_media_with_cache(self):
        adf = {"type": "doc", "content": [
            {"type": "media", "attrs": {"id": "m1", "alt": "image.png"}}
        ]}
        cache = {"m1": "data:image/png;base64,abc"}
        result = adf_to_text(adf, media_cache=cache)
        assert "data:image/png;base64,abc" in result

    def test_adf_to_text_media_without_cache(self):
        adf = {"type": "doc", "content": [
            {"type": "media", "attrs": {"id": "m1", "alt": "image.png"}}
        ]}
        result = adf_to_text(adf)
        assert "![image.png]" in result

    def test_adf_to_text_layout_section(self):
        adf = {"type": "doc", "content": [{
            "type": "layoutSection",
            "content": [{
                "type": "layoutColumn",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Column 1"}]}]
            }, {
                "type": "layoutColumn",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Column 2"}]}]
            }]
        }]}
        result = adf_to_text(adf)
        assert "Column 1" in result
        assert "Column 2" in result

    def test_adf_to_text_placeholder(self):
        adf = {"type": "doc", "content": [
            {"type": "placeholder", "attrs": {"text": "Enter text..."}}
        ]}
        assert "Enter text..." in adf_to_text(adf)

    def test_adf_to_text_nested_list(self):
        adf = {"type": "doc", "content": [{
            "type": "bulletList",
            "content": [{
                "type": "listItem",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Parent"}]},
                    {"type": "bulletList", "content": [{
                        "type": "listItem",
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Child"}]}]
                    }]}
                ]
            }]
        }]}
        result = adf_to_text(adf)
        assert "Parent" in result
        assert "Child" in result

    def test_adf_to_text_single_node_no_content_key(self):
        # adf_content without "content" at root level
        adf = {"type": "paragraph", "content": [{"type": "text", "text": "Direct"}]}
        result = adf_to_text(adf)
        assert "Direct" in result


class TestADFToTextWithImages:

    @pytest.mark.asyncio
    async def test_with_media_fetcher(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "mediaSingle",
                "content": [{
                    "type": "media",
                    "attrs": {"id": "m1", "alt": "screen.png", "type": "file"}
                }]
            }]
        }

        async def fetcher(media_id, alt):
            return "data:image/png;base64,encoded"

        result = await adf_to_text_with_images(adf, fetcher)
        assert "data:image/png;base64,encoded" in result

    @pytest.mark.asyncio
    async def test_with_media_fetcher_failure(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "media",
                "attrs": {"id": "m1", "alt": "fail.png", "type": "file"}
            }]
        }

        async def fetcher(media_id, alt):
            raise Exception("fetch failed")

        result = await adf_to_text_with_images(adf, fetcher)
        assert "fail.png" in result

    @pytest.mark.asyncio
    async def test_empty_adf(self):
        async def fetcher(media_id, alt):
            return None
        assert await adf_to_text_with_images(None, fetcher) == ""
        assert await adf_to_text_with_images({}, fetcher) == ""


# ===========================================================================
# Jira Connector Init
# ===========================================================================


class TestJiraInit:

    @pytest.mark.asyncio
    async def test_init_oauth(self):
        connector, dep, dsp, cs, tx = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)
            mock_client_cls.get_accessible_resources = AsyncMock(return_value=[
                MagicMock(id="cloud-1", url="https://test.atlassian.net")
            ])

            with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
                cs.get_config = AsyncMock(return_value={
                    "auth": {"authType": "OAUTH"},
                    "credentials": {"access_token": "token"},
                })
                connector._get_access_token = AsyncMock(return_value="token")

                result = await connector.init()
                assert result is True
                assert connector.cloud_id == "cloud-1"

    @pytest.mark.asyncio
    async def test_init_api_token(self):
        connector, dep, dsp, cs, tx = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)

            with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
                cs.get_config = AsyncMock(return_value={
                    "auth": {"authType": "API_TOKEN", "baseUrl": "https://test.atlassian.net"},
                })

                result = await connector.init()
                assert result is True
                assert connector.site_url == "https://test.atlassian.net"
                assert connector.cloud_id is None

    @pytest.mark.asyncio
    async def test_init_failure(self):
        connector, dep, dsp, cs, tx = _make_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as mock_client_cls:
            mock_client_cls.build_from_services = AsyncMock(side_effect=Exception("fail"))
            result = await connector.init()
            assert result is False


# ===========================================================================
# Get Fresh Datasource
# ===========================================================================


class TestJiraFreshDatasource:

    @pytest.mark.asyncio
    async def test_api_token_returns_existing(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.external_client = MagicMock()

        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
            ds = await connector._get_fresh_datasource()
            assert ds is not None

    @pytest.mark.asyncio
    async def test_oauth_updates_token(self):
        connector, dep, dsp, cs, tx = _make_connector()
        mock_client = MagicMock()
        internal = MagicMock()
        internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = internal
        connector.external_client = mock_client

        cs.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "new-token"},
        })

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource"):
            await connector._get_fresh_datasource()
            internal.set_token.assert_called_with("new-token")

    @pytest.mark.asyncio
    async def test_no_client_raises(self):
        connector, *_ = _make_connector()
        connector.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()


# ===========================================================================
# User Fetching
# ===========================================================================


class TestJiraFetchUsers:

    @pytest.mark.asyncio
    async def test_fetch_users_pagination(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        page1 = [
            {"accountId": "a1", "emailAddress": "alice@test.com", "displayName": "Alice", "active": True},
            {"accountId": "a2", "emailAddress": None, "displayName": "NoEmail", "active": True},  # skipped
        ]
        page2 = [
            {"accountId": "a3", "emailAddress": "inactive@test.com", "displayName": "Inactive", "active": False},  # skipped
        ]

        mock_ds = MagicMock()
        mock_ds.get_all_users = AsyncMock(side_effect=[
            _make_mock_response(data=page1),
            _make_mock_response(data=page2),
        ])

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            connector._safe_json_parse = MagicMock(side_effect=[page1, page2])
            result = await connector._fetch_users()
            assert len(result) == 1  # only Alice

    @pytest.mark.asyncio
    async def test_fetch_users_no_datasource(self):
        connector, *_ = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError):
            await connector._fetch_users()


# ===========================================================================
# Group Fetching & Sync
# ===========================================================================


class TestJiraGroupSync:

    @pytest.mark.asyncio
    async def test_sync_user_groups(self):
        connector, dep, *_ = _make_connector()
        connector.data_source = MagicMock()

        users = [AppUser(
            app_name=Connectors.JIRA, connector_id="conn-jira-1",
            source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
            full_name="Alice", is_active=True
        )]

        connector._fetch_groups = AsyncMock(return_value=[
            {"groupId": "g1", "name": "jira-software-users"},
        ])
        connector._fetch_group_members = AsyncMock(return_value=["alice@test.com"])

        result = await connector._sync_user_groups(users)
        assert "g1" in result
        assert "jira-software-users" in result
        dep.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_groups_pagination(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.bulk_get_groups = AsyncMock(return_value=_make_mock_response(data={
            "values": [{"groupId": "g1", "name": "group1"}],
            "isLast": True,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await connector._fetch_groups()
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetch_group_members(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_users_from_group = AsyncMock(return_value=_make_mock_response(data={
            "values": [{"emailAddress": "alice@test.com"}],
            "isLast": True,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await connector._fetch_group_members("g1", "group1")
            assert "alice@test.com" in result


# ===========================================================================
# Project Fetching
# ===========================================================================


class TestJiraProjectFetching:

    @pytest.mark.asyncio
    async def test_fetch_projects_all(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        projects = [{"id": "p1", "name": "Project One", "key": "PROJ", "url": "https://test.atlassian.net/project/PROJ", "description": "A project"}]

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(data={
            "values": projects, "isLast": True, "total": 1,
        }))

        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._safe_json_parse = MagicMock(return_value={"values": projects, "isLast": True, "total": 1})
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        record_groups, raw = await connector._fetch_projects()
        assert len(record_groups) == 1
        assert len(raw) == 1

    @pytest.mark.asyncio
    async def test_fetch_projects_with_keys_filter(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        projects = [{"id": "p1", "name": "Proj", "key": "PRJ", "url": None, "description": None}]

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(data={
            "values": projects, "isLast": True, "total": 1,
        }))

        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._safe_json_parse = MagicMock(return_value={"values": projects, "isLast": True, "total": 1})
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        record_groups, raw = await connector._fetch_projects(project_keys=["PRJ"])
        assert len(record_groups) == 1

    @pytest.mark.asyncio
    async def test_fetch_projects_description_as_adf(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        projects = [{
            "id": "p1", "name": "Proj", "key": "PRJ", "url": None,
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "ADF content"}]}]}
        }]

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(data={
            "values": projects, "isLast": True, "total": 1,
        }))

        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        connector._safe_json_parse = MagicMock(return_value={"values": projects, "isLast": True, "total": 1})
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        record_groups, raw = await connector._fetch_projects()
        rg, _ = record_groups[0]
        assert "ADF content" in (rg.description or "")


# ===========================================================================
# Permission Scheme
# ===========================================================================


class TestPermissionScheme:

    @pytest.mark.asyncio
    async def test_fetch_permission_scheme_all_types(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(data={"id": 1}))

        grants = [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "value": "g1"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "applicationRole", "parameter": "jira-software", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "user", "parameter": "a1", "value": None, "user": {"emailAddress": "alice@test.com"}}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "anyone", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectRole", "parameter": "10001", "value": None, "projectRole": {"name": "Developers", "id": "10001"}}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "sd.customer.portal.only", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "groupCustomField", "value": None}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "unknownType", "value": None, "parameter": None}},
            {"permission": "OTHER_PERM", "holder": {"type": "group", "value": "g2"}},  # not BROWSE_PROJECTS
        ]

        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(data={
            "permissions": grants,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            app_roles = {"jira-software": [{"groupId": "app-g1", "name": "SW Users"}]}
            perms = await connector._fetch_project_permission_scheme("PROJ", app_roles)

            entity_types = {p.entity_type for p in perms}
            assert EntityType.GROUP in entity_types
            assert EntityType.USER in entity_types
            assert EntityType.ORG in entity_types
            assert EntityType.ROLE in entity_types

    @pytest.mark.asyncio
    async def test_application_role_no_mapping_fallback(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(data={"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(data={
            "permissions": [
                {"permission": "BROWSE_PROJECTS", "holder": {"type": "applicationRole", "parameter": "unknown-role", "value": None}},
            ],
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            perms = await connector._fetch_project_permission_scheme("PROJ", {})
            assert len(perms) == 1
            assert perms[0].entity_type == EntityType.ORG


# ===========================================================================
# Deletion Handling
# ===========================================================================


class TestDeletionHandling:

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_not_found_in_db(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(status=404))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            tx.get_record_by_issue_key = AsyncMock(return_value=None)
            await connector._handle_deleted_issue("PROJ-1")
            # Should not crash, just log warning

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_still_exists(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(status=200, data={"key": "PROJ-1"}))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._handle_deleted_issue("PROJ-1")
            # Issue still exists, should not delete

    @pytest.mark.asyncio
    async def test_handle_deleted_epic(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(side_effect=Exception("not found"))

        issue_record = MagicMock()
        issue_record.id = "internal-1"
        issue_record.external_record_id = "ext-1"
        issue_record.type = MagicMock()
        issue_record.type.value = "EPIC"
        tx.get_record_by_issue_key = AsyncMock(return_value=issue_record)

        connector._delete_issue_children = AsyncMock(return_value=2)

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._handle_deleted_issue("PROJ-1")
            # Epics don't cascade delete children, but do delete attachments
            assert connector._delete_issue_children.call_count == 1  # only FILE, not TICKET

    @pytest.mark.asyncio
    async def test_handle_deleted_task(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_issue = AsyncMock(side_effect=Exception("not found"))

        issue_record = MagicMock()
        issue_record.id = "internal-1"
        issue_record.external_record_id = "ext-1"
        issue_record.type = "TASK"
        tx.get_record_by_issue_key = AsyncMock(return_value=issue_record)

        connector._delete_issue_children = AsyncMock(return_value=0)

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._handle_deleted_issue("PROJ-2")
            # Tasks cascade to subtasks AND attachments
            assert connector._delete_issue_children.call_count == 2

    @pytest.mark.asyncio
    async def test_delete_issue_children_recursive(self):
        connector, dep, dsp, cs, tx = _make_connector()

        # Child record
        child = MagicMock()
        child.id = "child-1"
        child.external_record_id = "child-ext-1"
        tx.get_records_by_parent = AsyncMock(side_effect=[
            [child],  # first call for subtasks
            [],  # nested subtasks of child
            [],  # attachments of child
        ])

        result = await connector._delete_issue_children("parent-ext-1", RecordType.TICKET, tx)
        assert result == 1

    @pytest.mark.asyncio
    async def test_detect_and_handle_deletions(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=["PROJ-1"])
        connector._handle_deleted_issue = AsyncMock()

        result = await connector._detect_and_handle_deletions(1000)
        assert result == 1
        connector._handle_deleted_issue.assert_called_once_with("PROJ-1")

    @pytest.mark.asyncio
    async def test_detect_no_deletions(self):
        connector, *_ = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=[])
        result = await connector._detect_and_handle_deletions(1000)
        assert result == 0

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_from_audit(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response(data={
            "records": [
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-1"}, "created": "2024-01-01"},
                {"objectItem": {"typeName": "OTHER_ACTION", "name": "PROJ-2"}, "created": "2024-01-01"},
            ],
            "total": 2,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z")
            assert result == ["PROJ-1"]


# ===========================================================================
# Sync Checkpoints
# ===========================================================================


class TestSyncCheckpoints:

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        result = await connector._get_issues_sync_checkpoint()
        assert result == 1000

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint_none(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await connector._get_issues_sync_checkpoint()
        assert result is None

    @pytest.mark.asyncio
    async def test_update_issues_sync_checkpoint(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.update_sync_point = AsyncMock()

        await connector._update_issues_sync_checkpoint({"total_synced": 5}, 1)
        connector.issues_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_issues_sync_checkpoint_no_sync(self):
        connector, *_ = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.update_sync_point = AsyncMock()

        await connector._update_issues_sync_checkpoint({"total_synced": 0}, 0)
        connector.issues_sync_point.update_sync_point.assert_not_called()


# ===========================================================================
# Full Sync Workflow
# ===========================================================================


class TestJiraFullSync:

    @pytest.mark.asyncio
    async def test_run_sync_full_workflow(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.data_source = MagicMock()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):

            users = [AppUser(
                app_name=Connectors.JIRA, connector_id="conn-jira-1",
                source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
                full_name="Alice", is_active=True
            )]
            connector._fetch_users = AsyncMock(return_value=users)
            connector._sync_user_groups = AsyncMock(return_value={})

            rg = RecordGroup(
                id=str(uuid4()), org_id="org-jira-1", external_group_id="p1",
                connector_id="conn-jira-1", connector_name=Connectors.JIRA,
                name="Project", short_name="PRJ", group_type=RecordGroupType.PROJECT,
            )
            connector._fetch_projects = AsyncMock(return_value=(
                [(rg, [])],
                [{"key": "PRJ", "lead": {"accountId": "a1", "displayName": "Alice"}}],
            ))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={
                "total_synced": 5, "new_count": 3, "updated_count": 2,
            })
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()

            await connector.run_sync()

            dep.on_new_app_users.assert_called_once()
            dep.on_new_record_groups.assert_called_once()
            connector._sync_project_roles.assert_called_once()
            connector._sync_project_lead_roles.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_no_users_returns(self):
        connector, dep, *_ = _make_connector()
        dep.get_all_active_users = AsyncMock(return_value=[])
        connector.data_source = MagicMock()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            await connector.run_sync()
            dep.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_sync_inits_if_no_datasource(self):
        connector, dep, *_ = _make_connector()
        connector.data_source = None

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):
            connector.init = AsyncMock()
            connector._fetch_users = AsyncMock(return_value=[])
            connector._sync_user_groups = AsyncMock(return_value={})
            connector._fetch_projects = AsyncMock(return_value=([], []))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={"total_synced": 0, "new_count": 0, "updated_count": 0})
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()

            await connector.run_sync()
            connector.init.assert_called_once()


# ===========================================================================
# Project Roles
# ===========================================================================


class TestProjectRoles:

    @pytest.mark.asyncio
    async def test_sync_project_roles(self):
        connector, dep, *_ = _make_connector()
        connector.data_source = MagicMock()

        users = [AppUser(
            app_name=Connectors.JIRA, connector_id="conn-jira-1",
            source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
            full_name="Alice", is_active=True
        )]

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(data={
            "Developers": "https://api.atlassian.com/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(data={
            "name": "Developers",
            "actors": [
                {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "a1", "emailAddress": "alice@test.com"}},
                {"type": "atlassian-group-role-actor", "name": "jira-users", "groupId": "g1"},
            ]
        }))

        groups_map = {"g1": users, "jira-users": users}

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await connector._sync_project_roles(["PRJ"], users, groups_map)
            dep.on_new_app_roles.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_project_lead_roles(self):
        connector, dep, *_ = _make_connector()

        users = [AppUser(
            app_name=Connectors.JIRA, connector_id="conn-jira-1",
            source_user_id="a1", org_id="org-jira-1", email="alice@test.com",
            full_name="Alice", is_active=True
        )]

        raw_projects = [
            {"key": "PRJ", "lead": {"accountId": "a1", "displayName": "Alice"}},
            {"key": "PRJ2", "lead": None},  # No lead
        ]

        await connector._sync_project_lead_roles(raw_projects, users)
        dep.on_new_app_roles.assert_called_once()
        call_args = dep.on_new_app_roles.call_args[0][0]
        assert len(call_args) == 2  # Both projects get roles


# ===========================================================================
# Filter Options
# ===========================================================================


class TestJiraFilterOptions:

    @pytest.mark.asyncio
    async def test_get_filter_options_projects(self):
        connector, *_ = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.search_projects = AsyncMock(return_value=_make_mock_response(data={
            "values": [{"key": "PROJ", "name": "My Project"}],
            "isLast": True,
        }))

        with patch.object(connector, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            connector._safe_json_parse = MagicMock(return_value={
                "values": [{"key": "PROJ", "name": "My Project"}],
                "isLast": True,
            })
            result = await connector.get_filter_options("project_keys")
            assert result.success is True
            assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_get_filter_options_unknown_raises(self):
        connector, *_ = _make_connector()
        with pytest.raises(ValueError, match="Unsupported"):
            await connector.get_filter_options("unknown")


# ===========================================================================
# Safe JSON Parse
# ===========================================================================


class TestSafeJsonParse:

    def test_parse_valid_response(self):
        connector, *_ = _make_connector()
        resp = MagicMock()
        resp.json.return_value = {"key": "value"}
        result = connector._safe_json_parse(resp, "test")
        assert result == {"key": "value"}

    def test_parse_invalid_json(self):
        connector, *_ = _make_connector()
        resp = MagicMock()
        resp.json.side_effect = Exception("bad json")
        resp.text.return_value = "not json"
        result = connector._safe_json_parse(resp, "test")
        assert result is None


# ===========================================================================
# Timestamp Parsing
# ===========================================================================


class TestJiraTimestampParsing:

    def test_parse_jira_timestamp(self):
        connector, *_ = _make_connector()
        result = connector._parse_jira_timestamp("2024-01-01T00:00:00.000+0000")
        assert isinstance(result, int)
        assert result > 0

    def test_parse_jira_timestamp_none(self):
        connector, *_ = _make_connector()
        assert connector._parse_jira_timestamp(None) == 0

    def test_parse_jira_timestamp_invalid(self):
        connector, *_ = _make_connector()
        # Should handle gracefully
        result = connector._parse_jira_timestamp("not-a-date")
        assert isinstance(result, int)
