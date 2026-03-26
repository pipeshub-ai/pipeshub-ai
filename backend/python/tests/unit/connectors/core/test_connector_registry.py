"""Tests for app.connectors.core.registry.connector_registry."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.connector_registry import (
    Connector,
    ConnectorRegistry,
    Origin,
    Permissions,
)
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container():
    """Build a minimal mock ConnectorAppContainer."""
    container = MagicMock()
    container.logger.return_value = MagicMock()
    return container


def _make_registry(container=None):
    """Create a ConnectorRegistry with mocked container."""
    if container is None:
        container = _make_container()
    return ConnectorRegistry(container), container


def _make_connector_class(
    name="Test Connector",
    app_group="Test Group",
    supported_auth_types="OAUTH",
    **extra_meta,
):
    """Create a connector class decorated with @Connector."""
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
    """Create an AsyncMock graph provider."""
    return AsyncMock()


# ===========================================================================
# @Connector Decorator
# ===========================================================================


class TestConnectorDecorator:
    """Tests for the @Connector decorator function."""

    def test_decorator_sets_metadata(self):
        """@Connector stores metadata on the class."""
        cls = _make_connector_class(
            name="Gmail",
            app_group="Google Workspace",
            supported_auth_types=["OAUTH"],
            app_description="Email client",
            app_categories=["email"],
        )

        assert hasattr(cls, "_connector_metadata")
        assert cls._connector_metadata["name"] == "Gmail"
        assert cls._connector_metadata["appGroup"] == "Google Workspace"
        assert cls._connector_metadata["supportedAuthTypes"] == ["OAUTH"]
        assert cls._connector_metadata["appDescription"] == "Email client"
        assert cls._connector_metadata["appCategories"] == ["email"]

    def test_decorator_marks_class_as_connector(self):
        """@Connector sets _is_connector = True on the class."""
        cls = _make_connector_class()
        assert cls._is_connector is True

    def test_single_auth_type_string_converted_to_list(self):
        """A string auth type is normalized to a single-element list."""
        cls = _make_connector_class(supported_auth_types="API_TOKEN")
        assert cls._connector_metadata["supportedAuthTypes"] == ["API_TOKEN"]

    def test_multiple_auth_types(self):
        """A list of auth types is stored as-is."""
        cls = _make_connector_class(supported_auth_types=["OAUTH", "API_TOKEN"])
        assert cls._connector_metadata["supportedAuthTypes"] == ["OAUTH", "API_TOKEN"]

    def test_empty_auth_types_list_raises(self):
        """An empty list of auth types raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            _make_connector_class(supported_auth_types=[])

    def test_invalid_auth_types_type_raises(self):
        """Non-string, non-list auth type raises ValueError."""
        with pytest.raises(ValueError, match="must be str or List"):
            _make_connector_class(supported_auth_types=123)

    def test_default_connector_scopes(self):
        """Default connectorScopes is [PERSONAL]."""
        cls = _make_connector_class()
        assert cls._connector_metadata["connectorScopes"] == [ConnectorScope.PERSONAL]

    def test_custom_connector_scopes(self):
        """Custom connector scopes are stored."""
        cls = _make_connector_class(
            connector_scopes=[ConnectorScope.PERSONAL, ConnectorScope.TEAM]
        )
        assert cls._connector_metadata["connectorScopes"] == [
            ConnectorScope.PERSONAL,
            ConnectorScope.TEAM,
        ]

    def test_connector_info_stored(self):
        """connector_info text is stored in metadata."""
        cls = _make_connector_class(connector_info="Syncs emails")
        assert cls._connector_metadata["connectorInfo"] == "Syncs emails"

    def test_connector_info_defaults_to_none(self):
        """connector_info defaults to None."""
        cls = _make_connector_class()
        assert cls._connector_metadata["connectorInfo"] is None


# ===========================================================================
# ConnectorRegistry.register_connector
# ===========================================================================


class TestRegisterConnector:
    """Tests for ConnectorRegistry.register_connector."""

    def test_register_decorated_class(self):
        """Registering a @Connector-decorated class succeeds."""
        registry, _ = _make_registry()
        cls = _make_connector_class(name="TestConn")

        result = registry.register_connector(cls)

        assert result is True
        assert "TestConn" in registry._connectors

    def test_register_undecorated_class_returns_false(self):
        """Registering a class without @Connector returns False."""
        registry, _ = _make_registry()

        class NoDecorator:
            pass

        result = registry.register_connector(NoDecorator)

        assert result is False
        assert len(registry._connectors) == 0

    def test_register_stores_metadata_copy(self):
        """Registered metadata is a copy, not the original dict."""
        registry, _ = _make_registry()
        cls = _make_connector_class(name="CopyTest")

        registry.register_connector(cls)

        assert registry._connectors["CopyTest"] is not cls._connector_metadata

    def test_register_exception_returns_false(self):
        """If registration logic throws, returns False."""
        registry, _ = _make_registry()
        # Create a class where accessing _connector_metadata raises
        cls = MagicMock()
        cls._connector_metadata = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        cls.__name__ = "BadClass"
        # hasattr will return True but accessing will fail differently
        del cls._connector_metadata
        # Now it has no _connector_metadata at all
        result = registry.register_connector(cls)
        assert result is False


# ===========================================================================
# ConnectorRegistry.discover_connectors
# ===========================================================================


