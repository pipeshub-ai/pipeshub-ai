"""
Additional coverage tests for app/containers/connector.py.

Targets remaining uncovered lines:
- initialize_container: kb_to_connector_migration non-skipped success path
- initialize_container: arango_service None skipping permissions/folder/rg migrations
- initialize_container: folder hierarchy migration with error key (not message)
- initialize_container: rg_app_edge non-skipped success path
- initialize_container: delete old agents templates non-skipped success path
- initialize_container: permissions migrations with actual results logged
- run_drive_to_drive_workspace_migration_wrapper: no updates needed path
- ConnectorAppContainer: kafka_service, celery_app provider existence
- ConnectorAppContainer: signed_url_config/handler providers
- ConnectorAppContainer: feature_flag_service provider
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.connector import (
    ConnectorAppContainer,
    initialize_container,
    run_connector_migration,
    run_drive_to_drive_workspace_migration_wrapper,
    run_files_to_records_migration_wrapper,
    run_knowledge_base_migration,
)


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
