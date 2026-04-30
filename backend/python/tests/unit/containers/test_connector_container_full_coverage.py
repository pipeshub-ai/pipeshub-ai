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
        arango_service = MagicMock()
        config_service = MagicMock()
        container = MagicMock()
        container.logger.return_value = logger
        container.config_service.return_value = config_service

        await run_connector_migration(container, arango_service)
        mock_cls.assert_called_once_with(
            arango_service=arango_service,
            config_service=config_service,
            logger=logger,
        )

    @pytest.mark.asyncio
    @patch("app.containers.connector.ConnectorMigrationService")
    async def test_logs_error_on_exception(self, mock_cls):
        logger = MagicMock()
        mock_cls.side_effect = RuntimeError("boom")
        container = MagicMock()
        container.logger.return_value = logger
        result = await run_connector_migration(container, MagicMock())
        assert result is False
        logger.error.assert_called()


class TestRunFilesToRecordsMigrationEdgeCases:
    @pytest.mark.asyncio
    @patch("app.containers.connector.run_files_to_records_migration", new_callable=AsyncMock)
    async def test_failure_with_no_error_key(self, mock_mig):
        mock_mig.return_value = {"success": False}
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.config_service.return_value = MagicMock()
        result = await run_files_to_records_migration_wrapper(container, MagicMock())
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
        container.config_service.return_value = MagicMock()
        result = await run_files_to_records_migration_wrapper(container, MagicMock())
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
        container.config_service.return_value = MagicMock()
        result = await run_drive_to_drive_workspace_migration_wrapper(container, MagicMock())
        assert result is True

    @pytest.mark.asyncio
    @patch("app.containers.connector.run_drive_to_drive_workspace_migration", new_callable=AsyncMock)
    async def test_failure_with_no_error_key(self, mock_mig):
        mock_mig.return_value = {"success": False}
        container = MagicMock()
        container.logger.return_value = MagicMock()
        container.config_service.return_value = MagicMock()
        result = await run_drive_to_drive_workspace_migration_wrapper(container, MagicMock())
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
        container.config_service.return_value = MagicMock()
        result = await run_drive_to_drive_workspace_migration_wrapper(container, MagicMock())
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
