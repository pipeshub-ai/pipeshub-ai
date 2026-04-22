"""
Unit tests for KnowledgeBaseService (app/connectors/sources/localKB/handlers/kb_service.py).

Covers:
- create_knowledge_base (success, user not found, tx failure, exception)
- get_knowledge_base (success, user not found, no permission, not found, exception)
- list_user_knowledge_bases (success, user not found, sort validation, exception)
- update_knowledge_base (success, user not found, no permission, not found, exception)
- delete_knowledge_base (success, user not found, no permission, exception)
- create_folder_in_kb (success, validation fail, name conflict, exception)
- create_nested_folder (success, validation fail, parent not found, name conflict, exception)
- get_folder_contents (success, user not found, no permission, not found, exception)
- updateFolder (success, user not found, no permission, folder not found, name conflict, exception)
- delete_folder (success, user not found, no permission, folder not found, exception)
- update_record (success, no kb context, user not found, no permission, exception)
- delete_records_in_kb (success, user not found, no permission, exception)
- delete_records_in_folder (success, user not found, no permission, folder not found, exception)
- create_kb_permissions (success, empty input, invalid role, exception)
- update_kb_permission (various validation paths)
- remove_kb_permission (various validation paths)
- list_kb_permissions (success, user not found, no permission, exception)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_graph_provider():
    return AsyncMock()


@pytest.fixture
def mock_kafka_service():
    return AsyncMock()


@pytest.fixture
def service(mock_logger, mock_graph_provider, mock_kafka_service):
    return KnowledgeBaseService(mock_logger, mock_graph_provider, mock_kafka_service)


# ===========================================================================
# create_knowledge_base
# ===========================================================================


class TestCreateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "_key": "uk1", "fullName": "Test User"
        })
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        service.graph_provider.batch_create_edges = AsyncMock(return_value=True)
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is True
        assert result["name"] == "My KB"
        assert result["userRole"] == "OWNER"

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_transaction_creation_fails(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "fullName": "Test"
        })
        service.graph_provider.begin_transaction = AsyncMock(side_effect=Exception("tx error"))

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_db_operation_fails_rollback(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "fullName": "Test"
        })
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("insert error"))
        service.graph_provider.rollback_transaction = AsyncMock()

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        service.graph_provider.rollback_transaction.assert_called_once_with("txn1")


# ===========================================================================
# get_knowledge_base
# ===========================================================================


class TestGetKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1"
        })
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_knowledge_base = AsyncMock(return_value={
            "id": "kb1", "name": "KB"
        })

        result = await service.get_knowledge_base("kb1", "user1")
        assert result["id"] == "kb1"

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.get_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await service.get_knowledge_base("kb1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_knowledge_base = AsyncMock(return_value=None)

        result = await service.get_knowledge_base("kb1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))

        result = await service.get_knowledge_base("kb1", "user1")
        assert result["success"] is False


# ===========================================================================
# list_user_knowledge_bases
# ===========================================================================


class TestListUserKnowledgeBases:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=(
            [{"id": "kb1", "name": "KB1"}],
            1,
            {"permissions": ["OWNER", "READER"]},
        ))

        result = await service.list_user_knowledge_bases("user1", "org1")
        assert len(result["knowledgeBases"]) == 1
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.list_user_knowledge_bases("user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_invalid_sort_by_defaults(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=([], 0, {}))

        result = await service.list_user_knowledge_bases(
            "user1", "org1", sort_by="invalid_field", sort_order="invalid"
        )
        assert "knowledgeBases" in result

    @pytest.mark.asyncio
    async def test_pagination_metadata(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=(
            [{"id": f"kb{i}"} for i in range(5)],
            25,
            {},
        ))

        result = await service.list_user_knowledge_bases("user1", "org1", page=2, limit=5)
        assert result["pagination"]["page"] == 2
        assert result["pagination"]["hasNext"] is True
        assert result["pagination"]["hasPrev"] is True

    @pytest.mark.asyncio
    async def test_graph_returns_error_dict(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=(
            {"success": False, "reason": "error"},
            0,
            {},
        ))

        result = await service.list_user_knowledge_bases("user1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("error"))

        result = await service.list_user_knowledge_bases("user1", "org1")
        assert result["success"] is False


# ===========================================================================
# update_knowledge_base
# ===========================================================================


class TestUpdateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.update_knowledge_base = AsyncMock(return_value=True)

        result = await service.update_knowledge_base("kb1", "user1", {"groupName": "New Name"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.update_knowledge_base = AsyncMock(return_value=None)

        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False


# ===========================================================================
# delete_knowledge_base
# ===========================================================================


class TestDeleteKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "fullName": "Test User"
        })
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_knowledge_base = AsyncMock(return_value={
            "success": True, "eventData": {"records": []}
        })

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is True
        assert result["code"] == 200

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "fullName": "Test"
        })
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_delete_fails(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "fullName": "Test"
        })
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_knowledge_base = AsyncMock(return_value={
            "success": False
        })

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


# ===========================================================================
# create_folder_in_kb
# ===========================================================================


class TestCreateFolderInKb:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.create_folder = AsyncMock(return_value={"success": True, "id": "f1"})

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_validation_fails(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={
            "valid": False, "success": False, "code": 403, "reason": "No permission"
        })

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result.get("valid") is False

    @pytest.mark.asyncio
    async def test_name_conflict(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "existing"})

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(side_effect=Exception("error"))

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result["success"] is False


# ===========================================================================
# create_nested_folder
# ===========================================================================


class TestCreateNestedFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.create_folder = AsyncMock(return_value={"success": True, "id": "f1"})

        result = await service.create_nested_folder("kb1", "parent1", "SubFolder", "user1", "org1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_parent_not_found(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)

        result = await service.create_nested_folder("kb1", "parent1", "SubFolder", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404


# ===========================================================================
# get_folder_contents
# ===========================================================================


class TestGetFolderContents:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_folder_contents = AsyncMock(return_value={
            "files": [], "folders": []
        })

        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert "files" in result

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_folder_contents = AsyncMock(return_value=None)

        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert result["success"] is False


# ===========================================================================
# updateFolder
# ===========================================================================


class TestUpdateFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.update_folder = AsyncMock(return_value=True)

        result = await service.updateFolder("f1", "kb1", "user1", "New Name")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.updateFolder("f1", "kb1", "user1", "New Name")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_folder_not_in_kb(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.updateFolder("f1", "kb1", "user1", "New Name")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_name_conflict(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "existing"})

        result = await service.updateFolder("f1", "kb1", "user1", "Existing")
        assert result["success"] is False
        assert result["code"] == 409


# ===========================================================================
# delete_folder
# ===========================================================================


class TestDeleteFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.delete_folder = AsyncMock(return_value={
            "success": True, "eventData": {}
        })

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False


# ===========================================================================
# update_record
# ===========================================================================


class TestUpdateRecord:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={
            "kb_id": "kb1"
        })
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        service.graph_provider.update_record = AsyncMock(return_value={"success": True})

        result = await service.update_record("user1", "rec1", {"name": "new"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_kb_context(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value=None)

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 403


# ===========================================================================
# delete_records_in_kb
# ===========================================================================


class TestDeleteRecordsInKb:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_records = AsyncMock(return_value={"success": True})

        result = await service.delete_records_in_kb("kb1", ["r1", "r2"], "user1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False


# ===========================================================================
# delete_records_in_folder
# ===========================================================================


class TestDeleteRecordsInFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.delete_records = AsyncMock(return_value={"success": True})

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 404


# ===========================================================================
# create_kb_permissions
# ===========================================================================


class TestCreateKbPermissions:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "rk1", "_key": "rk1"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.create_kb_permissions = AsyncMock(return_value={
            "success": True, "grantedCount": 2
        })

        result = await service.create_kb_permissions(
            "kb1", "requester1", ["u1", "u2"], [], "READER"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_empty_input(self, service):
        result = await service.create_kb_permissions(
            "kb1", "requester1", [], [], "READER"
        )
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_invalid_role_for_users(self, service):
        result = await service.create_kb_permissions(
            "kb1", "requester1", ["u1"], [], "INVALID_ROLE"
        )
        assert result["success"] is False
        assert result["code"] == 400


# ===========================================================================
# update_kb_permission
# ===========================================================================


class TestUpdateKbPermission:
    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, service):
        result = await service.update_kb_permission("kb1", "req1", [], [], "READER")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_teams_cannot_be_updated(self, service):
        result = await service.update_kb_permission("kb1", "req1", [], ["t1"], "READER")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_requester_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_requester_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_invalid_role(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "BADROLE")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.update_kb_permission = AsyncMock(return_value=True)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is True


# ===========================================================================
# remove_kb_permission
# ===========================================================================


class TestRemoveKbPermission:
    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, service):
        result = await service.remove_kb_permission("kb1", "req1", [], [])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_requester_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_requester_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_cannot_remove_all_owners(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "OWNER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=1)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.remove_kb_permission = AsyncMock(return_value=True)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is True


# ===========================================================================
# list_kb_permissions
# ===========================================================================


class TestListKbPermissions:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.list_kb_permissions = AsyncMock(return_value=[
            {"userId": "u1", "role": "OWNER"}
        ])

        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is True
        assert len(result["permissions"]) == 1

    @pytest.mark.asyncio
    async def test_requester_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_access(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("error"))

        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is False


# ===========================================================================
# update_record - exception path
# ===========================================================================


class TestUpdateRecordException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(side_effect=Exception("db error"))
        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 500


# ===========================================================================
# delete_records_in_kb - exception path
# ===========================================================================


class TestDeleteRecordsInKbException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False


# ===========================================================================
# delete_records_in_folder - exception path
# ===========================================================================


class TestDeleteRecordsInFolderException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False


# ===========================================================================
# create_kb_permissions - exception path
# ===========================================================================


class TestCreateKbPermissionsException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "rk1", "_key": "rk1"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.create_kb_permissions = AsyncMock(side_effect=Exception("db error"))
        result = await service.create_kb_permissions("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False
        assert result["code"] == 500


# ===========================================================================
# update_kb_permission - exception path
# ===========================================================================


class TestUpdateKbPermissionException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False


# ===========================================================================
# remove_kb_permission - exception path
# ===========================================================================


class TestRemoveKbPermissionException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False


# ===========================================================================
# delete_folder - exception path
# ===========================================================================


class TestDeleteFolderException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_delete_fails(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.delete_folder = AsyncMock(return_value={"success": False})
        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False


# ===========================================================================
# updateFolder - exception path
# ===========================================================================


class TestUpdateFolderException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.updateFolder("f1", "kb1", "user1", "New Name")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.updateFolder("f1", "kb1", "user1", "New Name")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_update_fails(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.update_folder = AsyncMock(return_value=False)
        result = await service.updateFolder("f1", "kb1", "user1", "New Name")
        assert result["success"] is False


# ===========================================================================
# get_folder_contents - exception path
# ===========================================================================


class TestGetFolderContentsException:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert result["success"] is False


# ===========================================================================
# create_nested_folder - more coverage
# ===========================================================================


class TestCreateNestedFolderMore:
    @pytest.mark.asyncio
    async def test_validation_fails(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={
            "valid": False, "success": False, "code": 403, "reason": "No permission"
        })
        result = await service.create_nested_folder("kb1", "p1", "Sub", "user1", "org1")
        assert result.get("valid") is False

    @pytest.mark.asyncio
    async def test_name_conflict(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "existing"})
        result = await service.create_nested_folder("kb1", "p1", "Sub", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(side_effect=Exception("error"))
        result = await service.create_nested_folder("kb1", "p1", "Sub", "user1", "org1")
        assert result["success"] is False


# ===========================================================================
# upload_records_to_kb and upload_records_to_folder
# ===========================================================================


class TestUploadRecords:
    @pytest.mark.asyncio
    async def test_upload_records_to_kb(self, service):
        service.graph_provider.upload_records = AsyncMock(return_value={"success": True})
        result = await service.upload_records_to_kb("kb1", "user1", "org1", [{"name": "test.txt"}])
        service.graph_provider.upload_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upload_records_to_folder(self, service):
        service.graph_provider.upload_records = AsyncMock(return_value={"success": True})
        result = await service.upload_records_to_folder("kb1", "f1", "user1", "org1", [{"name": "test.txt"}])
        service.graph_provider.upload_records.assert_awaited_once()
