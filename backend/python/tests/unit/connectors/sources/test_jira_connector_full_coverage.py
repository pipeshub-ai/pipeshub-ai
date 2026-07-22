"""Comprehensive tests for app.connectors.sources.atlassian.jira_cloud.connector."""

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus, RecordRelations
from app.connectors.sources.atlassian.jira_cloud.connector import (
    BATCH_PROCESSING_SIZE,
    DEFAULT_MAX_RESULTS,
    ISSUE_SEARCH_FIELDS,
    MAX_INLINE_IMAGE_BYTES,
    JiraConnector,
    adf_to_plain_text,
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
from app.models.permission import EntityType, Permission, PermissionType


def _make_mock_deps():
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
    data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
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


class TestAdfToPlainText:

    def test_non_dict_input(self):
        assert adf_to_plain_text("not a dict") == ""
        assert adf_to_plain_text(42) == ""
        assert adf_to_plain_text(None) == ""

    def test_paragraph_text(self):
        adf = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}],
        }
        assert adf_to_plain_text(adf) == "Hello world"

    def test_collects_nested_text_and_skips_media(self):
        adf = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Intro."}]},
                {"type": "table", "content": [{"type": "tableRow", "content": [
                    {"type": "tableCell", "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "cell"}]},
                        {"type": "mediaSingle", "content": [
                            {"type": "media", "attrs": {"id": "m1", "alt": "x.png"}}
                        ]},
                    ]},
                ]}]},
            ],
        }
        assert adf_to_plain_text(adf) == "Intro. cell"

    def test_whitespace_collapsed(self):
        adf = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "a   b \t c"}]}],
        }
        assert adf_to_plain_text(adf) == "a b c"

    def test_doc_without_content_key(self):
        assert adf_to_plain_text({"type": "paragraph"}) == ""


