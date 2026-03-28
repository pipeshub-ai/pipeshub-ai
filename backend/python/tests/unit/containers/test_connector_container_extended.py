"""
Extended tests for app/containers/connector.py to reach 85%+ coverage.
Covers migration wrappers and initialize_container edge cases.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_container():
    """Create a mock container with all needed attributes."""
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
# run_files_to_records_migration_wrapper
# ===================================================================


class TestRunFilesToRecordsMigrationWrapper:
    """Tests for run_files_to_records_migration_wrapper."""

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration")
    async def test_success_with_records_updated(self, mock_migration):
        from app.containers.connector import run_files_to_records_migration_wrapper

        mock_migration.return_value = {
            "success": True,
            "records_updated": 5,
            "md5_copied": 3,
            "size_copied": 2,
        }

        container = _mock_container()
        result = await run_files_to_records_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration")
    async def test_success_no_records_updated(self, mock_migration):
        from app.containers.connector import run_files_to_records_migration_wrapper

        mock_migration.return_value = {
            "success": True,
            "records_updated": 0,
        }

        container = _mock_container()
        result = await run_files_to_records_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration")
    async def test_failure(self, mock_migration):
        from app.containers.connector import run_files_to_records_migration_wrapper

        mock_migration.return_value = {
            "success": False,
            "error": "Something went wrong",
        }

        container = _mock_container()
        result = await run_files_to_records_migration_wrapper(container)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        from app.containers.connector import run_files_to_records_migration_wrapper

        container = _mock_container()
        container.graph_provider = AsyncMock(side_effect=Exception("DB error"))

        result = await run_files_to_records_migration_wrapper(container)
        assert result is False


# ===================================================================
# run_drive_to_drive_workspace_migration_wrapper
# ===================================================================


class TestRunDriveToDriveWorkspaceMigrationWrapper:
    """Tests for run_drive_to_drive_workspace_migration_wrapper."""

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration")
    async def test_success_with_updates(self, mock_migration):
        from app.containers.connector import (
            run_drive_to_drive_workspace_migration_wrapper,
        )

        mock_migration.return_value = {
            "success": True,
            "connectors_updated": 3,
            "records_updated": 10,
        }

        container = _mock_container()
        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration")
    async def test_success_no_updates(self, mock_migration):
        from app.containers.connector import (
            run_drive_to_drive_workspace_migration_wrapper,
        )

        mock_migration.return_value = {
            "success": True,
            "connectors_updated": 0,
            "records_updated": 0,
        }

        container = _mock_container()
        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration")
    async def test_failure(self, mock_migration):
        from app.containers.connector import (
            run_drive_to_drive_workspace_migration_wrapper,
        )

        mock_migration.return_value = {
            "success": False,
            "error": "Drive migration error",
        }

        container = _mock_container()
        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        from app.containers.connector import (
            run_drive_to_drive_workspace_migration_wrapper,
        )

        container = _mock_container()
        container.graph_provider = AsyncMock(side_effect=Exception("DB error"))

        result = await run_drive_to_drive_workspace_migration_wrapper(container)
        assert result is False


# ===================================================================
# run_knowledge_base_migration
# ===================================================================


class TestRunKnowledgeBaseMigration:
    """Tests for run_knowledge_base_migration."""

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_kb_migration")
    async def test_success_with_migrated(self, mock_kb_migration):
        from app.containers.connector import run_knowledge_base_migration

        mock_kb_migration.return_value = {
            "success": True,
            "migrated_count": 5,
        }

        container = _mock_container()
        result = await run_knowledge_base_migration(container)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_kb_migration")
    async def test_failure(self, mock_kb_migration):
        from app.containers.connector import run_knowledge_base_migration

        mock_kb_migration.return_value = {
            "success": False,
            "message": "Failed to migrate",
        }

        container = _mock_container()
        result = await run_knowledge_base_migration(container)
        assert result is False


# ===================================================================
# run_connector_migration
# ===================================================================


class TestRunConnectorMigration:
    """Tests for run_connector_migration."""

    @pytest.mark.asyncio
    @patch("app.containers.connector.ConnectorMigrationService")
    async def test_success(self, mock_migration_cls):
        from app.containers.connector import run_connector_migration

        mock_service = AsyncMock()
        mock_service.migrate_all = AsyncMock()
        mock_migration_cls.return_value = mock_service

        container = _mock_container()
        result = await run_connector_migration(container)
        assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        from app.containers.connector import run_connector_migration

        container = _mock_container()
        container.graph_provider = AsyncMock(side_effect=Exception("DB error"))

        result = await run_connector_migration(container)
        assert result is False


# ===================================================================
# initialize_container — migration edge cases
# ===================================================================


class TestInitializeContainerMigrationEdges:
    """Tests for edge cases in initialize_container migration flow."""

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
    async def test_kb_to_connector_migration_skipped(
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

        # All migrations succeed; KB to connector already completed (skipped)
        migration_state = {}
        mock_config_service.get_config = AsyncMock(return_value=migration_state)

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = True
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {"success": True, "skipped": True}
        mock_permissions_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_permissions_to_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder_hierarchy.return_value = {"success": True, "skipped": True}
        mock_rg_app_edge.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {
            "success": True,
            "agents_deleted": 0,
            "templates_deleted": 0,
            "total_edges_deleted": 0,
        }

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            assert result is True

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
    async def test_kb_to_connector_migration_success(
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

        migration_state = {}
        mock_config_service.get_config = AsyncMock(return_value=migration_state)

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = True
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {
            "success": True,
            "orgs_processed": 2,
            "apps_created": 3,
            "records_updated": 10,
        }
        mock_permissions_edge.return_value = {"success": True, "migrated_edges": 5, "deleted_edges": 2}
        mock_permissions_to_kb.return_value = {"success": True, "migrated_edges": 3, "deleted_edges": 1}
        mock_folder_hierarchy.return_value = {
            "success": True,
            "folders_migrated": 5,
            "edges_created": 3,
            "edges_updated": 2,
        }
        mock_rg_app_edge.return_value = {"success": True, "edges_created": 4}
        mock_delete_agents.return_value = {
            "success": True,
            "agents_deleted": 1,
            "templates_deleted": 2,
            "total_edges_deleted": 3,
        }

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            assert result is True

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
    async def test_delete_agents_templates_failure(
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

        migration_state = {
            "knowledgeBase": True,
            "driveToDriveWorkspace": True,
            "permissionsEdge": True,
            "permissionsToKb": True,
            "folderHierarchy": True,
            "recordGroupAppEdge": True,
        }
        mock_config_service.get_config = AsyncMock(return_value=migration_state)

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = True
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {
            "success": False,
            "message": "Failed to delete old agents",
        }

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            # Continues even if delete agents fails
            assert result is True

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
    async def test_rg_app_edge_migration_success_not_skipped(
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

        migration_state = {
            "knowledgeBase": True,
            "driveToDriveWorkspace": True,
            "permissionsEdge": True,
            "permissionsToKb": True,
            "folderHierarchy": True,
            "deleteOldAgentsTemplates": True,
        }
        mock_config_service.get_config = AsyncMock(return_value=migration_state)

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = True
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {"success": True, "skipped": True}
        mock_rg_app_edge.return_value = {"success": True, "edges_created": 7}

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            assert result is True

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
    async def test_rg_app_edge_migration_failure(
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

        migration_state = {
            "knowledgeBase": True,
            "driveToDriveWorkspace": True,
            "permissionsEdge": True,
            "permissionsToKb": True,
            "folderHierarchy": True,
            "deleteOldAgentsTemplates": True,
        }
        mock_config_service.get_config = AsyncMock(return_value=migration_state)

        mock_health.system_health_check = AsyncMock()
        mock_kb_mig.return_value = True
        mock_connector_mig.return_value = True
        mock_files_records.return_value = True
        mock_drive_ws.return_value = True
        mock_kb_connector_mig.return_value = {"success": True, "skipped": True}
        mock_rg_app_edge.return_value = {"success": False, "message": "Failed"}

        with patch("app.containers.connector.os.getenv", return_value="arangodb"):
            result = await initialize_container(container)
            assert result is True
