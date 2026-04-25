import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arango.arango_http_provider import (
    ARANGO_ID_PARTS_COUNT,
    MAX_REINDEX_DEPTH,
    ArangoHTTPProvider,
)


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
    provider.http_client = AsyncMock()
    return provider


class TestCreateTypedRecordFromArango:
    def test_no_type_doc_raises(self, provider):
        record_dict = {"_key": "r1", "recordType": "FILE"}
        with pytest.raises(ValueError, match="No type collection or no type doc"):
            provider._create_typed_record_from_arango(record_dict, None)

    def test_unknown_record_type_raises(self, provider):
        record_dict = {"_key": "r1", "recordType": "UNKNOWN_TYPE"}
        with pytest.raises(ValueError, match="No type collection or no type doc"):
            provider._create_typed_record_from_arango(record_dict, {"some": "doc"})

    def test_file_record_type(self, provider):
        record_dict = {"_key": "r1", "recordType": "FILE"}
        type_doc = {"fileSize": 100}
        with patch("app.services.graph_db.arango.arango_http_provider.FileRecord") as mock_fr:
            mock_fr.from_arango_record.return_value = "file_record"
            with patch("app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING", {"FILE": "files"}):
                result = provider._create_typed_record_from_arango(record_dict, type_doc)
                assert result == "file_record"

    def test_mail_record_type(self, provider):
        record_dict = {"_key": "r1", "recordType": "MAIL"}
        type_doc = {"subject": "test"}
        with patch("app.services.graph_db.arango.arango_http_provider.MailRecord") as mock_mr:
            mock_mr.from_arango_record.return_value = "mail_record"
            with patch("app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING", {"MAIL": "mails"}):
                result = provider._create_typed_record_from_arango(record_dict, type_doc)
                assert result == "mail_record"

    def test_exception_in_from_arango_record_raises(self, provider):
        record_dict = {"_key": "r1", "recordType": "FILE"}
        type_doc = {"bad": "data"}
        with patch("app.services.graph_db.arango.arango_http_provider.RECORD_TYPE_COLLECTION_MAPPING", {"FILE": "files"}):
            with patch("app.services.graph_db.arango.arango_http_provider.FileRecord") as mock_fr:
                mock_fr.from_arango_record.side_effect = Exception("parse error")
                with pytest.raises(ValueError, match="Failed to create typed record for FILE"):
                    provider._create_typed_record_from_arango(record_dict, type_doc)


class TestGetRecordById:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{
            "record": {"_key": "r1", "recordType": "FILE"},
            "typeDoc": {"fileSize": 100},
        }])
        with patch.object(connected_provider, "_create_typed_record_from_arango", return_value="typed"):
            result = await connected_provider.get_record_by_id("r1")
            assert result == "typed"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_id("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await connected_provider.get_record_by_id("r1")
        assert result is None


class TestCheckRecordGroupPermissions:
    @pytest.mark.asyncio
    async def test_allowed(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"allowed": True, "role": "OWNER"}])
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is True
        assert result["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_denied(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"allowed": False}])
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_empty_results(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider._check_record_group_permissions("rg1", "u1", "org1")
        assert result["allowed"] is False


class TestCheckConnectorNameExists:
    @pytest.mark.asyncio
    async def test_personal_scope_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider.check_connector_name_exists(
            "apps", "My Connector", "personal", user_id="u1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_personal_scope_not_exists(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        result = await connected_provider.check_connector_name_exists(
            "apps", "My Connector", "personal", user_id="u1"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_team_scope(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["key1"])
        result = await connected_provider.check_connector_name_exists(
            "apps", "Connector", "team", org_id="org1"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.check_connector_name_exists(
            "apps", "Connector", "personal", user_id="u1"
        )
        assert result is False


class TestBatchUpdateConnectorStatus:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{}, {}])
        result = await connected_provider.batch_update_connector_status(
            "apps", ["k1", "k2"], is_active=True, is_agent_active=False
        )
        assert result == 2

    @pytest.mark.asyncio
    async def test_empty_keys(self, connected_provider):
        result = await connected_provider.batch_update_connector_status(
            "apps", [], is_active=True, is_agent_active=False
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.batch_update_connector_status(
            "apps", ["k1"], is_active=True, is_agent_active=False
        )
        assert result == 0


class TestGetUserConnectorInstances:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"_key": "d1"}])
        result = await connected_provider.get_user_connector_instances(
            "apps", "u1", "org1", "team", "personal"
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_user_connector_instances(
            "apps", "u1", "org1", "team", "personal"
        )
        assert result == []


class TestGetFilteredConnectorInstances:
    @pytest.mark.asyncio
    async def test_with_personal_scope(self, connected_provider):
        async def mock_query(query, bind_vars=None, transaction=None):
            if "COUNT" in query or "COLLECT" in query:
                return [5]
            return [{"_key": "d1"}]
        connected_provider.execute_query = mock_query
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            "apps", "edges", "org1", "u1", scope="personal"
        )
        assert total == 5

    @pytest.mark.asyncio
    async def test_with_team_scope_admin(self, connected_provider):
        async def mock_query(query, bind_vars=None, transaction=None):
            if "COLLECT" in query:
                return [3]
            return [{"_key": "d1"}]
        connected_provider.execute_query = mock_query
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            "apps", "edges", "org1", "u1", scope="team", is_admin=True
        )
        assert isinstance(scope_counts, dict)

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        docs, total, scope_counts = await connected_provider.get_filtered_connector_instances(
            "apps", "edges", "org1", "u1"
        )
        assert docs == []
        assert total == 0


