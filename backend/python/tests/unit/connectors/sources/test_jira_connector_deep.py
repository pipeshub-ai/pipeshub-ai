"""Deep coverage tests for the Jira Cloud connector.

Covers additional methods not exercised by existing test suites:
- _extract_issue_data (full extraction with users)
- _create_attachment_file_record (with/without filter)
- _organize_issue_comments_to_threads (threading logic)
- _extract_attachment_filenames_from_wiki
- _handle_attachment_deletions_from_changelog
- _delete_attachment_record / _find_attachment_record_by_id
- _parse_issue_links (outward, inward, none)
- stream_record (TICKET, FILE, unsupported)
- reindex_records (updated, non-updated, base Record class)
- _check_and_fetch_updated_record (ticket, file, unknown)
- cleanup (with/without client)
- test_connection_and_access
- get_signed_url
- handle_webhook_notification
- run_incremental_sync
- _safe_json_parse
- _parse_jira_timestamp (edge cases)
- get_filter_options
"""

import logging
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
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
    FileRecord,
    MimeTypes,
    OriginTypes,
    Record,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.jira.deep")
    dep = MagicMock()
    dep.org_id = "org-jira-1"
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

    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    tx.delete_records_and_relations = AsyncMock()
    tx.get_file_record_by_id = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _transaction():
        yield tx

    dsp = MagicMock()
    dsp.transaction = _transaction

    cs = MagicMock()
    cs.get_config = AsyncMock(return_value={
        "auth": {"authType": "API_TOKEN", "baseUrl": "https://test.atlassian.net"}
    })

    return logger, dep, dsp, cs, tx


def _make_connector():
    logger, dep, dsp, cs, tx = _make_mock_deps()
    c = JiraConnector(logger, dep, dsp, cs, "conn-jira-deep", "team", "test-user-id")
    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()
    c.data_source = AsyncMock()
    c.external_client = MagicMock()
    c.site_url = "https://test.atlassian.net"
    c.cloud_id = "cloud-1"
    return c, dep, dsp, cs, tx


def _make_jira_response(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    resp.bytes = MagicMock(return_value=b"content")
    return resp


# ===========================================================================
# _extract_issue_data
# ===========================================================================


class TestExtractIssueData:

    def test_basic_extraction(self):
        c, *_ = _make_connector()
        issue = {
            "id": "10001",
            "key": "PROJ-1",
            "fields": {
                "summary": "Fix bug",
                "description": None,
                "issuetype": {"name": "Bug", "hierarchyLevel": 0},
                "status": {"name": "Open"},
                "priority": {"name": "High"},
                "creator": {"accountId": "acc1", "displayName": "Creator"},
                "reporter": {"accountId": "acc2", "displayName": "Reporter"},
                "assignee": {"accountId": "acc3", "displayName": "Assignee"},
                "parent": None,
                "created": "2024-01-15T10:30:45.000+0000",
                "updated": "2024-06-15T10:30:45.000+0000",
            }
        }
        result = c._extract_issue_data(issue, {})
        assert result["issue_id"] == "10001"
        assert result["issue_key"] == "PROJ-1"
        assert result["issue_name"] == "[PROJ-1] Fix bug"
        assert result["is_epic"] is False
        assert result["is_subtask"] is False

    def test_epic_detection(self):
        c, *_ = _make_connector()
        issue = {
            "id": "10001",
            "key": "PROJ-1",
            "fields": {
                "summary": "Epic",
                "description": None,
                "issuetype": {"name": "Epic", "hierarchyLevel": 1},
                "status": {"name": "Open"},
                "priority": None,
                "creator": None,
                "reporter": None,
                "assignee": None,
                "parent": None,
                "created": "2024-01-15T10:30:45.000+0000",
                "updated": "2024-01-15T10:30:45.000+0000",
            }
        }
        result = c._extract_issue_data(issue, {})
        assert result["is_epic"] is True
        assert result["is_subtask"] is False

    def test_subtask_with_parent(self):
        c, *_ = _make_connector()
        issue = {
            "id": "10002",
            "key": "PROJ-2",
            "fields": {
                "summary": "Sub",
                "description": None,
                "issuetype": {"name": "Sub-task", "hierarchyLevel": -1},
                "status": {"name": "Open"},
                "priority": None,
                "creator": None,
                "reporter": None,
                "assignee": None,
                "parent": {"id": "10001", "key": "PROJ-1"},
                "created": "2024-01-15T10:30:45.000+0000",
                "updated": "2024-01-15T10:30:45.000+0000",
            }
        }
        result = c._extract_issue_data(issue, {})
        assert result["is_subtask"] is True
        assert result["parent_external_id"] == "10001"
        assert result["parent_key"] == "PROJ-1"

    def test_user_resolution(self):
        c, *_ = _make_connector()
        user_map = {
            "acc1": MagicMock(email="creator@test.com"),
            "acc2": MagicMock(email="reporter@test.com"),
        }
        issue = {
            "id": "10001",
            "key": "PROJ-1",
            "fields": {
                "summary": "Test",
                "description": None,
                "issuetype": {"name": "Task", "hierarchyLevel": 0},
                "status": {"name": "Done"},
                "priority": {"name": "Medium"},
                "creator": {"accountId": "acc1", "displayName": "Creator"},
                "reporter": {"accountId": "acc2", "displayName": "Reporter"},
                "assignee": None,
                "parent": None,
                "created": "2024-01-15T10:30:45.000+0000",
                "updated": "2024-01-15T10:30:45.000+0000",
            }
        }
        result = c._extract_issue_data(issue, user_map)
        assert result["creator_email"] == "creator@test.com"
        assert result["reporter_email"] == "reporter@test.com"
        assert result["assignee_email"] is None

    def test_with_adf_description(self):
        c, *_ = _make_connector()
        issue = {
            "id": "10001",
            "key": "PROJ-1",
            "fields": {
                "summary": "Test",
                "description": {
                    "type": "doc",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}]
                },
                "issuetype": {"name": "Task", "hierarchyLevel": 0},
                "status": {"name": "Open"},
                "priority": None,
                "creator": None,
                "reporter": None,
                "assignee": None,
                "parent": None,
                "created": "2024-01-15T10:30:45.000+0000",
                "updated": "2024-01-15T10:30:45.000+0000",
            }
        }
        result = c._extract_issue_data(issue, {})
        assert "Hello world" in result["description"]
        assert "Issue Type:" in result["description"]


