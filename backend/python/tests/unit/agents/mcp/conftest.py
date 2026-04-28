"""Shared fixtures and module stubs for MCP unit tests.

Module-level stubs run at conftest import time — *before* any test_*.py file
in this directory is collected — so they take effect consistently regardless
of collection order.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# fastmcp stub
#
# test_client.py / test_discovery.py put a *non-package* fastmcp stub in
# sys.modules when they are collected, which breaks `from fastmcp.client.transports
# import …` in app.agents.mcp.client.  We install a full-package stub here first.
# ---------------------------------------------------------------------------


def _stub_fastmcp() -> None:
    """Install a complete fastmcp package stub unless the real one is available."""
    try:
        # If transports are importable the real package is present — nothing to do.
        from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport  # noqa: F401

        return
    except (ImportError, ModuleNotFoundError):
        pass

    def _make_pkg(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        mod.__path__ = []  # marks as a package
        mod.__package__ = name
        sys.modules[name] = mod
        return mod

    fastmcp = _make_pkg("fastmcp")
    fastmcp.Client = object  # type: ignore[attr-defined]

    fastmcp_client = _make_pkg("fastmcp.client")
    fastmcp.client = fastmcp_client  # type: ignore[attr-defined]

    fastmcp_transports = _make_pkg("fastmcp.client.transports")
    fastmcp_transports.SSETransport = object  # type: ignore[attr-defined]
    fastmcp_transports.StdioTransport = object  # type: ignore[attr-defined]
    fastmcp_transports.StreamableHttpTransport = object  # type: ignore[attr-defined]
    fastmcp_client.transports = fastmcp_transports  # type: ignore[attr-defined]


_stub_fastmcp()


# ---------------------------------------------------------------------------
# langchain_core stub
#
# The root conftest may have replaced langchain_core with a MagicMock if it
# wasn't installed.  MCPToolWrapper subclasses BaseTool, so we need a proper
# class (not a MagicMock) for the metaclass machinery to work.
# ---------------------------------------------------------------------------


def _stub_langchain_core_base_tool() -> None:
    """Ensure langchain_core.tools.BaseTool is a real class, not a MagicMock."""
    try:
        from langchain_core.tools import BaseTool as _RealBaseTool  # noqa: F401

        if not isinstance(_RealBaseTool, MagicMock):
            return  # real package is importable
    except Exception:
        pass

    class _StubBaseTool:
        name: str = ""
        description: str = ""

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    lc = sys.modules.get("langchain_core") or types.ModuleType("langchain_core")
    if not hasattr(lc, "__path__"):
        lc.__path__ = []
    sys.modules["langchain_core"] = lc

    lc_tools = types.ModuleType("langchain_core.tools")
    lc_tools.BaseTool = _StubBaseTool  # type: ignore[attr-defined]
    sys.modules["langchain_core.tools"] = lc_tools


_stub_langchain_core_base_tool()


@pytest.fixture
def mock_mcp_client():
    """A mock fastmcp Client supporting async context-manager and tool protocols."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.list_tools = AsyncMock(return_value=[])
    client.call_tool = AsyncMock(return_value="ok")
    return client


@pytest.fixture
def sample_tool_info():
    """A sample MCPToolInfo for use across wrapper and discovery tests."""
    from app.agents.mcp.models import MCPToolInfo

    return MCPToolInfo(
        name="search",
        namespaced_name="mcp_test_server_search",
        description="Search the web",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        server_name="test_server",
        instance_id="inst-test-uuid-1",
    )


@pytest.fixture
def sample_server_config():
    """A sample MCPServerConfig for STDIO transport."""
    from app.agents.mcp.models import MCPServerConfig, MCPTransport

    return MCPServerConfig(
        name="test_server",
        transport=MCPTransport.STDIO,
        command="npx",
        args=["-y", "test-mcp-server"],
    )


@pytest.fixture
def reset_mcp_registry():
    """Reset the MCPServerRegistry singleton before and after each test."""
    import app.agents.mcp.registry as reg

    reg._registry_instance = None
    yield
    reg._registry_instance = None
