"""Unit tests for app.connectors.core.base.connector.connector_service.BaseConnector.

Covers the concrete accessor methods (lines 103-116):
- get_app, get_app_group, get_app_name, get_app_group_name, get_connector_id
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.services.notification.types import NotificationSeverity, NotificationType

# ---------------------------------------------------------------------------
# Concrete subclass (BaseConnector is ABC)
# ---------------------------------------------------------------------------

class ConcreteConnector(BaseConnector):
    async def init(self):
        return True
    def test_connection_and_access(self):
        return True
    def get_signed_url(self, record):
        return None
    def stream_record(self, record, user_id=None, convertTo=None):
        return None
    def run_sync(self):
        pass
    def run_incremental_sync(self):
        pass
    def handle_webhook_notification(self, notification):
        pass
    async def cleanup(self):
        pass
    async def reindex_records(self, record_results):
        pass
    @classmethod
    async def create_connector(cls, logger, data_store_provider, config_service, connector_id):
        return None
    async def get_filter_options(self, filter_key, page=1, limit=20, search=None, cursor=None):
        raise NotImplementedError


class TestBaseConnectorAccessors:
    """Tests for the concrete accessor methods on BaseConnector."""

    def _make_connector(self):
        app = MagicMock()
        app.get_app_name.return_value = "googledrive"
        app.get_app_group.return_value = "google"
        app.get_app_group_name.return_value = "Google Workspace"
        logger = MagicMock()
        dep = MagicMock()
        cs = MagicMock()
        return ConcreteConnector(
            app=app, logger=logger, data_entities_processor=dep,
            data_store_provider=dep, config_service=cs, connector_id="conn-1",
            scope="team", created_by="test-user-id",
        )

    def test_get_app(self):
        c = self._make_connector()
        assert c.get_app() is c.app

    def test_get_app_group(self):
        c = self._make_connector()
        result = c.get_app_group()
        c.app.get_app_group.assert_called_once()
        assert result == "google"

    def test_get_app_name(self):
        c = self._make_connector()
        assert c.get_app_name() == "googledrive"

    def test_get_app_group_name(self):
        c = self._make_connector()
        assert c.get_app_group_name() == "Google Workspace"

    def test_get_connector_id(self):
        c = self._make_connector()
        assert c.get_connector_id() == "conn-1"


class TestBaseConnectorNotifyError:
    @pytest.mark.asyncio
    async def test_notify_error_schedules_publish_error(self):
        c = TestBaseConnectorAccessors()._make_connector()
        mock_svc = MagicMock()
        mock_svc.publish_notification = AsyncMock()
        c._notification_service = mock_svc
        c.data_entities_processor.org_id = "org-xyz"

        payload = {
            "title": "Sync failed",
            "message": "something failed",
            "connectorId": "conn-1",
            "errorCode": "E1",
        }
        await c.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            payload=payload,
        )
        await asyncio.sleep(0)

        mock_svc.publish_notification.assert_awaited_once()
        kwargs = mock_svc.publish_notification.await_args.kwargs
        assert kwargs["user_id"] == "test-user-id"
        assert kwargs["org_id"] == "org-xyz"
        assert kwargs["type"] is NotificationType.CONNECTOR_SYNC_ERROR
        assert kwargs["payload"]["connectorId"] == "conn-1"
        assert kwargs["payload"]["message"] == "something failed"
        assert kwargs["severity"] is NotificationSeverity.ERROR
        assert kwargs["payload"]["errorCode"] == "E1"

    @pytest.mark.asyncio
    async def test_notify_error_no_op_without_service(self):
        c = TestBaseConnectorAccessors()._make_connector()
        c._notification_service = None
        await c.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            payload={"title": "x", "message": "x"},
        )
        # no crash

    @pytest.mark.asyncio
    async def test_notify_error_no_op_without_created_by(self):
        app = MagicMock()
        app.get_app_name.return_value = "googledrive"
        app.get_app_group.return_value = "google"
        app.get_app_group_name.return_value = "Google Workspace"
        logger = MagicMock()
        dep = MagicMock()
        cs = MagicMock()
        c = ConcreteConnector(
            app=app,
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=dep,
            config_service=cs,
            connector_id="conn-1",
            scope="team",
            created_by="",
        )
        mock_svc = MagicMock()
        mock_svc.publish_notification = AsyncMock()
        c._notification_service = mock_svc
        await c.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            payload={"title": "x", "message": "x"},
        )
        await asyncio.sleep(0)
        mock_svc.publish_notification.assert_not_called()
