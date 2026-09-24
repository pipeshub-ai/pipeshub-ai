"""Extended tests for ConnectorFactory.create_connector covering uncovered lines.

Targets:
- Lines 225-226: org_id applied to data_entities_processor
- Lines 239-241: notification_service set on connector when not None
- Line 239: connector returned as None from inner create_connector
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.factory.connector_factory import ConnectorFactory


_ORIGINAL_REGISTRY = None


@pytest.fixture(autouse=True)
def _restore_registry():
    """Save and restore the class-level _connector_registry between tests."""
    global _ORIGINAL_REGISTRY
    _ORIGINAL_REGISTRY = ConnectorFactory._connector_registry.copy()
    yield
    ConnectorFactory._connector_registry = _ORIGINAL_REGISTRY


def _make_mock_processor_cls():
    """Return a mock processor class and the mock instance it produces."""
    mock_processor = MagicMock()
    mock_processor.initialize = AsyncMock()
    mock_processor.org_id = None
    mock_processor_cls = MagicMock(return_value=mock_processor)
    return mock_processor_cls, mock_processor


# ===========================================================================
# org_id applied to data_entities_processor (lines 225-226)
# ===========================================================================


class TestCreateConnectorOrgId:
    """Verify org_id is written to data_entities_processor."""

    @pytest.mark.asyncio
    async def test_org_id_set_on_processor(self):
        """When org_id is truthy, it is assigned to the processor."""
        expected_connector = MagicMock()
        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=expected_connector)
        ConnectorFactory.register_connector("test_orgid_set", mock_cls)

        mock_processor_cls, mock_processor = _make_mock_processor_cls()

        result = await ConnectorFactory.create_connector(
            name="test_orgid_set",
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="c1",
            scope="personal",
            created_by="user1",
            org_id="org-42",
            data_entities_processor_cls=mock_processor_cls,
        )

        assert result is expected_connector
        assert mock_processor.org_id == "org-42"

    @pytest.mark.asyncio
    async def test_org_id_not_set_when_falsy(self):
        """When org_id is empty string, processor.org_id is not changed."""
        expected_connector = MagicMock()
        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=expected_connector)
        ConnectorFactory.register_connector("test_orgid_falsy", mock_cls)

        mock_processor_cls, mock_processor = _make_mock_processor_cls()

        await ConnectorFactory.create_connector(
            name="test_orgid_falsy",
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="c2",
            scope="personal",
            created_by="user1",
            org_id="",
            data_entities_processor_cls=mock_processor_cls,
        )

        assert mock_processor.org_id is None

    @pytest.mark.asyncio
    async def test_org_id_not_set_when_none(self):
        """When org_id is None (default), processor.org_id is not changed."""
        expected_connector = MagicMock()
        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=expected_connector)
        ConnectorFactory.register_connector("test_orgid_none", mock_cls)

        mock_processor_cls, mock_processor = _make_mock_processor_cls()

        await ConnectorFactory.create_connector(
            name="test_orgid_none",
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="c3",
            scope="personal",
            created_by="user1",
            data_entities_processor_cls=mock_processor_cls,
        )

        assert mock_processor.org_id is None


# ===========================================================================
# notification_service assignment (lines 239-241)
# ===========================================================================


class TestCreateConnectorNotificationService:
    """Verify notification_service is applied to the connector."""

    @pytest.mark.asyncio
    async def test_notification_service_set_on_connector(self):
        """notification_service kwarg is set on the connector instance."""
        expected_connector = MagicMock(spec=[])
        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=expected_connector)
        ConnectorFactory.register_connector("test_notif_set", mock_cls)

        mock_processor_cls, _ = _make_mock_processor_cls()
        notif_svc = MagicMock()

        result = await ConnectorFactory.create_connector(
            name="test_notif_set",
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="c4",
            scope="personal",
            created_by="user1",
            data_entities_processor_cls=mock_processor_cls,
            notification_service=notif_svc,
        )

        assert result is expected_connector
        assert result._notification_service is notif_svc

    @pytest.mark.asyncio
    async def test_notification_service_not_set_when_absent(self):
        """Without notification_service kwarg, _notification_service is not touched."""
        expected_connector = MagicMock(spec=[])
        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=expected_connector)
        ConnectorFactory.register_connector("test_notif_absent", mock_cls)

        mock_processor_cls, _ = _make_mock_processor_cls()

        result = await ConnectorFactory.create_connector(
            name="test_notif_absent",
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="c5",
            scope="personal",
            created_by="user1",
            data_entities_processor_cls=mock_processor_cls,
        )

        assert result is expected_connector
        assert not hasattr(result, "_notification_service")

    @pytest.mark.asyncio
    async def test_notification_service_skipped_when_connector_none(self):
        """When inner create_connector returns None, notification is not set."""
        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=None)
        ConnectorFactory.register_connector("test_notif_skip", mock_cls)

        mock_processor_cls, _ = _make_mock_processor_cls()
        notif_svc = MagicMock()

        result = await ConnectorFactory.create_connector(
            name="test_notif_skip",
            logger=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="c6",
            scope="personal",
            created_by="user1",
            data_entities_processor_cls=mock_processor_cls,
            notification_service=notif_svc,
        )

        assert result is None


# ===========================================================================
# Inner create_connector returns None (line 239)
# ===========================================================================


class TestCreateConnectorInnerNone:
    """Verify behavior when the inner create_connector returns None."""

    @pytest.mark.asyncio
    async def test_inner_returns_none(self):
        """Factory returns None without raising when inner returns None."""
        mock_cls = MagicMock()
        mock_cls.create_connector = AsyncMock(return_value=None)
        ConnectorFactory.register_connector("test_inner_none", mock_cls)

        mock_processor_cls, _ = _make_mock_processor_cls()
        logger = MagicMock()

        result = await ConnectorFactory.create_connector(
            name="test_inner_none",
            logger=logger,
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="c7",
            scope="personal",
            created_by="user1",
            data_entities_processor_cls=mock_processor_cls,
        )

        assert result is None
        logger.info.assert_called()
