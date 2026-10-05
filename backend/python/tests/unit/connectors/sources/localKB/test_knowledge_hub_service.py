"""Unit tests for KnowledgeHubService."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.utils.user_messages import action_failed, not_found
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    FOLDER_MIME_TYPES,
    BrowseRequestError,
    KnowledgeHubService,
)
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    AvailableFilters,
    NodeType,
    OriginType,
)


@pytest.fixture
def logger():
    log = logging.getLogger("test_kh_service")
    log.setLevel(logging.CRITICAL)
    return log


@pytest.fixture
def mock_graph_provider():
    return AsyncMock()


@pytest.fixture
def service(logger, mock_graph_provider):
    return KnowledgeHubService(logger=logger, graph_provider=mock_graph_provider)


# ============================================================================
# _has_flattening_filters
# ============================================================================
class TestHasSearchFilters:
    def test_no_filters(self, service):
        assert service._has_flattening_filters(None, None, None, None, None, None, None, None, None) is False

    def test_with_query(self, service):
        assert service._has_flattening_filters("test", None, None, None, None, None, None, None, None) is True

    def test_with_node_types(self, service):
        assert service._has_flattening_filters(None, ["folder"], None, None, None, None, None, None, None) is True

    def test_with_record_types(self, service):
        assert service._has_flattening_filters(None, None, ["FILE"], None, None, None, None, None, None) is True

    def test_with_origins(self, service):
        assert service._has_flattening_filters(None, None, None, ["COLLECTION"], None, None, None, None, None) is True

    def test_with_connector_ids(self, service):
        assert service._has_flattening_filters(None, None, None, None, ["c1"], None, None, None, None) is True

    def test_with_indexing_status(self, service):
        assert service._has_flattening_filters(None, None, None, None, None, ["COMPLETED"], None, None, None) is True

    def test_with_created_at(self, service):
        assert service._has_flattening_filters(None, None, None, None, None, None, {"gte": 100}, None, None) is True

    def test_with_updated_at(self, service):
        assert service._has_flattening_filters(None, None, None, None, None, None, None, {"lte": 200}, None) is True

    def test_with_size(self, service):
        assert service._has_flattening_filters(None, None, None, None, None, None, None, None, {"gte": 0}) is True


class TestHasFlatteningFilters:
    def test_no_filters(self, service):
        assert service._has_flattening_filters(None, None, None, None, None, None, None, None, None) is False

    def test_with_query(self, service):
        assert service._has_flattening_filters("search", None, None, None, None, None, None, None, None) is True

    def test_with_all_none(self, service):
        assert service._has_flattening_filters(None, None, None, None, None, None, None, None, None) is False


# ============================================================================
# _format_enum_label
# ============================================================================
class TestFormatEnumLabel:
    def test_upper_snake_case(self, service):
        assert service._format_enum_label("FILE_NAME") == "File Name"

    def test_camel_case(self, service):
        assert service._format_enum_label("createdAt") == "Created At"

    def test_special_case(self, service):
        assert service._format_enum_label("AUTO_INDEX_OFF", {"AUTO_INDEX_OFF": "Manual Indexing"}) == "Manual Indexing"

    def test_camel_case_multi(self, service):
        assert service._format_enum_label("autoIndexOff") == "Auto Index Off"

    def test_simple_word(self, service):
        assert service._format_enum_label("name") == "Name"

    def test_no_special_cases(self, service):
        assert service._format_enum_label("createdAt", {"updatedAt": "Modified Date"}) == "Created At"


# ============================================================================
# _role_to_permission
# ============================================================================
class TestRoleToPermission:
    def test_owner(self, service):
        perm = service._role_to_permission("OWNER")
        assert perm.role == "OWNER"
        assert perm.canEdit is True
        assert perm.canDelete is True

    def test_admin(self, service):
        perm = service._role_to_permission("ADMIN")
        # Current code only allows OWNER and WRITER to edit/delete
        assert perm.canEdit is False
        assert perm.canDelete is False

    def test_editor(self, service):
        perm = service._role_to_permission("EDITOR")
        # Current code only allows OWNER and WRITER to edit/delete
        assert perm.canEdit is False
        assert perm.canDelete is False

    def test_writer(self, service):
        perm = service._role_to_permission("WRITER")
        assert perm.canEdit is True
        assert perm.canDelete is True

    def test_reader(self, service):
        perm = service._role_to_permission("READER")
        assert perm.canEdit is False
        assert perm.canDelete is False

    def test_commenter(self, service):
        perm = service._role_to_permission("COMMENTER")
        assert perm.canEdit is False
        assert perm.canDelete is False

    def test_empty_role(self, service):
        perm = service._role_to_permission("")
        assert perm.canEdit is False
        assert perm.canDelete is False

    def test_none_role(self, service):
        # _role_to_permission is only called when role is truthy (non-None)
        # so None input would cause a Pydantic validation error.
        # The caller guards against None, so we test with empty string instead.
        perm = service._role_to_permission("UNKNOWN_ROLE")
        assert perm.canEdit is False
        assert perm.canDelete is False

    def test_lowercase_role(self, service):
        perm = service._role_to_permission("owner")
        assert perm.canEdit is True
        assert perm.canDelete is True


# ============================================================================
# _doc_to_node_item
# ============================================================================
class TestDocToNodeItem:
    def test_connector_record_keeps_its_connector_instance_id(self, service) -> None:
        # The UI marks records from the bundled demo connector by this id; the
        # connector name alone ("SLACK") can't tell demo data from real data.
        doc = {
            "id": "rec1", "name": "#eng-payments thread", "nodeType": "record",
            "origin": "CONNECTOR", "connector": "SLACK", "connectorId": "demo-1",
            "createdAt": 1, "updatedAt": 2, "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.model_dump()["connectorId"] == "demo-1"

    def test_collection_record_sends_a_null_connector_id(self, service) -> None:
        # The nodes routes keep every key and send null for what doesn't apply.
        doc = {
            "id": "rec2", "name": "notes.md", "nodeType": "record", "origin": "COLLECTION",
            "connectorId": None, "createdAt": 1, "updatedAt": 2, "hasChildren": False,
        }
        dumped = service._doc_to_node_item(doc).model_dump()
        assert "connectorId" in dumped and dumped["connectorId"] is None

    def test_full_doc(self, service):
        doc = {
            "id": "node1",
            "name": "Test Node",
            "nodeType": "folder",
            "parentId": "parent1",
            "origin": "COLLECTION",
            "connector": None,
            "recordType": None,
            "indexingStatus": None,
            "createdAt": 1000,
            "updatedAt": 2000,
            "sizeInBytes": 1024,
            "mimeType": "application/vnd.folder",
            "extension": None,
            "webUrl": "http://example.com",
            "hasChildren": True,
            "previewRenderable": False,
            "userRole": "OWNER",
            "sharingStatus": "private",
            "isInternal": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.id == "node1"
        assert item.name == "Test Node"
        assert item.nodeType == NodeType.FOLDER
        assert item.origin == OriginType.COLLECTION
        assert item.permission is not None
        assert item.permission.role == "OWNER"
        assert item.hasChildren is True

    def test_connector_origin(self, service):
        doc = {
            "id": "n2",
            "name": "Test",
            "nodeType": "record",
            "origin": "CONNECTOR",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.origin == OriginType.CONNECTOR

    def test_fallback_id_from_key(self, service):
        doc = {
            "id": "",
            "_key": "key123",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.id == "key123"

    def test_fallback_id_from_arango_id(self, service):
        doc = {
            "_id": "collection/key456",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.id == "key456"

    def test_fallback_id_from_arango_id_no_slash(self, service):
        doc = {
            "_id": "simple_id",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.id == "simple_id"

    def test_no_id_at_all(self, service):
        doc = {
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.id == ""

    def test_invalid_node_type_defaults_to_record(self, service):
        doc = {
            "id": "n1",
            "name": "Test",
            "nodeType": "INVALID_TYPE",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.nodeType == NodeType.RECORD

    def test_user_role_list(self, service):
        doc = {
            "id": "n1",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
            "userRole": ["EDITOR", "READER"],
        }
        item = service._doc_to_node_item(doc)
        assert item.permission.role == "EDITOR"

    def test_user_role_empty_list(self, service):
        doc = {
            "id": "n1",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
            "userRole": [],
        }
        item = service._doc_to_node_item(doc)
        assert item.permission is None

    def test_no_user_role(self, service):
        doc = {
            "id": "n1",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.permission is None

    def test_id_not_string(self, service):
        doc = {
            "id": 12345,
            "_key": "fallback_key",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
        }
        item = service._doc_to_node_item(doc)
        assert item.id == "fallback_key"

    def test_is_internal_true(self, service):
        doc = {
            "id": "n1",
            "name": "Test",
            "nodeType": "record",
            "origin": "COLLECTION",
            "createdAt": 0,
            "updatedAt": 0,
            "hasChildren": False,
            "isInternal": True,
        }
        item = service._doc_to_node_item(doc)
        assert item.isInternal is True



# ============================================================================
# get_nodes - webUrl
# ============================================================================
class TestGetNodesWebUrl:
    @pytest.mark.asyncio
    async def test_gmail_weburl_placeholder_resolved_to_viewer_email(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1", "email": "viewer@acme.com"}
        access = {"grantee_ids": ["uk1"], "gated_app_ids": ["app1"]}
        mock_graph_provider.get_knowledge_hub_access_context_v2.return_value = access
        mock_graph_provider.get_knowledge_hub_access_v3.return_value = {**access, "by_connector": {"app1": []}}
        placeholder_url = "https://mail.google.com/mail?authuser={user.email}#all/m1"

        def row(row_id: str, connector: str) -> dict:
            return {
                "id": row_id, "name": row_id, "nodeType": "record", "sortKey": row_id, "nullRank": 0,
                "parentId": "p1", "parentType": "recordGroup", "parentName": "Parent",
                "origin": "CONNECTOR", "connector": connector, "webUrl": placeholder_url,
                "createdAt": 1, "updatedAt": 2, "hasChildren": False,
            }

        mock_graph_provider.get_knowledge_hub_connector_page_v3.return_value = {
            "rows": [row("r1", "GMAIL WORKSPACE"), row("r2", "DRIVE")],
            "hasMore": False, "total": 2, "counts": None,
            "scope": {
                "admitted": True, "nodeId": "p1",
                "currentNode": {"id": "p1", "name": "Current", "nodeType": "recordGroup"},
                "parentNode": {"id": "app1", "name": "App One", "nodeType": "app"},
                "breadcrumbs": [],
            },
        }
        result = await service.get_nodes(
            user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup"
        )
        assert result.items[0].webUrl == "https://mail.google.com/mail?authuser=viewer@acme.com#all/m1"
        assert result.items[1].webUrl == placeholder_url


# ============================================================================
# _get_permissions
# ============================================================================
class TestGetPermissions:
    @pytest.mark.asyncio
    async def test_returns_permissions(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = {
            "role": "EDITOR",
            "canUpload": True,
            "canCreateFolders": False,
            "canEdit": True,
            "canDelete": False,
            "canManagePermissions": False,
        }
        result = await service._get_permissions("uk1", "o1", "p1")
        assert result is not None
        assert result.role == "EDITOR"

    @pytest.mark.asyncio
    async def test_forwards_parent_type_to_graph_provider(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = {
            "role": "READER",
            "canUpload": False,
            "canCreateFolders": False,
            "canEdit": False,
            "canDelete": False,
            "canManagePermissions": False,
        }
        result = await service._get_permissions("uk1", "o1", "app-key", "app")
        assert result is not None
        mock_graph_provider.get_knowledge_hub_context_permissions.assert_called_once_with(
            user_key="uk1",
            org_id="o1",
            parent_id="app-key",
            parent_type="app",
        )

    @pytest.mark.parametrize("is_org_admin", [True, False])
    @pytest.mark.asyncio
    async def test_root_uses_the_callers_org_role(self, service, mock_graph_provider, is_org_admin):
        # What both providers answer at the root today: the graph User has no role.
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = {
            "role": "MEMBER", "canUpload": False, "canCreateFolders": False,
            "canEdit": False, "canDelete": False, "canManagePermissions": False,
        }
        result = await service._get_permissions("uk1", "o1", None, is_org_admin=is_org_admin)
        assert result.role == ("ADMIN" if is_org_admin else "MEMBER")
        assert result.canUpload is is_org_admin
        assert result.canManagePermissions is is_org_admin
        mock_graph_provider.get_knowledge_hub_context_permissions.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_root_ignores_org_role(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = {
            "role": "READER", "canUpload": False, "canCreateFolders": False,
            "canEdit": False, "canDelete": False, "canManagePermissions": False,
        }
        result = await service._get_permissions("uk1", "o1", "p1", "recordGroup", is_org_admin=True)
        assert result.role == "READER"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_role(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = {"role": None}
        result = await service._get_permissions("uk1", "o1", "p1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_context_permissions.side_effect = RuntimeError("fail")
        result = await service._get_permissions("uk1", "o1", "p1")
        assert result is None


class TestCollectionRole:
    """``collectionRole``: the role the trash list and restore check, at every level of a collection."""

    READER_CONTEXT = {
        "role": "READER",
        "canUpload": False,
        "canCreateFolders": False,
        "canEdit": False,
        "canDelete": False,
        "canManagePermissions": False,
    }

    @pytest.mark.asyncio
    async def test_at_the_collection_it_is_the_users_role_on_it(self, service, mock_graph_provider) -> None:
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = dict(self.READER_CONTEXT)
        mock_graph_provider.get_user_kb_permission = AsyncMock(return_value="FILEORGANIZER")
        result = await service._get_permissions("uk1", "o1", "kb1", "app")
        assert (result.role, result.collectionRole) == ("READER", "FILEORGANIZER")
        mock_graph_provider.get_user_kb_permission.assert_awaited_once_with("kb1", "uk1")

    @pytest.mark.asyncio
    async def test_inside_a_folder_it_is_still_the_collection_role(self, service, mock_graph_provider) -> None:
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = dict(self.READER_CONTEXT)
        mock_graph_provider.get_document = AsyncMock(return_value={"_key": "f1", "orgId": "o1", "connectorId": "kb1"})
        mock_graph_provider.get_user_kb_permission = AsyncMock(return_value="FILEORGANIZER")
        result = await service._get_permissions("uk1", "o1", "f1", "folder")
        assert (result.role, result.collectionRole) == ("READER", "FILEORGANIZER")
        mock_graph_provider.get_user_kb_permission.assert_awaited_once_with("kb1", "uk1")

    @pytest.mark.asyncio
    async def test_a_node_of_another_org_has_none(self, service, mock_graph_provider) -> None:
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = dict(self.READER_CONTEXT)
        mock_graph_provider.get_document = AsyncMock(return_value={"_key": "f1", "orgId": "o2", "connectorId": "kb1"})
        mock_graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        result = await service._get_permissions("uk1", "o1", "f1", "folder")
        assert result.collectionRole is None
        mock_graph_provider.get_user_kb_permission.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("parent_id", "parent_type"), [(None, None), ("rg1", "recordGroup"), ("p1", None)])
    async def test_outside_a_collection_it_is_none(self, service, mock_graph_provider, parent_id, parent_type) -> None:
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = dict(self.READER_CONTEXT)
        mock_graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        result = await service._get_permissions("uk1", "o1", parent_id, parent_type)
        assert result.collectionRole is None
        mock_graph_provider.get_user_kb_permission.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_failed_lookup_keeps_the_other_permissions(self, service, mock_graph_provider) -> None:
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = dict(self.READER_CONTEXT)
        mock_graph_provider.get_user_kb_permission = AsyncMock(side_effect=RuntimeError("graph down"))
        result = await service._get_permissions("uk1", "o1", "kb1", "app")
        assert result is not None
        assert (result.role, result.collectionRole) == ("READER", None)


# ============================================================================
# _get_available_filters
# ============================================================================
class TestGetAvailableFilters:
    @pytest.mark.asyncio
    async def test_lists_only_sources_the_user_can_open(self, service, mock_graph_provider):
        """The v2 gate decides, and reachable collections are listed too.

        A source with no `orgId` predicate and no notion of a grant onto the
        App could name another org's App and miss one the user reaches through
        a group.
        """
        mock_graph_provider.get_knowledge_hub_access_context_v2.return_value = {
            "grantee_ids": ["uk1"],
            "gated_app_ids": ["app1", "kb-1"],
        }
        mock_graph_provider.get_knowledge_hub_root_nodes_v2.return_value = {
            "partitions": [{"rows": [
                {"id": "app1", "name": "App 1", "connector": "google_drive",
                 "origin": "CONNECTOR"},
                {"id": "kb-1", "name": "Team KB", "connector": "KB",
                 "origin": "COLLECTION"},
            ]}],
            "scope": None,
        }

        result = await service._get_available_filters("uk1", "o1")

        assert isinstance(result, AvailableFilters)
        assert [option.id for option in result.connectors] == ["app1", "kb-1"]
        assert result.connectors[1].label == "Team KB"
        gated = mock_graph_provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs
        assert gated["user_app_ids"] == ["app1", "kb-1"]
        mock_graph_provider.get_knowledge_hub_filter_options.assert_not_called()
        assert len(result.nodeTypes) == len(list(NodeType))

    @pytest.mark.asyncio
    async def test_a_user_with_no_gated_sources_gets_no_connectors(
        self, service, mock_graph_provider
    ):
        mock_graph_provider.get_knowledge_hub_access_context_v2.return_value = {
            "grantee_ids": ["uk1"],
            "gated_app_ids": [],
        }
        result = await service._get_available_filters("uk1", "o1")
        assert result.connectors == []
        mock_graph_provider.get_knowledge_hub_root_nodes_v2.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_access_context_v2.side_effect = RuntimeError("fail")
        result = await service._get_available_filters("uk1", "o1")
        assert isinstance(result, AvailableFilters)
        assert result.connectors == []

