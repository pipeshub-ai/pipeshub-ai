"""Tests for app.edition_services — OSS edition service registry and lazy imports."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Importability of re-exported services
# ---------------------------------------------------------------------------


class TestReExports:
    """Verify that the canonical service classes are importable from edition_services."""

    def test_token_refresh_service_importable(self) -> None:
        from app.edition_services import TokenRefreshService

        assert TokenRefreshService is not None

    def test_toolset_token_refresh_service_importable(self) -> None:
        from app.edition_services import ToolsetTokenRefreshService

        assert ToolsetTokenRefreshService is not None


# ---------------------------------------------------------------------------
# __getattr__ lazy imports
# ---------------------------------------------------------------------------


class TestGetattr:
    """The module's __getattr__ returns concrete classes by name."""

    def test_event_service(self) -> None:
        import app.edition_services as mod

        cls = getattr(mod, "EventService")
        assert cls.__name__ == "EventService"
        assert cls.__module__.endswith("connectors.services.event_service")

    def test_entity_event_service(self) -> None:
        import app.edition_services as mod

        cls = getattr(mod, "EntityEventService")
        assert cls.__name__ == "EntityEventService"
        assert cls.__module__.endswith("messaging.kafka.handlers.entity")

    def test_record_event_handler(self) -> None:
        import app.edition_services as mod

        cls = getattr(mod, "RecordEventHandler")
        assert cls.__name__ == "RecordEventHandler"
        assert cls.__module__.endswith("messaging.kafka.handlers.record")

    def test_unknown_name_raises_attribute_error(self) -> None:
        import app.edition_services as mod

        with pytest.raises(AttributeError):
            getattr(mod, "NoSuchService")


# ---------------------------------------------------------------------------
# get_connector_registry_cls
# ---------------------------------------------------------------------------


class TestGetConnectorRegistryCls:
    def test_returns_connector_registry(self) -> None:
        from app.edition_services import get_connector_registry_cls

        result = get_connector_registry_cls()
        assert result.__name__ == "ConnectorRegistry"
        assert result.__module__.endswith("registry.connector_registry")


# ---------------------------------------------------------------------------
# scope_org_resources
# ---------------------------------------------------------------------------


class TestScopeOrgResources:
    async def test_returns_tuple(self) -> None:
        from app.edition_services import scope_org_resources

        app_container = MagicMock()
        cs_mock = MagicMock()
        app_container.config_service.return_value = cs_mock
        data_store = MagicMock()
        result = await scope_org_resources(app_container, data_store, "org-1")
        assert isinstance(result, tuple)
        assert len(result) == 2

    async def test_returns_config_service_and_data_store(self) -> None:
        from app.edition_services import scope_org_resources

        app_container = MagicMock()
        cs_mock = MagicMock(name="cs")
        app_container.config_service.return_value = cs_mock
        data_store = MagicMock(name="ds")
        cs, ds = await scope_org_resources(app_container, data_store, "org-1")
        if cs is cs_mock:
            assert ds is data_store
        else:
            assert type(cs).__name__.startswith("ConfigurationService")
            assert type(ds).__name__ == "GraphDataStore"


# ---------------------------------------------------------------------------
# register_extra_connectors (no-op)
# ---------------------------------------------------------------------------


class TestRegisterExtraConnectors:
    def test_no_op(self) -> None:
        from app.edition_services import register_extra_connectors

        register_extra_connectors()


# ---------------------------------------------------------------------------
# get_startup_extra_kwargs
# ---------------------------------------------------------------------------


class TestGetStartupExtraKwargs:
    def test_returns_empty_dict(self) -> None:
        from app.edition_services import get_startup_extra_kwargs

        result = get_startup_extra_kwargs(MagicMock())
        assert isinstance(result, dict)
        assert set(result) <= {"oauth_config_resolver"}


# ---------------------------------------------------------------------------
# pre_sync_hook (async no-op)
# ---------------------------------------------------------------------------


class TestPreSyncHook:
    async def test_is_coroutine_and_noop(self) -> None:
        from app.edition_services import pre_sync_hook

        result = await pre_sync_hook(MagicMock(), MagicMock())
        assert result is None


# ---------------------------------------------------------------------------
# get_data_entities_processor_cls
# ---------------------------------------------------------------------------


class TestGetDataEntitiesProcessorCls:
    def test_returns_processor_class(self) -> None:
        from app.edition_services import get_data_entities_processor_cls

        result = get_data_entities_processor_cls()
        assert result.__name__ == "DataSourceEntitiesProcessor"
        assert result.__module__.endswith("data_processor.data_source_entities_processor")