class TestReindexRecordGroupRecords:
    @pytest.mark.asyncio
    async def test_depth_minus_one_normalizes(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider._check_record_group_permissions = AsyncMock(return_value={"allowed": True, "role": "OWNER"})
        result = await connected_provider.reindex_record_group_records("rg1", -1, "u1", "org1")
        assert result["success"] is True
        assert result["depth"] == MAX_REINDEX_DEPTH

    @pytest.mark.asyncio
    async def test_record_group_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        result = await connected_provider.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_connector_id(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"id": "rg1"})
        result = await connected_provider.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_permission_denied(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider._check_record_group_permissions = AsyncMock(return_value={"allowed": False, "reason": "no access"})
        result = await connected_provider.reindex_record_group_records("rg1", 0, "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_negative_depth(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"id": "rg1", "connectorId": "c1", "connectorName": "Drive"})
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider._check_record_group_permissions = AsyncMock(return_value={"allowed": True, "role": "OWNER"})
        result = await connected_provider.reindex_record_group_records("rg1", -5, "u1", "org1")
        assert result["success"] is True
        assert result["depth"] == 0


class TestResetIndexingStatusToQueued:
    @pytest.mark.asyncio
    async def test_already_queued(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "r1", "indexingStatus": "QUEUED"}]
        )
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._reset_indexing_status_to_queued("r1")
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_empty(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "r1", "indexingStatus": "EMPTY"}]
        )
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._reset_indexing_status_to_queued("r1")
        connected_provider.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resets_to_queued(self, connected_provider):
        connected_provider.execute_query = AsyncMock(
            return_value=[{"_key": "r1", "indexingStatus": "INDEXED"}]
        )
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._reset_indexing_status_to_queued("r1")
        connected_provider.batch_upsert_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[])
        await connected_provider._reset_indexing_status_to_queued("r1")


class TestCheckRecordPermissions:
    @pytest.mark.asyncio
    async def test_has_permission(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"permission": "OWNER", "source": "DIRECT"}])
        result = await connected_provider._check_record_permissions("r1", "u1")
        assert result["permission"] == "OWNER"
        assert result["source"] == "DIRECT"

    @pytest.mark.asyncio
    async def test_no_permission(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=[{"permission": None, "source": "NONE"}])
        result = await connected_provider._check_record_permissions("r1", "u1")
        assert result["permission"] is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider._check_record_permissions("r1", "u1")
        assert result["permission"] is None
        assert result["source"] == "ERROR"


class TestBatchCreateEntityRelations:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}])
        result = await connected_provider.batch_create_entity_relations([
            {"from_id": "u1", "from_collection": "users", "to_id": "r1", "to_collection": "records", "edgeType": "ASSIGNED_TO"}
        ])
        assert result is True

    @pytest.mark.asyncio
    async def test_empty_edges(self, connected_provider):
        result = await connected_provider.batch_create_entity_relations([])
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider.batch_create_entity_relations([{"_from": "a/b", "_to": "c/d", "edgeType": "X"}])


