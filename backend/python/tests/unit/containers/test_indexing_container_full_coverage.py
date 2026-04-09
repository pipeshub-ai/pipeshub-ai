from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.indexing import IndexingAppContainer, initialize_container


class TestIndexingAppContainerInstantiation:
    def test_container_can_be_instantiated(self):
        container = IndexingAppContainer()
        assert container is not None

    def test_logger_provider_exists(self):
        container = IndexingAppContainer()
        logger = container.logger()
        assert logger is not None

    def test_logger_is_singleton(self):
        container = IndexingAppContainer()
        l1 = container.logger()
        l2 = container.logger()
        assert l1 is l2

    def test_key_value_store_provider_exists(self):
        container = IndexingAppContainer()
        assert container.key_value_store is not None

    def test_config_service_provider_exists(self):
        container = IndexingAppContainer()
        assert container.config_service is not None

    def test_arango_client_provider_exists(self):
        container = IndexingAppContainer()
        assert container.arango_client is not None

    def test_kafka_service_provider_exists(self):
        container = IndexingAppContainer()
        assert container.kafka_service is not None

    def test_graph_provider_resource_exists(self):
        container = IndexingAppContainer()
        assert container.graph_provider is not None

    def test_vector_db_service_resource_exists(self):
        container = IndexingAppContainer()
        assert container.vector_db_service is not None

    def test_indexing_pipeline_resource_exists(self):
        container = IndexingAppContainer()
        assert container.indexing_pipeline is not None

    def test_document_extractor_resource_exists(self):
        container = IndexingAppContainer()
        assert container.document_extractor is not None

    def test_blob_storage_resource_exists(self):
        container = IndexingAppContainer()
        assert container.blob_storage is not None

    def test_graphdb_resource_exists(self):
        container = IndexingAppContainer()
        assert container.graphdb is not None

    def test_vector_store_resource_exists(self):
        container = IndexingAppContainer()
        assert container.vector_store is not None

    def test_sink_orchestrator_resource_exists(self):
        container = IndexingAppContainer()
        assert container.sink_orchestrator is not None

    def test_parsers_resource_exists(self):
        container = IndexingAppContainer()
        assert container.parsers is not None

    def test_processor_resource_exists(self):
        container = IndexingAppContainer()
        assert container.processor is not None

    def test_event_processor_resource_exists(self):
        container = IndexingAppContainer()
        assert container.event_processor is not None

    def test_container_utils_on_class(self):
        assert IndexingAppContainer.container_utils is not None

    def test_wiring_config_modules(self):
        container = IndexingAppContainer()
        wiring = container.wiring_config
        assert "app.indexing_main" in wiring.modules


class TestInitializeContainer:
    def _make_mock_container(self):
        container = MagicMock()
        logger = MagicMock()
        container.logger.return_value = logger
        mock_graph_provider = MagicMock()
        container.graph_provider = AsyncMock(return_value=mock_graph_provider)
        return container, logger

    @pytest.mark.asyncio
    @patch("app.containers.indexing.Health.health_check_connector_service", new_callable=AsyncMock)
    @patch("app.containers.indexing.Health.system_health_check", new_callable=AsyncMock)
    async def test_success(self, mock_sys_health, mock_conn_health):
        container, logger = self._make_mock_container()
        result = await initialize_container(container)
        assert result is True
        mock_conn_health.assert_awaited_once_with(container)
        mock_sys_health.assert_awaited_once_with(container)

    @pytest.mark.asyncio
    @patch("app.containers.indexing.Health.health_check_connector_service", new_callable=AsyncMock)
    @patch("app.containers.indexing.Health.system_health_check", new_callable=AsyncMock)
    async def test_stores_resolved_graph_provider(self, mock_sys_health, mock_conn_health):
        container, logger = self._make_mock_container()
        mock_gp = MagicMock()
        container.graph_provider = AsyncMock(return_value=mock_gp)
        await initialize_container(container)
        assert container._graph_provider is mock_gp

    @pytest.mark.asyncio
    @patch("app.containers.indexing.Health.health_check_connector_service", new_callable=AsyncMock)
    async def test_fails_on_connector_health_check(self, mock_conn_health):
        container, logger = self._make_mock_container()
        mock_conn_health.side_effect = Exception("connector down")
        with pytest.raises(Exception, match="connector down"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch("app.containers.indexing.Health.health_check_connector_service", new_callable=AsyncMock)
    async def test_fails_on_graph_provider_none(self, mock_conn_health):
        container, logger = self._make_mock_container()
        container.graph_provider = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="Failed to initialize Graph Database Provider"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch("app.containers.indexing.Health.health_check_connector_service", new_callable=AsyncMock)
    @patch("app.containers.indexing.Health.system_health_check", new_callable=AsyncMock)
    async def test_fails_on_system_health_check(self, mock_sys_health, mock_conn_health):
        container, logger = self._make_mock_container()
        mock_sys_health.side_effect = Exception("system unhealthy")
        with pytest.raises(Exception, match="system unhealthy"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch("app.containers.indexing.Health.health_check_connector_service", new_callable=AsyncMock)
    @patch("app.containers.indexing.Health.system_health_check", new_callable=AsyncMock)
    async def test_logs_initialization_messages(self, mock_sys_health, mock_conn_health):
        container, logger = self._make_mock_container()
        await initialize_container(container)
        assert any("Initializing" in str(c) for c in logger.info.call_args_list)
        assert any("Graph Database Provider initialized" in str(c) for c in logger.info.call_args_list)

    @pytest.mark.asyncio
    @patch("app.containers.indexing.Health.health_check_connector_service", new_callable=AsyncMock)
    async def test_logs_error_on_failure(self, mock_conn_health):
        container, logger = self._make_mock_container()
        container.graph_provider = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await initialize_container(container)
        assert any("Failed to initialize" in str(c) for c in logger.error.call_args_list)
