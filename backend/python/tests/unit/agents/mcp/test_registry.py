"""Unit tests for app.agents.mcp.registry."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def reset_mcp_registry():
    import app.agents.mcp.registry as reg

    reg._registry_instance = None
    yield
    reg._registry_instance = None


class TestMCPServerRegistry:
    """Tests for MCPServerRegistry and get_mcp_server_registry singleton."""

    def test_singleton_pattern(self, reset_mcp_registry):
        from app.agents.mcp.registry import (
            MCPServerRegistry,
            get_mcp_server_registry,
        )

        call_count = 0
        real_init = MCPServerRegistry.__init__

        def counting_init(self) -> None:
            nonlocal call_count
            call_count += 1
            real_init(self)

        with patch("app.agents.mcp.registry.logger", MagicMock()):
            with patch.object(MCPServerRegistry, "__init__", counting_init):
                a = get_mcp_server_registry()
                b = get_mcp_server_registry()

        assert a is b
        assert isinstance(a, MCPServerRegistry)
        assert call_count == 1

    def test_get_template_found(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        tpl = registry.get_template("github")
        assert tpl is not None
        assert tpl.type_id == "github"

    def test_get_template_not_found(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        assert registry.get_template("xyz") is None

    def test_list_templates_default(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        result = registry.list_templates()

        assert set(result.keys()) == {
            "items",
            "total",
            "page",
            "limit",
            "totalPages",
        }

    def test_list_templates_pagination(self, reset_mcp_registry):
        from app.agents.mcp.catalog import BUILTIN_CATALOG
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        result = registry.list_templates(page=1, limit=2)

        assert len(result["items"]) <= 2
        assert result["total"] == len(BUILTIN_CATALOG)

    def test_list_templates_search(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        result = registry.list_templates(search="github")

        type_ids = {item["type_id"] for item in result["items"]}
        assert "github" in type_ids

    def test_list_templates_search_no_match(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        result = registry.list_templates(search="zzz")

        assert result["items"] == []

    def test_get_template_schema(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        schema = registry.get_template_schema("github")

        assert schema is not None
        assert "configSchema" in schema
        assert isinstance(schema["configSchema"], dict)

    def test_get_template_schema_not_found(self, reset_mcp_registry):
        from app.agents.mcp.registry import get_mcp_server_registry

        registry = get_mcp_server_registry()
        assert registry.get_template_schema("xyz") is None