class TestDeleteEdgesByRelationshipTypes:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider.delete_edges_by_relationship_types("u1", "users", "edges", ["PARENT_CHILD"])
        assert result == 2

    @pytest.mark.asyncio
    async def test_empty_types(self, connected_provider):
        result = await connected_provider.delete_edges_by_relationship_types("u1", "users", "edges", [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider.delete_edges_by_relationship_types("u1", "users", "edges", ["PARENT_CHILD"])


class TestDeleteAllEdgesForNodes:
    @pytest.mark.asyncio
    async def test_success_multiple_collections(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[1, 1])
        total, failed = await connected_provider._delete_all_edges_for_nodes(
            None, ["users/u1"], ["isOfType", "permissions"]
        )
        assert total == 4
        assert failed == []

    @pytest.mark.asyncio
    async def test_empty_node_ids(self, connected_provider):
        total, failed = await connected_provider._delete_all_edges_for_nodes(None, [], ["isOfType"])
        assert total == 0
        assert failed == []

    @pytest.mark.asyncio
    async def test_partial_failure(self, connected_provider):
        call_count = 0
        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("fail")
            return [1]
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=[Exception("fail"), [1]])
        total, failed = await connected_provider._delete_all_edges_for_nodes(
            None, ["users/u1"], ["coll1", "coll2"]
        )
        assert len(failed) == 1
        assert total == 1


class TestGetNodesByFilters:
    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"name": "test"}])
        result = await connected_provider.get_nodes_by_filters(
            "records", {"orgId": "org1"}, return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_without_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        result = await connected_provider.get_nodes_by_filters("records", {"orgId": "org1"})
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_nodes_by_filters("records", {"orgId": "org1"})
        assert result == []


class TestGetNodesByFieldIn:
    @pytest.mark.asyncio
    async def test_with_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"name": "A"}])
        result = await connected_provider.get_nodes_by_field_in(
            "records", "orgId", ["o1", "o2"], return_fields=["name"]
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_without_return_fields(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        result = await connected_provider.get_nodes_by_field_in("records", "orgId", ["o1"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_nodes_by_field_in("records", "orgId", ["o1"])
        assert result == []


class TestRemoveNodesByField:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider.remove_nodes_by_field("records", "orgId", field_value="org1")
        assert result == 2

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider.remove_nodes_by_field("records", "orgId", field_value="org1")


class TestGetEdgesToNode:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_from": "a", "_to": "b"}])
        result = await connected_provider.get_edges_to_node("records/r1", "isOfType")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_edges_to_node("records/r1", "isOfType")
        assert result == []


class TestGetEdgesFromNode:
    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_from": "a", "_to": "b"}])
        result = await connected_provider.get_edges_from_node("users/u1", "permissions")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_edges_from_node("users/u1", "permissions")
        assert result == []


class TestGetRelatedNodes:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "n1"}])
        result = await connected_provider.get_related_nodes("users/u1", "permissions", "records", "outbound")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "n1"}])
        result = await connected_provider.get_related_nodes("records/r1", "permissions", "users", "inbound")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_related_nodes("users/u1", "perm", "records")
        assert result == []


class TestGetRelatedNodeField:
    @pytest.mark.asyncio
    async def test_outbound(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["email@test.com"])
        result = await connected_provider.get_related_node_field("users/u1", "perm", "records", "email")
        assert result == ["email@test.com"]

    @pytest.mark.asyncio
    async def test_inbound(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["val"])
        result = await connected_provider.get_related_node_field("r/r1", "p", "u", "f", "inbound")
        assert result == ["val"]

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_related_node_field("r/r1", "p", "u", "f")
        assert result == []


