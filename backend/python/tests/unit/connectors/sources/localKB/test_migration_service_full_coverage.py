import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    GraphNames,
    LegacyCollectionNames,
    LegacyGraphNames,
)
from app.connectors.sources.localKB.handlers.migration_service import (
    KnowledgeBaseMigrationService,
    run_kb_migration,
)


@pytest.fixture
def mock_arango_service():
    svc = AsyncMock()
    svc.db = MagicMock()
    svc.batch_upsert_nodes = AsyncMock()
    svc.batch_create_edges = AsyncMock()
    svc.get_user_by_user_id = AsyncMock(return_value=None)
    svc.get_document = AsyncMock(return_value={"_key": "doc1"})
    return svc


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def service(mock_arango_service, mock_logger):
    return KnowledgeBaseMigrationService(mock_arango_service, mock_logger)


class TestInit:
    def test_init_sets_attributes(self, mock_arango_service, mock_logger):
        svc = KnowledgeBaseMigrationService(mock_arango_service, mock_logger)
        assert svc.arango_service is mock_arango_service
        assert svc.logger is mock_logger
        assert svc.db is mock_arango_service.db
        assert svc.OLD_KB_COLLECTION == LegacyCollectionNames.KNOWLEDGE_BASE.value
        assert svc.NEW_KB_COLLECTION == CollectionNames.RECORD_GROUPS.value
        assert svc.OLD_GRAPH_NAME == LegacyGraphNames.FILE_ACCESS_GRAPH.value
        assert svc.NEW_GRAPH_NAME == GraphNames.KNOWLEDGE_GRAPH.value


class TestValidateMigrationPreconditions:
    @pytest.mark.asyncio
    async def test_old_collection_not_found(self, service):
        service.db.collections.return_value = [{"name": "records"}, {"name": "users"}]
        result = await service._validate_migration_preconditions()
        assert result["success"] is True
        assert result["migration_needed"] is False

    @pytest.mark.asyncio
    async def test_missing_new_collections_raises(self, service):
        service.db.collections.return_value = [
            {"name": LegacyCollectionNames.KNOWLEDGE_BASE.value},
        ]
        with pytest.raises(Exception, match="Required new collections missing"):
            await service._validate_migration_preconditions()

    @pytest.mark.asyncio
    async def test_all_collections_present(self, service):
        names = [
            LegacyCollectionNames.KNOWLEDGE_BASE.value,
            CollectionNames.RECORD_GROUPS.value,
            CollectionNames.PERMISSION.value,
            CollectionNames.BELONGS_TO.value,
            CollectionNames.USERS.value,
            CollectionNames.RECORDS.value,
        ]
        service.db.collections.return_value = [{"name": n} for n in names]
        result = await service._validate_migration_preconditions()
        assert result["success"] is True
        assert result["migration_needed"] is True


class TestRunMigration:
    @pytest.mark.asyncio
    async def test_preconditions_fail(self, service):
        service._validate_migration_preconditions = AsyncMock(
            return_value={"success": False, "message": "fail"}
        )
        result = await service.run_migration()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_migration_needed(self, service):
        service._validate_migration_preconditions = AsyncMock(
            return_value={"success": True, "migration_needed": False}
        )
        result = await service.run_migration()
        assert result["success"] is True
        assert result["migrated_count"] == 0

    @pytest.mark.asyncio
    async def test_migration_fails(self, service):
        service._validate_migration_preconditions = AsyncMock(
            return_value={"success": True, "migration_needed": True}
        )
        service._analyze_old_system = AsyncMock(return_value={"old_kbs": [{"_key": "1"}]})
        service._migrate_data = AsyncMock(
            return_value={"success": False, "message": "migration error"}
        )
        result = await service.run_migration()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_migration_succeeds(self, service):
        service._validate_migration_preconditions = AsyncMock(
            return_value={"success": True, "migration_needed": True}
        )
        service._analyze_old_system = AsyncMock(return_value={"old_kbs": [{"_key": "1"}]})
        service._migrate_data = AsyncMock(return_value={
            "success": True,
            "migrated_count": 1,
            "failed_count": 0,
            "details": [{"success": True, "new_kb_id": "new1"}],
        })
        service._verify_migration = AsyncMock()
        result = await service.run_migration()
        assert result["success"] is True
        assert result["migrated_count"] == 1

    @pytest.mark.asyncio
    async def test_migration_exception(self, service):
        service._validate_migration_preconditions = AsyncMock(side_effect=Exception("boom"))
        result = await service.run_migration()
        assert result["success"] is False
        assert "boom" in result["message"]


