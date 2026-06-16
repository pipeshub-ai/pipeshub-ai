"""Unit tests for app.connectors.core.base.connector.connector_service.BaseConnector.

Covers accessor methods, notify(), and _suppress_notification() backoff dedupe.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.connector.connector_service import (
    INITIAL_NOTIFICATION_BACKOFF,
    MAX_NOTIFICATION_BACKOFF,
    BaseConnector,
)
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
            title="Sync failed",
            message="something failed",
            payload=payload,
        )
        await asyncio.sleep(0)

        mock_svc.publish_notification.assert_awaited_once()
        kwargs = mock_svc.publish_notification.await_args.kwargs
        assert kwargs["recipient_user_ids"] == ["test-user-id"]
        assert kwargs["org_id"] == "org-xyz"
        assert kwargs["type"] is NotificationType.CONNECTOR_SYNC_ERROR
        assert kwargs["payload"]["connectorId"] == "conn-1"
        assert kwargs["message"] == "something failed"
        assert kwargs["severity"] is NotificationSeverity.ERROR
        assert kwargs["payload"]["errorCode"] == "E1"

    @pytest.mark.asyncio
    async def test_notify_error_no_op_without_service(self):
        c = TestBaseConnectorAccessors()._make_connector()
        c._notification_service = None
        await c.notify(
            type=NotificationType.CONNECTOR_SYNC_ERROR,
            severity=NotificationSeverity.ERROR,
            title="x",
            message="x",
            payload={},
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
            title="x",
            message="x",
            payload={},
        )
        await asyncio.sleep(0)
        mock_svc.publish_notification.assert_not_called()


@pytest.fixture(autouse=True)
def clear_notification_cache():
    BaseConnector._notification_cache.clear()
    yield
    BaseConnector._notification_cache.clear()


class TestBaseConnectorSuppressNotification:
    TITLE = "Website not accessible"
    MESSAGE = "Website https://example.com is not accessible."

    def _key(self, connector_id: str = "conn-1") -> str:
        return f"{connector_id}:{self.TITLE}:{self.MESSAGE}"

    def test_info_and_success_never_suppressed(self):
        c = TestBaseConnectorAccessors()._make_connector()
        for severity in (NotificationSeverity.INFO, NotificationSeverity.SUCCESS):
            assert c._suppress_notification(self.TITLE, self.MESSAGE, severity) is False
        assert BaseConnector._notification_cache == {}

    def test_first_error_not_suppressed_and_seeds_cache(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 1_000_000

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(
                self.TITLE, self.MESSAGE, NotificationSeverity.ERROR
            ) is False

        key = self._key()
        assert BaseConnector._notification_cache[key] == (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

    def test_duplicate_within_backoff_is_suppressed(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 1_000_000
        key = self._key()
        BaseConnector._notification_cache[key] = (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now + 1,
        ):
            assert c._suppress_notification(
                self.TITLE, self.MESSAGE, NotificationSeverity.ERROR
            ) is True

    def test_after_backoff_window_doubles_backoff(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 10_000_000
        key = self._key()
        next_allowed = now - 1_000
        BaseConnector._notification_cache[key] = (next_allowed, INITIAL_NOTIFICATION_BACKOFF)

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(
                self.TITLE, self.MESSAGE, NotificationSeverity.ERROR
            ) is False

        expected_backoff = INITIAL_NOTIFICATION_BACKOFF * 2
        assert BaseConnector._notification_cache[key] == (
            now + expected_backoff,
            expected_backoff,
        )

    def test_after_max_backoff_resets_to_initial(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 100_000_000
        key = self._key()
        next_allowed = now - MAX_NOTIFICATION_BACKOFF - 1
        BaseConnector._notification_cache[key] = (next_allowed, MAX_NOTIFICATION_BACKOFF)

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(
                self.TITLE, self.MESSAGE, NotificationSeverity.ERROR
            ) is False

        assert BaseConnector._notification_cache[key] == (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

    def test_cache_shared_across_connector_instances(self):
        first = TestBaseConnectorAccessors()._make_connector()
        second = TestBaseConnectorAccessors()._make_connector()
        now = 2_000_000

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert first._suppress_notification(
                self.TITLE, self.MESSAGE, NotificationSeverity.ERROR
            ) is False
            assert second._suppress_notification(
                self.TITLE, self.MESSAGE, NotificationSeverity.ERROR
            ) is True

    def test_different_messages_are_independent(self):
        c = TestBaseConnectorAccessors()._make_connector()
        now = 3_000_000

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now,
        ):
            assert c._suppress_notification(
                self.TITLE, self.MESSAGE, NotificationSeverity.ERROR
            ) is False
            assert c._suppress_notification(
                self.TITLE, "other message", NotificationSeverity.ERROR
            ) is False

        assert len(BaseConnector._notification_cache) == 2

    @pytest.mark.asyncio
    async def test_notify_skips_publish_when_suppressed(self):
        c = TestBaseConnectorAccessors()._make_connector()
        mock_svc = MagicMock()
        mock_svc.publish_notification = AsyncMock()
        c._notification_service = mock_svc
        c.data_entities_processor.org_id = "org-xyz"
        now = 4_000_000
        key = self._key()
        BaseConnector._notification_cache[key] = (
            now + INITIAL_NOTIFICATION_BACKOFF,
            INITIAL_NOTIFICATION_BACKOFF,
        )

        with patch(
            "app.connectors.core.base.connector.connector_service.get_epoch_timestamp_in_ms",
            return_value=now + 1,
        ):
            await c.notify(
                type=NotificationType.CONNECTOR_NOT_ACCESSIBLE,
                severity=NotificationSeverity.ERROR,
                title=self.TITLE,
                message=self.MESSAGE,
            )
            await asyncio.sleep(0)

        mock_svc.publish_notification.assert_not_called()
