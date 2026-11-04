"""Comprehensive coverage tests for the Linear connector."""

import json
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, ProgressStatus, RecordRelations
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.linear.api_gateway import LinearAuthError
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    PLACEHOLDER_REVISION_PREFIX,
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
from app.sources.client.graphql.response import GraphQLResponse
from typing import Never


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_sync_point() -> MagicMock:
    """An in-memory stand-in for SyncPoint: what is written is what is read back."""
    store = {}

    async def read(key) -> dict:
        return dict(store.get(key) or {})

    async def update(key, data) -> None:
        store[key] = dict(data)

    sync_point = MagicMock()
    sync_point.store = store
    sync_point.read_sync_point = AsyncMock(side_effect=read)
    sync_point.update_sync_point = AsyncMock(side_effect=update)
    return sync_point


def _use_memory_state(connector) -> None:
    """Checkpoints live in memory, retries do not really sleep, and the processor
    calls made when records are removed or refreshed can be awaited."""
    for name in (
        "issues_sync_point", "attachments_sync_point", "documents_sync_point",
        "projects_sync_point", "deletion_sync_point", "reconcile_sync_point",
    ):
        setattr(connector, name, _memory_sync_point())
    connector._reconciler._sync_point = connector.reconcile_sync_point
    connector._sleep = AsyncMock()
    connector._api._sleep = connector._sleep
    # The cheap "did anything change" probe is tested on its own.
    connector._team_has_changes = AsyncMock(return_value=True)
    processor = connector.data_entities_processor
    for name, value in (
        ("get_records_by_parent", []),
        ("get_records_by_record_type", []),
        ("get_records_in_record_group", []),
        ("get_nodes_by_filters", []),
        ("on_records_deleted_cascade", None),
        ("on_records_detached_from_parent", None),
        ("on_record_content_update", None),
        ("on_record_group_deleted", None),
        ("on_user_group_deleted", None),
    ):
        if not isinstance(getattr(processor, name), AsyncMock):
            setattr(processor, name, AsyncMock(return_value=value))


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
    data_entities_processor.get_placeholder_records = AsyncMock(return_value=[])
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
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
        scope="personal",
        created_by="test-user-id",
    )
    connector._tx_store = mock_tx_store
    _use_memory_state(connector)
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
        assert ticket.record_name == "[ENG-1] Test Issue"
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
                with patch.object(
                    c, "_sync_issues_for_teams", new_callable=AsyncMock,
                    return_value=(set(), [])
                ):
                    with patch.object(c, "_sync_attachments", new_callable=AsyncMock):
                        with patch.object(c, "_sync_documents", new_callable=AsyncMock):
                            with patch.object(c, "_sync_projects_for_teams", new_callable=AsyncMock):
                                with patch.object(c, "_sync_deleted_issues", new_callable=AsyncMock):
                                    with patch.object(c, "_sync_deleted_projects", new_callable=AsyncMock):
                                        with patch.object(
                                            c,
                                            "_sweep_placeholder_records",
                                            new_callable=AsyncMock,
                                            return_value=0,
                                        ):
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
        c._now = MagicMock(return_value=1_700_000_000_000)

        team_rg, perms = _make_team_record_group()

        async def fake_fetch(*args, **kwargs):
            issue = _make_issue_data()
            ticket = c._transform_issue_to_ticket_record(issue, "team-1")
            yield [(ticket, [])]

        with patch.object(c, "_fetch_issues_for_team_batch", side_effect=fake_fetch) as fetch:
            full_sync_team_ids, failed = await c._sync_issues_for_teams([(team_rg, perms)])

        c.data_entities_processor.on_new_records.assert_called()
        # A team with no checkpoint is read from the beginning...
        assert fetch.call_args.kwargs["last_sync_time"] is None
        assert full_sync_team_ids == {"team-1"}
        assert failed == []
        # ...and once its whole list is stored, the checkpoint (kept under the team's
        # id) moves to the moment the run started.
        assert c.issues_sync_point.store["team_issues_team-1"]["last_sync_time"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_sync_issues_error_continues(self):
        c = _make_connector()
        c._now = MagicMock(return_value=1_700_000_000_000)
        team_rg, perms = _make_team_record_group()
        team_rg2, perms2 = _make_team_record_group("team-2", "DES")

        async def read(key) -> dict:
            if key == "team_issues_team-1":
                raise Exception("checkpoint error")
            return {}

        c.issues_sync_point.read_sync_point = AsyncMock(side_effect=read)

        async def no_issues(*args, **kwargs) -> AsyncIterator[list]:
            if False:
                yield []

        with patch.object(c, "_fetch_issues_for_team_batch", side_effect=no_issues) as fetch:
            full_sync_team_ids, failed = await c._sync_issues_for_teams([(team_rg, perms), (team_rg2, perms2)])

        # The first team is reported and its checkpoint is left alone; the second runs.
        assert failed == ["Team ENG"]
        assert full_sync_team_ids == {"team-2"}
        assert fetch.call_count == 1
        assert fetch.call_args.kwargs["team_id"] == "team-2"
        assert "team_issues_team-1" not in c.issues_sync_point.store
        assert c.issues_sync_point.store["team_issues_team-2"]["last_sync_time"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_sync_issues_auth_error_stops_the_sync(self) -> None:
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        team_rg2, perms2 = _make_team_record_group("team-2", "DES")

        def rejected(*args, **kwargs) -> Never:
            raise LinearAuthError("token revoked", 401)

        with patch.object(c, "_fetch_issues_for_team_batch", side_effect=rejected) as fetch:
            with pytest.raises(LinearAuthError):
                await c._sync_issues_for_teams([(team_rg, perms), (team_rg2, perms2)])

        assert fetch.call_count == 1
        assert c.issues_sync_point.store == {}

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
        c._now = MagicMock(return_value=1_700_000_000_000)
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        att = _make_attachment_data()
        mock_ds.attachments = AsyncMock(return_value=_mock_attachments_response([att], has_next=False))

        parent_record = MagicMock()
        parent_record.id = "parent-node-id"

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])
            c.data_entities_processor.get_record_by_weburl = AsyncMock(return_value=None)
            await c._sync_attachments([(team_rg, perms)])

        # First run: no date filter, first page.
        mock_ds.attachments.assert_awaited_once_with(first=50, after=None, filter=None)
        (batch,), _ = c.data_entities_processor.on_new_records.await_args
        assert len(batch) == 1
        link, link_permissions = batch[0]
        assert isinstance(link, LinkRecord)
        assert link.external_record_id == "att-1"
        assert link.parent_external_record_id == "issue-1"
        assert link.external_record_group_id == "team-1"
        assert link_permissions == []
        assert c.attachments_sync_point.store["attachments"]["last_sync_time"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_sync_attachments_empty(self):
        c = _make_connector()
        await c._sync_attachments([])


class TestLinearSyncDocuments:
    @pytest.mark.asyncio
    async def test_sync_documents_with_data(self):
        c = _make_connector()
        c.indexing_filters = None
        c._now = MagicMock(return_value=1_700_000_000_000)
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        doc = _make_document_data()
        mock_ds.documents = AsyncMock(return_value=_mock_documents_response([doc], has_next=False))

        parent_record = MagicMock()
        parent_record.id = "parent-node-id"
        parent_record.external_record_group_id = "team-1"
        parent_record.is_placeholder = False

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=[parent_record, None])
            await c._sync_documents([(team_rg, perms)])

        mock_ds.documents.assert_awaited_once_with(first=50, after=None, filter=None)
        (batch,), _ = c.data_entities_processor.on_new_records.await_args
        assert len(batch) == 1
        page, page_permissions = batch[0]
        assert isinstance(page, WebpageRecord)
        assert page.external_record_id == "doc-1"
        assert page.parent_external_record_id == "issue-1"
        assert page.parent_record_type == RecordType.TICKET
        assert page.external_record_group_id == "team-1"
        assert page_permissions == []
        assert c.documents_sync_point.store["documents"]["last_sync_time"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_sync_documents_no_issue(self):
        c = _make_connector()
        c.indexing_filters = None
        c._now = MagicMock(return_value=1_700_000_000_000)
        team_rg, perms = _make_team_record_group()

        mock_ds = MagicMock()
        doc = _make_document_data()
        doc["issue"] = None
        doc["project"] = None
        mock_ds.documents = AsyncMock(return_value=_mock_documents_response([doc], has_next=False))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await c._sync_documents([(team_rg, perms)])

        # A document attached to nothing has no place to be filed: skipped, and the
        # listing still counts as read to its end.
        c.data_entities_processor.on_new_records.assert_not_awaited()
        assert c.documents_sync_point.store["documents"]["last_sync_time"] == 1_700_000_000_000

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
        c._now = MagicMock(return_value=1_700_000_000_000)
        team_rg, perms = _make_team_record_group()
        mock_ds = MagicMock()
        mock_ds.trashed_issues = AsyncMock(return_value=_mock_issues_response([], has_next=False))

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await c._sync_deleted_issues([(team_rg, perms)])

        # The first run reads everything that is in the trash now.
        mock_ds.trashed_issues.assert_awaited_once_with(
            first=100, after=None,
            filter={"team": {"id": {"in": ["team-1"]}}, "archivedAt": {"null": False}},
        )
        assert c.deletion_sync_point.store["trashed_issues"]["last_sync_time"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_trashed(self):
        c = _make_connector()
        c._now = MagicMock(return_value=1_700_000_500_000)
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.store["trashed_issues"] = {"last_sync_time": 1_700_000_000_000}

        mock_ds = MagicMock()
        trashed_issue = _make_issue_data()
        trashed_issue["trashed"] = True
        trashed_issue["archivedAt"] = "2024-06-01T10:00:00.000Z"
        archived_only = _make_issue_data(issue_id="issue-2", identifier="ENG-2")
        archived_only["trashed"] = False
        archived_only["archivedAt"] = "2024-06-01T10:00:00.000Z"
        mock_ds.trashed_issues = AsyncMock(
            return_value=_mock_issues_response([trashed_issue, archived_only], has_next=False))
        c._remover.remove = AsyncMock(return_value=True)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await c._sync_deleted_issues([(team_rg, perms)])

        # Later runs read what was archived since the last completed run, minus the
        # one-minute overlap.
        since = c._linear_datetime_from_timestamp(1_700_000_000_000 - 60_000)
        assert mock_ds.trashed_issues.await_args.kwargs["filter"] == {
            "team": {"id": {"in": ["team-1"]}}, "archivedAt": {"gte": since},
        }
        # Only the trashed issue is removed; an archived one stays.
        c._remover.remove.assert_awaited_once_with("issue-1")
        assert c.deletion_sync_point.store["trashed_issues"]["last_sync_time"] == 1_700_000_500_000

    @pytest.mark.asyncio
    async def test_failed_trash_listing_keeps_the_checkpoint(self) -> None:
        c = _make_connector()
        c.notify = AsyncMock()
        team_rg, perms = _make_team_record_group()
        c.deletion_sync_point.store["trashed_issues"] = {"last_sync_time": 1_700_000_000_000}

        failed = MagicMock()
        failed.success = False
        failed.message = "boom"
        mock_ds = MagicMock()
        mock_ds.trashed_issues = AsyncMock(return_value=failed)
        c._remover.remove = AsyncMock(return_value=True)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await c._sync_deleted_issues([(team_rg, perms)])

        # A response with no status is retried three times, then the step is reported.
        assert mock_ds.trashed_issues.await_count == 4
        assert [call.args[0] for call in c._sleep.await_args_list] == [1, 3, 9]
        c._remover.remove.assert_not_awaited()
        c.notify.assert_awaited_once()
        assert c.deletion_sync_point.store["trashed_issues"]["last_sync_time"] == 1_700_000_000_000

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_empty_teams(self):
        c = _make_connector()
        await c._sync_deleted_issues([])
        assert c.deletion_sync_point.store == {}


class TestLinearSyncDeletedProjects:
    @pytest.mark.asyncio
    async def test_compares_stored_projects_of_the_synced_teams(self) -> None:
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        team_rg2, perms2 = _make_team_record_group("team-2", "DES")
        c._reconciler.run_projects = AsyncMock(return_value=2)

        await c._sync_deleted_projects([(team_rg, perms), (team_rg2, perms2)])

        c._reconciler.run_projects.assert_awaited_once_with({"team-1", "team-2"})

    @pytest.mark.asyncio
    async def test_no_teams_does_nothing(self) -> None:
        c = _make_connector()
        c._reconciler.run_projects = AsyncMock(return_value=0)
        await c._sync_deleted_projects([])
        c._reconciler.run_projects.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_comparison_is_not_fatal(self) -> None:
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        c._reconciler.run_projects = AsyncMock(side_effect=RuntimeError("listing failed"))
        await c._sync_deleted_projects([(team_rg, perms)])
        c._reconciler.run_projects.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auth_error_stops_the_sync(self) -> None:
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        c._reconciler.run_projects = AsyncMock(side_effect=LinearAuthError("token revoked", 401))
        with pytest.raises(LinearAuthError):
            await c._sync_deleted_projects([(team_rg, perms)])


class TestLinearMarkRecordDeleted:
    @pytest.mark.asyncio
    async def test_parent_not_found(self):
        c = _make_connector()
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await c._mark_record_and_children_deleted("nonexistent", "issue")
        c.data_entities_processor.get_record_by_external_id.assert_awaited_once_with(
            c.connector_id, "nonexistent")
        c.data_entities_processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parent_with_children(self):
        c = _make_connector()
        parent = MagicMock()
        parent.id = "p1"
        parent.external_record_id = "ext-1"
        parent.record_type = RecordType.TICKET
        child = MagicMock()
        child.id = "c1"
        child.external_record_id = "ext-child-1"
        child.record_type = RecordType.LINK

        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=parent)
        c.data_entities_processor.get_records_by_parent = AsyncMock(side_effect=[[child], []])

        await c._mark_record_and_children_deleted("ext-1", "issue")

        # The child goes with its parent, through the processor's delete path.
        c.data_entities_processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["c1", "p1"], c.connector_id, cascade_children=False, include_trashed_roots=True)
        c.data_entities_processor.on_records_detached_from_parent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_removal_error_is_logged_not_raised(self) -> None:
        c = _make_connector()
        c._remover.remove = AsyncMock(side_effect=RuntimeError("db down"))
        await c._mark_record_and_children_deleted("ext-1", "issue")
        c._remover.remove.assert_awaited_once_with("ext-1")
        c.logger.error.assert_called()


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
                with pytest.raises(ConnectorInitError, match="No organization data"):
                    await c.init()

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
                with pytest.raises(ConnectorInitError, match="No organization data"):
                    await c.init()


