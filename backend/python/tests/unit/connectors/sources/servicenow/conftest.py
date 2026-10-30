"""Fixtures for the ServiceNow connector tests: a connector wired to mocked platform services.

Shared rows, fakes and wiring helpers are in ``servicenow_fakes.py``.
"""

import logging
from collections.abc import Iterator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from servicenow_fakes import FROZEN_NOW, MemorySyncPoint, make_data_store_provider

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.sources.servicenow.servicenow import connector as connector_module
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector


@pytest.fixture(autouse=True)
def fresh_notice_state() -> Iterator[None]:
    """The notice back-off cache and the persistent notices sent are process-wide, as on a server: one
    test's notices must not suppress or count toward the next one's."""
    BaseConnector._notification_cache.clear()
    ServiceNowConnector._persistent_noticed.clear()
    yield
    BaseConnector._notification_cache.clear()
    ServiceNowConnector._persistent_noticed.clear()


@pytest.fixture()
def mock_logger() -> logging.Logger:
    return logging.getLogger("test.servicenow")


@pytest.fixture()
def mock_data_entities_processor() -> object:
    class _Proc:
        org_id = "org-sn-1"

    proc = _Proc()
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.get_all_app_users = AsyncMock(return_value=[])
    proc.batch_upsert_user_groups = AsyncMock()
    proc.create_user_group_membership = AsyncMock()
    proc.get_user_by_source_id = AsyncMock(return_value=None)
    proc.get_record_by_external_id = AsyncMock(return_value=None)
    proc.full_sync_running = MagicMock(return_value=False)
    proc.reindex_existing_records = AsyncMock()
    proc.on_record_group_deleted = AsyncMock(return_value=True)
    proc.delete_record_by_external_id = AsyncMock()
    proc.get_records_by_parent = AsyncMock(return_value=[])
    proc.get_records_by_record_type = AsyncMock(return_value=[])
    proc.get_record_group_by_external_id = AsyncMock(return_value=MagicMock())  # stored
    return proc


@pytest.fixture()
def mock_data_store_provider() -> MagicMock:
    return make_data_store_provider()


@pytest.fixture()
def mock_config_service() -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "oauthConfigId": "oauth-sn-1",
        },
        "credentials": {
            "access_token": "sn-access-token",
            "refresh_token": "sn-refresh-token",
        },
    })
    return svc


@pytest.fixture()
def servicenow_connector(mock_logger, mock_data_entities_processor,
                          mock_data_store_provider, mock_config_service) -> ServiceNowConnector:
    with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
        connector = ServiceNowConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="sn-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
    connector.connector_name = Connectors.SERVICENOW
    # The knowledge base and category steps keep their last listed scope here.
    connector.category_sync_point = MemorySyncPoint()
    return connector


@pytest.fixture()
def frozen_now(monkeypatch) -> datetime:
    """Freezes the connector's clock (its module's datetime and _utc_now) at FROZEN_NOW, for tests that
    build dates from today."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:
            return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW.replace(tzinfo=None)

    monkeypatch.setattr(connector_module, "datetime", _Frozen)
    monkeypatch.setattr(ServiceNowConnector, "_utc_now", staticmethod(lambda: FROZEN_NOW.strftime("%Y-%m-%d %H:%M:%S")))
    return FROZEN_NOW