class TestFetchApplicationRolesToGroupsMapping:

    @pytest.mark.asyncio
    async def test_fetches_fresh_every_call(self):
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
        mock_ds.get_all_application_roles = AsyncMock(return_value=_make_mock_response(200, roles_data))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector._fetch_application_roles_to_groups_mapping()
        assert "jira-software" in result
        assert len(result["jira-software"]) == 2
        assert "empty-role" not in result

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_all_application_roles = AsyncMock(return_value=_make_mock_response(403))
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
        connector._fetch_groups = AsyncMock(return_value=([], False))
        jira_users = [_make_app_user()]

        result = await connector._sync_user_groups(jira_users)
        assert result == {}

    @pytest.mark.asyncio
    async def test_groups_with_members(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=([
            {"groupId": "g1", "name": "developers"},
        ], False))
        connector._fetch_group_members = AsyncMock(return_value=(["acc-1"], True))
        user = _make_app_user(email="user@example.com", account_id="acc-1")

        result = await connector._sync_user_groups([user])
        assert "g1" in result
        assert "developers" in result
        assert len(result["g1"]) == 1
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_group_without_id_skipped(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=([
            {"groupId": None, "name": "bad-group"},
            {"groupId": "g1", "name": None},
        ], False))
        connector._fetch_group_members = AsyncMock(return_value=[])

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_group_error_continues(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=([
            {"groupId": "g1", "name": "devs"},
        ], False))
        connector._fetch_group_members = AsyncMock(side_effect=Exception("API error"))

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(side_effect=Exception("total failure"))
        connector.notify = AsyncMock()

        result = await connector._sync_user_groups([])
        assert result == {}
        connector.notify.assert_awaited_once()


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
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Developers": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "Developers": "https://jira/rest/api/3/project/PROJ/role/10001",
        }))
        mock_ds.get_project_role = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(200, {
            "atlassian-addons-project-access": "https://jira/rest/api/3/project/PROJ/role/99999",
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_role_fetch_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.notify = AsyncMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(return_value=_make_mock_response(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()
        connector.notify.assert_awaited_once()
        assert "PROJ" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_handles_project_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.notify = AsyncMock()

        mock_ds = MagicMock()
        mock_ds.get_project_roles = AsyncMock(side_effect=Exception("network"))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()
        connector.notify.assert_awaited_once()
        assert "PROJ" in connector.notify.await_args.kwargs["message"]


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
        # Epic/subtask are now captured by the mapped issue_type, not is_epic/is_subtask flags.
        assert result["issue_type"].value == "EPIC"
        assert result["parent_external_id"] is None

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
        assert result["issue_type"].value == "SUBTASK"
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

    def test_resolves_mime_type_from_extension_map(self):
        connector = _make_connector()
        record = connector._create_attachment_file_record(
            attachment_id="104",
            filename="notes.md",
            mime_type="application/octet-stream",
            file_size=200,
            created_at=0,
            parent_issue_id="issue-1",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
        )
        assert record.extension == "md"
        assert record.mime_type == "text/markdown"

    def test_falls_back_to_api_mime_for_unmapped_extension(self):
        connector = _make_connector()
        record = connector._create_attachment_file_record(
            attachment_id="105",
            filename="data.unknownext",
            mime_type="application/custom",
            file_size=50,
            created_at=0,
            parent_issue_id="issue-1",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
        )
        assert record.mime_type == "application/custom"


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

    def test_all_comments_in_single_thread(self):
        connector = _make_connector()
        comments = [
            {"id": "c1", "created": "2024-01-01T00:00:00.000Z"},
            {"id": "c2", "created": "2024-01-01T01:00:00.000Z"},
            {"id": "c3", "created": "2024-01-02T00:00:00.000Z"},
        ]
        threads = connector._organize_issue_comments_to_threads(comments)
        assert len(threads) == 1
        assert len(threads[0]) == 3
        assert threads[0][0]["id"] == "c1"
        assert threads[0][2]["id"] == "c3"

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
            return_value=_make_mock_response(200, b"file-content")
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

        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_response))
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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(410))
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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_response))
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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_response))
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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_response))
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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(410))
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
        mock_ds.get_issue = AsyncMock(return_value=_make_mock_response(200, issue_response))
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
        connector._find_attachment_record_by_id = AsyncMock(return_value=mock_record)

        # Method now RETURNS the internal ids to soft-delete; the caller performs the delete.
        soft_delete_ids = await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        assert soft_delete_ids == ["rec-1"]

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
        tx_store.get_records_by_parent = AsyncMock(return_value=[mock_record])
        connector._find_attachment_record_by_id = AsyncMock(return_value=None)

        soft_delete_ids = await connector._handle_attachment_deletions_from_changelog(issue, tx_store)
        assert soft_delete_ids == ["rec-1"]

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
        # _detect_and_handle_deletions now returns (checkpoint_ms, success); the checkpoint is
        # advanced only when success is True.
        connector._detect_and_handle_deletions = AsyncMock(return_value=(1700000000001, True))

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
        mock_ds.get_current_user = AsyncMock(return_value=_make_mock_response(200))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_current_user = AsyncMock(return_value=_make_mock_response(401))
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

        await connector.cleanup()
        assert connector.external_client is None
        assert connector.data_source is None

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


