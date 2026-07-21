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

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.service import DefaultEndpoints
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.models.entities import FileRecord


# Fixtures live in conftest.py (service, mock_graph_provider, mock_processor, …)


def _mock_mongo_to_graph(user_ids, org_id=None, chunk_size=500):
    return {uid: f"gk_{uid}" for uid in user_ids}


def _setup_kb_owner_resolve(service):
    service.graph_provider.get_user_by_user_id = AsyncMock(
        return_value={"id": "rk1", "_key": "rk1", "orgId": "org-1"}
    )
    service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
        side_effect=_mock_mongo_to_graph
    )


def _setup_writer(service):
    service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
    service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")


def _minimal_upload_record_dict(**overrides):
    base = {
        "id": "rec-upload-1",
        "_key": "rec-upload-1",
        "orgId": "org1",
        "recordName": "a.pdf",
        "recordType": "FILE",
        "externalRecordId": "ext-a",
        "version": 1,
        "origin": "UPLOAD",
        "connectorName": "KB",
        "connectorId": "kb1",
        "createdAtTimestamp": 1,
        "updatedAtTimestamp": 1,
    }
    base.update(overrides)
    return base


def _upload_analysis(*, parent_folder_id=None):
    return {
        "parent_folder_id": parent_folder_id,
        "sorted_folder_paths": ["docs"],
        "folder_hierarchy": {
            "docs": {"name": "docs", "parent_path": None, "level": 1},
        },
        "file_destinations": {
            0: {"type": "folder", "folder_hierarchy_path": "docs", "folder_id": "folder-docs"},
        },
    }


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
    async def test_created_by_stores_external_user_id_not_graph_key(self, service):
        """createdBy must be the external user_id (matches every other connector
        type's convention, and what connector_registry._can_access_connector
        compares against) — not the internal graph user_key."""
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "graph-key-uk1", "_key": "graph-key-uk1", "fullName": "Test User"
        })
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        service.graph_provider.batch_create_edges = AsyncMock(return_value=True)
        service.graph_provider.commit_transaction = AsyncMock()

        await service.create_knowledge_base("external-user-1", "org1", "My KB")

        kb_data = service.graph_provider.batch_upsert_nodes.call_args[0][0][0]
        assert kb_data["createdBy"] == "external-user-1"

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

    @pytest.mark.asyncio
    async def test_empty_name(self, service):
        result = await service.create_knowledge_base("user1", "org1", "   ")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_rollback_also_fails(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "uk1", "fullName": "Test"}
        )
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("db down"))
        service.graph_provider.rollback_transaction = AsyncMock(side_effect=RuntimeError("rollback down"))

        result = await service.create_knowledge_base("user1", "org1", "My KB")
        assert result["success"] is False
        assert result["code"] == 500
        service.logger.warning.assert_called()


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

    @pytest.mark.asyncio
    async def test_applied_filters_populated(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_user_knowledge_bases = AsyncMock(return_value=([], 0, {}))

        result = await service.list_user_knowledge_bases(
            "user1",
            "org1",
            search="docs",
            permissions=["OWNER"],
            sort_by="updatedAtTimestamp",
            sort_order="desc",
        )
        applied = result["filters"]["applied"]
        assert applied["search"] == "docs"
        assert applied["permissions"] == ["OWNER"]
        assert applied["sort_by"] == "updatedAtTimestamp"
        assert applied["sort_order"] == "desc"


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

    @pytest.mark.asyncio
    async def test_empty_name(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")

        result = await service.update_knowledge_base("kb1", "user1", {"name": "  "})
        assert result["success"] is False
        assert result["code"] == 400


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
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": True, "virtual_record_ids": []
        })

        result = await service.delete_knowledge_base("kb1", "user1", "org1")
        assert result["success"] is True
        assert result["code"] == 200

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await service.delete_knowledge_base("kb1", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "fullName": "Test"
        })
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await service.delete_knowledge_base("kb1", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_delete_fails(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "uk1", "fullName": "Test"
        })
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": False
        })

        result = await service.delete_knowledge_base("kb1", "user1", "org1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=Exception("db error"))

        result = await service.delete_knowledge_base("kb1", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_publishes_bulk_delete_event(self, service, mock_kafka_service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": True,
            "virtual_record_ids": ["v1", "v2"],
        })

        result = await service.delete_knowledge_base("kb1", "user1", "org1")
        assert result["success"] is True
        mock_kafka_service.publish_event.assert_awaited_once()
        event = mock_kafka_service.publish_event.await_args[0][1]
        assert event["eventType"] == "bulkDeleteRecords"
        assert event["payload"]["virtualRecordIds"] == ["v1", "v2"]

    @pytest.mark.asyncio
    async def test_kafka_publish_failure_still_succeeds(self, service, mock_kafka_service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.delete_connector_instance = AsyncMock(return_value={
            "success": True,
            "virtual_record_ids": ["v1"],
        })
        mock_kafka_service.publish_event = AsyncMock(side_effect=RuntimeError("kafka down"))

        result = await service.delete_knowledge_base("kb1", "user1", "org1")
        assert result["success"] is True
        service.logger.error.assert_called()


class TestCreateFolderInKb:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)

        result = await service.create_folder_in_kb("kb1", "Folder", "user1", "org1")
        assert result["success"] is True
        service.processor.on_new_records.assert_awaited_once()

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

    @pytest.mark.asyncio
    async def test_empty_name(self, service):
        result = await service.create_folder_in_kb("kb1", "  ", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 400


# ===========================================================================
# create_nested_folder
# ===========================================================================


class TestCreateNestedFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)

        result = await service.create_nested_folder("kb1", "parent1", "SubFolder", "user1", "org1")
        assert result["success"] is True
        service.processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_parent_not_found(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)

        result = await service.create_nested_folder("kb1", "parent1", "SubFolder", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_duplicate_name_in_parent(self, service):
        service.graph_provider._validate_folder_creation = AsyncMock(return_value={"valid": True})
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "existing"})

        result = await service.create_nested_folder("kb1", "parent1", "Sub", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 409


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

    @pytest.mark.asyncio
    async def test_folder_not_in_kb(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.get_folder_contents("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404


# ===========================================================================
# updateFolder
# ===========================================================================


class TestUpdateFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "Old Name"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        mock_folder = MagicMock()
        mock_folder.record_name = "Old Name"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=mock_folder)

        result = await service.updateFolder("f1", "kb1", "user1", "New Name")
        assert result["success"] is True
        service.processor.on_record_metadata_update.assert_awaited_once()

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
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "Old Name"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "existing"})

        result = await service.updateFolder("f1", "kb1", "user1", "Existing")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_empty_name(self, service):
        _setup_writer(service)
        result = await service.updateFolder("f1", "kb1", "user1", "  ")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_unchanged_name_skips_update(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "Docs"})

        result = await service.updateFolder("f1", "kb1", "user1", "docs")
        assert result["success"] is True
        service.processor.on_record_metadata_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_folder_document_missing(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.get_document = AsyncMock(return_value=None)

        result = await service.updateFolder("f1", "kb1", "user1", "Renamed")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_folder_record_missing(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "Old"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value=None)
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=None)

        result = await service.updateFolder("f1", "kb1", "user1", "New")
        assert result["success"] is False
        assert result["code"] == 404


