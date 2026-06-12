"""Integration tests for Confluence Data Center Personal connector - deep sync flow."""

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

pytestmark = pytest.mark.confluence_datacenter_personal

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.confluence_datacenter_personal.connector import (
    ConfluenceDataCenterPersonalConnector,
)
from app.connectors.core.registry.filters import FilterCollection
from app.models.entities import (
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    """Create mock dependencies."""
    logger = logging.getLogger("test.confluence.personal.deep")
    dep = MagicMock()
    dep.org_id = "org-conf-personal-1"
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_user_by_user_id = AsyncMock(
        return_value=MagicMock(email="creator@example.com")
    )

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    dsp.transaction = MagicMock(return_value=mock_tx)

    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _make_connector():
    """Create test connector."""
    logger, dep, dsp, cs = _make_mock_deps()
    return ConfluenceDataCenterPersonalConnector(
        logger, dep, dsp, cs, "conn-conf-personal-1", "personal", "test-user-123"
    )


def _resp(status=200, data=None):
    """Create mock HTTP response."""
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


def _space_rg(key="DEV", sid="s-1"):
    """Create mock space RecordGroup."""
    return RecordGroup(
        id=str(uuid4()),
        org_id="org-conf-personal-1",
        external_group_id=sid,
        connector_id="conn-conf-personal-1",
        connector_name=Connectors.CONFLUENCE_DATA_CENTER_PERSONAL,
        name=f"Space {key}",
        short_name=key,
        group_type=RecordGroupType.CONFLUENCE_SPACES,
    )


def _page_payload(
    page_id: str = "page-1",
    title: str = "Test Page",
    space_key: str = "DEV",
    body: str = "<p>Test content</p>",
) -> dict[str, Any]:
    return {
        "id": page_id,
        "title": title,
        "type": "page",
        "status": "current",
        "space": {"id": "space-1", "key": space_key},
        "version": {"when": "2024-01-01T00:00:00.000Z", "number": 1},
        "history": {
            "lastUpdated": {"when": "2024-01-01T00:00:00.000Z", "number": 1},
        },
        "body": {"storage": {"value": body}},
        "children": {"attachment": {"results": [], "size": 0}},
        "_links": {"webui": f"/spaces/{space_key}/pages/{page_id}"},
    }


def _configure_content_sync_datasource(
    mock_datasource: MagicMock,
    *,
    pages: list[dict[str, Any]] | None = None,
    sync_operations: list[tuple[str, dict[str, Any]]] | None = None,
) -> None:
    page_results = pages or []

    async def _pages_v1(**kwargs: Any) -> MagicMock:
        if sync_operations is not None:
            sync_operations.append(("get_pages_v1", kwargs))
        return _resp(data={"results": page_results, "_links": {}, "size": len(page_results)})

    async def _blogposts_v1(**kwargs: Any) -> MagicMock:
        if sync_operations is not None:
            sync_operations.append(("get_blogposts_v1", kwargs))
        return _resp(data={"results": [], "_links": {}, "size": 0})

    mock_datasource.get_pages_v1 = AsyncMock(side_effect=_pages_v1)
    mock_datasource.get_blogposts_v1 = AsyncMock(side_effect=_blogposts_v1)


def _patch_load_connector_filters(
    sync_filters: FilterCollection | MagicMock | None = None,
    indexing_filters: FilterCollection | None = None,
):
    return patch(
        "app.connectors.sources.atlassian.confluence_datacenter_personal.connector.load_connector_filters",
        new=AsyncMock(
            return_value=(
                sync_filters or FilterCollection(),
                indexing_filters or FilterCollection(),
            )
        ),
    )


def _prepare_connector_for_run_sync(
    connector: ConfluenceDataCenterPersonalConnector,
    mock_datasource: MagicMock,
    *,
    pages: list[dict[str, Any]] | None = None,
    sync_operations: list[tuple[str, dict[str, Any]]] | None = None,
) -> None:
    _configure_content_sync_datasource(
        mock_datasource,
        pages=pages,
        sync_operations=sync_operations,
    )
    connector._get_fresh_datasource = AsyncMock(return_value=mock_datasource)
    connector._fetch_space_homepage_info = AsyncMock(return_value=(None, None, None))
    connector._fetch_comments_recursive = AsyncMock(return_value=[])
    connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
    connector.pages_sync_point.update_sync_point = AsyncMock()


# ===========================================================================
# run_sync - Full Integration
# ===========================================================================


class TestFullSyncIntegration:
    """Integration tests for full sync without permission APIs."""

    @pytest.mark.asyncio
    async def test_full_sync_flow_no_permission_apis(self):
        """Test complete sync flow: spaces -> pages -> attachments, no permission APIs."""
        connector = _make_connector()
        
        # Mock connector group permission
        mock_group_perm = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        
        # Mock clients
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        # Mock ensure_connector_group_permission (cache on connector like production)
        async def _ensure_group_perm() -> Permission:
            connector._connector_group_permission = mock_group_perm
            return mock_group_perm

        connector.ensure_connector_group_permission = AsyncMock(
            side_effect=_ensure_group_perm
        )
        
        # Track all sync operations
        sync_operations: list[tuple[str, dict[str, Any]]] = []
        
        # Mock datasource
        mock_datasource = MagicMock()
        
        async def _get_spaces_v1(**kwargs: Any) -> MagicMock:
            sync_operations.append(("get_spaces_v1", kwargs))
            return _resp(data={
                "results": [{
                    "id": "space-1",
                    "key": "DEV",
                    "name": "Test Space",
                    "type": "global",
                    "history": {
                        "createdDate": "2024-01-01T00:00:00.000Z"
                    },
                    "_links": {"webui": "/spaces/DEV"}
                }],
                "_links": {"base": "https://confluence.example.com"}
            })

        mock_datasource.get_spaces_v1 = AsyncMock(side_effect=_get_spaces_v1)
        _prepare_connector_for_run_sync(
            connector,
            mock_datasource,
            pages=[_page_payload()],
            sync_operations=sync_operations,
        )
        
        # Mock space transform
        connector._transform_to_space_record_group = MagicMock(return_value=_space_rg("DEV", "space-1"))
        
        with _patch_load_connector_filters():
            await connector.run_sync()
        
        # Verify sync operations executed in correct order
        assert len(sync_operations) > 0
        
        # Verify no permission API calls were made
        permission_api_patterns = [
            "get_space_permissions",
            "get_restrictions",
            "get_auditing_events",
            "permissions",
        ]
        for op_name, op_kwargs in sync_operations:
            assert not any(pattern in op_name for pattern in permission_api_patterns), \
                f"Permission API called: {op_name}"
        
        # Verify ConnectorGroup permission was created
        connector.ensure_connector_group_permission.assert_called_once()
        
        # Verify spaces were synced with ConnectorGroup permission
        connector.data_entities_processor.on_new_record_groups.assert_called()
        space_calls = connector.data_entities_processor.on_new_record_groups.call_args_list
        assert len(space_calls) > 0
        
        # Verify first call has ConnectorGroup permission
        first_call_groups = space_calls[0][0][0]
        assert len(first_call_groups) > 0
        record_group, permissions = first_call_groups[0]
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.GROUP
        assert "internal-" in permissions[0].external_id
        
        # Verify pages were synced
        connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_with_space_filters(self):
        """Test sync with space key filters."""
        connector = _make_connector()
        
        mock_group_perm = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        connector.ensure_connector_group_permission = AsyncMock(return_value=mock_group_perm)
        
        mock_datasource = MagicMock()
        mock_datasource.get_spaces_v1 = AsyncMock(return_value=_resp(data={
            "results": [
                {
                    "id": "space-dev",
                    "key": "DEV",
                    "name": "Dev Space",
                    "type": "global",
                    "history": {"createdDate": "2024-01-01T00:00:00.000Z"},
                    "_links": {"webui": "/spaces/DEV"}
                },
            ],
            "_links": {"base": "https://confluence.example.com"}
        }))
        _prepare_connector_for_run_sync(connector, mock_datasource, pages=[])
        connector._transform_to_space_record_group = MagicMock(return_value=_space_rg("DEV", "space-dev"))
        
        # Set up filter to include only DEV
        from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.IN
        space_filter.get_value.return_value = ["DEV"]

        sync_filters = MagicMock()
        sync_filters.get = MagicMock(
            side_effect=lambda k: space_filter if k == SyncFilterKey.SPACE_KEYS else None
        )

        with _patch_load_connector_filters(sync_filters=sync_filters):
            await connector.run_sync()
        
        # Verify get_spaces_v1 was called with keys=["DEV"]
        mock_datasource.get_spaces_v1.assert_called()
        call_kwargs = mock_datasource.get_spaces_v1.call_args[1]
        assert call_kwargs["keys"] == ["DEV"]


class TestPermissionInheritance:
    """Test that content inherits permissions from parent space."""

    @pytest.mark.asyncio
    async def test_pages_inherit_from_space(self):
        """Test that pages get empty permission list and inherit from space."""
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        
        mock_group_perm = Permission(
            entity_type=EntityType.GROUP,
            external_id="internal-conn-conf-personal-1",
            type=PermissionType.READ
        )
        connector._connector_group_permission = mock_group_perm
        connector.ensure_connector_group_permission = AsyncMock(return_value=mock_group_perm)
        
        saved_records: list[tuple[Any, list[Permission]]] = []
        
        async def mock_on_new_records(records_with_perms: list[tuple[Any, list[Permission]]]) -> None:
            saved_records.extend(records_with_perms)
        
        connector.data_entities_processor.on_new_records = AsyncMock(side_effect=mock_on_new_records)
        
        mock_datasource = MagicMock()
        mock_datasource.get_spaces_v1 = AsyncMock(return_value=_resp(data={
            "results": [{
                "id": "space-1",
                "key": "DEV",
                "name": "Test Space",
                "type": "global",
                "history": {"createdDate": "2024-01-01T00:00:00.000Z"},
                "_links": {"webui": "/spaces/DEV"}
            }],
            "_links": {"base": "https://confluence.example.com"}
        }))
        _prepare_connector_for_run_sync(
            connector,
            mock_datasource,
            pages=[_page_payload(body="<p>Test</p>")],
        )
        connector._transform_to_space_record_group = MagicMock(return_value=_space_rg("DEV", "space-1"))
        
        with _patch_load_connector_filters():
            await connector.run_sync()
        
        # Verify pages were saved with empty permission list (inherit from space)
        assert len(saved_records) > 0
        for record, permissions in saved_records:
            if isinstance(record, WebpageRecord):
                assert permissions == [], \
                    f"Pages should have empty permissions (inherit from space), got: {permissions}"
                assert record.inherit_permissions is True, \
                    "Pages should have inherit_permissions=True"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
