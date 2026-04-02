"""Extended coverage tests for Linear connector - additional untested methods."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus, RecordRelations
from app.connectors.core.registry.filters import (
    FilterCollection,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.linear.connector import LinearConnector
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    BlocksContainer,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    ItemType,
    LinkRecord,
    MimeTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import Permission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_connector():
    """Build a LinearConnector with all dependencies mocked."""
    logger = logging.getLogger("test.linear.comp")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-lc"
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
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id="linear-comp-1",
    )
    connector._tx_store = mock_tx_store
    return connector


@pytest.fixture()
def connector():
    return _make_connector()


# ===========================================================================
# _process_project_external_links
# ===========================================================================
class TestProcessProjectExternalLinks:
    async def test_creates_link_records(self, connector):
        connector.indexing_filters = FilterCollection()
        connector.sync_filters = FilterCollection()
        links_data = [
            {"id": "link-1", "url": "https://ext.com/doc", "title": "Ext Doc",
             "createdAt": "2024-01-01T00:00:00.000Z", "updatedAt": "2024-01-02T00:00:00.000Z"},
        ]
        tx_store = connector._tx_store
        records, groups = await connector._process_project_external_links(
            external_links_data=links_data,
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=tx_store,
            create_block_groups=False,
        )
        assert len(records) == 1
        assert len(groups) == 0

    async def test_creates_block_groups_when_requested(self, connector):
        connector.indexing_filters = FilterCollection()
        connector.sync_filters = FilterCollection()
        links_data = [
            {"id": "link-2", "url": "https://ext.com/page", "label": "Page",
             "createdAt": "2024-01-01T00:00:00.000Z", "updatedAt": "2024-01-02T00:00:00.000Z"},
        ]
        tx_store = connector._tx_store
        records, groups = await connector._process_project_external_links(
            external_links_data=links_data,
            project_id="proj-2",
            project_node_id="node-2",
            team_id="team-2",
            tx_store=tx_store,
            create_block_groups=True,
        )
        assert len(records) == 1
        assert len(groups) == 1
        assert groups[0].sub_type == GroupSubType.CHILD_RECORD

    async def test_skips_links_without_id(self, connector):
        connector.indexing_filters = FilterCollection()
        links_data = [{"url": "https://example.com"}]
        tx_store = connector._tx_store
        records, groups = await connector._process_project_external_links(
            external_links_data=links_data,
            project_id="proj-3",
            project_node_id="node-3",
            team_id="team-3",
            tx_store=tx_store,
        )
        assert len(records) == 0

    async def test_handles_link_processing_error(self, connector):
        connector.indexing_filters = FilterCollection()
        # Link with id but no url will raise ValueError in _transform_attachment_to_link_record
        links_data = [{"id": "link-err", "createdAt": "", "updatedAt": ""}]
        tx_store = connector._tx_store
        records, groups = await connector._process_project_external_links(
            external_links_data=links_data,
            project_id="proj-4",
            project_node_id="node-4",
            team_id="team-4",
            tx_store=tx_store,
        )
        assert len(records) == 0  # Error caught, continues


# ===========================================================================
# _process_project_documents
# ===========================================================================
class TestProcessProjectDocuments:
    async def test_creates_document_records(self, connector):
        connector.indexing_filters = FilterCollection()
        connector.sync_filters = FilterCollection()
        docs_data = [
            {"id": "doc-1", "url": "https://linear.app/doc/1", "title": "Design Doc",
             "createdAt": "2024-01-01T00:00:00.000Z", "updatedAt": "2024-01-02T00:00:00.000Z"},
        ]
        tx_store = connector._tx_store
        records, groups = await connector._process_project_documents(
            documents_data=docs_data,
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=tx_store,
            create_block_groups=False,
        )
        assert len(records) == 1
        assert isinstance(records[0][0], WebpageRecord)

    async def test_skips_docs_without_id(self, connector):
        connector.indexing_filters = FilterCollection()
        docs_data = [{"url": "https://linear.app/doc/2", "title": "No ID"}]
        tx_store = connector._tx_store
        records, _ = await connector._process_project_documents(
            documents_data=docs_data,
            project_id="proj-5",
            project_node_id="node-5",
            team_id="team-5",
            tx_store=tx_store,
        )
        assert len(records) == 0


# ===========================================================================
# _prepare_project_related_records
# ===========================================================================
class TestPrepareProjectRelatedRecords:
    async def test_with_links_and_documents(self, connector):
        connector.indexing_filters = FilterCollection()
        connector.sync_filters = FilterCollection()
        full_data = {
            "externalLinks": {"nodes": [
                {"id": "l1", "url": "https://ext.com", "title": "External",
                 "createdAt": "", "updatedAt": ""},
            ]},
            "documents": {"nodes": [
                {"id": "d1", "url": "https://linear.app/d1", "title": "Doc",
                 "createdAt": "", "updatedAt": ""},
            ]},
        }
        tx_store = connector._tx_store
        records = await connector._prepare_project_related_records(
            full_project_data=full_data,
            project_id="proj-1",
            existing_record=None,
            team_id="team-1",
            tx_store=tx_store,
        )
        assert len(records) == 2

    async def test_no_links_or_documents(self, connector):
        full_data = {}
        tx_store = connector._tx_store
        records = await connector._prepare_project_related_records(
            full_project_data=full_data,
            project_id="proj-2",
            existing_record=None,
            team_id="team-2",
            tx_store=tx_store,
        )
        assert len(records) == 0


# ===========================================================================
# _organize_comments_by_thread - additional edge cases
# ===========================================================================
class TestOrganizeCommentsByThreadExtended:
    def test_single_thread(self, connector):
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        comments = [
            BlockComment(text="First", format=DataFormat.MARKDOWN, thread_id="t1", author_name="A", created_at=ts),
            BlockComment(text="Second", format=DataFormat.MARKDOWN, thread_id="t1", author_name="B",
                         created_at=datetime(2024, 1, 2, tzinfo=timezone.utc)),
        ]
        result = connector._organize_comments_by_thread(comments)
        assert len(result) == 1
        assert len(result[0]) == 2
        assert result[0][0].text == "First"

    def test_multiple_threads_sorted(self, connector):
        ts1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 2, tzinfo=timezone.utc)
        comments = [
            BlockComment(text="Late", format=DataFormat.MARKDOWN, thread_id="t2", author_name="A", created_at=ts2),
            BlockComment(text="Early", format=DataFormat.MARKDOWN, thread_id="t1", author_name="B", created_at=ts1),
        ]
        result = connector._organize_comments_by_thread(comments)
        assert len(result) == 2
        assert result[0][0].text == "Early"  # t1 is earlier
        assert result[1][0].text == "Late"

    def test_no_thread_id(self, connector):
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        comments = [
            BlockComment(text="No thread", format=DataFormat.MARKDOWN, thread_id=None, author_name="A", created_at=ts),
        ]
        result = connector._organize_comments_by_thread(comments)
        assert len(result) == 1

    def test_empty_list(self, connector):
        result = connector._organize_comments_by_thread([])
        assert result == []


# ===========================================================================
# _create_blockgroup - additional edge cases
# ===========================================================================
class TestCreateBlockGroupExtended:
    def test_raises_without_weburl(self, connector):
        with pytest.raises(ValueError, match="weburl is required"):
            connector._create_blockgroup(name="Test", weburl="", data="content")

    def test_raises_without_data(self, connector):
        with pytest.raises(ValueError, match="data is required"):
            connector._create_blockgroup(name="Test", weburl="https://example.com", data="")

    def test_creates_with_comments(self, connector):
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        comments = [[
            BlockComment(text="Comment", format=DataFormat.MARKDOWN, thread_id="t1", author_name="A", created_at=ts),
        ]]
        bg = connector._create_blockgroup(
            name="Test BG",
            weburl="https://example.com",
            data="Content data",
            comments=comments,
            group_subtype=GroupSubType.CONTENT,
            index=5,
        )
        assert bg.name == "Test BG"
        assert bg.index == 5
        assert len(bg.comments) == 1
        assert bg.sub_type == GroupSubType.CONTENT


# ===========================================================================
# _parse_linear_datetime_to_datetime
# ===========================================================================
class TestParseLinearDatetimeToDatetimeExtended:
    def test_valid(self, connector):
        result = connector._parse_linear_datetime_to_datetime("2024-06-15T12:30:00.000Z")
        assert result is not None
        assert result.year == 2024
        assert result.month == 6

    def test_empty_string(self, connector):
        assert connector._parse_linear_datetime_to_datetime("") is None

    def test_none(self, connector):
        assert connector._parse_linear_datetime_to_datetime(None) is None

    def test_invalid(self, connector):
        assert connector._parse_linear_datetime_to_datetime("not-a-date") is None


# ===========================================================================
# _linear_datetime_from_timestamp edge case
# ===========================================================================
class TestLinearDatetimeFromTimestampExtended:
    def test_valid_timestamp(self, connector):
        # Jan 1, 2024 UTC
        ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        result = connector._linear_datetime_from_timestamp(ts)
        assert "2024-01-01" in result
        assert result.endswith("Z")

    def test_zero_timestamp(self, connector):
        result = connector._linear_datetime_from_timestamp(0)
        assert "1970-01-01" in result


# ===========================================================================
# _extract_file_urls_from_markdown - more edge cases
# ===========================================================================
class TestExtractFileUrlsExtended:
    def test_mixed_linear_and_external(self, connector):
        md = "[File](https://uploads.linear.app/abc.pdf) [Ext](https://google.com/doc)"
        result = connector._extract_file_urls_from_markdown(md)
        assert len(result) == 1
        assert "linear.app" in result[0]["url"]

    def test_empty_link_text_not_matched(self, connector):
        """Empty link text [] doesn't match the regex [([^\]]+)] which requires 1+ chars."""
        md = "[](https://uploads.linear.app/file.pdf)"
        result = connector._extract_file_urls_from_markdown(md)
        assert len(result) == 0  # regex requires at least one char in link text

    def test_exclude_images_keeps_links(self, connector):
        md = "![img](https://uploads.linear.app/img.png) [doc](https://uploads.linear.app/doc.pdf)"
        result = connector._extract_file_urls_from_markdown(md, exclude_images=True)
        assert len(result) == 1
        # Link text "doc" is used as filename
        assert result[0]["filename"] == "doc"