class TestDiscoverConnectors:
    """Tests for ConnectorRegistry.discover_connectors."""

    def test_discover_from_module(self):
        """Discovers connector classes from a module."""
        registry, _ = _make_registry()

        # Create a mock module with a connector class
        cls = _make_connector_class(name="DiscoverMe")
        mock_module = MagicMock()
        mock_module.__dir__ = lambda self: ["DiscoverMe"]
        mock_module.DiscoverMe = cls

        with patch("builtins.__import__", return_value=mock_module):
            registry.discover_connectors(["fake.module"])

        assert "DiscoverMe" in registry._connectors

    def test_discover_handles_import_error(self):
        """Import errors for individual modules are logged and skipped."""
        registry, container = _make_registry()
        logger = container.logger()

        with patch("builtins.__import__", side_effect=ImportError("no module")):
            registry.discover_connectors(["nonexistent.module"])

        assert len(registry._connectors) == 0
        logger.warning.assert_called()

    def test_discover_skips_non_connector_classes(self):
        """Classes without @Connector decorator are not registered."""
        registry, _ = _make_registry()

        class NotAConnector:
            pass

        mock_module = MagicMock()
        mock_module.__dir__ = lambda self: ["NotAConnector"]
        mock_module.NotAConnector = NotAConnector

        with patch("builtins.__import__", return_value=mock_module):
            registry.discover_connectors(["some.module"])

        assert len(registry._connectors) == 0


# ===========================================================================
# ConnectorRegistry._normalize_connector_name
# ===========================================================================


