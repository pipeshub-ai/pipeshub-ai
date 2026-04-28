"""
Unit tests for MCP Server decorator
"""

import pytest
from app.agents.mcp.mcp_server_decorator import MCPServer
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPTransport


def test_mcp_server_decorator_basic():
    """Test that @MCPServer decorator attaches metadata to class"""
    
    @MCPServer(
        type_id="test_server",
        display_name="Test Server",
        description="A test MCP server",
        transport=MCPTransport.STDIO,
        command="test-command",
    )
    class TestServer:
        pass
    
    # Check that the decorator marked the class
    assert hasattr(TestServer, '_is_mcp_server')
    assert TestServer._is_mcp_server is True
    
    # Check that metadata was attached
    assert hasattr(TestServer, '_mcp_server_metadata')
    metadata = TestServer._mcp_server_metadata
    
    assert metadata['type_id'] == "test_server"
    assert metadata['display_name'] == "Test Server"
    assert metadata['description'] == "A test MCP server"
    assert metadata['transport'] == "stdio"
    assert metadata['command'] == "test-command"


def test_mcp_server_decorator_with_auth():
    """Test @MCPServer decorator with authentication configuration"""
    
    auth_hint = AuthHint(
        methods=["api_key"],
        default_method="api_key",
        env_mapping={"apiKey": "TEST_API_KEY"},
    )
    
    @MCPServer(
        type_id="test_auth_server",
        display_name="Test Auth Server",
        description="Server with auth",
        transport=MCPTransport.STREAMABLE_HTTP,
        url="https://test.example.com/mcp",
        auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_types=["API_TOKEN"],
        required_env=["TEST_API_KEY"],
        optional_env=["TEST_OPTIONAL"],
        auth=auth_hint,
    )
    class TestAuthServer:
        pass
    
    metadata = TestAuthServer._mcp_server_metadata
    
    assert metadata['type_id'] == "test_auth_server"
    assert metadata['transport'] == "streamable_http"
    assert metadata['url'] == "https://test.example.com/mcp"
    assert metadata['auth_mode'] == "api_token"
    assert metadata['supported_auth_types'] == ["API_TOKEN"]
    assert metadata['required_env'] == ["TEST_API_KEY"]
    assert metadata['optional_env'] == ["TEST_OPTIONAL"]
    assert metadata['auth'] == auth_hint


def test_mcp_server_decorator_defaults():
    """Test @MCPServer decorator with minimal parameters (defaults)"""
    
    @MCPServer(
        type_id="minimal_server",
        display_name="Minimal Server",
        description="Minimal config",
    )
    class MinimalServer:
        pass
    
    metadata = MinimalServer._mcp_server_metadata
    
    assert metadata['type_id'] == "minimal_server"
    assert metadata['display_name'] == "Minimal Server"
    assert metadata['transport'] == "stdio"  # default
    assert metadata['auth_mode'] == "none"  # default
    assert metadata['command'] == ""
    assert metadata['default_args'] == []
    assert metadata['supported_auth_types'] == []
    assert metadata['required_env'] == []
    assert metadata['optional_env'] == []
    assert metadata['tags'] == []
