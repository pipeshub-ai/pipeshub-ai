"""SDK-1..SDK-9 (WS-14, C3.15): typed Python and TypeScript stubs of the run's pinned grant, in the session folder.

The stubs are rendered with the rest of ``/pipeshub/session`` at each turn's start, so they name exactly the
granted tools; each function's signature and docstring come from its tool's schema and description. They are pure
glue over ``ph tools call`` (no network code, no credential), deterministic for a grant, and safe against tool
names and descriptions written by a connected app or an external MCP server. The parity of their answers with
``ph`` is C3.16, in ``tests/unit/harness/gateway/test_ws14_sdk_parity.py``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import subprocess
import sys
import typing
from pathlib import Path
from typing import Any

import pytest

from harness_core.engines.controller import EngineControllerConfig
from harness_core.gateway.types import McpTool
from harness_core.security.token_patterns import find_forbidden
from harness_engines import pipeshub
from harness_engines.claude_code import launch as claude_code_launch
from harness_engines.codex import launch as codex_launch
from harness_engines.opencode import launch as opencode_launch
from harness_engines.pipeshub import (
    AGENTS_POINTER,
    SDK_PATH,
    SESSION_DIR,
    session_files,
    session_links,
    tools_files,
)
from harness_engines.pipeshub.sdk import (
    DIGEST_FILE,
    META_TOOLS,
    PACKAGE,
    identifier,
    sdk_digest,
    sdk_files,
    stubs,
)
from tests.unit.harness_core.engines.test_controller import GRANTED, IMAGE, World, task
from tests.unit.harness_engines.pipeshub.sdk_support import TSC, lay_out, need_node

ENDPOINTS = EngineControllerConfig(images={}).endpoints("pxh-model-aaaa", "pxh-tools-bbbb")
JQL = {"type": "string", "description": "The JQL query, like `project = PP`."}
TOOLS = (
    McpTool(
        "jira__search_issues",
        "Search Jira issues with JQL.\nReturns keys and the fields asked for.",
        {
            "type": "object",
            "properties": {
                "jql": JQL,
                "maxResults": {"type": "integer", "description": "At most this many."},
                "fields": {"type": "array", "items": {"type": "string"}},
                "order": {"enum": ["asc", "desc"]},
                "class": {"type": ["string", "null"]},
            },
            "required": ["jql"],
        },
    ),
    McpTool("jira__create_issue", "Create a Jira issue.", {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}),
    McpTool("slack__send_message", "Send a Slack message.", {}),
    McpTool("knowledgegraph__search", "Search indexed knowledge by meaning.", {"type": "object", "properties": {"query": {"type": "string"}}}),
    McpTool("search_tools", "Find tools.", {}),
    McpTool("call_tool", "Call a deferred tool.", {}),
)
# Written by a connected app or an external MCP server: none of it may become code.
HOSTILE = (
    McpTool(
        'evil__x"""; import os; os.system("touch /tmp/pwned") #',
        'Ends early? """\nimport os\nos.system("touch /tmp/pwned")\n"""  */ require("child_process").exec("x") /* \\ \x00 ',
        {
            "type": "object",
            "properties": {
                'a"); os.system("x"); ("': {"type": "string", "description": '""" */ evil'},
                "import": {"type": "integer"},
                "frozenset": {"type": "boolean"},
                "9lives": {"enum": ['"""', "*/", "a'b"]},
            },
            "required": ["import"],
        },
    ),
    McpTool("evil__x2", "Second.", {}),
    McpTool("_runtime__call", "A toolset named like the package's own parts.", {}),
    McpTool("class__delete", "Keywords of both languages.", {}),
)


def _import(sdk_root: Path, module: str) -> Any:  # noqa: ANN401
    """Imports a generated module from a laid-out folder, leaving ``sys.modules`` and ``sys.path`` as they were."""
    sys.path.insert(0, str(sdk_root))
    try:
        return importlib.import_module(f"{PACKAGE}.{module}" if module else PACKAGE)
    finally:
        sys.path.remove(str(sdk_root))