class TestFetchAttachmentAsBase64:

    @pytest.mark.asyncio
    async def test_image_returns_data_uri(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_attachment_content = AsyncMock(return_value=_make_mock_response(200))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        cache: dict = {}
        result = await connector._fetch_attachment_as_base64(
            {"id": "42", "mimeType": "image/png", "size": 10}, cache
        )
        assert result.startswith("data:image/png;base64,")
        assert cache["42"] == result

    @pytest.mark.asyncio
    async def test_non_image_returns_none_without_fetch(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=AssertionError("must not fetch"))

        cache: dict = {}
        result = await connector._fetch_attachment_as_base64(
            {"id": "7", "mimeType": "application/pdf", "size": 10}, cache
        )
        assert result is None
        assert cache["7"] is None

    @pytest.mark.asyncio
    async def test_cache_hit_short_circuits(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=AssertionError("must not fetch"))

        cache = {"42": "data:image/png;base64,CACHED"}
        result = await connector._fetch_attachment_as_base64(
            {"id": "42", "mimeType": "image/png"}, cache
        )
        assert result == "data:image/png;base64,CACHED"

    @pytest.mark.asyncio
    async def test_oversized_returns_none(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=AssertionError("must not fetch"))

        cache: dict = {}
        result = await connector._fetch_attachment_as_base64(
            {"id": "42", "mimeType": "image/png", "size": MAX_INLINE_IMAGE_BYTES + 1}, cache
        )
        assert result is None
        assert cache["42"] is None

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_none(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_attachment_content = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        cache: dict = {}
        result = await connector._fetch_attachment_as_base64(
            {"id": "42", "mimeType": "image/png", "size": 10}, cache
        )
        assert result is None
        assert cache["42"] is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("boom"))

        cache: dict = {}
        result = await connector._fetch_attachment_as_base64(
            {"id": "42", "mimeType": "image/png", "size": 10}, cache
        )
        assert result is None
        assert cache["42"] is None


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
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response(200, {
            "records": [{
                "objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-99"},
                "created": "2024-01-01",
            }],
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # Now returns (issue_keys, ok).
        keys, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert "PROJ-99" in keys
        assert ok is True

    @pytest.mark.asyncio
    async def test_empty_audit(self):
        connector = _make_connector()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response(200, {
            "records": [],
            "total": 0,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        keys, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert keys == []
        assert ok is True

    @pytest.mark.asyncio
    async def test_audit_api_failure(self):
        connector = _make_connector()
        connector.notify = AsyncMock()

        mock_ds = MagicMock()
        mock_ds.get_audit_records = AsyncMock(return_value=_make_mock_response(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # A failed page returns ok=False so the caller won't advance past unread deletions.
        keys, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01", "2024-01-02")
        assert keys == []
        assert ok is False


class TestDetectAndHandleDeletions:

    @pytest.mark.asyncio
    async def test_no_deletions(self):
        connector = _make_connector()
        # _fetch_deleted_issues_from_audit returns (keys, ok); _detect returns (checkpoint_ms, success).
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=([], True))

        _checkpoint, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is True

    @pytest.mark.asyncio
    async def test_with_deletions(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=(["PROJ-1"], True))
        connector._handle_deleted_issue = AsyncMock()

        _checkpoint, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is True
        connector._handle_deleted_issue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletion_error_continues(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=(["PROJ-1", "PROJ-2"], True))
        connector._handle_deleted_issue = AsyncMock(
            side_effect=[Exception("err"), None]
        )

        # One deletion failed -> success is False so the caller retries the window.
        _checkpoint, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is False
        assert connector._handle_deleted_issue.await_count == 2

    @pytest.mark.asyncio
    async def test_overall_exception(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(side_effect=Exception("total fail"))

        _checkpoint, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is False


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
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
    async def test_application_role_without_mapping_skips(self):
        """When mapping is empty (not due to 403) and role_key exists, skip — don't over-grant to ORG."""
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "applicationRole", "parameter": "unknown-role"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ", {})
        assert len(permissions) == 0

    @pytest.mark.asyncio
    async def test_application_role_forbidden_grants_creator(self):
        """When 403 flag is set, grant configuring user instead of ORG."""
        connector = _make_connector()
        connector._app_roles_forbidden = True
        connector.creator_email = "admin@example.com"
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "applicationRole", "parameter": "jira-software"},
            }],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ", {})
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.USER
        assert permissions[0].email == "admin@example.com"

    @pytest.mark.asyncio
    async def test_scheme_fetch_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(403))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions == []

    @pytest.mark.asyncio
    async def test_grants_fetch_failure(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(500))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        # A transient (non-401/403) failure returns None -> caller skips the project and
        # preserves its existing ACL, rather than wiping it to an empty list.
        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("err"))

        permissions = await connector._fetch_project_permission_scheme("PROJ")
        assert permissions is None

    @pytest.mark.asyncio
    async def test_skips_sd_customer_and_custom_fields(self):
        connector = _make_connector()
        mock_ds = MagicMock()
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
        mock_ds.get_assigned_permission_scheme = AsyncMock(return_value=_make_mock_response(200, {"id": 1}))
        mock_ds.get_permission_scheme_grants = AsyncMock(return_value=_make_mock_response(200, {
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
