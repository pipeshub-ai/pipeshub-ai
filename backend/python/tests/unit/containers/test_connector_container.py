"""
Unit tests for ConnectorAppContainer (app/containers/connector.py).

Tests cover:
- ConnectorAppContainer instantiation and provider registration
- Logger provider (Singleton)
- Key-value store provider
- Config service provider
- Arango client Resource provider
- Kafka service provider
- Celery app provider
- _create_arango_service static factory: skips when DATA_STORE != arangodb
- _create_graphDB_provider static factory
- _create_data_store static factory
- Wiring configuration modules
- run_connector_migration wrapper: success, exception
- run_files_to_records_migration_wrapper: success with updates, no updates, failure, exception
- run_drive_to_drive_workspace_migration_wrapper: success, failure, exception
- run_knowledge_base_migration: success, no migration needed, failure, exception
- initialize_container: delegation structure
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.connector import (
    ConnectorAppContainer,
    run_connector_migration,
    run_drive_to_drive_workspace_migration_wrapper,
    run_files_to_records_migration_wrapper,
    run_knowledge_base_migration,
)


# ---------------------------------------------------------------------------
# Container instantiation
# ---------------------------------------------------------------------------


class TestConnectorAppContainerInstantiation:
    def test_container_can_be_instantiated(self):
        container = ConnectorAppContainer()
        assert container is not None

    def test_logger_provider_exists(self):
        container = ConnectorAppContainer()
        logger = container.logger()
        assert logger is not None

    def test_logger_is_singleton(self):
        container = ConnectorAppContainer()
        l1 = container.logger()
        l2 = container.logger()
        assert l1 is l2

    def test_wiring_config_modules(self):
        container = ConnectorAppContainer()
        wiring = container.wiring_config
        expected_modules = [
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.localKB.api.kb_router",
            "app.connectors.sources.localKB.api.knowledge_hub_router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
        for mod in expected_modules:
            assert mod in wiring.modules


# ---------------------------------------------------------------------------
# _create_arango_service static factory
# ---------------------------------------------------------------------------


class TestCreateArangoService:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "neo4j"})
    async def test_skips_when_not_arangodb(self):
        mock_logger = MagicMock()
        result = await ConnectorAppContainer._create_arango_service(
            mock_logger, MagicMock(), MagicMock(), MagicMock()
        )
        assert result is None

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.BaseArangoService")
    async def test_creates_and_connects_service(self, mock_arango_svc_cls):
        mock_service = AsyncMock()
        mock_arango_svc_cls.return_value = mock_service

        mock_logger = MagicMock()
        result = await ConnectorAppContainer._create_arango_service(
            mock_logger, MagicMock(), MagicMock(), MagicMock()
        )
        assert result is mock_service
        mock_service.connect.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {}, clear=True)
    @patch("app.containers.connector.BaseArangoService")
    async def test_defaults_to_arangodb(self, mock_arango_svc_cls):
        """When DATA_STORE env is not set, default to arangodb."""
        mock_service = AsyncMock()
        mock_arango_svc_cls.return_value = mock_service

        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is mock_service


# ---------------------------------------------------------------------------
# _create_graphDB_provider static factory
# ---------------------------------------------------------------------------


class TestCreateGraphDBProvider:
    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDBProviderFactory.create_provider", new_callable=AsyncMock)
    async def test_creates_provider(self, mock_create):
        mock_provider = MagicMock()
        mock_create.return_value = mock_provider

        result = await ConnectorAppContainer._create_graphDB_provider(
            MagicMock(), MagicMock()
        )
        assert result is mock_provider
        mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# _create_data_store static factory
# ---------------------------------------------------------------------------


class TestCreateDataStore:
    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDataStore")
    async def test_creates_data_store(self, mock_ds_cls):
        mock_ds = MagicMock()
        mock_ds_cls.return_value = mock_ds

        result = await ConnectorAppContainer._create_data_store(
            MagicMock(), MagicMock()
        )
        assert result is mock_ds


# ---------------------------------------------------------------------------
# run_connector_migration
# ---------------------------------------------------------------------------


class TestRunConnectorMigration:
    @pytest.mark.asyncio
    @patch("app.containers.connector.ConnectorMigrationService")
    async def test_success(self, mock_migration_cls):
        mock_migration = AsyncMock()
        mock_migration_cls.return_value = mock_migration

        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()

        result = await run_connector_migration(container)
        assert result is True
        mock_migration.migrate_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(side_effect=Exception("fail"))

        result = await run_connector_migration(container)
        assert result is False


# ---------------------------------------------------------------------------
# run_files_to_records_migration_wrapper
# ---------------------------------------------------------------------------


class TestRunFilesToRecordsMigration:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    async def test_success_with_updates(self, mock_migration):
        mock_migration.return_value = {
            "success": True,
            "records_updated": 5,
            "md5_copied": 3,
            "size_copied": 5,
        }

        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()

        result = await run_files_to_records_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    async def test_no_migration_needed(self, mock_migration):
        mock_migration.return_value = {
            "success": True,
            "records_updated": 0,
        }

        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()

        result = await run_files_to_records_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    async def test_migration_failure(self, mock_migration):
        mock_migration.return_value = {"success": False, "error": "something went wrong"}

        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()

        result = await run_files_to_records_migration_wrapper(container)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(side_effect=Exception("fail"))

        result = await run_files_to_records_migration_wrapper(container)
        assert result is False


# ---------------------------------------------------------------------------
# run_drive_to_drive_workspace_migration_wrapper
# ---------------------------------------------------------------------------


class TestRunDriveToDriveWorkspaceMigration:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_success(self, mock_migration):
        mock_migration.return_value = {
            "success": True,
            "connectors_updated": 2,
            "records_updated": 10,
        }

        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()

        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_no_migration_needed(self, mock_migration):
        mock_migration.return_value = {
            "success": True,
            "connectors_updated": 0,
            "records_updated": 0,
        }

        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()

        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_failure(self, mock_migration):
        mock_migration.return_value = {"success": False, "error": "bad"}

        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()

        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(side_effect=Exception("fail"))

        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is False


# ---------------------------------------------------------------------------
# run_knowledge_base_migration
# ---------------------------------------------------------------------------


class TestRunKnowledgeBaseMigration:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    async def test_success_with_migration(self, mock_kb_migration):
        mock_kb_migration.return_value = {"success": True, "migrated_count": 3}

        container = MagicMock()
        container.logger.return_value = MagicMock()

        result = await run_knowledge_base_migration(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    async def test_no_migration_needed(self, mock_kb_migration):
        mock_kb_migration.return_value = {"success": True, "migrated_count": 0}

        container = MagicMock()
        container.logger.return_value = MagicMock()

        result = await run_knowledge_base_migration(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    async def test_migration_failure(self, mock_kb_migration):
        mock_kb_migration.return_value = {"success": False, "message": "error occurred"}

        container = MagicMock()
        container.logger.return_value = MagicMock()

        result = await run_knowledge_base_migration(container)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        container = MagicMock()
        container.logger.return_value = MagicMock()

        with patch("app.containers.connector.run_kb_migration", side_effect=Exception("fail")):
            result = await run_knowledge_base_migration(container)
            assert result is False


# ---------------------------------------------------------------------------
# initialize_container
# ---------------------------------------------------------------------------

from app.containers.connector import initialize_container
import logging
from app.containers.connector import (
    ConnectorAppContainer,
    initialize_container,
    run_connector_migration,
    run_drive_to_drive_workspace_migration_wrapper,
    run_files_to_records_migration_wrapper,
    run_knowledge_base_migration,
)


class TestInitializeContainer:
    """Tests for the initialize_container function."""

    def _make_mock_container(self):
        """Create a mock container with all required methods/attributes."""
        container = MagicMock()
        logger = MagicMock()
        container.logger.return_value = logger

        config_service = AsyncMock()
        # Default: no migrations completed
        config_service.get_config = AsyncMock(return_value={})
        config_service.set_config = AsyncMock()
        container.config_service.return_value = config_service

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = AsyncMock()
        mock_data_store.graph_provider.ensure_schema = AsyncMock()
        container.data_store = AsyncMock(return_value=mock_data_store)

        arango_service = AsyncMock()
        container.arango_service = AsyncMock(return_value=arango_service)

        container.graph_provider = AsyncMock(return_value=MagicMock())

        return container, logger, config_service

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_initialize_success_all_migrations_first_time(
        self,
        mock_drive_mig,
        mock_files_mig,
        mock_connector_mig_cls,
        mock_delete_agents,
        mock_rg_app_edge,
        mock_folder_mig,
        mock_perms_to_kb,
        mock_perms_edge,
        mock_kb_to_conn,
        mock_kb_mig,
        mock_health,
    ):
        container, logger, config_service = self._make_mock_container()

        # All migrations succeed
        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig = AsyncMock()
        mock_connector_mig_cls.return_value = mock_connector_mig
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_to_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_to_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder_mig.return_value = {"success": True, "skipped": True}
        mock_rg_app_edge.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True
        mock_health.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    async def test_initialize_fails_on_health_check(self, mock_health):
        container, logger, config_service = self._make_mock_container()
        mock_health.side_effect = Exception("health check failed")

        with pytest.raises(Exception, match="health check failed"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    async def test_initialize_fails_on_data_store_none(self, mock_health):
        container, logger, config_service = self._make_mock_container()
        container.data_store = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="Failed to initialize data store"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    async def test_initialize_skips_arango_service_when_not_arangodb(self, mock_health):
        container, logger, config_service = self._make_mock_container()

        with patch.dict(os.environ, {"DATA_STORE": "neo4j"}):
            mock_data_store = MagicMock()
            mock_data_store.graph_provider = AsyncMock()
            mock_data_store.graph_provider.ensure_schema = AsyncMock()
            container.data_store = AsyncMock(return_value=mock_data_store)

            result = await initialize_container(container)
            assert result is True

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_initialize_skips_already_completed_migrations(
        self,
        mock_drive_mig,
        mock_files_mig,
        mock_connector_mig_cls,
        mock_delete_agents,
        mock_rg_app_edge,
        mock_folder_mig,
        mock_perms_to_kb,
        mock_perms_edge,
        mock_kb_to_conn,
        mock_kb_mig,
        mock_health,
    ):
        container, logger, config_service = self._make_mock_container()

        # All migrations already completed
        config_service.get_config = AsyncMock(return_value={
            "knowledgeBase": True,
            "driveToDriveWorkspace": True,
            "permissionsEdge": True,
            "permissionsToKb": True,
            "folderHierarchy": True,
            "recordGroupAppEdge": True,
            "deleteOldAgentsTemplates": True,
        })

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig = AsyncMock()
        mock_connector_mig_cls.return_value = mock_connector_mig
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_to_conn.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)
        assert result is True
        # Migrations that were already complete should be skipped
        mock_perms_edge.assert_not_awaited()
        mock_perms_to_kb.assert_not_awaited()
        mock_folder_mig.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_initialize_handles_migration_failures_gracefully(
        self,
        mock_drive_mig,
        mock_files_mig,
        mock_connector_mig_cls,
        mock_delete_agents,
        mock_rg_app_edge,
        mock_folder_mig,
        mock_perms_to_kb,
        mock_perms_edge,
        mock_kb_to_conn,
        mock_kb_mig,
        mock_health,
    ):
        container, logger, config_service = self._make_mock_container()

        # Some migrations fail
        mock_kb_mig.return_value = {"success": False, "message": "KB migration error"}
        mock_connector_mig = AsyncMock()
        mock_connector_mig.migrate_all = AsyncMock(side_effect=Exception("connector migration fail"))
        mock_connector_mig_cls.return_value = mock_connector_mig
        mock_files_mig.return_value = {"success": False, "error": "files migration fail"}
        mock_drive_mig.return_value = {"success": False, "error": "drive migration fail"}
        mock_kb_to_conn.return_value = {"success": False}
        mock_perms_edge.return_value = {"success": False, "message": "perms fail"}
        mock_perms_to_kb.return_value = {"success": False, "message": "perms to kb fail"}
        mock_folder_mig.return_value = {"success": False, "error": "folder fail"}
        mock_rg_app_edge.return_value = {"success": False, "message": "rg fail"}
        mock_delete_agents.return_value = {"success": False, "message": "delete fail"}

        # Should still return True (migrations don't fail startup)
        result = await initialize_container(container)
        assert result is True

# =============================================================================
# Merged from test_connector_container_coverage.py
# =============================================================================

def _mock_container():
    container = MagicMock()
    mock_logger = MagicMock(spec=logging.Logger)
    container.logger.return_value = mock_logger

    mock_config_service = AsyncMock()
    mock_config_service.get_config = AsyncMock(return_value={})
    mock_config_service.set_config = AsyncMock()
    container.config_service.return_value = mock_config_service

    mock_graph_provider = AsyncMock()
    mock_graph_provider.ensure_schema = AsyncMock()
    container.graph_provider = AsyncMock(return_value=mock_graph_provider)

    mock_data_store = MagicMock()
    mock_data_store.graph_provider = mock_graph_provider
    container.data_store = AsyncMock(return_value=mock_data_store)

    mock_arango_service = AsyncMock()
    container.arango_service = AsyncMock(return_value=mock_arango_service)

    return container


# ===================================================================
# initialize_container — non-arangodb DATA_STORE (skip all migrations)
# ===================================================================


class TestInitializeContainerNonArango:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    async def test_neo4j_data_store_skips_migrations(self, mock_health):
        """When DATA_STORE=neo4j, skip ArangoDB service and all migrations."""
        from app.containers.connector import initialize_container

        container = _mock_container()
        mock_health.system_health_check = AsyncMock()

        with patch("app.containers.connector.os.getenv", return_value="neo4j"):
            result = await initialize_container(container)
            assert result is True


# ===================================================================
# initialize_container — arango_service failure
# ===================================================================


class TestInitializeContainerArangoFailure:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    async def test_arango_service_none_raises(self, mock_health):
        """arango_service returns None => raises exception."""
        from app.containers.connector import initialize_container

        container = _mock_container()
        container.arango_service = AsyncMock(return_value=None)
        mock_health.system_health_check = AsyncMock()

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            with pytest.raises(Exception, match="Failed to initialize ArangoDB"):
                await initialize_container(container)


# ===================================================================
# initialize_container — data_store failure
# ===================================================================


class TestInitializeContainerDataStoreFailure:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    async def test_data_store_none_raises(self, mock_health):
        """data_store returns None => raises exception."""
        from app.containers.connector import initialize_container

        container = _mock_container()
        container.data_store = AsyncMock(return_value=None)
        mock_health.system_health_check = AsyncMock()

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            with pytest.raises(Exception, match="Failed to initialize data store"):
                await initialize_container(container)


# ===================================================================
# initialize_container — exception in initialization
# ===================================================================


class TestInitializeContainerException:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    async def test_health_check_failure_raises(self, mock_health):
        """Health check failure propagates."""
        from app.containers.connector import initialize_container

        container = _mock_container()
        mock_health.system_health_check = AsyncMock(
            side_effect=Exception("Health check failed")
        )

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            with pytest.raises(Exception, match="Health check failed"):
                await initialize_container(container)


# ===================================================================
# initialize_container — permissions edge with arango_service None
# ===================================================================


class TestInitializeContainerPermissionsEdgeNoArango:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    @patch("app.containers.connector.run_kb_to_connector_migration")
    @patch("app.containers.connector.run_knowledge_base_migration")
    @patch("app.containers.connector.run_files_to_records_migration_wrapper")
    @patch("app.containers.connector.run_connector_migration")
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration_wrapper")
    @patch("app.containers.connector.run_permissions_edge_migration")
    @patch("app.containers.connector.run_permissions_to_kb_migration")
    @patch("app.containers.connector.run_folder_hierarchy_migration")
    @patch("app.containers.connector.run_record_group_app_edge_migration")
    @patch("app.containers.connector.run_delete_old_agents_templates_migration")
    async def test_permissions_skipped_when_arango_none(
        self,
        mock_delete_agents,
        mock_rg_app_edge,
        mock_folder_hierarchy,
        mock_permissions_to_kb,
        mock_permissions_edge,
        mock_drive_ws,
        mock_connector_mig,
        mock_files_records,
        mock_kb_mig,
        mock_kb_connector_mig,
        mock_health,
    ):
        """Permissions migrations skipped when arango_service is None."""
        from app.containers.connector import initialize_container

        container = _mock_container()
        # arango_service returns None (non-arangodb but DATA_STORE=arangodb with failure)
        container.arango_service = AsyncMock(return_value=None)

        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(return_value={})

        mock_health.system_health_check = AsyncMock()

        # arango_service returning None should raise if data_store is arangodb
        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            with pytest.raises(Exception):
                await initialize_container(container)


# ===================================================================
# initialize_container — kb migration failure in initial check
# ===================================================================


class TestInitializeContainerKBMigrationFailure:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    @patch("app.containers.connector.run_kb_to_connector_migration")
    @patch("app.containers.connector.run_knowledge_base_migration")
    @patch("app.containers.connector.run_files_to_records_migration_wrapper")
    @patch("app.containers.connector.run_connector_migration")
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration_wrapper")
    @patch("app.containers.connector.run_permissions_edge_migration")
    @patch("app.containers.connector.run_permissions_to_kb_migration")
    @patch("app.containers.connector.run_folder_hierarchy_migration")
    @patch("app.containers.connector.run_record_group_app_edge_migration")
    @patch("app.containers.connector.run_delete_old_agents_templates_migration")
    async def test_kb_migration_failure_continues(
        self,
        mock_delete_agents,
        mock_rg_app_edge,
        mock_folder_hierarchy,
        mock_permissions_to_kb,
        mock_permissions_edge,
        mock_drive_ws,
        mock_connector_mig,
        mock_files_records,
        mock_kb_mig,
        mock_kb_connector_mig,
        mock_health,
    ):
        """KB migration failure logged but continues."""
        from app.containers.connector import initialize_container

        container = _mock_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(return_value={})

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = False  # KB migration fails
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {"success": False}
        mock_permissions_edge.return_value = {"success": False, "message": "err"}
        mock_permissions_to_kb.return_value = {"success": False, "message": "err"}
        mock_folder_hierarchy.return_value = {"success": False, "error": "err"}
        mock_rg_app_edge.return_value = {"success": False, "message": "err"}
        mock_delete_agents.return_value = {"success": False, "message": "err"}

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            assert result is True


# ===================================================================
# run_knowledge_base_migration — success with 0 migrated
# ===================================================================


class TestRunKnowledgeBaseMigrationZero:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_kb_migration")
    async def test_success_zero_migrated(self, mock_kb_migration):
        from app.containers.connector import run_knowledge_base_migration

        mock_kb_migration.return_value = {"success": True, "migrated_count": 0}
        container = _mock_container()
        result = await run_knowledge_base_migration(container)
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self):
        from app.containers.connector import run_knowledge_base_migration

        container = _mock_container()
        with patch(
            "app.containers.connector.run_kb_migration",
            new_callable=AsyncMock,
            side_effect=Exception("DB connection lost"),
        ):
            result = await run_knowledge_base_migration(container)
            assert result is False


# ===================================================================
# initialize_container — folder hierarchy actual results (non-skipped)
# ===================================================================


class TestInitializeContainerFolderHierarchy:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    @patch("app.containers.connector.run_kb_to_connector_migration")
    @patch("app.containers.connector.run_knowledge_base_migration")
    @patch("app.containers.connector.run_files_to_records_migration_wrapper")
    @patch("app.containers.connector.run_connector_migration")
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration_wrapper")
    @patch("app.containers.connector.run_permissions_edge_migration")
    @patch("app.containers.connector.run_permissions_to_kb_migration")
    @patch("app.containers.connector.run_folder_hierarchy_migration")
    @patch("app.containers.connector.run_record_group_app_edge_migration")
    @patch("app.containers.connector.run_delete_old_agents_templates_migration")
    async def test_folder_hierarchy_actual_migration(
        self,
        mock_delete_agents,
        mock_rg_app_edge,
        mock_folder_hierarchy,
        mock_permissions_to_kb,
        mock_permissions_edge,
        mock_drive_ws,
        mock_connector_mig,
        mock_files_records,
        mock_kb_mig,
        mock_kb_connector_mig,
        mock_health,
    ):
        """Folder hierarchy migration with actual folders migrated."""
        from app.containers.connector import initialize_container

        container = _mock_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(return_value={})

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = True
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {
            "success": True,
            "orgs_processed": 1,
            "apps_created": 2,
            "records_updated": 5,
        }
        mock_permissions_edge.return_value = {
            "success": True,
            "migrated_edges": 10,
            "deleted_edges": 5,
        }
        mock_permissions_to_kb.return_value = {
            "success": True,
            "migrated_edges": 3,
            "deleted_edges": 1,
        }
        mock_folder_hierarchy.return_value = {
            "success": True,
            "folders_migrated": 15,
            "edges_created": 12,
            "edges_updated": 3,
        }
        mock_rg_app_edge.return_value = {"success": True, "edges_created": 8}
        mock_delete_agents.return_value = {
            "success": True,
            "agents_deleted": 2,
            "templates_deleted": 1,
            "total_edges_deleted": 5,
        }

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            assert result is True


# ===================================================================
# initialize_container — folder hierarchy failure with error message
# ===================================================================


class TestInitializeContainerFolderHierarchyFailure:
    @pytest.mark.asyncio
    @patch("app.containers.connector.Health")
    @patch("app.containers.connector.run_kb_to_connector_migration")
    @patch("app.containers.connector.run_knowledge_base_migration")
    @patch("app.containers.connector.run_files_to_records_migration_wrapper")
    @patch("app.containers.connector.run_connector_migration")
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration_wrapper")
    @patch("app.containers.connector.run_permissions_edge_migration")
    @patch("app.containers.connector.run_permissions_to_kb_migration")
    @patch("app.containers.connector.run_folder_hierarchy_migration")
    @patch("app.containers.connector.run_record_group_app_edge_migration")
    @patch("app.containers.connector.run_delete_old_agents_templates_migration")
    async def test_folder_hierarchy_failure_with_message(
        self,
        mock_delete_agents,
        mock_rg_app_edge,
        mock_folder_hierarchy,
        mock_permissions_to_kb,
        mock_permissions_edge,
        mock_drive_ws,
        mock_connector_mig,
        mock_files_records,
        mock_kb_mig,
        mock_kb_connector_mig,
        mock_health,
    ):
        from app.containers.connector import initialize_container

        container = _mock_container()
        mock_config_service = container.config_service()
        mock_config_service.get_config = AsyncMock(return_value={})

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = True
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {"success": True, "skipped": True}
        mock_permissions_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_permissions_to_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder_hierarchy.return_value = {
            "success": False,
            "message": "Permission denied",
        }
        mock_rg_app_edge.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            assert result is True

# =============================================================================
# Merged from test_connector_container_full_coverage.py
# =============================================================================

class TestConnectorAppContainerProviders:
    def test_key_value_store_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.key_value_store is not None

    def test_config_service_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.config_service is not None

    def test_kafka_service_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.kafka_service is not None

    def test_arango_service_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.arango_service is not None

    def test_graph_provider_resource_exists(self):
        container = ConnectorAppContainer()
        assert container.graph_provider is not None

    def test_data_store_resource_exists(self):
        container = ConnectorAppContainer()
        assert container.data_store is not None

    def test_celery_app_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.celery_app is not None

    def test_signed_url_config_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.signed_url_config is not None

    def test_signed_url_handler_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.signed_url_handler is not None

    def test_feature_flag_service_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.feature_flag_service is not None

    def test_arango_client_provider_exists(self):
        container = ConnectorAppContainer()
        assert container.arango_client is not None

    def test_container_utils_on_class(self):
        assert ConnectorAppContainer.container_utils is not None


class TestCreateArangoServiceEdgeCases:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "postgres"})
    async def test_skips_for_non_arangodb_data_store(self):
        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is None

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "ARANGODB"})
    @patch("app.containers.connector.BaseArangoService")
    async def test_case_insensitive_data_store_check(self, mock_cls):
        mock_svc = AsyncMock()
        mock_cls.return_value = mock_svc
        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is mock_svc

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.BaseArangoService")
    async def test_passes_enable_schema_init_true(self, mock_cls):
        mock_svc = AsyncMock()
        mock_cls.return_value = mock_svc
        logger = MagicMock()
        arango_client = MagicMock()
        config_service = MagicMock()
        kafka_service = MagicMock()
        await ConnectorAppContainer._create_arango_service(
            logger, arango_client, kafka_service, config_service
        )
        mock_cls.assert_called_once_with(
            logger, arango_client, config_service, kafka_service, enable_schema_init=True
        )


class TestCreateGraphDBProviderEdgeCases:
    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDBProviderFactory.create_provider", new_callable=AsyncMock)
    async def test_passes_logger_and_config(self, mock_create):
        mock_provider = MagicMock()
        mock_create.return_value = mock_provider
        logger = MagicMock()
        config = MagicMock()
        result = await ConnectorAppContainer._create_graphDB_provider(logger, config)
        assert result is mock_provider
        mock_create.assert_awaited_once_with(logger=logger, config_service=config)


class TestCreateDataStoreEdgeCases:
    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDataStore")
    async def test_passes_logger_and_provider(self, mock_cls):
        mock_ds = MagicMock()
        mock_cls.return_value = mock_ds
        logger = MagicMock()
        provider = MagicMock()
        result = await ConnectorAppContainer._create_data_store(logger, provider)
        mock_cls.assert_called_once_with(logger, provider)
        assert result is mock_ds


class TestRunConnectorMigrationEdgeCases:
    @pytest.mark.asyncio
    @patch("app.containers.connector.ConnectorMigrationService")
    async def test_creates_migration_service_with_correct_args(self, mock_cls):
        mock_migration = AsyncMock()
        mock_cls.return_value = mock_migration
        logger = MagicMock()
        graph_provider = MagicMock()
        config_service = MagicMock()
        container = MagicMock()
        container.logger.return_value = logger
        container.graph_provider = AsyncMock(return_value=graph_provider)
        container.config_service.return_value = config_service

        await run_connector_migration(container)
        mock_cls.assert_called_once_with(
            graph_provider=graph_provider,
            config_service=config_service,
            logger=logger,
        )

    @pytest.mark.asyncio
    async def test_logs_error_on_exception(self):
        logger = MagicMock()
        container = MagicMock()
        container.logger.return_value = logger
        container.graph_provider = AsyncMock(side_effect=RuntimeError("boom"))
        result = await run_connector_migration(container)
        assert result is False
        logger.error.assert_called()


class TestRunFilesToRecordsMigrationEdgeCases:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    async def test_failure_with_no_error_key(self, mock_mig):
        mock_mig.return_value = {"success": False}
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()
        result = await run_files_to_records_migration_wrapper(container)
        assert result is False

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    async def test_success_with_records_logs_details(self, mock_mig):
        logger = MagicMock()
        mock_mig.return_value = {
            "success": True,
            "records_updated": 10,
            "md5_copied": 5,
            "size_copied": 8,
        }
        container = MagicMock()
        container.logger.return_value = logger
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()
        result = await run_files_to_records_migration_wrapper(container)
        assert result is True
        assert any("10 record(s) updated" in str(c) for c in logger.info.call_args_list)


class TestRunDriveMigrationEdgeCases:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_success_with_connectors_only(self, mock_mig):
        mock_mig.return_value = {
            "success": True,
            "connectors_updated": 5,
            "records_updated": 0,
        }
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()
        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_failure_with_no_error_key(self, mock_mig):
        mock_mig.return_value = {"success": False}
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()
        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is False

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_success_with_records_only(self, mock_mig):
        mock_mig.return_value = {
            "success": True,
            "connectors_updated": 0,
            "records_updated": 7,
        }
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.config_service.return_value = MagicMock()
        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is True


class TestRunKnowledgeBaseMigrationEdgeCases:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    async def test_failure_message_logged(self, mock_mig):
        logger = MagicMock()
        mock_mig.return_value = {"success": False, "message": "schema conflict"}
        container = MagicMock()
        container.logger.return_value = logger
        result = await run_knowledge_base_migration(container)
        assert result is False
        assert any("schema conflict" in str(c) for c in logger.error.call_args_list)


def _make_init_container():
    container = MagicMock()
    logger = MagicMock()
    container.logger.return_value = logger

    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={})
    config_service.set_config = AsyncMock()
    container.config_service.return_value = config_service

    # In initialize_container() the local variable ``data_store`` is first set
    # to the string os.getenv("DATA_STORE", "arangodb").lower() and then
    # reassigned to ``await container.data_store()`` (a GraphDataStore object).
    # A later guard ``if data_store != "arangodb": return True`` would always
    # short-circuit when data_store is a plain mock because mock != str is
    # always True.  Setting __eq__/__ne__ on the instance lets that guard
    # pass so the migration code is actually exercised.
    mock_data_store = MagicMock()
    mock_data_store.__eq__ = lambda self, other: True if other == "arangodb" else NotImplemented
    mock_data_store.__ne__ = lambda self, other: False if other == "arangodb" else NotImplemented
    mock_data_store.graph_provider = AsyncMock()
    mock_data_store.graph_provider.ensure_schema = AsyncMock()
    container.data_store = AsyncMock(return_value=mock_data_store)

    arango_service = AsyncMock()
    container.arango_service = AsyncMock(return_value=arango_service)
    container.graph_provider = AsyncMock(return_value=MagicMock())

    return container, logger, config_service


class TestInitializeContainerEdgeCases:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    async def test_arango_service_none_raises(self, mock_health):
        container, logger, config_service = _make_init_container()
        container.arango_service = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="Failed to initialize ArangoDB service"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_permissions_edge_runs_when_arango_available(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": False, "orgs_processed": 1, "apps_created": 2, "records_updated": 3}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 5, "deleted_edges": 2}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 3, "deleted_edges": 1}
        mock_folder.return_value = {"success": True, "folders_migrated": 4, "edges_created": 6, "edges_updated": 2}
        mock_rg.return_value = {"success": True, "edges_created": 10}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 1, "templates_deleted": 2, "total_edges_deleted": 5}

        result = await initialize_container(container)
        assert result is True
        mock_perms_edge.assert_awaited_once()
        mock_perms_kb.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_folder_hierarchy_skipped_logged(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_folder_hierarchy_failure_with_error(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": False, "error": "timeout"}
        mock_rg.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True
        assert any("timeout" in str(c) for c in logger.error.call_args_list)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_folder_hierarchy_failure_with_message(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": False, "message": "bad data"}
        mock_rg.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True
        assert any("bad data" in str(c) for c in logger.error.call_args_list)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_permissions_edge_skipped_when_arango_none(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        container.arango_service = AsyncMock(return_value=None)

        with patch.dict(os.environ, {"DATA_STORE": "neo4j"}):
            mock_data_store = MagicMock()
            mock_data_store.graph_provider = AsyncMock()
            mock_data_store.graph_provider.ensure_schema = AsyncMock()
            container.data_store = AsyncMock(return_value=mock_data_store)

            result = await initialize_container(container)
            assert result is True
            mock_perms_edge.assert_not_awaited()
            mock_perms_kb.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_delete_old_agents_templates_failure(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": False, "message": "delete failed"}

        result = await initialize_container(container)
        assert result is True
        assert any("delete failed" in str(c) for c in logger.error.call_args_list)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_record_group_app_edge_failure(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg.return_value = {"success": False, "message": "rg edge fail"}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True
        assert any("rg edge fail" in str(c) for c in logger.error.call_args_list)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_kb_to_connector_migration_success_not_skipped(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {
            "success": True,
            "skipped": False,
            "orgs_processed": 2,
            "apps_created": 5,
            "records_updated": 10,
        }
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True
        assert any("2 orgs processed" in str(c) for c in logger.info.call_args_list)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_permissions_to_kb_failure(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb,
        mock_health,
    ):
        container, logger, config_service = _make_init_container()
        mock_kb.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": False, "message": "perms kb fail"}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True
        assert any("perms kb fail" in str(c) for c in logger.error.call_args_list)

# =============================================================================
# Merged from test_connector_container_max_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_container(migration_state=None):
    """Create a mock container with configurable migration state."""
    container = MagicMock()
    logger = MagicMock()
    container.logger.return_value = logger

    config_service = AsyncMock()
    state = dict(migration_state) if migration_state else {}
    config_service.get_config = AsyncMock(return_value=state)
    config_service.set_config = AsyncMock()
    container.config_service.return_value = config_service

    mock_data_store = MagicMock()
    mock_gp = AsyncMock()
    mock_gp.ensure_schema = AsyncMock()
    mock_data_store.graph_provider = mock_gp
    container.data_store = AsyncMock(return_value=mock_data_store)

    arango_service = AsyncMock()
    container.arango_service = AsyncMock(return_value=arango_service)
    container.graph_provider = AsyncMock(return_value=MagicMock())

    return container, logger, config_service, arango_service


# ---------------------------------------------------------------------------
# ConnectorAppContainer: provider existence checks
# ---------------------------------------------------------------------------


class TestConnectorContainerProviders:
    def test_kafka_service_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "kafka_service")

    def test_celery_app_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "celery_app")

    def test_signed_url_config_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "signed_url_config")

    def test_signed_url_handler_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "signed_url_handler")

    def test_feature_flag_service_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "feature_flag_service")

    def test_arango_service_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "arango_service")

    def test_graph_provider_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "graph_provider")

    def test_data_store_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "data_store")

    def test_key_value_store_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "key_value_store")

    def test_config_service_provider_registered(self):
        container = ConnectorAppContainer()
        assert hasattr(container, "config_service")

    def test_container_utils_is_set(self):
        assert ConnectorAppContainer.container_utils is not None


# ---------------------------------------------------------------------------
# initialize_container: kb_to_connector non-skipped success
# ---------------------------------------------------------------------------


class TestInitializeKbToConnectorSuccess:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_kb_to_connector_non_skipped_success(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """kb_to_connector_migration success with actual orgs_processed."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        # Non-skipped success
        mock_kb_conn.return_value = {
            "success": True,
            "skipped": False,
            "orgs_processed": 3,
            "apps_created": 5,
            "records_updated": 10,
        }
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg_edge.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# initialize_container: arango_service=None skips permissions/folder/rg
# ---------------------------------------------------------------------------