@pytest.fixture
def imported(tmp_path: Path) -> Any:  # noqa: ANN401
    yield lambda tools, module: _import(lay_out(tmp_path, tools, ENDPOINTS) / "session" / "sdk" / "python", module)
    for name in [m for m in sys.modules if m == PACKAGE or m.startswith(f"{PACKAGE}.")]:
        del sys.modules[name]


def test_sdk1_only_the_granted_tools_appear_one_module_per_toolset() -> None:
    """SDK-1 (C3.15): one module per toolset, one function per granted tool, and nothing else: no meta tool (``ph``
    refuses ``search_tools`` and ``call_tool`` by name), no tool the grant does not hold."""
    modules = stubs(TOOLS)
    assert {m: [s.function for s in r] for m, (_, r) in modules.items()} == {
        "jira": ["create_issue", "search_issues"],
        "knowledgegraph": ["search"],
        "slack": ["send_message"],
    }
    _, files = sdk_files(TOOLS)
    shown = b"".join(content for path, content in files.items() if "_runtime" not in path)
    assert not any(name.encode() in shown for name in META_TOOLS)
    narrowed = tuple(t for t in TOOLS if t.name != "jira__create_issue")
    _, fewer = sdk_files(narrowed)
    assert b"create_issue" not in b"".join(fewer.values())
    assert {p for p in files if p.startswith(("python/", "ts/"))} >= {
        "python/pipeshub_tools/__init__.py", "python/pipeshub_tools/_runtime.py", "python/pipeshub_tools/jira.py",
        "python/pipeshub_tools/py.typed", "ts/index.mjs", "ts/index.d.mts", "ts/_runtime.mjs", "ts/_runtime.d.mts",
        "ts/jira.mjs", "ts/jira.d.mts", "ts/slack.mjs", "ts/knowledgegraph.d.mts",
    }


def test_sdk2_signatures_and_docstrings_come_from_the_tool_schemas(imported: Any) -> None:  # noqa: ANN401
    """SDK-2 (C3.15): keyword arguments named and typed from the input schema (required first, optional ``None``),
    a name that is no identifier mapped to one, and the description and each argument's in the docstring."""
    jira = imported(TOOLS, "jira")
    signature = inspect.signature(jira.search_issues)
    assert list(signature.parameters) == ["jql", "class_", "fields", "max_results", "order"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in signature.parameters.values())
    assert signature.parameters["jql"].default is inspect.Parameter.empty
    assert all(signature.parameters[n].default is None for n in ("class_", "fields", "max_results", "order"))
    hints = typing.get_type_hints(jira.search_issues)
    assert hints["jql"] is str
    assert hints["max_results"] == int | None and hints["fields"] == list[str] | None
    assert hints["order"] == typing.Literal["asc", "desc"] | None and hints["class_"] == str | None
    assert hints["return"].__name__ == "CallResult"
    doc = inspect.getdoc(jira.search_issues) or ""
    assert doc.startswith("Search Jira issues with JQL.\nReturns keys and the fields asked for.")
    assert "jql (str): The JQL query, like `project = PP`." in doc
    assert "max_results (int, optional) (argument 'maxResults'): At most this many." in doc
    assert "ph tools call jira__search_issues" in doc and "ApprovalPending" in doc
    # A tool whose schema names no argument takes any keyword arguments.
    slack = imported(TOOLS, "slack")
    assert [p.kind for p in inspect.signature(slack.send_message).parameters.values()] == [inspect.Parameter.VAR_KEYWORD]
    package = imported(TOOLS, "")
    assert package.TOOLSETS == ("jira", "knowledgegraph", "slack") and callable(package.call)


