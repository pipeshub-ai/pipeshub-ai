"""Unit tests for app.connectors.core.base.connector.connector_service.BaseConnector.

Covers the concrete accessor methods (lines 103-116):
- get_app, get_app_group, get_app_name, get_app_group_name, get_connector_id
"""

from unittest.mock import MagicMock

from app.connectors.core.base.connector.connector_service import BaseConnector


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
