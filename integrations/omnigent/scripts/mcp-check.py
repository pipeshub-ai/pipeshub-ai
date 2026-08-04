#!/usr/bin/env python3
"""Preflight check for PipesHub's Streamable HTTP MCP endpoint.

Performs initialize (+ optional initialized notification) and tools/list.
Prints only safe information: status and tool names. Never prints tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_PROTOCOL = "2024-11-05"
REQUIRED_TOOLS = (
    "pipeshub_chat",
    "pipeshub_search",
    "pipeshub_sources",
    "pipeshub_directory",
    "pipeshub_download_record",
    "pipeshub_agents",
)


def _request(
    url: str,
    token: str,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
) -> tuple[int, dict[str, str], Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        resp_headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        status = exc.code
        if status in (401, 403):
            raise SystemExit(
                f"MCP auth failed with HTTP {status}. "
                "Re-run ./scripts/setup.sh with a valid credential."
            ) from None
        raise SystemExit(f"MCP request failed with HTTP {status}: {raw[:300]}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach MCP endpoint {url}: {exc.reason}") from None

    data = _parse_body(raw)
    return status, resp_headers, data


def _parse_body(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    # Streamable HTTP may return a single JSON object or SSE-framed JSON.
    if raw.startswith("event:") or "data:" in raw.splitlines()[0:3]:
        for line in raw.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    return json.loads(chunk)
        raise SystemExit(f"MCP SSE response contained no data payload: {raw[:300]}")
    return json.loads(raw)


def _rpc_result(data: Any, *, label: str) -> Any:
    if not isinstance(data, dict):
        raise SystemExit(f"{label}: expected JSON object, got {type(data).__name__}")
    if "error" in data and data["error"]:
        err = data["error"]
        if isinstance(err, dict):
            msg = err.get("message") or err
        else:
            msg = err
        raise SystemExit(f"{label} failed: {msg}")
    if "result" not in data:
        raise SystemExit(f"{label}: missing result field: {json.dumps(data)[:300]}")
    return data["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Full MCP URL, e.g. http://localhost:3000/mcp")
    parser.add_argument("--token", required=True, help="Bearer access token (not logged)")
    parser.add_argument(
        "--require-tool",
        nargs="+",
        dest="require_tools",
        default=None,
        help="Tool name(s) that must appear in tools/list. "
        f"Default: {' '.join(REQUIRED_TOOLS)}",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    require_tools = list(args.require_tools) if args.require_tools else list(REQUIRED_TOOLS)

    url = args.url.rstrip("/")
    token = args.token

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": DEFAULT_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "pipeshub-omnigent-mcp-check", "version": "0.1.0"},
        },
    }
    status, headers, init_data = _request(url, token, init_payload)
    init_result = _rpc_result(init_data, label="initialize")
    session_id = headers.get("mcp-session-id")

    # Best-effort notification; ignore failures in fully-stateless servers.
    try:
        _request(
            url,
            token,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            session_id=session_id,
        )
    except SystemExit:
        pass

    _, _, list_data = _request(
        url,
        token,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        session_id=session_id,
    )
    list_result = _rpc_result(list_data, label="tools/list")
    tools = list_result.get("tools") if isinstance(list_result, dict) else None
    if not isinstance(tools, list):
        raise SystemExit("tools/list did not return a tools array")

    names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
    missing = [tool for tool in require_tools if tool not in names]
    if missing:
        raise SystemExit(
            "Required tool(s) missing: "
            + ", ".join(missing)
            + f". Available: {', '.join(names) or '(none)'}"
        )

    server_name = None
    if isinstance(init_result, dict):
        info = init_result.get("serverInfo") or {}
        if isinstance(info, dict):
            server_name = info.get("name")

    payload = {
        "ok": True,
        "http_status": status,
        "server": server_name,
        "tools": names,
        "required_tools": require_tools,
    }
    if args.json:
        print(json.dumps(payload))
    else:
        print(f"MCP OK ({url})")
        if server_name:
            print(f"server: {server_name}")
        print("tools:")
        required_set = set(require_tools)
        for name in names:
            marker = " *" if name in required_set else ""
            print(f"  - {name}{marker}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