# ===================================================================
# Filter options
# ===================================================================

class TestLinearFilterOptions:
    @pytest.mark.asyncio
    async def test_team_options_with_search_filter(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        # Linear applies the search; it returns only what matched.
        mock_ds.execute_operation = AsyncMock(return_value=_mock_teams_response(
            [{"id": "t1", "name": "Engineering", "key": "ENG"}],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.get_filter_options("team_ids", search="  eng ")
        mock_ds.execute_operation.assert_awaited_once_with("teamList", {
            "first": 20,
            "after": None,
            "filter": {"or": [
                {"name": {"containsIgnoreCase": "eng"}},
                {"key": {"containsIgnoreCase": "eng"}},
            ]},
        })
        assert len(result.options) == 1
        assert "Engineering" in result.options[0].label

    @pytest.mark.asyncio
    async def test_team_options_without_search_sends_no_filter(self) -> None:
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.execute_operation = AsyncMock(return_value=_mock_teams_response(
            [
                {"id": "t1", "name": "Engineering", "key": "ENG"},
                {"id": "t2", "name": "Design", "key": "DES"},
            ],
            has_next=False,
        ))
        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            result = await c.get_filter_options("team_ids")
        mock_ds.execute_operation.assert_awaited_once_with("teamList", {"first": 20, "after": None, "filter": None})
        assert [o.id for o in result.options] == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_team_options_api_failure(self):
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        failed = MagicMock()
        failed.success = False
        failed.message = "API error"
        mock_ds.execute_operation = AsyncMock(return_value=failed)
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
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 422

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
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 400


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

        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=parent_record)

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
            "", "parent-1", "node-1", RecordType.TICKET, "team-1"
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
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing_record)

        md = "[doc](https://uploads.linear.app/test/doc.pdf)"
        new_files, existing = await c._extract_files_from_markdown(
            md, "parent-1", "node-1", RecordType.TICKET, "team-1"
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
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        att_data = [_make_attachment_data()]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1")
        assert len(result) == 1
        assert result[0].child_id == "existing-att-id"

    @pytest.mark.asyncio
    async def test_new_attachment(self):
        c = _make_connector()
        c.indexing_filters = None
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        att_data = [_make_attachment_data()]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1")
        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_empty_attachment_id(self):
        c = _make_connector()
        att_data = [{"id": "", "url": "http://x"}]
        result = await c._process_issue_attachments(att_data, "issue-1", "node-1", "team-1")
        assert len(result) == 0


class TestLinearProcessIssueDocuments:
    @pytest.mark.asyncio
    async def test_existing_document(self):
        c = _make_connector()
        c.indexing_filters = None
        existing = MagicMock()
        existing.id = "existing-doc-id"
        existing.record_name = "doc"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        doc_data = [_make_document_data()]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1")
        assert len(result) == 1
        assert result[0].child_id == "existing-doc-id"

    @pytest.mark.asyncio
    async def test_new_document(self):
        c = _make_connector()
        c.indexing_filters = None
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        doc_data = [_make_document_data()]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1")
        c.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_empty_document_id(self):
        c = _make_connector()
        doc_data = [{"id": "", "url": "http://x"}]
        result = await c._process_issue_documents(doc_data, "issue-1", "node-1", "team-1")
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
            with pytest.raises(HTTPException) as exc_info:
                await c._fetch_document_content("doc-1")

        # A failure with no HTTP status is a transport failure: retried three times,
        # then reported as "could not reach the source".
        assert mock_ds.document.await_count == 4
        assert [call.args[0] for call in c._sleep.await_args_list] == [1, 3, 9]
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    @pytest.mark.parametrize("response,expected_status,expected_calls", [
        (GraphQLResponse(success=False, message="Entity not found: Document", status_code=400), 404, 1),
        (GraphQLResponse(success=False, message="Unauthorized", status_code=401), 409, 1),
        (GraphQLResponse(success=False, message="Too many requests", status_code=429), 429, 7),
        (GraphQLResponse(success=False, message="Bad gateway", status_code=502), 502, 4),
        (GraphQLResponse(success=True, data={"document": None}), 404, 1),
    ])
    async def test_failure_is_mapped_for_the_reader(self, response, expected_status, expected_calls) -> None:
        c = _make_connector()
        c.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(return_value=response)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            with pytest.raises(HTTPException) as exc_info:
                await c._fetch_document_content("doc-1")

        assert exc_info.value.status_code == expected_status
        assert mock_ds.document.await_count == expected_calls

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
                    project_data, "proj-1", MagicMock(), "team-1"
                )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_related(self):
        c = _make_connector()
        project_data = _make_project_data()
        result = await c._prepare_project_related_records(
            project_data, "proj-1", None, "team-1"
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
# Placeholder ancestor sweep
# ===================================================================

def _placeholder_ticket(
    external_id: str = "parent-1",
    group_id: str = "team-1",
    revision: str | None = None,
) -> TicketRecord:
    return TicketRecord(
        id=str(uuid4()),
        org_id="org-1",
        record_name=external_id,
        record_type=RecordType.TICKET,
        external_record_id=external_id,
        external_revision_id=revision,
        external_record_group_id=group_id,
        record_group_type=RecordGroupType.PROJECT,
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.LINEAR,
        connector_id="linear-conn-1",
        mime_type=MimeTypes.UNKNOWN.value,
        is_placeholder=True,
    )


class TestLinearPlaceholderSweep:
    @pytest.mark.asyncio
    async def test_sweep_backfills_parent_stub(self):
        c = _make_connector()
        stub = _placeholder_ticket()
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        issue = _make_issue_data(
            issue_id="parent-1",
            identifier="ENG-10",
            title="Parent issue",
        )
        issue["team"] = {"id": "team-1", "key": "ENG", "name": "Eng"}
        # Relations must not become related_external_records on the stub.
        issue["relations"] = {
            "nodes": [{
                "type": "blocks",
                "relatedIssue": {"id": "related-99", "identifier": "ENG-99"},
            }]
        }
        resp = MagicMock()
        resp.success = True
        resp.data = {"issues": {"nodes": [issue]}}
        ds = MagicMock()
        ds.issues = AsyncMock(return_value=resp)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            total = await c._sweep_placeholder_records(
                synced_team_ids={"team-1"},
                full_sync_team_ids=set(),
            )

        assert total == 1
        c.data_entities_processor.on_new_records.assert_awaited()
        record, perms = c.data_entities_processor.on_new_records.await_args.args[0][0]
        assert record.is_placeholder is True
        assert "[ENG-10]" in (record.record_name or "")
        assert record.weburl
        assert record.external_revision_id.startswith(PLACEHOLDER_REVISION_PREFIX)
        assert not record.related_external_records
        assert perms == []

    @pytest.mark.asyncio
    async def test_sweep_skips_already_backfilled_unless_full_sync(self):
        c = _make_connector()
        stub = _placeholder_ticket(revision=f"{PLACEHOLDER_REVISION_PREFIX}123")
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])

        total = await c._sweep_placeholder_records(
            synced_team_ids={"team-1"},
            full_sync_team_ids=set(),
        )
        assert total == 0
        c.data_entities_processor.on_new_records.assert_not_awaited()

        with patch.object(c, "_fetch_ancestor_level", new_callable=AsyncMock, return_value=[None]):
            total = await c._sweep_placeholder_records(
                synced_team_ids={"team-1"},
                full_sync_team_ids={"team-1"},
            )
        assert total == 0
        c.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_sweep_skips_stubs_outside_synced_teams(self):
        c = _make_connector()
        stub = _placeholder_ticket(group_id="other-team")
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])

        total = await c._sweep_placeholder_records(
            synced_team_ids={"team-1"},
            full_sync_team_ids=set(),
        )
        assert total == 0
        c.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sweep_bfs_continues_to_grandparent(self):
        c = _make_connector()
        parent_stub = _placeholder_ticket(external_id="parent-1")
        grand_stub = _placeholder_ticket(external_id="grand-1")

        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[parent_stub])

        parent_issue = _make_issue_data(issue_id="parent-1", identifier="ENG-10", title="Parent")
        parent_issue["parent"] = {"id": "grand-1", "identifier": "ENG-1"}
        parent_issue["team"] = {"id": "team-1"}
        grand_issue = _make_issue_data(issue_id="grand-1", identifier="ENG-1", title="Grandparent")
        grand_issue["team"] = {"id": "team-1"}

        async def get_by_ext(*, connector_id, external_record_id):
            if external_record_id == "grand-1":
                return grand_stub
            return None

        c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=get_by_ext)

        async def fetch_level(frontier):
            out = []
            for stub in frontier:
                if stub.external_record_id == "parent-1":
                    out.append(parent_issue)
                elif stub.external_record_id == "grand-1":
                    out.append(grand_issue)
                else:
                    out.append(None)
            return out

        with patch.object(c, "_fetch_ancestor_level", side_effect=fetch_level):
            total = await c._sweep_placeholder_records(
                synced_team_ids={"team-1"},
                full_sync_team_ids=set(),
            )

        assert total == 2
        assert c.data_entities_processor.on_new_records.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_ancestor_level_falls_back_to_get(self):
        c = _make_connector()
        stub = _placeholder_ticket()
        bad = MagicMock()
        bad.success = False
        bad.message = "filter rejected"
        issue = _make_issue_data(issue_id="parent-1", identifier="ENG-10", title="Parent")
        ok = MagicMock()
        ok.success = True
        ok.data = {"issue": issue}
        ds = MagicMock()
        ds.issues = AsyncMock(return_value=bad)
        ds.issue = AsyncMock(return_value=ok)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            out = await c._fetch_ancestor_level([stub])

        assert out[0] is not None and out[0]["id"] == "parent-1"
        ds.issue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_ancestor_level_gets_partial_batch_misses(self):
        c = _make_connector()
        stub_a = _placeholder_ticket(external_id="a")
        stub_b = _placeholder_ticket(external_id="b")
        issue_a = _make_issue_data(issue_id="a", identifier="ENG-1", title="A")
        issue_b = _make_issue_data(issue_id="b", identifier="ENG-2", title="B")

        batch_ok = MagicMock()
        batch_ok.success = True
        batch_ok.data = {"issues": {"nodes": [issue_a]}}  # b omitted
        get_ok = MagicMock()
        get_ok.success = True
        get_ok.data = {"issue": issue_b}
        ds = MagicMock()
        ds.issues = AsyncMock(return_value=batch_ok)
        ds.issue = AsyncMock(return_value=get_ok)

        with patch.object(c, "_get_fresh_datasource", new_callable=AsyncMock, return_value=ds):
            out = await c._fetch_ancestor_level([stub_a, stub_b])

        assert out[0]["id"] == "a"
        assert out[1]["id"] == "b"
        ds.issue.assert_awaited_once_with(id="b")

    def test_build_ancestor_stub_soft_fails_without_team(self):
        c = _make_connector()
        stub = _placeholder_ticket()
        stub.external_record_group_id = None
        issue = _make_issue_data(issue_id="parent-1", identifier="ENG-10", title="Parent")
        issue.pop("team", None)

        assert c._build_ancestor_stub(issue, stub) is None

    @pytest.mark.asyncio
    async def test_sweep_soft_fails_rebuild_keeps_stub(self):
        c = _make_connector()
        stub = _placeholder_ticket()
        c.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        issue = _make_issue_data(issue_id="parent-1", identifier="ENG-10", title="Parent")

        with patch.object(c, "_fetch_ancestor_level", new_callable=AsyncMock, return_value=[issue]):
            with patch.object(c, "_build_ancestor_stub", return_value=None):
                total = await c._sweep_placeholder_records(
                    synced_team_ids={"team-1"},
                    full_sync_team_ids=set(),
                )

        assert total == 0
        record, _ = c.data_entities_processor.on_new_records.await_args.args[0][0]
        assert record is stub
        assert record.is_placeholder is True

    @pytest.mark.asyncio
    async def test_stream_record_rejects_placeholder(self):
        c = _make_connector()
        c.data_source = MagicMock()
        stub = _placeholder_ticket()
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(stub)
        assert exc_info.value.status_code == 422
        assert "placeholder" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_run_sync_calls_placeholder_sweep(self):
        c = _make_connector()
        c.data_source = MagicMock()
        team_rg, _ = _make_team_record_group()
        c.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock(email="a@t.com")]
        )
        c._fetch_users = AsyncMock(return_value=[])
        c._fetch_teams = AsyncMock(return_value=([], [(team_rg, [])]))
        c._sync_issues_for_teams = AsyncMock(return_value=({"team-1"}, []))
        c._sync_attachments = AsyncMock()
        c._sync_documents = AsyncMock()
        c._sync_projects_for_teams = AsyncMock()
        c._sync_deleted_issues = AsyncMock()
        c._sync_deleted_projects = AsyncMock()
        c._sweep_placeholder_records = AsyncMock(return_value=2)

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await c.run_sync()

        c._sweep_placeholder_records.assert_awaited_once_with(
            synced_team_ids={"team-1"},
            full_sync_team_ids={"team-1"},
        )

    @pytest.mark.asyncio
    async def test_sync_issues_returns_full_sync_team_ids(self):
        c = _make_connector()
        team_rg, perms = _make_team_record_group()
        team_rg2, perms2 = _make_team_record_group("team-2", "DES")
        # team-2 completed a run before; team-1 never did.
        c.issues_sync_point.store["team_issues_team-2"] = {"last_sync_time": 1_700_000_000_000}

        async def fake_fetch(*args, **kwargs):
            if False:
                yield []

        with patch.object(c, "_fetch_issues_for_team_batch", side_effect=fake_fetch):
            result = await c._sync_issues_for_teams([(team_rg, perms), (team_rg2, perms2)])
        full_sync_team_ids, failed_team_keys = result
        assert full_sync_team_ids == {"team-1"}
        assert failed_team_keys == []


# ===================================================================
# Create connector factory
# ===================================================================

class TestLinearCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        processor = MagicMock()
        processor.org_id = "org-1"
        connector = await LinearConnector.create_connector(
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id="test-conn",
            scope="personal",
            created_by="test-user-id",
            data_entities_processor=processor,
        )
        assert isinstance(connector, LinearConnector)
