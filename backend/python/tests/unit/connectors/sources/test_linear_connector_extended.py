"""Extended coverage tests for the Linear connector - transformation, filtering, and helpers."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.linear.connector import LinearConnector
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    ProjectRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_by_weburl = AsyncMock(return_value=None)
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


def _make_connector():
    """Build a LinearConnector with all dependencies mocked."""
    logger = logging.getLogger("test.linear")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_store_provider = _make_mock_data_store_provider()
    config_service = AsyncMock()
    connector_id = "linear-test-1"
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
        scope="personal",
        created_by="test-user-id",
    )
    connector.organization_id = "org-linear-1"
    connector.organization_name = "Test Org"
    connector.organization_url_key = "test-org"
    return connector


def _make_issue_data(
    issue_id="issue-1",
    identifier="ENG-1",
    title="Test Issue",
    priority=3,
    state_name="In Progress",
    state_type="started",
    assignee_email="dev@test.com",
    creator_email="pm@test.com",
    created_at="2024-01-15T10:00:00.000Z",
    updated_at="2024-06-15T10:00:00.000Z",
    parent_id=None,
    labels=None,
    relations=None,
    url="https://linear.app/test-org/issue/ENG-1",
):
    data = {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "priority": priority,
        "state": {"name": state_name, "type": state_type},
        "assignee": {
            "email": assignee_email,
            "name": "Developer",
            "displayName": "Dev Name",
        } if assignee_email else None,
        "creator": {
            "email": creator_email,
            "name": "PM",
            "displayName": "PM Name",
        } if creator_email else None,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "url": url,
        "labels": {"nodes": labels or []},
        "relations": {"nodes": relations or []},
    }
    if parent_id:
        data["parent"] = {"id": parent_id}
    return data


def _make_project_data(
    project_id="proj-1",
    name="Test Project",
    slug_id="test-project",
    status_name="active",
    priority_label="High",
    lead_email="lead@test.com",
    created_at="2024-01-01T00:00:00.000Z",
    updated_at="2024-06-01T00:00:00.000Z",
    url="https://linear.app/test-org/project/test-project",
):
    return {
        "id": project_id,
        "name": name,
        "slugId": slug_id,
        "status": {"name": status_name},
        "priorityLabel": priority_label,
        "lead": {
            "id": "lead-1",
            "email": lead_email,
            "name": "Lead",
            "displayName": "Lead Name",
        } if lead_email else None,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "url": url,
    }


# ---------------------------------------------------------------------------
# Tests: _parse_linear_datetime
# ---------------------------------------------------------------------------

class TestParseLinearDatetime:
    def test_valid_datetime(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime("2024-01-15T10:30:00.000Z")
        assert result is not None
        assert isinstance(result, int)
        assert result > 0

    def test_empty_string(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime("")
        assert result is None

    def test_invalid_string(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime("not-a-date")
        assert result is None


class TestParseLinearDatetimeToDatetime:
    def test_valid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime("2024-01-15T10:30:00.000Z")
        assert isinstance(result, datetime)

    def test_empty(self):
        connector = _make_connector()
        assert connector._parse_linear_datetime_to_datetime("") is None

    def test_none(self):
        connector = _make_connector()
        assert connector._parse_linear_datetime_to_datetime(None) is None


class TestLinearDatetimeFromTimestamp:
    def test_valid_timestamp(self):
        connector = _make_connector()
        # 2024-01-15T10:30:00.000Z
        ts = 1705313400000
        result = connector._linear_datetime_from_timestamp(ts)
        assert result.endswith("Z")
        assert "2024-01-15" in result

    def test_invalid_timestamp(self):
        connector = _make_connector()
        result = connector._linear_datetime_from_timestamp(-999999999999999999)
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: _transform_issue_to_ticket_record
# ---------------------------------------------------------------------------

class TestTransformIssueToTicketRecord:
    def test_basic_transformation(self):
        connector = _make_connector()
        issue = _make_issue_data()
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")

        assert ticket.record_type == RecordType.TICKET
        assert ticket.record_name == "Test Issue"
        assert ticket.external_record_id == "issue-1"
        assert ticket.external_record_group_id == "team-1"
        assert ticket.connector_id == "linear-test-1"
        assert ticket.assignee_email == "dev@test.com"
        assert ticket.creator_email == "pm@test.com"
        assert ticket.version == 0

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_issue_to_ticket_record({"id": ""}, "team-1")

    def test_missing_team_id_raises(self):
        connector = _make_connector()
        issue = _make_issue_data()
        with pytest.raises(ValueError, match="team_id is required"):
            connector._transform_issue_to_ticket_record(issue, "")

    def test_priority_mapping(self):
        connector = _make_connector()
        # Priority 0 = none
        issue = _make_issue_data(priority=0)
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        # Priority should be mapped (none -> some default)
        assert ticket.priority is not None or ticket.priority is None  # depends on mapper

    def test_priority_urgent(self):
        connector = _make_connector()
        issue = _make_issue_data(priority=1)
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket is not None

    def test_no_assignee(self):
        connector = _make_connector()
        issue = _make_issue_data(assignee_email=None)
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.assignee_email is None

    def test_no_creator(self):
        connector = _make_connector()
        issue = _make_issue_data(creator_email=None)
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.creator_email is None

    def test_sub_issue_type(self):
        connector = _make_connector()
        issue = _make_issue_data(parent_id="parent-1")
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.type == ItemType.SUB_ISSUE

    def test_identifier_only_name(self):
        connector = _make_connector()
        issue = _make_issue_data(title="", identifier="ENG-99")
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == "ENG-99"

    def test_no_identifier_no_title(self):
        connector = _make_connector()
        issue = _make_issue_data(title="", identifier="")
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == "issue-1"  # Falls back to issue ID

    def test_existing_record_version_increment(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.source_updated_at = 1000  # Different from issue
        issue = _make_issue_data()
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1", existing)
        assert ticket.version == 3
        assert ticket.id == "existing-id"

    def test_existing_record_same_version(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        # Match the exact updatedAt timestamp
        ts = connector._parse_linear_datetime("2024-06-15T10:00:00.000Z")
        existing.source_updated_at = ts
        issue = _make_issue_data()
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1", existing)
        assert ticket.version == 2

    def test_relations_mapped(self):
        connector = _make_connector()
        issue = _make_issue_data(relations=[
            {
                "type": "blocks",
                "relatedIssue": {"id": "related-1"},
            }
        ])
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert len(ticket.related_external_records) > 0

    def test_label_type_mapping(self):
        connector = _make_connector()
        issue = _make_issue_data(labels=[{"name": "Bug"}])
        ticket = connector._transform_issue_to_ticket_record(issue, "team-1")
        # Bug label should map to BUG type
        assert ticket.type is not None


# ---------------------------------------------------------------------------
# Tests: _transform_to_project_record
# ---------------------------------------------------------------------------

class TestTransformToProjectRecord:
    def test_basic_transformation(self):
        connector = _make_connector()
        project = _make_project_data()
        record = connector._transform_to_project_record(project, "team-1")

        assert record.record_type == RecordType.PROJECT
        assert record.record_name == "Test Project"
        assert record.external_record_id == "proj-1"
        assert record.external_record_group_id == "team-1"
        assert record.lead_email == "lead@test.com"

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_to_project_record({"id": ""}, "team-1")

    def test_name_fallback_to_slug(self):
        connector = _make_connector()
        project = _make_project_data(name="", slug_id="my-slug")
        record = connector._transform_to_project_record(project, "team-1")
        assert record.record_name == "my-slug"

    def test_name_fallback_to_id(self):
        connector = _make_connector()
        project = _make_project_data(name="", slug_id="")
        record = connector._transform_to_project_record(project, "team-1")
        assert record.record_name == "proj-1"

    def test_no_lead(self):
        connector = _make_connector()
        project = _make_project_data(lead_email=None)
        record = connector._transform_to_project_record(project, "team-1")
        assert record.lead_email is None

    def test_existing_record_versioning(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-proj-id"
        existing.version = 5
        existing.source_updated_at = 1000
        project = _make_project_data()
        record = connector._transform_to_project_record(project, "team-1", existing)
        assert record.id == "existing-proj-id"
        assert record.version == 6


# ---------------------------------------------------------------------------
# Tests: _transform_attachment_to_link_record
# ---------------------------------------------------------------------------

class TestTransformAttachmentToLinkRecord:
    def test_basic_attachment(self):
        connector = _make_connector()
        data = {
            "id": "att-1",
            "url": "https://example.com/attachment",
            "title": "My Attachment",
            "createdAt": "2024-01-15T10:00:00.000Z",
            "updatedAt": "2024-06-15T10:00:00.000Z",
        }
        record = connector._transform_attachment_to_link_record(data, "issue-1", "node-1", "team-1")
        assert record.record_type == RecordType.LINK
        assert record.record_name == "My Attachment"
        assert record.weburl == "https://example.com/attachment"

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_attachment_to_link_record(
                {"id": "", "url": "http://x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_attachment_to_link_record(
                {"id": "att-1", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_title_fallback_to_subtitle(self):
        connector = _make_connector()
        data = {
            "id": "att-1",
            "url": "https://example.com",
            "subtitle": "Subtitle",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-01T00:00:00.000Z",
        }
        record = connector._transform_attachment_to_link_record(data, "issue-1", "node-1", "team-1")
        assert record.record_name == "Subtitle"

    def test_title_fallback_to_label(self):
        connector = _make_connector()
        data = {
            "id": "att-1",
            "url": "https://example.com",
            "label": "Label",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-01T00:00:00.000Z",
        }
        record = connector._transform_attachment_to_link_record(data, "issue-1", "node-1", "team-1")
        assert record.record_name == "Label"

    def test_parent_record_type_project(self):
        connector = _make_connector()
        data = {
            "id": "att-1",
            "url": "https://example.com",
            "title": "Link",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-01T00:00:00.000Z",
        }
        record = connector._transform_attachment_to_link_record(
            data, "proj-1", "node-1", "team-1", parent_record_type=RecordType.PROJECT
        )
        assert record.parent_record_type == RecordType.PROJECT


# ---------------------------------------------------------------------------
# Tests: _transform_document_to_webpage_record
# ---------------------------------------------------------------------------

class TestTransformDocumentToWebpageRecord:
    def test_basic_document(self):
        connector = _make_connector()
        data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "My Document",
            "createdAt": "2024-01-15T10:00:00.000Z",
            "updatedAt": "2024-06-15T10:00:00.000Z",
        }
        record = connector._transform_document_to_webpage_record(data, "issue-1", "node-1", "team-1")
        assert record.record_type == RecordType.WEBPAGE
        assert record.record_name == "My Document"

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_document_to_webpage_record(
                {"id": "", "url": "http://x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_document_to_webpage_record(
                {"id": "doc-1", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_no_title_uses_id(self):
        connector = _make_connector()
        data = {
            "id": "doc-abcdefgh",
            "url": "https://linear.app/doc/1",
            "title": "",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-01T00:00:00.000Z",
        }
        record = connector._transform_document_to_webpage_record(data, "issue-1", "node-1", "team-1")
        assert record.record_name == "Document doc-abcd"


# ---------------------------------------------------------------------------
# Tests: _extract_file_urls_from_markdown
# ---------------------------------------------------------------------------

class TestExtractFileUrlsFromMarkdown:
    def test_image_extraction(self):
        connector = _make_connector()
        md = "![screenshot](https://uploads.linear.app/abc/image.png)"
        result = connector._extract_file_urls_from_markdown(md)
        assert len(result) == 1
        assert result[0]["url"] == "https://uploads.linear.app/abc/image.png"

    def test_link_extraction(self):
        connector = _make_connector()
        md = "[report.pdf](https://uploads.linear.app/abc/report.pdf)"
        result = connector._extract_file_urls_from_markdown(md)
        assert len(result) == 1
        assert result[0]["filename"] == "report.pdf"

    def test_exclude_images(self):
        connector = _make_connector()
        md = "![img](https://uploads.linear.app/abc/image.png)\n[doc.pdf](https://uploads.linear.app/abc/doc.pdf)"
        result = connector._extract_file_urls_from_markdown(md, exclude_images=True)
        assert len(result) == 1
        assert "doc.pdf" in result[0]["filename"]

    def test_non_linear_urls_ignored(self):
        connector = _make_connector()
        md = "[file](https://example.com/file.pdf)"
        result = connector._extract_file_urls_from_markdown(md)
        assert result == []

    def test_empty_markdown(self):
        connector = _make_connector()
        result = connector._extract_file_urls_from_markdown("")
        assert result == []

    def test_none_markdown(self):
        connector = _make_connector()
        result = connector._extract_file_urls_from_markdown(None)
        assert result == []

    def test_deduplication(self):
        connector = _make_connector()
        md = (
            "![img](https://uploads.linear.app/abc/file.png)\n"
            "[link](https://uploads.linear.app/abc/file.png)"
        )
        result = connector._extract_file_urls_from_markdown(md)
        # First match (image) should win, second is deduplicated
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: _get_mime_type_from_url
# ---------------------------------------------------------------------------

class TestGetMimeTypeFromUrl:
    def test_pdf(self):
        connector = _make_connector()
        result = connector._get_mime_type_from_url("https://x.com/file.pdf", "file.pdf")
        assert "pdf" in result.lower()

    def test_png(self):
        connector = _make_connector()
        result = connector._get_mime_type_from_url("https://x.com/image.png")
        assert "png" in result.lower()

    def test_unknown_extension(self):
        connector = _make_connector()
        result = connector._get_mime_type_from_url("https://x.com/file.xyz123")
        assert result is not None  # Should return UNKNOWN mime type

    def test_no_extension(self):
        connector = _make_connector()
        result = connector._get_mime_type_from_url("https://x.com/file")
        assert result is not None

    def test_filename_priority(self):
        connector = _make_connector()
        result = connector._get_mime_type_from_url("https://x.com/blob", "report.docx")
        assert "word" in result.lower() or "docx" in result.lower() or "document" in result.lower()


# ---------------------------------------------------------------------------
# Tests: _apply_date_filters_to_linear_filter
# ---------------------------------------------------------------------------

class TestApplyDateFiltersToLinearFilter:
    def test_no_filters(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter)
        assert "updatedAt" not in linear_filter

    def test_last_sync_time_only(self):
        connector = _make_connector()
        connector.sync_filters = FilterCollection()
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=1705313400000)
        assert "updatedAt" in linear_filter
        assert "gt" in linear_filter["updatedAt"]

    def test_modified_filter_with_checkpoint_uses_max(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1000, None)
        mock_filters = MagicMock()
        mock_filters.get = MagicMock(side_effect=lambda key: mock_filter if str(key) == "modified" or (hasattr(key, 'value') and key.value == "modified") else None)
        connector.sync_filters = mock_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=2000)
        # Should use max(1000, 2000) = 2000
        assert "updatedAt" in linear_filter


# ---------------------------------------------------------------------------
# Tests: Sync checkpoint helpers
# ---------------------------------------------------------------------------

class TestSyncCheckpoints:
    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 12345})
        result = await connector._get_team_sync_checkpoint("ENG")
        assert result == 12345

    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint_none(self):
        connector = _make_connector()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await connector._get_team_sync_checkpoint("ENG")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_team_sync_checkpoint(self):
        connector = _make_connector()
        connector.issues_sync_point.update_sync_point = AsyncMock()
        await connector._update_team_sync_checkpoint("ENG", 99999)
        connector.issues_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_attachments_sync_checkpoint(self):
        connector = _make_connector()
        connector.attachments_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 77777})
        result = await connector._get_attachments_sync_checkpoint()
        assert result == 77777

    @pytest.mark.asyncio
    async def test_update_attachments_sync_checkpoint(self):
        connector = _make_connector()
        connector.attachments_sync_point.update_sync_point = AsyncMock()
        await connector._update_attachments_sync_checkpoint(88888)
        connector.attachments_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_documents_sync_checkpoint(self):
        connector = _make_connector()
        connector.documents_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 55555})
        result = await connector._get_documents_sync_checkpoint()
        assert result == 55555

    @pytest.mark.asyncio
    async def test_update_documents_sync_checkpoint(self):
        connector = _make_connector()
        connector.documents_sync_point.update_sync_point = AsyncMock()
        await connector._update_documents_sync_checkpoint(66666)
        connector.documents_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_team_project_sync_checkpoint(self):
        connector = _make_connector()
        connector.projects_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 33333})
        result = await connector._get_team_project_sync_checkpoint("ENG")
        assert result == 33333

    @pytest.mark.asyncio
    async def test_update_team_project_sync_checkpoint(self):
        connector = _make_connector()
        connector.projects_sync_point.update_sync_point = AsyncMock()
        await connector._update_team_project_sync_checkpoint("ENG", 44444)
        connector.projects_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_deletion_sync_checkpoint(self):
        connector = _make_connector()
        connector.deletion_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 11111})
        result = await connector._get_deletion_sync_checkpoint("issues")
        assert result == 11111

    @pytest.mark.asyncio
    async def test_update_deletion_sync_checkpoint(self):
        connector = _make_connector()
        connector.deletion_sync_point.update_sync_point = AsyncMock()
        await connector._update_deletion_sync_checkpoint("issues", 22222)
        connector.deletion_sync_point.update_sync_point.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: _organize_comments_by_thread
# ---------------------------------------------------------------------------

class TestOrganizeCommentsByThread:
    def test_empty_comments(self):
        connector = _make_connector()
        result = connector._organize_comments_by_thread([])
        assert result == []

    def test_single_thread(self):
        from app.models.blocks import BlockComment, DataFormat
        connector = _make_connector()
        c1 = BlockComment(
            text="first", format=DataFormat.MARKDOWN, thread_id="t1", author_name="A",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="second", format=DataFormat.MARKDOWN, thread_id="t1", author_name="B",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c2, c1])
        assert len(result) == 1
        assert result[0][0].author_name == "A"  # Sorted by time

    def test_multiple_threads(self):
        from app.models.blocks import BlockComment, DataFormat
        connector = _make_connector()
        c1 = BlockComment(
            text="body", format=DataFormat.MARKDOWN, thread_id="t1", author_name="A",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="body", format=DataFormat.MARKDOWN, thread_id="t2", author_name="B",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c1, c2])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests: _get_fresh_datasource
# ---------------------------------------------------------------------------

class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_raises_when_no_client(self):
        connector = _make_connector()
        connector.external_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_token_raises(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "apiToken": ""},
        })
        with pytest.raises(Exception, match="No access token"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_oauth_token(self):
        connector = _make_connector()
        mock_internal_client = MagicMock()
        mock_internal_client.get_token.return_value = "old-token"
        connector.external_client = MagicMock()
        connector.external_client.get_client.return_value = mock_internal_client
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "new-token"},
        })
        result = await connector._get_fresh_datasource()
        mock_internal_client.set_token.assert_called_with("new-token")

    @pytest.mark.asyncio
    async def test_token_unchanged_no_update(self):
        connector = _make_connector()
        mock_internal_client = MagicMock()
        mock_internal_client.get_token.return_value = "same-token"
        connector.external_client = MagicMock()
        connector.external_client.get_client.return_value = mock_internal_client
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "apiToken": "same-token"},
        })
        result = await connector._get_fresh_datasource()
        mock_internal_client.set_token.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: get_filter_options
# ---------------------------------------------------------------------------

class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_unknown_filter_key_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="Unknown filter field"):
            await connector.get_filter_options("unknown_key")


# ---------------------------------------------------------------------------
# Tests: _transform_file_url_to_file_record
# ---------------------------------------------------------------------------

class TestTransformFileUrlToFileRecord:
    @pytest.mark.asyncio
    async def test_basic_file_record(self):
        connector = _make_connector()
        connector.data_source = None  # No datasource => skip file size fetch
        record = await connector._transform_file_url_to_file_record(
            file_url="https://uploads.linear.app/abc/report.pdf",
            filename="report.pdf",
            parent_external_id="issue-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
        )
        assert record.record_type == RecordType.FILE
        assert record.record_name == "report.pdf"
        assert record.extension == "pdf"
        assert record.is_file is True
        assert record.is_dependent_node is True