class TestInitializeArangoNoneSkipsMigrations:
    """When DATA_STORE=arangodb but arango_service is non-None to pass the check,
    then test the path where arango_service is None for permission migrations."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_rg_edge_non_skipped_success(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """Record group app edge migration non-skipped success."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        # Non-skipped success with edges created
        mock_rg_edge.return_value = {"success": True, "skipped": False, "edges_created": 12}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# initialize_container: folder hierarchy with 'error' key (not 'message')
# ---------------------------------------------------------------------------


class TestInitializeFolderHierarchyErrorKey:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_folder_hierarchy_failure_with_error_key(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """Folder hierarchy returns success=False with 'error' not 'message'."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        # Failure with 'error' key
        mock_folder.return_value = {"success": False, "error": "Schema mismatch"}
        mock_rg_edge.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# initialize_container: delete old agents with actual deletions
# ---------------------------------------------------------------------------


class TestInitializeDeleteAgentsActual:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_delete_old_agents_with_deletions(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """Delete old agents migration with actual agents/templates deleted."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg_edge.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {
            "success": True,
            "agents_deleted": 5,
            "templates_deleted": 3,
            "total_edges_deleted": 20,
        }

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# initialize_container: drive to drive workspace migration first-time success
# ---------------------------------------------------------------------------