# ===========================================================================
# _create_attachment_file_record
# ===========================================================================


class TestCreateAttachmentFileRecord:

    def test_basic_creation(self):
        c, *_ = _make_connector()
        record = c._create_attachment_file_record(
            attachment_id="12345",
            filename="report.pdf",
            mime_type="application/pdf",
            file_size=1024,
            created_at=1700000000000,
            parent_issue_id="10001",
            parent_node_id="node-1",
            project_id="proj-1",
            weburl="https://test.atlassian.net/attachment/12345",
        )
        assert record.record_name == "report.pdf"
        assert record.extension == "pdf"
        assert record.external_record_id == "attachment_12345"
        assert record.mime_type == "application/pdf"
        assert record.is_file is True
        assert record.is_dependent_node is True

    def test_no_extension(self):
        c, *_ = _make_connector()
        record = c._create_attachment_file_record(
            attachment_id="12346",
            filename="README",
            mime_type="text/plain",
            file_size=256,
            created_at=1700000000000,
            parent_issue_id="10001",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
        )
        assert record.extension is None

    def test_with_existing_record_id(self):
        c, *_ = _make_connector()
        record = c._create_attachment_file_record(
            attachment_id="12347",
            filename="img.png",
            mime_type="image/png",
            file_size=2048,
            created_at=1700000000000,
            parent_issue_id="10001",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
            record_id="existing-id",
            version=3,
        )
        assert record.id == "existing-id"
        assert record.version == 3

    def test_with_indexing_filter_disabled(self):
        c, *_ = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = c._create_attachment_file_record(
            attachment_id="12348",
            filename="data.csv",
            mime_type="text/csv",
            file_size=512,
            created_at=1700000000000,
            parent_issue_id="10001",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
        )
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    def test_skip_filter_check(self):
        c, *_ = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = c._create_attachment_file_record(
            attachment_id="12349",
            filename="data.csv",
            mime_type="text/csv",
            file_size=512,
            created_at=1700000000000,
            parent_issue_id="10001",
            parent_node_id=None,
            project_id="proj-1",
            weburl=None,
            skip_filter_check=True,
        )
        # Should NOT have AUTO_INDEX_OFF because skip_filter_check=True
        assert not hasattr(record, 'indexing_status') or record.indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


# ===========================================================================
# _organize_issue_comments_to_threads
# ===========================================================================


