"""Extended tests for LocalKB services: KnowledgeBaseService, KnowledgeHubService, MigrationService."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
    _get_node_type_value,
)
from app.connectors.sources.localKB.handlers.migration_service import (
    KnowledgeBaseMigrationService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kb_service():
    logger = MagicMock()
    graph_provider = AsyncMock()
    kafka_service = MagicMock()
    return KnowledgeBaseService(logger=logger, graph_provider=graph_provider, kafka_service=kafka_service)


def _make_knowledge_hub_service():
    logger = MagicMock()
    graph_provider = AsyncMock()
    return KnowledgeHubService(logger=logger, graph_provider=graph_provider)


def _make_user(user_id="user-key-1", full_name="Test User"):
    return {"id": user_id, "_key": user_id, "fullName": full_name, "firstName": "Test", "lastName": "User"}


# ===========================================================================
# KnowledgeBaseService - create_knowledge_base
# ===========================================================================

class TestCreateKnowledgeBase:
    async def test_create_kb_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.begin_transaction = AsyncMock(return_value="txn-1")
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.commit_transaction = AsyncMock()
        result = await svc.create_knowledge_base("user-1", "org-1", "My KB")
        assert result["success"] is True
        assert result["name"] == "My KB"
        assert result["userRole"] == "OWNER"

    async def test_create_kb_user_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await svc.create_knowledge_base("bad-user", "org-1", "KB")
        assert result["success"] is False
        assert result["code"] == 404

    async def test_create_kb_transaction_failure(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.begin_transaction = AsyncMock(side_effect=Exception("tx error"))
        result = await svc.create_knowledge_base("user-1", "org-1", "KB")
        assert result["success"] is False
        assert result["code"] == 500

    async def test_create_kb_exception_with_rollback(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.begin_transaction = AsyncMock(return_value="txn-1")
        svc.graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("db error"))
        svc.graph_provider.rollback_transaction = AsyncMock()
        result = await svc.create_knowledge_base("user-1", "org-1", "KB")
        assert result["success"] is False
        svc.graph_provider.rollback_transaction.assert_called_once()

    async def test_create_kb_rollback_failure(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.begin_transaction = AsyncMock(return_value="txn-1")
        svc.graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("db error"))
        svc.graph_provider.rollback_transaction = AsyncMock(side_effect=Exception("rb fail"))
        result = await svc.create_knowledge_base("user-1", "org-1", "KB")
        assert result["success"] is False

    async def test_create_kb_user_name_fallback(self):
        """User without fullName uses firstName+lastName."""
        svc = _make_kb_service()
        user = {"id": "u1", "_key": "u1", "fullName": "", "firstName": "John", "lastName": "Doe"}
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=user)
        svc.graph_provider.begin_transaction = AsyncMock(return_value="txn-1")
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.commit_transaction = AsyncMock()
        result = await svc.create_knowledge_base("user-1", "org-1", "KB")
        assert result["success"] is True


# ===========================================================================
# KnowledgeBaseService - get_knowledge_base
# ===========================================================================

class TestGetKnowledgeBase:
    async def test_get_kb_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.get_knowledge_base = AsyncMock(return_value={"id": "kb-1", "name": "KB"})
        result = await svc.get_knowledge_base("kb-1", "user-1")
        assert result["name"] == "KB"

    async def test_get_kb_user_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await svc.get_knowledge_base("kb-1", "bad-user")
        assert result["success"] is False
        assert result["code"] == "500" or result["code"] == 404

    async def test_get_kb_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
        result = await svc.get_knowledge_base("kb-1", "user-1")
        assert result["success"] is False

    async def test_get_kb_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.get_knowledge_base = AsyncMock(return_value=None)
        result = await svc.get_knowledge_base("kb-1", "user-1")
        assert result["success"] is False

    async def test_get_kb_exception(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("error"))
        result = await svc.get_knowledge_base("kb-1", "user-1")
        assert result["success"] is False


# ===========================================================================
# KnowledgeBaseService - list_user_knowledge_bases
# ===========================================================================

class TestListKnowledgeBases:
    async def test_list_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=([{"id": "kb-1"}], 1, {"roles": ["OWNER"]})
        )
        result = await svc.list_user_knowledge_bases("user-1", "org-1")
        assert result["pagination"]["totalCount"] == 1

    async def test_list_user_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await svc.list_user_knowledge_bases("bad-user", "org-1")
        assert result["success"] is False

    async def test_list_with_filters(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=([{"id": "kb-1"}], 1, {})
        )
        result = await svc.list_user_knowledge_bases(
            "user-1", "org-1", search="test", permissions=["OWNER"],
            sort_by="updatedAtTimestamp", sort_order="desc",
        )
        assert "filters" in result
        assert result["filters"]["applied"]["search"] == "test"

    async def test_list_invalid_sort_field(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=([], 0, {})
        )
        result = await svc.list_user_knowledge_bases(
            "user-1", "org-1", sort_by="invalid_field", sort_order="invalid",
        )
        assert result["pagination"]["totalCount"] == 0

    async def test_list_returns_error_from_provider(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=({"success": False, "reason": "error"}, 0, {})
        )
        result = await svc.list_user_knowledge_bases("user-1", "org-1")
        assert result.get("success") is False

    async def test_list_exception(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await svc.list_user_knowledge_bases("user-1", "org-1")
        assert result["success"] is False


# ===========================================================================
# KnowledgeBaseService - update_knowledge_base
# ===========================================================================

class TestUpdateKnowledgeBase:
    async def test_update_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.update_knowledge_base = AsyncMock(return_value=True)
        result = await svc.update_knowledge_base("kb-1", "user-1", {"groupName": "New Name"})
        assert result["success"] is True

    async def test_update_user_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await svc.update_knowledge_base("kb-1", "bad-user", {})
        assert result["success"] is False

    async def test_update_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        result = await svc.update_knowledge_base("kb-1", "user-1", {})
        assert result["success"] is False

    async def test_update_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.update_knowledge_base = AsyncMock(return_value=None)
        result = await svc.update_knowledge_base("kb-1", "user-1", {})
        assert result["success"] is False

    async def test_update_exception(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await svc.update_knowledge_base("kb-1", "user-1", {})
        assert result["success"] is False


# ===========================================================================
# KnowledgeBaseService - delete_knowledge_base
# ===========================================================================

class TestDeleteKnowledgeBase:
    async def test_delete_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.delete_knowledge_base = AsyncMock(
            return_value={"success": True, "eventData": {"records": []}}
        )
        result = await svc.delete_knowledge_base("kb-1", "user-1")
        assert result["success"] is True

    async def test_delete_user_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await svc.delete_knowledge_base("kb-1", "bad-user")
        assert result["success"] is False

    async def test_delete_not_owner(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        result = await svc.delete_knowledge_base("kb-1", "user-1")
        assert result["success"] is False

    async def test_delete_failure(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.delete_knowledge_base = AsyncMock(return_value={"success": False})
        result = await svc.delete_knowledge_base("kb-1", "user-1")
        assert result["success"] is False

    async def test_delete_exception(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(side_effect=ValueError("error"))
        result = await svc.delete_knowledge_base("kb-1", "user-1")
        assert result["success"] is False


# ===========================================================================
# KnowledgeBaseService - folder operations
# ===========================================================================

class TestFolderOperations:
    async def test_create_folder_success(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        svc.graph_provider.create_folder = AsyncMock(return_value={"success": True, "id": "f1"})
        result = await svc.create_folder_in_kb("kb-1", "Folder", "user-1", "org-1")
        assert result["success"] is True

    async def test_create_folder_validation_fails(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(
            return_value={"valid": False, "success": False, "code": 403, "reason": "No perm"}
        )
        result = await svc.create_folder_in_kb("kb-1", "Folder", "user-1", "org-1")
        assert result["success"] is False

    async def test_create_folder_name_conflict(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "existing"})
        result = await svc.create_folder_in_kb("kb-1", "Folder", "user-1", "org-1")
        assert result["code"] == 409

    async def test_create_folder_exception(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(side_effect=Exception("err"))
        result = await svc.create_folder_in_kb("kb-1", "Folder", "user-1", "org-1")
        assert result["success"] is False

    async def test_create_nested_folder_success(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        svc.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        svc.graph_provider.create_folder = AsyncMock(return_value={"success": True})
        result = await svc.create_nested_folder("kb-1", "parent-f", "Sub", "user-1", "org-1")
        assert result["success"] is True

    async def test_create_nested_folder_parent_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        svc.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)
        result = await svc.create_nested_folder("kb-1", "bad-parent", "Sub", "user-1", "org-1")
        assert result["code"] == 404

    async def test_get_folder_contents_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.get_folder_contents = AsyncMock(return_value={"items": []})
        result = await svc.get_folder_contents("kb-1", "f1", "user-1")
        assert "items" in result

    async def test_get_folder_contents_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
        result = await svc.get_folder_contents("kb-1", "f1", "user-1")
        assert result["success"] is False

    async def test_get_folder_contents_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.get_folder_contents = AsyncMock(return_value=None)
        result = await svc.get_folder_contents("kb-1", "f1", "user-1")
        assert result["success"] is False

    async def test_update_folder_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        svc.graph_provider.update_folder = AsyncMock(return_value=True)
        result = await svc.updateFolder("f1", "kb-1", "user-1", "New Name")
        assert result["success"] is True

    async def test_update_folder_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        result = await svc.updateFolder("f1", "kb-1", "user-1", "New Name")
        assert result["success"] is False

    async def test_update_folder_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)
        result = await svc.updateFolder("f1", "kb-1", "user-1", "New Name")
        assert result["success"] is False

    async def test_update_folder_name_conflict(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "x"})
        result = await svc.updateFolder("f1", "kb-1", "user-1", "Duplicate")
        assert result["code"] == 409

    async def test_delete_folder_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        svc.graph_provider.delete_folder = AsyncMock(return_value={"success": True, "eventData": {}})
        result = await svc.delete_folder("kb-1", "f1", "user-1")
        assert result["success"] is True

    async def test_delete_folder_not_owner(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        result = await svc.delete_folder("kb-1", "f1", "user-1")
        assert result["success"] is False

    async def test_delete_folder_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)
        result = await svc.delete_folder("kb-1", "f1", "user-1")
        assert result["success"] is False


# ===========================================================================
# KnowledgeBaseService - record operations
# ===========================================================================

class TestRecordOperations:
    async def test_update_record_success(self):
        svc = _make_kb_service()
        svc.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb-1"})
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.update_record = AsyncMock(return_value={"success": True})
        result = await svc.update_record("user-1", "rec-1", {"name": "new"})
        assert result["success"] is True

    async def test_update_record_no_kb_context(self):
        svc = _make_kb_service()
        svc.graph_provider._get_kb_context_for_record = AsyncMock(return_value=None)
        result = await svc.update_record("user-1", "rec-1", {})
        assert result["success"] is False

    async def test_update_record_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb-1"})
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        result = await svc.update_record("user-1", "rec-1", {})
        assert result["success"] is False

    async def test_delete_records_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.delete_records = AsyncMock(return_value={"success": True})
        result = await svc.delete_records_in_kb("kb-1", ["r1", "r2"], "user-1")
        assert result["success"] is True

    async def test_delete_records_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=_make_user())
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        result = await svc.delete_records_in_kb("kb-1", ["r1"], "user-1")
        assert result["success"] is False

    async def test_delete_records_exception(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await svc.delete_records_in_kb("kb-1", ["r1"], "user-1")
        assert result["success"] is False


# ===========================================================================
# KnowledgeHubService
# ===========================================================================

class TestKnowledgeHubService:
    def test_get_node_type_value_enum(self):
        mock_enum = MagicMock()
        mock_enum.value = "recordGroup"
        assert _get_node_type_value(mock_enum) == "recordGroup"

    def test_get_node_type_value_string(self):
        assert _get_node_type_value("record") == "record"

    def test_has_search_filters_none(self):
        svc = _make_knowledge_hub_service()
        assert svc._has_search_filters(None, None, None, None, None, None, None, None, None) is False

    def test_has_search_filters_with_query(self):
        svc = _make_knowledge_hub_service()
        assert svc._has_search_filters("search", None, None, None, None, None, None, None, None) is True

    def test_has_flattening_filters_with_size(self):
        svc = _make_knowledge_hub_service()
        assert svc._has_flattening_filters(None, None, None, None, None, None, None, None, {"gte": 100}) is True

    async def test_get_nodes_user_not_found(self):
        svc = _make_knowledge_hub_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await svc.get_nodes("user-1", "org-1")
        assert result.success is False


# ===========================================================================
# KnowledgeBaseMigrationService
# ===========================================================================

class TestMigrationService:
    def _make_migration_service(self):
        arango_service = MagicMock()
        arango_service.db = MagicMock()
        logger = MagicMock()
        return KnowledgeBaseMigrationService(arango_service=arango_service, logger=logger)

    async def test_validate_no_old_collection(self):
        svc = self._make_migration_service()
        svc.db.collections.return_value = [{"name": "recordGroups"}]
        result = await svc._validate_migration_preconditions()
        assert result["success"] is True
        assert result["migration_needed"] is False

    async def test_validate_missing_new_collections(self):
        svc = self._make_migration_service()
        svc.db.collections.return_value = [{"name": "knowledgeBase"}]
        with pytest.raises(Exception, match="Required new collections missing"):
            await svc._validate_migration_preconditions()

    async def test_validate_success(self):
        svc = self._make_migration_service()
        svc.db.collections.return_value = [
            {"name": "knowledgeBase"},
            {"name": "recordGroups"},
            {"name": "permission"},
            {"name": "belongsTo"},
            {"name": "users"},
            {"name": "records"},
        ]
        result = await svc._validate_migration_preconditions()
        assert result["success"] is True
        assert result["migration_needed"] is True

    async def test_run_migration_no_migration_needed(self):
        svc = self._make_migration_service()
        svc.db.collections.return_value = [{"name": "recordGroups"}]
        result = await svc.run_migration()
        assert result["success"] is True
        assert result["migrated_count"] == 0

    async def test_run_migration_exception(self):
        svc = self._make_migration_service()
        svc.db.collections.return_value = []
        # Force exception in _validate_migration_preconditions
        svc.db.collections.side_effect = Exception("db error")
        result = await svc.run_migration()
        assert result["success"] is False

    async def test_migrate_data_empty_kbs(self):
        svc = self._make_migration_service()
        result = await svc._migrate_data({"old_kbs": []})
        assert result["success"] is True
        assert result["migrated_count"] == 0
