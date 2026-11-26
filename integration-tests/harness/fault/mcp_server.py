"""A remote MCP server on streamable HTTP for the degraded scenarios (QA-17) and the rig's mcp-5-servers profile.

    python mcp_server.py <port> [host]

The scenarios kill this process while a run is in flight. It runs on the backend's own MCP SDK (``MCPServer``): the
suites that start it run in the backend's virtualenv, which has no ``fastmcp`` (WP88-MCP1).
"""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

server = MCPServer("fault-mcp")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return text


def main() -> None:
    port = int(sys.argv[1])
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    print(f"MCP SERVER READY {port}", flush=True)  # noqa: T201 - the rig waits for the port too
    # Reached through the degraded rig's proxy, under the host's private address: the Host header names the proxy's
    # address, which DNS-rebinding protection (on for a server bound to loopback) answers 421.
    server.run(transport="streamable-http", host=host, port=port,
               transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


if __name__ == "__main__":
    main()
