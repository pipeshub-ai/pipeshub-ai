#!/usr/bin/env python3
"""Offline contract: agent config/examples expose the full PipesHub MCP surface.

Fails if a tool is dropped from allowlists, AGENTS.md routing, or OAuth scopes.
No live PipesHub required.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = [
    "pipeshub_chat",
    "pipeshub_search",
    "pipeshub_sources",
    "pipeshub_directory",
    "pipeshub_download_record",
    "pipeshub_agents",
]

EXPECTED_OAUTH_SCOPES = [
    "semantic:write",
    "kb:read",
    "conversation:write",
    "conversation:chat",
    "agent:read",
    "agent:execute",
    "user:read",
    "team:read",
]


def _fail(msg: str) -> None:
    raise SystemExit(f"FAIL  {msg}")


def _pass(msg: str) -> None:
    print(f"PASS  {msg}")


def _tools_in_yaml_block(text: str) -> list[str]:
    """Extract ordered tool names under a `tools:` list in YAML-ish text."""
    # Match list items like `      - pipeshub_chat`
    return re.findall(r"(?m)^\s*-\s+(pipeshub_[a-z0-9_]+)\s*$", text)


def check_config_example() -> None:
    path = ROOT / "agent" / "config.yaml.example"
    text = path.read_text(encoding="utf-8")
    tools = _tools_in_yaml_block(text)
    if tools != EXPECTED_TOOLS:
        _fail(f"config.yaml.example tools={tools!r} expected {EXPECTED_TOOLS!r}")
    if "__PIPESHUB_MCP_URL__" not in text:
        _fail("config.yaml.example missing MCP URL placeholder")
    if "${PIPESHUB_MCP_TOKEN}" not in text:
        _fail("config.yaml.example missing PIPESHUB_MCP_TOKEN placeholder")
    if "transport: http" not in text:
        _fail("config.yaml.example missing transport: http")
    _pass("config.yaml.example allowlists all MCP tools")


def check_directory_mcp_example() -> None:
    path = ROOT / "agent" / "tools" / "mcp" / "pipeshub.yaml.example"
    text = path.read_text(encoding="utf-8")
    tools = _tools_in_yaml_block(text)
    if tools != EXPECTED_TOOLS:
        _fail(f"pipeshub.yaml.example tools={tools!r} expected {EXPECTED_TOOLS!r}")
    _pass("pipeshub.yaml.example allowlists all MCP tools")


def check_agents_md() -> None:
    text = (ROOT / "agent" / "AGENTS.md").read_text(encoding="utf-8")
    for tool in EXPECTED_TOOLS:
        namespaced = f"pipeshub__{tool}"
        if namespaced not in text:
            _fail(f"AGENTS.md missing namespaced tool {namespaced}")
    if "Default tool: `pipeshub__pipeshub_chat`" not in text:
        _fail("AGENTS.md must declare pipeshub__pipeshub_chat as the default tool")
    if "must use **chat**, not directory" not in text:
        _fail("AGENTS.md should steer who-is questions to chat, not directory")
    _pass("AGENTS.md documents every namespaced MCP tool (chat-first)")


def check_lib_scopes_and_required_tools() -> None:
    text = (ROOT / "scripts" / "lib.sh").read_text(encoding="utf-8")
    for scope in EXPECTED_OAUTH_SCOPES:
        if scope not in text:
            _fail(f"lib.sh OAUTH_SCOPES missing {scope}")
    for tool in EXPECTED_TOOLS:
        if tool not in text:
            _fail(f"lib.sh REQUIRED_TOOLS missing {tool}")
    _pass("lib.sh OAuth scopes and REQUIRED_TOOLS cover full MCP surface")


def check_mcp_check_defaults() -> None:
    text = (ROOT / "scripts" / "mcp-check.py").read_text(encoding="utf-8")
    for tool in EXPECTED_TOOLS:
        if f'"{tool}"' not in text and f"'{tool}'" not in text:
            _fail(f"mcp-check.py REQUIRED_TOOLS missing {tool}")
    if "nargs=" not in text and 'nargs="' not in text and "nargs='" not in text:
        # ensure multi-tool CLI still present
        if "nargs" not in text:
            _fail("mcp-check.py must accept multiple --require-tool values (nargs)")
    _pass("mcp-check.py defaults include all MCP tools")


def check_readme() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for tool in EXPECTED_TOOLS:
        if f"pipeshub__{tool}" not in text and tool not in text:
            _fail(f"README.md does not mention {tool}")
    _pass("README mentions the full MCP tool surface")


def main() -> int:
    check_config_example()
    check_directory_mcp_example()
    check_agents_md()
    check_lib_scopes_and_required_tools()
    check_mcp_check_defaults()
    check_readme()
    print()
    print("MCP contract checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
