"""Tests for Confluence Data Center Personal connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, Optional

import pytest

pytestmark = pytest.mark.confluence_datacenter_personal

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.confluence_datacenter_personal.connector import (
    ConfluenceDataCenterPersonalConnector,
)
from app.models.entities import (
    AppUser,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.connectors.core.registry.filters import FilterCollection, FilterOperator, SyncFilterKey


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    """Create mock dependencies for testing."""
    logger = logging.getLogger("test.confluence_personal")
    
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-conf-personal-1"
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    data_entities_processor.get_user_by_user_id = AsyncMock(
        return_value=MagicMock(email="creator@example.com")
    )

    data_store_provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    """Create a test connector instance."""
    logger, dep, dsp, cs = _make_mock_deps()
    return ConfluenceDataCenterPersonalConnector(
        logger, dep, dsp, cs, "conn-conf-personal-1", "personal", "test-user-123"
    )


def _make_mock_response(status=200, data=None):
    """Create a mock HTTP response."""
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


# ===========================================================================
# Tests
# ===========================================================================


class TestConfluencePersonalConnectorInit:
    """Test connector initialization."""

    def test_init_creates_personal_app(self):
        """Test that connector initializes with ConfluenceDataCenterPersonalApp."""
        connector = _make_connector()
        assert connector.app.app_name == Connectors.CONFLUENCE_DATA_CENTER_PERSONAL
        assert connector.connector_id == "conn-conf-personal-1"
        assert connector.scope == "personal"
        assert connector.created_by == "test-user-123"

    @pytest.mark.asyncio
    async def test_init_with_valid_credentials(self):
        """Test connector init with valid API token."""
        connector = _make_connector()
        
        # Mock config service to return credentials
        connector.config_service.get_config.return_value = {
            "baseUrl": "https://confluence.example.com",
            "apiToken": "test-token-123"
        }
        
        with patch(
            "app.connectors.sources.atlassian.confluence_datacenter_personal.connector.ExternalConfluenceClient"
        ) as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_cls.build_from_services = AsyncMock(return_value=mock_client_instance)

            result = await connector.init()
            assert result is True
            assert connector.external_client is not None
            assert connector.data_source is not None


class TestConfluencePersonalConnectorSync:
    """Test connector sync flow."""

    @pytest.mark.asyncio
    async def test_run_sync_creates_connector_group(self):
        """Test that run_sync creates ConnectorGroup permission."""
        connector = _make_connector()
        
        # Mock initialization
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        # Mock ensure_connector_group_permission
        mock_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        connector.ensure_connector_group_permission = AsyncMock(return_value=mock_permission)
        
        # Mock filters
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        
        with patch("app.connectors.sources.atlassian.confluence_datacenter_personal.connector.load_connector_filters", 
                   return_value=(FilterCollection(), FilterCollection())):
            # Mock _sync_spaces to return empty list
            connector._sync_spaces = AsyncMock(return_value=[])
            
            await connector.run_sync()
            
            # Verify ConnectorGroup permission was created
            connector.ensure_connector_group_permission.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_resolves_creator_email(self):
        """Test that run_sync resolves creator email from created_by."""
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        # Mock ensure_connector_group_permission
        mock_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        connector.ensure_connector_group_permission = AsyncMock(return_value=mock_permission)
        
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        
        with patch("app.connectors.sources.atlassian.confluence_datacenter_personal.connector.load_connector_filters",
                   return_value=(FilterCollection(), FilterCollection())):
            connector._sync_spaces = AsyncMock(return_value=[])
            
            await connector.run_sync()
            
            # Verify creator email was resolved
            assert connector.creator_email == "creator@example.com"
            connector.data_entities_processor.get_user_by_user_id.assert_called_once_with("test-user-123")

    @pytest.mark.asyncio
    async def test_run_sync_skips_user_and_group_sync(self):
        """Test that run_sync does NOT call user/group sync methods."""
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        mock_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        connector.ensure_connector_group_permission = AsyncMock(return_value=mock_permission)
        
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        
        with patch("app.connectors.sources.atlassian.confluence_datacenter_personal.connector.load_connector_filters",
                   return_value=(FilterCollection(), FilterCollection())):
            connector._sync_spaces = AsyncMock(return_value=[])
            
            # Verify these methods don't exist (were removed)
            assert not hasattr(connector, '_sync_users')
            assert not hasattr(connector, '_sync_user_groups')
            assert not hasattr(connector, '_fetch_space_permissions')
            assert not hasattr(connector, '_fetch_page_permissions')
            
            await connector.run_sync()


class TestConfluencePersonalSpaceSync:
    """Test space synchronization."""

    @pytest.mark.asyncio
    async def test_sync_spaces_grants_connector_group_permission(self):
        """Test that _sync_spaces grants ConnectorGroup permission to all spaces."""
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        # Mock ConnectorGroup permission
        mock_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        connector._connector_group_permission = mock_permission
        connector.ensure_connector_group_permission = AsyncMock(return_value=mock_permission)
        
        # Mock filters
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        
        # Mock datasource to return test spaces
        mock_datasource = MagicMock()
        mock_spaces_response = _make_mock_response(
            status=HttpStatusCode.SUCCESS.value,
            data={
                "results": [
                    {
                        "id": "space-1",
                        "key": "TEST",
                        "name": "Test Space",
                        "type": "global",
                        "history": {
                            "createdDate": "2024-01-01T00:00:00.000Z",
                            "createdBy": {"displayName": "Admin"}
                        },
                        "_links": {"webui": "/spaces/TEST"}
                    }
                ],
                "_links": {"base": "https://confluence.example.com"}
            }
        )
        mock_datasource.get_spaces_v1 = AsyncMock(return_value=mock_spaces_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_datasource)
        
        # Mock space transform
        mock_space_rg = RecordGroup(
            external_group_id="space-1",
            short_name="TEST",
            name="Test Space",
            connector_id="conn-conf-personal-1",
            connector_name=Connectors.CONFLUENCE_DATA_CENTER_PERSONAL,
            group_type=RecordGroupType.CONFLUENCE_SPACES,
        )
        connector._transform_to_space_record_group = MagicMock(return_value=mock_space_rg)
        
        spaces = await connector._sync_spaces()
        
        # Verify spaces were synced
        assert len(spaces) == 1
        
        # Verify on_new_record_groups was called with ConnectorGroup permission
        connector.data_entities_processor.on_new_record_groups.assert_called_once()
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(call_args) == 1
        record_group, permissions = call_args[0]
        assert record_group.external_group_id == "space-1"
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.GROUP
        assert permissions[0].external_id == "internal-conn-conf-personal-1"

    @pytest.mark.asyncio
    async def test_sync_spaces_respects_space_key_filters(self):
        """Test that _sync_spaces applies space key filters."""
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        mock_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        connector._connector_group_permission = mock_permission
        
        # Set up space keys filter (include only "DEV")
        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.IN
        space_filter.get_value.return_value = ["DEV"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: space_filter if k == SyncFilterKey.SPACE_KEYS else None
        )
        connector.indexing_filters = FilterCollection()
        
        mock_datasource = MagicMock()
        mock_spaces_response = _make_mock_response(
            status=HttpStatusCode.SUCCESS.value,
            data={
                "results": [
                    {
                        "id": "space-dev",
                        "key": "DEV",
                        "name": "Dev Space",
                        "type": "global",
                        "history": {
                            "createdDate": "2024-01-01T00:00:00.000Z",
                            "createdBy": {"displayName": "Admin"}
                        },
                        "_links": {"webui": "/spaces/DEV"}
                    }
                ],
                "_links": {"base": "https://confluence.example.com"}
            }
        )
        mock_datasource.get_spaces_v1 = AsyncMock(return_value=mock_spaces_response)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_datasource)
        
        mock_space_rg = RecordGroup(
            external_group_id="space-dev",
            short_name="DEV",
            name="Dev Space",
            connector_id="conn-conf-personal-1",
            connector_name=Connectors.CONFLUENCE_DATA_CENTER_PERSONAL,
            group_type=RecordGroupType.CONFLUENCE_SPACES,
        )
        connector._transform_to_space_record_group = MagicMock(return_value=mock_space_rg)
        
        spaces = await connector._sync_spaces()
        
        # Verify get_spaces_v1 was called with the filter
        mock_datasource.get_spaces_v1.assert_called_once()
        call_kwargs = mock_datasource.get_spaces_v1.call_args[1]
        assert call_kwargs["keys"] == ["DEV"]


class TestConfluencePersonalPermissions:
    """Test permission handling in personal connector."""

    @pytest.mark.asyncio
    async def test_no_permission_api_calls_during_sync(self):
        """Test that personal connector does NOT call permission APIs."""
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        # Verify permission fetch methods don't exist
        assert not hasattr(connector, '_fetch_space_permissions')
        assert not hasattr(connector, '_fetch_page_permissions')
        assert not hasattr(connector, '_sync_permission_changes_from_audit_log')

    def test_creator_only_access(self):
        """Test that connector uses creator-only access model."""
        connector = _make_connector()
        assert connector.created_by == "test-user-123"
        assert connector.scope == "personal"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
