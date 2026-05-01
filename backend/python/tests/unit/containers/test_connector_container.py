"""
Unit tests for ConnectorAppContainer (app/containers/connector.py).

Covers:
- ConnectorAppContainer instantiation and provider registration
- Static factories: _create_arango_service, _create_graphDB_provider, _create_data_store
- Wiring configuration
- initialize_container: health check, deployment config, arango service,
  data store, schema, run_all_team_migration, non-arangodb early return
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.connector import (
    ConnectorAppContainer,
    initialize_container,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_container(*, arango_service_value="real"):
    """Create a mock container matching initialize_container's expectations.

    Args:
        arango_service_value: "real" for a mock service, None for None.
    """
    container = MagicMock()
    logger = MagicMock()
    container.logger.return_value = logger

    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value={})
    config_service.set_config = AsyncMock()
    container.config_service.return_value = config_service

    mock_gp = AsyncMock()
    mock_gp.ensure_schema = AsyncMock()
    mock_data_store = MagicMock()
    mock_data_store.graph_provider = mock_gp
    container.data_store = AsyncMock(return_value=mock_data_store)

    if arango_service_value == "real":
        container.arango_service = AsyncMock(return_value=AsyncMock())
    else:
        container.arango_service = AsyncMock(return_value=None)

    container.graph_provider = AsyncMock(return_value=MagicMock())

    return container, logger, config_service


# ===========================================================================
# ConnectorAppContainer — instantiation & providers
# ===========================================================================


class TestConnectorAppContainerInstantiation:
    def test_container_can_be_instantiated(self):
        container = ConnectorAppContainer()
        assert container is not None

    def test_logger_is_singleton(self):
        container = ConnectorAppContainer()
        assert container.logger() is container.logger()

    def test_container_utils_on_class(self):
        assert ConnectorAppContainer.container_utils is not None


class TestConnectorAppContainerProviders:
    """Every DI provider declared on the container must be resolvable."""

    @pytest.mark.parametrize(
        "attr",
        [
            "key_value_store",
            "config_service",
            "kafka_service",
            "arango_service",
            "arango_client",
            "graph_provider",
            "data_store",
            "celery_app",
            "signed_url_config",
            "signed_url_handler",
            "feature_flag_service",
        ],
    )
    def test_provider_exists(self, attr):
        container = ConnectorAppContainer()
        assert getattr(container, attr) is not None


class TestWiringConfiguration:
    def test_wiring_config_has_all_expected_modules(self):
        container = ConnectorAppContainer()
        expected = [
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.localKB.api.kb_router",
            "app.connectors.sources.localKB.api.knowledge_hub_router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
        for mod in expected:
            assert mod in container.wiring_config.modules


# ===========================================================================
# Static factories
# ===========================================================================


class TestCreateArangoService:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "neo4j"})
    async def test_skips_when_not_arangodb(self):
        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is None

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "postgres"})
    async def test_skips_for_postgres(self):
        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is None

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.BaseArangoService")
    async def test_creates_and_connects_service(self, mock_cls):
        mock_service = AsyncMock()
        mock_cls.return_value = mock_service

        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is mock_service
        mock_service.connect.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {}, clear=True)
    @patch("app.containers.connector.BaseArangoService")
    async def test_defaults_to_arangodb_when_env_unset(self, mock_cls):
        mock_service = AsyncMock()
        mock_cls.return_value = mock_service

        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is mock_service

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "ARANGODB"})
    @patch("app.containers.connector.BaseArangoService")
    async def test_case_insensitive_data_store(self, mock_cls):
        mock_cls.return_value = AsyncMock()
        result = await ConnectorAppContainer._create_arango_service(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert result is not None

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.BaseArangoService")
    async def test_passes_enable_schema_init_true(self, mock_cls):
        mock_cls.return_value = AsyncMock()
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


class TestCreateGraphDBProvider:
    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDBProviderFactory.create_provider", new_callable=AsyncMock)
    async def test_creates_provider(self, mock_create):
        mock_provider = MagicMock()
        mock_create.return_value = mock_provider

        result = await ConnectorAppContainer._create_graphDB_provider(MagicMock(), MagicMock())
        assert result is mock_provider
        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDBProviderFactory.create_provider", new_callable=AsyncMock)
    async def test_passes_logger_and_config(self, mock_create):
        mock_create.return_value = MagicMock()
        logger = MagicMock()
        config = MagicMock()

        await ConnectorAppContainer._create_graphDB_provider(logger, config)
        mock_create.assert_awaited_once_with(logger=logger, config_service=config)


class TestCreateDataStore:
    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDataStore")
    async def test_creates_data_store(self, mock_cls):
        mock_ds = MagicMock()
        mock_cls.return_value = mock_ds

        result = await ConnectorAppContainer._create_data_store(MagicMock(), MagicMock())
        assert result is mock_ds

    @pytest.mark.asyncio
    @patch("app.containers.connector.GraphDataStore")
    async def test_passes_logger_and_provider(self, mock_cls):
        mock_cls.return_value = MagicMock()
        logger = MagicMock()
        provider = MagicMock()

        result = await ConnectorAppContainer._create_data_store(logger, provider)
        mock_cls.assert_called_once_with(logger, provider)


# ===========================================================================
# initialize_container — happy path
# ===========================================================================


class TestInitializeContainerSuccess:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_full_success(self, mock_all_team, mock_health):
        container, logger, config_service = _make_mock_container()
        mock_all_team.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)

        assert result is True
        mock_health.assert_awaited_once()
        mock_all_team.assert_awaited_once()
        config_service.set_config.assert_awaited()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_ensure_schema_called(self, mock_all_team, mock_health):
        container, logger, config_service = _make_mock_container()
        mock_all_team.return_value = {"success": True, "skipped": True}

        await initialize_container(container)

        data_store = await container.data_store()
        data_store.graph_provider.ensure_schema.assert_awaited_once()


# ===========================================================================
# initialize_container — failure paths
# ===========================================================================


class TestInitializeContainerFailures:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    async def test_health_check_failure_raises(self, mock_health):
        container, _, _ = _make_mock_container()
        mock_health.side_effect = Exception("Health check failed")

        with pytest.raises(Exception, match="Health check failed"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    async def test_arango_service_none_raises(self, mock_health):
        container, _, _ = _make_mock_container(arango_service_value=None)

        with pytest.raises(Exception, match="Failed to initialize ArangoDB service"):
            await initialize_container(container)

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    async def test_data_store_none_raises(self, mock_health):
        container, _, _ = _make_mock_container()
        container.data_store = AsyncMock(return_value=None)

        with pytest.raises(Exception, match="Failed to initialize data store"):
            await initialize_container(container)


# ===========================================================================
# initialize_container — deployment config edge cases
# ===========================================================================


class TestDeploymentConfig:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_get_config_failure_warns_but_continues(self, mock_all_team, mock_health):
        container, logger, config_service = _make_mock_container()
        config_service.get_config = AsyncMock(side_effect=Exception("etcd down"))
        mock_all_team.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)
        assert result is True

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_set_config_failure_warns_but_continues(self, mock_all_team, mock_health):
        container, logger, config_service = _make_mock_container()
        config_service.set_config = AsyncMock(side_effect=Exception("etcd unreachable"))
        mock_all_team.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)
        assert result is True

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_get_config_returns_none_uses_empty_dict(self, mock_all_team, mock_health):
        container, _, config_service = _make_mock_container()
        config_service.get_config = AsyncMock(return_value=None)
        mock_all_team.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)
        assert result is True


# ===========================================================================
# initialize_container — non-arangodb DATA_STORE
# ===========================================================================


class TestNonArangoDBDataStore:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "neo4j"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_neo4j_skips_arango_service_init(self, mock_all_team, mock_health):
        container, logger, _ = _make_mock_container()
        mock_all_team.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)

        assert result is True
        logger.info.assert_any_call("⏭️ Skipping ArangoDB service init (DATA_STORE=neo4j)")

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "neo4j"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_neo4j_still_returns_true(self, mock_all_team, mock_health):
        container, logger, _ = _make_mock_container()
        mock_all_team.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)

        assert result is True


# ===========================================================================
# initialize_container — run_all_team_migration outcomes
# ===========================================================================


class TestAllTeamMigration:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_already_completed(self, mock_all_team, mock_health):
        container, logger, _ = _make_mock_container()
        mock_all_team.return_value = {"success": True, "skipped": True}

        result = await initialize_container(container)

        assert result is True
        logger.info.assert_any_call("✅ All team migration already completed")

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_completed_with_work(self, mock_all_team, mock_health):
        container, logger, _ = _make_mock_container()
        mock_all_team.return_value = {
            "success": True,
            "skipped": False,
            "orgs_processed": 3,
            "teams_created": 5,
        }

        result = await initialize_container(container)
        assert result is True

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_failure_logged_but_continues(self, mock_all_team, mock_health):
        container, logger, _ = _make_mock_container()
        mock_all_team.return_value = {"success": False, "error": "DB error"}

        result = await initialize_container(container)

        assert result is True
        logger.error.assert_any_call("❌ All team migration failed: DB error")

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_failure_with_unknown_error(self, mock_all_team, mock_health):
        container, logger, _ = _make_mock_container()
        mock_all_team.return_value = {"success": False}

        result = await initialize_container(container)

        assert result is True
        logger.error.assert_any_call("❌ All team migration failed: Unknown error")

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_exception_logged_but_continues(self, mock_all_team, mock_health):
        container, logger, _ = _make_mock_container()
        mock_all_team.side_effect = Exception("unexpected crash")

        result = await initialize_container(container)
        assert result is True
