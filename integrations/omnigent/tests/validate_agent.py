#!/usr/bin/env python3
"""Offline validation of the Omnigent agent definition.

Uses Omnigent's parser/validator when available. Falls back to a structural
check of config.yaml.example so CI can still catch broken examples without
Omnigent installed.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "agent" / "config.yaml.example"
AGENTS_MD = ROOT / "agent" / "AGENTS.md"
PLACEHOLDER = "__PIPESHUB_MCP_URL__"
SAMPLE_URL = "http://localhost:3000/mcp"
EXPECTED_TOOLS = [
    "pipeshub_chat",
    "pipeshub_search",
    "pipeshub_sources",
    "pipeshub_directory",
    "pipeshub_download_record",
    "pipeshub_agents",
]


def validate_with_omnigent(agent_dir: Path) -> None:
    from omnigent.spec.parser import parse
    from omnigent.spec.validator import validate

    spec = parse(agent_dir, expand_env=False)
    result = validate(spec)
    if not result.valid:
        for err in result.errors:
            print(f"{err.path}: {err.message}", file=sys.stderr)
        raise SystemExit(1)
    if not spec.mcp_servers:
        raise SystemExit("agent defines no MCP servers")
    mcp = spec.mcp_servers[0]
    if mcp.url != SAMPLE_URL:
        raise SystemExit(f"unexpected MCP url after substitution: {mcp.url!r}")
    if mcp.tools != EXPECTED_TOOLS:
        raise SystemExit(f"expected tools allowlist {EXPECTED_TOOLS!r}, got {mcp.tools!r}")
    print(f"Omnigent validation OK: {spec.name} -> {mcp.url} tools={mcp.tools}")


def validate_structure(example_text: str) -> None:
    required = [
        "spec_version: 1",
        "name: pipeshub-knowledge",
        "instructions: AGENTS.md",
        "type: mcp",
        "transport: http",
        PLACEHOLDER,
        "PIPESHUB_MCP_TOKEN",
        "executor:",
        "harness: claude-sdk",
        *EXPECTED_TOOLS,
    ]
    missing = [item for item in required if item not in example_text]
    if missing:
        raise SystemExit(f"config.yaml.example missing required pieces: {missing}")
    if not AGENTS_MD.is_file():
        raise SystemExit(f"missing {AGENTS_MD}")
    agents = AGENTS_MD.read_text(encoding="utf-8")
    if "pipeshub__pipeshub_chat" not in agents:
        raise SystemExit("AGENTS.md must route to pipeshub__pipeshub_chat")
    print("Structural validation OK (Omnigent package not installed)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-omnigent",
        action="store_true",
        help="Fail if the Omnigent Python package is unavailable",
    )
    args = parser.parse_args()

    if not EXAMPLE.is_file():
        raise SystemExit(f"missing {EXAMPLE}")
    text = EXAMPLE.read_text(encoding="utf-8")
    if PLACEHOLDER not in text:
        raise SystemExit(f"{EXAMPLE} missing {PLACEHOLDER}")

    import re

    rendered = re.sub(
        r'(?m)^(\s*url:\s*)["\']?__PIPESHUB_MCP_URL__["\']?\s*$',
        rf"\1{SAMPLE_URL}",
        text,
        count=1,
    )
    if SAMPLE_URL not in rendered:
        raise SystemExit("failed to substitute MCP url placeholder for validation")

    with tempfile.TemporaryDirectory() as tmp:
        agent_dir = Path(tmp) / "agent"
        agent_dir.mkdir()
        (agent_dir / "config.yaml").write_text(rendered, encoding="utf-8")
        shutil.copyfile(AGENTS_MD, agent_dir / "AGENTS.md")
        # Only fall back when Omnigent itself is unavailable. ImportErrors from
        # parse()/validate() of a broken install should still fail validation.
        try:
            import omnigent.spec.parser  # noqa: F401
            import omnigent.spec.validator  # noqa: F401
        except ImportError:
            if args.require_omnigent:
                raise SystemExit(
                    "Omnigent package not installed. "
                    "Install with: uv pip install 'omnigent==0.7.0'"
                ) from None
            validate_structure(text)
        else:
            validate_with_omnigent(agent_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
