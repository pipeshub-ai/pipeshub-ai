"""WP88-MCP1: the fault suite's MCP server runs on the backend's virtualenv, the one its workflows install."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

FAULT = Path(__file__).resolve().parents[1] / "harness" / "fault"

pytestmark = pytest.mark.unit


def test_wp88_mcp1_the_fault_suite_s_mcp_server_runs_on_the_backend_s_own_sdk() -> None:
    """The degraded scenarios and the rig's mcp-5-servers profile start ``mcp_server.py`` with the backend's
    virtualenv (harness-fault.yml and perf-harness.yml install only it), which has the MCP SDK 2 and not
    ``fastmcp``: every degraded scenario errored at set-up (``No module named 'fastmcp'``)."""
    spec = importlib.util.spec_from_file_location("fault_mcp_server", FAULT / "mcp_server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    tools = asyncio.run(module.server.list_tools())
    assert [tool.name for tool in tools] == ["echo"]


def test_wp88_mcp2_the_rigs_reach_their_mcp_server_on_an_address_the_url_guard_allows() -> None:
    """WP88-MCP2: the product's MCP URL guard refuses loopback in every mode; the degraded rig's and the perf rig's
    MCP URLs named 127.0.0.1, so every call failed ``MCPUrlBlockedError`` once the server started."""
    import ipaddress

    spec = importlib.util.spec_from_file_location("fault_stack_wp88", FAULT / "stack.py")
    assert spec is not None and spec.loader is not None
    stack = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stack
    spec.loader.exec_module(stack)
    address = ipaddress.ip_address(stack.mcp_host())
    assert address.is_private and not address.is_loopback
    degraded = (FAULT / "degraded.py").read_text()
    assert "http://127.0.0.1:{proxy('mcp')}" not in degraded and "stack.mcp_host()" in degraded
