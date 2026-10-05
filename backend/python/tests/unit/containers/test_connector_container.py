"""
Unit tests for ConnectorAppContainer (app/containers/connector.py).

Covers:
- ConnectorAppContainer instantiation and provider registration
- Static factories: _create_graphDB_provider, _create_data_store
- Wiring configuration
- initialize_container: health check, deployment config,
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


def _make_mock_container():
    """Create a mock container matching initialize_container's expectations."""
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
# initialize_container — node relation migration ordering
# ===========================================================================


class TestNodeRelationMigrationIsFatal:
    """The one migration that must NOT follow the house log-and-continue pattern.

    It renames the hierarchy edge collection and runs *before* ensure_schema().
    If it fails and initialization carries on, ensure_schema() creates an empty
    nodeRelations while the real edges still sit under the old name — the
    service then serves a graph with no hierarchy at all, and the next boot's
    both-exist branch has to clean up after it. The All-team and KB-apps
    migrations below it deliberately swallow their errors, so nothing but this
    test stops someone "fixing" the inconsistency.
    """

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_node_relation_migration", new_callable=AsyncMock)
    async def test_a_failed_migration_is_fatal_and_skips_schema_init(
        self, mock_migration, mock_health
    ):
        container, _logger, _config_service = _make_mock_container()
        mock_migration.return_value = {"success": False, "error": "rename refused"}
        ensure_schema = container.data_store.return_value.graph_provider.ensure_schema

        with pytest.raises(Exception, match="rename refused"):
            await initialize_container(container)

        ensure_schema.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "arangodb"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_node_relation_migration", new_callable=AsyncMock)
    async def test_a_successful_migration_lets_schema_init_run(
        self, mock_migration, mock_all_team, mock_health
    ):
        """Guards the guard: without this, the assertion above would hold even
        if ensure_schema were never awaited on any path."""
        container, _logger, _config_service = _make_mock_container()
        mock_migration.return_value = {"success": True, "skipped": True}
        mock_all_team.return_value = {"success": True, "skipped": True}
        ensure_schema = container.data_store.return_value.graph_provider.ensure_schema

        assert await initialize_container(container) is True
        ensure_schema.assert_awaited_once()


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


# ===========================================================================
# Graph migrations that run after the service has started
# ===========================================================================

_BACKGROUND = {
    "links": "run_record_link_migration",
    "folders": "run_folder_mime_type_migration",
    "hierarchy": "run_hierarchy_backfill_migration",
    "listing": "run_kh_listing_state_migration",
}
_DONE = {"success": True, "skipped": True}


class TestBackgroundGraphMigrations:
    @pytest.fixture
    def migrations(self):
        """The four background migrations, recording the order they were tried in."""
        tried: list[str] = []
        mocks = {}
        patches = []
        for key, name in _BACKGROUND.items():
            mock = AsyncMock(return_value=_DONE)
            mock.side_effect = lambda *_a, _key=key, _mock=mock, **_k: tried.append(_key) or _mock.return_value
            mocks[key] = mock
            patches.append(patch(f"app.containers.connector.{name}", mock))
        sleep = patch("app.containers.connector.asyncio.sleep", new_callable=AsyncMock)
        for p in patches:
            p.start()
        mocks["sleep"] = sleep.start()
        mocks["tried"] = tried
        yield mocks
        patch.stopall()

    @pytest.mark.asyncio
    async def test_runs_each_once_in_dependency_order(self, migrations):
        from app.containers.connector import _kh_graph_migrations

        assert await _kh_graph_migrations(MagicMock(), MagicMock(), MagicMock(), kb_apps_done=True) is True

        assert migrations["tried"] == ["links", "folders", "hierarchy", "listing"]
        migrations["sleep"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_step_is_retried_and_holds_back_the_steps_that_read_it(self, migrations):
        from app.containers.connector import KH_MIGRATION_RETRY_SECONDS, _kh_graph_migrations

        outcomes = iter([{"success": False, "error": "deadlock"}, _DONE])
        migrations["links"].side_effect = lambda **_k: migrations["tried"].append("links") or next(outcomes)

        assert await _kh_graph_migrations(MagicMock(), MagicMock(), MagicMock(), kb_apps_done=True) is True

        assert migrations["tried"] == ["links", "folders", "links", "hierarchy", "listing"]
        migrations["sleep"].assert_awaited_once_with(KH_MIGRATION_RETRY_SECONDS)

    @pytest.mark.asyncio
    async def test_a_listing_stamp_that_raised_is_retried(self, migrations):
        from app.containers.connector import _kh_graph_migrations

        calls = {"n": 0}

        def stamp(**_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("DeadlockDetected")
            return _DONE

        migrations["listing"].side_effect = stamp

        assert await _kh_graph_migrations(MagicMock(), MagicMock(), MagicMock(), kb_apps_done=True) is True

        assert calls["n"] == 2
        assert migrations["hierarchy"].await_count == 1

    @pytest.mark.asyncio
    async def test_hierarchy_steps_wait_for_the_kb_apps_migration(self, migrations):
        from app.containers.connector import _kh_graph_migrations

        assert await _kh_graph_migrations(MagicMock(), MagicMock(), MagicMock(), kb_apps_done=False) is False

        assert migrations["tried"] == ["links", "folders"]

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"DATA_STORE": "neo4j"})
    @patch("app.containers.connector.Health.system_health_check", new_callable=AsyncMock)
    @patch("app.containers.connector.run_kb_apps_migration", new_callable=AsyncMock)
    @patch("app.containers.connector.run_all_team_migration", new_callable=AsyncMock)
    async def test_the_service_starts_without_waiting_for_them(self, mock_all_team, mock_kb_apps, mock_health):
        import asyncio

        from app.containers import connector as module

        container, _logger, _config = _make_mock_container()
        mock_all_team.return_value = _DONE
        mock_kb_apps.return_value = _DONE
        never = asyncio.Event()

        async def slow(**_k):
            await never.wait()

        before = set(module._kh_scope_tasks)
        with patch.object(module, "run_record_link_migration", slow):
            assert await asyncio.wait_for(initialize_container(container), timeout=5) is True
            await asyncio.sleep(0)
        started = module._kh_scope_tasks - before
        try:
            assert [task for task in started if not task.done()], "the migrations are still running"
        finally:
            for task in started:
                task.cancel()
            module._kh_scope_tasks.difference_update(started)
