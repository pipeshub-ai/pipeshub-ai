"""
Additional coverage tests for app/containers/connector.py.

Targets remaining uncovered blocks after test_connector_container_extended.py:
- initialize_container: non-arangodb DATA_STORE path (skips migrations)
- initialize_container: arango_service is None paths
- initialize_container: folder hierarchy with actual migration results
- initialize_container: kb migration failure in first check
- initialize_container: data_store check failure
- initialize_container: exception in initialization
- run_knowledge_base_migration: success with 0 migrated
- run_knowledge_base_migration: exception
- _create_arango_service: non-arangodb data store
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