class TestAnalyzeOldSystem:
    @pytest.mark.asyncio
    async def test_analyze_groups_by_org(self, service):
        old_kbs = [
            {"_key": "kb1", "orgId": "org1", "userId": "u1"},
            {"_key": "kb2", "orgId": "org1", "userId": "u2"},
        ]
        user_perms = [
            {"edge": {}, "user": {"orgId": "org1", "userId": "u1"}, "kb": {"_key": "kb1"}},
        ]
        kb_rels = [
            {"edge": {}, "kb": {"_key": "kb1", "orgId": "org1", "userId": "u1"}, "record": {"_key": "r1"}},
        ]

        call_count = [0]
        def mock_execute(query, **kwargs):
            m = MagicMock()
            if call_count[0] == 0:
                m.__iter__ = lambda s: iter(old_kbs)
            elif call_count[0] == 1:
                m.__iter__ = lambda s: iter(user_perms)
            else:
                m.__iter__ = lambda s: iter(kb_rels)
            call_count[0] += 1
            return m

        service.db.aql.execute = mock_execute
        result = await service._analyze_old_system()
        assert result["analysis"]["total_kbs"] == 2
        assert result["analysis"]["total_orgs"] == 1
        assert len(result["org_data"]["org1"]) == 2

    @pytest.mark.asyncio
    async def test_analyze_exception_propagates(self, service):
        service.db.aql.execute = MagicMock(side_effect=Exception("db error"))
        with pytest.raises(Exception, match="db error"):
            await service._analyze_old_system()


class TestMigrateData:
    @pytest.mark.asyncio
    async def test_no_old_kbs(self, service):
        result = await service._migrate_data({"old_kbs": []})
        assert result["success"] is True
        assert result["migrated_count"] == 0

    @pytest.mark.asyncio
    async def test_all_succeed(self, service):
        txn = MagicMock()
        txn.commit_transaction = MagicMock()
        service.db.begin_transaction = MagicMock(return_value=txn)
        service._migrate_knowledge_bases = AsyncMock(
            return_value=[{"success": True, "old_kb_id": "kb1"}]
        )
        result = await service._migrate_data({
            "old_kbs": [{"_key": "kb1"}],
            "org_data": {},
        })
        assert result["success"] is True
        assert result["migrated_count"] == 1

    @pytest.mark.asyncio
    async def test_some_fail_aborts_transaction(self, service):
        txn = MagicMock()
        txn.abort_transaction = MagicMock()
        service.db.begin_transaction = MagicMock(return_value=txn)
        service._migrate_knowledge_bases = AsyncMock(return_value=[
            {"success": True, "old_kb_id": "kb1"},
            {"success": False, "old_kb_id": "kb2", "error": "bad"},
        ])
        result = await service._migrate_data({
            "old_kbs": [{"_key": "kb1"}, {"_key": "kb2"}],
            "org_data": {},
        })
        assert result["success"] is False
        assert result["failed_count"] == 1

    @pytest.mark.asyncio
    async def test_exception_aborts_transaction(self, service):
        txn = MagicMock()
        txn.abort_transaction = MagicMock()
        service.db.begin_transaction = MagicMock(return_value=txn)
        service._migrate_knowledge_bases = AsyncMock(side_effect=Exception("crash"))
        with pytest.raises(Exception, match="crash"):
            await service._migrate_data({
                "old_kbs": [{"_key": "kb1"}],
                "org_data": {},
            })

    @pytest.mark.asyncio
    async def test_exception_abort_also_fails(self, service):
        txn = MagicMock()
        txn.abort_transaction = MagicMock(side_effect=Exception("abort fail"))
        service.db.begin_transaction = MagicMock(return_value=txn)
        service._migrate_knowledge_bases = AsyncMock(side_effect=Exception("crash"))
        with pytest.raises(Exception, match="crash"):
            await service._migrate_data({
                "old_kbs": [{"_key": "kb1"}],
                "org_data": {},
            })


