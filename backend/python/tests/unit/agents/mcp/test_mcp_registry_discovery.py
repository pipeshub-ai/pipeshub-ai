"""
Unit tests for MCP Server Registry auto-discovery
"""

import pytest
from app.agents.mcp.registry import MCPServerRegistry
from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


def test_registry_register_server():
    """Test registering an MCP server class"""
    
    @MCPServer(
        type_id="test_registry_server",
        display_name="Test Registry Server",
        description="Test server for registry",
        transport=MCPTransport.STDIO,
        command="test-cmd",
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


def test_registry_register_server_without_decorator():
    """Test that registering a non-decorated class fails gracefully"""
    
    class PlainClass:
        pass
    
    registry = MCPServerRegistry()
    success = registry.register_server(PlainClass)
    
    assert success is False
    assert len(registry._catalog) == 0


def test_registry_discover_servers():
    """Test discovering servers from a module path"""
    
    registry = MCPServerRegistry()
    
    # Discover from the brave_search module
    registry.discover_servers(['app.agents.mcp.servers.brave_search'])
    
    # Should have registered brave_search
    assert "brave_search" in registry._catalog
    template = registry.get_template("brave_search")
    assert template is not None
    assert template.display_name == "Brave Search"


def test_registry_auto_discover_mcp_servers():
    """Test auto-discovery of all MCP servers"""
    
    registry = MCPServerRegistry()
    assert registry._initialized is False
    
    registry.auto_discover_mcp_servers()
    
    assert registry._initialized is True
    
    # Should have discovered brave_search and exa
    templates = registry.list_all()
    assert len(templates) >= 2
    
    type_ids = [t.type_id for t in templates]
    assert "brave_search" in type_ids
    assert "exa" in type_ids


def test_registry_list_templates():
    """Test listing templates with pagination"""
    
    registry = MCPServerRegistry()
    registry.auto_discover_mcp_servers()
    
    result = registry.list_templates(page=1, limit=10)
    
    assert "items" in result
    assert "total" in result
    assert "page" in result
    assert "limit" in result
    assert "totalPages" in result
    
    assert result["page"] == 1
    assert result["limit"] == 10
    assert len(result["items"]) >= 2  # brave_search and exa


def test_registry_list_templates_with_search():
    """Test listing templates with search filter"""
    
    registry = MCPServerRegistry()
    registry.auto_discover_mcp_servers()
    
    # Search for "brave"
    result = registry.list_templates(search="brave", page=1, limit=10)
    
    assert result["total"] >= 1
    assert any("brave" in item["displayName"].lower() for item in result["items"])


def test_registry_get_template_schema():
    """Test getting full template schema"""
    
    registry = MCPServerRegistry()
    registry.auto_discover_mcp_servers()
    
    schema = registry.get_template_schema("brave_search")
    
    assert schema is not None
    assert schema["typeId"] == "brave_search"
    assert "configSchema" in schema
    assert "authConfig" in schema
    
    # Check config schema structure
    config = schema["configSchema"]
    assert "transport" in config
    assert "requiredEnv" in config
    assert "optionalEnv" in config
    assert "authMode" in config


def test_registry_singleton_behavior():
    """Test that get_mcp_server_registry returns a singleton"""
    
    from app.agents.mcp.registry import get_mcp_server_registry, _registry_instance
    
    # Reset the singleton for this test
    import app.agents.mcp.registry as registry_module
    registry_module._registry_instance = None
    
    registry1 = get_mcp_server_registry()
    registry2 = get_mcp_server_registry()
    
    assert registry1 is registry2
