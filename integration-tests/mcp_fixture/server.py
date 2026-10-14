"""A small external MCP server for agents to call during the integration run.

Runs as the ``mcp-fixture`` service of the integration stack. PipesHub is
registered with it as an external MCP server (streamable HTTP), and an agent
that has it attached should call its tool when a question needs it.

    POST   /mcp                        MCP over streamable HTTP (JSON replies)
    GET    /__fixture__/health         200 once serving
    GET    /__fixture__/calls          every tools/call received, oldest first
    POST   /__fixture__/reset          forget the recorded calls

The tool answers with facts no model could know (an order status), so a test
can tell from the call log that the agent really asked this server rather than
guessing. Standard library only, so it runs on a stock Python image.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SERVER_NAME = "pipeshub-it-mcp"
ORDER_TOOL = "lookup_order_status"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": ORDER_TOOL,
        "description": (
            "Look up the live shipping status of a customer order in the Kestrel "
            "order system. Use it for any question about where an order is."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The order id, e.g. KO-1234"},
            },
            "required": ["order_id"],
        },
    },
]

_calls: list[dict[str, Any]] = []
_lock = threading.Lock()


def order_status(order_id: str) -> str:
    digits = "".join(ch for ch in order_id if ch.isdigit()) or "0"
    return (
        f"Order {order_id}: status SHIPPED, carrier Kestrel Freight, "
        f"tracking KF-{int(digits) * 7 % 100000:05d}, delivery in 2 days."
    )


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_rpc(message: dict[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in, its reply out (``None`` for a notification)."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if request_id is None:
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(request_id, {
            "protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name != ORDER_TOOL:
            return _error(request_id, -32602, f"Unknown tool: {name}")
        order_id = str(arguments.get("order_id") or "").strip()
        with _lock:
            _calls.append({"tool": name, "arguments": arguments, "at": time.time()})
        if not order_id:
            return _result(request_id, {
                "content": [{"type": "text", "text": "order_id is required"}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [{"type": "text", "text": order_status(order_id)}],
            "isError": False,
        })
    return _error(request_id, -32601, f"Method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass

    def _send(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        if self.path == "/__fixture__/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/__fixture__/calls":
            with _lock:
                self._send(200, {"calls": list(_calls)})
        elif self.path == "/mcp":
            # No server-initiated stream; clients fall back to request/response.
            self._send(405, None, {"Allow": "POST, DELETE"})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib name
        self._send(200 if self.path == "/mcp" else 404, None)

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path == "/__fixture__/reset":
            with _lock:
                _calls.clear()
            self._send(200, {"status": "reset"})
            return
        if self.path != "/mcp":
            self._send(404, {"error": "not found"})
            return
        try:
            message = json.loads(raw or b"{}")
        except ValueError:
            self._send(400, _error(None, -32700, "Parse error"))
            return
        if isinstance(message, list):
            replies = [r for r in (handle_rpc(m) for m in message if isinstance(m, dict)) if r]
            self._send(200, replies) if replies else self._send(202, None)
            return
        if not isinstance(message, dict):
            self._send(400, _error(None, -32600, "Invalid Request"))
            return
        reply = handle_rpc(message)
        if reply is None:
            self._send(202, None)
            return
        headers = {}
        if message.get("method") == "initialize":
            headers["Mcp-Session-Id"] = uuid.uuid4().hex
        self._send(200, reply, headers)


def serve(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(args.host, args.port).serve_forever()


if __name__ == "__main__":
    main()
