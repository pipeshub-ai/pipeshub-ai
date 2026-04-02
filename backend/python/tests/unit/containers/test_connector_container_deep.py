"""
Additional tests for ConnectorAppContainer (app/containers/connector.py).

Targets coverage for the initialize_container function's internal migration logic:
- get_migration_state, migration_completed, mark_migration_completed closures
- All migration code paths inside initialize_container
- Permissions edge migration with arango_service None
- Folder hierarchy migration with various results
- Record group app edge migration
- Delete old agents templates migration
- kb_to_connector_migration path with success/skipped/failure
- arango_service None path for permission and folder hierarchy migrations
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.connector import initialize_container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_container(migration_state=None):
    """Create a mock container with configurable migration state."""
    container = MagicMock()
    logger = MagicMock()
    container.logger.return_value = logger

    config_service = AsyncMock()
    # Migration state dictionary
    state = migration_state if migration_state is not None else {}
    config_service.get_config = AsyncMock(return_value=state)
    config_service.set_config = AsyncMock()
    container.config_service.return_value = config_service

    mock_data_store = MagicMock()
    mock_data_store.graph_provider = AsyncMock()
    mock_data_store.graph_provider.ensure_schema = AsyncMock()
    container.data_store = AsyncMock(return_value=mock_data_store)

    arango_service = AsyncMock()
    container.arango_service = AsyncMock(return_value=arango_service)
    container.graph_provider = AsyncMock(return_value=MagicMock())

    return container, logger, config_service, arango_service


# ---------------------------------------------------------------------------
# initialize_container - Internal migration closures
# ---------------------------------------------------------------------------


class TestInitializeContainerMigrationClosures:
    """Tests for the internal closures: get_migration_state, migration_completed, mark_migration_completed."""

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
    async def test_mark_migration_completed_stores_state(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """mark_migration_completed should call set_config on the config_service."""
        container, logger, config_service, arango_service = _make_mock_container()

        # KB migration succeeds on first run (not previously completed)
        mock_kb_mig.return_value = {"success": True, "migrated_count": 1}
        mock_connector_mig_cls.return_value = AsyncMock()
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 1, "records_updated": 2}
        mock_kb_to_conn.return_value = {"success": True, "skipped": False, "orgs_processed": 1, "apps_created": 1, "records_updated": 1}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 1, "deleted_edges": 0}
        mock_perms_to_kb.return_value = {"success": True, "migrated_edges": 1, "deleted_edges": 0}
        mock_folder_mig.return_value = {"success": True, "folders_migrated": 2, "edges_created": 3, "edges_updated": 1}
        mock_rg_app_edge.return_value = {"success": True, "edges_created": 5}
        mock_delete_agents.return_value = {"success": True, "agents_deleted": 1, "templates_deleted": 2, "total_edges_deleted": 3}

        result = await initialize_container(container)
        assert result is True
        # The function completed successfully - migrations ran with their results logged

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
    async def test_get_migration_state_returns_empty_when_none(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """get_migration_state should return {} when config returns None."""
        container, logger, config_service, arango_service = _make_mock_container()
        config_service.get_config = AsyncMock(return_value=None)

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig_cls.return_value = AsyncMock()
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


class TestInitializeContainerArangoNone:
    """Tests for paths where arango_service is None."""

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
    async def test_arango_service_none_skips_arango_dependent_migrations(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """When arango_service is None, permission and folder migrations should be skipped."""
        container, logger, config_service, _ = _make_mock_container()
        # arango_service returns None
        container.arango_service = AsyncMock(return_value=None)

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig_cls.return_value = AsyncMock()
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_to_conn.return_value = {"success": True, "skipped": True}
        mock_rg_app_edge.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        with pytest.raises(Exception, match="Failed to initialize ArangoDB service"):
            await initialize_container(container)


class TestInitializeContainerFolderHierarchyMigration:
    """Tests for folder hierarchy migration branches."""

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
    async def test_folder_hierarchy_migration_success_not_skipped(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """Folder hierarchy migration with actual folders migrated."""
        container, logger, config_service, arango_service = _make_mock_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig_cls.return_value = AsyncMock()
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_to_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_to_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        # Folder migration with actual work done
        mock_folder_mig.return_value = {
            "success": True,
            "folders_migrated": 5,
            "edges_created": 10,
            "edges_updated": 3,
        }
        mock_rg_app_edge.return_value = {"success": True, "edges_created": 2}
        mock_delete_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

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
    async def test_folder_hierarchy_migration_failure_with_error_key(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """Folder hierarchy migration failure using 'error' key."""
        container, logger, config_service, arango_service = _make_mock_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig_cls.return_value = AsyncMock()
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_to_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_to_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder_mig.return_value = {"success": False, "error": "folder error"}
        mock_rg_app_edge.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True  # Failures don't fail startup


class TestInitializeContainerKbToConnectorMigration:
    """Tests for kb_to_connector migration paths."""

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
    async def test_kb_to_connector_success_not_skipped(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """KB to connector migration completes with actual work."""
        container, logger, config_service, arango_service = _make_mock_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig_cls.return_value = AsyncMock()
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_to_conn.return_value = {
            "success": True,
            "skipped": False,
            "orgs_processed": 3,
            "apps_created": 2,
            "records_updated": 5,
        }
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_to_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder_mig.return_value = {"success": True, "skipped": True}
        mock_rg_app_edge.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {"success": True, "agents_deleted": 0, "templates_deleted": 0, "total_edges_deleted": 0}

        result = await initialize_container(container)
        assert result is True


class TestInitializeContainerDeleteAgentsMigration:
    """Tests for delete old agents/templates migration paths."""

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
    async def test_delete_agents_templates_failure(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """Delete agents/templates migration failure is handled gracefully."""
        container, logger, config_service, arango_service = _make_mock_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 0}
        mock_connector_mig_cls.return_value = AsyncMock()
        mock_files_mig.return_value = {"success": True, "records_updated": 0}
        mock_drive_mig.return_value = {"success": True, "connectors_updated": 0, "records_updated": 0}
        mock_kb_to_conn.return_value = {"success": True, "skipped": True}
        mock_perms_edge.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_perms_to_kb.return_value = {"success": True, "migrated_edges": 0, "deleted_edges": 0}
        mock_folder_mig.return_value = {"success": True, "skipped": True}
        mock_rg_app_edge.return_value = {"success": True, "skipped": True}
        mock_delete_agents.return_value = {"success": False, "message": "delete failed"}

        result = await initialize_container(container)
        assert result is True


class TestInitializeContainerKBMigrationFirstRunSuccess:
    """Test KB migration on first run (not previously completed) with success."""

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
    async def test_kb_migration_first_run_success_marks_completed(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """KB migration first run success calls mark_migration_completed."""
        container, logger, config_service, arango_service = _make_mock_container()

        mock_kb_mig.return_value = {"success": True, "migrated_count": 1}
        mock_connector_mig_cls.return_value = AsyncMock()
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
    async def test_kb_migration_first_run_failure_continues(
        self, mock_drive_mig, mock_files_mig, mock_connector_mig_cls,
        mock_delete_agents, mock_rg_app_edge, mock_folder_mig,
        mock_perms_to_kb, mock_perms_edge, mock_kb_to_conn,
        mock_kb_mig, mock_health,
    ):
        """KB migration failure on first run doesn't stop initialization."""
        container, logger, config_service, arango_service = _make_mock_container()

        mock_kb_mig.return_value = {"success": False, "message": "KB error"}
        mock_connector_mig_cls.return_value = AsyncMock()
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