# ===========================================================================
# _get_mime_type_from_url - more extensions
# ===========================================================================
class TestGetMimeTypeExtended:
    def test_markdown_extension(self, connector):
        result = connector._get_mime_type_from_url("file.md")
        assert result == MimeTypes.MARKDOWN.value

    def test_html_extension(self, connector):
        result = connector._get_mime_type_from_url("page.html")
        assert result == MimeTypes.HTML.value

    def test_csv_extension(self, connector):
        result = connector._get_mime_type_from_url("data.csv")
        assert result == MimeTypes.CSV.value

    def test_zip_extension(self, connector):
        result = connector._get_mime_type_from_url("archive.zip")
        assert result == MimeTypes.ZIP.value

    def test_no_extension_no_filename(self, connector):
        result = connector._get_mime_type_from_url("https://example.com/noext")
        assert result == MimeTypes.UNKNOWN.value

    def test_filename_takes_priority(self, connector):
        result = connector._get_mime_type_from_url("https://example.com/download", filename="report.xlsx")
        assert result == MimeTypes.XLSX.value


# ===========================================================================
# _apply_date_filters_to_linear_filter - additional cases
# ===========================================================================
class TestApplyDateFiltersExtended:
    def test_checkpoint_only(self, connector):
        connector.sync_filters = FilterCollection()
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=1704067200000)
        assert "updatedAt" in linear_filter
        assert "gt" in linear_filter["updatedAt"]

    def test_modified_filter_only(self, connector):
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1704067200000, None)
        mock_filters = MagicMock()
        mock_filters.get.side_effect = lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        connector.sync_filters = mock_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter)
        assert "updatedAt" in linear_filter

    def test_both_modified_and_created(self, connector):
        mock_mod_filter = MagicMock()
        mock_mod_filter.get_value.return_value = (1704067200000, 1704153600000)
        mock_created_filter = MagicMock()
        mock_created_filter.get_value.return_value = (1704067200000, 1704153600000)

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_mod_filter
            if key == SyncFilterKey.CREATED:
                return mock_created_filter
            return None

        mock_filters = MagicMock()
        mock_filters.get.side_effect = side_effect
        connector.sync_filters = mock_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter)
        assert "updatedAt" in linear_filter
        assert "createdAt" in linear_filter

    def test_checkpoint_and_filter_uses_max(self, connector):
        # filter modified_after = earlier, checkpoint = later
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1000, None)
        mock_filters = MagicMock()
        mock_filters.get.side_effect = lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None
        connector.sync_filters = mock_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=2000)
        # Should use max(1000, 2000) = 2000
        assert "updatedAt" in linear_filter