def test_sdk2_the_typescript_types_come_from_the_same_schemas() -> None:
    """SDK-2 (C3.15) for TypeScript: one declared function per tool over its arguments' object type, with JSDoc."""
    _, files = sdk_files(TOOLS)
    dts = files["ts/jira.d.mts"].decode()
    assert (
        "export declare function search_issues(args: { jql: string; class?: string | null; fields?: Array<string>; "
        'maxResults?: number; order?: "asc" | "desc" }): Promise<CallResult>;'
    ) in dts
    assert " * Search Jira issues with JQL.\n * Returns keys and the fields asked for." in dts
    assert " * @param args.maxResults At most this many." in dts
    assert "export declare function send_message(args?: Record<string, unknown>): Promise<CallResult>;" in files["ts/slack.d.mts"].decode()
    assert 'return invoke("jira__search_issues", args);' in files["ts/jira.mjs"].decode()
    index = files["ts/index.mjs"].decode()
    assert 'export * as jira from "./jira.mjs";' in index and 'export const TOOLSETS = ["jira", "knowledgegraph", "slack"];' in index


def test_sdk2_the_typescript_stubs_type_check_and_load(tmp_path: Path) -> None:
    """SDK-2: ``tsc --strict`` accepts the declarations and a script using them, and refuses a wrong argument type;
    Node loads every generated module."""
    node = need_node()
    folder = lay_out(tmp_path, TOOLS + HOSTILE, ENDPOINTS)
    ts = folder / "session" / "sdk" / "ts"
    for module in sorted(ts.glob("*.mjs")):
        subprocess.run([node, "--check", str(module)], check=True, timeout=30)
    loaded = subprocess.run(
        [node, "--input-type=module", "-e", f"const m = await import({json.dumps(str(ts / 'index.mjs'))}); console.log(JSON.stringify(Object.keys(m.jira)))"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    assert json.loads(loaded.stdout) == ["create_issue", "search_issues"]
    if not TSC.exists():
        pytest.skip("backend/nodejs/apps/node_modules (tsc) missing: run npm ci")
    good = tmp_path / "good.mts"
    good.write_text(
        f'import {{ jira, ApprovalPending }} from {json.dumps(str(ts / "index.mjs"))};\n'
        'const r = await jira.search_issues({ jql: "project = PP", maxResults: 5, order: "asc" });\n'
        "const text: string = r.text;\nconsole.log(text, r.json(), ApprovalPending.name);\n"
    )
    bad = tmp_path / "bad.mts"
    bad.write_text(f'import {{ jira }} from {json.dumps(str(ts / "index.mjs"))};\nawait jira.search_issues({{ jql: 1 }});\n')
    flags = ["--noEmit", "--strict", "--module", "nodenext", "--moduleResolution", "nodenext", "--target", "es2022", "--skipLibCheck", "false"]
    path = {"PATH": f"{Path(node).parent}:/usr/bin:/bin"}
    checked = subprocess.run([str(TSC), *flags, str(good)], capture_output=True, text=True, timeout=120, env=path)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    refused = subprocess.run([str(TSC), *flags, str(bad)], capture_output=True, text=True, timeout=120, env=path)
    assert refused.returncode != 0 and "not assignable to type 'string'" in refused.stdout


def test_sdk3_the_same_grant_renders_the_same_bytes_and_another_grant_another_folder() -> None:
    """SDK-3 (C3.15, WS-02/03): deterministic in any order, named after a digest of the grant, the generator and
    the runtimes; the digest is written down (``DIGEST``, ``SDK_DIGEST``)."""
    name, files = sdk_files(TOOLS)
    assert sdk_files(tuple(reversed(TOOLS))) == (name, files)
    digest = sdk_digest(TOOLS)
    assert name == f"sdk-{digest[:16]}" and files[DIGEST_FILE] == f"sha256:{digest}\n".encode()
    assert f"SDK_DIGEST = {digest!r}".encode() in files["python/pipeshub_tools/__init__.py"]
    assert f'SDK_DIGEST = "{digest}"'.encode() in files["ts/index.mjs"]
    changed = (McpTool(TOOLS[1].name, "Create a Jira issue in a project.", TOOLS[1].input_schema), *TOOLS[:1], *TOOLS[2:])
    assert sdk_files(changed)[0] != name and sdk_files(TOOLS[:3])[0] != name
    # The meta tools are not stubs, so they do not move the digest.
    assert sdk_digest(TOOLS[:4]) == digest


def test_sdk4_hostile_names_and_descriptions_never_become_code(tmp_path: Path, imported: Any) -> None:  # noqa: ANN401
    """SDK-4: a tool name, description or argument name from a connected app is written only as an escaped string or
    comment; every module parses, holds only its imports and functions, and importing it runs nothing."""
    _, files = sdk_files(HOSTILE)
    for path, content in files.items():
        if not path.endswith(".py") or "_runtime" in path:
            continue
        tree = ast.parse(content.decode())
        kinds = {type(node).__name__ for node in tree.body}
        assert kinds <= {"Expr", "ImportFrom", "Import", "FunctionDef", "Assign"}, (path, kinds)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                assert ast.unparse(node.func) in ("_rt.invoke", "_rt.call", "frozenset"), (path, ast.unparse(node))
    evil = imported(HOSTILE, "evil")
    first = "x_import_os_os_system_touch_tmp_pwned"
    assert sorted(n for n, _ in inspect.getmembers(evil, inspect.isfunction)) == sorted([first, "x2"])
    assert list(inspect.signature(getattr(evil, first)).parameters) == ["import_", "t_9lives", "a_os_system_x", "frozenset_"]
    doc = inspect.getdoc(getattr(evil, first)) or ""
    assert 'Ends early? """\nimport os' in doc and "\x00" not in doc and " " not in doc
    assert not Path("/tmp/pwned").exists()
    assert set(stubs(HOSTILE)) == {"evil", "runtime", "class_"}
    for path in ("ts/evil.mjs", "ts/evil.d.mts"):
        text = files[path].decode()
        # Outside string literals, one comment per stub, each ended only by the generator: the tool's ``*/`` is broken.
        code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)
        assert code.count("*/") == code.count("/**") == 2 and "*\\/ require" in text, path
    assert 'invoke("evil__x\\"\\"\\"; import os; os.system(\\"touch /tmp/pwned\\") #", args)' in files["ts/evil.mjs"].decode()


def test_sdk5_the_stubs_hold_no_credential_and_no_network_path_of_their_own() -> None:
    """SDK-5 (C3.15): nothing in the stubs is a token or the attempt's placeholder, and their runtimes open no
    connection: the only way out is ``ph`` (the session folder's ``ph.py``), so it is ``ph``'s gateway, placeholder
    and egress proxy."""
    files = session_files(TOOLS, ENDPOINTS)
    sdk_paths = [p for p in files if p.startswith("sdk-")]
    assert sdk_paths
    for path in sdk_paths:
        text = files[path].decode()
        assert find_forbidden(text) == () and ENDPOINTS.tools_placeholder not in text and "Bearer" not in text, path
    runtime = files[next(p for p in sdk_paths if p.endswith("_runtime.py"))].decode()
    imported_modules = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(runtime))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert imported_modules <= {"__future__", "importlib", "io", "json", "os", "sys", "dataclasses", "typing", "collections", "types"}
    assert '["--json", "tools", "call", tool, "--json", "-"]' in runtime and '"ph.py"' in runtime
    mjs = files[next(p for p in sdk_paths if p.endswith("_runtime.mjs"))].decode()
    imports = sorted(line for line in mjs.splitlines() if line.startswith("import "))
    assert imports == [
        'import { dirname, join } from "node:path";',
        'import { existsSync, realpathSync } from "node:fs";',
        'import { fileURLToPath } from "node:url";',
        'import { spawn } from "node:child_process";',
    ]
    assert "fetch(" not in mjs and '"--json", "tools", "call", tool, "--json", "-"' in mjs


