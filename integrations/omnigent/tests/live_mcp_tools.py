#!/usr/bin/env python3
"""Live smoke test for every PipesHub MCP tool used by the Omnigent agent.

Requires a configured local credentials file from ``./scripts/setup.sh``:
  integrations/omnigent/.local/credentials.env

Safe by default:
  - never prints tokens
  - download is only attempted when search returns a recordId
  - chat uses a tiny internal_search query

Exit codes:
  0  all exercised tools succeeded (or download skipped with reason)
  1  failure
  2  skipped (no credentials / unreachable) — treated as skip, not CI failure
     when run via ``./tests/run_all.sh`` without ``--require-live``
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS = ROOT / ".local" / "credentials.env"

EXPECTED_TOOLS = [
    "pipeshub_chat",
    "pipeshub_search",
    "pipeshub_sources",
    "pipeshub_directory",
    "pipeshub_download_record",
    "pipeshub_agents",
]


def _load_credentials(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        env[key] = shlex.split(raw)[0] if raw else ""
    return env


class UnreachableError(RuntimeError):
    """MCP endpoint could not be reached (treat as live-test skip)."""


def _parse_body(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    lines = raw.splitlines()
    if raw.startswith("event:") or any(line.startswith("data:") for line in lines):
        for line in lines:
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(f"SSE data was not valid JSON: {exc}") from None
        raise RuntimeError(f"SSE response had no data payload: {raw[:200]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"response was not valid JSON: {exc}") from None


def _rpc(
    url: str,
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    rpc_id: int | None = 1,
    session_id: str | None = None,
    timeout: float = 120.0,
    allow_empty: bool = False,
) -> tuple[Any, str | None]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if rpc_id is not None:
        payload["id"] = rpc_id
    if params is not None:
        payload["params"] = params
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {exc.code} on {method}: {body}") from None
    except urllib.error.URLError as exc:
        raise UnreachableError(f"unreachable {url}: {exc.reason}") from None

    if allow_empty and not raw.strip():
        return None, sid or session_id

    data = _parse_body(raw)
    if data is None and allow_empty:
        return None, sid or session_id
    if not isinstance(data, dict):
        raise RuntimeError(f"{method}: bad response type {type(data).__name__}")
    if data.get("error"):
        raise RuntimeError(f"{method} error: {data['error']}")
    if "result" not in data and not allow_empty:
        raise RuntimeError(f"{method}: missing result field")
    return data.get("result"), sid or session_id

def _tool_text(result: Any, *, limit: int | None = 2000) -> str:
    if not isinstance(result, dict):
        text = str(result)
    else:
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        else:
            text = json.dumps(result)
    if limit is not None:
        return text[:limit]
    return text


def _call_tool(
    url: str, token: str, session_id: str | None, name: str, arguments: dict[str, Any], rpc_id: int
) -> tuple[dict[str, Any], str | None]:
    result, sid = _rpc(
        url,
        token,
        "tools/call",
        {"name": name, "arguments": arguments},
        rpc_id=rpc_id,
        session_id=session_id,
    )
    if not isinstance(result, dict):
        raise RuntimeError(f"tools/call {name}: expected object result, got {type(result).__name__}")
    return result, sid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials",
        type=Path,
        default=CREDENTIALS,
        help="Path to credentials.env from setup.sh",
    )
    parser.add_argument(
        "--skip-chat",
        action="store_true",
        help="Skip pipeshub_chat (slower / uses org LLM credits)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip pipeshub_download_record even if a recordId is available",
    )
    args = parser.parse_args()

    if not args.credentials.is_file():
        print(f"SKIP  no credentials at {args.credentials}; run ./scripts/setup.sh first")
        return 2

    env = _load_credentials(args.credentials)
    url = (env.get("PIPESHUB_MCP_URL") or "").rstrip("/")
    token = env.get("PIPESHUB_MCP_TOKEN") or ""
    if not url or not token:
        print("SKIP  credentials file missing PIPESHUB_MCP_URL or PIPESHUB_MCP_TOKEN")
        return 2

    print(f"Live MCP smoke against {url}")
    fails = 0

    try:
        init, session_id = _rpc(
            url,
            token,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pipeshub-omnigent-live-mcp", "version": "0.2.0"},
            },
            rpc_id=1,
        )
        print("PASS  initialize")
    except UnreachableError as exc:
        print(f"SKIP  initialize: {exc}")
        return 2
    except RuntimeError as exc:
        print(f"FAIL  initialize: {exc}")
        return 1

    # Match mcp-check.py / MCP Streamable HTTP clients: notify before tools/list.
    try:
        _rpc(
            url,
            token,
            "notifications/initialized",
            {},
            rpc_id=None,
            session_id=session_id,
            allow_empty=True,
        )
    except UnreachableError as exc:
        print(f"SKIP  notifications/initialized: {exc}")
        return 2
    except RuntimeError:
        # Best-effort on fully stateless servers.
        pass

    try:
        listed, session_id = _rpc(
            url, token, "tools/list", {}, rpc_id=2, session_id=session_id
        )
        if not isinstance(listed, dict):
            print(f"FAIL  tools/list: expected object result, got {type(listed).__name__}")
            return 1
        names = [
            t.get("name")
            for t in listed.get("tools", [])
            if isinstance(t, dict) and t.get("name")
        ]
        missing = [t for t in EXPECTED_TOOLS if t not in names]
        if missing:
            print(f"FAIL  tools/list missing {missing}; available={names}")
            return 1
        print(f"PASS  tools/list includes all {len(EXPECTED_TOOLS)} expected tools")
    except UnreachableError as exc:
        print(f"SKIP  tools/list: {exc}")
        return 2
    except RuntimeError as exc:
        print(f"FAIL  tools/list: {exc}")
        return 1

    # --- pipeshub_sources ---
    try:
        result, session_id = _call_tool(
            url,
            token,
            session_id,
            "pipeshub_sources",
            {"include": ["sources", "llmModels"]},
            rpc_id=10,
        )
        text = _tool_text(result)
        if result.get("isError"):
            raise RuntimeError(text)
        print("PASS  pipeshub_sources")
    except UnreachableError as exc:
        print(f"SKIP  pipeshub_sources: {exc}")
        return 2
    except RuntimeError as exc:
        print(f"FAIL  pipeshub_sources: {exc}")
        fails += 1

    # --- pipeshub_directory (whoami) ---
    try:
        result, session_id = _call_tool(
            url,
            token,
            session_id,
            "pipeshub_directory",
            {"action": "whoami"},
            rpc_id=11,
        )
        text = _tool_text(result)
        if result.get("isError"):
            raise RuntimeError(text)
        print("PASS  pipeshub_directory (whoami)")
    except UnreachableError as exc:
        print(f"SKIP  pipeshub_directory: {exc}")
        return 2
    except RuntimeError as exc:
        print(f"FAIL  pipeshub_directory: {exc}")
        fails += 1

    # --- pipeshub_agents ---
    try:
        result, session_id = _call_tool(
            url, token, session_id, "pipeshub_agents", {}, rpc_id=12
        )
        text = _tool_text(result)
        if result.get("isError"):
            raise RuntimeError(text)
        print("PASS  pipeshub_agents")
    except UnreachableError as exc:
        print(f"SKIP  pipeshub_agents: {exc}")
        return 2
    except RuntimeError as exc:
        print(f"FAIL  pipeshub_agents: {exc}")
        fails += 1

    # --- pipeshub_search ---
    record_id = None
    try:
        result, session_id = _call_tool(
            url,
            token,
            session_id,
            "pipeshub_search",
            {"query": "bug bash", "limit": 3},
            rpc_id=13,
        )
        text = _tool_text(result, limit=None)
        if isinstance(result, dict) and result.get("isError"):
            raise RuntimeError(text[:500])
        try:
            parsed = json.loads(text) if text.strip().startswith("{") else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"search result was not JSON: {exc}") from None
        hits = parsed.get("hits") if isinstance(parsed, dict) else None
        if isinstance(hits, list) and hits and isinstance(hits[0], dict):
            record_id = hits[0].get("recordId")
        hit_count = len(hits) if isinstance(hits, list) else 0
        print(f"PASS  pipeshub_search (hits={hit_count})")
    except UnreachableError as exc:
        print(f"SKIP  pipeshub_search: {exc}")
        return 2
    except RuntimeError as exc:
        print(f"FAIL  pipeshub_search: {exc}")
        fails += 1

    # --- pipeshub_download_record ---
    if args.skip_download:
        print("SKIP  pipeshub_download_record (--skip-download)")
    elif not record_id:
        print("SKIP  pipeshub_download_record (no recordId from search)")
    else:
        try:
            result, session_id = _call_tool(
                url,
                token,
                session_id,
                "pipeshub_download_record",
                {"recordId": record_id},
                rpc_id=14,
            )
            text = _tool_text(result)
            if result.get("isError"):
                raise RuntimeError(text)
            print("PASS  pipeshub_download_record")
        except UnreachableError as exc:
            print(f"SKIP  pipeshub_download_record: {exc}")
            return 2
        except RuntimeError as exc:
            print(f"FAIL  pipeshub_download_record: {exc}")
            fails += 1

    # --- pipeshub_chat ---
    if args.skip_chat:
        print("SKIP  pipeshub_chat (--skip-chat)")
    else:
        try:
            result, session_id = _call_tool(
                url,
                token,
                session_id,
                "pipeshub_chat",
                {
                    "query": "Reply with one short sentence: what is PipesHub?",
                    "chatMode": "internal_search",
                },
                rpc_id=15,
            )
            text = _tool_text(result)
            if result.get("isError"):
                raise RuntimeError(text)
            if not text.strip():
                raise RuntimeError("empty chat result")
            print("PASS  pipeshub_chat")
        except UnreachableError as exc:
            print(f"SKIP  pipeshub_chat: {exc}")
            return 2
        except RuntimeError as exc:
            print(f"FAIL  pipeshub_chat: {exc}")
            fails += 1

    if fails:
        print(f"\n{fails} live MCP tool check(s) failed.")
        return 1
    print("\nAll live MCP tool checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