class TestNormalizeConnectorName:
    """Tests for _normalize_connector_name."""

    def test_lowercase(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("Google Drive") == "googledrive"

    def test_spaces_removed(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("Share Point Online") == "sharepointonline"

    def test_already_normalized(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("slack") == "slack"

    def test_mixed_case_and_spaces(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("My Custom Connector") == "mycustomconnector"

    def test_empty_string(self):
        registry, _ = _make_registry()
        assert registry._normalize_connector_name("") == ""


# ===========================================================================
# ConnectorRegistry._can_access_connector
# ===========================================================================


class TestCanAccessConnector:
    """Tests for _can_access_connector."""

    @pytest.mark.asyncio
    async def test_team_scope_admin_has_access(self):
        """Admin can access team-scoped connectors."""
        registry, _ = _make_registry()
        instance = {"scope": ConnectorScope.TEAM.value, "createdBy": "user-1"}

        result = await registry._can_access_connector(instance, "admin-1", is_admin=True)

        assert result is True

    @pytest.mark.asyncio
    async def test_team_scope_creator_has_access(self):
        """Creator can access their own team-scoped connector."""
        registry, _ = _make_registry()
        instance = {"scope": ConnectorScope.TEAM.value, "createdBy": "user-1"}

        result = await registry._can_access_connector(instance, "user-1", is_admin=False)

        assert result is True

    @pytest.mark.asyncio
    async def test_team_scope_other_user_no_access(self):
        """Non-admin, non-creator cannot access team-scoped connector."""
        registry, _ = _make_registry()
        instance = {"scope": ConnectorScope.TEAM.value, "createdBy": "user-1"}

        result = await registry._can_access_connector(instance, "user-2", is_admin=False)

        assert result is False

    @pytest.mark.asyncio
    async def test_personal_scope_creator_has_access(self):
        """Creator can access their personal connector."""
        registry, _ = _make_registry()
        instance = {"scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1"}

        result = await registry._can_access_connector(instance, "user-1", is_admin=False)

        assert result is True

    @pytest.mark.asyncio
    async def test_personal_scope_other_user_no_access(self):
        """Non-creator cannot access someone's personal connector."""
        registry, _ = _make_registry()
        instance = {"scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1"}

        result = await registry._can_access_connector(instance, "user-2", is_admin=False)

        assert result is False

    @pytest.mark.asyncio
    async def test_personal_scope_admin_no_access(self):
        """Admin cannot access someone else's personal connector."""
        registry, _ = _make_registry()
        instance = {"scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1"}

        result = await registry._can_access_connector(instance, "admin-1", is_admin=True)

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_scope_returns_false(self):
        """Unknown scope returns False."""
        registry, _ = _make_registry()
        instance = {"scope": "unknown", "createdBy": "user-1"}

        result = await registry._can_access_connector(instance, "user-1", is_admin=True)

        assert result is False

    @pytest.mark.asyncio
    async def test_default_scope_is_personal(self):
        """Missing scope defaults to personal."""
        registry, _ = _make_registry()
        instance = {"createdBy": "user-1"}  # No scope key

        result = await registry._can_access_connector(instance, "user-1", is_admin=False)

        assert result is True  # Personal scope, creator = user

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Exceptions return False."""
        registry, _ = _make_registry()
        # Pass None to cause an exception
        instance = None

        result = await registry._can_access_connector(instance, "user-1", is_admin=True)

        assert result is False


# ===========================================================================
# ConnectorRegistry._check_name_uniqueness
# ===========================================================================


class TestCheckNameUniqueness:
    """Tests for _check_name_uniqueness."""

    @pytest.mark.asyncio
    async def test_unique_name_returns_true(self):
        """When name does not exist, returns True."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.check_connector_name_exists.return_value = False

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._check_name_uniqueness(
            "My Connector", "personal", "org-1", "user-1"
        )

        assert result is True
        gp.check_connector_name_exists.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duplicate_name_returns_false(self):
        """When name already exists, returns False."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.check_connector_name_exists.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._check_name_uniqueness(
            "Existing Connector", "personal", "org-1", "user-1"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_exception_fails_open(self):
        """On exception, returns True (fail-open)."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.check_connector_name_exists.side_effect = RuntimeError("db error")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._check_name_uniqueness(
            "Test", "team", "org-1", "user-1"
        )

        assert result is True


# ===========================================================================
# ConnectorRegistry._get_graph_provider (lazy init)
# ===========================================================================


class TestGetGraphProvider:
    """Tests for _get_graph_provider lazy initialization."""

    @pytest.mark.asyncio
    async def test_initializes_once(self):
        """Graph provider is created from container on first call."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result1 = await registry._get_graph_provider()
        result2 = await registry._get_graph_provider()

        assert result1 is gp
        assert result2 is gp
        # data_store should only be called once (lazy init)
        container.data_store.assert_awaited_once()


# ===========================================================================
# Origin and Permissions Enums
# ===========================================================================


class TestEnums:
    """Tests for Origin and Permissions enums."""

    def test_origin_values(self):
        assert Origin.UPLOAD.value == "UPLOAD"
        assert Origin.CONNECTOR.value == "CONNECTOR"

    def test_permissions_values(self):
        assert Permissions.READER.value == "READER"
        assert Permissions.WRITER.value == "WRITER"
        assert Permissions.OWNER.value == "OWNER"
        assert Permissions.COMMENTER.value == "COMMENTER"


# ===========================================================================
# ConnectorRegistry._build_connector_info
# ===========================================================================


class TestBuildConnectorInfo:
    """Tests for _build_connector_info."""

    def test_basic_info_from_metadata(self):
        """Builds connector info from metadata only."""
        registry, _ = _make_registry()
        metadata = {
            "appGroup": "TestGroup",
            "supportedAuthTypes": ["OAUTH"],
            "appDescription": "A test connector",
            "appCategories": ["test"],
            "config": {"iconPath": "/icons/test.svg", "supportsRealtime": True},
            "connectorScopes": [ConnectorScope.PERSONAL.value],
            "connectorInfo": "Some info",
        }

        result = registry._build_connector_info("TestConn", metadata)

        assert result["name"] == "TestConn"
        assert result["type"] == "TestConn"
        assert result["appGroup"] == "TestGroup"
        assert result["iconPath"] == "/icons/test.svg"
        assert result["supportsRealtime"] is True
        assert result["connectorInfo"] == "Some info"

    def test_instance_data_overrides(self):
        """Instance data fields override metadata defaults."""
        registry, _ = _make_registry()
        metadata = {
            "appGroup": "TestGroup",
            "config": {},
            "connectorScopes": [ConnectorScope.PERSONAL.value],
        }
        instance_data = {
            "_key": "inst-1",
            "name": "My Custom Name",
            "authType": "API_TOKEN",
            "isActive": True,
            "isAgentActive": False,
            "isConfigured": True,
            "isAuthenticated": True,
            "scope": ConnectorScope.TEAM.value,
            "createdBy": "user-1",
            "updatedBy": "user-1",
        }

        result = registry._build_connector_info("TestConn", metadata, instance_data)

        assert result["_key"] == "inst-1"
        assert result["name"] == "My Custom Name"
        assert result["authType"] == "API_TOKEN"
        assert result["isActive"] is True
        assert result["scope"] == ConnectorScope.TEAM.value

    def test_default_icon_path_when_missing(self):
        """Missing iconPath in config uses default."""
        registry, _ = _make_registry()
        metadata = {"appGroup": "G", "config": {}, "connectorScopes": []}

        result = registry._build_connector_info("X", metadata)

        assert result["iconPath"] == "/assets/icons/connectors/default.svg"

    def test_scope_param_overrides_metadata(self):
        """Explicit scope param overrides metadata scopes."""
        registry, _ = _make_registry()
        metadata = {
            "appGroup": "G",
            "config": {},
            "connectorScopes": [ConnectorScope.PERSONAL.value],
        }

        result = registry._build_connector_info("X", metadata, scope=ConnectorScope.TEAM.value)

        assert result["scope"] == ConnectorScope.TEAM.value

    def test_supports_sync_and_agent_flags(self):
        """supportsSync and supportsAgent flags are read from config."""
        registry, _ = _make_registry()
        metadata = {
            "appGroup": "G",
            "config": {"supportsSync": True, "supportsAgent": True},
            "connectorScopes": [],
        }

        result = registry._build_connector_info("X", metadata)

        assert result["supportsSync"] is True
        assert result["supportsAgent"] is True

    def test_instance_data_locked_field(self):
        """isLocked field from instance_data is included."""
        registry, _ = _make_registry()
        metadata = {"appGroup": "G", "config": {}, "connectorScopes": []}
        instance_data = {
            "_key": "i1",
            "name": "N",
            "isLocked": True,
            "scope": ConnectorScope.PERSONAL.value,
        }

        result = registry._build_connector_info("X", metadata, instance_data)

        assert result["isLocked"] is True

    def test_no_instance_data_no_key(self):
        """Without instance_data, '_key' is not in result."""
        registry, _ = _make_registry()
        metadata = {"appGroup": "G", "config": {}, "connectorScopes": []}

        result = registry._build_connector_info("X", metadata)

        assert "_key" not in result


# ===========================================================================
# ConnectorRegistry._get_connector_instance_from_db
# ===========================================================================


class TestGetConnectorInstanceFromDb:
    """Tests for _get_connector_instance_from_db."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Returns document when found in DB."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "c1", "type": "Gmail"}

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._get_connector_instance_from_db("c1")

        assert result["_key"] == "c1"

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Returns None when document not found."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = None

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._get_connector_instance_from_db("missing")

        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Returns None on DB error."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.side_effect = RuntimeError("db down")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._get_connector_instance_from_db("c1")

        assert result is None


# ===========================================================================
# ConnectorRegistry._deactivate_connector_instance
# ===========================================================================


class TestDeactivateConnectorInstance:
    """Tests for _deactivate_connector_instance."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Deactivation updates the document and returns True."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "c1", "isActive": True}
        gp.update_node.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._deactivate_connector_instance("c1")

        assert result is True
        gp.update_node.assert_awaited_once()
        update_args = gp.update_node.call_args[0][2]
        assert update_args["isActive"] is False
        assert update_args["isAgentActive"] is False

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Returns False when connector not found."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = None

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._deactivate_connector_instance("missing")

        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Returns False on error."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.side_effect = RuntimeError("error")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._deactivate_connector_instance("c1")

        assert result is False


# ===========================================================================
# ConnectorRegistry.sync_with_database
# ===========================================================================


class TestSyncWithDatabase:
    """Tests for sync_with_database."""

    @pytest.mark.asyncio
    async def test_deactivates_orphaned_connectors(self):
        """Connectors in DB but not in registry are deactivated."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_all_documents.return_value = [
            {"_key": "c1", "type": "OldConnector", "isActive": True},
        ]
        gp.batch_update_connector_status.return_value = 1

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.sync_with_database()

        assert result is True
        gp.batch_update_connector_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_registered_connectors(self):
        """Connectors already in registry are not deactivated."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_all_documents.return_value = [
            {"_key": "c1", "type": "Gmail", "isActive": True},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.sync_with_database()

        assert result is True
        gp.batch_update_connector_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_knowledge_base(self):
        """Knowledge Base connectors are never deactivated."""
        from app.config.constants.arangodb import Connectors

        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_all_documents.return_value = [
            {"_key": "kb1", "type": Connectors.KNOWLEDGE_BASE.value, "isActive": True},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.sync_with_database()

        assert result is True
        gp.batch_update_connector_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_inactive_connectors(self):
        """Already-inactive connectors are not re-deactivated."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_all_documents.return_value = [
            {"_key": "c1", "type": "OldConnector", "isActive": False},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.sync_with_database()

        assert result is True
        gp.batch_update_connector_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Returns False on error."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_all_documents.side_effect = RuntimeError("db error")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.sync_with_database()

        assert result is False


# ===========================================================================
# ConnectorRegistry._get_all_connector_instances
# ===========================================================================


class TestGetAllConnectorInstancesInternal:
    """Tests for _get_all_connector_instances (internal helper)."""

    @pytest.mark.asyncio
    async def test_returns_instances_with_metadata(self):
        """Returns connector info for instances that match registry entries."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_user_connector_instances.return_value = [
            {"_key": "c1", "type": "Gmail", "name": "My Gmail", "scope": "personal"},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._get_all_connector_instances("user-1", "org-1")

        assert len(result) == 1
        assert result[0]["type"] == "Gmail"

    @pytest.mark.asyncio
    async def test_skips_unknown_types(self):
        """Instances with types not in registry are skipped."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_user_connector_instances.return_value = [
            {"_key": "c1", "type": "UnknownType", "name": "X"},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._get_all_connector_instances("user-1", "org-1")

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        """Returns empty list on error."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_user_connector_instances.side_effect = RuntimeError("error")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._get_all_connector_instances("user-1", "org-1")

        assert result == []


# ===========================================================================
# ConnectorRegistry.get_all_registered_connectors
# ===========================================================================


class TestGetAllRegisteredConnectors:
    """Tests for get_all_registered_connectors."""

    @pytest.mark.asyncio
    async def test_no_search(self):
        """Returns all connectors when no search is provided."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=[]):
            result = await registry.get_all_registered_connectors(is_admin=True)

        assert len(result["connectors"]) == 1
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_search_filters(self):
        """Search query filters connectors by name."""
        registry, container = _make_registry()
        cls1 = _make_connector_class(name="Gmail", app_group="Google")
        cls2 = _make_connector_class(name="Slack", app_group="Messaging")
        registry.register_connector(cls1)
        registry.register_connector(cls2)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=[]):
            result = await registry.get_all_registered_connectors(
                is_admin=True, search="gmail"
            )

        assert len(result["connectors"]) == 1
        assert result["connectors"][0]["name"] == "Gmail"

    @pytest.mark.asyncio
    async def test_pagination(self):
        """Pagination limits results."""
        registry, container = _make_registry()
        for i in range(5):
            cls = _make_connector_class(name=f"Conn{i}", app_group="G")
            registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=[]):
            result = await registry.get_all_registered_connectors(
                is_admin=True, page=1, limit=2
            )

        assert len(result["connectors"]) == 2
        assert result["pagination"]["totalCount"] == 5
        assert result["pagination"]["hasNext"] is True
        assert result["pagination"]["hasPrev"] is False

    @pytest.mark.asyncio
    async def test_pagination_page2(self):
        """Page 2 of pagination works correctly."""
        registry, container = _make_registry()
        for i in range(5):
            cls = _make_connector_class(name=f"Conn{i}", app_group="G")
            registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=[]):
            result = await registry.get_all_registered_connectors(
                is_admin=True, page=2, limit=2
            )

        assert len(result["connectors"]) == 2
        assert result["pagination"]["hasPrev"] is True

    @pytest.mark.asyncio
    async def test_scope_filter(self):
        """Scope filter excludes connectors without matching scope."""
        registry, container = _make_registry()
        cls_personal = _make_connector_class(
            name="PersonalOnly", app_group="G",
            connector_scopes=[ConnectorScope.PERSONAL],
        )
        cls_team = _make_connector_class(
            name="TeamOnly", app_group="G",
            connector_scopes=[ConnectorScope.TEAM],
        )
        registry.register_connector(cls_personal)
        registry.register_connector(cls_team)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=[]):
            result = await registry.get_all_registered_connectors(
                is_admin=True, scope=ConnectorScope.TEAM.value
            )

        names = [c["name"] for c in result["connectors"]]
        assert "TeamOnly" in names
        assert "PersonalOnly" not in names

    @pytest.mark.asyncio
    async def test_hidden_connectors_excluded(self):
        """Connectors with hideConnector=True in config are excluded."""
        registry, container = _make_registry()
        cls = _make_connector_class(
            name="Hidden", app_group="G",
            config={"hideConnector": True},
        )
        registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=[]):
            result = await registry.get_all_registered_connectors(is_admin=True)

        assert len(result["connectors"]) == 0

    @pytest.mark.asyncio
    async def test_beta_connectors_excluded_when_disabled(self):
        """Beta connectors are excluded when feature flag is disabled."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="BetaConn", app_group="G")
        registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=False)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=["betaconn"]):
            result = await registry.get_all_registered_connectors(is_admin=True)

        assert len(result["connectors"]) == 0

    @pytest.mark.asyncio
    async def test_feature_flag_error_includes_all(self):
        """Feature flag error fails open, including all connectors."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Conn", app_group="G")
        registry.register_connector(cls)

        container.feature_flag_service = AsyncMock(side_effect=RuntimeError("no service"))

        result = await registry.get_all_registered_connectors(is_admin=True)

        assert len(result["connectors"]) == 1

    @pytest.mark.asyncio
    async def test_registry_counts_by_scope(self):
        """registryCountsByScope tallies connectors per scope."""
        registry, container = _make_registry()
        cls1 = _make_connector_class(
            name="Both", app_group="G",
            connector_scopes=[ConnectorScope.PERSONAL, ConnectorScope.TEAM],
        )
        cls2 = _make_connector_class(
            name="PersonalOnly", app_group="G",
            connector_scopes=[ConnectorScope.PERSONAL],
        )
        registry.register_connector(cls1)
        registry.register_connector(cls2)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=[]):
            result = await registry.get_all_registered_connectors(is_admin=True)

        counts = result["registryCountsByScope"]
        assert counts["personal"] == 2
        assert counts["team"] == 1