def test_sdk6_the_session_folder_links_sdk_to_this_grants_stubs() -> None:
    """SDK-6 (C3.15): the stubs live in the session folder under ``sdk-<digest>`` with an ``sdk`` link to them, as
    ``tools``; a re-seeded sandbox with another grant switches the link at once."""
    files = session_files(TOOLS, ENDPOINTS)
    links = session_links(files)
    assert links["sdk"] == sdk_files(TOOLS)[0] and links["tools"] == tools_files(TOOLS)[0]
    other = session_links(session_files(TOOLS[:2], ENDPOINTS))
    assert other["sdk"] != links["sdk"]
    assert SDK_PATH == f"{SESSION_DIR}/sdk"


async def test_sdk6_each_turn_renders_the_stubs_from_the_runs_pinned_grant() -> None:
    """SDK-6 (C3.15): the controller seeds the stubs with the turn's folder, from the grant the run is pinned to, so
    a pin or a resume (every turn start) renders them again."""
    world = World(config_overrides={"tool_folder": pipeshub.pipeshub_tool_folder()}, image_files={IMAGE: {"/pipeshub/VERSION": b"1\n"}})
    outcome = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert outcome.reason == "completed"
    seen = world.adapter.files_at_open[0]  # pyright: ignore[reportAttributeAccessIssue]
    folder = seen[f"{SESSION_DIR}/sdk"].decode()
    assert folder.startswith("sdk-") and f"{SESSION_DIR}/{folder}/ts/index.mjs" in seen
    modules = [c.decode() for p, c in seen.items() if p.startswith(f"{SESSION_DIR}/{folder}/python/") and p.endswith(".py")]
    stubbed = sorted(m for text in modules for m in re.findall(r"_rt\.(?:invoke|call)\('([^']+)'", text))
    assert stubbed == sorted(t.name for t in GRANTED) and stubbed


