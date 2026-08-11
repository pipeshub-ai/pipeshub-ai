"""Unit tests for app.agents.mcp.mcp_server_decorator, registry, and catalog."""
from unittest.mock import patch

import pytest

import app.agents.mcp.mcp_server_decorator as decorator_module
from app.agents.mcp.catalog import paginate, search_templates
from app.agents.mcp.mcp_server_decorator import get_registered_templates, mcp_server
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport
from app.agents.mcp.registry import MCPRegistry, get_mcp_registry


def _template(type_id: str, **overrides) -> MCPServerTemplate:
    defaults = dict(
        type_id=type_id,
        display_name=type_id.title(),
        description=f"{type_id} description",
        transport=MCPTransport.STDIO,
        default_auth_mode=MCPAuthMode.API_TOKEN,
        tags=["search"],
    )
    defaults.update(overrides)
    return MCPServerTemplate(**defaults)


# Each decorator test scopes the module-level registry dict with `patch.dict` so it
# never leaks state into other tests (in particular `TestMCPRegistry`, which relies on
# the real `github`/`notion`/etc. templates having been registered once at import
# time — Python's module cache means a plain `clear_registered_templates()` would wipe
# them for good, since re-importing an already-imported server module is a no-op).
class TestMcpServerDecorator:
    def test_registers_template_by_normalized_type_id(self) -> None:
        with patch.dict(decorator_module._MCP_SERVER_TEMPLATES, {}, clear=True):
            @mcp_server(_template("Brave Search"))
            class BraveServer:
                pass

            templates = get_registered_templates()
            assert "brave_search" in templates
            assert BraveServer.mcp_template.type_id == "Brave Search"

    def test_duplicate_type_id_raises(self) -> None:
        with patch.dict(decorator_module._MCP_SERVER_TEMPLATES, {}, clear=True):
            @mcp_server(_template("dup"))
            class First:
                pass

            with pytest.raises(ValueError, match="Duplicate"):
                @mcp_server(_template("dup"))
                class Second:
                    pass

    def test_clear_registered_templates(self) -> None:
        with patch.dict(decorator_module._MCP_SERVER_TEMPLATES, {}, clear=True):
            @mcp_server(_template("temp"))
            class TempServer:
                pass

            assert get_registered_templates()
            decorator_module.clear_registered_templates()
            assert get_registered_templates() == {}


class TestMCPRegistry:
    def test_auto_discover_templates_loads_builtin_servers(self) -> None:
        registry = MCPRegistry()
        registry.auto_discover_templates()
        templates = registry.list_templates()
        type_ids = {t.type_id for t in templates}
        # Active catalog representatives — one per transport/auth combination among
        # currently enabled servers (others are temporarily commented out).
        assert "github" in type_ids
        assert "atlassian_rovo" in type_ids
        assert "linear" in type_ids
        assert "notion" in type_ids
        assert "slack" in type_ids
        assert "exa" in type_ids
        assert "box" in type_ids
        assert "clickup" in type_ids

    def test_auto_discover_templates_are_well_formed(self) -> None:
        """Every registered template must carry the fields its own transport/auth mode
        needs — catches a copy-paste mistake in a new `@mcp_server` module (e.g. an HTTP
        template missing `default_url`) at test time instead of at first-use in the UI."""
        registry = MCPRegistry()
        registry.auto_discover_templates()
        templates = registry.list_templates()
        assert len(templates) >= 8

        seen_type_ids: set[str] = set()
        for template in templates:
            assert template.type_id not in seen_type_ids, f"duplicate type_id: {template.type_id}"
            seen_type_ids.add(template.type_id)
            assert template.display_name
            assert template.description
            assert template.default_auth_mode in template.supported_auth_modes

            if template.transport == MCPTransport.STDIO:
                assert template.command, f"{template.type_id}: STDIO template missing command"
            else:
                assert template.default_url, f"{template.type_id}: HTTP/SSE template missing default_url"

            if template.default_auth_mode == MCPAuthMode.API_TOKEN:
                assert template.required_env, f"{template.type_id}: API_TOKEN template missing required_env"
            if template.default_auth_mode == MCPAuthMode.OAUTH:
                assert template.authorization_url, f"{template.type_id}: OAuth template missing authorization_url"
                assert template.token_url, f"{template.type_id}: OAuth template missing token_url"

    def test_auto_discover_is_idempotent(self) -> None:
        registry = MCPRegistry()
        registry.auto_discover_templates()
        count_first = len(registry.list_templates())
        registry.auto_discover_templates()
        assert len(registry.list_templates()) == count_first

    def test_get_template_normalizes_lookup(self) -> None:
        registry = MCPRegistry()
        registry.auto_discover_templates()
        assert registry.get_template("Atlassian-Rovo") is not None
        assert registry.get_template("ATLASSIAN_ROVO") is not None

    def test_get_template_missing_returns_none(self) -> None:
        registry = MCPRegistry()
        registry.auto_discover_templates()
        assert registry.get_template("does_not_exist") is None

    def test_get_template_empty_type_id_returns_none(self) -> None:
        registry = MCPRegistry()
        assert registry.get_template("") is None

    def test_get_mcp_registry_singleton(self) -> None:
        reg1 = get_mcp_registry()
        reg2 = get_mcp_registry()
        assert reg1 is reg2

    def test_auto_discover_skips_modules_that_fail_to_import(self) -> None:
        """A broken server module must not abort catalog discovery for the rest."""
        import importlib

        import app.agents.mcp.registry as registry_module

        registry = MCPRegistry()
        broken = type("ModuleInfo", (), {"name": "broken_server"})()
        real_import_module = importlib.import_module

        def import_module_side_effect(name, package=None):
            # registry.importlib is the stdlib module — a blanket raise breaks
            # unittest.mock's own importlib.import_module-based target resolution.
            if name.endswith(".broken_server"):
                raise RuntimeError("simulated import failure")
            return real_import_module(name, package)

        with patch.object(registry_module.pkgutil, "iter_modules", return_value=[broken]), \
             patch.object(
                 registry_module.importlib,
                 "import_module",
                 side_effect=import_module_side_effect,
             ), \
             patch.object(registry_module, "get_registered_templates", return_value={}):
            registry.auto_discover_templates()

        assert registry.list_templates() == []
        assert registry._discovered is True