class TestMigrateKnowledgeBases:
    @pytest.mark.asyncio
    async def test_iterates_orgs_and_users(self, service):
        txn = MagicMock()
        migration_data = {
            "org_data": {
                "org1": {
                    "u1": {
                        "kbs": [{"_key": "kb1", "name": "KB 1"}],
                        "user_permissions": [],
                        "kb_records": [],
                    }
                }
            }
        }
        service._ensure_user_exists = AsyncMock(return_value="ukey1")
        service._migrate_single_kb = AsyncMock(return_value={
            "success": True, "old_kb_id": "kb1",
        })
        result = await service._migrate_knowledge_bases(migration_data, txn)
        assert len(result) == 1
        assert result[0]["success"] is True

    @pytest.mark.asyncio
    async def test_kb_error_captured(self, service):
        txn = MagicMock()
        migration_data = {
            "org_data": {
                "org1": {
                    "u1": {
                        "kbs": [{"_key": "kb1"}],
                        "user_permissions": [],
                        "kb_records": [],
                    }
                }
            }
        }
        service._ensure_user_exists = AsyncMock(return_value="ukey1")
        service._migrate_single_kb = AsyncMock(side_effect=Exception("kb fail"))
        result = await service._migrate_knowledge_bases(migration_data, txn)
        assert len(result) == 1
        assert result[0]["success"] is False

    @pytest.mark.asyncio
    async def test_user_error_handled(self, service):
        txn = MagicMock()
        migration_data = {
            "org_data": {
                "org1": {
                    "u1": {
                        "kbs": [{"_key": "kb1"}],
                        "user_permissions": [],
                        "kb_records": [],
                    }
                }
            }
        }
        service._ensure_user_exists = AsyncMock(side_effect=Exception("user fail"))
        result = await service._migrate_knowledge_bases(migration_data, txn)
        assert len(result) == 0


class TestMigrateSingleKb:
    @pytest.mark.asyncio
    async def test_creates_kb_and_edges(self, service):
        txn = MagicMock()
        old_kb = {"_key": "old1", "name": "Test KB", "createdAtTimestamp": 1000}
        user_data = {"kb_records": []}
        service._migrate_kb_records = AsyncMock(return_value=3)
        result = await service._migrate_single_kb(old_kb, "ukey", "org1", user_data, 2000, txn)
        assert result["success"] is True
        assert result["old_kb_id"] == "old1"
        assert result["migrated_records"] == 3
        service.arango_service.batch_upsert_nodes.assert_called_once()
        service.arango_service.batch_create_edges.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_name_when_missing(self, service):
        txn = MagicMock()
        old_kb = {"_key": "old2"}
        user_data = {"kb_records": []}
        service._migrate_kb_records = AsyncMock(return_value=0)
        result = await service._migrate_single_kb(old_kb, "ukey", "org1", user_data, 2000, txn)
        assert result["kb_name"] == "Migrated Knowledge Base"


class TestMigrateKbRecords:
    @pytest.mark.asyncio
    async def test_no_records(self, service):
        user_data = {"kb_records": []}
        count = await service._migrate_kb_records("old1", "new1", user_data, 1000, MagicMock())
        assert count == 0

    @pytest.mark.asyncio
    async def test_creates_edges(self, service):
        user_data = {
            "kb_records": [
                {"kb": {"_key": "old1"}, "record": {"_key": "r1"}},
                {"kb": {"_key": "old1"}, "record": {"_key": "r2"}},
                {"kb": {"_key": "other"}, "record": {"_key": "r3"}},
            ]
        }
        txn = MagicMock()
        count = await service._migrate_kb_records("old1", "new1", user_data, 1000, txn)
        assert count == 2
        assert service.arango_service.batch_create_edges.call_count == 2