class TestOrganizeIssueCommentsToThreads:

    def test_empty_comments(self):
        c, *_ = _make_connector()
        result = c._organize_issue_comments_to_threads([])
        assert result == []

    def test_single_top_level_comment(self):
        c, *_ = _make_connector()
        comments = [
            {"id": "c1", "created": "2024-01-15T10:00:00.000+0000", "parent": {}}
        ]
        result = c._organize_issue_comments_to_threads(comments)
        assert len(result) == 1
        assert len(result[0]) == 1

    def test_threaded_comments(self):
        c, *_ = _make_connector()
        comments = [
            {"id": "c1", "created": "2024-01-15T10:00:00.000+0000", "parent": {}},
            {"id": "c2", "created": "2024-01-15T11:00:00.000+0000", "parent": {"id": "c1"}},
            {"id": "c3", "created": "2024-01-16T10:00:00.000+0000", "parent": {}},
        ]
        result = c._organize_issue_comments_to_threads(comments)
        assert len(result) == 2  # Two threads: c1+c2, c3

    def test_sorting_by_created(self):
        c, *_ = _make_connector()
        comments = [
            {"id": "c2", "created": "2024-01-16T10:00:00.000+0000", "parent": {}},
            {"id": "c1", "created": "2024-01-15T10:00:00.000+0000", "parent": {}},
        ]
        result = c._organize_issue_comments_to_threads(comments)
        assert len(result) == 2
        # First thread should be the earlier one
        assert result[0][0]["id"] == "c1"


# ===========================================================================
# _extract_attachment_filenames_from_wiki
# ===========================================================================


class TestExtractAttachmentFilenamesFromWiki:

    def test_basic_extraction(self):
        c, *_ = _make_connector()
        text = "See !screenshot.png|width=300! for reference"
        result = c._extract_attachment_filenames_from_wiki(text)
        assert "screenshot.png" in result

    def test_multiple_attachments(self):
        c, *_ = _make_connector()
        text = "!file1.pdf! and !file2.docx|thumbnail!"
        result = c._extract_attachment_filenames_from_wiki(text)
        assert "file1.pdf" in result
        assert "file2.docx" in result

    def test_no_attachments(self):
        c, *_ = _make_connector()
        text = "No attachments here"
        result = c._extract_attachment_filenames_from_wiki(text)
        assert result == set()


# ===========================================================================
# _delete_attachment_record
# ===========================================================================


class TestDeleteAttachmentRecord:

    @pytest.mark.asyncio
    async def test_deletes_record(self):
        c, dep, dsp, cs, tx = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "att_123"
        record.record_name = "file.pdf"

        await c._delete_attachment_record(record, "PROJ-1", tx, "test reason")
        tx.delete_records_and_relations.assert_called_once_with(
            record_key="r1", hard_delete=True
        )


# ===========================================================================
# _find_attachment_record_by_id
# ===========================================================================


class TestFindAttachmentRecordById:

    @pytest.mark.asyncio
    async def test_finds_record(self):
        c, dep, dsp, cs, tx = _make_connector()
        mock_record = MagicMock()
        tx.get_record_by_external_id = AsyncMock(return_value=mock_record)

        result = await c._find_attachment_record_by_id("12345", tx)
        assert result == mock_record
        tx.get_record_by_external_id.assert_called_with(
            connector_id="conn-jira-deep",
            external_id="attachment_12345"
        )

    @pytest.mark.asyncio
    async def test_not_found(self):
        c, dep, dsp, cs, tx = _make_connector()
        tx.get_record_by_external_id = AsyncMock(return_value=None)

        result = await c._find_attachment_record_by_id("99999", tx)
        assert result is None


# ===========================================================================
# _handle_attachment_deletions_from_changelog
# ===========================================================================


class TestHandleAttachmentDeletionsFromChangelog:

    @pytest.mark.asyncio
    async def test_no_changelog(self):
        c, dep, dsp, cs, tx = _make_connector()
        issue = {"id": "10001", "key": "PROJ-1"}
        await c._handle_attachment_deletions_from_changelog(issue, tx)
        tx.delete_records_and_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_histories(self):
        c, dep, dsp, cs, tx = _make_connector()
        issue = {"id": "10001", "key": "PROJ-1", "changelog": {"histories": []}}
        await c._handle_attachment_deletions_from_changelog(issue, tx)
        tx.delete_records_and_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_attachment_deletion(self):
        c, dep, dsp, cs, tx = _make_connector()
        mock_record = MagicMock()
        mock_record.id = "r1"
        mock_record.external_record_id = "att_555"
        mock_record.record_name = "deleted.pdf"
        tx.get_record_by_external_id = AsyncMock(return_value=mock_record)

        issue = {
            "id": "10001",
            "key": "PROJ-1",
            "fields": {"attachment": []},
            "changelog": {
                "histories": [{
                    "items": [{
                        "field": "Attachment",
                        "fieldId": "attachment",
                        "from": "555",
                        "to": None,
                    }]
                }]
            }
        }
        await c._handle_attachment_deletions_from_changelog(issue, tx)
        tx.delete_records_and_relations.assert_called()