# ===========================================================================
# _transform_issue_to_ticket_record - more edge cases
# ===========================================================================
class TestTransformIssueExtended:
    def test_label_matching_bug(self, connector):
        """Labels with type 'Bug' should be mapped to ItemType.BUG."""
        issue = {
            "id": "iss-1",
            "identifier": "ENG-100",
            "title": "Bug report",
            "priority": 2,
            "state": {"name": "In Progress", "type": "started"},
            "labels": {"nodes": [{"name": "Bug"}]},
            "assignee": None,
            "creator": None,
            "parent": None,
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
            "url": "https://linear.app/iss-1",
            "relations": {"nodes": []},
        }
        result = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert result.type == ItemType.BUG

    def test_no_state(self, connector):
        issue = {
            "id": "iss-2",
            "identifier": "ENG-200",
            "title": "No state",
            "priority": None,
            "state": None,
            "labels": {"nodes": []},
            "assignee": None,
            "creator": None,
            "parent": None,
            "createdAt": "",
            "updatedAt": "",
            "url": "",
            "relations": None,
        }
        result = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert result is not None

    def test_priority_4_maps_to_low(self, connector):
        issue = {
            "id": "iss-3",
            "identifier": "ENG-300",
            "title": "Low priority",
            "priority": 4,
            "state": {"name": "Open", "type": "started"},
            "labels": None,
            "assignee": None,
            "creator": None,
            "parent": None,
            "createdAt": "",
            "updatedAt": "",
            "url": "",
            "relations": {"nodes": []},
        }
        result = connector._transform_issue_to_ticket_record(issue, "team-1")
        assert result is not None


