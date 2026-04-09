"""
Extended unit tests for ArangoHTTPProvider covering uncovered methods:
- ensure_schema (full success path, collection creation, graph creation)
- _create_typed_record_from_arango (all record types and fallback)
- _check_record_group_permissions (allowed, denied, exception)
- check_connector_name_exists (personal scope, team scope, exception)
- batch_update_connector_status (success, empty keys, exception)
- get_user_connector_instances (success, empty, exception)
- get_filtered_connector_instances (various filters, scope counts, exception)
- reindex_record_group_records (success, missing group, missing user, permission denied)
- _reset_indexing_status_to_queued (success, already queued, not found, exception)
- _ensure_departments_seed (success with new departments)
- _ensure_indexes
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arango.arango_http_provider import (
    ARANGO_ID_PARTS_COUNT,
    MAX_REINDEX_DEPTH,
    ArangoHTTPProvider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "url": "http://localhost:8529",
        "username": "root",
        "password": "secret",
        "db": "test_db",
    })
    return cs


@pytest.fixture
def provider(mock_logger, mock_config_service):
    return ArangoHTTPProvider(mock_logger, mock_config_service)


@pytest.fixture
def connected_provider(provider):
    """Provider with a mock http_client already attached."""
    provider.http_client = AsyncMock()
    return provider


# ---------------------------------------------------------------------------
# ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchemaExtended:
    @pytest.mark.asyncio
    async def test_ensure_schema_success_creates_collections_and_graph(self, connected_provider):
        """Full happy path: creates missing collections, graph, seeds departments."""
        client = connected_provider.http_client
        client.has_collection = AsyncMock(return_value=False)
        client.create_collection = AsyncMock(return_value=True)
        client.has_graph = AsyncMock(return_value=False)
        client.create_graph = AsyncMock(return_value=True)
        # _ensure_departments_seed
        connected_provider.execute_query = AsyncMock(return_value=[])
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        result = await connected_provider.ensure_schema()
        assert result is True
        assert client.create_collection.call_count > 0

    @pytest.mark.asyncio
    async def test_ensure_schema_existing_collections_and_graph(self, connected_provider):
        """All collections and graph already exist."""
        client = connected_provider.http_client
        client.has_collection = AsyncMock(return_value=True)
        client.create_collection = AsyncMock(return_value=True)
        client.has_graph = AsyncMock(return_value=True)
        connected_provider.execute_query = AsyncMock(return_value=[])
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        result = await connected_provider.ensure_schema()
        assert result is True
        client.create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_schema_collection_creation_fails_continues(self, connected_provider):
        """If a collection creation fails, schema continues."""
        client = connected_provider.http_client
        client.has_collection = AsyncMock(return_value=False)
        client.create_collection = AsyncMock(return_value=False)
        client.has_graph = AsyncMock(return_value=True)
        connected_provider.execute_query = AsyncMock(return_value=[])
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        result = await connected_provider.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_ensure_schema_graph_creation_no_valid_definitions(self, connected_provider):
        """Graph creation with no valid edge definitions."""
        client = connected_provider.http_client
        client.has_collection = AsyncMock(return_value=False)
        client.create_collection = AsyncMock(return_value=True)
        client.has_graph = AsyncMock(return_value=False)
        # has_collection for edge definitions returns False
        async def has_collection_side_effect(name):
            return False
        client.has_collection = AsyncMock(side_effect=has_collection_side_effect)
        client.create_collection = AsyncMock(return_value=True)
        connected_provider.execute_query = AsyncMock(return_value=[])
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=True)

        result = await connected_provider.ensure_schema()
        assert result is True


# ---------------------------------------------------------------------------
# _create_typed_record_from_arango
# ---------------------------------------------------------------------------


class TestCreateTypedRecordFromArango:
    def test_no_type_doc_returns_base_record(self, provider):
        record_dict = {
            "_key": "r1", "recordType": "FILE", "orgId": "org1",
            "recordName": "test.txt", "externalRecordId": "ext1",
            "version": 1, "origin": "CONNECTOR",
            "connectorId": "c1",
            "createdAtTimestamp": 1700000000000,
            "updatedAtTimestamp": 1700000000000,
        }
        result = provider._create_typed_record_from_arango(record_dict, None)
        assert result is not None

    def test_unknown_record_type_returns_base_record(self, provider):
        # Use a valid RecordType that is NOT in RECORD_TYPE_COLLECTION_MAPPING
        # so the code falls back to base Record
        record_dict = {
            "_key": "r1", "recordType": "OTHERS", "orgId": "org1",
            "recordName": "test.txt", "externalRecordId": "ext1",
            "version": 1, "origin": "CONNECTOR",
            "connectorId": "c1",
            "createdAtTimestamp": 1700000000000,
            "updatedAtTimestamp": 1700000000000,
        }
        type_doc = {"_key": "t1"}
        result = provider._create_typed_record_from_arango(record_dict, type_doc)
        assert result is not None

    @patch("app.services.graph_db.arango.arango_http_provider.FileRecord")
    def test_file_record_type(self, mock_file_record, provider):
        mock_file_record.from_arango_record = MagicMock(return_value=MagicMock())
        record_dict = {"_key": "r1", "recordType": "FILE", "orgId": "org1"}
        type_doc = {"_key": "t1"}
        result = provider._create_typed_record_from_arango(record_dict, type_doc)
        # _translate_node_from_arango converts _key to id
        mock_file_record.from_arango_record.assert_called_once_with(
            {"id": "t1"}, {"recordType": "FILE", "orgId": "org1", "id": "r1"}
        )

    @patch("app.services.graph_db.arango.arango_http_provider.MailRecord")
    def test_mail_record_type(self, mock_mail_record, provider):
        mock_mail_record.from_arango_record = MagicMock(return_value=MagicMock())
        record_dict = {"_key": "r1", "recordType": "MAIL", "orgId": "org1"}
        type_doc = {"_key": "t1"}
        provider._create_typed_record_from_arango(record_dict, type_doc)
        mock_mail_record.from_arango_record.assert_called_once()

    @patch("app.services.graph_db.arango.arango_http_provider.WebpageRecord")
    def test_webpage_record_type(self, mock_webpage, provider):
        mock_webpage.from_arango_record = MagicMock(return_value=MagicMock())
        record_dict = {"_key": "r1", "recordType": "WEBPAGE", "orgId": "org1"}
        type_doc = {"_key": "t1"}
        provider._create_typed_record_from_arango(record_dict, type_doc)
        mock_webpage.from_arango_record.assert_called_once()

    @patch("app.services.graph_db.arango.arango_http_provider.TicketRecord")
    def test_ticket_record_type(self, mock_ticket, provider):
        mock_ticket.from_arango_record = MagicMock(return_value=MagicMock())
        record_dict = {"_key": "r1", "recordType": "TICKET", "orgId": "org1"}
        type_doc = {"_key": "t1"}
        provider._create_typed_record_from_arango(record_dict, type_doc)
        mock_ticket.from_arango_record.assert_called_once()

    @patch("app.services.graph_db.arango.arango_http_provider.CommentRecord")
    def test_comment_record_type(self, mock_comment, provider):
        mock_comment.from_arango_record = MagicMock(return_value=MagicMock())
        record_dict = {"_key": "r1", "recordType": "COMMENT", "orgId": "org1"}
        type_doc = {"_key": "t1"}
        provider._create_typed_record_from_arango(record_dict, type_doc)
        mock_comment.from_arango_record.assert_called_once()

    @patch("app.services.graph_db.arango.arango_http_provider.LinkRecord")
    def test_link_record_type(self, mock_link, provider):
        mock_link.from_arango_record = MagicMock(return_value=MagicMock())
        record_dict = {"_key": "r1", "recordType": "LINK", "orgId": "org1"}
        type_doc = {"_key": "t1"}
        provider._create_typed_record_from_arango(record_dict, type_doc)
        mock_link.from_arango_record.assert_called_once()

    @patch("app.services.graph_db.arango.arango_http_provider.ProjectRecord")
    def test_project_record_type(self, mock_project, provider):
        mock_project.from_arango_record = MagicMock(return_value=MagicMock())
        record_dict = {"_key": "r1", "recordType": "PROJECT", "orgId": "org1"}
        type_doc = {"_key": "t1"}
        provider._create_typed_record_from_arango(record_dict, type_doc)
        mock_project.from_arango_record.assert_called_once()

    def test_typed_record_exception_falls_back(self, provider):
        """If from_arango_record raises, fall back to base Record."""
        record_dict = {
            "_key": "r1", "recordType": "FILE", "orgId": "org1",
            "recordName": "test.txt", "externalRecordId": "ext1",
            "version": 1, "origin": "CONNECTOR",
            "connectorId": "c1",
            "createdAtTimestamp": 1700000000000,
            "updatedAtTimestamp": 1700000000000,
        }
        type_doc = {"_key": "t1"}
        with patch("app.services.graph_db.arango.arango_http_provider.FileRecord") as mock_fr:
            mock_fr.from_arango_record = MagicMock(side_effect=Exception("parse error"))
            result = provider._create_typed_record_from_arango(record_dict, type_doc)
            assert result is not None


# ---------------------------------------------------------------------------
# get_record_by_id
# ---------------------------------------------------------------------------


class TestGetRecordByIdExtended:
    @pytest.mark.asyncio
    async def test_get_record_by_id_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"record": {"_key": "r1", "recordType": "FILE", "orgId": "org1"}, "typeDoc": {"_key": "t1"}}
        ])
        with patch.object(connected_provider, "_create_typed_record_from_arango", return_value=MagicMock()):
            result = await connected_provider.get_record_by_id("r1")
            assert result is not None

    @pytest.mark.asyncio
    async def test_get_record_by_id_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_id("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_record_by_id_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.get_record_by_id("r1")
        assert result is None


# ---------------------------------------------------------------------------
# _check_record_group_permissions
# ---------------------------------------------------------------------------


class TestCheckRecordGroupPermissions:
    @pytest.mark.asyncio
    async def test_permission_allowed(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"allowed": True, "role": "OWNER"}
        ])
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is True
        assert result["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_permission_denied(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"allowed": False, "role": None}
        ])
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_permission_empty_results(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_permission_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# check_connector_name_exists
# ---------------------------------------------------------------------------


class TestCheckConnectorNameExists:
    @pytest.mark.asyncio
    async def test_personal_scope_name_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="My Connector",
            scope="personal", org_id="org1", user_id="user1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_personal_scope_name_not_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="My Connector",
            scope="personal", org_id="org1", user_id="user1"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_team_scope_name_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="Team Connector",
            scope="team", org_id="org1", user_id="user1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.check_connector_name_exists(
            collection="apps", instance_name="Test",
            scope="personal", org_id="org1", user_id="user1"
        )
        assert result is False


# ---------------------------------------------------------------------------
# batch_update_connector_status
# ---------------------------------------------------------------------------


class TestBatchUpdateConnectorStatus:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "k1"}, {"_key": "k2"}])
        result = await connected_provider.batch_update_connector_status(
            collection="apps", connector_keys=["k1", "k2"],
            is_active=False, is_agent_active=False
        )
        assert result == 2

    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        result = await connected_provider.batch_update_connector_status(
            collection="apps", connector_keys=[],
            is_active=False, is_agent_active=False
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.batch_update_connector_status(
            collection="apps", connector_keys=["k1"],
            is_active=False, is_agent_active=False
        )
        assert result == 0


# ---------------------------------------------------------------------------
# get_user_connector_instances
# ---------------------------------------------------------------------------


class TestGetUserConnectorInstances:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"_key": "c1", "type": "Gmail", "scope": "personal"},
        ])
        result = await connected_provider.get_user_connector_instances(
            collection="apps", user_id="user1", org_id="org1",
            team_scope="team", personal_scope="personal"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=None)
        result = await connected_provider.get_user_connector_instances(
            collection="apps", user_id="user1", org_id="org1",
            team_scope="team", personal_scope="personal"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.get_user_connector_instances(
            collection="apps", user_id="user1", org_id="org1",
            team_scope="team", personal_scope="personal"
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_filtered_connector_instances
# ---------------------------------------------------------------------------


class TestGetFilteredConnectorInstances:
    @pytest.mark.asyncio
    async def test_basic_no_filters(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [5],  # count query
            [0],  # personal count
            [{"_key": "c1"}, {"_key": "c2"}],  # main query
        ])
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            collection="apps", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1"
        )
        assert total == 5
        assert len(docs) == 2

    @pytest.mark.asyncio
    async def test_personal_scope_filter(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [3],  # count
            [3],  # personal count
            [{"_key": "c1"}],  # results
        ])
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            collection="apps", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", scope="personal"
        )
        assert total == 3

    @pytest.mark.asyncio
    async def test_team_scope_with_admin(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [10],  # count
            [2],  # personal count
            [5],  # team count
            [{"_key": "c1"}],  # results
        ])
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            collection="apps", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", scope="team", is_admin=True,
            kb_connector_type="KB"
        )
        assert total == 10

    @pytest.mark.asyncio
    async def test_with_search(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=[
            [1],  # count
            [0],  # personal count
            [{"_key": "c1"}],  # results
        ])
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            collection="apps", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1", search="gmail"
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            collection="apps", edge_collection="orgAppRelation",
            org_id="org1", user_id="user1"
        )
        assert docs == []
        assert total == 0


# ---------------------------------------------------------------------------
# reindex_record_group_records
# ---------------------------------------------------------------------------


class TestReindexRecordGroupRecords:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "rg1", "connectorId": "conn1", "connectorName": "Gmail"
        })
        connected_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "u1", "_key": "u1"
        })
        connected_provider._check_record_group_permissions = AsyncMock(return_value={
            "allowed": True, "role": "OWNER"
        })

        result = await connected_provider.reindex_record_group_records(
            "rg1", depth=5, user_id="user1", org_id="org1"
        )
        assert result["success"] is True
        assert result["connectorId"] == "conn1"

    @pytest.mark.asyncio
    async def test_depth_minus_one_becomes_max(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "rg1", "connectorId": "conn1", "connectorName": "Gmail"
        })
        connected_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "u1", "_key": "u1"
        })
        connected_provider._check_record_group_permissions = AsyncMock(return_value={
            "allowed": True
        })

        result = await connected_provider.reindex_record_group_records(
            "rg1", depth=-1, user_id="user1", org_id="org1"
        )
        assert result["success"] is True
        assert result["depth"] == MAX_REINDEX_DEPTH

    @pytest.mark.asyncio
    async def test_negative_depth_becomes_zero(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "rg1", "connectorId": "conn1", "connectorName": "Gmail"
        })
        connected_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "u1", "_key": "u1"
        })
        connected_provider._check_record_group_permissions = AsyncMock(return_value={
            "allowed": True
        })

        result = await connected_provider.reindex_record_group_records(
            "rg1", depth=-5, user_id="user1", org_id="org1"
        )
        assert result["depth"] == 0

    @pytest.mark.asyncio
    async def test_record_group_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        result = await connected_provider.reindex_record_group_records(
            "nonexistent", depth=5, user_id="user1", org_id="org1"
        )
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_connector_id(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"id": "rg1"})
        result = await connected_provider.reindex_record_group_records(
            "rg1", depth=5, user_id="user1", org_id="org1"
        )
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "rg1", "connectorId": "conn1", "connectorName": "Gmail"
        })
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.reindex_record_group_records(
            "rg1", depth=5, user_id="user1", org_id="org1"
        )
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_permission_denied(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "rg1", "connectorId": "conn1", "connectorName": "Gmail"
        })
        connected_provider.get_user_by_user_id = AsyncMock(return_value={
            "id": "u1", "_key": "u1"
        })
        connected_provider._check_record_group_permissions = AsyncMock(return_value={
            "allowed": False, "reason": "No permission"
        })
        result = await connected_provider.reindex_record_group_records(
            "rg1", depth=5, user_id="user1", org_id="org1"
        )
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.reindex_record_group_records(
            "rg1", depth=5, user_id="user1", org_id="org1"
        )
        assert result["success"] is False
        assert result["code"] == 500


# ---------------------------------------------------------------------------
# _reset_indexing_status_to_queued
# ---------------------------------------------------------------------------


class TestResetIndexingStatusToQueued:
    @pytest.mark.asyncio
    async def test_resets_to_queued(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "r1", "indexingStatus": "COMPLETED"
        })
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._reset_indexing_status_to_queued("r1")
        connected_provider.batch_upsert_nodes.assert_called_once()

    @pytest.mark.asyncio
    async def test_already_queued_skips(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "r1", "indexingStatus": "QUEUED"
        })
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._reset_indexing_status_to_queued("r1")
        connected_provider.batch_upsert_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_status_skips(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={
            "id": "r1", "indexingStatus": "EMPTY"
        })
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._reset_indexing_status_to_queued("r1")
        connected_provider.batch_upsert_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._reset_indexing_status_to_queued("r1")
        connected_provider.batch_upsert_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_handled(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("db error"))
        # Should not raise
        await connected_provider._reset_indexing_status_to_queued("r1")


# ---------------------------------------------------------------------------
# Translation layer
# ---------------------------------------------------------------------------


class TestTranslationLayer:
    def test_translate_node_to_arango(self, provider):
        node = {"id": "key1", "name": "test"}
        result = provider._translate_node_to_arango(node)
        assert result["_key"] == "key1"
        assert "id" not in result

    def test_translate_node_to_arango_no_id(self, provider):
        node = {"name": "test"}
        result = provider._translate_node_to_arango(node)
        assert "id" not in result
        assert "_key" not in result
        assert result["name"] == "test"

    def test_translate_node_from_arango(self, provider):
        arango_node = {"_key": "key1", "name": "test"}
        result = provider._translate_node_from_arango(arango_node)
        assert result["id"] == "key1"
        assert "_key" not in result

    def test_translate_node_from_arango_no_key(self, provider):
        arango_node = {"name": "test"}
        result = provider._translate_node_from_arango(arango_node)
        assert "id" not in result
        assert "_key" not in result

    def test_translate_edge_to_arango_generic_format(self, provider):
        edge = {
            "from_id": "u1",
            "from_collection": "users",
            "to_id": "r1",
            "to_collection": "records",
            "role": "OWNER",
        }
        result = provider._translate_edge_to_arango(edge)
        assert result["_from"] == "users/u1"
        assert result["_to"] == "records/r1"
        assert "from_id" not in result
        assert "to_id" not in result

    def test_translate_edge_to_arango_old_format(self, provider):
        edge = {"_from": "users/u1", "_to": "records/r1"}
        result = provider._translate_edge_to_arango(edge)
        assert result["_from"] == "users/u1"
        assert result["_to"] == "records/r1"

    def test_translate_edge_from_arango(self, provider):
        arango_edge = {"_from": "users/u1", "_to": "records/r1", "role": "OWNER"}
        result = provider._translate_edge_from_arango(arango_edge)
        assert result["from_collection"] == "users"
        assert result["from_id"] == "u1"
        assert result["to_collection"] == "records"
        assert result["to_id"] == "r1"
        assert "_from" not in result
        assert "_to" not in result

    def test_translate_edge_from_arango_no_from_to(self, provider):
        arango_edge = {"role": "READER"}
        result = provider._translate_edge_from_arango(arango_edge)
        assert "from_id" not in result
        assert "to_id" not in result

    def test_translate_nodes_to_arango_batch(self, provider):
        nodes = [{"id": "k1"}, {"id": "k2"}]
        result = provider._translate_nodes_to_arango(nodes)
        assert len(result) == 2
        assert result[0]["_key"] == "k1"
        assert result[1]["_key"] == "k2"

    def test_translate_nodes_from_arango_batch(self, provider):
        nodes = [{"_key": "k1"}, {"_key": "k2"}]
        result = provider._translate_nodes_from_arango(nodes)
        assert result[0]["id"] == "k1"
        assert result[1]["id"] == "k2"

    def test_translate_edges_to_arango_batch(self, provider):
        edges = [{"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records"}]
        result = provider._translate_edges_to_arango(edges)
        assert result[0]["_from"] == "users/u1"

    def test_translate_edges_from_arango_batch(self, provider):
        edges = [{"_from": "users/u1", "_to": "records/r1"}]
        result = provider._translate_edges_from_arango(edges)
        assert result[0]["from_id"] == "u1"


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, provider):
        provider.config_service.get_config = AsyncMock(return_value={
            "url": "http://localhost:8529",
            "username": "root",
            "password": "secret",
            "db": "test_db",
        })
        with patch("app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client.database_exists = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()
            assert result is True

    @pytest.mark.asyncio
    async def test_connect_no_config(self, provider):
        provider.config_service.get_config = AsyncMock(return_value=None)
        result = await provider.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_db_not_exist_creates(self, provider):
        provider.config_service.get_config = AsyncMock(return_value={
            "url": "http://localhost:8529",
            "username": "root",
            "password": "secret",
            "db": "new_db",
        })
        with patch("app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client.database_exists = AsyncMock(return_value=False)
            mock_client.create_database = AsyncMock(return_value=True)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()
            assert result is True
            mock_client.create_database.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_db_creation_fails(self, provider):
        provider.config_service.get_config = AsyncMock(return_value={
            "url": "http://localhost:8529",
            "username": "root",
            "password": "secret",
            "db": "fail_db",
        })
        with patch("app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client.database_exists = AsyncMock(return_value=False)
            mock_client.create_database = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()
            assert result is False

    @pytest.mark.asyncio
    async def test_connect_fails(self, provider):
        provider.config_service.get_config = AsyncMock(return_value={
            "url": "http://localhost:8529",
            "username": "root",
            "password": "secret",
            "db": "test_db",
        })
        with patch("app.services.graph_db.arango.arango_http_provider.ArangoHTTPClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.connect = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.connect()
            assert result is False


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_success(self, connected_provider):
        connected_provider.http_client.disconnect = AsyncMock()
        result = await connected_provider.disconnect()
        assert result is True
        assert connected_provider.http_client is None

    @pytest.mark.asyncio
    async def test_disconnect_no_client(self, provider):
        provider.http_client = None
        result = await provider.disconnect()
        assert result is True

    @pytest.mark.asyncio
    async def test_disconnect_exception(self, connected_provider):
        connected_provider.http_client.disconnect = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.disconnect()
        assert result is False


# ---------------------------------------------------------------------------
# Transaction management
# ---------------------------------------------------------------------------


class TestTransactions:
    @pytest.mark.asyncio
    async def test_begin_transaction(self, connected_provider):
        connected_provider.http_client.begin_transaction = AsyncMock(return_value="txn1")
        result = await connected_provider.begin_transaction(["col1"], ["col2"])
        assert result == "txn1"

    @pytest.mark.asyncio
    async def test_begin_transaction_error(self, connected_provider):
        connected_provider.http_client.begin_transaction = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.begin_transaction([], [])

    @pytest.mark.asyncio
    async def test_commit_transaction(self, connected_provider):
        connected_provider.http_client.commit_transaction = AsyncMock()
        await connected_provider.commit_transaction("txn1")

    @pytest.mark.asyncio
    async def test_commit_transaction_error(self, connected_provider):
        connected_provider.http_client.commit_transaction = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.commit_transaction("txn1")

    @pytest.mark.asyncio
    async def test_rollback_transaction(self, connected_provider):
        connected_provider.http_client.abort_transaction = AsyncMock()
        await connected_provider.rollback_transaction("txn1")

    @pytest.mark.asyncio
    async def test_rollback_transaction_error(self, connected_provider):
        connected_provider.http_client.abort_transaction = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.rollback_transaction("txn1")


# ---------------------------------------------------------------------------
# Document Operations
# ---------------------------------------------------------------------------


class TestGetDocument:
    @pytest.mark.asyncio
    async def test_get_document_found(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(return_value={"_key": "k1", "name": "doc"})
        result = await connected_provider.get_document("k1", "records")
        assert result["id"] == "k1"
        assert result["name"] == "doc"

    @pytest.mark.asyncio
    async def test_get_document_not_found(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(return_value=None)
        result = await connected_provider.get_document("k1", "records")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_exception(self, connected_provider):
        connected_provider.http_client.get_document = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_document("k1", "records")
        assert result is None


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------


class TestBatchUpsertNodes:
    @pytest.mark.asyncio
    async def test_empty_list(self, connected_provider):
        result = await connected_provider.batch_upsert_nodes([], "records")
        assert result is True

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.batch_insert_documents = AsyncMock(return_value={"errors": 0})
        result = await connected_provider.batch_upsert_nodes([{"id": "k1"}], "records")
        assert result is True

    @pytest.mark.asyncio
    async def test_with_errors(self, connected_provider):
        connected_provider.http_client.batch_insert_documents = AsyncMock(return_value={"errors": 2})
        result = await connected_provider.batch_upsert_nodes([{"id": "k1"}], "records")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.batch_insert_documents = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.batch_upsert_nodes([{"id": "k1"}], "records")


class TestDeleteNodes:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.batch_delete_documents = AsyncMock(return_value=2)
        result = await connected_provider.delete_nodes(["k1", "k2"], "records")
        assert result is True

    @pytest.mark.asyncio
    async def test_partial_delete(self, connected_provider):
        connected_provider.http_client.batch_delete_documents = AsyncMock(return_value=1)
        result = await connected_provider.delete_nodes(["k1", "k2"], "records")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.batch_delete_documents = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.delete_nodes(["k1"], "records")


class TestUpdateNode:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.update_document = AsyncMock(return_value={"_key": "k1"})
        result = await connected_provider.update_node("k1", "records", {"name": "new"})
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.update_document = AsyncMock(return_value=None)
        result = await connected_provider.update_node("k1", "records", {"name": "new"})
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.update_document = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.update_node("k1", "records", {})


# ---------------------------------------------------------------------------
# Edge Operations
# ---------------------------------------------------------------------------


class TestBatchCreateEdges:
    @pytest.mark.asyncio
    async def test_empty_edges(self, connected_provider):
        result = await connected_provider.batch_create_edges([], "permission")
        assert result is True

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "e1"}])
        edges = [{"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records"}]
        result = await connected_provider.batch_create_edges(edges, "permission")
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        edges = [{"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records"}]
        with pytest.raises(Exception, match="fail"):
            await connected_provider.batch_create_edges(edges, "permission")


class TestBatchCreateEntityRelations:
    @pytest.mark.asyncio
    async def test_empty_edges(self, connected_provider):
        result = await connected_provider.batch_create_entity_relations([])
        assert result is True

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "e1"}])
        edges = [{"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records", "edgeType": "ASSIGNED"}]
        result = await connected_provider.batch_create_entity_relations(edges)
        assert result is True


class TestGetEdge:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[
            {"_from": "users/u1", "_to": "records/r1", "role": "OWNER"}
        ])
        result = await connected_provider.get_edge("u1", "users", "r1", "records", "permission")
        assert result["from_id"] == "u1"
        assert result["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_edge("u1", "users", "r1", "records", "permission")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_edge("u1", "users", "r1", "records", "permission")
        assert result is None


class TestDeleteEdge:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.delete_edge = AsyncMock(return_value=True)
        result = await connected_provider.delete_edge("u1", "users", "r1", "records", "permission")
        assert result is True


class TestDeleteEdgesFrom:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}, {}])
        result = await connected_provider.delete_edges_from("u1", "users", "permission")
        assert result == 3

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.delete_edges_from("u1", "users", "permission")


class TestDeleteEdgesTo:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider.delete_edges_to("r1", "records", "permission")
        assert result == 2


class TestDeleteEdgesByRelationshipTypes:
    @pytest.mark.asyncio
    async def test_empty_types(self, connected_provider):
        result = await connected_provider.delete_edges_by_relationship_types("u1", "users", "entityRelations", [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}])
        result = await connected_provider.delete_edges_by_relationship_types("u1", "users", "entityRelations", ["ASSIGNED_TO"])
        assert result == 1


# ---------------------------------------------------------------------------
# Query Operations
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "k1"}])
        result = await connected_provider.execute_query("FOR doc IN records RETURN doc")
        assert result == [{"_key": "k1"}]

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.execute_query("FOR doc IN records RETURN doc")


class TestGetNodesByFilters:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"name": "test"}])
        result = await connected_provider.get_nodes_by_filters("records", {"orgId": "org1"})
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"name": "test"}])
        result = await connected_provider.get_nodes_by_filters("records", {"orgId": "org1"}, return_fields=["name"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_nodes_by_filters("records", {"orgId": "org1"})
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_nodes_by_filters("records", {"orgId": "org1"})
        assert result == []


class TestGetNodesByFieldIn:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"name": "a"}])
        result = await connected_provider.get_nodes_by_field_in("records", "orgId", ["org1", "org2"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"name": "a"}])
        result = await connected_provider.get_nodes_by_field_in("records", "orgId", ["org1"], return_fields=["name"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_result(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_nodes_by_field_in("records", "orgId", ["org1"])
        assert result == []


class TestRemoveNodesByField:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider.remove_nodes_by_field("records", "orgId", field_value="org1")
        assert result == 2

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await connected_provider.remove_nodes_by_field("records", "orgId", field_value="org1")


class TestGetEdgesToNode:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_from": "u1", "_to": "r1"}])
        result = await connected_provider.get_edges_to_node("records/r1", "permission")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=None)
        result = await connected_provider.get_edges_to_node("records/r1", "permission")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("fail"))
        result = await connected_provider.get_edges_to_node("records/r1", "permission")
        assert result == []


class TestGetEdgesFromNode:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_from": "u1", "_to": "r1"}])
        result = await connected_provider.get_edges_from_node("users/u1", "permission")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _check_record_permissions
# ---------------------------------------------------------------------------


class TestCheckRecordPermissions:
    @pytest.mark.asyncio
    async def test_permission_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"permission": "OWNER", "source": "DIRECT"}
        ])
        result = await connected_provider._check_record_permissions("r1", "u1")
        assert result["permission"] == "OWNER"
        assert result["source"] == "DIRECT"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"permission": None, "source": "NONE"}
        ])
        result = await connected_provider._check_record_permissions("r1", "u1")
        assert result["permission"] is None
        assert result["source"] == "NONE"

    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._check_record_permissions("r1", "u1")
        assert result["permission"] is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider._check_record_permissions("r1", "u1")
        assert result["permission"] is None
        assert result["source"] == "ERROR"

    @pytest.mark.asyncio
    async def test_disable_drive_inheritance(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[
            {"permission": "READER", "source": "DOMAIN"}
        ])
        result = await connected_provider._check_record_permissions("r1", "u1", check_drive_inheritance=False)
        assert result["permission"] == "READER"


# ---------------------------------------------------------------------------
# ensure_schema - edge case: not connected
# ---------------------------------------------------------------------------


class TestEnsureSchemaNotConnected:
    @pytest.mark.asyncio
    async def test_ensure_schema_not_connected(self, provider):
        provider.http_client = None
        result = await provider.ensure_schema()
        assert result is False


# ---------------------------------------------------------------------------
# _ensure_indexes
# ---------------------------------------------------------------------------


class TestEnsureIndexes:
    @pytest.mark.asyncio
    async def test_ensure_indexes_success(self, connected_provider):
        connected_provider.http_client.ensure_persistent_index = AsyncMock()
        await connected_provider._ensure_indexes()
        connected_provider.http_client.ensure_persistent_index.assert_called_once()


# ---------------------------------------------------------------------------
# _ensure_departments_seed
# ---------------------------------------------------------------------------


class TestEnsureDepartmentsSeed:
    @pytest.mark.asyncio
    async def test_seed_new_departments(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        await connected_provider._ensure_departments_seed()
        connected_provider.batch_upsert_nodes.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_departments_exist(self, connected_provider):
        from app.config.constants.arangodb import DepartmentNames
        existing = [d.value for d in DepartmentNames]
        connected_provider.execute_query = AsyncMock(return_value=existing)
        connected_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        await connected_provider._ensure_departments_seed()
        connected_provider.batch_upsert_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("fail"))
        # Should not raise
        await connected_provider._ensure_departments_seed()