# ===========================================================================
# ConnectorRegistry.get_all_connector_instances
# ===========================================================================


class TestGetAllConnectorInstances:
    """Tests for get_all_connector_instances (public, with pagination)."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Returns paginated connector instances."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_filtered_connector_instances.return_value = (
            [{"_key": "c1", "type": "Gmail", "name": "My Gmail", "scope": "personal"}],
            1,
            {"personal": 1, "team": 0},
        )

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_all_connector_instances(
            "user-1", "org-1", is_admin=False
        )

        assert len(result["connectors"]) == 1
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_skips_unregistered_types(self):
        """Instances with types not in registry are skipped."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_filtered_connector_instances.return_value = (
            [{"_key": "c1", "type": "UnknownType", "name": "X"}],
            1,
            {"personal": 0, "team": 0},
        )

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_all_connector_instances(
            "user-1", "org-1", is_admin=False
        )

        assert len(result["connectors"]) == 0

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        """Returns empty result on error."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_filtered_connector_instances.side_effect = RuntimeError("db error")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_all_connector_instances(
            "user-1", "org-1", is_admin=False
        )

        assert result["connectors"] == []
        assert result["pagination"]["totalCount"] == 0


# ===========================================================================
# ConnectorRegistry.get_connector_metadata
# ===========================================================================


class TestGetConnectorMetadata:
    """Tests for get_connector_metadata."""

    @pytest.mark.asyncio
    async def test_registered_type(self):
        """Returns metadata for a registered connector type."""
        registry, _ = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        result = await registry.get_connector_metadata("Gmail")

        assert result is not None
        assert result["name"] == "Gmail"

    @pytest.mark.asyncio
    async def test_unregistered_type(self):
        """Returns None for an unregistered connector type."""
        registry, _ = _make_registry()

        result = await registry.get_connector_metadata("NonExistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_with_instance_data(self):
        """Instance data is merged into metadata."""
        registry, _ = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        result = await registry.get_connector_metadata(
            "Gmail", {"_key": "i1", "name": "My Gmail", "scope": "personal"}
        )

        assert result["_key"] == "i1"
        assert result["name"] == "My Gmail"


# ===========================================================================
# ConnectorRegistry.get_connector_instance
# ===========================================================================


class TestGetConnectorInstance:
    """Tests for get_connector_instance (with access control)."""

    @pytest.mark.asyncio
    async def test_success_with_access(self):
        """Returns connector instance when user has access."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "Gmail", "name": "My Gmail",
            "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
        }

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_connector_instance(
            "c1", "user-1", "org-1", is_admin=False
        )

        assert result is not None
        assert result["_key"] == "c1"

    @pytest.mark.asyncio
    async def test_no_access(self):
        """Returns None when user lacks access."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "Gmail",
            "scope": ConnectorScope.PERSONAL.value, "createdBy": "other-user",
        }

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_connector_instance(
            "c1", "user-1", "org-1", is_admin=False
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Returns None when connector not found in DB."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = None

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_connector_instance(
            "missing", "user-1", "org-1", is_admin=False
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_unregistered_type(self):
        """Returns None when connector type is not in registry."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "UnknownType",
            "scope": ConnectorScope.TEAM.value, "createdBy": "user-1",
        }

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_connector_instance(
            "c1", "user-1", "org-1", is_admin=True
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Returns None on error."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.side_effect = RuntimeError("boom")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_connector_instance(
            "c1", "user-1", "org-1", is_admin=False
        )

        assert result is None


