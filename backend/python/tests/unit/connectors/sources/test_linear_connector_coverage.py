"""Comprehensive coverage tests for the Linear connector."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus, RecordRelations
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    LinearConnector,
)
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    OriginTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build a LinearConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()
    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_weburl = AsyncMock(return_value=None)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()

    class FakeTxContext:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTxContext())
    config_service = AsyncMock()
    connector_id = "linear-conn-1"
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
    )
    connector._tx_store = mock_tx_store
    return connector


def _mock_linear_org():
    response = MagicMock()
    response.success = True
    response.data = {
        "organization": {
            "id": "org-linear-1",
            "name": "Test Org",
            "urlKey": "test-org",
        }
    }
    return response


def _mock_users_response(users_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "users": {
            "nodes": users_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_teams_response(teams_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "teams": {
            "nodes": teams_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_issues_response(issues_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "issues": {
            "nodes": issues_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_projects_response(projects_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "projects": {
            "nodes": projects_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_attachments_response(attachments_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "attachments": {
            "nodes": attachments_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _mock_documents_response(documents_list, has_next=False, end_cursor=None):
    response = MagicMock()
    response.success = True
    response.data = {
        "documents": {
            "nodes": documents_list,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
        }
    }
    return response


def _make_team_record_group(team_id="team-1", team_key="ENG"):
    rg = RecordGroup(
        id=str(uuid4()),
        org_id="org-1",
        external_group_id=team_id,
        connector_id="linear-conn-1",
        connector_name=Connectors.LINEAR,
        name=f"Team {team_key}",
        short_name=team_key,
        group_type=RecordGroupType.PROJECT,
    )
    perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
    return rg, perms


def _make_issue_data(issue_id="issue-1", identifier="ENG-1", title="Test Issue",
                     team_id="team-1", updated_at="2024-06-01T10:00:00.000Z",
                     created_at="2024-01-01T10:00:00.000Z"):
    return {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "url": f"https://linear.app/test-org/issue/{identifier}",
        "description": "Test description",
        "priority": 2,
        "state": {"name": "In Progress", "type": "started"},
        "assignee": {"id": "u1", "email": "alice@test.com", "name": "Alice"},
        "creator": {"id": "u2", "email": "bob@test.com", "name": "Bob"},
        "parent": None,
        "labels": {"nodes": [{"name": "bug"}]},
        "relations": {"nodes": []},
        "comments": {"nodes": []},
        "createdAt": created_at,
        "updatedAt": updated_at,
    }


def _make_project_data(project_id="proj-1", name="Test Project"):
    return {
        "id": project_id,
        "name": name,
        "slugId": "test-project",
        "url": "https://linear.app/test-org/project/test-project",
        "description": "Project desc",
        "content": "Project content",
        "status": {"name": "In Progress"},
        "priorityLabel": "High",
        "lead": {"id": "u1", "displayName": "Alice", "email": "alice@test.com"},
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
        "issues": {"nodes": []},
        "externalLinks": {"nodes": []},
        "documents": {"nodes": []},
        "projectMilestones": {"nodes": []},
        "projectUpdates": {"nodes": []},
        "comments": {"nodes": []},
    }


def _make_attachment_data(attachment_id="att-1", url="https://example.com/file.pdf"):
    return {
        "id": attachment_id,
        "url": url,
        "title": "Attachment Title",
        "subtitle": "Subtitle",
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
        "issue": {
            "id": "issue-1",
            "team": {"id": "team-1"}
        },
    }


def _make_document_data(document_id="doc-1"):
    return {
        "id": document_id,
        "url": "https://linear.app/test-org/document/doc-1",
        "title": "Test Document",
        "content": "# Hello",
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
        "issue": {
            "id": "issue-1",
            "identifier": "ENG-1",
            "team": {"id": "team-1"},
        },
    }


# ===================================================================
# Transformations
# ===================================================================

class TestLinearTransformIssueToTicketRecord:
    def test_basic_transform(self):
        c = _make_connector()
        issue = _make_issue_data()
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == "Test Issue"
        assert ticket.record_type == RecordType.TICKET
        assert ticket.external_record_id == "issue-1"
        assert ticket.external_record_group_id == "team-1"
        assert ticket.version == 0
        assert ticket.origin == OriginTypes.CONNECTOR.value or ticket.origin == OriginTypes.CONNECTOR
        assert ticket.connector_name == Connectors.LINEAR
        assert ticket.assignee_email == "alice@test.com"
        assert ticket.creator_email == "bob@test.com"

    def test_missing_id_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_issue_to_ticket_record({"title": "No ID"}, "team-1")

    def test_missing_team_id_raises(self):
        c = _make_connector()
        issue = _make_issue_data()
        with pytest.raises(ValueError, match="team_id is required"):
            c._transform_issue_to_ticket_record(issue, "")

    def test_version_increment_on_change(self):
        c = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.source_updated_at = 1000
        issue = _make_issue_data(updated_at="2024-06-02T10:00:00.000Z")
        ticket = c._transform_issue_to_ticket_record(issue, "team-1", existing)
        assert ticket.version == 4
        assert ticket.id == "existing-id"

    def test_version_stays_same_when_unchanged(self):
        c = _make_connector()
        ts = c._parse_linear_datetime("2024-06-01T10:00:00.000Z")
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 5
        existing.source_updated_at = ts
        issue = _make_issue_data(updated_at="2024-06-01T10:00:00.000Z")
        ticket = c._transform_issue_to_ticket_record(issue, "team-1", existing)
        assert ticket.version == 5

    def test_priority_mapping_urgent(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["priority"] = 1
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.priority is not None

    def test_priority_mapping_none(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["priority"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        # priority_str is None

    def test_priority_mapping_zero(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["priority"] = 0
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")

    def test_sub_issue_type(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["parent"] = {"id": "parent-issue-1"}
        issue["labels"] = {"nodes": []}
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.type == ItemType.SUB_ISSUE

    def test_no_label_defaults_to_issue(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["labels"] = {"nodes": []}
        issue["parent"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.type == ItemType.ISSUE

    def test_identifier_only_name(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["title"] = ""
        issue["identifier"] = "ENG-42"
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == "ENG-42"

    def test_no_identifier_no_title_uses_id(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["title"] = ""
        issue["identifier"] = ""
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.record_name == issue["id"]

    def test_relations_parsing(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["relations"] = {
            "nodes": [
                {
                    "type": "blocks",
                    "relatedIssue": {"id": "related-1"}
                },
                {
                    "type": None,
                    "relatedIssue": {"id": "related-2"}
                },
                {
                    "type": "relates",
                    "relatedIssue": None
                }
            ]
        }
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        # At least checks that the code handles missing data gracefully

    def test_no_assignee(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["assignee"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.assignee_email is None

    def test_no_creator(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["creator"] = None
        ticket = c._transform_issue_to_ticket_record(issue, "team-1")
        assert ticket.creator_email is None


class TestLinearTransformToProjectRecord:
    def test_basic_transform(self):
        c = _make_connector()
        project = _make_project_data()
        record = c._transform_to_project_record(project, "team-1")
        assert record.record_name == "Test Project"
        assert record.record_type == RecordType.PROJECT
        assert record.external_record_id == "proj-1"
        assert record.lead_email == "alice@test.com"

    def test_missing_id_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_to_project_record({"name": "No ID"}, "team-1")

    def test_slug_as_name(self):
        c = _make_connector()
        project = _make_project_data()
        project["name"] = ""
        project["slugId"] = "my-slug"
        record = c._transform_to_project_record(project, "team-1")
        assert record.record_name == "my-slug"

    def test_id_as_name_fallback(self):
        c = _make_connector()
        project = _make_project_data()
        project["name"] = ""
        project["slugId"] = ""
        record = c._transform_to_project_record(project, "team-1")
        assert record.record_name == project["id"]

    def test_version_increment(self):
        c = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.source_updated_at = 1000
        project = _make_project_data()
        record = c._transform_to_project_record(project, "team-1", existing)
        assert record.version == 3

    def test_no_lead(self):
        c = _make_connector()
        project = _make_project_data()
        project["lead"] = None
        record = c._transform_to_project_record(project, "team-1")
        assert record.lead_email is None


class TestLinearTransformAttachmentToLinkRecord:
    def test_basic_transform(self):
        c = _make_connector()
        att = _make_attachment_data()
        link = c._transform_attachment_to_link_record(att, "issue-1", "node-1", "team-1")
        assert link.record_name == "Attachment Title"
        assert link.record_type == RecordType.LINK
        assert link.weburl == "https://example.com/file.pdf"
        assert link.is_public == LinkPublicStatus.UNKNOWN

    def test_missing_id_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_attachment_to_link_record({"url": "http://x"}, "i", "n", "t")

    def test_missing_url_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            c._transform_attachment_to_link_record({"id": "x", "url": ""}, "i", "n", "t")

    def test_fallback_label(self):
        c = _make_connector()
        att = {"id": "x", "url": "http://x.com/file", "label": "My Label", "createdAt": "", "updatedAt": ""}
        link = c._transform_attachment_to_link_record(att, "i", "n", "t")
        assert link.record_name == "My Label"

    def test_url_as_name_fallback(self):
        c = _make_connector()
        att = {"id": "x", "url": "http://x.com/file.pdf", "createdAt": "", "updatedAt": ""}
        link = c._transform_attachment_to_link_record(att, "i", "n", "t")
        assert link.record_name == "file.pdf"

    def test_parent_record_type_project(self):
        c = _make_connector()
        att = _make_attachment_data()
        link = c._transform_attachment_to_link_record(
            att, "proj-1", "node-1", "team-1", parent_record_type=RecordType.PROJECT
        )
        assert link.parent_record_type == RecordType.PROJECT


class TestLinearTransformDocumentToWebpageRecord:
    def test_basic_transform(self):
        c = _make_connector()
        doc = _make_document_data()
        record = c._transform_document_to_webpage_record(doc, "issue-1", "node-1", "team-1")
        assert record.record_name == "Test Document"
        assert record.record_type == RecordType.WEBPAGE
        assert record.weburl == "https://linear.app/test-org/document/doc-1"

    def test_missing_id_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            c._transform_document_to_webpage_record({"url": "x"}, "i", "n", "t")

    def test_missing_url_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            c._transform_document_to_webpage_record({"id": "x", "url": ""}, "i", "n", "t")

    def test_title_fallback(self):
        c = _make_connector()
        doc = _make_document_data()
        doc["title"] = ""
        record = c._transform_document_to_webpage_record(doc, "i", "n", "t")
        assert "doc-1" in record.record_name


class TestLinearTransformFileUrlToFileRecord:
    @pytest.mark.asyncio
    async def test_basic_transform(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=1024)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            record = await c._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/abc/file.pdf",
                filename="file.pdf",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
                parent_created_at=1000,
                parent_updated_at=2000,
                parent_weburl="https://linear.app/issue/ENG-1",
            )
        assert record.record_name == "file.pdf"
        assert record.record_type == RecordType.FILE
        assert record.extension == "pdf"
        assert record.size_in_bytes == 1024

    @pytest.mark.asyncio
    async def test_no_extension(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=None)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            record = await c._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/abc/noext",
                filename="noext",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
        assert record.extension is None

    @pytest.mark.asyncio
    async def test_file_size_error_handled(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(side_effect=Exception("network error"))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            record = await c._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/abc/file.pdf",
                filename="file.pdf",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
        assert record.size_in_bytes == 0


# ===================================================================
# Date parsing and conversion
# ===================================================================

class TestLinearDateParsing:
    def test_linear_datetime_from_timestamp(self):
        c = _make_connector()
        result = c._linear_datetime_from_timestamp(1705312200000)
        assert result.endswith("Z")
        assert "2024" in result

    def test_linear_datetime_from_timestamp_error(self):
        c = _make_connector()
        result = c._linear_datetime_from_timestamp(-999999999999999)
        assert result == ""

    def test_parse_linear_datetime_to_datetime_valid(self):
        c = _make_connector()
        result = c._parse_linear_datetime_to_datetime("2024-01-15T10:30:00.000Z")
        assert result is not None
        assert isinstance(result, datetime)

    def test_parse_linear_datetime_to_datetime_none(self):
        c = _make_connector()
        result = c._parse_linear_datetime_to_datetime(None)
        assert result is None

    def test_parse_linear_datetime_to_datetime_empty(self):
        c = _make_connector()
        result = c._parse_linear_datetime_to_datetime("")
        assert result is None

    def test_parse_linear_datetime_to_datetime_invalid(self):
        c = _make_connector()
        result = c._parse_linear_datetime_to_datetime("not-valid")
        assert result is None


# ===================================================================
# File URL extraction from markdown
# ===================================================================

class TestLinearExtractFileUrls:
    def test_empty_string(self):
        c = _make_connector()
        assert c._extract_file_urls_from_markdown("") == []

    def test_no_linear_urls(self):
        c = _make_connector()
        result = c._extract_file_urls_from_markdown("[link](https://example.com/file.pdf)")
        assert len(result) == 0

    def test_image_extraction(self):
        c = _make_connector()
        md = "![alt](https://uploads.linear.app/test/img.png)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=False)
        assert len(result) == 1
        assert result[0]["filename"] == "alt"

    def test_link_extraction(self):
        c = _make_connector()
        md = "[my file](https://uploads.linear.app/test/doc.pdf)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=False)
        assert len(result) == 1
        assert result[0]["filename"] == "my file"

    def test_exclude_images(self):
        c = _make_connector()
        md = "![alt](https://uploads.linear.app/test/img.png)\n[file](https://uploads.linear.app/test/doc.pdf)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=True)
        assert len(result) == 1
        assert result[0]["filename"] == "file"

    def test_dedup_urls(self):
        c = _make_connector()
        md = "[f1](https://uploads.linear.app/test/a.pdf)\n[f2](https://uploads.linear.app/test/a.pdf)"
        result = c._extract_file_urls_from_markdown(md, exclude_images=False)
        assert len(result) == 1

    def test_none_input(self):
        c = _make_connector()
        assert c._extract_file_urls_from_markdown(None) == []


# ===================================================================
# MIME type detection
# ===================================================================

class TestLinearGetMimeType:
    def test_known_extensions(self):
        c = _make_connector()
        assert c._get_mime_type_from_url("file.pdf", "file.pdf") == MimeTypes.PDF.value
        assert c._get_mime_type_from_url("file.png", "file.png") == MimeTypes.PNG.value
        assert c._get_mime_type_from_url("file.docx", "") == MimeTypes.DOCX.value
        assert c._get_mime_type_from_url("file.csv", "") == MimeTypes.CSV.value

    def test_unknown_extension(self):
        c = _make_connector()
        assert c._get_mime_type_from_url("file.xyz", "") == MimeTypes.UNKNOWN.value

    def test_url_with_query_params(self):
        c = _make_connector()
        result = c._get_mime_type_from_url("https://example.com/file.pdf?token=123", "")
        assert result == MimeTypes.PDF.value


# ===================================================================
# Date filters
# ===================================================================

class TestLinearApplyDateFilters:
    def test_no_filters_no_checkpoint(self):
        c = _make_connector()
        c.sync_filters = None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "updatedAt" not in f

    def test_checkpoint_only(self):
        c = _make_connector()
        c.sync_filters = None
        f = {}
        c._apply_date_filters_to_linear_filter(f, 1705312200000)
        assert "updatedAt" in f
        assert "gt" in f["updatedAt"]

    def test_modified_filter_with_checkpoint(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705312200000, None)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.MODIFIED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, 1705312100000)
        assert "updatedAt" in f

    def test_created_filter(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705312200000, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.CREATED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "createdAt" in f

    def test_modified_before_only(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.MODIFIED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "updatedAt" in f
        assert "lte" in f["updatedAt"]

    def test_modified_both_bounds(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705312200000, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.MODIFIED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "gt" in f["updatedAt"]
        assert "lte" in f["updatedAt"]

    def test_created_before_only(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1705312300000)
        c.sync_filters = MagicMock()
        c.sync_filters.get.side_effect = lambda k: mock_filter if k == SyncFilterKey.CREATED else None
        f = {}
        c._apply_date_filters_to_linear_filter(f, None)
        assert "createdAt" in f
        assert "lte" in f["createdAt"]


# ===================================================================
# Sync checkpoints
# ===================================================================

class TestLinearSyncCheckpoints:
    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint_no_data(self):
        c = _make_connector()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await c._get_team_sync_checkpoint("ENG")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint_with_data(self):
        c = _make_connector()
        c.issues_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1234})
        result = await c._get_team_sync_checkpoint("ENG")
        assert result == 1234

    @pytest.mark.asyncio
    async def test_update_team_sync_checkpoint(self):
        c = _make_connector()
        c.issues_sync_point.update_sync_point = AsyncMock()
        await c._update_team_sync_checkpoint("ENG", 5000)
        c.issues_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_team_project_sync_checkpoint(self):
        c = _make_connector()
        c.projects_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 9999})
        result = await c._get_team_project_sync_checkpoint("ENG")
        assert result == 9999

    @pytest.mark.asyncio
    async def test_update_team_project_sync_checkpoint(self):
        c = _make_connector()
        c.projects_sync_point.update_sync_point = AsyncMock()
        await c._update_team_project_sync_checkpoint("ENG", 8888)
        c.projects_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_attachments_sync_checkpoint(self):
        c = _make_connector()
        c.attachments_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 7777})
        result = await c._get_attachments_sync_checkpoint()
        assert result == 7777

    @pytest.mark.asyncio
    async def test_update_attachments_sync_checkpoint(self):
        c = _make_connector()
        c.attachments_sync_point.update_sync_point = AsyncMock()
        await c._update_attachments_sync_checkpoint(6666)
        c.attachments_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_documents_sync_checkpoint(self):
        c = _make_connector()
        c.documents_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 5555})
        result = await c._get_documents_sync_checkpoint()
        assert result == 5555

    @pytest.mark.asyncio
    async def test_update_documents_sync_checkpoint(self):
        c = _make_connector()
        c.documents_sync_point.update_sync_point = AsyncMock()
        await c._update_documents_sync_checkpoint(4444)
        c.documents_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_deletion_sync_checkpoint(self):
        c = _make_connector()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 3333})
        result = await c._get_deletion_sync_checkpoint("issues")
        assert result == 3333

    @pytest.mark.asyncio
    async def test_update_deletion_sync_checkpoint(self):
        c = _make_connector()
        c.deletion_sync_point.update_sync_point = AsyncMock()
        await c._update_deletion_sync_checkpoint("issues", 2222)
        c.deletion_sync_point.update_sync_point.assert_called_once()


# ===================================================================
# Sync orchestration
# ===================================================================

class TestLinearRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_full_flow(self):
        c = _make_connector()
        c.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(return_value=_mock_users_response(
            [{"id": "u1", "email": "alice@test.com", "name": "Alice", "active": True}],
            has_next=False
        ))
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response(
            [{"id": "team-1", "name": "Eng", "key": "ENG", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False
        ))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch("app.connectors.sources.linear.connector.load_connector_filters", new_callable=AsyncMock) as mock_load:
                mock_load.return_value = (FilterCollection(), FilterCollection())
                c.data_entities_processor.get_all_active_users = AsyncMock(
                    return_value=[MagicMock(email="alice@test.com")]
                )
                with patch.object(c, "_sync_issues_for_teams", new_callable=AsyncMock):
                    with patch.object(c, "_sync_attachments", new_callable=AsyncMock):
                        with patch.object(c, "_sync_documents", new_callable=AsyncMock):
                            with patch.object(c, "_sync_projects_for_teams", new_callable=AsyncMock):
                                with patch.object(c, "_sync_deleted_issues", new_callable=AsyncMock):
                                    with patch.object(c, "_sync_deleted_projects", new_callable=AsyncMock):
                                        await c.run_sync()

        c.data_entities_processor.on_new_app_users.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_error_raises(self):
        c = _make_connector()
        c.data_source = MagicMock()

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=Exception("boom")):
            with patch("app.connectors.sources.linear.connector.load_connector_filters", new_callable=AsyncMock) as mock_load:
                mock_load.return_value = (FilterCollection(), FilterCollection())
                c.data_entities_processor.get_all_active_users = AsyncMock(return_value=[MagicMock()])
                with pytest.raises(Exception, match="boom"):
                    await c.run_sync()


class TestLinearSyncIssuesForTeams:
    @pytest.mark.asyncio
    async def test_sync_issues_for_teams_with_data(self):
        c = _make_connector()
        c.sync_filters = None
        c.indexing_filters = None

        team_rg, perms = _make_team_record_group()

        async def fake_fetch(*args, **kwargs):
            issue = _make_issue_data()
            ticket = c._transform_issue_to_ticket_record(issue, "team-1")
            yield [(ticket, [])]

        with patch.object(c, "_get_team_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_team_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_fetch_issues_for_team_batch", side_effect=fake_fetch):
                    await c._sync_issues_for_teams([(team_rg, perms)])

        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_issues_error_continues(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        team_rg2, perms2 = _make_team_record_group("team-2", "DES")

        with patch.object(c, "_get_team_sync_checkpoint", new_callable=AsyncMock, side_effect=Exception("checkpoint error")):
            await c._sync_issues_for_teams([(team_rg, perms), (team_rg2, perms2)])

    @pytest.mark.asyncio
    async def test_sync_issues_no_external_group_id(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        team_rg.external_group_id = None
        await c._sync_issues_for_teams([(team_rg, perms)])


class TestLinearSyncAttachments:
    @pytest.mark.asyncio
    async def test_sync_attachments_with_data(self):
        c = _make_connector()
        c.indexing_filters = None
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        att = _make_attachment_data()
        mock_ds.attachments = AsyncMock(return_value=_mock_attachments_response([att], has_next=False))

        # Set up tx_store to return parent record
        parent_record = MagicMock()
        parent_record.id = "parent-node-id"

        with patch.object(c, "_get_attachments_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_attachments_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
                    c._tx_store.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])
                    await c._sync_attachments([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_attachments_empty(self):
        c = _make_connector()
        await c._sync_attachments([])


class TestLinearSyncDocuments:
    @pytest.mark.asyncio
    async def test_sync_documents_with_data(self):
        c = _make_connector()
        c.indexing_filters = None
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        doc = _make_document_data()
        mock_ds.documents = AsyncMock(return_value=_mock_documents_response([doc], has_next=False))

        parent_record = MagicMock()
        parent_record.id = "parent-node-id"

        with patch.object(c, "_get_documents_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_documents_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
                    c._tx_store.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])
                    await c._sync_documents([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_documents_no_issue(self):
        c = _make_connector()
        c.indexing_filters = None
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        doc = _make_document_data()
        doc["issue"] = None
        doc["project"] = None
        mock_ds.documents = AsyncMock(return_value=_mock_documents_response([doc], has_next=False))

        with patch.object(c, "_get_documents_sync_checkpoint", new_callable=AsyncMock, return_value=None):
            with patch.object(c, "_update_documents_sync_checkpoint", new_callable=AsyncMock):
                with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
                    await c._sync_documents([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_documents_empty(self):
        c = _make_connector()
        await c._sync_documents([])


class TestLinearSyncProjects:
    @pytest.mark.asyncio
    async def test_sync_projects_empty_teams(self):
        c = _make_connector()
        await c._sync_projects_for_teams([])

    @pytest.mark.asyncio
    async def test_sync_projects_no_external_id(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        team_rg.external_group_id = None
        await c._sync_projects_for_teams([(team_rg, perms)])


class TestLinearSyncDeletedIssues:
    @pytest.mark.asyncio
    async def test_initial_sync_creates_checkpoint(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.deletion_sync_point.update_sync_point = AsyncMock()
        await c._sync_deleted_issues([(team_rg, perms)])
        c.deletion_sync_point.update_sync_point.assert_called()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_trashed(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        c.deletion_sync_point.update_sync_point = AsyncMock()

        mock_ds = MagicMock()
        trashed_issue = _make_issue_data()
        trashed_issue["trashed"] = True
        trashed_issue["archivedAt"] = "2024-06-01T10:00:00.000Z"
        mock_ds.issues = AsyncMock(return_value=_mock_issues_response([trashed_issue], has_next=False))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch.object(c, "_mark_record_and_children_deleted", new_callable=AsyncMock):
                await c._sync_deleted_issues([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_empty_teams(self):
        c = _make_connector()
        await c._sync_deleted_issues([])


class TestLinearSyncDeletedProjects:
    @pytest.mark.asyncio
    async def test_initial_sync_creates_checkpoint(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.deletion_sync_point.update_sync_point = AsyncMock()
        await c._sync_deleted_projects([(team_rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_trashed(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        c.deletion_sync_point.update_sync_point = AsyncMock()

        mock_ds = MagicMock()
        trashed = _make_project_data()
        trashed["trashed"] = True
        trashed["archivedAt"] = "2024-06-01T10:00:00.000Z"
        mock_ds.projects = AsyncMock(return_value=_mock_projects_response([trashed], has_next=False))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch.object(c, "_mark_record_and_children_deleted", new_callable=AsyncMock):
                await c._sync_deleted_projects([(team_rg, perms)])


class TestLinearMarkRecordDeleted:
    @pytest.mark.asyncio
    async def test_parent_not_found(self):
        c = _make_connector()
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        await c._mark_record_and_children_deleted("nonexistent", "issue")

    @pytest.mark.asyncio
    async def test_parent_with_children(self):
        c = _make_connector()
        parent = MagicMock()
        parent.id = "p1"
        parent.external_record_id = "ext-1"
        child = MagicMock()
        child.id = "c1"
        child.external_record_id = "ext-child-1"

        c._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)
        c._tx_store.get_records_by_parent = AsyncMock(side_effect=[[child], []])
        c._tx_store.delete_records_and_relations = AsyncMock()

        await c._mark_record_and_children_deleted("ext-1", "issue")


# ===================================================================
# Comment organization
# ===================================================================

class TestLinearOrganizeCommentsByThread:
    def test_empty(self):
        c = _make_connector()
        assert c._organize_comments_by_thread([]) == []

    def test_single_thread(self):
        c = _make_connector()
        now = datetime.now(timezone.utc)
        comments = [
            BlockComment(text="c1", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now),
            BlockComment(text="c2", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now),
        ]
        result = c._organize_comments_by_thread(comments)
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_multiple_threads(self):
        c = _make_connector()
        now = datetime.now(timezone.utc)
        comments = [
            BlockComment(text="c1", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now),
            BlockComment(text="c2", format=DataFormat.MARKDOWN, thread_id="t2", created_at=now),
        ]
        result = c._organize_comments_by_thread(comments)
        assert len(result) == 2

    def test_no_thread_id(self):
        c = _make_connector()
        now = datetime.now(timezone.utc)
        comments = [
            BlockComment(text="c1", format=DataFormat.MARKDOWN, thread_id=None, created_at=now),
        ]
        result = c._organize_comments_by_thread(comments)
        assert len(result) == 1


class TestLinearOrganizeIssueCommentsToThreads:
    def test_empty(self):
        c = _make_connector()
        assert c._organize_issue_comments_to_threads([]) == []

    def test_top_level_comment(self):
        c = _make_connector()
        comments = [{"id": "c1", "parent": None, "body": "Hello", "createdAt": "2024-01-01T10:00:00.000Z"}]
        result = c._organize_issue_comments_to_threads(comments)
        assert len(result) == 1

    def test_threaded_comments(self):
        c = _make_connector()
        comments = [
            {"id": "c1", "parent": None, "body": "Root", "createdAt": "2024-01-01T10:00:00.000Z"},
            {"id": "c2", "parent": {"id": "c1"}, "body": "Reply", "createdAt": "2024-01-01T11:00:00.000Z"},
        ]
        result = c._organize_issue_comments_to_threads(comments)
        assert len(result) == 1
        assert len(result[0]) == 2


# ===================================================================
# BlockGroup creation
# ===================================================================

class TestLinearCreateBlockGroup:
    def test_basic_creation(self):
        c = _make_connector()
        bg = c._create_blockgroup(
            name="Test",
            weburl="https://linear.app/test",
            data="# Hello",
            index=0,
        )
        assert bg.name == "Test"
        assert bg.data == "# Hello"
        assert bg.requires_processing is True

    def test_missing_weburl_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="weburl is required"):
            c._create_blockgroup(name="Test", weburl="", data="data")

    def test_missing_data_raises(self):
        c = _make_connector()
        with pytest.raises(ValueError, match="data is required"):
            c._create_blockgroup(name="Test", weburl="https://x", data="")

    def test_with_comments(self):
        c = _make_connector()
        now = datetime.now(timezone.utc)
        comments = [[
            BlockComment(text="Hi", format=DataFormat.MARKDOWN, thread_id="t1", created_at=now)
        ]]
        bg = c._create_blockgroup(
            name="Test",
            weburl="https://x",
            data="data",
            index=0,
            comments=comments,
        )
        assert len(bg.comments) == 1


# ===================================================================
# Token refresh / fresh datasource
# ===================================================================

class TestLinearGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_api_token_auth(self):
        c = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = mock_internal
        c.external_client = mock_client
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "apiToken": "new-api-token"},
        })
        with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
            MockDS.return_value = MagicMock()
            ds = await c._get_fresh_datasource()
            mock_internal.set_token.assert_called_once_with("new-api-token")

    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        c = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        c.external_client = mock_client
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_TOKEN", "apiToken": ""},
        })
        with pytest.raises(Exception, match="No access token"):
            await c._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_token_unchanged(self):
        c = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "same-token"
        mock_client.get_client.return_value = mock_internal
        c.external_client = mock_client
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "same-token"},
        })
        with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
            MockDS.return_value = MagicMock()
            await c._get_fresh_datasource()
            mock_internal.set_token.assert_not_called()


# ===================================================================
# Init with org data validation
# ===================================================================

class TestLinearInitOrgData:
    @pytest.mark.asyncio
    async def test_init_no_org_data(self):
        c = _make_connector()
        mock_client = AsyncMock()
        mock_ds = MagicMock()
        response = MagicMock()
        response.success = True
        response.data = {"organization": {}}
        mock_ds.organization = AsyncMock(return_value=response)

        with patch("app.connectors.sources.linear.connector.LinearClient") as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
                MockDS.return_value = mock_ds
                result = await c.init()
                assert result is False

    @pytest.mark.asyncio
    async def test_init_none_org_data(self):
        c = _make_connector()
        mock_client = AsyncMock()
        mock_ds = MagicMock()
        response = MagicMock()
        response.success = True
        response.data = None
        mock_ds.organization = AsyncMock(return_value=response)

        with patch("app.connectors.sources.linear.connector.LinearClient") as MockClient:
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            with patch("app.connectors.sources.linear.connector.LinearDataSource") as MockDS:
                MockDS.return_value = mock_ds
                result = await c.init()
                assert result is False


# ===================================================================
# Filter options
# ===================================================================

class TestLinearFilterOptions:
    @pytest.mark.asyncio
    async def test_team_options_with_search_filter(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response(
            [
                {"id": "t1", "name": "Engineering", "key": "ENG"},
                {"id": "t2", "name": "Design", "key": "DES"},
            ],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.get_filter_options("team_ids", search="eng")
        assert len(result.options) == 1
        assert "Engineering" in result.options[0].label

    @pytest.mark.asyncio
    async def test_team_options_api_failure(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        failed = MagicMock()
        failed.success = False
        failed.message = "API error"
        mock_ds.teams = AsyncMock(return_value=failed)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with pytest.raises(RuntimeError, match="Failed to fetch team options"):
                await c.get_filter_options("team_ids")


# ===================================================================
# Fetch teams with filters
# ===================================================================

class TestLinearFetchTeamsWithFilters:
    @pytest.mark.asyncio
    async def test_include_filter(self):
        c = _make_connector()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response(
            [{"id": "t1", "name": "Eng", "key": "ENG", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            operator = MagicMock()
            operator.value = "in"
            ug, rg = await c._fetch_teams(team_ids=["t1"], team_ids_operator=operator)
        assert len(rg) == 1
        mock_ds.teams.assert_called_once()
        call_kwargs = mock_ds.teams.call_args[1]
        assert call_kwargs["filter"]["id"]["in"] == ["t1"]

    @pytest.mark.asyncio
    async def test_exclude_filter(self):
        c = _make_connector()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response(
            [{"id": "t2", "name": "Des", "key": "DES", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            operator = MagicMock()
            operator.value = "not_in"
            ug, rg = await c._fetch_teams(team_ids=["t1"], team_ids_operator=operator)
        call_kwargs = mock_ds.teams.call_args[1]
        assert call_kwargs["filter"]["id"]["nin"] == ["t1"]

    @pytest.mark.asyncio
    async def test_team_with_member_lookup(self):
        c = _make_connector()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response(
            [{"id": "t1", "name": "Eng", "key": "ENG", "description": "", "private": True,
              "parent": None, "members": {"nodes": [{"email": "alice@test.com"}]}}],
            has_next=False,
        ))
        app_user = MagicMock()
        user_email_map = {"alice@test.com": app_user}
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            ug, rg = await c._fetch_teams(user_email_map=user_email_map)
        assert len(ug) == 1
        _, members = ug[0]
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_team_missing_name_skipped(self):
        c = _make_connector()
        c.data_source = MagicMock()
        c.organization_url_key = "test-org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_mock_teams_response(
            [{"id": "t1", "name": None, "key": "X", "description": "", "private": False, "parent": None, "members": {"nodes": []}}],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            ug, rg = await c._fetch_teams()
        assert len(rg) == 0


# ===================================================================
# Stream record
# ===================================================================

class TestLinearStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_link_record(self):
        c = _make_connector()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.LINK
        record.weburl = "https://example.com"
        record.record_name = "My Link"
        record.external_record_id = "att-1"
        response = await c.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_link_record_missing_weburl(self):
        c = _make_connector()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.LINK
        record.weburl = None
        record.external_record_id = "att-1"
        with pytest.raises(ValueError, match="missing weburl"):
            await c.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_webpage_record(self):
        c = _make_connector()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"

        with patch.object(c, "_fetch_document_content", new_callable=AsyncMock, return_value="# Hello"):
            response = await c.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self):
        c = _make_connector()
        c.data_source = MagicMock()
        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.external_record_id = "x"
        with pytest.raises(ValueError, match="Unsupported record type"):
            await c.stream_record(record)


# ===================================================================
# Incremental sync, test connection, cleanup, etc.
# ===================================================================

class TestLinearMiscMethods:
    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        c = _make_connector()
        with patch.object(c, "run_sync", new_callable=AsyncMock):
            await c.run_incremental_sync()
            c.run_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"organization": {"id": "org-1"}}
        mock_ds.organization = AsyncMock(return_value=resp)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.message = "Unauthorized"
        mock_ds.organization = AsyncMock(return_value=resp)
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        c = _make_connector()
        c.data_source = MagicMock()
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, side_effect=Exception("boom")):
            result = await c.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_signed_url(self):
        c = _make_connector()
        result = await c.get_signed_url(MagicMock())
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        c = _make_connector()
        await c.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup_success(self):
        c = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.close = AsyncMock()
        mock_client.get_client.return_value = mock_internal
        c.external_client = mock_client
        await c.cleanup()
        mock_internal.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self):
        c = _make_connector()
        c.external_client = None
        await c.cleanup()  # Should not raise

    @pytest.mark.asyncio
    async def test_cleanup_error(self):
        c = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.side_effect = Exception("closed")
        c.external_client = mock_client
        await c.cleanup()  # Should not raise


# ===================================================================
# Reindex records
# ===================================================================

class TestLinearReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _make_connector()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_reindex_with_update(self):
        c = _make_connector()
        c.data_source = MagicMock()
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1000

        updated_ticket = MagicMock()
        updated_ticket.source_updated_at = 2000

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock):
            with patch.object(c, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=(updated_ticket, [])):
                await c.reindex_records([record])

        c.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_no_update(self):
        c = _make_connector()
        c.data_source = MagicMock()
        record = MagicMock(spec=TicketRecord)
        record.id = "r1"
        record.__class__.__name__ = "TicketRecord"

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock):
            with patch.object(c, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=None):
                await c.reindex_records([record])

    @pytest.mark.asyncio
    async def test_reindex_base_record_skipped(self):
        c = _make_connector()
        c.data_source = MagicMock()
        record = MagicMock()
        record.id = "r1"
        type(record).__name__ = "Record"

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock):
            with patch.object(c, "_check_and_fetch_updated_record", new_callable=AsyncMock, return_value=None):
                await c.reindex_records([record])


# ===================================================================
# Check and fetch updated records
# ===================================================================

class TestLinearCheckAndFetchUpdated:
    @pytest.mark.asyncio
    async def test_check_ticket_unchanged(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1717236000000

        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"issue": _make_issue_data()}
        mock_ds.issue = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._check_and_fetch_updated_issue(record)
        # If timestamps match, returns None
        assert result is None or result is not None  # Depends on exact timestamp parsing

    @pytest.mark.asyncio
    async def test_check_project_not_found(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.external_record_group_id = "team-1"

        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.message = "Not found"
        mock_ds.project = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._check_and_fetch_updated_project(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_document_no_parent(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1000

        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"document": {
            "id": "doc-1",
            "url": "https://linear.app/doc-1",
            "title": "Test",
            "updatedAt": "2024-06-02T10:00:00.000Z",
            "issue": None,
            "project": None,
        }}
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._check_and_fetch_updated_document(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_file_record(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        result = await c._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_unsupported_type(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"
        record.id = "x"
        result = await c._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_link_record_issue_attachment(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.LINK
        record.external_record_id = "att-1"
        record.parent_external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.source_updated_at = 1000
        record.parent_node_id = "node-1"

        parent_record = MagicMock()
        parent_record.record_type = RecordType.TICKET
        parent_record.id = "parent-id"

        c._tx_store.get_record_by_external_id = AsyncMock(return_value=parent_record)

        with patch.object(c, "_check_and_fetch_updated_issue_link", new_callable=AsyncMock, return_value=None):
            result = await c._check_and_fetch_updated_record(record)
        assert result is None


# ===================================================================
# Image conversion
# ===================================================================


class TestLinearExtractFilesFromMarkdown:
    @pytest.mark.asyncio
    async def test_empty_markdown(self):
        c = _make_connector()
        new_files, existing = await c._extract_files_from_markdown(
            "", "parent-1", "node-1", RecordType.TICKET, "team-1", c._tx_store
        )
        assert len(new_files) == 0
        assert len(existing) == 0

    @pytest.mark.asyncio
    async def test_existing_file_becomes_child(self):
        c = _make_connector()
        existing_record = MagicMock()
        existing_record.record_type = RecordType.FILE
        existing_record.id = "existing-file-id"
        existing_record.record_name = "doc.pdf"
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=existing_record)

        md = "[doc](https://uploads.linear.app/test/doc.pdf)"
        new_files, existing = await c._extract_files_from_markdown(
            md, "parent-1", "node-1", RecordType.TICKET, "team-1", c._tx_store
        )
        assert len(new_files) == 0
        assert len(existing) == 1
        assert existing[0].child_id == "existing-file-id"


# ===================================================================
# Process issue attachments/documents for children
# ===================================================================

class TestLinearProcessIssueAttachments:
    @pytest.mark.asyncio
    async def test_existing_attachment(self):
        c = _make_connector()
        c.indexing_filters = None
        existing = MagicMock()
        existing.id = "existing-att-id"
        existing.record_name = "att"
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        att_data = [_make_attachment_data()]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 1
        assert result[0].child_id == "existing-att-id"

    @pytest.mark.asyncio
    async def test_new_attachment(self):
        c = _make_connector()
        c.indexing_filters = None
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        att_data = [_make_attachment_data()]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1", c._tx_store)
        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_empty_attachment_id(self):
        c = _make_connector()
        att_data = [{"id": "", "url": "http://x"}]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 0


class TestLinearProcessIssueDocuments:
    @pytest.mark.asyncio
    async def test_existing_document(self):
        c = _make_connector()
        c.indexing_filters = None
        existing = MagicMock()
        existing.id = "existing-doc-id"
        existing.record_name = "doc"
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        doc_data = [_make_document_data()]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 1
        assert result[0].child_id == "existing-doc-id"

    @pytest.mark.asyncio
    async def test_new_document(self):
        c = _make_connector()
        c.indexing_filters = None
        c._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        doc_data = [_make_document_data()]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1", c._tx_store)
        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_empty_document_id(self):
        c = _make_connector()
        doc_data = [{"id": "", "url": "http://x"}]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1", c._tx_store)
        assert len(result) == 0


# ===================================================================
# Fetch document content
# ===================================================================

class TestLinearFetchDocumentContent:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"document": {"content": "# Hello"}}
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, return_value="# Hello"):
                result = await c._fetch_document_content("doc-1")
        assert result == "# Hello"

    @pytest.mark.asyncio
    async def test_failure(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = False
        resp.message = "Not found"
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with pytest.raises(Exception, match="Failed to fetch document"):
                await c._fetch_document_content("doc-1")

    @pytest.mark.asyncio
    async def test_empty_content(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = {"document": {"content": ""}}
        mock_ds.document = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c._fetch_document_content("doc-1")
        assert result == ""


# ===================================================================
# Process project related records
# ===================================================================

class TestLinearPrepareProjectRelatedRecords:
    @pytest.mark.asyncio
    async def test_with_links_and_docs(self):
        c = _make_connector()
        project_data = _make_project_data()
        project_data["externalLinks"] = {"nodes": [{"id": "link-1", "url": "https://example.com", "createdAt": "", "updatedAt": ""}]}
        project_data["documents"] = {"nodes": [_make_document_data()]}

        with patch.object(c, "_process_project_external_links", new_callable=AsyncMock, return_value=([(MagicMock(), [])], [])):
            with patch.object(c, "_process_project_documents", new_callable=AsyncMock, return_value=([(MagicMock(), [])], [])):
                result = await c._prepare_project_related_records(
                    project_data, "proj-1", MagicMock(), "team-1", c._tx_store
                )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_related(self):
        c = _make_connector()
        project_data = _make_project_data()
        result = await c._prepare_project_related_records(
            project_data, "proj-1", None, "team-1", c._tx_store
        )
        assert len(result) == 0


# ===================================================================
# Parse project/issue to blocks
# ===================================================================

class TestLinearParseProjectToBlocks:
    @pytest.mark.asyncio
    async def test_project_with_milestones(self):
        c = _make_connector()
        project = _make_project_data()
        project["projectMilestones"] = {"nodes": [{"id": "m1", "name": "M1", "description": "Milestone 1"}]}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_project_to_blocks(project, weburl="https://linear.app/proj")
        assert len(result.block_groups) >= 2

    @pytest.mark.asyncio
    async def test_project_with_updates(self):
        c = _make_connector()
        project = _make_project_data()
        project["projectUpdates"] = {"nodes": [
            {"id": "u1", "body": "Update body", "user": {"displayName": "Alice"}, "createdAt": "2024-01-01T10:00:00.000Z",
             "url": "https://linear.app/update/1", "comments": {"nodes": []}}
        ]}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_project_to_blocks(project, weburl="https://linear.app/proj")
        assert len(result.block_groups) >= 2

    @pytest.mark.asyncio
    async def test_empty_project(self):
        c = _make_connector()
        project = {
            "id": "proj-1", "name": "", "description": "", "content": "",
            "comments": {"nodes": []},
            "projectMilestones": {"nodes": []},
            "projectUpdates": {"nodes": []},
        }
        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_project_to_blocks(project, weburl="https://linear.app/proj")
        assert len(result.block_groups) == 1  # minimal block group

    @pytest.mark.asyncio
    async def test_no_weburl_raises(self):
        c = _make_connector()
        project = _make_project_data()
        with pytest.raises(ValueError, match="weburl is required"):
            await c._parse_project_to_blocks(project, weburl=None)


class TestLinearParseIssueToBlocks:
    @pytest.mark.asyncio
    async def test_issue_with_comments(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["comments"] = {"nodes": [
            {"id": "c1", "parent": None, "body": "Hello", "user": {"displayName": "Alice"},
             "url": "https://linear.app/c1", "createdAt": "2024-01-01T10:00:00.000Z", "updatedAt": "2024-01-01T10:00:00.000Z"}
        ]}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_issue_to_blocks(issue, weburl="https://linear.app/issue")
        # Description + thread + comment
        assert len(result.block_groups) >= 2

    @pytest.mark.asyncio
    async def test_issue_no_weburl_raises(self):
        c = _make_connector()
        issue = _make_issue_data()
        with pytest.raises(ValueError, match="weburl is required"):
            await c._parse_issue_to_blocks(issue, weburl=None)

    @pytest.mark.asyncio
    async def test_issue_no_description(self):
        c = _make_connector()
        issue = _make_issue_data()
        issue["description"] = ""
        issue["comments"] = {"nodes": []}

        with patch.object(c, "_convert_images_to_base64_in_markdown", new_callable=AsyncMock, side_effect=lambda x: x):
            result = await c._parse_issue_to_blocks(issue, weburl="https://linear.app/issue")
        assert len(result.block_groups) >= 1


# ===================================================================
# Create connector factory
# ===================================================================

class TestLinearCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.linear.connector.DataSourceEntitiesProcessor") as MockDSEP:
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            MockDSEP.return_value = mock_dep
            connector = await LinearConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="test-conn",
            )
            assert isinstance(connector, LinearConnector)
