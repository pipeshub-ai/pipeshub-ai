"""Parametrized tests for the six built-in MCP server template definitions.

Each server lives in app/agents/mcp/servers/ and is decorated with @MCPServer.
These tests verify that:
  1. The class carries the correct marker attributes from the decorator.
  2. Its metadata produces a valid MCPServerTemplate via the registry.
  3. The registry's auto_discover_mcp_servers() finds every server.
"""

import importlib

import pytest

# (module_path, class_name, expected_type_id)
_SERVER_PARAMS = [
    ("app.agents.mcp.servers.brave_search", "BraveSearch", "brave_search"),
    ("app.agents.mcp.servers.exa", "ExaSearch", "exa"),
    ("app.agents.mcp.servers.github", "GitHubMCP", "github"),
    ("app.agents.mcp.servers.jira", "JiraMCP", "jira"),
    ("app.agents.mcp.servers.notion", "NotionMCP", "notion"),
    ("app.agents.mcp.servers.slack", "SlackMCP", "slack"),
]


@pytest.fixture
def fresh_registry():
    """Provide a fresh MCPServerRegistry (and clean up the singleton slot)."""
    import app.agents.mcp.registry as reg

    reg._registry_instance = None
    registry = reg.MCPServerRegistry()
    yield registry
    reg._registry_instance = None


# ---------------------------------------------------------------------------
# Per-server decorator tests
# ---------------------------------------------------------------------------


class TestServerTemplateDecorators:
    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_class_has_is_mcp_server_marker(self, module_path, class_name, type_id):
        """Every server class must carry _is_mcp_server=True set by @MCPServer."""
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)

        assert getattr(cls, "_is_mcp_server", False) is True

    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_class_has_metadata_dict(self, module_path, class_name, type_id):
        """Every server class must have a _mcp_server_metadata dict."""
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)

        assert hasattr(cls, "_mcp_server_metadata")
        assert isinstance(cls._mcp_server_metadata, dict)

    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_metadata_has_required_keys(self, module_path, class_name, type_id):
        """The metadata dict must contain all fields expected by MCPServerTemplate."""
        mod = importlib.import_module(module_path)
        meta = getattr(mod, class_name)._mcp_server_metadata

        for key in ("type_id", "display_name", "description", "transport", "auth_mode"):
            assert key in meta, f"Missing key '{key}' in {class_name}._mcp_server_metadata"

    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_type_id_matches_expected(self, module_path, class_name, type_id):
        mod = importlib.import_module(module_path)
        meta = getattr(mod, class_name)._mcp_server_metadata

        assert meta["type_id"] == type_id

    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_display_name_is_non_empty(self, module_path, class_name, type_id):
        mod = importlib.import_module(module_path)
        meta = getattr(mod, class_name)._mcp_server_metadata

        assert isinstance(meta["display_name"], str)
        assert len(meta["display_name"]) > 0

    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_description_is_non_empty(self, module_path, class_name, type_id):
        mod = importlib.import_module(module_path)
        meta = getattr(mod, class_name)._mcp_server_metadata

        assert isinstance(meta["description"], str)
        assert len(meta["description"]) > 0


# ---------------------------------------------------------------------------
# Per-server registration tests
# ---------------------------------------------------------------------------


class TestServerTemplateRegistration:
    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_register_server_succeeds(self, module_path, class_name, type_id, fresh_registry):
        """register_server() must return True for every decorated class."""
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)

        success = fresh_registry.register_server(cls)

        assert success is True

    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_registered_template_is_valid(self, module_path, class_name, type_id, fresh_registry):
        """After registration, get_template() must return a valid MCPServerTemplate."""
        from app.agents.mcp.models import MCPServerTemplate

        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        fresh_registry.register_server(cls)

        tpl = fresh_registry.get_template(type_id)

        assert tpl is not None
        assert isinstance(tpl, MCPServerTemplate)
        assert tpl.type_id == type_id
        assert tpl.display_name != ""
        assert tpl.description != ""

    @pytest.mark.parametrize("module_path,class_name,type_id", _SERVER_PARAMS)
    def test_template_schema_includes_auth_config(
        self, module_path, class_name, type_id, fresh_registry
    ):
        """get_template_schema() must return a dict with configSchema and authConfig."""
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        fresh_registry.register_server(cls)

        schema = fresh_registry.get_template_schema(type_id)

        assert schema is not None
        assert "configSchema" in schema
        assert "authConfig" in schema


# ---------------------------------------------------------------------------
# Auto-discovery tests
# ---------------------------------------------------------------------------


class TestRegistryAutoDiscovery:
    def test_auto_discover_registers_all_six_servers(self, fresh_registry):
        fresh_registry.auto_discover_mcp_servers()

        type_ids = {t.type_id for t in fresh_registry.list_all()}
        expected = {"brave_search", "exa", "github", "jira", "notion", "slack"}

        assert expected.issubset(type_ids)

    def test_auto_discover_sets_initialized_flag(self, fresh_registry):
        assert fresh_registry._initialized is False

        fresh_registry.auto_discover_mcp_servers()

        assert fresh_registry._initialized is True

    def test_auto_discover_is_idempotent(self, fresh_registry):
        fresh_registry.auto_discover_mcp_servers()
        count_after_first = len(fresh_registry.list_all())

        fresh_registry.auto_discover_mcp_servers()

        assert len(fresh_registry.list_all()) == count_after_first

    def test_all_templates_have_non_empty_display_name(self, fresh_registry):
        fresh_registry.auto_discover_mcp_servers()

        for tpl in fresh_registry.list_all():
            assert tpl.display_name, f"display_name is empty for {tpl.type_id}"

    def test_all_templates_have_valid_transport(self, fresh_registry):
        from app.agents.mcp.models import MCPTransport

        fresh_registry.auto_discover_mcp_servers()
        valid_transports = set(MCPTransport)

        for tpl in fresh_registry.list_all():
            assert tpl.transport in valid_transports, (
                f"{tpl.type_id} has invalid transport: {tpl.transport}"
            )

    def test_all_templates_have_valid_auth_mode(self, fresh_registry):
        from app.agents.mcp.models import MCPAuthMode

        fresh_registry.auto_discover_mcp_servers()
        valid_modes = set(MCPAuthMode)

        for tpl in fresh_registry.list_all():
            assert tpl.auth_mode in valid_modes, (
                f"{tpl.type_id} has invalid auth_mode: {tpl.auth_mode}"
            )
