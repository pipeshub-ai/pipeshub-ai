"""A minimal MCP server over stdio (newline-delimited JSON-RPC), standard library only.

Run as ``python fake_mcp_stdio_server.py``: it serves one ``list_prs`` tool until
stdin closes. The leak test spawns it the way PipesHub spawns ``npx`` servers.
"""

from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "list_prs",
        "description": "List pull requests.",
        "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
    }
]


def _result(request: dict[str, object]) -> dict[str, object] | None:
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        assert isinstance(params, dict)
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-github", "version": "1.0.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return {"content": [{"type": "text", "text": "pr-1"}], "isError": False}
    if method == "ping":
        return {}
    return None


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if "id" not in request:
            continue
        result = _result(request)
        reply: dict[str, object] = {"jsonrpc": "2.0", "id": request["id"]}
        if result is None:
            reply["error"] = {"code": -32601, "message": f"unknown method {request.get('method')}"}
        else:
            reply["result"] = result
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
