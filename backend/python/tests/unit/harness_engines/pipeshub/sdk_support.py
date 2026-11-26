"""The generated SDK stubs (WS-14) laid out on disk as a sandbox sees them, and three ways to call one tool with them:
``ph --json tools call`` (the session folder's ``ph``), the Python stub and the TypeScript stub, each its own process.

Every way answers with the same normalized outcome, ``{"ok": True, "data": {"tool", "text"}}`` or ``{"ok": False,
"error": {"code", "exit", "message"}}``, plus the process's exit code (``ph``'s; a stub's error carries ``exit``).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_engines.pipeshub import (
    FOLDER,
    HOOK_PROGRAM_FILE,
    SESSION_DIR,
    session_files,
    session_links,
)
from harness_engines.pipeshub.sdk import PACKAGE, PYTHON_DIR, SDK_DIR, TS_DIR, stubs

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import GatewayEndpoints
    from harness_core.gateway.types import McpTool

NODE_BIN = Path.home() / ".local" / "node" / "current" / "bin"
TSC = Path(__file__).resolve().parents[5] / "nodejs" / "apps" / "node_modules" / ".bin" / "tsc"

PY_CALLER = r"""
import importlib, json, sys
tool, module, function, raw = sys.argv[1], sys.argv[2], sys.argv[3], sys.stdin.read()
import pipeshub_tools
args = json.loads(raw)
try:
    if module:
        result = getattr(importlib.import_module(f"pipeshub_tools.{module}"), function)(**args)
    else:
        result = pipeshub_tools.call(tool, args)
    print(json.dumps({"ok": True, "data": {"tool": result.tool, "text": result.text}}))
except pipeshub_tools.CallError as failed:
    print(json.dumps({"ok": False, "error": {"code": failed.code, "exit": failed.exit, "message": failed.message}, "kind": type(failed).__name__}))
"""

TS_CALLER = r"""
const [index, tool, module, fn, raw] = process.argv.slice(1);
const sdk = await import(index);
const args = JSON.parse(raw);
try {
  const result = module ? await sdk[module][fn](args) : await sdk.call(tool, args);
  console.log(JSON.stringify({ ok: true, data: { tool: result.tool, text: result.text } }));
} catch (failed) {
  if (!(failed instanceof sdk.CallError)) throw failed;
  console.log(JSON.stringify({ ok: false, error: { code: failed.code, exit: failed.exit, message: failed.message }, kind: failed.constructor.name }));
}
"""


def node() -> str | None:
    return shutil.which("node") or (str(NODE_BIN / "node") if (NODE_BIN / "node").exists() else None)


def need_node() -> str:
    found = node()
    if found is None:
        if os.environ.get("PXH_REQUIRE_NODE") == "1":
            pytest.fail("PXH_REQUIRE_NODE=1 but node is not installed")
        pytest.skip("node is not installed")
    return found


def lay_out(root: Path, tools: tuple[McpTool, ...], endpoints: GatewayEndpoints) -> Path:
    """The turn's session folder under ``root`` as the controller seeds it (files and links); the ``/pipeshub`` root."""
    folder = root / FOLDER.lstrip("/")
    session = root / SESSION_DIR.lstrip("/")
    files = session_files(tools, endpoints)
    for path, content in files.items():
        target = session / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for name, target in session_links(files).items():
        link = session / name
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target)
    (folder / "tools").unlink(missing_ok=True)
    (folder / "tools").symlink_to("session/tools")
    return folder


def where(tools: tuple[McpTool, ...], tool: str) -> tuple[str, str]:
    """``(module, function)`` of ``tool``'s stub; ``("", "")`` when the grant has none (the generic ``call``)."""
    for module, (_, rendered) in stubs(tools).items():
        for stub in rendered:
            if stub.tool == tool:
                return module, stub.function
    return "", ""


def _env(folder: Path, extra: Mapping[str, str]) -> dict[str, str]:
    path = os.pathsep.join([str(NODE_BIN), os.environ.get("PATH", "/usr/bin:/bin")])
    return {"PATH": path, "HOME": str(folder.parent), "PIPESHUB_FOLDER": str(folder), **extra}


async def _run(argv: list[str], stdin: str, env: dict[str, str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )
    async with asyncio.timeout(25):
        out, err = await process.communicate(stdin.encode())
    assert process.returncode is not None
    return process.returncode, out.decode(), err.decode()


def _last_json(out: str, err: str) -> dict[str, Any]:
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines, err[-2000:]
    return json.loads(lines[-1])


async def through_ph(folder: Path, tool: str, arguments: Mapping[str, Any], extra: Mapping[str, str] = {}) -> tuple[dict[str, Any], int]:
    """``ph --json tools call <tool> --json -``, the session folder's ``ph``."""
    ph = folder / "session" / HOOK_PROGRAM_FILE
    code, out, err = await _run(
        [sys.executable, "-I", str(ph), "--json", "tools", "call", tool, "--json", "-"], json.dumps(arguments), _env(folder, extra)
    )
    envelope = _last_json(out, err)
    assert envelope.pop("command") == "tools.call"
    return envelope, code


async def through_python(
    folder: Path, tools: tuple[McpTool, ...], tool: str, arguments: Mapping[str, Any], extra: Mapping[str, str] = {}
) -> tuple[dict[str, Any], str | None]:
    """The Python stub of ``tool`` (the generic ``call`` for a tool the grant has none of); its outcome and error class."""
    module, function = where(tools, tool)
    env = {**_env(folder, extra), "PYTHONPATH": str(folder / "session" / SDK_DIR / PYTHON_DIR)}
    _, out, err = await _run([sys.executable, "-c", PY_CALLER, tool, module, function], json.dumps(arguments), env)
    answer = _last_json(out, err)
    return answer, answer.pop("kind", None)


async def through_typescript(
    folder: Path, tools: tuple[McpTool, ...], tool: str, arguments: Mapping[str, Any], extra: Mapping[str, str] = {}
) -> tuple[dict[str, Any], str | None]:
    """The TypeScript stub of ``tool``, run by Node from the generated ES modules."""
    module, function = where(tools, tool)
    index = folder / "session" / SDK_DIR / TS_DIR / "index.mjs"
    argv = [need_node(), "--input-type=module", "-e", TS_CALLER, str(index), tool, module, function, json.dumps(arguments)]
    _, out, err = await _run(argv, "", _env(folder, extra))
    answer = _last_json(out, err)
    return answer, answer.pop("kind", None)


__all__ = ["PACKAGE", "TSC", "lay_out", "need_node", "node", "through_ph", "through_python", "through_typescript", "where"]