# ===========================================================================
# _transform_to_project_record - more edge cases
# ===========================================================================
class TestTransformProjectExtended:
    def test_no_name_no_slug_uses_id(self, connector):
        data = {
            "id": "proj-1",
            "name": "",
            "slugId": "",
            "status": None,
            "priorityLabel": "",
            "lead": None,
            "createdAt": "",
            "updatedAt": "",
            "url": "",
        }
        result = connector._transform_to_project_record(data, "team-1")
        assert result.record_name == "proj-1"

    def test_slug_fallback(self, connector):
        data = {
            "id": "proj-2",
            "name": "",
            "slugId": "project-slug",
            "status": None,
            "priorityLabel": "",
            "lead": None,
            "createdAt": "",
            "updatedAt": "",
            "url": "",
        }
        result = connector._transform_to_project_record(data, "team-1")
        assert result.record_name == "project-slug"

    def test_with_lead(self, connector):
        data = {
            "id": "proj-3",
            "name": "My Project",
            "slugId": "",
            "status": {"name": "Active"},
            "priorityLabel": "High",
            "lead": {"id": "user-1", "displayName": "Alice", "email": "alice@example.com"},
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
            "url": "https://linear.app/proj-3",
        }
        result = connector._transform_to_project_record(data, "team-1")
        assert result.lead_name == "Alice"
        assert result.lead_email == "alice@example.com"


# ===========================================================================
# _transform_attachment_to_link_record - edge cases
# ===========================================================================
class TestTransformAttachmentExtended:
    def test_subtitle_as_title(self, connector):
        data = {
            "id": "att-1",
            "url": "https://ext.com/link",
            "subtitle": "Sub Title",
            "createdAt": "",
            "updatedAt": "",
        }
        result = connector._transform_attachment_to_link_record(data, "issue-1", "node-1", "team-1")
        assert result.record_name == "Sub Title"

    def test_label_as_title(self, connector):
        data = {
            "id": "att-2",
            "url": "https://ext.com/link",
            "label": "Link Label",
            "createdAt": "",
            "updatedAt": "",
        }
        result = connector._transform_attachment_to_link_record(data, "issue-1", "node-1", "team-1")
        assert result.record_name == "Link Label"

    def test_url_path_as_name_fallback(self, connector):
        data = {
            "id": "att-3",
            "url": "https://ext.com/path/to/resource",
            "createdAt": "",
            "updatedAt": "",
        }
        result = connector._transform_attachment_to_link_record(data, "issue-1", "node-1", "team-1")
        assert result.record_name == "resource"


# ===========================================================================
# _transform_document_to_webpage_record - edge cases
# ===========================================================================
class TestTransformDocumentExtended:
    def test_title_fallback_to_id(self, connector):
        data = {
            "id": "doc-abcd1234",
            "url": "https://linear.app/doc/1",
            "title": "",
            "createdAt": "",
            "updatedAt": "",
        }
        result = connector._transform_document_to_webpage_record(data, "issue-1", "node-1", "team-1")
        assert result.record_name == "Document doc-abcd"

    def test_version_increment_on_change(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 5
        existing.source_updated_at = 1000
        data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Updated Doc",
            "createdAt": "",
            "updatedAt": "2024-06-15T12:00:00.000Z",
        }
        result = connector._transform_document_to_webpage_record(
            data, "issue-1", "node-1", "team-1", existing_record=existing
        )
        assert result.id == "existing-id"
        assert result.version == 6

    def test_parent_record_type_project(self, connector):
        data = {
            "id": "doc-2",
            "url": "https://linear.app/doc/2",
            "title": "Project Doc",
            "createdAt": "",
            "updatedAt": "",
        }
        result = connector._transform_document_to_webpage_record(
            data, "proj-1", "node-1", "team-1", parent_record_type=RecordType.PROJECT
        )
        assert result.parent_record_type == RecordType.PROJECT
