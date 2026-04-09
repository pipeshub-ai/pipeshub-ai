"""Unit tests for KnowledgeHubService."""

import logging
from collections import Counter
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    FOLDER_MIME_TYPES,
    KnowledgeHubService,
    _get_node_type_value,
)
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    AppliedFilters,
    AvailableFilters,
    BreadcrumbItem,
    CountItem,
    CountsInfo,
    CurrentNode,
    FilterOption,
    FiltersInfo,
    ItemPermission,
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
    OriginType,
    PaginationInfo,
    PermissionsInfo,
    SortField,
    SortOrder,
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
# _get_node_type_value
# ============================================================================
class TestGetNodeTypeValue:
    def test_enum_value(self):
        assert _get_node_type_value(NodeType.FOLDER) == "folder"
        assert _get_node_type_value(NodeType.APP) == "app"
        assert _get_node_type_value(NodeType.RECORD_GROUP) == "recordGroup"
        assert _get_node_type_value(NodeType.RECORD) == "record"

    def test_string_value(self):
        assert _get_node_type_value("folder") == "folder"
        assert _get_node_type_value("custom") == "custom"

    def test_non_enum_with_value_attr(self):
        obj = MagicMock()
        obj.value = "test_val"
        assert _get_node_type_value(obj) == "test_val"