class TestGetRecordByExternalId:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1", "externalRecordId": "ext1"}])
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = "record"
            result = await connected_provider.get_record_by_external_id("c1", "ext1")
            assert result == "record"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_external_id("c1", "ext1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_record_by_external_id("c1", "ext1")
        assert result is None


class TestGetRecordPath:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["Folder/File.txt"])
        result = await connected_provider.get_record_path("r1")
        assert result == "Folder/File.txt"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_path("r1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_record_path("r1")
        assert result is None


class TestGetRecordByExternalRevisionId:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "r1"}])
        with patch("app.services.graph_db.arango.arango_http_provider.Record") as mock_record:
            mock_record.from_arango_base_record.return_value = "rec"
            result = await connected_provider.get_record_by_external_revision_id("c1", "rev1")
            assert result == "rec"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_external_revision_id("c1", "rev1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_record_by_external_revision_id("c1", "rev1")
        assert result is None


class TestGetRecordKeyByExternalId:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=["key1"])
        result = await connected_provider.get_record_key_by_external_id("ext1", "c1")
        assert result == "key1"

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_key_by_external_id("ext1", "c1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_record_key_by_external_id("ext1", "c1")
        assert result is None


class TestGetRecordByPath:
    @pytest.mark.asyncio
    async def test_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{"id": "unique_r1","recordName":"test","externalRecordId":"ext1"}])
        result = await connected_provider.get_record_by_path("c1", ["test","file"],"record_group_id")
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.get_record_by_path("c1", ["missing"],"record_group_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.get_record_by_path("c1", ["some","file"],"record_group_id")
        assert result is None


class TestEnsureSchema:
    @pytest.mark.asyncio
    async def test_not_connected(self, provider):
        result = await provider.ensure_schema()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(return_value=True)
        connected_provider.http_client.has_graph = AsyncMock(return_value=True)
        connected_provider._ensure_departments_seed = AsyncMock()
        result = await connected_provider.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_creates_missing_collections(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(return_value=False)
        connected_provider.http_client.create_collection = AsyncMock(return_value=True)
        connected_provider.http_client.has_graph = AsyncMock(return_value=True)
        connected_provider._ensure_departments_seed = AsyncMock()
        result = await connected_provider.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_creates_graph(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(return_value=True)
        connected_provider.http_client.has_graph = AsyncMock(return_value=False)
        connected_provider.http_client.create_graph = AsyncMock(return_value=True)
        connected_provider._ensure_departments_seed = AsyncMock()
        result = await connected_provider.ensure_schema()
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.has_collection = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.ensure_schema()
        assert result is False


class TestEnsureDepartmentsSeed:
    @pytest.mark.asyncio
    async def test_inserts_missing(self, connected_provider):
        connected_provider.execute_query = AsyncMock(return_value=["Engineering"])
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._ensure_departments_seed()

    @pytest.mark.asyncio
    async def test_all_exist(self, connected_provider):
        from app.config.constants.arangodb import DepartmentNames
        connected_provider.execute_query = AsyncMock(return_value=[d.value for d in DepartmentNames])
        connected_provider.batch_upsert_nodes = AsyncMock()
        await connected_provider._ensure_departments_seed()

    @pytest.mark.asyncio
    async def test_exception_non_fatal(self, connected_provider):
        connected_provider.execute_query = AsyncMock(side_effect=Exception("err"))
        await connected_provider._ensure_departments_seed()


class TestDeleteAllEdgesForNode:
    @pytest.mark.asyncio
    async def test_with_slash_key(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[{}, {}])
        result = await connected_provider.delete_all_edges_for_node("users/u1", "permissions")
        assert result == 2

    @pytest.mark.asyncio
    async def test_without_slash_returns_zero(self, connected_provider):
        result = await connected_provider.delete_all_edges_for_node("u1", "permissions")
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_edges_found(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await connected_provider.delete_all_edges_for_node("users/u1", "permissions")
        assert result == 0

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.http_client.execute_aql = AsyncMock(side_effect=Exception("err"))
        with pytest.raises(Exception):
            await connected_provider.delete_all_edges_for_node("users/u1", "permissions")


class TestReindexSingleRecord:
    @pytest.mark.asyncio
    async def test_record_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value=None)
        result = await connected_provider.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_deleted_record(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"isDeleted": True})
        result = await connected_provider.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"connectorName": "Drive", "origin": "CONNECTOR"})
        connected_provider.get_user_by_user_id = AsyncMock(return_value=None)
        result = await connected_provider.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_unsupported_origin(self, connected_provider):
        connected_provider.get_document = AsyncMock(return_value={"origin": "UNKNOWN", "connectorName": "X"})
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        result = await connected_provider.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_depth_normalization(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=[
            {"origin": "CONNECTOR", "connectorName": "Drive", "connectorId": "c1", "isInternal": False},
            {"isActive": True},
        ])
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider._check_record_permissions = AsyncMock(return_value={"permission": "OWNER"})
        connected_provider._reset_indexing_status_to_queued = AsyncMock()
        result = await connected_provider.reindex_single_record("r1", "u1", "org1", depth=-1)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_connector_disabled(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=[
            {"origin": "CONNECTOR", "connectorName": "Drive", "connectorId": "c1"},
            {"isActive": False},
        ])
        connected_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        connected_provider._check_record_permissions = AsyncMock(return_value={"permission": "OWNER"})
        result = await connected_provider.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 400

    @pytest.mark.asyncio
    async def test_exception(self, connected_provider):
        connected_provider.get_document = AsyncMock(side_effect=Exception("err"))
        result = await connected_provider.reindex_single_record("r1", "u1", "org1")
        assert result["success"] is False
        assert result["code"] == 500
