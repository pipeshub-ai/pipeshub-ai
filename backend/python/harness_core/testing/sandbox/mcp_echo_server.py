"""A minimal STDIO MCP server for runner tests (P5.10). Standard library only; also runs as a script.

Written for PipesHub's tests and licensed with them (Apache-2.0). Tools:
``echo`` (returns its text), ``env_digest`` (SHA-256 of an environment variable,
so a test proves what the server holds without it ever printing it),
``fetch`` (HTTP GET through the environment's proxy), ``send_secret`` (the same GET
with an environment variable as its ``X-Api-Key``, as a server uses its key), ``where``
(host name and uid: which container answered). Newline-delimited JSON-RPC 2.0 on stdin/stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "pxh-echo"
_TEXT = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
_NAME = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
_URL = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
_SEND = {
    "type": "object",
    "properties": {"url": {"type": "string"}, "name": {"type": "string"}},
    "required": ["url", "name"],
}
SECRET_HEADER = "X-Api-Key"
TOOLS: tuple[dict[str, Any], ...] = (
    {"name": "echo", "description": "Return the text.", "inputSchema": _TEXT},
    {"name": "env_digest", "description": "SHA-256 of an environment variable.", "inputSchema": _NAME},
    {"name": "fetch", "description": "HTTP GET a URL.", "inputSchema": _URL},
    {"name": "send_secret", "description": "HTTP GET a URL with a variable as its key.", "inputSchema": _SEND},
    {"name": "where", "description": "Host name and uid of the server.", "inputSchema": {"type": "object"}},
)


@dataclass(frozen=True)
class FetchNeeded:
    """``respond`` asks its driver to fetch a URL; ``fetched`` turns the answer into the reply."""

    request_id: object
    url: str
    headers: Mapping[str, str] = field(default_factory=dict[str, str])


def _text(request_id: object, text: str, *, error: bool = False) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}], "isError": error}}


def respond(message: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any] | FetchNeeded | None:
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        return None
    params: Mapping[str, Any] = message.get("params") or {}
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
        }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list(TOOLS)}}
    if method == "tools/call":
        name = params.get("name")
        args: Mapping[str, Any] = params.get("arguments") or {}
        if name == "echo":
            return _text(request_id, str(args.get("text", "")))
        if name == "env_digest":
            value = env.get(str(args.get("name", "")))
            return _text(request_id, "unset" if value is None else hashlib.sha256(value.encode()).hexdigest())
        if name == "fetch":
            return FetchNeeded(request_id, str(args.get("url", "")))
        if name == "send_secret":
            key = env.get(str(args.get("name", "")), "")
            return FetchNeeded(request_id, str(args.get("url", "")), {SECRET_HEADER: key})
        if name == "where":
            return _text(request_id, f"{socket.gethostname()} uid={os.getuid()}")
        return _text(request_id, f"unknown tool {name}", error=True)
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method {method} not found"}}


def fetched(request_id: object, status: int, body: str) -> dict[str, Any]:
    return _text(request_id, f"{status} {body[:200]}", error=status >= 400)


def _http_get(url: str, headers: Mapping[str, str]) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=dict(headers)), timeout=20) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 599, type(exc).__name__


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        reply = respond(json.loads(line), os.environ)
        if isinstance(reply, FetchNeeded):
            reply = fetched(reply.request_id, *_http_get(reply.url, reply.headers))
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
