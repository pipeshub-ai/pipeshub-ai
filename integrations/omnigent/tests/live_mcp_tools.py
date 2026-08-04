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


def _parse_body(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("event:") or "data:" in raw.splitlines()[0:3]:
        for line in raw.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    return json.loads(chunk)
        raise RuntimeError(f"SSE response had no data payload: {raw[:200]}")
    return json.loads(raw)


def _rpc(
    url: str,
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    rpc_id: int = 1,
    session_id: str | None = None,
    timeout: float = 120.0,
) -> tuple[Any, str | None]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
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
        raise RuntimeError(f"unreachable {url}: {exc.reason}") from None
    data = _parse_body(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"{method}: bad response type {type(data).__name__}")
    if data.get("error"):
        raise RuntimeError(f"{method} error: {data['error']}")
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
) -> tuple[Any, str | None]:
    return _rpc(
        url,
        token,
        "tools/call",
        {"name": name, "arguments": arguments},
        rpc_id=rpc_id,
        session_id=session_id,
    )


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
    except RuntimeError as exc:
        print(f"FAIL  initialize: {exc}")
        return 1

    try:
        listed, session_id = _rpc(
            url, token, "tools/list", {}, rpc_id=2, session_id=session_id
        )
        names = [
            t.get("name")
            for t in (listed or {}).get("tools", [])
            if isinstance(t, dict) and t.get("name")
        ]
        missing = [t for t in EXPECTED_TOOLS if t not in names]
        if missing:
            print(f"FAIL  tools/list missing {missing}; available={names}")
            return 1
        print(f"PASS  tools/list includes all {len(EXPECTED_TOOLS)} expected tools")
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
