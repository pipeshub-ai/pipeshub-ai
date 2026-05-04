"""Tests for app.connectors.core.factory.connector_factory.ConnectorFactory."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.factory.connector_factory import ConnectorFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Store original registry state so tests don't pollute each other.
_ORIGINAL_REGISTRY = None


@pytest.fixture(autouse=True)
def _restore_registry():
    """Save and restore the class-level _connector_registry between tests."""
    global _ORIGINAL_REGISTRY
    _ORIGINAL_REGISTRY = ConnectorFactory._connector_registry.copy()
    yield
    ConnectorFactory._connector_registry = _ORIGINAL_REGISTRY


# ===========================================================================
# get_connector_class
# ===========================================================================


class TestGetConnectorClass:
    """Tests for ConnectorFactory.get_connector_class."""

    def test_returns_known_connector(self):
        """Returns the class for a known connector name."""
        cls = ConnectorFactory.get_connector_class("onedrive")
        assert cls is not None

    def test_case_insensitive_lookup(self):
        """Lookup is case-insensitive."""
        cls1 = ConnectorFactory.get_connector_class("OneDrive")
        cls2 = ConnectorFactory.get_connector_class("ONEDRIVE")
        cls3 = ConnectorFactory.get_connector_class("onedrive")
        assert cls1 == cls2 == cls3

    def test_returns_none_for_unknown(self):
        """Returns None for unknown connector names."""
        result = ConnectorFactory.get_connector_class("nonexistent_connector")
        assert result is None

    def test_returns_none_for_empty_string(self):
        """Returns None for empty string."""
        result = ConnectorFactory.get_connector_class("")
        assert result is None


# ===========================================================================
# list_connectors
# ===========================================================================


class TestListConnectors:
    """Tests for ConnectorFactory.list_connectors."""

    def test_returns_dict(self):
        """Returns a dictionary."""
        result = ConnectorFactory.list_connectors()
        assert isinstance(result, dict)

    def test_returns_copy(self):
        """Returns a copy, not the original registry."""
        result = ConnectorFactory.list_connectors()
        assert result is not ConnectorFactory._connector_registry

    def test_modifying_copy_does_not_affect_registry(self):
        """Modifying the returned dict does not change the registry."""
        result = ConnectorFactory.list_connectors()
        original_len = len(ConnectorFactory._connector_registry)
        result["fake_connector"] = MagicMock()
        assert len(ConnectorFactory._connector_registry) == original_len

    def test_contains_known_connectors(self):
        """Contains at least some known connectors."""
        result = ConnectorFactory.list_connectors()
        assert "onedrive" in result
        assert "confluence" in result
        assert "jira" in result


# ===========================================================================
# register_connector
# ===========================================================================


class TestRegisterConnector:
    """Tests for ConnectorFactory.register_connector."""

    def test_register_new_connector(self):
        """New connector is added to the registry."""
        mock_cls = MagicMock()
        ConnectorFactory.register_connector("custom_test", mock_cls)

        assert ConnectorFactory._connector_registry["custom_test"] is mock_cls

    def test_register_normalizes_name_to_lowercase(self):
        """Connector name is normalized to lowercase."""
        mock_cls = MagicMock()
        ConnectorFactory.register_connector("MyConnector", mock_cls)

        assert "myconnector" in ConnectorFactory._connector_registry

    def test_register_overwrites_existing(self):
        """Registering an existing name overwrites the previous class."""
        mock_cls_1 = MagicMock()
        mock_cls_2 = MagicMock()

        ConnectorFactory.register_connector("test_overwrite", mock_cls_1)
        ConnectorFactory.register_connector("test_overwrite", mock_cls_2)

        assert ConnectorFactory._connector_registry["test_overwrite"] is mock_cls_2


# ===========================================================================
# initialize_beta_connector_registry
# ===========================================================================


class TestInitializeBetaConnectorRegistry:
    """Tests for ConnectorFactory.initialize_beta_connector_registry."""

    def test_registers_all_beta_connectors(self):
        """All beta connectors from _beta_connector_definitions are registered."""
        ConnectorFactory.initialize_beta_connector_registry()

        for name in ConnectorFactory._beta_connector_definitions:
            assert name in ConnectorFactory._connector_registry

    def test_beta_connectors_classes_correct(self):
        """Beta connector classes match the definitions."""
        ConnectorFactory.initialize_beta_connector_registry()

        for name, expected_cls in ConnectorFactory._beta_connector_definitions.items():
            assert ConnectorFactory._connector_registry[name] is expected_cls

    def test_idempotent(self):
        """Calling initialize twice does not cause issues."""
        ConnectorFactory.initialize_beta_connector_registry()
        first = ConnectorFactory.list_connectors()

        ConnectorFactory.initialize_beta_connector_registry()
        second = ConnectorFactory.list_connectors()

        assert first.keys() == second.keys()


# ===========================================================================
# list_beta_connectors
# ===========================================================================


class TestListBetaConnectors:
    """Tests for ConnectorFactory.list_beta_connectors."""

    def test_returns_dict(self):
        result = ConnectorFactory.list_beta_connectors()
        assert isinstance(result, dict)

    def test_returns_copy(self):
        """Returns a copy of beta definitions."""
        result = ConnectorFactory.list_beta_connectors()
        assert result is not ConnectorFactory._beta_connector_definitions

    def test_contains_known_beta_connectors(self):
        result = ConnectorFactory.list_beta_connectors()
        assert "slack" in result
        assert "calendar" in result

    def test_modifying_copy_does_not_affect_definitions(self):
        result = ConnectorFactory.list_beta_connectors()
        result["new_beta"] = MagicMock()
        assert "new_beta" not in ConnectorFactory._beta_connector_definitions


# ===========================================================================
# create_connector
# ===========================================================================


class TestCreateConnector:
    """Tests for ConnectorFactory.create_connector."""

    @pytest.mark.asyncio
    async def test_unknown_connector_returns_none(self):
        """Unknown connector name returns None."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()

        result = await ConnectorFactory.create_connector(
            name="totally_unknown",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-1",
            scope="personal",
            created_by="user-123",
        )

        assert result is None
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_successful_creation(self):
        """Successful connector creation returns the connector instance."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()
        expected_connector = MagicMock()

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=expected_connector)

        # Register and create
        ConnectorFactory.register_connector("test_create", mock_cls)

        result = await ConnectorFactory.create_connector(
            name="test_create",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-2",
            scope="personal",
            created_by="user-123",
        )

        assert result is expected_connector
        mock_cls.create_connector.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_creation_exception_returns_none(self):
        """If create_connector raises, returns None."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(side_effect=RuntimeError("init failed"))

        ConnectorFactory.register_connector("test_fail", mock_cls)

        result = await ConnectorFactory.create_connector(
            name="test_fail",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-3",
            scope="personal",
            created_by="user-123",
        )

        assert result is None
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_creation_passes_kwargs(self):
        """Extra kwargs are forwarded to the connector's create_connector."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=MagicMock())

        ConnectorFactory.register_connector("test_kwargs", mock_cls)

        await ConnectorFactory.create_connector(
            name="test_kwargs",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-4",
            scope="personal",
            created_by="user-123",
            custom_param="value123",
        )

        call_kwargs = mock_cls.create_connector.call_args[1]
        assert call_kwargs["custom_param"] == "value123"


# ===========================================================================
# initialize_connector
# ===========================================================================


class TestInitializeConnector:
    """Tests for ConnectorFactory.initialize_connector."""

    @pytest.mark.asyncio
    async def test_successful_initialization(self):
        """Connector is created and initialized."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()
        mock_connector = AsyncMock()
        mock_connector.init.return_value = True

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=mock_connector)
        ConnectorFactory.register_connector("test_init", mock_cls)

        result = await ConnectorFactory.initialize_connector(
            name="test_init",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-5",
            scope="personal",
            created_by="user-123",
        )

        assert result is mock_connector
        mock_connector.init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_init_returns_false(self):
        """If init() returns False, returns None."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()
        mock_connector = AsyncMock()
        mock_connector.init.return_value = False

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=mock_connector)
        ConnectorFactory.register_connector("test_init_fail", mock_cls)

        result = await ConnectorFactory.initialize_connector(
            name="test_init_fail",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-6",
            scope="personal",
            created_by="user-123",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_init_exception_returns_none(self):
        """If init() raises, returns None."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()
        mock_connector = AsyncMock()
        mock_connector.init.side_effect = RuntimeError("init boom")

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=mock_connector)
        ConnectorFactory.register_connector("test_init_exc", mock_cls)

        result = await ConnectorFactory.initialize_connector(
            name="test_init_exc",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-7",
            scope="personal",
            created_by="user-123",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_create_returns_none_skips_init(self):
        """If create_connector returns None, init is not called."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()

        result = await ConnectorFactory.initialize_connector(
            name="totally_unknown_init",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-8",
            scope="personal",
            created_by="user-123",
        )

        assert result is None


# ===========================================================================
# create_and_start_sync
# ===========================================================================


class TestCreateAndStartSync:
    """Tests for ConnectorFactory.create_and_start_sync (lines 230-242)."""

    @pytest.mark.asyncio
    async def test_connector_none_returns_none(self):
        """If initialize_connector returns None, returns None."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)

        result = await ConnectorFactory.create_and_start_sync(
            name="totally_unknown_sync",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-10",
            scope="personal",
            created_by="test-user-id",
        )
        assert result is None

    @pytest.mark.asyncio
    @patch("app.connectors.core.factory.connector_factory.sync_task_manager")
    async def test_start_sync_success(self, mock_stm):
        """Connector is created, initialized, and sync started."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": {"selectedStrategy": "FULL_SYNC"}})

        mock_connector = AsyncMock()
        mock_connector.init.return_value = True
        mock_connector.run_sync = MagicMock(return_value=MagicMock())

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=mock_connector)
        ConnectorFactory.register_connector("test_sync_start", mock_cls)

        mock_stm.start_sync = AsyncMock()

        result = await ConnectorFactory.create_and_start_sync(
            name="test_sync_start",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-11",
            scope="personal",
            created_by="test-user-id",
        )
        assert result is mock_connector
        mock_stm.start_sync.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.core.factory.connector_factory.sync_task_manager")
    async def test_manual_strategy_skips_sync(self, mock_stm):
        """Manual sync strategy skips start_sync call."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": {"selectedStrategy": "MANUAL"}})

        mock_connector = AsyncMock()
        mock_connector.init.return_value = True

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=mock_connector)
        ConnectorFactory.register_connector("test_manual_sync", mock_cls)

        mock_stm.start_sync = AsyncMock()

        result = await ConnectorFactory.create_and_start_sync(
            name="test_manual_sync",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-12",
            scope="personal",
            created_by="test-user-id",
        )
        assert result is mock_connector
        mock_stm.start_sync.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.core.factory.connector_factory.sync_task_manager")
    async def test_sync_exception_returns_none(self, mock_stm):
        """If start_sync raises, returns None."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": {"selectedStrategy": "FULL_SYNC"}})

        mock_connector = AsyncMock()
        mock_connector.init.return_value = True
        mock_connector.run_sync = MagicMock(return_value=MagicMock())

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=mock_connector)
        ConnectorFactory.register_connector("test_sync_fail", mock_cls)

        mock_stm.start_sync = AsyncMock(side_effect=RuntimeError("sync error"))

        result = await ConnectorFactory.create_and_start_sync(
            name="test_sync_fail",
            logger=logger,
            data_store_provider=data_store,
            config_service=config_service,
            connector_id="conn-13",
            scope="personal",
            created_by="test-user-id",
        )
        assert result is None
        logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_config_none_defaults_safely(self):
        """If get_config returns None, sync strategy is None and sync proceeds."""
        logger = MagicMock()
        data_store = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)

        mock_connector = AsyncMock()
        mock_connector.init.return_value = True
        mock_connector.run_sync = MagicMock(return_value=MagicMock())

        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=mock_connector)
        ConnectorFactory.register_connector("test_config_none", mock_cls)

        with patch("app.connectors.core.factory.connector_factory.sync_task_manager") as mock_stm:
            mock_stm.start_sync = AsyncMock()
            result = await ConnectorFactory.create_and_start_sync(
                name="test_config_none",
                logger=logger,
                data_store_provider=data_store,
                config_service=config_service,
                connector_id="conn-14",
                scope="personal",
                created_by="test-user-id",
            )
        assert result is mock_connector