# ===========================================================================
# delete_folder
# ===========================================================================


class TestDeleteFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is True
        service.processor.on_records_deleted_cascade.assert_awaited_once_with(["f1"], "kb1")

    @pytest.mark.asyncio
    async def test_not_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(side_effect=RuntimeError("db"))
        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


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
        mock_record = MagicMock()
        mock_record.record_name = "old"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=mock_record)
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "new"})

        result = await service.update_record("user1", "rec1", {"recordName": "new"})
        assert result["success"] is True
        service.processor.on_record_metadata_update.assert_awaited_once()

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

    @pytest.mark.asyncio
    async def test_no_kb_role_returns_404(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await service.update_record("user1", "rec1", {"recordName": "new"})
        assert result["success"] is False
        assert result["code"] == 404
        assert result["reason"] == "Record not found"

    @pytest.mark.asyncio
    async def test_rename_empty_name(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})

        result = await service.update_record("user1", "rec1", {"recordName": "  "})
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_rename_duplicate_sibling(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_document = AsyncMock(
            side_effect=[
                {"recordName": "old.pdf"},
                {"isFile": True, "mimeType": "application/pdf"},
            ]
        )
        service.graph_provider.get_record_parent_info = AsyncMock(
            return_value={"id": "parent1", "type": "record"}
        )
        service.graph_provider.find_file_by_name_in_parent = AsyncMock(return_value={"id": "other"})

        result = await service.update_record("user1", "rec1", {"recordName": "taken.pdf"})
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_kb_context_not_found(self, service):
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value=None)
        result = await service.update_record("user1", "rec1", {"recordName": "x"})
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_file_content_update(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        mock_record = MagicMock()
        mock_record.record_name = "file.pdf"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=mock_record)
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "file.pdf"})

        result = await service.update_record(
            "user1", "rec1", {"recordName": "file.pdf"},
            file_metadata={"lastModified": 999},
        )
        assert result["success"] is True
        service.processor.on_record_content_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rename_unchanged_skips_duplicate_check(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_document = AsyncMock(
            side_effect=[
                {"recordName": "same.pdf"},
                {"isFile": True, "mimeType": "application/pdf"},
                {"recordName": "same.pdf"},
            ]
        )
        mock_record = MagicMock()
        mock_record.record_name = "same.pdf"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=mock_record)

        result = await service.update_record("user1", "rec1", {"recordName": "same.pdf"})
        assert result["success"] is True
        service.graph_provider.find_file_by_name_in_parent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rename_record_document_missing(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_document = AsyncMock(return_value=None)

        result = await service.update_record("user1", "rec1", {"recordName": "new.pdf"})
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_file_record_not_found(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=None)

        result = await service.update_record("user1", "rec1", {})
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_ignores_unmapped_update_keys(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        mock_record = MagicMock()
        mock_record.record_name = "file.pdf"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=mock_record)
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "file.pdf"})

        result = await service.update_record(
            "user1", "rec1", {"recordName": "file.pdf", "unknownField": "x"}
        )
        assert result["success"] is True
        service.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_rename_duplicate_in_parent_folder(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_document = AsyncMock(
            side_effect=[
                {"recordName": "old.pdf"},
                {"isFile": True, "mimeType": "application/pdf"},
            ]
        )
        service.graph_provider.get_record_parent_info = AsyncMock(
            return_value={"id": "parent1", "type": "record"}
        )
        service.graph_provider.find_file_by_name_in_parent = AsyncMock(return_value={"id": "dup"})

        result = await service.update_record("user1", "rec1", {"recordName": "taken.pdf"})
        assert result["success"] is False
        assert "this folder" in result["reason"]


# ===========================================================================
# delete_records_in_kb
# ===========================================================================


class TestDeleteRecordsInKb:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")

        result = await service.delete_records_in_kb("kb1", ["r1", "r2"], "user1")
        assert result["success"] is True
        service.processor.on_records_deleted_cascade.assert_awaited_once_with(["r1", "r2"], "kb1")

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

    @pytest.mark.asyncio
    async def test_none_matched_returns_404(self, service):
        _setup_writer(service)
        service.processor.on_records_deleted_cascade = AsyncMock(return_value={
            "success": True,
            "total_requested": 1,
            "successfully_deleted": 0,
            "failed_records": [{"record_id": "r1"}],
        })

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_strips_event_data_from_response(self, service):
        _setup_writer(service)
        service.processor.on_records_deleted_cascade = AsyncMock(return_value={
            "success": True,
            "total_requested": 1,
            "successfully_deleted": 1,
            "eventData": {"payloads": [{"recordId": "r1"}]},
        })

        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is True
        assert "eventData" not in result

    @pytest.mark.asyncio
    async def test_processor_failure(self, service):
        _setup_writer(service)
        service.processor.on_records_deleted_cascade = AsyncMock(return_value={
            "success": False,
            "reason": "db error",
        })
        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_exception(self, service):
        _setup_writer(service)
        service.processor.on_records_deleted_cascade = AsyncMock(side_effect=RuntimeError("boom"))
        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_processor_returns_none(self, service):
        _setup_writer(service)
        service.processor.on_records_deleted_cascade = AsyncMock(return_value=None)
        result = await service.delete_records_in_kb("kb1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500


# ===========================================================================
# delete_records_in_folder
# ===========================================================================


class TestDeleteRecordsInFolder:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is True
        service.processor.on_records_deleted_cascade.assert_awaited_once_with(["r1"], "kb1")

    @pytest.mark.asyncio
    async def test_insufficient_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_folder_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_none_matched_returns_404(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.processor.on_records_deleted_cascade = AsyncMock(return_value={
            "success": True,
            "total_requested": 1,
            "successfully_deleted": 0,
        })

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_processor_failure(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.processor.on_records_deleted_cascade = AsyncMock(return_value={
            "success": False,
            "reason": "delete failed",
        })
        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False


    @pytest.mark.asyncio
    async def test_processor_returns_none(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
        service.processor.on_records_deleted_cascade = AsyncMock(return_value=None)

        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_exception(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_exists_in_kb = AsyncMock(side_effect=RuntimeError("db"))
        result = await service.delete_records_in_folder("kb1", "f1", ["r1"], "user1")
        assert result["success"] is False
        assert result["code"] == 500


# ===========================================================================
# create_kb_permissions
# ===========================================================================


class TestCreateKbPermissions:
    @pytest.mark.asyncio
    async def test_success(self, service):
        _setup_kb_owner_resolve(service)
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

    @pytest.mark.asyncio
    async def test_teams_only_success(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.create_kb_permissions = AsyncMock(return_value={
            "success": True,
            "grantedCount": 1,
        })
        result = await service.create_kb_permissions("kb1", "requester1", [], ["t1"], "")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_graph_create_failure(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.create_kb_permissions = AsyncMock(return_value={
            "success": False,
            "reason": "graph error",
        })
        result = await service.create_kb_permissions("kb1", "requester1", ["u1"], [], "READER")
        assert result["success"] is False


    @pytest.mark.asyncio
    async def test_partial_user_resolution_failure(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            return_value={"u1": "gk_u1"}
        )
        result = await service.create_kb_permissions(
            "kb1", "requester1", ["u1", "u-missing"], [], "READER"
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
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.update_kb_permission = AsyncMock(return_value=True)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_all_targets_skipped(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            return_value={"u1": "gk_unknown"}
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={"users": {}, "teams": {}})
        service.graph_provider.count_kb_owners = AsyncMock(return_value=1)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_teams_not_allowed(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        result = await service.update_kb_permission("kb1", "req1", [], ["t1"], "WRITER")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_kb_not_found(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.get_document = AsyncMock(return_value=None)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_cannot_change_creator_permission(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1", "orgId": "org-1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            return_value={"creator-mongo": "creator-gk", "u1": "creator-gk"}
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"creator-gk": "OWNER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "creator-mongo"})

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_cannot_change_other_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1", "orgId": "org-1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            return_value={"u1": "other-owner", "creator-mongo": "creator-gk"}
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"other-owner": "OWNER", "rk1": "OWNER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "creator-mongo"})

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_bulk_owner_update_rejected(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "gk_u1", "orgId": "org-1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            side_effect=[
                {"u1": "gk_u1", "u2": "gk_u2"},
                {"u1": "gk_u1"},
            ]
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "OWNER", "gk_u2": "OWNER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "u1"})

        result = await service.update_kb_permission("kb1", "u1", ["u1", "u2"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_cannot_demote_last_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "gk_u1", "orgId": "org-1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            side_effect=[
                {"u1": "gk_u1"},
                {"u1": "gk_u1"},
            ]
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "OWNER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=1)
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "u1"})

        result = await service.update_kb_permission("kb1", "u1", ["u1"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_graph_update_failure(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "rk1"})
        service.graph_provider.update_kb_permission = AsyncMock(return_value=False)

        result = await service.update_kb_permission("kb1", "req1", ["u1"], [], "WRITER")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_success_with_skipped_user(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            return_value={"u1": "gk_u1", "u2": "gk_missing"}
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "rk1"})
        service.graph_provider.update_kb_permission = AsyncMock(return_value=True)

        result = await service.update_kb_permission("kb1", "req1", ["u1", "u2"], [], "WRITER")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_bulk_promote_to_owner_allowed(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "gk_u1", "orgId": "org-1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            side_effect=[
                {"u1": "gk_u1", "u2": "gk_u2"},
                {"u1": "gk_u1"},
            ]
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER", "gk_u2": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=1)
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "u1"})
        service.graph_provider.update_kb_permission = AsyncMock(return_value=True)

        result = await service.update_kb_permission("kb1", "u1", ["u1", "u2"], [], "OWNER")
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
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.remove_kb_permission = AsyncMock(return_value=True)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_all_targets_skipped(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            return_value={"u1": "gk_missing"}
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={"users": {}, "teams": {}})

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_kb_not_found(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.get_document = AsyncMock(return_value=None)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_cannot_remove_creator(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1", "orgId": "org-1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            return_value={"u1": "creator-gk"}
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"creator-gk": "OWNER"}, "teams": {}
        })
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "creator-mongo"})
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            side_effect=[
                {"u1": "creator-gk"},
                {"creator-mongo": "creator-gk"},
            ]
        )

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_cannot_remove_other_owner(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "rk1", "orgId": "org-1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            side_effect=[
                {"u1": "other-owner"},
                {"creator-mongo": "creator-gk"},
            ]
        )
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"other-owner": "OWNER", "rk1": "OWNER"}, "teams": {}
        })
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "creator-mongo"})
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_success_with_skipped_team(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "rk1"})
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.remove_kb_permission = AsyncMock(return_value=True)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], ["missing-team"])
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_graph_remove_failure(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {"gk_u1": "READER"}, "teams": {}
        })
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "rk1"})
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.remove_kb_permission = AsyncMock(return_value=False)

        result = await service.remove_kb_permission("kb1", "req1", ["u1"], [])
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_remove_team_permission(self, service):
        _setup_kb_owner_resolve(service)
        service.graph_provider.get_kb_permissions = AsyncMock(return_value={
            "users": {}, "teams": {"team-1": True}
        })
        service.graph_provider.get_document = AsyncMock(return_value={"createdBy": "rk1"})
        service.graph_provider.count_kb_owners = AsyncMock(return_value=2)
        service.graph_provider.remove_kb_permission = AsyncMock(return_value=True)

        result = await service.remove_kb_permission("kb1", "req1", [], ["team-1"])
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
        _setup_kb_owner_resolve(service)
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
        service.processor.on_records_deleted_cascade = AsyncMock(side_effect=Exception("delete failed"))
        result = await service.delete_folder("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


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

    @pytest.mark.asyncio
    async def test_empty_name(self, service):
        result = await service.create_nested_folder("kb1", "p1", "  ", "user1", "org1")
        assert result["success"] is False
        assert result["code"] == 400


# ===========================================================================
# upload_records_to_kb and upload_records_to_folder
# ===========================================================================


class TestUploadRecords:
    @pytest.mark.asyncio
    async def test_upload_records_to_kb(self, service):
        service._upload_records = AsyncMock(return_value={"success": True, "totalCreated": 1})
        result = await service.upload_records_to_kb("kb1", "user1", "org1", [{"name": "test.txt"}])
        service._upload_records.assert_awaited_once_with(
            "kb1", "user1", "org1", [{"name": "test.txt"}], parent_folder_id=None
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_upload_records_to_folder(self, service):
        service._upload_records = AsyncMock(return_value={"success": True, "totalCreated": 1})
        result = await service.upload_records_to_folder("kb1", "f1", "user1", "org1", [{"name": "test.txt"}])
        service._upload_records.assert_awaited_once_with(
            "kb1", "user1", "org1", [{"name": "test.txt"}], parent_folder_id="f1"
        )
        assert result["success"] is True


# ===========================================================================
# validate_folder_for_upload
# ===========================================================================


class TestValidateFolderForUpload:
    @pytest.mark.asyncio
    async def test_valid_folder_returns_valid(self, service):
        service.graph_provider.validate_folder_for_upload = AsyncMock(
            return_value={"valid": True, "upload_target": "folder", "user_role": "OWNER"}
        )

        result = await service.validate_folder_for_upload("kb1", "f1", "user1", "org1")

        assert result["valid"] is True
        service.graph_provider.validate_folder_for_upload.assert_awaited_once_with(
            kb_id="kb1", folder_id="f1", user_id="user1", org_id="org1"
        )

    @pytest.mark.asyncio
    async def test_folder_not_in_kb_propagates_404(self, service):
        service.graph_provider.validate_folder_for_upload = AsyncMock(
            return_value={"valid": False, "success": False, "code": 404, "reason": "Folder f1 not found in KB kb1"}
        )

        result = await service.validate_folder_for_upload("kb1", "f1", "user1", "org1")

        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_nonexistent_folder_propagates_404(self, service):
        service.graph_provider.validate_folder_for_upload = AsyncMock(
            return_value={"valid": False, "success": False, "code": 404, "reason": "Folder ghost not found"}
        )

        result = await service.validate_folder_for_upload("kb1", "ghost", "user1", "org1")

        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_insufficient_permission_propagates_403(self, service):
        service.graph_provider.validate_folder_for_upload = AsyncMock(
            return_value={"valid": False, "success": False, "code": 403, "reason": "Insufficient permissions"}
        )

        result = await service.validate_folder_for_upload("kb1", "f1", "user1", "org1")

        assert result["valid"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_passes_all_arguments_correctly(self, service):
        service.graph_provider.validate_folder_for_upload = AsyncMock(
            return_value={"valid": True, "upload_target": "folder"}
        )

        await service.validate_folder_for_upload("my-kb", "my-folder", "my-user", "my-org")

        service.graph_provider.validate_folder_for_upload.assert_awaited_once_with(
            kb_id="my-kb",
            folder_id="my-folder",
            user_id="my-user",
            org_id="my-org",
        )


# ===========================================================================
# _build_kb_folder_record
# ===========================================================================


class TestBuildKbFolderRecord:
    def test_folder_record_fields(self, service):
        folder = service._build_kb_folder_record("kb1", "fid", "Docs", "org1", None)
        assert folder.is_file is False
        assert folder.indexing_status == ProgressStatus.COMPLETED.value
        assert folder.connector_id == "kb1"
        assert folder.parent_external_record_id is None


# ===========================================================================
# sibling conflict helpers
# ===========================================================================


class TestSiblingConflictHelpers:
    @pytest.mark.asyncio
    async def test_folder_conflict(self, service):
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "other"})
        err = await service._assert_no_folder_sibling_conflict("kb1", "parent1", "Docs", "self")
        assert err["code"] == 409

    @pytest.mark.asyncio
    async def test_folder_no_conflict(self, service):
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        assert await service._assert_no_folder_sibling_conflict("kb1", None, "Docs", "self") is None

    @pytest.mark.asyncio
    async def test_file_conflict(self, service):
        service.graph_provider.find_file_by_name_in_parent = AsyncMock(return_value={"id": "dup"})
        err = await service._assert_no_file_sibling_conflict(
            "kb1", "parent1", "a.pdf", "application/pdf", "self"
        )
        assert err["code"] == 409


# ===========================================================================
# _resolve_user_ids_to_graph_keys
# ===========================================================================


class TestResolveUserIds:
    @pytest.mark.asyncio
    async def test_empty_list(self, service):
        keys, err = await service._resolve_user_ids_to_graph_keys([], "req1")
        assert keys == []
        assert err is None

    @pytest.mark.asyncio
    async def test_missing_mapping(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"orgId": "org1"})
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(return_value=None)
        keys, err = await service._resolve_user_ids_to_graph_keys(["u1"], "req1")
        assert keys is None
        assert err["code"] == 400

    @pytest.mark.asyncio
    async def test_value_error(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"orgId": "org1"})
        service.graph_provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(
            side_effect=ValueError("bad chunk")
        )
        keys, err = await service._resolve_user_ids_to_graph_keys(["u1"], "req1")
        assert keys is None
        assert err["code"] == 400

    @pytest.mark.asyncio
    async def test_malformed_user_record(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"fullName": "No Key"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        _, _, err = await service._resolve_user_and_kb_access("kb1", "user1")
        assert err["code"] == 500


# ===========================================================================
# move_record
# ===========================================================================


class TestMoveRecord:
    @pytest.mark.asyncio
    async def test_file_destination_conflict(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old-parent"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.get_document = AsyncMock(
            side_effect=[
                {"recordName": "a.pdf"},
                {"isFile": True, "mimeType": "application/pdf"},
            ]
        )
        service.graph_provider.find_file_by_name_in_parent = AsyncMock(return_value={"id": "dup"})

        result = await service.move_record("kb1", "rec1", "new-folder", "user1")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_folder_destination_conflict(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old-parent"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=True)
        service.graph_provider.is_record_descendant_of = AsyncMock(return_value=False)
        service.graph_provider.get_document = AsyncMock(return_value={"recordName": "Docs"})
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value={"id": "dup"})

        result = await service.move_record("kb1", "folder1", "new-parent", "user1")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_success_move_file(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old-parent"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.get_document = AsyncMock(
            side_effect=[
                {"recordName": "a.pdf"},
                {"isFile": True, "mimeType": "application/pdf"},
            ]
        )
        service.graph_provider.find_file_by_name_in_parent = AsyncMock(return_value=None)
        mock_record = MagicMock()
        mock_record.external_record_id = "ext-1"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=mock_record)

        result = await service.move_record("kb1", "rec1", "new-folder", "user1")
        assert result["success"] is True
        service.processor.on_records_moved.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_already_at_destination(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "folder1"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)

        result = await service.move_record("kb1", "rec1", "folder1", "user1")
        assert result["success"] is True
        assert "already" in result["message"].lower()
        service.processor.on_records_moved.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_target_folder_not_found(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)

        result = await service.move_record("kb1", "rec1", "missing", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception_rolls_back_transaction(self, service):
        _setup_writer(service)
        service.graph_provider.begin_transaction = AsyncMock(return_value="txn1")
        service.graph_provider._get_kb_context_for_record = AsyncMock(side_effect=RuntimeError("boom"))
        service.graph_provider.rollback_transaction = AsyncMock()

        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_record_not_in_kb(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "other-kb"})
        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_move_folder_into_self(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        result = await service.move_record("kb1", "folder1", "folder1", "user1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_circular_folder_move(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=True)
        service.graph_provider.is_record_descendant_of = AsyncMock(return_value=True)
        result = await service.move_record("kb1", "parent", "child", "user1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_move_to_root_success(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old-parent"})
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.get_document = AsyncMock(
            side_effect=[{"recordName": "a.pdf"}, {"isFile": True, "mimeType": "application/pdf"}]
        )
        service.graph_provider.find_file_by_name_in_parent = AsyncMock(return_value=None)
        mock_record = MagicMock()
        mock_record.external_record_id = "ext-1"
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=mock_record)

        result = await service.move_record("kb1", "rec1", None, "user1")
        assert result["success"] is True
        assert result["newParentId"] is None

    @pytest.mark.asyncio
    async def test_record_document_missing(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.get_document = AsyncMock(return_value=None)
        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_file_record_missing_at_move(self, service):
        _setup_writer(service)
        service.graph_provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
        service.graph_provider.get_record_parent_info = AsyncMock(return_value={"id": "old"})
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.is_record_folder = AsyncMock(return_value=False)
        service.graph_provider.get_document = AsyncMock(
            side_effect=[{"recordName": "a.pdf"}, {"isFile": True, "mimeType": "application/pdf"}]
        )
        service.graph_provider.find_file_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider.get_file_record_by_id = AsyncMock(return_value=None)
        result = await service.move_record("kb1", "rec1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404


# ===========================================================================
# list_kb_records
# ===========================================================================


class TestListAllRecords:
    @pytest.mark.asyncio
    async def test_success(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_all_records = AsyncMock(
            return_value=([{"id": "r1"}], 1, {"recordTypes": ["FILE"]})
        )
        result = await service.list_all_records("user1", "org1", page=1, limit=10)
        assert len(result["records"]) == 1
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.list_all_records("ghost", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("db"))
        result = await service.list_all_records("user1", "org1")
        assert "error" in result
        assert result["pagination"]["totalCount"] == 0


class TestGetFolderChildren:
    @pytest.mark.asyncio
    async def test_no_access(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_success(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.get_folder_children = AsyncMock(return_value={
            "success": True,
            "totalCount": 2,
            "counts": {"folders": 1, "records": 1},
            "availableFilters": {},
        })
        result = await service.get_folder_children("kb1", "f1", "user1", page=1, limit=10)
        assert result["success"] is True
        assert result["pagination"]["totalItems"] == 2

    @pytest.mark.asyncio
    async def test_folder_not_in_kb(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=False)
        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_provider_not_found(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.get_folder_children = AsyncMock(return_value={"success": False})
        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(side_effect=RuntimeError("db"))
        result = await service.get_folder_children("kb1", "f1", "user1")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_invalid_sort_defaults(self, service):
        _setup_writer(service)
        service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
        service.graph_provider.get_folder_children = AsyncMock(return_value={
            "success": True,
            "totalCount": 1,
            "counts": {"folders": 0, "records": 1},
            "availableFilters": {},
        })
        await service.get_folder_children("kb1", "f1", "user1", sort_by="bad", sort_order="sideways")
        call_kwargs = service.graph_provider.get_folder_children.await_args.kwargs
        assert call_kwargs["sort_by"] == "name"
        assert call_kwargs["sort_order"] == "asc"


class TestGetKbChildrenExtended:
    @pytest.mark.asyncio
    async def test_provider_failure(self, service):
        _setup_writer(service)
        service.graph_provider.get_kb_children = AsyncMock(return_value={
            "success": False,
            "reason": "KB missing",
        })
        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_exception(self, service):
        _setup_writer(service)
        service.graph_provider.get_kb_children = AsyncMock(side_effect=RuntimeError("db"))
        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 500


class TestListKbRecordsExtended:
    @pytest.mark.asyncio
    async def test_exception(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_kb_records = AsyncMock(side_effect=RuntimeError("db"))
        result = await service.list_kb_records("kb1", "user1", "org1")
        assert "error" in result
        assert result["pagination"]["totalCount"] == 0


class TestListKbRecords:
    @pytest.mark.asyncio
    async def test_user_not_found(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await service.list_kb_records("kb1", "ghost", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_pagination(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.list_kb_records = AsyncMock(
            return_value=([{"id": "r1"}], 25, {"recordTypes": ["FILE"]})
        )
        result = await service.list_kb_records("kb1", "user1", "org1", page=2, limit=10)
        assert result["pagination"]["page"] == 2
        assert result["pagination"]["totalPages"] == 3


# ===========================================================================
# get_kb_children
# ===========================================================================


class TestGetKbChildren:
    @pytest.mark.asyncio
    async def test_no_access(self, service):
        service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
        service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
        result = await service.get_kb_children("kb1", "user1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_success_includes_permissions(self, service):
        _setup_writer(service)
        service.graph_provider.get_kb_children = AsyncMock(return_value={
            "success": True,
            "totalCount": 5,
            "counts": {"folders": 1, "records": 4},
            "availableFilters": {},
        })
        result = await service.get_kb_children("kb1", "user1", page=1, limit=5)
        assert result["success"] is True
        assert result["userPermission"]["canUpload"] is True

    @pytest.mark.asyncio
    async def test_invalid_sort_defaults(self, service):
        _setup_writer(service)
        service.graph_provider.get_kb_children = AsyncMock(return_value={
            "success": True,
            "totalCount": 1,
            "counts": {"folders": 0, "records": 1},
            "availableFilters": {},
        })
        await service.get_kb_children("kb1", "user1", sort_by="bad", sort_order="sideways")
        call_kwargs = service.graph_provider.get_kb_children.await_args.kwargs
        assert call_kwargs["sort_by"] == "name"
        assert call_kwargs["sort_order"] == "asc"


# ===========================================================================
# _upload_records and upload helpers
# ===========================================================================


class TestUploadRecordsPipeline:
    @pytest.mark.asyncio
    async def test_storage_url_default_on_config_error(self, service, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        url = await service._get_storage_url()
        assert url == DefaultEndpoints.STORAGE_ENDPOINT.value

    @pytest.mark.asyncio
    async def test_storage_url_from_config(self, service, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"storage": {"endpoint": "http://custom:9000"}})
        url = await service._get_storage_url()
        assert url == "http://custom:9000"

    def test_build_kb_file_record_signed_url(self, service):
        record = _minimal_upload_record_dict(recordName="report.pdf", externalRecordId="ext-99")
        file_rec = {"name": "report.pdf", "mimeType": "application/pdf", "isFile": True}
        built = service._build_kb_file_record("kb1", record, file_rec, "parent-folder", "http://storage:3001")
        assert isinstance(built, FileRecord)
        assert built.fetch_signed_url.endswith("/api/v1/document/internal/ext-99/download")

    def test_build_kb_file_record_root_no_parent(self, service):
        record = _minimal_upload_record_dict()
        file_rec = {"name": "root.pdf", "mimeType": "application/pdf", "isFile": True}
        built = service._build_kb_file_record("kb1", record, file_rec, None, "http://storage:3001")
        assert built.parent_external_record_id is None

    def test_build_kb_file_record_skips_signed_url_without_external_id(self, service):
        record = _minimal_upload_record_dict(externalRecordId="")
        file_rec = {"name": "root.pdf", "mimeType": "application/pdf", "isFile": True}
        built = service._build_kb_file_record("kb1", record, file_rec, None, "http://storage:3001")
        assert built.fetch_signed_url is None

    @pytest.mark.asyncio
    async def test_resolve_upload_folders_reuses_existing(self, service):
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(
            return_value={"_key": "existing-folder"}
        )
        folder_map, new_records = await service._resolve_upload_folders("kb1", "org1", _upload_analysis())
        assert folder_map["docs"] == "existing-folder"
        assert new_records == []

    @pytest.mark.asyncio
    async def test_resolve_upload_folders_creates_new(self, service):
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        folder_map, new_records = await service._resolve_upload_folders("kb1", "org1", _upload_analysis())
        assert folder_map["docs"]
        assert len(new_records) == 1
        assert new_records[0].is_file is False

    @pytest.mark.asyncio
    async def test_resolve_upload_folders_broken_hierarchy(self, service):
        analysis = {
            "parent_folder_id": None,
            "sorted_folder_paths": ["child"],
            "folder_hierarchy": {
                "child": {"name": "child", "parent_path": "missing-parent", "level": 2},
            },
        }
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Parent folder resolution failed"):
            await service._resolve_upload_folders("kb1", "org1", analysis)

    @pytest.mark.asyncio
    async def test_build_upload_file_records_skips_duplicate(self, service):
        service.graph_provider._fetch_existing_file_names_in_parent = AsyncMock(
            return_value={("report.pdf", "application/pdf")}
        )
        service.graph_provider._normalize_name = lambda n: n
        service.graph_provider._normalized_name_variants_lower = lambda n: [n.lower()]
        files = [{
            "filePath": "docs/report.pdf",
            "record": {"recordName": "report.pdf"},
            "fileRecord": {"name": "report.pdf", "mimeType": "application/pdf"},
        }]
        analysis = {"parent_folder_id": None, "file_destinations": {0: {"type": "root"}}}
        records, skipped, failed = await service._build_upload_file_records(
            "kb1", files, analysis, "http://storage:3001"
        )
        assert records == []
        assert skipped[0]["reason"] == "DUPLICATE_NAME"
        assert failed == []

    @pytest.mark.asyncio
    async def test_build_upload_file_records_missing_folder_id(self, service):
        service.graph_provider._fetch_existing_file_names_in_parent = AsyncMock(return_value=set())
        service.graph_provider._normalize_name = lambda n: n
        service.graph_provider._normalized_name_variants_lower = lambda n: [n.lower()]
        files = [{"filePath": "orphan.pdf", "record": {}, "fileRecord": {"name": "orphan.pdf"}}]
        analysis = {
            "parent_folder_id": "parent1",
            "file_destinations": {0: {"type": "folder", "folder_id": None}},
        }
        records, skipped, failed = await service._build_upload_file_records(
            "kb1", files, analysis, "http://storage:3001"
        )
        assert records == []
        assert failed == ["orphan.pdf"]

    @pytest.mark.asyncio
    async def test_upload_records_folders_then_files(self, service, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3001"}}
        )
        service.graph_provider._validate_upload_context = AsyncMock(return_value={"valid": True})
        service.graph_provider._analyze_upload_structure = MagicMock(return_value=_upload_analysis())
        service.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        service.graph_provider._populate_file_destinations = MagicMock()
        service.graph_provider._fetch_existing_file_names_in_parent = AsyncMock(return_value=set())
        service.graph_provider._normalize_name = lambda n: n or ""
        service.graph_provider._normalized_name_variants_lower = lambda n: [n.lower()] if n else []
        service.graph_provider._generate_upload_message = MagicMock(return_value="uploaded")
        files = [{
            "filePath": "docs/a.pdf",
            "record": _minimal_upload_record_dict(recordName="a.pdf", externalRecordId="ext-a"),
            "fileRecord": {"name": "a.pdf", "mimeType": "application/pdf", "isFile": True},
        }]
        result = await service._upload_records("kb1", "user1", "org1", files, parent_folder_id=None)
        assert result["success"] is True
        assert result["foldersCreated"] == 1
        entities = service.processor.on_new_records.await_args[0][0]
        assert entities[0][0].is_file is False
        assert entities[1][0].is_file is True

    @pytest.mark.asyncio
    async def test_upload_records_invalid_context(self, service):
        service.graph_provider._validate_upload_context = AsyncMock(
            return_value={"valid": False, "success": False, "code": 404, "reason": "KB not found"}
        )
        result = await service._upload_records("kb1", "user1", "org1", [], parent_folder_id=None)
        assert result["valid"] is False
        service.processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_records_empty_files(self, service, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"storage": {"endpoint": "http://storage:3001"}})
        service.graph_provider._validate_upload_context = AsyncMock(return_value={"valid": True})
        service.graph_provider._analyze_upload_structure = MagicMock(return_value={
            "parent_folder_id": None,
            "sorted_folder_paths": [],
            "folder_hierarchy": {},
            "file_destinations": {},
        })
        service.graph_provider._populate_file_destinations = MagicMock()
        service.graph_provider._generate_upload_message = MagicMock(return_value="done")
        result = await service._upload_records("kb1", "user1", "org1", [], parent_folder_id=None)
        assert result["success"] is True
        assert result["totalCreated"] == 0
        service.processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upload_records_unexpected_error(self, service):
        service.graph_provider._validate_upload_context = AsyncMock(side_effect=RuntimeError("boom"))
        result = await service._upload_records("kb1", "user1", "org1", [], parent_folder_id=None)
        assert result["success"] is False
        assert result["code"] == 500