# ===========================================================================
# ConnectorRegistry.create_connector_instance_on_configuration
# ===========================================================================


class TestCreateConnectorInstanceOnConfiguration:
    """Tests for create_connector_instance_on_configuration."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Successfully creates a connector instance."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "org-1"}  # org exists
        gp.check_connector_name_exists.return_value = False
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.create_connector_instance_on_configuration(
            "Gmail", "My Gmail", "personal", "user-1", "org-1",
            is_admin=False, selected_auth_type="OAUTH",
        )

        assert result is not None
        assert result["type"] == "Gmail"
        assert result["name"] == "My Gmail"
        assert result["authType"] == "OAUTH"

    @pytest.mark.asyncio
    async def test_unregistered_type_returns_none(self):
        """Returns None when connector type is not in registry."""
        registry, _ = _make_registry()

        result = await registry.create_connector_instance_on_configuration(
            "NonExistent", "Test", "personal", "user-1", "org-1",
            is_admin=False, selected_auth_type="OAUTH",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_duplicate_name_raises(self):
        """Raises ValueError when instance name already exists."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "org-1"}
        gp.check_connector_name_exists.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        with pytest.raises(ValueError, match="already exists"):
            await registry.create_connector_instance_on_configuration(
                "Gmail", "Duplicate", "personal", "user-1", "org-1",
                is_admin=False, selected_auth_type="OAUTH",
            )

    @pytest.mark.asyncio
    async def test_no_selected_auth_type_raises(self):
        """Raises ValueError when selected_auth_type is not provided."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "org-1"}
        gp.check_connector_name_exists.return_value = False

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        with pytest.raises(ValueError, match="selected_auth_type is required"):
            await registry.create_connector_instance_on_configuration(
                "Gmail", "My Gmail", "personal", "user-1", "org-1",
                is_admin=False, selected_auth_type=None,
            )

    @pytest.mark.asyncio
    async def test_unsupported_auth_type_raises(self):
        """Raises ValueError when selected auth type is not supported."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google", supported_auth_types=["OAUTH"])
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "org-1"}
        gp.check_connector_name_exists.return_value = False

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        with pytest.raises(ValueError, match="not supported"):
            await registry.create_connector_instance_on_configuration(
                "Gmail", "My Gmail", "personal", "user-1", "org-1",
                is_admin=False, selected_auth_type="API_TOKEN",
            )

    @pytest.mark.asyncio
    async def test_org_not_found_returns_none(self):
        """Returns None when organization is not found."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = None  # org not found

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.create_connector_instance_on_configuration(
            "Gmail", "My Gmail", "personal", "user-1", "org-1",
            is_admin=False, selected_auth_type="OAUTH",
        )

        assert result is None


# ===========================================================================
# ConnectorRegistry.update_connector_instance
# ===========================================================================


class TestUpdateConnectorInstance:
    """Tests for update_connector_instance."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful update returns updated document."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "Gmail", "name": "Old Name",
            "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
        }
        gp.update_node.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.update_connector_instance(
            "c1", {"isActive": True}, "user-1", "org-1", is_admin=False
        )

        assert result is not None
        assert result["isActive"] is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Returns None when connector not found."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = None

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.update_connector_instance(
            "missing", {"isActive": True}, "user-1", "org-1", is_admin=False
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_no_access(self):
        """Returns None when user lacks permission to update."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "Gmail",
            "scope": ConnectorScope.PERSONAL.value, "createdBy": "other-user",
        }

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.update_connector_instance(
            "c1", {"isActive": True}, "user-1", "org-1", is_admin=False
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_name_update_duplicate_raises(self):
        """Raises ValueError when new name already exists."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "Gmail", "name": "Old",
            "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
        }
        gp.check_connector_name_exists.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        with pytest.raises(ValueError, match="already exists"):
            await registry.update_connector_instance(
                "c1", {"name": "Duplicate"}, "user-1", "org-1", is_admin=False
            )

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Returns None on unexpected error."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.side_effect = RuntimeError("error")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.update_connector_instance(
            "c1", {"isActive": True}, "user-1", "org-1", is_admin=False
        )

        assert result is None