# ===========================================================================
# _parse_issue_links
# ===========================================================================


class TestParseIssueLinks:

    def test_outward_link(self):
        c, *_ = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [{
                    "type": {"name": "Blocks", "outward": "blocks"},
                    "outwardIssue": {"id": "10002", "key": "PROJ-2"}
                }]
            }
        }
        result = c._parse_issue_links(issue)
        assert len(result) == 1
        assert result[0].external_record_id == "10002"

    def test_inward_link_skipped(self):
        c, *_ = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [{
                    "type": {"name": "Blocks", "inward": "is blocked by"},
                    "inwardIssue": {"id": "10003", "key": "PROJ-3"}
                }]
            }
        }
        result = c._parse_issue_links(issue)
        assert len(result) == 0

    def test_none_issue(self):
        c, *_ = _make_connector()
        result = c._parse_issue_links(None)
        assert result == []

    def test_no_issuelinks(self):
        c, *_ = _make_connector()
        issue = {"fields": {}}
        result = c._parse_issue_links(issue)
        assert result == []


# ===========================================================================
# _safe_json_parse
# ===========================================================================


class TestSafeJsonParse:

    def test_valid_json(self):
        c, *_ = _make_connector()
        resp = MagicMock()
        resp.json.return_value = {"key": "value"}
        result = c._safe_json_parse(resp)
        assert result == {"key": "value"}

    def test_invalid_json(self):
        c, *_ = _make_connector()
        resp = MagicMock()
        resp.json.side_effect = Exception("JSON decode error")
        result = c._safe_json_parse(resp, "test context")
        assert result is None


# ===========================================================================
# _parse_jira_timestamp (extended)
# ===========================================================================


