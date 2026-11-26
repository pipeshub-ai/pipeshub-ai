"""LS-1..LS-6 (C5.01, WS-14): a large grant's SDK stubs are built on first import, not at the turn's start.

§5 of the checklist: tool schema files are materialized at session start; generated SDK stubs are built lazily on
first import if large. Up to ``SDK_EAGER_MAX_TOOLS`` granted tools the worker renders every toolset module (WS-14).
Above it the session folder holds the package, its runtime, the worker's own renderer and an index of each toolset's
schema files; ``import pipeshub_tools.<toolset>`` renders that module in memory from the pinned schema files, and the
TypeScript index builds each toolset's namespace from the tools' names when it loads.
"""

from __future__ import annotations

import importlib
import inspect
import json
import linecache
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from harness_core.engines.controller import EngineControllerConfig
from harness_core.gateway.types import McpTool
from harness_engines.pipeshub import (
    schema_paths,
    session_files,
    session_links,
    tools_files,
)
from harness_engines.pipeshub import sdk as sdk_module
from harness_engines.pipeshub.sdk import (
    LAZY_INDEX,
    PACKAGE,
    SDK_EAGER_MAX_TOOLS,
    lazy,
    py_module,
    sdk_files,
    stubs,
)
from tests.unit.harness_engines.pipeshub.sdk_support import TSC, lay_out, need_node
from tests.unit.harness_engines.pipeshub.test_ws14_sdk_stubs import HOSTILE, TOOLS

ENDPOINTS = EngineControllerConfig(images={}).endpoints("pxh-model-aaaa", "pxh-tools-bbbb")
QUERY = {"type": "string", "description": "What to look for."}


def grant(n: int) -> tuple[McpTool, ...]:
    return tuple(
        McpTool(
            f"set{i % 25}__tool_{i}",
            f"Tool {i} reads records.",
            {"type": "object", "properties": {"q": QUERY, "limit": {"type": "integer"}}, "required": ["q"]},
        )
        for i in range(n)
    )


LARGE = grant(SDK_EAGER_MAX_TOOLS + 1)


def _python_root(folder: Path) -> Path:
    return folder / "session" / "sdk" / "python"


@pytest.fixture
def fresh() -> Any:  # noqa: ANN401
    """Imports from a laid-out folder with ``sys.modules``, ``sys.path`` and ``sys.meta_path`` restored after."""
    paths, finders = list(sys.path), list(sys.meta_path)

    def load(folder: Path, module: str = "") -> Any:  # noqa: ANN401
        sys.path.insert(0, str(_python_root(folder)))
        return importlib.import_module(f"{PACKAGE}.{module}" if module else PACKAGE)

    yield load
    sys.path[:] = paths
    sys.meta_path[:] = finders
    for name in [m for m in sys.modules if m == PACKAGE or m.startswith(f"{PACKAGE}.")]:
        del sys.modules[name]


def test_ls1_a_small_grant_renders_every_module_and_a_large_one_none() -> None:
    """LS-1: at or under the threshold every toolset module is in the folder (WS-14 as before); over it the folder
    holds the package, its runtime, the renderer and the index, and no toolset module in either language."""
    assert not lazy(grant(SDK_EAGER_MAX_TOOLS)) and lazy(LARGE)
    # The meta tools are not stubs and do not count.
    meta = (McpTool("search_tools", "Find.", {}), McpTool("call_tool", "Call.", {}))
    assert not lazy(grant(SDK_EAGER_MAX_TOOLS) + meta)
    small = session_files(grant(SDK_EAGER_MAX_TOOLS), ENDPOINTS)
    assert any(p.endswith("/python/pipeshub_tools/set0.py") for p in small)
    files = session_files(LARGE, ENDPOINTS)
    sdk_dir = session_links(files)["sdk"]
    shown = {p.removeprefix(f"{sdk_dir}/") for p in files if p.startswith(f"{sdk_dir}/")}
    assert shown == {
        "DIGEST",
        f"python/{PACKAGE}/__init__.py",
        f"python/{PACKAGE}/_runtime.py",
        f"python/{PACKAGE}/_render.py",
        f"python/{PACKAGE}/_lazy.py",
        f"python/{PACKAGE}/{LAZY_INDEX}",
        f"python/{PACKAGE}/py.typed",
        "ts/_runtime.mjs",
        "ts/_runtime.d.mts",
        "ts/index.mjs",
        "ts/index.d.mts",
    }
    # The index names each tool's schema file in this turn's tools folder.
    index = json.loads(files[f"{sdk_dir}/python/{PACKAGE}/{LAZY_INDEX}"])
    tools_dir = session_links(files)["tools"]
    assert index["tools"] == tools_dir
    paths = schema_paths(LARGE)
    for entry in index["modules"].values():
        for name, _, path in entry["tools"]:
            assert path == paths[name] and f"{tools_dir}/{path}" in files