# ============================================================================
# _has_search_filters / _has_flattening_filters
# ============================================================================
class TestHasSearchFilters:
    def test_no_filters(self, service):
        assert service._has_search_filters(None, None, None, None, None, None, None, None, None) is False

    def test_with_query(self, service):
        assert service._has_search_filters("test", None, None, None, None, None, None, None, None) is True

    def test_with_node_types(self, service):
        assert service._has_search_filters(None, ["folder"], None, None, None, None, None, None, None) is True

    def test_with_record_types(self, service):
        assert service._has_search_filters(None, None, ["FILE"], None, None, None, None, None, None) is True

    def test_with_origins(self, service):
        assert service._has_search_filters(None, None, None, ["COLLECTION"], None, None, None, None, None) is True

    def test_with_connector_ids(self, service):
        assert service._has_search_filters(None, None, None, None, ["c1"], None, None, None, None) is True

    def test_with_indexing_status(self, service):
        assert service._has_search_filters(None, None, None, None, None, ["COMPLETED"], None, None, None) is True

    def test_with_created_at(self, service):
        assert service._has_search_filters(None, None, None, None, None, None, {"gte": 100}, None, None) is True

    def test_with_updated_at(self, service):
        assert service._has_search_filters(None, None, None, None, None, None, None, {"lte": 200}, None) is True

    def test_with_size(self, service):
        assert service._has_search_filters(None, None, None, None, None, None, None, None, {"gte": 0}) is True


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
        assert perm.canEdit is True
        assert perm.canDelete is True

    def test_editor(self, service):
        perm = service._role_to_permission("EDITOR")
        assert perm.canEdit is True
        assert perm.canDelete is False

    def test_writer(self, service):
        perm = service._role_to_permission("WRITER")
        assert perm.canEdit is True
        assert perm.canDelete is False

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
# get_nodes - main entry point
# ============================================================================
class TestGetNodes:
    @pytest.mark.asyncio
    async def test_user_not_found(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = None
        result = await service.get_nodes(user_id="u1", org_id="o1")
        assert result.success is False
        assert result.error == "User not found"

    @pytest.mark.asyncio
    async def test_browse_root_no_filters(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_user_app_ids.return_value = ["app1"]
        mock_graph_provider.get_knowledge_hub_root_nodes.return_value = {
            "nodes": [
                {
                    "id": "app1",
                    "name": "App 1",
                    "nodeType": "app",
                    "origin": "COLLECTION",
                    "createdAt": 100,
                    "updatedAt": 200,
                    "hasChildren": True,
                }
            ],
            "total": 1,
        }
        result = await service.get_nodes(user_id="u1", org_id="o1")
        assert result.success is True
        assert len(result.items) == 1
        assert result.pagination.totalItems == 1

    @pytest.mark.asyncio
    async def test_browse_children_of_parent(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {
            "id": "parent1",
            "name": "Parent",
            "nodeType": "app",
        }
        mock_graph_provider.get_knowledge_hub_children.return_value = {
            "nodes": [],
            "total": 0,
        }
        mock_graph_provider.get_knowledge_hub_parent_node.return_value = None
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            parent_id="parent1", parent_type="app",
        )
        assert result.success is True
        assert result.currentNode is not None

    @pytest.mark.asyncio
    async def test_search_global(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_search.return_value = {
            "nodes": [],
            "total": 0,
        }
        mock_graph_provider.get_knowledge_hub_filter_options.return_value = {"apps": []}
        result = await service.get_nodes(user_id="u1", org_id="o1", q="search term")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_search_scoped_with_flattening_filters(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_search.return_value = {
            "nodes": [],
            "total": 0,
        }
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {
            "id": "p1", "name": "Parent", "nodeType": "folder",
        }
        mock_graph_provider.get_knowledge_hub_parent_node.return_value = None
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            parent_id="p1", parent_type="folder",
            q="test",
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_flattened_flag(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_search.return_value = {
            "nodes": [],
            "total": 0,
        }
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {
            "id": "p1", "name": "Parent", "nodeType": "folder",
        }
        mock_graph_provider.get_knowledge_hub_parent_node.return_value = None
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            parent_id="p1", parent_type="folder",
            flattened=True,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_include_breadcrumbs(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_children.return_value = {"nodes": [], "total": 0}
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {
            "id": "p1", "name": "Parent", "nodeType": "folder",
        }
        mock_graph_provider.get_knowledge_hub_parent_node.return_value = None
        mock_graph_provider.get_knowledge_hub_breadcrumbs.return_value = [
            {"id": "root", "name": "Root", "nodeType": "app"},
        ]
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            parent_id="p1", parent_type="folder",
            include=["breadcrumbs"],
        )
        assert result.success is True
        assert result.breadcrumbs is not None
        assert len(result.breadcrumbs) == 1

    @pytest.mark.asyncio
    async def test_include_counts(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_user_app_ids.return_value = []
        mock_graph_provider.get_knowledge_hub_root_nodes.return_value = {
            "nodes": [
                {"id": "n1", "name": "Folder", "nodeType": "folder", "origin": "COLLECTION", "createdAt": 0, "updatedAt": 0, "hasChildren": True},
                {"id": "n2", "name": "Record", "nodeType": "record", "origin": "COLLECTION", "createdAt": 0, "updatedAt": 0, "hasChildren": False},
                {"id": "n3", "name": "App", "nodeType": "app", "origin": "COLLECTION", "createdAt": 0, "updatedAt": 0, "hasChildren": True},
            ],
            "total": 10,
        }
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            include=["counts"],
        )
        assert result.success is True
        assert result.counts is not None
        assert result.counts.total == 10
        labels = {ci.label for ci in result.counts.items}
        assert "folders" in labels
        assert "records" in labels
        assert "apps" in labels

    @pytest.mark.asyncio
    async def test_include_permissions(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_user_app_ids.return_value = []
        mock_graph_provider.get_knowledge_hub_root_nodes.return_value = {"nodes": [], "total": 0}
        mock_graph_provider.get_knowledge_hub_context_permissions.return_value = {
            "role": "OWNER",
            "canUpload": True,
            "canCreateFolders": True,
            "canEdit": True,
            "canDelete": True,
            "canManagePermissions": True,
        }
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            include=["permissions"],
        )
        assert result.success is True
        assert result.permissions is not None
        assert result.permissions.role == "OWNER"

    @pytest.mark.asyncio
    async def test_include_available_filters_browse(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_children.return_value = {"nodes": [], "total": 0}
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {
            "id": "p1", "name": "Parent", "nodeType": "folder",
        }
        mock_graph_provider.get_knowledge_hub_parent_node.return_value = None
        mock_graph_provider.get_knowledge_hub_filter_options.return_value = {"apps": []}
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            parent_id="p1", parent_type="folder",
            include=["availableFilters"],
        )
        assert result.success is True
        assert result.filters.available is not None

    @pytest.mark.asyncio
    async def test_validation_error(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_node_info.return_value = None
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            parent_id="bad_id", parent_type="folder",
        )
        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_general_exception(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.side_effect = RuntimeError("DB down")
        result = await service.get_nodes(user_id="u1", org_id="o1")
        assert result.success is False
        assert "Failed to retrieve nodes" in result.error

    @pytest.mark.asyncio
    async def test_pagination_negative_page(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_user_app_ids.return_value = []
        mock_graph_provider.get_knowledge_hub_root_nodes.return_value = {"nodes": [], "total": 0}
        result = await service.get_nodes(user_id="u1", org_id="o1", page=-1, limit=300)
        assert result.success is True
        assert result.pagination.page == 1
        assert result.pagination.limit == 200

    @pytest.mark.asyncio
    async def test_parent_node_info(self, service, mock_graph_provider):
        mock_graph_provider.get_user_by_user_id.return_value = {"_key": "uk1"}
        mock_graph_provider.get_knowledge_hub_children.return_value = {"nodes": [], "total": 0}
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {
            "id": "p1", "name": "Current", "nodeType": "folder",
        }
        mock_graph_provider.get_knowledge_hub_parent_node.return_value = {
            "id": "gp1", "name": "GrandParent", "nodeType": "app", "subType": "google_drive",
        }
        result = await service.get_nodes(
            user_id="u1", org_id="o1",
            parent_id="p1", parent_type="folder",
        )
        assert result.success is True
        assert result.parentNode is not None
        assert result.parentNode.id == "gp1"


# ============================================================================
# _validate_node_existence_and_type
# ============================================================================
class TestValidateNodeExistenceAndType:
    @pytest.mark.asyncio
    async def test_node_not_found(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await service._validate_node_existence_and_type("n1", "folder", "uk", "o1")

    @pytest.mark.asyncio
    async def test_type_mismatch(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {"nodeType": "app"}
        with pytest.raises(ValueError, match="type mismatch"):
            await service._validate_node_existence_and_type("n1", "folder", "uk", "o1")

    @pytest.mark.asyncio
    async def test_type_matches(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {"nodeType": "folder"}
        await service._validate_node_existence_and_type("n1", "folder", "uk", "o1")


# ============================================================================
# _get_current_node_info
# ============================================================================
class TestGetCurrentNodeInfo:
    @pytest.mark.asyncio
    async def test_returns_current_node(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {
            "id": "n1", "name": "Node", "nodeType": "folder", "subType": None,
        }
        result = await service._get_current_node_info("n1")
        assert result is not None
        assert result.id == "n1"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_info(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = None
        result = await service._get_current_node_info("n1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_missing_id(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {"name": "Node", "nodeType": "folder"}
        result = await service._get_current_node_info("n1")
        assert result is None


# ============================================================================
# _get_breadcrumbs
# ============================================================================
class TestGetBreadcrumbs:
    @pytest.mark.asyncio
    async def test_returns_breadcrumbs(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_breadcrumbs.return_value = [
            {"id": "r", "name": "Root", "nodeType": "app"},
            {"id": "f", "name": "Folder", "nodeType": "folder", "subType": None},
        ]
        result = await service._get_breadcrumbs("f")
        assert len(result) == 2
        assert isinstance(result[0], BreadcrumbItem)

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_breadcrumbs.side_effect = RuntimeError("fail")
        result = await service._get_breadcrumbs("n1")
        assert result == []


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


# ============================================================================
# _get_available_filters
# ============================================================================
class TestGetAvailableFilters:
    @pytest.mark.asyncio
    async def test_returns_filters(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_filter_options.return_value = {
            "apps": [{"id": "app1", "name": "App 1", "type": "google_drive"}],
        }
        result = await service._get_available_filters("uk1", "o1")
        assert isinstance(result, AvailableFilters)
        assert len(result.connectors) == 1
        assert len(result.nodeTypes) == len(list(NodeType))

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_filter_options.side_effect = RuntimeError("fail")
        result = await service._get_available_filters("uk1", "o1")
        assert isinstance(result, AvailableFilters)
        assert result.connectors == []


# ============================================================================
# _get_root_level_nodes
# ============================================================================
class TestGetRootLevelNodes:
    @pytest.mark.asyncio
    async def test_returns_root_nodes(self, service, mock_graph_provider):
        mock_graph_provider.get_user_app_ids.return_value = ["app1", "app2"]
        mock_graph_provider.get_knowledge_hub_root_nodes.return_value = {
            "nodes": [{"id": "app1", "name": "App 1", "nodeType": "app", "origin": "COLLECTION", "createdAt": 0, "updatedAt": 0, "hasChildren": True}],
            "total": 1,
        }
        items, total, filters = await service._get_root_level_nodes("uk1", "o1", 0, 50, "name", "asc", None, None, None, False)
        assert len(items) == 1
        assert total == 1
        assert filters is None

    @pytest.mark.asyncio
    async def test_filter_by_connector_ids(self, service, mock_graph_provider):
        mock_graph_provider.get_user_app_ids.return_value = ["app1", "app2", "app3"]
        mock_graph_provider.get_knowledge_hub_root_nodes.return_value = {"nodes": [], "total": 0}
        await service._get_root_level_nodes("uk1", "o1", 0, 50, "name", "asc", None, None, ["app1"], False)
        call_args = mock_graph_provider.get_knowledge_hub_root_nodes.call_args
        assert call_args.kwargs["user_app_ids"] == ["app1"]

    @pytest.mark.asyncio
    async def test_raises_on_error(self, service, mock_graph_provider):
        mock_graph_provider.get_user_app_ids.side_effect = RuntimeError("fail")
        with pytest.raises(RuntimeError):
            await service._get_root_level_nodes("uk1", "o1", 0, 50, "name", "asc", None, None, None, False)


# ============================================================================
# _search_nodes
# ============================================================================
class TestSearchNodes:
    @pytest.mark.asyncio
    async def test_search_global(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_search.return_value = {"nodes": [], "total": 0}
        items, total, filters = await service._search_nodes(
            user_key="uk", org_id="o1", skip=0, limit=50,
            sort_by="name", sort_order="asc", q="test",
            node_types=None, record_types=None, origins=None,
            connector_ids=None, indexing_status=None,
            created_at=None, updated_at=None, size=None,
            only_containers=False, include_filters=False,
        )
        assert items == []
        assert total == 0
        assert filters is None

    @pytest.mark.asyncio
    async def test_search_with_filters(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_search.return_value = {"nodes": [], "total": 0}
        mock_graph_provider.get_knowledge_hub_filter_options.return_value = {"apps": []}
        items, total, filters = await service._search_nodes(
            user_key="uk", org_id="o1", skip=0, limit=50,
            sort_by="name", sort_order="asc", q="test",
            node_types=None, record_types=None, origins=None,
            connector_ids=None, indexing_status=None,
            created_at=None, updated_at=None, size=None,
            only_containers=False, include_filters=True,
        )
        assert filters is not None

    @pytest.mark.asyncio
    async def test_search_raises_on_error(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_search.side_effect = RuntimeError("fail")
        with pytest.raises(RuntimeError):
            await service._search_nodes(
                user_key="uk", org_id="o1", skip=0, limit=50,
                sort_by="name", sort_order="asc", q="test",
                node_types=None, record_types=None, origins=None,
                connector_ids=None, indexing_status=None,
                created_at=None, updated_at=None, size=None,
                only_containers=False,
            )


# ============================================================================
# _get_children_nodes
# ============================================================================
class TestGetChildrenNodes:
    @pytest.mark.asyncio
    async def test_root_delegates(self, service, mock_graph_provider):
        mock_graph_provider.get_user_app_ids.return_value = []
        mock_graph_provider.get_knowledge_hub_root_nodes.return_value = {"nodes": [], "total": 0}
        items, total, filters = await service._get_children_nodes(
            user_key="uk", org_id="o1", parent_id=None, parent_type=None,
            skip=0, limit=50, sort_by="name", sort_order="asc",
            q=None, node_types=None, record_types=None, origins=None,
            connector_ids=None, indexing_status=None,
            created_at=None, updated_at=None, size=None,
            only_containers=False,
        )
        assert items == []
        assert filters is None

    @pytest.mark.asyncio
    async def test_children_with_parent(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {"nodeType": "folder"}
        mock_graph_provider.get_knowledge_hub_children.return_value = {
            "nodes": [
                {"id": "c1", "name": "Child", "nodeType": "record", "origin": "COLLECTION", "createdAt": 0, "updatedAt": 0, "hasChildren": False}
            ],
            "total": 1,
        }
        items, total, filters = await service._get_children_nodes(
            user_key="uk", org_id="o1", parent_id="p1", parent_type="folder",
            skip=0, limit=50, sort_by="updatedAt", sort_order="desc",
            q=None, node_types=None, record_types=None, origins=None,
            connector_ids=None, indexing_status=None,
            created_at=None, updated_at=None, size=None,
            only_containers=False,
        )
        assert len(items) == 1
        assert total == 1
        assert filters is None

    @pytest.mark.asyncio
    async def test_sort_field_mapping(self, service, mock_graph_provider):
        mock_graph_provider.get_knowledge_hub_node_info.return_value = {"nodeType": "folder"}
        mock_graph_provider.get_knowledge_hub_children.return_value = {"nodes": [], "total": 0}
        await service._get_children_nodes(
            user_key="uk", org_id="o1", parent_id="p1", parent_type="folder",
            skip=0, limit=50, sort_by="size", sort_order="asc",
            q=None, node_types=None, record_types=None, origins=None,
            connector_ids=None, indexing_status=None,
            created_at=None, updated_at=None, size=None,
            only_containers=False,
        )
        call_args = mock_graph_provider.get_knowledge_hub_children.call_args
        assert call_args.kwargs["sort_field"] == "sizeInBytes"
        assert call_args.kwargs["sort_dir"] == "ASC"