class TestParseJiraTimestampExtended:

    def test_with_milliseconds(self):
        c, *_ = _make_connector()
        result = c._parse_jira_timestamp("2024-01-15T10:30:45.123+0000")
        assert result > 0

    def test_without_milliseconds(self):
        c, *_ = _make_connector()
        result = c._parse_jira_timestamp("2024-01-15T10:30:45+0000")
        assert result > 0

    def test_iso_with_colon_tz(self):
        c, *_ = _make_connector()
        result = c._parse_jira_timestamp("2024-01-15T10:30:45.123+00:00")
        assert result > 0

    def test_z_suffix(self):
        c, *_ = _make_connector()
        result = c._parse_jira_timestamp("2024-01-15T10:30:45.123Z")
        assert result > 0

    def test_none_returns_zero(self):
        c, *_ = _make_connector()
        assert c._parse_jira_timestamp(None) == 0

    def test_empty_returns_zero(self):
        c, *_ = _make_connector()
        assert c._parse_jira_timestamp("") == 0


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_ticket_record(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.external_record_id = "10001"
        c._process_issue_blockgroups_for_streaming = AsyncMock(return_value=b'{"blocks": []}')

        from fastapi.responses import StreamingResponse
        result = await c.stream_record(record)
        assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_file_record(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_id = "attachment_12345"
        record.record_name = "report.pdf"
        record.mime_type = "application/pdf"

        resp = _make_jira_response(200)
        ds = AsyncMock()
        ds.get_attachment_content = AsyncMock(return_value=resp)
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.create_stream_record_response") as mock_stream, \
             patch("app.connectors.sources.atlassian.jira_cloud.connector.sanitize_filename_for_content_disposition", return_value="report.pdf"), \
             patch("app.connectors.sources.atlassian.jira_cloud.connector.quote", return_value="report.pdf"):
            mock_stream.return_value = MagicMock()
            result = await c.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.external_record_id = "ext-1"

        with pytest.raises(ValueError, match="Unsupported record type"):
            await c.stream_record(record)

    @pytest.mark.asyncio
    async def test_file_fetch_failure_raises(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_id = "attachment_12345"
        record.record_name = "report.pdf"

        resp = _make_jira_response(404)
        ds = AsyncMock()
        ds.get_attachment_content = AsyncMock(return_value=resp)
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        with pytest.raises(Exception, match="Failed to fetch attachment"):
            await c.stream_record(record)


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_empty_records(self):
        c, dep, *_ = _make_connector()
        await c.reindex_records([])
        dep.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_datasource_raises(self):
        c, *_ = _make_connector()
        c.data_source = None
        rec = MagicMock(id="r1")
        with pytest.raises(Exception, match="DataSource not initialized"):
            await c.reindex_records([rec])

    @pytest.mark.asyncio
    async def test_updated_records(self):
        c, dep, *_ = _make_connector()
        rec = MagicMock(id="r1")
        c._check_and_fetch_updated_record = AsyncMock(
            return_value=("updated", [])
        )
        await c.reindex_records([rec])
        dep.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_updated_typed_records(self):
        c, dep, *_ = _make_connector()
        # Create a TicketRecord (not base Record class)
        rec = MagicMock(spec=TicketRecord)
        rec.id = "r1"
        rec.record_type = RecordType.TICKET
        type(rec).__name__ = "TicketRecord"

        c._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await c.reindex_records([rec])
        dep.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_base_record_class_skipped(self):
        c, dep, *_ = _make_connector()
        # Create a base Record (should be skipped for reindex)
        rec = MagicMock(spec=Record)
        rec.id = "r1"
        rec.record_type = RecordType.TICKET
        type(rec).__name__ = "Record"

        c._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await c.reindex_records([rec])
        dep.reindex_existing_records.assert_not_called()


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:

    @pytest.mark.asyncio
    async def test_ticket_delegates(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.id = "r1"
        c._check_and_fetch_updated_issue = AsyncMock(return_value=("issue", []))
        result = await c._check_and_fetch_updated_record(record)
        assert result == ("issue", [])

    @pytest.mark.asyncio
    async def test_file_delegates(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.id = "r1"
        c._check_and_fetch_updated_attachment = AsyncMock(return_value=("att", []))
        result = await c._check_and_fetch_updated_record(record)
        assert result == ("att", [])

    @pytest.mark.asyncio
    async def test_unknown_type_returns_none(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.id = "r1"
        result = await c._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c, *_ = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.id = "r1"
        c._check_and_fetch_updated_issue = AsyncMock(side_effect=Exception("fail"))
        result = await c._check_and_fetch_updated_record(record)
        assert result is None


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:

    @pytest.mark.asyncio
    async def test_with_client(self):
        c, *_ = _make_connector()
        internal = MagicMock()
        internal.close = AsyncMock()
        c.external_client.get_client.return_value = internal

        await c.cleanup()
        internal.close.assert_called_once()
        assert c.external_client is None
        assert c.data_source is None

    @pytest.mark.asyncio
    async def test_no_client(self):
        c, *_ = _make_connector()
        c.external_client = None
        await c.cleanup()
        assert c.data_source is None

    @pytest.mark.asyncio
    async def test_close_error_handled(self):
        c, *_ = _make_connector()
        internal = MagicMock()
        internal.close = AsyncMock(side_effect=Exception("already closed"))
        c.external_client.get_client.return_value = internal

        # Should not raise
        await c.cleanup()
        assert c.external_client is None


# ===========================================================================
# test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_success(self):
        c, *_ = _make_connector()
        resp = _make_jira_response(200)
        ds = AsyncMock()
        ds.get_current_user = AsyncMock(return_value=resp)
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self):
        c, *_ = _make_connector()
        c._get_fresh_datasource = AsyncMock(side_effect=Exception("auth error"))

        result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_datasource_inits(self):
        c, *_ = _make_connector()
        c.data_source = None
        c.init = AsyncMock()

        resp = _make_jira_response(200)
        ds = AsyncMock()
        ds.get_current_user = AsyncMock(return_value=resp)
        c._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await c.test_connection_and_access()
        c.init.assert_called_once()


# ===========================================================================
# get_signed_url / handle_webhook_notification / run_incremental_sync
# ===========================================================================


class TestPublicApiMethods:

    @pytest.mark.asyncio
    async def test_get_signed_url_returns_empty(self):
        c, *_ = _make_connector()
        record = MagicMock()
        result = await c.get_signed_url(record)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        c, *_ = _make_connector()
        # Should not raise
        await c.handle_webhook_notification({"event": "test"})

    @pytest.mark.asyncio
    async def test_run_incremental_sync_calls_run_sync(self):
        c, *_ = _make_connector()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_called_once()


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_project_keys(self):
        c, *_ = _make_connector()
        c._get_project_options = AsyncMock(return_value="project_opts")
        from app.connectors.core.registry.filters import SyncFilterKey
        result = await c.get_filter_options(SyncFilterKey.PROJECT_KEYS)
        assert result == "project_opts"

    @pytest.mark.asyncio
    async def test_unsupported_filter_key(self):
        c, *_ = _make_connector()
        with pytest.raises(ValueError, match="Unsupported"):
            await c.get_filter_options("invalid_key")
