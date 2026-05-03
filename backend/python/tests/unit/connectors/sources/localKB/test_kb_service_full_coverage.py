from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService


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


@pytest.fixture
def user_data():
    return {"id": "uk1", "_key": "uk1", "fullName": "Test User", "firstName": "Test", "lastName": "User"}


class TestCreateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success_with_fullname(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock()
        service.graph_provider.batch_create_edges = AsyncMock()
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is True
        assert result["name"] == "My KB"
        assert result["userRole"] == "OWNER"
        assert "id" in result
        assert "createdAtTimestamp" in result
        assert "updatedAtTimestamp" in result

    @pytest.mark.asyncio
    async def test_success_user_with_key_only(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk2", "firstName": "Jane", "lastName": "Doe"}
        )
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock()
        service.graph_provider.batch_create_edges = AsyncMock()
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.create_knowledge_base("user2", "org1", "KB 2")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_success_user_no_name_fields(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "uk3"}
        )
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock()
        service.graph_provider.batch_create_edges = AsyncMock()
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.create_knowledge_base("user3", "org1", "KB 3")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_transaction_creation_fails(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.begin_transaction = AsyncMock(side_effect=Exception("tx error"))

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        assert result["code"] == 500
        assert "tx error" in result["reason"]

    @pytest.mark.asyncio
    async def test_db_operation_fails_rollback(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("insert error"))
        service.graph_provider.rollback_transaction = AsyncMock()

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        assert result["code"] == 500
        service.graph_provider.rollback_transaction.assert_called_once_with("txn1")

    @pytest.mark.asyncio
    async def test_rollback_also_fails(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("insert error"))
        service.graph_provider.rollback_transaction = AsyncMock(side_effect=Exception("rollback error"))

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_edge_creation_fails_rollback(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock()
        service.graph_provider.batch_create_edges = AsyncMock(side_effect=Exception("edge error"))
        service.graph_provider.rollback_transaction = AsyncMock()

        result = await service.create_knowledge_base("user1", "org1", "KB")
        assert result["success"] is False
        service.graph_provider.rollback_transaction.assert_called_once_with("txn1")

    @pytest.mark.asyncio
    async def test_exception_before_txn_raises_unbound(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            side_effect=Exception("unexpected")
        )
        with pytest.raises(UnboundLocalError):
            await service.create_knowledge_base("user1", "org1", "KB")


class TestGetKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_knowledge_base = AsyncMock(return_value={"id": "kb1", "name": "KB"})

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
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_kb_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_knowledge_base = AsyncMock(return_value=None)

        result = await service.get_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == "404"

    @pytest.mark.asyncio
    async def test_user_key_fallback(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_knowledge_base = AsyncMock(return_value={"id": "kb1"})

        result = await service.get_knowledge_base("kb1", "user1")
        assert result["id"] == "kb1"

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.get_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == "500"


class TestListUserKnowledgeBases:
    @pytest.mark.asyncio
    async def test_success_with_filters(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=(
            [{"id": "kb1"}], 1, {"permissions": ["OWNER"]}
        ))

        result = await service.list_user_knowledge_bases(
            "user1", "org1", search="test", permissions=["OWNER"],
            sort_by="updatedAtTimestamp", sort_order="desc"
        )
        assert result["knowledgeBases"] == [{"id": "kb1"}]
        assert result["filters"]["applied"]["search"] == "test"
        assert result["filters"]["applied"]["permissions"] == ["OWNER"]
        assert result["filters"]["applied"]["sort_by"] == "updatedAtTimestamp"
        assert result["filters"]["applied"]["sort_order"] == "desc"

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.list_user_knowledge_bases("user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_invalid_sort_defaults(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=([], 0, {}))

        result = await service.list_user_knowledge_bases(
            "user1", "org1", sort_by="INVALID", sort_order="INVALID"
        )
        assert "knowledgeBases" in result

    @pytest.mark.asyncio
    async def test_pagination_first_page(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=(
            [{"id": "kb1"}], 10, {}
        ))

        result = await service.list_user_knowledge_bases("user1", "org1", page=1, limit=5)
        assert result["pagination"]["hasNext"] is True
        assert result["pagination"]["hasPrev"] is False
        assert result["pagination"]["totalPages"] == 2

    @pytest.mark.asyncio
    async def test_pagination_last_page(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=(
            [{"id": "kb1"}], 10, {}
        ))

        result = await service.list_user_knowledge_bases("user1", "org1", page=2, limit=5)
        assert result["pagination"]["hasNext"] is False
        assert result["pagination"]["hasPrev"] is True

    @pytest.mark.asyncio
    async def test_graph_returns_error_dict(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=(
            {"success": False, "reason": "error"}, 0, {}
        ))

        result = await service.list_user_knowledge_bases("user1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_filters_applied(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=([], 0, {}))

        result = await service.list_user_knowledge_bases("user1", "org1")
        assert result["filters"]["applied"] == {}

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.list_user_knowledge_bases("user1", "org1")
        assert result["success"] is False
        assert result["code"] == 500


class TestUpdateKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success_with_all_allowed_roles(self, service):
        for role in ["OWNER", "WRITER", "ORGANIZER", "FILEORGANIZER"]:
            service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
            service.graph_provider.get_user_kb_permission = AsyncMock(return_value=role)
            service.graph_provider.update_knowledge_base = AsyncMock(return_value=True)

            result = await service.update_knowledge_base("kb1", "user1", {"groupName": "New"})
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission_reader(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_insufficient_permission_commenter(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="COMMENTER")

        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_kb_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.update_knowledge_base = AsyncMock(return_value=None)

        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False
        assert result["code"] == "404"

    @pytest.mark.asyncio
    async def test_updates_timestamp_added(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.update_knowledge_base = AsyncMock(return_value=True)

        updates = {"groupName": "New"}
        await service.update_knowledge_base("kb1", "user1", updates)
        assert "updatedAtTimestamp" in updates

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.update_knowledge_base("kb1", "user1", {})
        assert result["success"] is False
        assert result["code"] == 500


class TestDeleteKnowledgeBase:
    @pytest.mark.asyncio
    async def test_success_with_event_data(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_knowledge_base = AsyncMock(return_value={
            "success": True, "eventData": {"records": ["r1"]}
        })

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is True
        assert result["code"] == 200
        assert result["eventData"] == {"records": ["r1"]}

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_not_owner_writer(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_not_owner_reader(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_delete_returns_failure(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_knowledge_base = AsyncMock(return_value={"success": False})

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception_value_error(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_knowledge_base = AsyncMock(side_effect=ValueError("bad value"))

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception_runtime_error_logs_traceback(self, service, user_data):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=user_data)
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_knowledge_base = AsyncMock(side_effect=RuntimeError("runtime"))

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_user_with_key_fallback_and_firstname_lastname(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "uk2", "firstName": "Jane", "lastName": "Doe"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_knowledge_base = AsyncMock(return_value={"success": True, "eventData": None})

        result = await service.delete_knowledge_base("kb1", "user1")
        assert result["success"] is True


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
        service.graph_provider._validate_folder_creation = AsyncMock(
            return_value={"valid": False, "success": False, "code": 403, "reason": "No permission"}
        )

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
    async def test_create_folder_returns_failure(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.create_folder = AsyncMock(return_value={"success": False})

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_create_folder_returns_none(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.create_folder = AsyncMock(return_value=None)

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(side_effect=Exception("err"))

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 500


class TestCreateNestedFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.create_folder = AsyncMock(return_value={"success": True, "id": "f2"})

        result = await service.create_nested_folder("kb1", "parent1", "Sub", "user1", "org1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_validation_fails(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(
            return_value={"valid": False, "success": False, "code": 403}
        )

        result = await service.create_nested_folder("kb1", "parent1", "Sub", "user1", "org1")
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_parent_not_found(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)

        result = await service.create_nested_folder("kb1", "parent1", "Sub", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_name_conflict(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "existing"})

        result = await service.create_nested_folder("kb1", "parent1", "Sub", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_create_returns_failure(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.create_folder = AsyncMock(return_value={"success": False})

        result = await service.create_nested_folder("kb1", "parent1", "Sub", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(side_effect=Exception("err"))

        result = await service.create_nested_folder("kb1", "p1", "Sub", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 500


class TestGetFolderContents:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_folder_contents = AsyncMock(return_value={"files": [], "folders": []})

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
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_folder_contents = AsyncMock(return_value=None)

        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))
        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


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
        assert result["code"] == 200

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.updateFolder("f1", "kb1", "user1", "Name")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_permission_reader(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.updateFolder("f1", "kb1", "user1", "Name")
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_writer_allowed(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.update_folder = AsyncMock(return_value=True)

        result = await service.updateFolder("f1", "kb1", "user1", "Name")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_folder_not_in_kb(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.updateFolder("f1", "kb1", "user1", "Name")
        assert result["success"] is False
        assert result["code"] == "404"

    @pytest.mark.asyncio
    async def test_name_conflict(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "x"})

        result = await service.updateFolder("f1", "kb1", "user1", "Existing")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_update_fails(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.update_folder = AsyncMock(return_value=False)

        result = await service.updateFolder("f1", "kb1", "user1", "Name")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.updateFolder("f1", "kb1", "user1", "Name")
        assert result["success"] is False
        assert result["code"] == 500


class TestDeleteFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.delete_folder = AsyncMock(return_value={"success": True, "eventData": {"records": []}})

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is True
        assert result["code"] == 200

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == "404"

    @pytest.mark.asyncio
    async def test_delete_returns_failure(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.delete_folder = AsyncMock(return_value={"success": False})

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


class TestUpdateRecord:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        service.graph_provider.update_record = AsyncMock(return_value={"success": True})

        result = await service.update_record("user1", "rec1", {"name": "new"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_success_with_file_metadata(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="FILEORGANIZER")
        service.graph_provider.update_record = AsyncMock(return_value={"success": True})

        result = await service.update_record("user1", "rec1", {"name": "new"}, file_metadata={"size": 100})
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
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_reader_denied(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_commenter_denied(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="COMMENTER")

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_update_returns_failure(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.update_record = AsyncMock(return_value={"success": False, "reason": "fail"})

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_update_returns_none(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.update_record = AsyncMock(return_value=None)

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(side_effect=Exception("err"))
        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 500


class TestDeleteRecordsInKb:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_records = AsyncMock(return_value={"success": True})

        result = await service.delete_records_in_kb("kb1", ["r1", "r2"], "user1")
        assert result["success"] is True
        service.graph_provider.delete_records.assert_called_once_with(
            record_ids=["r1", "r2"], kb_id="kb1", folder_id=None
        )

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_reader_denied(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_commenter_denied(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="COMMENTER")

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_delete_returns_failure(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_records = AsyncMock(return_value={"success": False, "reason": "err"})

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_delete_returns_none(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_records = AsyncMock(return_value=None)

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500


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
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_delete_returns_none(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.delete_records = AsyncMock(return_value=None)

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500


class TestCreateKbPermissions:
    @pytest.mark.asyncio
    async def test_success_users(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "rk1", "_key": "rk1"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.create_kb_permissions = AsyncMock(
            return_value={"success": True, "grantedCount": 2}
        )

        result = await service.create_kb_permissions("kb1", "req1", ["u1", "u2"], [], "READER")
        assert result["success"] is True
        assert result["grantedCount"] == 2

    @pytest.mark.asyncio
    async def test_success_teams_only(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "rk1", "_key": "rk1"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.create_kb_permissions = AsyncMock(
            return_value={"success": True, "grantedCount": 1}
        )

        result = await service.create_kb_permissions("kb1", "req1", [], ["t1"], "")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_empty_input(self, service):
        result = await service.create_kb_permissions("kb1", "req1", [], [], "READER")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_invalid_role_for_users(self, service):
        result = await service.create_kb_permissions("kb1", "req1", ["u1"], [], "INVALID")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_deduplicates_users(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "rk1", "_key": "rk1"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.create_kb_permissions = AsyncMock(
            return_value={"success": True, "grantedCount": 1}
        )

        result = await service.create_kb_permissions("kb1", "req1", ["u1", "u1"], [], "READER")
        assert result["success"] is True
        call_args = service.graph_provider.create_kb_permissions.call_args
        assert len(call_args.kwargs["user_ids"]) == 1

    @pytest.mark.asyncio
    async def test_service_returns_failure(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "rk1", "_key": "rk1"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.create_kb_permissions = AsyncMock(
            return_value={"success": False, "reason": "denied"}
        )

        result = await service.create_kb_permissions("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "rk1", "_key": "rk1"}
        )
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.create_kb_permissions = AsyncMock(side_effect=Exception("err"))
        result = await service.create_kb_permissions("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False
        assert result["code"] == 500


class TestUpdateKbPermission:
    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, service):
        result = await service.update_kb_permission("kb1", "req1", [], [], "READER")
        assert result["success"] is False
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_teams_cannot_be_updated(self, service):
        result = await service.update_kb_permission("kb1", "req1", [], ["t1"], "READER")
        assert result["success"] is False
        assert result["code"] == "400"

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
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_invalid_role(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "BAD")
        assert result["success"] is False
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_success_non_owner_users(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.update_kb_permission = AsyncMock(return_value=True)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is True
        assert result["newRole"] == "WRITER"

    @pytest.mark.asyncio
    async def test_no_valid_users_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=1)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == "404"

    @pytest.mark.asyncio
    async def test_bulk_owner_operation_rejected(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "OWNER", "u2": "WRITER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)

        result = await service.update_kb_permission("kb1", "req1", ["u1", "u2"], [], "READER")
        assert result["success"] is False
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_single_owner_downgrade_blocked_if_last(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "OWNER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=1)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_single_owner_downgrade_allowed_if_not_last(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "OWNER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.update_kb_permission = AsyncMock(return_value=True)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_update_returns_falsy(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=1)
        service.graph_provider.update_kb_permission = AsyncMock(return_value=None)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "READER")
        assert result["success"] is False
        assert result["code"] == "500"


class TestRemoveKbPermission:
    @pytest.mark.asyncio
    async def test_no_users_or_teams(self, service):
        result = await service.remove_kb_permission("kb1", "req1", [], [])
        assert result["success"] is False
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_requester_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_requester_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_no_valid_entities(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={"users": {}, "teams": {}})

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], ["t1"])
        assert result["success"] is False
        assert result["code"] == "404"
        assert "skipped_users" in result
        assert "skipped_teams" in result

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
        assert result["code"] == "400"

    @pytest.mark.asyncio
    async def test_success_with_skipped(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "READER"}, "teams": {"t1": "MEMBER"}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.remove_kb_permission = AsyncMock(return_value=True)

        result = await service.remove_kb_permission("kb1", "req1", ["u1", "u_missing"], ["t1", "t_missing"])
        assert result["success"] is True
        assert "u1" in result["userIds"]

    @pytest.mark.asyncio
    async def test_remove_returns_falsy(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.remove_kb_permission = AsyncMock(return_value=None)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == "500"


class TestListKbPermissions:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.list_kb_permissions = AsyncMock(return_value=[{"userId": "u1", "role": "OWNER"}])

        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is True
        assert result["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_requester_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_access(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.list_kb_permissions("kb1", "req1")
        assert result["success"] is False
        assert result["code"] == "500"


class TestListAllRecords:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_all_records = AsyncMock(return_value=(
            [{"id": "r1"}], 1, {"recordTypes": ["FILE"]}
        ))

        result = await service.list_all_records("user1", "org1")
        assert result["records"] == [{"id": "r1"}]
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.list_all_records("user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_invalid_sort_defaults(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_all_records = AsyncMock(return_value=([], 0, {}))

        result = await service.list_all_records(
            "user1", "org1", sort_by="INVALID", sort_order="INVALID"
        )
        assert "records" in result

    @pytest.mark.asyncio
    async def test_with_all_filters(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_all_records = AsyncMock(return_value=([], 0, {}))

        result = await service.list_all_records(
            "user1", "org1", search="test", record_types=["FILE"],
            origins=["local"], connectors=["kb"], indexing_status=["COMPLETED"],
            permissions=["OWNER"], date_from=100, date_to=200, source="local"
        )
        applied = result["filters"]["applied"]
        assert "search" in applied
        assert "recordTypes" in applied
        assert "source" in applied
        assert "dateRange" in applied

    @pytest.mark.asyncio
    async def test_source_all_not_in_filters(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_all_records = AsyncMock(return_value=([], 0, {}))

        result = await service.list_all_records("user1", "org1", source="all")
        assert "source" not in result["filters"]["applied"]

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.list_all_records("user1", "org1")
        assert result["records"] == []
        assert "error" in result


class TestListKbRecords:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_kb_records = AsyncMock(return_value=(
            [{"id": "r1"}], 1, {}
        ))

        result = await service.list_kb_records("kb1", "user1", "org1")
        assert result["records"] == [{"id": "r1"}]

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.list_kb_records("kb1", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_with_filters(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_kb_records = AsyncMock(return_value=([], 0, {}))

        result = await service.list_kb_records(
            "kb1", "user1", "org1", search="x",
            record_types=["FILE"], date_from=100, date_to=200,
            sort_by="recordName", sort_order="asc"
        )
        assert "search" in result["filters"]["applied"]

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.list_kb_records("kb1", "user1", "org1")
        assert result["records"] == []
        assert "error" in result


class TestGetKbChildren:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_kb_children = AsyncMock(return_value={
            "success": True, "totalCount": 3,
            "counts": {"folders": 1, "records": 2},
            "availableFilters": {"recordTypes": ["FILE"]},
        })

        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is True
        assert result["userPermission"]["role"] == "OWNER"
        assert result["userPermission"]["canUpload"] is True
        assert result["userPermission"]["canDelete"] is True
        assert result["pagination"]["totalItems"] == 3

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_kb_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_kb_children = AsyncMock(return_value={
            "success": False, "reason": "KB not found"
        })

        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_invalid_sort_defaults(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_kb_children = AsyncMock(return_value={
            "success": True, "totalCount": 0, "counts": {"folders": 0, "records": 0},
            "availableFilters": {},
        })

        result = await service.get_kb_children(
            "kb1", "user1", sort_by="INVALID", sort_order="INVALID"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_reader_permissions(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_kb_children = AsyncMock(return_value={
            "success": True, "totalCount": 0, "counts": {"folders": 0, "records": 0},
            "availableFilters": {},
        })

        result = await service.get_kb_children("kb1", "user1")
        assert result["userPermission"]["canUpload"] is False
        assert result["userPermission"]["canDelete"] is False
        assert result["userPermission"]["canManagePermissions"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


class TestGetFolderChildren:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        service.graph_provider.get_folder_children = AsyncMock(return_value={
            "success": True, "totalCount": 2,
            "counts": {"folders": 1, "records": 1},
            "availableFilters": {},
        })

        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is True
        assert result["userPermission"]["canUpload"] is True
        assert result["userPermission"]["canEdit"] is True
        assert result["pagination"]["totalItems"] == 2

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.get_folder_children = AsyncMock(return_value={
            "success": False, "reason": "Folder not found"
        })

        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("err"))
        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


class TestErrorResponse:
    def test_error_response_helper(self, service):
        result = service._error_response(404, "Not found")
        assert result["success"] is False
        assert result["code"] == 404
        assert result["reason"] == "Not found"

    def test_error_response_500(self, service):
        result = service._error_response(500, "Server error")
        assert result["code"] == 500


class TestUploadRecords:
    @pytest.mark.asyncio
    async def test_upload_to_kb(self, service):
        service.graph_provider.upload_records = AsyncMock(return_value={"success": True})
        result = await service.upload_records_to_kb("kb1", "user1", "org1", [{"name": "f.txt"}])
        assert result["success"] is True
        service.graph_provider.upload_records.assert_awaited_once_with(
            "kb1", "user1", "org1", [{"name": "f.txt"}], parent_folder_id=None
        )

    @pytest.mark.asyncio
    async def test_upload_to_folder(self, service):
        service.graph_provider.upload_records = AsyncMock(return_value={"success": True})
        result = await service.upload_records_to_folder("kb1", "f1", "user1", "org1", [{"name": "f.txt"}])
        assert result["success"] is True
        service.graph_provider.upload_records.assert_awaited_once_with(
            "kb1", "user1", "org1", [{"name": "f.txt"}], parent_folder_id="f1"
        )


class TestMoveRecord:
    @pytest.mark.asyncio
    async def test_success_move_to_folder(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old_parent"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.delete_parent_child_edge_to_record = AsyncMock(return_value=True)
        service.graph_provider.create_parent_child_edge = AsyncMock(return_value=True)
        service.graph_provider.update_record_external_parent_id = AsyncMock(return_value=True)
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", "new_folder", "user1")
        assert result["success"] is True
        assert result["newParentId"] == "new_folder"

    @pytest.mark.asyncio
    async def test_success_move_to_root(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old_parent"})
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.delete_parent_child_edge_to_record = AsyncMock(return_value=True)
        service.graph_provider.update_record_external_parent_id = AsyncMock(return_value=True)
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", None, "user1")
        assert result["success"] is True
        assert result["newParentId"] is None

    @pytest.mark.asyncio
    async def test_empty_string_new_parent_treated_as_none(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old_parent"})
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.delete_parent_child_edge_to_record = AsyncMock(return_value=True)
        service.graph_provider.update_record_external_parent_id = AsyncMock(return_value=True)
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", "", "user1")
        assert result["success"] is True
        assert result["newParentId"] is None

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_record_not_in_kb(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value=None)

        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_record_in_different_kb(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "other_kb"})

        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_noop_same_parent(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "f1"})

        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is True
        assert "already" in result["message"]

    @pytest.mark.asyncio
    async def test_noop_already_at_root(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)

        result = await service.move_record("kb1", "rec1", None, "user1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_target_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.move_record("kb1", "rec1", "bad_folder", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_cannot_move_into_self(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)

        result = await service.move_record("kb1", "rec1", "rec1", "user1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_circular_reference_blocked(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=True)
        service.graph_provider.is_record_descendant_of = AsyncMock(return_value=True)

        result = await service.move_record("kb1", "rec1", "child_folder", "user1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_transaction_failure_rollback(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.delete_parent_child_edge_to_record = AsyncMock(return_value=False)
        service.graph_provider.rollback_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", "new_folder", "user1")
        assert result["success"] is False
        assert result["code"] == 500
        service.graph_provider.rollback_transaction.assert_called_once_with("txn1")

    @pytest.mark.asyncio
    async def test_create_edge_failure_rollback(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.delete_parent_child_edge_to_record = AsyncMock(return_value=True)
        service.graph_provider.create_parent_child_edge = AsyncMock(return_value=False)
        service.graph_provider.rollback_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", "new_folder", "user1")
        assert result["success"] is False
        service.graph_provider.rollback_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_external_parent_failure_rollback(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.create_parent_child_edge = AsyncMock(return_value=True)
        service.graph_provider.update_record_external_parent_id = AsyncMock(return_value=False)
        service.graph_provider.rollback_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", "new_folder", "user1")
        assert result["success"] is False
        service.graph_provider.rollback_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_failure_handled(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.create_parent_child_edge = AsyncMock(return_value=True)
        service.graph_provider.update_record_external_parent_id = AsyncMock(side_effect=Exception("boom"))
        service.graph_provider.rollback_transaction = AsyncMock(side_effect=Exception("rb fail"))

        result = await service.move_record("kb1", "rec1", "new_folder", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_move_from_root_to_folder_no_delete_edge(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.create_parent_child_edge = AsyncMock(return_value=True)
        service.graph_provider.update_record_external_parent_id = AsyncMock(return_value=True)
        service.graph_provider.commit_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", "new_folder", "user1")
        assert result["success"] is True
        service.graph_provider.delete_parent_child_edge_to_record.assert_not_awaited()