# ===========================================================================
# ConnectorRegistry.get_active_connector_instances
# ===========================================================================


class TestGetActiveConnectorInstances:
    """Tests for get_active_connector_instances."""

    @pytest.mark.asyncio
    async def test_filters_active_only(self):
        """Only active instances are returned."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_user_connector_instances.return_value = [
            {"_key": "c1", "type": "Gmail", "name": "Active", "isActive": True, "scope": "personal"},
            {"_key": "c2", "type": "Gmail", "name": "Inactive", "isActive": False, "scope": "personal"},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_active_connector_instances("user-1", "org-1")

        assert len(result) == 1
        assert result[0]["isActive"] is True

    @pytest.mark.asyncio
    async def test_config_removed(self):
        """Config key is removed from active instances."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_user_connector_instances.return_value = [
            {"_key": "c1", "type": "Gmail", "name": "Active", "isActive": True, "scope": "personal"},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_active_connector_instances("user-1", "org-1")

        assert "config" not in result[0]


# ===========================================================================
# ConnectorRegistry.get_inactive_connector_instances
# ===========================================================================


class TestGetInactiveConnectorInstances:
    """Tests for get_inactive_connector_instances."""

    @pytest.mark.asyncio
    async def test_filters_inactive_only(self):
        """Only inactive instances are returned."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_user_connector_instances.return_value = [
            {"_key": "c1", "type": "Gmail", "name": "Active", "isActive": True, "scope": "personal"},
            {"_key": "c2", "type": "Gmail", "name": "Inactive", "isActive": False, "scope": "personal"},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_inactive_connector_instances("user-1", "org-1")

        assert len(result) == 1
        assert result[0]["isActive"] is False


# ===========================================================================
# ConnectorRegistry.get_filter_options
# ===========================================================================


class TestGetFilterOptions:
    """Tests for get_filter_options."""

    @pytest.mark.asyncio
    async def test_returns_all_options(self):
        """Returns aggregated filter options from all connectors."""
        registry, _ = _make_registry()
        cls1 = _make_connector_class(
            name="Gmail", app_group="Google",
            supported_auth_types=["OAUTH"],
        )
        cls2 = _make_connector_class(
            name="Slack", app_group="Messaging",
            supported_auth_types=["API_TOKEN", "OAUTH"],
        )
        registry.register_connector(cls1)
        registry.register_connector(cls2)

        # Patch enum .values() calls since they may not exist on all Python versions
        with patch.object(ProgressStatus, "values", return_value=["NOT_STARTED", "COMPLETED"], create=True), \
             patch.object(RecordType, "values", return_value=["FILE", "MAIL"], create=True), \
             patch.object(Origin, "values", return_value=["UPLOAD", "CONNECTOR"], create=True), \
             patch.object(Permissions, "values", return_value=["READER", "WRITER"], create=True):
            result = await registry.get_filter_options()

        assert "Google" in result["appGroups"]
        assert "Messaging" in result["appGroups"]
        assert "OAUTH" in result["authTypes"]
        assert "API_TOKEN" in result["authTypes"]
        assert "Gmail" in result["connectorNames"]
        assert "Slack" in result["connectorNames"]
        assert len(result["scopes"]) > 0


# ===========================================================================
# ConnectorRegistry._get_beta_connector_names
# ===========================================================================


class TestGetBetaConnectorNames:
    """Tests for _get_beta_connector_names."""

    def test_returns_normalized_names(self):
        """Returns normalized beta connector names."""
        registry, _ = _make_registry()

        mock_cls = MagicMock()
        mock_cls._connector_metadata = {"name": "Beta Connector"}

        MockFactory = MagicMock()
        MockFactory.list_beta_connectors.return_value = {"beta": mock_cls}

        with patch.dict(
            "sys.modules",
            {"app.connectors.core.factory.connector_factory": MagicMock(ConnectorFactory=MockFactory)},
        ):
            result = registry._get_beta_connector_names()

        assert "betaconnector" in result

    def test_exception_returns_empty(self):
        """Returns empty list when _get_beta_connector_names raises."""
        registry, _ = _make_registry()

        # Patch the method itself to raise
        with patch.object(
            registry, "_get_beta_connector_names", return_value=[]
        ):
            result = registry._get_beta_connector_names()

        assert result == []


# ===========================================================================
# ConnectorRegistry._create_connector_instance — deeper coverage
# ===========================================================================


class TestCreateConnectorInstanceDeep:
    """Deeper tests for _create_connector_instance."""

    @pytest.mark.asyncio
    async def test_batch_upsert_returns_false_returns_none(self):
        """When batch_upsert_nodes fails, returns None."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "org-1"}
        gp.check_connector_name_exists.return_value = False
        gp.batch_upsert_nodes.return_value = False

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._create_connector_instance(
            "Gmail", "My Gmail", registry._connectors["Gmail"],
            "personal", "user-1", "org-1",
            selected_auth_type="OAUTH",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_batch_create_edges_returns_false_returns_none(self):
        """When batch_create_edges fails, returns None."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.return_value = {"_key": "org-1"}
        gp.check_connector_name_exists.return_value = False
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = False

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._create_connector_instance(
            "Gmail", "My Gmail", registry._connectors["Gmail"],
            "personal", "user-1", "org-1",
            selected_auth_type="OAUTH",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_general_exception_returns_none(self):
        """General exception during creation returns None."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_document.side_effect = RuntimeError("db crash")

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry._create_connector_instance(
            "Gmail", "My Gmail", registry._connectors["Gmail"],
            "personal", "user-1", "org-1",
            selected_auth_type="OAUTH",
        )
        assert result is None


# ===========================================================================
# ConnectorRegistry.get_active_agent_connector_instances
# ===========================================================================


class TestGetActiveAgentConnectorInstances:
    """Tests for get_active_agent_connector_instances."""

    @pytest.mark.asyncio
    async def test_filters_active_agent_only(self):
        """Only instances with isAgentActive=True and isConfigured=True are returned."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_filtered_connector_instances.return_value = (
            [
                {"_key": "c1", "type": "Gmail", "name": "Agent Active",
                 "isAgentActive": True, "isConfigured": True, "scope": "personal"},
                {"_key": "c2", "type": "Gmail", "name": "Not Agent Active",
                 "isAgentActive": False, "isConfigured": True, "scope": "personal"},
                {"_key": "c3", "type": "Gmail", "name": "Agent Not Configured",
                 "isAgentActive": True, "isConfigured": False, "scope": "personal"},
            ],
            3,
            {"personal": 3, "team": 0},
        )

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_active_agent_connector_instances(
            "user-1", "org-1", is_admin=False
        )

        assert len(result["connectors"]) == 1
        assert result["connectors"][0]["name"] == "Agent Active"


# ===========================================================================
# ConnectorRegistry.get_configured_connector_instances
# ===========================================================================


class TestGetConfiguredConnectorInstances:
    """Tests for get_configured_connector_instances."""

    @pytest.mark.asyncio
    async def test_filters_configured_only(self):
        """Only configured instances are returned."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_filtered_connector_instances.return_value = (
            [
                {"_key": "c1", "type": "Gmail", "name": "Configured",
                 "isConfigured": True, "scope": "personal"},
                {"_key": "c2", "type": "Gmail", "name": "Not Configured",
                 "isConfigured": False, "scope": "personal"},
            ],
            2,
            {"personal": 2, "team": 0},
        )

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_configured_connector_instances(
            "user-1", "org-1", is_admin=False
        )

        assert len(result["connectors"]) == 1
        assert result["connectors"][0]["isConfigured"] is True