class TestEnsureUserExists:
    @pytest.mark.asyncio
    async def test_user_found_in_new_system(self, service):
        service.arango_service.get_user_by_user_id = AsyncMock(
            return_value={"_key": "existing_key"}
        )
        result = await service._ensure_user_exists("u1", "org1", MagicMock())
        assert result == "existing_key"

    @pytest.mark.asyncio
    async def test_user_found_in_transaction_query(self, service):
        service.arango_service.get_user_by_user_id = AsyncMock(return_value=None)
        txn = MagicMock()
        cursor = MagicMock()
        cursor.__iter__ = lambda s: iter([{"_key": "txn_key"}])
        txn.aql.execute = MagicMock(return_value=cursor)
        result = await service._ensure_user_exists("u1", "org1", txn)
        assert result == "txn_key"

    @pytest.mark.asyncio
    async def test_creates_minimal_user(self, service):
        service.arango_service.get_user_by_user_id = AsyncMock(return_value=None)
        txn = MagicMock()
        cursor = MagicMock()
        cursor.__iter__ = lambda s: iter([])
        txn.aql.execute = MagicMock(return_value=cursor)
        result = await service._ensure_user_exists("u1", "org1", txn)
        assert result is not None
        service.arango_service.batch_upsert_nodes.assert_called_once()


class TestCreateNewEdgeDefinition:
    @pytest.mark.asyncio
    async def test_creates_edge_def(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=True)
        edge_def = {"edge_collection": "test_edge", "from_vertex_collections": ["a"], "to_vertex_collections": ["b"]}
        await service._create_new_edge_definition(graph, edge_def)
        graph.create_edge_definition.assert_called_once_with(**edge_def)

    @pytest.mark.asyncio
    async def test_skips_when_collection_missing(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=False)
        edge_def = {"edge_collection": "missing_edge"}
        await service._create_new_edge_definition(graph, edge_def)
        graph.create_edge_definition.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_creation_error(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=True)
        graph.create_edge_definition = MagicMock(side_effect=Exception("create fail"))
        edge_def = {"edge_collection": "bad_edge"}
        await service._create_new_edge_definition(graph, edge_def)


class TestCreateCompleteNewGraph:
    @pytest.mark.asyncio
    async def test_creates_graph_with_edges(self, service):
        graph = MagicMock()
        service.db.create_graph = MagicMock(return_value=graph)
        service.db.has_collection = MagicMock(return_value=True)
        await service._create_complete_new_graph()
        service.db.create_graph.assert_called_once_with(service.NEW_GRAPH_NAME)
        assert graph.create_edge_definition.call_count > 0

    @pytest.mark.asyncio
    async def test_skips_missing_collections(self, service):
        graph = MagicMock()
        service.db.create_graph = MagicMock(return_value=graph)
        service.db.has_collection = MagicMock(return_value=False)
        await service._create_complete_new_graph()
        graph.create_edge_definition.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_edge_creation_error(self, service):
        graph = MagicMock()
        service.db.create_graph = MagicMock(return_value=graph)
        service.db.has_collection = MagicMock(return_value=True)
        graph.create_edge_definition = MagicMock(side_effect=Exception("edge fail"))
        await service._create_complete_new_graph()

    @pytest.mark.asyncio
    async def test_raises_on_graph_creation_failure(self, service):
        service.db.create_graph = MagicMock(side_effect=Exception("graph fail"))
        with pytest.raises(Exception, match="graph fail"):
            await service._create_complete_new_graph()


class TestUpdateExistingEdgeDefinition:
    @pytest.mark.asyncio
    async def test_no_update_needed(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=True)
        graph.edge_definitions = MagicMock(return_value=[{
            "edge_collection": "ec1",
            "from_vertex_collections": ["a"],
            "to_vertex_collections": ["b"],
        }])
        new_def = {"edge_collection": "ec1", "from_vertex_collections": ["a"], "to_vertex_collections": ["b"]}
        await service._update_existing_edge_definition(graph, "ec1", new_def)
        graph.delete_edge_definition.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_when_different(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=True)
        graph.edge_definitions = MagicMock(return_value=[{
            "edge_collection": "ec1",
            "from_vertex_collections": ["a"],
            "to_vertex_collections": ["b"],
        }])
        new_def = {"edge_collection": "ec1", "from_vertex_collections": ["a", "c"], "to_vertex_collections": ["b"]}
        await service._update_existing_edge_definition(graph, "ec1", new_def)
        graph.delete_edge_definition.assert_called_once_with("ec1", purge=False)
        graph.create_edge_definition.assert_called_once_with(**new_def)

    @pytest.mark.asyncio
    async def test_skips_missing_collection(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=False)
        await service._update_existing_edge_definition(graph, "ec1", {})
        graph.edge_definitions.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_missing_definition(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=True)
        graph.edge_definitions = MagicMock(return_value=[])
        await service._update_existing_edge_definition(graph, "ec1", {})

    @pytest.mark.asyncio
    async def test_raises_on_error(self, service):
        graph = MagicMock()
        service.db.has_collection = MagicMock(return_value=True)
        graph.edge_definitions = MagicMock(side_effect=Exception("err"))
        with pytest.raises(Exception, match="err"):
            await service._update_existing_edge_definition(graph, "ec1", {})


