"""Tests for the Drupal Wiki record building (ids, timestamps, permissions, tree).

The builders are methods on the connector, so every test takes the shared
``connector`` fixture from :mod:`test_drupal_wiki_connector`.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.drupal_wiki.connector import (
    PAGE_ID_PREFIX,
    DrupalWikiConnector,
    source_id_from_external,
)
from app.models.entities import RecordType
from app.models.permission import EntityType, PermissionType
from app.sources.external.drupal_wiki.graphql import DrupalWikiGraphQLError

CONNECTOR_ID = "conn-1"
ORG_ID = "org-1"
BASE_URL = "https://wiki.example.com"


@pytest.fixture()
def connector() -> DrupalWikiConnector:
    processor = MagicMock()
    processor.org_id = ORG_ID
    instance = DrupalWikiConnector(
        logging.getLogger("test.drupal_wiki"),
        processor,
        MagicMock(),
        MagicMock(),
        CONNECTOR_ID,
        "team",
        "creator-1",
    )
    instance.base_url = BASE_URL
    instance.data_source = MagicMock()
    instance.sync_filters = FilterCollection()
    instance.indexing_filters = FilterCollection()
    instance.graphql = MagicMock()
    instance.graphql.get_space_tree = AsyncMock(side_effect=DrupalWikiGraphQLError("no tree"))
    return instance

PAGE = {
    "id": 733,
    "title": "Runbook",
    "type": "DOCUMENT",
    "homeSpace": 12,
    "lastModified": 1722935527,
}


class TestSpacePermissions:
    def test_open_spaces_grant_the_whole_org(self, connector: DrupalWikiConnector) -> None:
        for status in ("AUTHENTICATED", "ANONYMOUS"):
            permissions = connector._build_space_permissions({"id": 3, "accessStatus": status}, None)
            assert len(permissions) == 1
            assert permissions[0].entity_type == EntityType.ORG
            assert permissions[0].type == PermissionType.READ

    def test_private_space_without_members_grants_nothing(self, connector: DrupalWikiConnector) -> None:
        assert connector._build_space_permissions({"id": 12, "accessStatus": "PRIVATE"}, None) == []

    def test_every_member_of_a_private_space_gets_one_read_edge(self, connector: DrupalWikiConnector) -> None:
        # Holding any role means being able to see the space -- an approver cannot
        # approve a revision without reading it -- so membership is the grant, and
        # nothing downstream separates the kinds of grant.
        members = {
            "users": [
                {"id": 1, "email": "alice@example.com"},
                {"id": 2, "email": "bob@example.com"},
                {"id": 3, "email": "eve@example.com"},
            ],
            "groups": [{"id": 5}],
        }
        permissions = connector._build_space_permissions({"id": 12, "accessStatus": "PRIVATE"}, members)
        by_key = {(p.entity_type, p.email or p.external_id): p.type for p in permissions}
        assert by_key == {
            (EntityType.USER, "alice@example.com"): PermissionType.READ,
            (EntityType.USER, "bob@example.com"): PermissionType.READ,
            (EntityType.USER, "eve@example.com"): PermissionType.READ,
            (EntityType.GROUP, "5"): PermissionType.READ,
        }

    def test_duplicate_principal_yields_one_edge(self, connector: DrupalWikiConnector) -> None:
        members = {
            "users": [
                {"id": 1, "email": "alice@example.com"},
                {"id": 2, "email": "ALICE@example.com"},
            ],
            "groups": [],
        }
        permissions = connector._build_space_permissions({"id": 12, "accessStatus": "PRIVATE"}, members)
        assert len(permissions) == 1
        assert permissions[0].type == PermissionType.READ

class TestRecords:
    def test_page_record(self, connector: DrupalWikiConnector) -> None:
        record = connector._build_page_record(PAGE)
        assert record.external_record_id == "page:733"
        assert record.external_revision_id == "1722935527"
        assert record.external_record_group_id == "12"
        assert record.record_type == RecordType.WEBPAGE
        assert record.mime_type == MimeTypes.HTML.value
        assert record.weburl == "https://wiki.example.com/node/733"
        # lastModified is in seconds; records store milliseconds.
        assert record.source_updated_at == 1722935527000
        assert record.inherit_permissions is True
        assert record.version == 0
        assert record.parent_external_record_id is None

    def test_page_record_with_parent_and_indexing_off(self, connector: DrupalWikiConnector) -> None:
        record = connector._build_page_record(
            PAGE, parent_page_id=700, auto_index_off=True
        )
        assert record.parent_external_record_id == "page:700"
        assert record.parent_record_type == RecordType.WEBPAGE
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    def test_attachment_record_hangs_off_its_page(self, connector: DrupalWikiConnector) -> None:
        record = connector._build_attachment_record(
            {"id": 88, "name": "Diagram", "fileName": "diagram.pdf", "fileSize": 2048,
             "lastModified": 1722935600},
            PAGE,
        )
        assert record.external_record_id == "attachment:88"
        assert record.parent_external_record_id == "page:733"
        # WEBPAGE parents make the processor write an ATTACHMENT edge.
        assert record.parent_record_type == RecordType.WEBPAGE
        assert record.external_record_group_id == "12"
        assert record.is_file is True
        assert record.extension == "pdf"
        assert record.mime_type == "application/pdf"
        assert record.size_in_bytes == 2048
        assert record.weburl == "https://wiki.example.com/node/733"

    def test_attachment_without_known_type_falls_back(self, connector: DrupalWikiConnector) -> None:
        record = connector._build_attachment_record(
            {"id": 9, "fileName": "notes.unknownext", "lastModified": 1}, PAGE,
        )
        assert record.mime_type == MimeTypes.UNKNOWN.value
        assert record.record_name == "notes.unknownext"

    def test_external_id_round_trip(self, connector: DrupalWikiConnector) -> None:
        record = connector._build_page_record(PAGE)
        assert source_id_from_external(record.external_record_id, PAGE_ID_PREFIX) == 733
        assert source_id_from_external("attachment:88", PAGE_ID_PREFIX) is None
        assert source_id_from_external("page:not-a-number", PAGE_ID_PREFIX) is None

    def test_page_web_url_handles_trailing_slash(self, connector: DrupalWikiConnector) -> None:
        connector.base_url = "https://wiki.example.com/"
        assert connector._page_web_url(5) == "https://wiki.example.com/node/5"


class TestTreeAndHtml:
    def test_parent_ids_resolve_through_node_ids(self, connector: DrupalWikiConnector) -> None:
        tree = [
            {"id": "n1", "pageId": 100, "parentId": None, "position": 0},
            {"id": "n2", "pageId": 200, "parentId": "n1", "position": 1},
            {"id": "n3", "pageId": 300, "parentId": "n2", "position": 0},
        ]
        assert connector._extract_parent_page_ids(tree) == {200: 100, 300: 200}

    def test_tree_ignores_broken_nodes(self, connector: DrupalWikiConnector) -> None:
        tree = [
            {"id": "n1", "pageId": None, "parentId": None},
            {"id": "n2", "pageId": 200, "parentId": "missing"},
            {"pageId": 300, "parentId": "n1"},
        ]
        assert connector._extract_parent_page_ids(tree) == {}

    def test_page_html_escapes_the_title(self, connector: DrupalWikiConnector) -> None:
        # The body is wiki HTML by design; the title is plain text and must be escaped.
        html = connector._render_page_html({"title": "<script>alert(1)</script>", "body": "<p>ok</p>"})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_page_html_includes_title_tags_and_categories(self, connector: DrupalWikiConnector) -> None:
        html = connector._render_page_html({
            "title": "Runbook",
            "body": "<p>Restart the service</p>",
            "tags": [{"name": "ops"}],
            "categories": [{"name": "Handbook"}],
        })
        assert "<h1>Runbook</h1>" in html
        assert "<p>Restart the service</p>" in html
        assert "Categories:</strong> Handbook" in html
        assert "Tags:</strong> ops" in html

    def test_page_html_without_body(self, connector: DrupalWikiConnector) -> None:
        assert connector._render_page_html({"title": "Empty"}) == "<h1>Empty</h1>\n\n"


class TestExternalIdNamespacing:
    def test_a_page_and_an_attachment_with_the_same_id_stay_distinct(self, connector: DrupalWikiConnector) -> None:
        # Drupal Wiki numbers pages and attachments from independent sequences, and
        # records are looked up by (connector, external id) alone. Without the
        # prefixes these two would be one record.
        page = connector._build_page_record(
            {"id": 88, "title": "P", "homeSpace": 12, "lastModified": 1000}
        )
        attachment = connector._build_attachment_record(
            {"id": 88, "fileName": "a.pdf", "lastModified": 1000},
            {"id": 733, "homeSpace": 12},
        )
        assert page.external_record_id != attachment.external_record_id
        assert source_id_from_external(page.external_record_id, PAGE_ID_PREFIX) == 88
        assert source_id_from_external(attachment.external_record_id, PAGE_ID_PREFIX) is None