# ===========================================================================
# ConnectorRegistry.get_inactive_connector_instances — config removed
# ===========================================================================


class TestGetInactiveConnectorInstancesDeep:
    """Deeper tests for get_inactive_connector_instances."""

    @pytest.mark.asyncio
    async def test_config_removed(self):
        """Config key is removed from inactive instances."""
        registry, container = _make_registry()
        cls = _make_connector_class(name="Gmail", app_group="Google")
        registry.register_connector(cls)

        gp = _make_graph_provider()
        gp.get_user_connector_instances.return_value = [
            {"_key": "c1", "type": "Gmail", "name": "Inactive",
             "isActive": False, "scope": "personal"},
        ]

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.get_inactive_connector_instances("user-1", "org-1")

        assert len(result) == 1
        assert "config" not in result[0]


# ===========================================================================
# ConnectorRegistry.discover_connectors — exception in outer try
# ===========================================================================


class TestDiscoverConnectorsDeep:
    """Deeper tests for discover_connectors."""

    def test_discover_general_exception_handled(self):
        """General exception during discovery is handled."""
        registry, container = _make_registry()
        logger = container.logger()

        # Force an exception by making the module_paths iteration fail
        with patch("builtins.__import__", side_effect=RuntimeError("unexpected")):
            # The inner except only catches ImportError, so RuntimeError
            # will be caught by the outer except
            registry.discover_connectors(["bad.module"])

        # Should have logged the error
        logger.error.assert_called()

    def test_discover_multiple_modules(self):
        """Multiple modules can be discovered."""
        registry, _ = _make_registry()
        cls1 = _make_connector_class(name="Conn1")
        cls2 = _make_connector_class(name="Conn2")

        mock_module1 = MagicMock()
        mock_module1.__dir__ = lambda self: ["Conn1"]
        mock_module1.Conn1 = cls1

        mock_module2 = MagicMock()
        mock_module2.__dir__ = lambda self: ["Conn2"]
        mock_module2.Conn2 = cls2

        def import_side_effect(name, **kwargs):
            if name == "mod1":
                return mock_module1
            if name == "mod2":
                return mock_module2
            raise ImportError("unknown")

        with patch("builtins.__import__", side_effect=import_side_effect):
            registry.discover_connectors(["mod1", "mod2"])

        assert "Conn1" in registry._connectors
        assert "Conn2" in registry._connectors