class TestUpdateGraphVertices:
    @pytest.mark.asyncio
    async def test_creates_new_graph_when_missing(self, service):
        service.db.has_graph = MagicMock(return_value=False)
        service._create_complete_new_graph = AsyncMock()
        await service._update_graph_vertices()
        service._create_complete_new_graph.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_definitions(self, service):
        service.db.has_graph = MagicMock(return_value=True)
        graph = MagicMock()
        graph.edge_definitions = MagicMock(return_value=[
            {"edge_collection": CollectionNames.BELONGS_TO.value},
        ])
        service.db.graph = MagicMock(return_value=graph)
        service._update_existing_edge_definition = AsyncMock()
        service._create_new_edge_definition = AsyncMock()
        await service._update_graph_vertices()

    @pytest.mark.asyncio
    async def test_handles_edge_processing_error(self, service):
        service.db.has_graph = MagicMock(return_value=True)
        graph = MagicMock()
        graph.edge_definitions = MagicMock(return_value=[])
        service.db.graph = MagicMock(return_value=graph)
        service._create_new_edge_definition = AsyncMock(side_effect=Exception("edge err"))
        await service._update_graph_vertices()

    @pytest.mark.asyncio
    async def test_raises_on_major_failure(self, service):
        service.db.has_graph = MagicMock(side_effect=Exception("graph err"))
        with pytest.raises(Exception, match="graph err"):
            await service._update_graph_vertices()


class TestUpdateGraphStructure:
    @pytest.mark.asyncio
    async def test_deletes_old_creates_new(self, service):
        service.db.has_graph = MagicMock(side_effect=[True, False])
        service.db.delete_graph = MagicMock()
        service._create_complete_new_graph = AsyncMock()
        await service._update_graph_structure()
        service.db.delete_graph.assert_called_once_with(service.OLD_GRAPH_NAME)
        service._create_complete_new_graph.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_graph_already_exists(self, service):
        service.db.has_graph = MagicMock(side_effect=[False, True])
        service._update_graph_vertices = AsyncMock()
        await service._update_graph_structure()
        service._update_graph_vertices.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_failure(self, service):
        service.db.has_graph = MagicMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            await service._update_graph_structure()


class TestCleanupOldCollections:
    @pytest.mark.asyncio
    async def test_no_old_collections(self, service):
        service.db.has_collection = MagicMock(return_value=False)
        await service._cleanup_old_collections()

    @pytest.mark.asyncio
    async def test_cleanup_success(self, service):
        service.db.has_collection = MagicMock(return_value=True)
        cleanup_txn = MagicMock()
        cleanup_txn.aql.execute = MagicMock()
        cleanup_txn.commit_transaction = MagicMock()
        service.db.begin_transaction = MagicMock(return_value=cleanup_txn)
        service.db.delete_collection = MagicMock()
        await service._cleanup_old_collections()
        assert service.db.delete_collection.call_count >= 1

    @pytest.mark.asyncio
    async def test_cleanup_delete_fails_gracefully(self, service):
        call_count = [0]
        def has_col(name):
            call_count[0] += 1
            if call_count[0] > 6:
                return False
            return True
        service.db.has_collection = MagicMock(side_effect=has_col)
        cleanup_txn = MagicMock()
        cleanup_txn.aql.execute = MagicMock()
        cleanup_txn.commit_transaction = MagicMock()
        service.db.begin_transaction = MagicMock(return_value=cleanup_txn)
        service.db.delete_collection = MagicMock(side_effect=Exception("drop fail"))
        await service._cleanup_old_collections()

    @pytest.mark.asyncio
    async def test_cleanup_transaction_fails(self, service):
        service.db.has_collection = MagicMock(return_value=True)
        cleanup_txn = MagicMock()
        cleanup_txn.aql.execute = MagicMock(side_effect=Exception("txn fail"))
        cleanup_txn.abort_transaction = MagicMock()
        service.db.begin_transaction = MagicMock(return_value=cleanup_txn)
        await service._cleanup_old_collections()


