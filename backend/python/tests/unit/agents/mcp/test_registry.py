"""Unit tests for app.agents.mcp.registry.MCPServerRegistry.

Merges coverage previously split across test_registry.py and
test_mcp_registry_discovery.py. The old test_catalog.py was removed
entirely (catalog.py is now a deprecation stub with no exports).
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Direct registry operations (no singleton — each test makes its own instance)
# ---------------------------------------------------------------------------


class TestMCPServerRegistryOperations:
    """Tests for MCPServerRegistry methods using fresh instances."""

    def test_register_server_success(self):
        from app.agents.mcp.mcp_server_decorator import MCPServer
        from app.agents.mcp.models import AuthHint, MCPTransport
        from app.agents.mcp.registry import MCPServerRegistry

        @MCPServer(
            type_id="test_registry_server",
            display_name="Test Registry Server",
            description="Test server for registry",
            transport=MCPTransport.STDIO,
            command="test-cmd",
            auth=AuthHint(),  # required: None is rejected by Pydantic for AuthHint field
        )
        class TestRegistryServer:
            pass

        registry = MCPServerRegistry()
        success = registry.register_server(TestRegistryServer)

        assert success is True
        assert "test_registry_server" in registry._catalog
        template = registry.get_template("test_registry_server")
        assert template is not None
        assert template.type_id == "test_registry_server"
        assert template.display_name == "Test Registry Server"

    def test_register_server_without_decorator_fails_gracefully(self):
        from app.agents.mcp.registry import MCPServerRegistry

        class PlainClass:
            pass

        registry = MCPServerRegistry()
        success = registry.register_server(PlainClass)

        assert success is False
        assert len(registry._catalog) == 0

    def test_register_server_missing_type_id_fails(self):
        from app.agents.mcp.registry import MCPServerRegistry

        class BadServer:
            _is_mcp_server = True
            _mcp_server_metadata = {}  # missing type_id

        registry = MCPServerRegistry()
        success = registry.register_server(BadServer)

        assert success is False

    def test_discover_servers_from_module(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.discover_servers(["app.agents.mcp.servers.brave_search"])

        assert "brave_search" in registry._catalog
        template = registry.get_template("brave_search")
        assert template is not None
        assert template.display_name == "Brave Search"

    def test_discover_servers_invalid_module_does_not_raise(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        # Should log an error and continue, not propagate the exception.
        registry.discover_servers(["nonexistent.module.path"])

        assert len(registry._catalog) == 0

    def test_auto_discover_populates_all_six_servers(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        assert registry._initialized is False

        registry.auto_discover_mcp_servers()

        assert registry._initialized is True
        type_ids = [t.type_id for t in registry.list_all()]
        for expected in ("brave_search", "exa", "github", "jira", "notion", "slack"):
            assert expected in type_ids

    def test_auto_discover_is_idempotent(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()
        count_first = len(registry.list_all())

        registry.auto_discover_mcp_servers()  # second call must be a no-op

        assert len(registry.list_all()) == count_first

    def test_list_all_returns_all_templates(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()

        all_templates = registry.list_all()
        assert len(all_templates) >= 6

    def test_get_template_found(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()

        tpl = registry.get_template("github")
        assert tpl is not None
        assert tpl.type_id == "github"

    def test_get_template_not_found_returns_none(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        assert registry.get_template("nonexistent_xyz") is None

    def test_list_templates_returns_expected_shape(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()

        result = registry.list_templates()

        assert set(result.keys()) == {"items", "total", "page", "limit", "totalPages"}
        assert result["page"] == 1
        assert result["limit"] == 20
        assert result["total"] >= 6

    def test_list_templates_pagination_limits_items(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()
        total = len(registry.list_all())

        result = registry.list_templates(page=1, limit=2)

        assert len(result["items"]) == 2
        assert result["total"] == total
        assert result["totalPages"] == (total + 1) // 2

    def test_list_templates_last_page(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()
        total = len(registry.list_all())

        result = registry.list_templates(page=total, limit=1)

        assert len(result["items"]) == 1

    def test_list_templates_search_by_name(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()

        result = registry.list_templates(search="brave")

        assert result["total"] >= 1
        # Items are serialised with camelCase aliases
        type_ids = [item["typeId"] for item in result["items"]]
        assert "brave_search" in type_ids

    def test_list_templates_search_by_description(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()

        result = registry.list_templates(search="jira")
        assert result["total"] >= 1

    def test_list_templates_search_no_match_returns_empty(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()

        result = registry.list_templates(search="zzz_nonexistent_xyz_999")

        assert result["items"] == []
        assert result["total"] == 0

    def test_get_template_schema_structure(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        registry.auto_discover_mcp_servers()

        schema = registry.get_template_schema("brave_search")

        assert schema is not None
        assert schema["typeId"] == "brave_search"

        config = schema["configSchema"]
        assert "transport" in config
        assert "requiredEnv" in config
        assert "optionalEnv" in config
        assert "authMode" in config
        assert "supportedAuthTypes" in config

        auth = schema["authConfig"]
        assert "methods" in auth
        assert "defaultMethod" in auth
        assert "oauth2AuthorizationUrl" in auth
        assert "oauth2TokenUrl" in auth
        assert "oauth2Scopes" in auth
        assert "envMapping" in auth

    def test_get_template_schema_not_found_returns_none(self):
        from app.agents.mcp.registry import MCPServerRegistry

        registry = MCPServerRegistry()
        assert registry.get_template_schema("nonexistent_xyz") is None


# ---------------------------------------------------------------------------
# Singleton (get_mcp_server_registry) — require the reset_mcp_registry fixture
# defined in conftest.py so each test starts with a clean slate.
# ---------------------------------------------------------------------------


class TestMCPServerRegistrySingleton:
    """Tests for the get_mcp_server_registry lazy singleton helper."""

    def test_singleton_returns_same_instance(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        a = get_mcp_server_registry()
        b = get_mcp_server_registry()

        assert a is b

    def test_singleton_constructs_registry_exactly_once(self, reset_mcp_registry):
        from app.agents.mcp.registry import MCPServerRegistry, get_mcp_server_registry

        call_count = 0
        real_init = MCPServerRegistry.__init__

        def counting_init(self) -> None:
            nonlocal call_count
            call_count += 1
            real_init(self)

        with patch.object(MCPServerRegistry, "__init__", counting_init):
            get_mcp_server_registry()
            get_mcp_server_registry()

        assert call_count == 1

    def test_singleton_starts_empty_until_auto_discover(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        # get_mcp_server_registry() only creates the instance; auto-discovery
        # is triggered externally at app startup.
        assert registry._initialized is False
        assert registry.list_all() == []

    def test_singleton_get_template_after_auto_discover(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        registry.auto_discover_mcp_servers()

        tpl = registry.get_template("github")
        assert tpl is not None
        assert tpl.type_id == "github"

    def test_singleton_get_template_not_found(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        assert registry.get_template("xyz_does_not_exist") is None

    def test_singleton_list_templates_structure(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        registry.auto_discover_mcp_servers()

        result = registry.list_templates()
        assert set(result.keys()) == {"items", "total", "page", "limit", "totalPages"}

    def test_singleton_get_template_schema_found(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        registry.auto_discover_mcp_servers()

        schema = registry.get_template_schema("github")
        assert schema is not None
        assert "configSchema" in schema
        assert isinstance(schema["configSchema"], dict)

    def test_singleton_get_template_schema_not_found(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        assert registry.get_template_schema("xyz") is None