def test_ls2_a_toolset_imported_lazily_is_the_module_a_small_grant_holds(tmp_path: Path, fresh: Any) -> None:  # noqa: ANN401
    """LS-2: ``import pipeshub_tools.<toolset>`` (and ``from pipeshub_tools import <toolset>``) gives the module
    the worker would have written: the same source, signatures and docstrings, from the pinned schema files."""
    folder = lay_out(tmp_path, LARGE, ENDPOINTS)
    package = fresh(folder)
    assert package.TOOLSETS == tuple(stubs(LARGE))
    eager = stubs(LARGE)
    for module in ("set0", "set13"):
        loaded = importlib.import_module(f"{PACKAGE}.{module}")
        assert "".join(linecache.getlines(f"<{PACKAGE}.{module}>")) == py_module(*eager[module])
        assert str(inspect.signature(loaded.__dict__[eager[module][1][0].function])) == (
            "(*, q: 'str', limit: 'int | None' = None) -> '_rt.CallResult'"
        )
    attribute = package.set7
    assert attribute is sys.modules[f"{PACKAGE}.set7"] and (inspect.getdoc(attribute.tool_7) or "").startswith("Tool 7")
    with pytest.raises(AttributeError):
        _ = package.no_such_toolset
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{PACKAGE}.no_such_toolset")