class TestSearchTemplates:
    def test_no_search_returns_all(self) -> None:
        templates = [_template("a"), _template("b")]
        assert search_templates(templates, None) == templates

    def test_blank_search_returns_all(self) -> None:
        templates = [_template("a")]
        assert search_templates(templates, "   ") == templates

    def test_matches_type_id(self) -> None:
        templates = [_template("brave_search"), _template("exa")]
        result = search_templates(templates, "brave")
        assert len(result) == 1
        assert result[0].type_id == "brave_search"

    def test_matches_display_name_case_insensitive(self) -> None:
        # type_id must not contain the query, otherwise the type_id branch short-circuits
        # before the display_name return is exercised.
        templates = [_template("exa", display_name="Brave Search")]
        result = search_templates(templates, "BRAVE")
        assert len(result) == 1
        assert result[0].type_id == "exa"

    def test_matches_description(self) -> None:
        templates = [
            _template("exa", display_name="Exa", description="AI web search engine"),
            _template("github", display_name="GitHub", description="code hosting"),
        ]
        result = search_templates(templates, "web search")
        assert len(result) == 1
        assert result[0].type_id == "exa"

    def test_matches_tags(self) -> None:
        templates = [_template("brave_search", tags=["search", "web"])]
        result = search_templates(templates, "web")
        assert len(result) == 1

    def test_no_match_returns_empty(self) -> None:
        templates = [_template("brave_search")]
        assert search_templates(templates, "nonexistent") == []


class TestPaginate:
    def test_default_page_and_limit(self) -> None:
        items = list(range(10))
        page_items, total = paginate(items, None, None)
        assert total == 10
        assert page_items == items

    def test_second_page(self) -> None:
        items = list(range(10))
        page_items, total = paginate(items, 2, 3)
        assert page_items == [3, 4, 5]
        assert total == 10

    def test_limit_clamped_to_max(self) -> None:
        items = list(range(500))
        page_items, _ = paginate(items, 1, 10_000)
        assert len(page_items) == 200

    def test_page_below_one_clamped(self) -> None:
        items = list(range(5))
        page_items, _ = paginate(items, 0, 2)
        assert page_items == items[:2]

    def test_out_of_range_page_returns_empty(self) -> None:
        items = list(range(3))
        page_items, total = paginate(items, 5, 2)
        assert page_items == []
        assert total == 3