def test_sdk7_engines_are_told_where_the_stubs_are() -> None:
    """SDK-7: every engine's base prompt (Claude Code, Codex, opencode and its ACP profile) names the stubs next to
    ``ph``, and the turn's ``tools/INDEX.md`` says how to import them."""
    assert SDK_PATH in AGENTS_POINTER and "/pipeshub/tools/INDEX.md" in AGENTS_POINTER
    for launch in (claude_code_launch, codex_launch, opencode_launch):
        assert launch.BASE_PROMPT.endswith(AGENTS_POINTER), launch.__name__
    _, files = tools_files(TOOLS)
    index = files["INDEX.md"].decode()
    assert f"PYTHONPATH={SDK_PATH}/python" in index and f"from {PACKAGE} import <toolset>" in index
    assert f'"{SDK_PATH}/ts/index.mjs"' in index


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("search_issues", "search_issues"),
        ("searchIssues", "search_issues"),
        ("class", "class_"),
        ("match", "match_"),
        ("delete", "delete_"),
        ("frozenset", "frozenset_"),
        ("9lives", "t_9lives"),
        ("--", "tool"),
        ("Ünïcode name", "n_code_name"),
    ],
)
def test_sdk8_identifiers_are_valid_in_both_languages(raw: str, expected: str) -> None:
    """SDK-8: an identifier is rebuilt from ``[a-z0-9_]``, never a keyword of Python or JavaScript, never a builtin
    the module uses; a clash gets a suffix."""
    assert identifier(raw, set()) == expected
    taken: set[str] = set()
    assert [identifier("a b", taken), identifier("a-b", taken), identifier("a_b", taken)] == ["a_b", "a_b_2", "a_b_3"]


def test_sdk9_a_grant_of_hundreds_of_tools_renders_quickly() -> None:
    """SDK-9: the stubs are rendered at every turn start, so the measured grant size (385 tools) renders well
    within the turn's start budget.

    The cost is held in bytes, which the renderer decides, as LS-6 holds it; a wall-clock bound measured the host's
    load instead."""
    many = tuple(
        McpTool(f"set{i % 20}__tool_{i}", f"Tool {i}.", {"type": "object", "properties": {"q": JQL, "n": {"type": "integer"}}, "required": ["q"]})
        for i in range(385)
    )
    _, files = sdk_files(many)
    assert sum(len(c) for c in files.values()) < 2_000_000
