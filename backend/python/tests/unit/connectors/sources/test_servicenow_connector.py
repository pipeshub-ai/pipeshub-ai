"""Tests for ServiceNow connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.servicenow.servicenow.connector import (
    ORGANIZATIONAL_ENTITIES,
    ServiceNowConnector,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.servicenow")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-sn-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
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
                          mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
        connector = ServiceNowConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="sn-conn-1",
        )
    return connector


# ===========================================================================
# Constants
# ===========================================================================

class TestOrganizationalEntities:
    def test_company_config(self):
        assert "company" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["company"]["table"] == "core_company"

    def test_department_config(self):
        assert "department" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["department"]["table"] == "cmn_department"

    def test_location_config(self):
        assert "location" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["location"]["table"] == "cmn_location"

    def test_cost_center_config(self):
        assert "cost_center" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["cost_center"]["table"] == "cmn_cost_center"

    def test_all_have_required_fields(self):
        for entity_type, config in ORGANIZATIONAL_ENTITIES.items():
            assert "table" in config, f"{entity_type} missing 'table'"
            assert "fields" in config, f"{entity_type} missing 'fields'"
            assert "prefix" in config, f"{entity_type} missing 'prefix'"
            assert "sync_point_key" in config, f"{entity_type} missing 'sync_point_key'"


# ===========================================================================
# ServiceNowConnector
# ===========================================================================

class TestServiceNowConnectorInit:
    def test_constructor(self, servicenow_connector):
        assert servicenow_connector.connector_id == "sn-conn-1"
        assert servicenow_connector.servicenow_client is None
        assert servicenow_connector.servicenow_datasource is None
        assert servicenow_connector.instance_url is None

    def test_sync_points_created(self, servicenow_connector):
        assert servicenow_connector.user_sync_point is not None
        assert servicenow_connector.group_sync_point is not None
        assert servicenow_connector.kb_sync_point is not None
        assert servicenow_connector.article_sync_point is not None

    def test_org_entity_sync_points(self, servicenow_connector):
        assert "company" in servicenow_connector.org_entity_sync_points
        assert "department" in servicenow_connector.org_entity_sync_points
        assert "location" in servicenow_connector.org_entity_sync_points
        assert "cost_center" in servicenow_connector.org_entity_sync_points

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowRESTClientViaOAuthAuthorizationCode")
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource")
    async def test_init_success(self, mock_ds_cls, mock_client_cls, mock_fetch_oauth,
                                servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "sn-client-id",
                "clientSecret": "sn-client-secret",
                "instanceUrl": "https://dev12345.service-now.com",
                "redirectUri": "http://localhost/callback",
            }
        }
        mock_client_cls.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()

        result = await servicenow_connector.init()
        assert result is True
        assert servicenow_connector.instance_url == "https://dev12345.service-now.com"

    async def test_init_fails_no_config(self, servicenow_connector):
        servicenow_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await servicenow_connector.init()
        assert result is False

    async def test_init_fails_no_oauth_config_id(self, servicenow_connector):
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {},
            "credentials": {},
        })
        result = await servicenow_connector.init()
        assert result is False

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_oauth_not_found(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = None
        result = await servicenow_connector.init()
        assert result is False

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_incomplete_config(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "id",
                # Missing clientSecret, instanceUrl, redirectUri
            }
        }
        result = await servicenow_connector.init()
        assert result is False

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_no_access_token(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "id",
                "clientSecret": "secret",
                "instanceUrl": "https://sn.example.com",
                "redirectUri": "http://localhost/callback",
            }
        }
        # Override config to have no access_token
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-sn-1"},
            "credentials": {},
        })
        result = await servicenow_connector.init()
        assert result is False