class TestVerifyMigration:
    @pytest.mark.asyncio
    async def test_verify_success(self, service):
        results = [
            {"success": True, "new_kb_id": "new1"},
            {"success": True, "new_kb_id": "new2"},
        ]
        service.arango_service.get_document = AsyncMock(return_value={"_key": "x"})
        await service._verify_migration(results)

    @pytest.mark.asyncio
    async def test_verify_with_failures(self, service):
        results = [
            {"success": False, "old_kb_id": "old1", "error": "err"},
            {"success": True, "new_kb_id": "new1"},
        ]
        service.arango_service.get_document = AsyncMock(return_value={"_key": "x"})
        await service._verify_migration(results)

    @pytest.mark.asyncio
    async def test_verify_missing_kb_raises(self, service):
        results = [{"success": True, "new_kb_id": "missing1"}]
        service.arango_service.get_document = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found after migration"):
            await service._verify_migration(results)

    @pytest.mark.asyncio
    async def test_verify_get_document_exception(self, service):
        results = [{"success": True, "new_kb_id": "err1"}]
        service.arango_service.get_document = AsyncMock(side_effect=Exception("doc err"))
        with pytest.raises(Exception, match="doc err"):
            await service._verify_migration(results)


class TestRollbackMigration:
    @pytest.mark.asyncio
    async def test_no_backup_data(self, service):
        result = await service.rollback_migration()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_with_backup_data(self, service):
        result = await service.rollback_migration(backup_data={"some": "data"})
        assert result["success"] is False
        assert "not implemented" in result["message"].lower()


class TestRunKbMigration:
    @pytest.mark.asyncio
    async def test_successful_migration(self):
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        mock_arango = AsyncMock()
        mock_arango.db = MagicMock()
        container.arango_service = AsyncMock(return_value=mock_arango)

        with patch(
            "app.connectors.sources.localKB.handlers.migration_service.KnowledgeBaseMigrationService"
        ) as MockCls:
            instance = AsyncMock()
            instance.run_migration = AsyncMock(return_value={
                "success": True, "migrated_count": 2, "message": "ok"
            })
            instance._update_graph_structure = AsyncMock()
            instance._cleanup_old_collections = AsyncMock()
            MockCls.return_value = instance

            result = await run_kb_migration(container)
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_migration_no_migration_needed(self):
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        mock_arango = AsyncMock()
        mock_arango.db = MagicMock()
        container.arango_service = AsyncMock(return_value=mock_arango)

        with patch(
            "app.connectors.sources.localKB.handlers.migration_service.KnowledgeBaseMigrationService"
        ) as MockCls:
            instance = AsyncMock()
            instance.run_migration = AsyncMock(return_value={
                "success": True, "migrated_count": 0, "message": "No migration needed"
            })
            instance._update_graph_structure = AsyncMock()
            instance._cleanup_old_collections = AsyncMock()
            MockCls.return_value = instance

            result = await run_kb_migration(container)
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_migration_failure(self):
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        mock_arango = AsyncMock()
        mock_arango.db = MagicMock()
        container.arango_service = AsyncMock(return_value=mock_arango)

        with patch(
            "app.connectors.sources.localKB.handlers.migration_service.KnowledgeBaseMigrationService"
        ) as MockCls:
            instance = AsyncMock()
            instance.run_migration = AsyncMock(return_value={
                "success": False, "migrated_count": 0, "message": "failed"
            })
            instance._update_graph_structure = AsyncMock()
            instance._cleanup_old_collections = AsyncMock()
            MockCls.return_value = instance

            result = await run_kb_migration(container)
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_migration_exception(self):
        container = MagicMock()
        container.logger = MagicMock(return_value=MagicMock())
        container.arango_service = AsyncMock(side_effect=Exception("container fail"))

        result = await run_kb_migration(container)
        assert result["success"] is False
        assert "container fail" in result["message"]