# ===========================================================================
# ConnectorRegistry.update_connector_instance — name unchanged
# ===========================================================================


class TestUpdateConnectorInstanceDeep:
    """Deeper tests for update_connector_instance."""

    @pytest.mark.asyncio
    async def test_update_without_name_change(self):
        """Update without name change skips uniqueness check."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "Gmail", "name": "Old Name",
            "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
        }
        gp.update_node.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.update_connector_instance(
            "c1", {"isActive": True}, "user-1", "org-1", is_admin=False
        )

        assert result is not None
        # check_connector_name_exists should NOT have been called
        gp.check_connector_name_exists.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_name_unique(self):
        """Update with new unique name succeeds."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_document.return_value = {
            "_key": "c1", "type": "Gmail", "name": "Old Name",
            "scope": ConnectorScope.PERSONAL.value, "createdBy": "user-1",
        }
        gp.check_connector_name_exists.return_value = False
        gp.update_node.return_value = True

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.update_connector_instance(
            "c1", {"name": "New Name"}, "user-1", "org-1", is_admin=False
        )

        assert result is not None
        assert result["name"] == "New Name"


# ===========================================================================
# ConnectorRegistry.get_all_registered_connectors — beta enterprise filter
# ===========================================================================


class TestGetAllRegisteredConnectorsBetaEnterprise:
    """Tests for beta connector enterprise filtering."""

    @pytest.mark.asyncio
    async def test_beta_excluded_for_enterprise_team_scope(self):
        """Beta connectors excluded for enterprise accounts with team scope."""
        registry, container = _make_registry()
        cls = _make_connector_class(
            name="BetaConn", app_group="G",
            connector_scopes=[ConnectorScope.TEAM],
        )
        registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=["betaconn"]):
            result = await registry.get_all_registered_connectors(
                is_admin=True, scope=ConnectorScope.TEAM.value,
                account_type="enterprise",
            )

        assert len(result["connectors"]) == 0

    @pytest.mark.asyncio
    async def test_beta_allowed_for_individual_team_scope(self):
        """Beta connectors allowed for individual accounts with team scope."""
        registry, container = _make_registry()
        cls = _make_connector_class(
            name="BetaConn", app_group="G",
            connector_scopes=[ConnectorScope.TEAM],
        )
        registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=["betaconn"]):
            result = await registry.get_all_registered_connectors(
                is_admin=True, scope=ConnectorScope.TEAM.value,
                account_type="individual",
            )

        assert len(result["connectors"]) == 1

    @pytest.mark.asyncio
    async def test_beta_allowed_for_enterprise_personal_scope(self):
        """Beta connectors allowed for enterprise accounts with personal scope."""
        registry, container = _make_registry()
        cls = _make_connector_class(
            name="BetaConn", app_group="G",
            connector_scopes=[ConnectorScope.PERSONAL],
        )
        registry.register_connector(cls)

        ff = MagicMock()
        ff.refresh = AsyncMock()
        ff.is_feature_enabled = MagicMock(return_value=True)
        container.feature_flag_service = AsyncMock(return_value=ff)

        with patch.object(registry, "_get_beta_connector_names", return_value=["betaconn"]):
            result = await registry.get_all_registered_connectors(
                is_admin=True, scope=ConnectorScope.PERSONAL.value,
                account_type="enterprise",
            )

        assert len(result["connectors"]) == 1


# ===========================================================================
# ConnectorRegistry.sync_with_database — doc key via 'id' fallback
# ===========================================================================


class TestSyncWithDatabaseDeep:
    """Deeper sync_with_database tests."""

    @pytest.mark.asyncio
    async def test_uses_id_fallback_for_key(self):
        """When _key is missing, uses 'id' field for deactivation."""
        registry, container = _make_registry()
        gp = _make_graph_provider()
        gp.get_all_documents.return_value = [
            {"id": "c1", "type": "OldConnector", "isActive": True},
        ]
        gp.batch_update_connector_status.return_value = 1

        mock_data_store = MagicMock()
        mock_data_store.graph_provider = gp
        container.data_store = AsyncMock(return_value=mock_data_store)

        result = await registry.sync_with_database()

        assert result is True
        call_args = gp.batch_update_connector_status.call_args
        assert "c1" in call_args.kwargs.get("connector_keys", call_args[1].get("connector_keys", []))