def test_ls2_a_lazy_call_is_one_ph_tools_call(tmp_path: Path, fresh: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    """LS-2: a lazily rendered function hands the same tool name and arguments to the runtime as an eager one."""
    package = fresh(lay_out(tmp_path, LARGE, ENDPOINTS))
    seen: list[tuple[str, dict[str, Any]]] = []
    runtime = importlib.import_module(f"{PACKAGE}._runtime")
    monkeypatch.setattr(runtime, "invoke", lambda tool, given, optional: seen.append((tool, given)) or "ok")
    assert package.set3.tool_3(q="x") == "ok"
    assert seen == [("set3__tool_3", {"q": "x", "limit": None})]


def test_ls3_nothing_is_written_to_the_read_only_session_folder(tmp_path: Path, fresh: Any) -> None:  # noqa: ANN401
    """LS-3: the session folder is a read-only mount; rendering on import writes nothing (no module file, no
    bytecode) and works with every directory read-only."""
    folder = lay_out(tmp_path, LARGE, ENDPOINTS)
    before = sorted(str(p) for p in folder.rglob("*"))
    for path in folder.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        package = fresh(folder)
        assert package.set4.tool_4.__name__ == "tool_4"
    finally:
        for path in folder.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(stat.S_IRWXU)
    assert sorted(str(p) for p in folder.rglob("*")) == before


def test_ls4_hostile_names_render_lazily_exactly_as_eagerly(tmp_path: Path, fresh: Any) -> None:  # noqa: ANN401
    """LS-4: names and descriptions from connected apps and MCP servers go through the same renderer (SDK-4's
    escaping) whether the module is written by the worker or on import; no other code runs."""
    tools = (*TOOLS, *HOSTILE, *LARGE)
    package = fresh(lay_out(tmp_path, tools, ENDPOINTS))
    eager = stubs(tools)
    for module in eager:
        if module.startswith("set"):
            continue
        importlib.import_module(f"{PACKAGE}.{module}")
        assert "".join(linecache.getlines(f"<{PACKAGE}.{module}>")) == py_module(*eager[module]), module
    assert not Path("/tmp/pwned").exists()
    assert set(package.TOOLSETS) == set(eager)


def test_ls5_the_typescript_index_builds_each_namespace_and_type_checks(tmp_path: Path) -> None:
    """LS-5: ``import { <toolset> } from ".../index.mjs"`` works as for a small grant; each function is one
    ``invoke`` of the full tool name; ``tsc --strict`` accepts a call and refuses an unknown function."""
    node = need_node()
    tools = (*TOOLS, *HOSTILE, *LARGE)
    folder = lay_out(tmp_path, tools, ENDPOINTS)
    index = folder / "session" / "sdk" / "ts" / "index.mjs"
    text = index.read_text()
    assert 'export const jira = _ns({ create_issue: "jira__create_issue", search_issues: "jira__search_issues" });' in text
    script = (
        f"const m = await import({json.dumps(str(index))});"
        "console.log(JSON.stringify({jira: Object.keys(m.jira), set0: Object.keys(m.set0).length, frozen: Object.isFrozen(m.jira), toolsets: m.TOOLSETS.length}))"
    )
    loaded = subprocess.run([node, "--input-type=module", "-e", script], check=True, capture_output=True, text=True, timeout=30)
    assert json.loads(loaded.stdout) == {
        "jira": ["create_issue", "search_issues"],
        "set0": len(stubs(LARGE)["set0"][1]),
        "frozen": True,
        "toolsets": len(stubs(tools)),
    }
    if not TSC.exists():
        pytest.skip("backend/nodejs/apps/node_modules (tsc) missing: run npm ci")
    good = tmp_path / "good.mts"
    good.write_text(f'import {{ jira }} from {json.dumps(str(index))};\nconst r = await jira.search_issues({{ jql: "x" }});\nconsole.log(r.text);\n')
    bad = tmp_path / "bad.mts"
    bad.write_text(f'import {{ jira }} from {json.dumps(str(index))};\nawait jira.no_such_tool({{}});\n')
    flags = ["--noEmit", "--strict", "--module", "nodenext", "--moduleResolution", "nodenext", "--target", "es2022", "--skipLibCheck", "false"]
    env = {"PATH": f"{Path(node).parent}:/usr/bin:/bin"}
    checked = subprocess.run([str(TSC), *flags, str(good)], capture_output=True, text=True, timeout=120, env=env)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    refused = subprocess.run([str(TSC), *flags, str(bad)], capture_output=True, text=True, timeout=120, env=env)
    assert refused.returncode != 0 and "no_such_tool" in refused.stdout


def test_ls6_a_large_grant_starts_a_turn_with_a_fraction_of_the_bytes() -> None:
    """LS-6: the point of the row: a 3,000-tool grant's turn start writes the schema files and a small SDK folder
    (no module per toolset), so its stubs cost little to render and to copy into the sandbox at every turn.

    The cost is held in bytes, which the renderer decides; a wall-clock bound measured the host's load instead
    (5.3 s on a busy gate for a render that takes milliseconds alone)."""
    many = grant(3000)
    folder, paths = tools_files(many)[0], schema_paths(many)
    _, on_use = sdk_files(many, (folder, paths))
    _, eager = sdk_files(many)
    lazy_bytes, eager_bytes = sum(map(len, on_use.values())), sum(map(len, eager.values()))
    assert lazy_bytes * 8 < eager_bytes, (lazy_bytes, eager_bytes)
    assert lazy_bytes < 512 * 1024, lazy_bytes


def test_ls6_the_digest_differs_for_stubs_built_on_use() -> None:
    """SDK-3 holds for both layouts: the same large grant names the same folder, and the folder rendered on use is
    not mistaken for the eager one (its digest covers the renderer and the tools folder it reads)."""
    folder, paths = tools_files(LARGE)[0], schema_paths(LARGE)
    first = sdk_files(LARGE, (folder, paths))
    assert sdk_files(tuple(reversed(LARGE)), (folder, paths)) == first
    assert first[0] != sdk_files(LARGE)[0]
    assert sdk_module.sdk_digest(LARGE, folder) != sdk_module.sdk_digest(LARGE)
    assert os.path.basename(first[0]).startswith("sdk-")
