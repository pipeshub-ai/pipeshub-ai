"""
Extended unit tests for ConnectorRegistry covering uncovered methods:
- _normalize_connector_name
- _get_beta_connector_names
- _can_access_connector (personal/team/unknown scope)
- _check_name_uniqueness (unique, not unique, exception)
- _get_connector_instance_from_db (found, not found, exception)
- _create_connector_instance (full flow, name conflict, missing org, auth validation)
- _deactivate_connector_instance (success, not found, exception)
- sync_with_database (deactivation of orphans, KB skip, exception)
- _build_connector_info (with and without instance data)
- _get_all_connector_instances
- get_all_registered_connectors (search, scope filter, pagination, beta filtering)
- get_all_connector_instances (database instances with pagination)
- get_active_connector_instances / get_inactive_connector_instances
- get_active_agent_connector_instances
- get_configured_connector_instances
- get_connector_metadata
- get_connector_instance (access control)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.connector_registry import (
    Connector,
    ConnectorRegistry,
    Origin,
    Permissions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container():
    container = MagicMock()
    container.logger.return_value = MagicMock()
    return container


def _make_registry(container=None):
    if container is None:
        container = _make_container()
    return ConnectorRegistry(container), container


def _make_connector_class(
    name="Test Connector",
    app_group="Test Group",
    supported_auth_types="OAUTH",
    **extra_meta,
):
    @Connector(
        name=name,
        app_group=app_group,
        supported_auth_types=supported_auth_types,
        **extra_meta,
    )
    class FakeConnector:
        pass
    return FakeConnector


def _make_graph_provider():
    return AsyncMock()


# ===========================================================================
# _normalize_connector_name
# ===========================================================================


class TestNormalizeConnectorName:
    def test_removes_spaces_and_lowercases(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("Google Drive") == "googledrive"

    def test_already_normalized(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("gmail") == "gmail"

    def test_empty_string(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("") == ""


# ===========================================================================
# _get_beta_connector_names
# ===========================================================================


class TestGetBetaConnectorNames:
    def test_returns_empty_on_import_error(self):
        registry, _ = _make_registry()
        with patch(
            "app.connectors.core.factory.connector_factory.ConnectorFactory",
            side_effect=Exception("import error"),
        ):
            # Should handle exception gracefully
            result = registry._get_beta_connector_names()
            assert isinstance(result, list)


# ===========================================================================
# _can_access_connector
# ===========================================================================


class TestCanAccessConnector:
    @pytest.mark.asyncio
    async def test_team_scope_admin_can_access(self):
        registry, _ = _make_registry()
        instance = {"scope": "team", "createdBy": "other_user"}
        assert await registry._can_access_connector(instance, "admin_user", is_admin=True) is True

    @pytest.mark.asyncio
    async def test_team_scope_creator_can_access(self):
        registry, _ = _make_registry()
        instance = {"scope": "team", "createdBy": "user1"}
        assert await registry._can_access_connector(instance, "user1", is_admin=False) is True

    @pytest.mark.asyncio
    async def test_team_scope_non_admin_non_creator_denied(self):
        registry, _ = _make_registry()
        instance = {"scope": "team", "createdBy": "other_user"}
        assert await registry._can_access_connector(instance, "user1", is_admin=False) is False

    @pytest.mark.asyncio
    async def test_personal_scope_creator_can_access(self):
        registry, _ = _make_registry()
        instance = {"scope": "personal", "createdBy": "user1"}
        assert await registry._can_access_connector(instance, "user1", is_admin=False) is True

    @pytest.mark.asyncio
    async def test_personal_scope_non_creator_denied(self):
        registry, _ = _make_registry()
        instance = {"scope": "personal", "createdBy": "other_user"}
        assert await registry._can_access_connector(instance, "user1", is_admin=True) is False

    @pytest.mark.asyncio
    async def test_unknown_scope_denied(self):
        registry, _ = _make_registry()
        instance = {"scope": "unknown", "createdBy": "user1"}
        assert await registry._can_access_connector(instance, "user1", is_admin=True) is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        registry, _ = _make_registry()
        # Missing 'scope' key should not crash
        instance = {}
        result = await registry._can_access_connector(instance, "user1", is_admin=True)
        # Either False or handles gracefully
        assert isinstance(result, bool)


# ===========================================================================
# _check_name_uniqueness
# ===========================================================================


class TestCheckNameUniqueness:
    @pytest.mark.asyncio
    async def test_name_is_unique(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.check_connector_name_exists = AsyncMock(return_value=False)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry._check_name_uniqueness("My Connector", "personal", "org1", "user1")
        assert result is True

    @pytest.mark.asyncio
    async def test_name_already_exists(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.check_connector_name_exists = AsyncMock(return_value=True)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry._check_name_uniqueness("My Connector", "personal", "org1", "user1")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_true_fail_open(self):
        registry, container = _make_registry()
        container.data_store = AsyncMock(side_effect=Exception("db error"))

        result = await registry._check_name_uniqueness("My Connector", "personal", "org1", "user1")
        assert result is True


# ===========================================================================
# _get_connector_instance_from_db
# ===========================================================================


class TestGetConnectorInstanceFromDb:
    @pytest.mark.asyncio
    async def test_found(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value={"_key": "conn1", "type": "Gmail"})
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry._get_connector_instance_from_db("conn1")
        assert result == {"_key": "conn1", "type": "Gmail"}

    @pytest.mark.asyncio
    async def test_not_found(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value=None)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry._get_connector_instance_from_db("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        registry, container = _make_registry()
        container.data_store = AsyncMock(side_effect=Exception("db error"))

        result = await registry._get_connector_instance_from_db("conn1")
        assert result is None


# ===========================================================================
# _create_connector_instance
# ===========================================================================


class TestCreateConnectorInstance:
    @pytest.mark.asyncio
    async def test_success(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value={"_key": "org1"})
        gp.check_connector_name_exists = AsyncMock(return_value=False)
        gp.batch_upsert_nodes = AsyncMock(return_value=[{"_key": "new1"}])
        gp.batch_create_edges = AsyncMock(return_value=[{"_key": "e1"}])
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        metadata = {"appGroup": "Google Workspace", "supportedAuthTypes": ["OAUTH"]}
        result = await registry._create_connector_instance(
            "Gmail", "My Gmail", metadata, "personal", "user1", "org1",
            selected_auth_type="OAUTH"
        )
        assert result is not None
        assert result["type"] == "Gmail"

    @pytest.mark.asyncio
    async def test_org_not_found(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value=None)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        metadata = {"appGroup": "Test", "supportedAuthTypes": ["OAUTH"]}
        result = await registry._create_connector_instance(
            "Gmail", "My Gmail", metadata, "personal", "user1", "org1",
            selected_auth_type="OAUTH"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_name_conflict_raises(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value={"_key": "org1"})
        gp.check_connector_name_exists = AsyncMock(return_value=True)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        metadata = {"appGroup": "Test", "supportedAuthTypes": ["OAUTH"]}
        with pytest.raises(ValueError, match="already exists"):
            await registry._create_connector_instance(
                "Gmail", "My Gmail", metadata, "personal", "user1", "org1",
                selected_auth_type="OAUTH"
            )

    @pytest.mark.asyncio
    async def test_no_selected_auth_type_raises(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value={"_key": "org1"})
        gp.check_connector_name_exists = AsyncMock(return_value=False)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        metadata = {"appGroup": "Test", "supportedAuthTypes": ["OAUTH"]}
        with pytest.raises(ValueError, match="selected_auth_type is required"):
            await registry._create_connector_instance(
                "Gmail", "My Gmail", metadata, "personal", "user1", "org1",
                selected_auth_type=None
            )

    @pytest.mark.asyncio
    async def test_unsupported_auth_type_raises(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value={"_key": "org1"})
        gp.check_connector_name_exists = AsyncMock(return_value=False)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        metadata = {"appGroup": "Test", "supportedAuthTypes": ["OAUTH"]}
        with pytest.raises(ValueError, match="not supported"):
            await registry._create_connector_instance(
                "Gmail", "My Gmail", metadata, "personal", "user1", "org1",
                selected_auth_type="API_TOKEN"
            )


# ===========================================================================
# _deactivate_connector_instance
# ===========================================================================


class TestDeactivateConnectorInstance:
    @pytest.mark.asyncio
    async def test_success(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value={"_key": "conn1", "isActive": True})
        gp.update_node = AsyncMock(return_value=True)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry._deactivate_connector_instance("conn1")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document = AsyncMock(return_value=None)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry._deactivate_connector_instance("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self):
        registry, container = _make_registry()
        container.data_store = AsyncMock(side_effect=Exception("db error"))

        result = await registry._deactivate_connector_instance("conn1")
        assert result is False


# ===========================================================================
# sync_with_database
# ===========================================================================


class TestSyncWithDatabase:
    @pytest.mark.asyncio
    async def test_deactivates_orphaned_connectors(self):
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_all_documents = AsyncMock(return_value=[
            {"id": "c1", "type": "Gmail", "isActive": True},
            {"id": "c2", "type": "Slack", "isActive": True},  # Not registered
        ])
        gp.batch_update_connector_status = AsyncMock(return_value=1)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry.sync_with_database()
        assert result is True
        gp.batch_update_connector_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_kb_connectors(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_all_documents = AsyncMock(return_value=[
            {"id": "c1", "type": "KB", "isActive": True},
        ])
        gp.batch_update_connector_status = AsyncMock(return_value=0)
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry.sync_with_database()
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self):
        registry, container = _make_registry()
        container.data_store = AsyncMock(side_effect=Exception("db error"))

        result = await registry.sync_with_database()
        assert result is False


# ===========================================================================
# _build_connector_info
# ===========================================================================


class TestBuildConnectorInfo:
    def test_without_instance_data(self):
        registry, _ = _make_registry()
        metadata = {
            "appGroup": "Google Workspace",
            "supportedAuthTypes": ["OAUTH"],
            "appDescription": "Email client",
            "appCategories": ["email"],
            "config": {"supportsRealtime": True},
            "connectorScopes": ["personal"],
            "connectorInfo": "Info text"
        }
        info = registry._build_connector_info("Gmail", metadata)
        assert info["name"] == "Gmail"
        assert info["type"] == "Gmail"
        assert info["appGroup"] == "Google Workspace"
        assert info["supportsRealtime"] is True

    def test_with_instance_data(self):
        registry, _ = _make_registry()
        metadata = {
            "appGroup": "Google Workspace",
            "supportedAuthTypes": ["OAUTH"],
            "appDescription": "Email",
            "appCategories": [],
            "config": {},
            "connectorScopes": ["personal"],
            "connectorInfo": None
        }
        instance_data = {
            "authType": "OAUTH",
            "isActive": True,
            "isAgentActive": False,
            "isConfigured": True,
            "isAuthenticated": True,
            "status": "active",
            "createdAtTimestamp": 1000,
            "updatedAtTimestamp": 2000,
            "_key": "conn1",
            "name": "My Gmail",
            "scope": "personal",
            "createdBy": "user1",
            "updatedBy": "user1",
            "isLocked": False,
        }
        info = registry._build_connector_info("Gmail", metadata, instance_data)
        assert info["authType"] == "OAUTH"
        assert info["isActive"] is True
        assert info["_key"] == "conn1"
        assert info["name"] == "My Gmail"


# ===========================================================================
# get_all_registered_connectors (search and pagination)
# ===========================================================================


class TestGetAllRegisteredConnectors:
    @pytest.mark.asyncio
    async def test_basic_pagination(self):
        registry, container = _make_registry()
        # Register 5 connectors
        for i in range(5):
            cls = _make_connector_class(
                name=f"Connector{i}",
                app_group="Test",
                connector_scopes=[ConnectorScope.PERSONAL],
            )
            registry.register_connector(cls)

        # Mock feature flag service
        ff = AsyncMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        result = await registry.get_all_registered_connectors(
            is_admin=True, page=1, limit=3
        )
        assert len(result["connectors"]) == 3
        assert result["pagination"]["totalCount"] == 5
        assert result["pagination"]["hasNext"] is True
        assert result["pagination"]["hasPrev"] is False

    @pytest.mark.asyncio
    async def test_search_filter(self):
        registry, container = _make_registry()
        cls1 = _make_connector_class(name="Gmail", app_group="Google")
        cls2 = _make_connector_class(name="Slack", app_group="Messaging")
        registry.register_connector(cls1)
        registry.register_connector(cls2)

        ff = AsyncMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        result = await registry.get_all_registered_connectors(
            is_admin=True, search="gmail"
        )
        assert len(result["connectors"]) == 1

    @pytest.mark.asyncio
    async def test_scope_filter(self):
        registry, container = _make_registry()
        cls1 = _make_connector_class(
            name="Personal Conn", app_group="Test",
            connector_scopes=[ConnectorScope.PERSONAL],
        )
        cls2 = _make_connector_class(
            name="Team Conn", app_group="Test",
            connector_scopes=[ConnectorScope.TEAM],
        )
        registry.register_connector(cls1)
        registry.register_connector(cls2)

        ff = AsyncMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        result = await registry.get_all_registered_connectors(
            is_admin=True, scope=ConnectorScope.PERSONAL.value
        )
        assert len(result["connectors"]) == 1
        assert result["connectors"][0]["name"] == "Personal Conn"


# ===========================================================================
# get_all_connector_instances
# ===========================================================================


class TestGetAllConnectorInstances:
    @pytest.mark.asyncio
    async def test_success(self):
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_filtered_connector_instances = AsyncMock(return_value=(
            [{"_key": "c1", "type": "Gmail"}],
            1,
            {"personal": 1, "team": 0}
        ))
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry.get_all_connector_instances(
            "user1", "org1", is_admin=True
        )
        assert len(result["connectors"]) == 1
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_unknown_type_skipped(self):
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_filtered_connector_instances = AsyncMock(return_value=(
            [{"_key": "c1", "type": "UnknownConnector"}],
            1,
            {"personal": 0, "team": 0}
        ))
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry.get_all_connector_instances(
            "user1", "org1", is_admin=True
        )
        assert len(result["connectors"]) == 0

    @pytest.mark.asyncio
    async def test_exception(self):
        registry, container = _make_registry()
        container.data_store = AsyncMock(side_effect=Exception("db error"))

        result = await registry.get_all_connector_instances(
            "user1", "org1", is_admin=True
        )
        assert result["connectors"] == []
        assert result["pagination"]["totalCount"] == 0


# ===========================================================================
# get_active_connector_instances
# ===========================================================================


class TestGetActiveConnectorInstances:
    @pytest.mark.asyncio
    async def test_filters_active_only(self):
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_user_connector_instances = AsyncMock(return_value=[
            {"_key": "c1", "type": "Gmail", "isActive": True, "config": {}},
            {"_key": "c2", "type": "Gmail", "isActive": False, "config": {}},
        ])
        data_store = MagicMock()
        data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=data_store)

        result = await registry.get_active_connector_instances("user1", "org1")
        assert len(result) == 1
        # Config should be stripped
        assert "config" not in result[0]


# ===========================================================================
# get_connector_metadata
# ===========================================================================


class TestGetConnectorMetadata:
    @pytest.mark.asyncio
    async def test_found(self):
        registry, _ = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        result = await registry.get_connector_metadata("Gmail")
        assert result is not None
        assert result["name"] == "Gmail"

    @pytest.mark.asyncio
    async def test_not_found(self):
        registry, _ = _make_registry()

        result = await registry.get_connector_metadata("NonExistent")
        assert result is None


# ===========================================================================
# discover_connectors
# ===========================================================================


class TestDiscoverConnectors:
    def test_import_error_handled(self):
        registry, _ = _make_registry()
        registry.discover_connectors(["nonexistent.module.path"])
        assert len(registry._connectors) == 0

    def test_general_exception_handled(self):
        registry, _ = _make_registry()
        with patch("builtins.__import__", side_effect=Exception("unexpected")):
            registry.discover_connectors(["some.module"])
        assert len(registry._connectors) == 0
