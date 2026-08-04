#!/usr/bin/env python3
"""Materialize a runnable Omnigent agent with secrets resolved.

Omnigent 0.7.0 expands ``${VAR}`` in ``tools/mcp/*.yaml`` headers, but NOT in
inline MCP headers under ``tools.<name>.headers`` in ``config.yaml``. The
tool allowlist requires the inline form, so this helper writes a temporary
agent directory with the Bearer token already substituted.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path


def materialize(agent_dir: Path, token: str, mcp_url: str | None = None) -> Path:
    src_config = agent_dir / "config.yaml"
    if not src_config.is_file():
        raise SystemExit(f"missing {src_config}; run ./scripts/setup.sh first")

    text = src_config.read_text(encoding="utf-8")
    if mcp_url:
        text = re.sub(
            r"(?m)^(\s*url:\s*).*$",
            rf"\1{mcp_url}",
            text,
            count=1,
        )
    if "${PIPESHUB_MCP_TOKEN}" not in text and "Bearer " not in text:
        raise SystemExit(
            f"{src_config} has no ${'{'}PIPESHUB_MCP_TOKEN{'}'} placeholder and no Bearer token"
        )
    text = text.replace("${PIPESHUB_MCP_TOKEN}", token)

    out = Path(tempfile.mkdtemp(prefix="pipeshub-omnigent-agent-"))
    try:
        (out / "config.yaml").write_text(text, encoding="utf-8")
        agents_md = agent_dir / "AGENTS.md"
        if agents_md.is_file():
            shutil.copyfile(agents_md, out / "AGENTS.md")
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-dir", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--mcp-url")
    args = parser.parse_args()
    path = materialize(args.agent_dir.resolve(), args.token, args.mcp_url)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