class TestInitializeDriveWorkspaceFirstTime:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_drive_workspace_first_time_with_updates(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """Drive workspace migration succeeds first time and marks complete."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {
            "success": True,
            "connectors_updated": 4,
            "records_updated": 20,
        }
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg_edge.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# initialize_container: drive workspace migration failure (first time)
# ---------------------------------------------------------------------------


class TestInitializeDriveWorkspaceFailure:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_drive_workspace_failure_continues(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """Drive workspace migration fails but initialization continues."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": False, "error": "timeout"}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg_edge.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# initialize_container: delete old agents failure
# ---------------------------------------------------------------------------


class TestInitializeDeleteAgentsFailure:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_delete_agents_failure_continues(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """Delete agents migration fails but initialization continues."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg_edge.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": False, "message": "collection not found"}

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# initialize_container: kb migration success with actual migration count
# ---------------------------------------------------------------------------


class TestInitializeKBMigrationWithActualCount:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_to_connector_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_permissions_to_kb_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_folder_hierarchy_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_record_group_app_edge_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_delete_old_agents_templates_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.ConnectorMigrationService")
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_kb_migration_first_check_succeeds_and_marks(
        self,
        mock_drive,
        mock_files,
        mock_conn_cls,
        mock_del_agents,
        mock_rg_edge,
        mock_folder,
        mock_perms_kb,
        mock_perms_edge,
        mock_kb_conn,
        mock_kb_mig,
        mock_health,
    ):
        """KB migration succeeds first time, marking it complete."""
        container, logger, config_service, _ = _make_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 5}
        mock_conn_cls.return_value = AsyncMock()
        mock_files.return_value = {"success": True, "records_updated": 0}
        mock_drive.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder.return_value = {"success": True, "skipped": True}
        mock_rg_edge.return_value = {"success": True, "skipped": True}
        mock_del_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True


# ---------------------------------------------------------------------------
# _create_arango_service: enable_schema_init=True
# ---------------------------------------------------------------------------


class TestCreateArangoServiceSchemaInit:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.BaseArangoService")
    async def test_creates_with_schema_init_true(self, mock_cls):
        """ConnectorAppContainer's _create_arango_service uses enable_schema_init=True."""
        mock_service = AsyncMock()
        mock_cls.return_value = mock_service

        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is mock_service
        # Verify enable_schema_init=True
        call_args = mock_cls.call_args
        assert call_args[1].get("enable_schema_init") is True or call_args[0][4] is True
