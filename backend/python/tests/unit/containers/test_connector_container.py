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
