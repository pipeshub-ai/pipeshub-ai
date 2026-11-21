"""C-16 (F3): the feature-flag matrix and the code, the image and the deployment files agree.

``contracts/harness/flags.json`` is the matrix. Every environment variable the harness
reads in Python is a matrix flag or a listed non-flag setting, and every Python reader
the matrix names reads it (FM-2); the same for the env names the image's entrypoint
decides on (FM-6), the Labs flags harness code reads (FM-4), and the
``/services/agentHarness`` documents (FM-5). ``FLAGS.md`` renders the matrix (FM-7), and
what Compose, the env templates, Helm and ``install.sh`` ship is what the matrix says
(FD-2). Node's half is ``backend/nodejs/apps/tests/cohesion/c16-flag-matrix.test.ts``.
"""

from __future__ import annotations

import ast
import json
import re
from functools import cache
from typing import TYPE_CHECKING, Any, cast

import pytest
import yaml

from tests.cohesion.compose_files import ComposeLoader
from tests.cohesion.compose_files import render_defaults as _render
from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT, modules, with_overlay
from tests.edition_overlay import overlay

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

MATRIX_PATH = REPO_ROOT / "contracts" / "harness" / "flags.json"
FLAGS_DOC = REPO_ROOT / "docs" / "agent-harness-v2" / "FLAGS.md"
DOCKERFILE = REPO_ROOT / "Dockerfile"
# main moved the process monitor out of the Dockerfile into its own script.
PROCESS_MONITOR = REPO_ROOT / "deployment" / "process_monitor.sh"


def entrypoint_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8") + PROCESS_MONITOR.read_text(encoding="utf-8")
COMPOSE_DIR = REPO_ROOT / "deployment" / "docker-compose"
HELM_DIR = REPO_ROOT / "deployment" / "helm" / "pipeshub-ai"
REGISTRY = REPO_ROOT / "backend/nodejs/apps/src/modules/configuration_manager/constants/constants.ts"
NODE_SRC = REPO_ROOT / "backend/nodejs/apps/src"

# Where the harness reads its environment in Python: the harness packages, its process roles,
# and the few legacy-loop modules that carry a harness flag (the sandbox settings).
SCOPE_ROOTS = ("app/harness", "harness_core", "harness_engines", "app/agent_loop_lib/sandbox", "app/sandbox")
# Outside those trees, any read of a name in the harness's and the sandbox's namespaces counts too (H6 FM-E1):
# a hand-kept file list missed main's SANDBOX_DISK_QUOTA in egress_firewall.py (OI-N14).
# Main's operator switches over what an agent may run (custom STDIO servers, skills, code execution, the model
# transport) count too (CPS, R2 F13): they sat outside the matrix, so nothing kept their defaults or parse honest.
# The control plane's switches too, as in the Node half (CPD-10: one reader of ``CONTROL_PLANE_POLICY_STORE``).
HARNESS_ENV = re.compile(
    r"^(?:(?:AGENT_HARNESS|CONTROL_PLANE|HARNESS|SANDBOX|EGRESS|MODEL_GATEWAY|TOOL_GATEWAY|PIPESHUB_CODE|MCP_HUB|MCP_STDIO|PIPESHUB_SKILLS?)_"
    # The private-address switch the MCP hub's SSRF check reads through app.utils.url_fetcher.
    r"|(?:MCP_ALLOW_CUSTOM_STDIO|PIPESHUB_ENABLE_SKILLS|PIPESHUB_ENABLE_CODE_EXECUTION|PIPESHUB_AGENT_TRANSPORT"
    r"|PIPESHUB_BLOCK_PRIVATE_ADDRESSES)$)"
)
# Boolean flags read with a parse of their own, never env_flag; the matrix states each parse. Moving one to
# env_flag changes what a deployment's value means, so the list only shrinks, deliberately.
OWN_BOOLEAN_PARSERS = {"true_only", "boolean_words"}
WIDE_ROOTS = ("app",)
SCOPE_FILES = (
    "app/harness_main.py",
    "app/model_gateway_main.py",
    "app/sandbox_broker_main.py",
    "app/egress_proxy_main.py",
    "app/agents/agent_loop/sandbox_bridge.py",
    "app/agent_loop_lib/sandbox/coding/settings.py",
)
# Callables whose first argument is an environment variable's name.
ENV_READERS = {
    "getenv", "env_bool", "env_int", "env_flag", "_env_bool", "_env_int", "_env_float", "_env_cap", "_parse_env_extra",
    # harness_engines.profile_trust (R32-TRUST): a setting read from an injected mapping or the process environment.
    "env_setting",
}
# These copy host variables into a sandbox by a list (PATH, LANG, SystemRoot, ...): passthrough, not settings.
PASSTHROUGH = {"app/agent_loop_lib/sandbox/coding/environment.py", "app/sandbox/base_executor.py"}
# The one boolean parse (F3 follow-up): every env flag the matrix marks ``parser: env_flag`` is read through it.
FLAG_HELPER = "env_flag"
PY_FLAG_HELPER = PYTHON_ROOT / "app/utils/env_flags.py"
# Names the entrypoint uses to decide whether to start a harness process.
_SHELL_PREFIX = r"(?:AGENT_HARNESS|MODEL_GATEWAY|SANDBOX|EGRESS|PIPESHUB_CODE)_[A-Z0-9_]+"
SHELL_NAME = re.compile(rf"\$\{{?({_SHELL_PREFIX})|env_flag ({_SHELL_PREFIX})")
SHELL_RAW = re.compile(rf"\$\{{?({_SHELL_PREFIX})")


@cache
def base_matrix() -> dict[str, Any]:
    """This repository's matrix alone: what ``FLAGS.md`` renders."""
    return cast("dict[str, Any]", json.loads(MATRIX_PATH.read_text(encoding="utf-8")))


@cache
def matrix() -> dict[str, Any]:
    """The matrix with an edition's overlay entries (``contracts/harness/ee/flags.json``), when present."""
    return with_overlay(base_matrix(), "flags.json")


def flags(kind: str | None = None) -> list[dict[str, Any]]:
    return [f for f in matrix()["flags"] if kind is None or f["kind"] == kind]


def setting_names(kind: str) -> set[str]:
    return {s["name"] for s in matrix()["settings"] if s["kind"] == kind}


def scope_files() -> list[Path]:
    return [*modules(*SCOPE_ROOTS), *(PYTHON_ROOT / f for f in SCOPE_FILES)]


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module- and class-level ``NAME = "literal"`` assignments (env names are held this way)."""
    found: dict[str, str] = {}
    bodies = [tree.body, *(node.body for node in tree.body if isinstance(node, ast.ClassDef))]
    for body in bodies:
        for node in body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                for target in targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = value.value
    return found


@cache
def _module_constants(path: Path) -> dict[str, str]:
    # Parsed once: every module that imports from this one asks.
    return _string_constants(ast.parse(path.read_text(encoding="utf-8")))


def _imported_constants(tree: ast.Module) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in {"app", "harness_core"}:
            source = PYTHON_ROOT.joinpath(*node.module.split("."))
            path = source.with_suffix(".py") if source.with_suffix(".py").exists() else source / "__init__.py"
            if not path.exists():
                continue
            constants = _module_constants(path)
            for alias in node.names:
                if alias.name in constants:
                    found[alias.asname or alias.name] = constants[alias.name]
    return found


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        if func.attr == "get" and isinstance(func.value, ast.Attribute) and func.value.attr == "environ":
            return "getenv"
        return func.attr
    return None


def env_reads(path: Path) -> tuple[set[str], list[str], set[str]]:
    """Env names ``path`` reads, the reads whose name could not be resolved, and the names read
    other than through ``env_flag``."""
    source = path.read_text(encoding="utf-8-sig")
    # Every read the scan knows names `environ` or a reader, so only such files are parsed.
    if "environ" not in source and not any(reader in source for reader in ENV_READERS):
        return set(), [], set()
    tree = ast.parse(source)
    own = _string_constants(tree)
    imported: dict[str, str] | None = None
    names: set[str] = set()
    raw: set[str] = set()
    unresolved: list[str] = []

    def constant(key: str) -> str | None:
        # The modules this one imports from are read only for a name it does not define itself.
        nonlocal imported
        if key in own:
            return own[key]
        if imported is None:
            imported = _imported_constants(tree)
        return imported.get(key)

    def resolve(arg: ast.expr, line: int, via: str) -> None:
        key = arg.id if isinstance(arg, ast.Name) else arg.attr if isinstance(arg, ast.Attribute) else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            name = arg.value
        elif key is not None and (found := constant(key)) is not None:
            name = found
        else:
            unresolved.append(f"{path.relative_to(REPO_ROOT)}:{line}")
            return
        names.add(name)
        if via != FLAG_HELPER:
            raw.add(name)

    # The readers' own bodies read their ``name`` parameter; their callers are what counts.
    helpers = {
        id(inner)
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name in ENV_READERS
        for inner in ast.walk(fn)
    }
    for node in ast.walk(tree):
        if id(node) in helpers:
            continue
        called = _called_name(node) if isinstance(node, ast.Call) else None
        if isinstance(node, ast.Call) and called in ENV_READERS and node.args:
            resolve(node.args[0], node.lineno, called or "")
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.ctx, ast.Load)
        ):
            resolve(node.slice, node.lineno, "environ")
    return names, unresolved, raw


@cache
def python_reads() -> dict[str, set[str]]:
    """File (relative to the repository) -> env names it reads, over the harness scope."""
    reads: dict[str, set[str]] = {}
    problems: list[str] = []
    for path in scope_files():
        names, unresolved, _ = env_reads(path)
        if str(path.relative_to(PYTHON_ROOT)) in PASSTHROUGH:
            continue
        problems += unresolved
        if names:
            reads[str(path.relative_to(REPO_ROOT))] = names
    assert problems == [], f"env reads whose name C-16 cannot resolve (use a literal or a module constant): {problems}"
    return reads


@cache
def wide_reads() -> dict[str, set[str]]:
    """File -> harness-namespace env names it reads, anywhere under ``WIDE_ROOTS`` outside the scope."""
    scoped = {path.resolve() for path in scope_files()}
    reads: dict[str, set[str]] = {}
    for root in WIDE_ROOTS:
        for path in sorted((PYTHON_ROOT / root).rglob("*.py")):
            if path.resolve() in scoped or "__pycache__" in path.parts or "generated" in path.parts:
                continue
            text = path.read_text(encoding="utf-8-sig")
            if "environ" not in text and "getenv" not in text and "env_" not in text:
                continue
            names = {name for name in env_reads(path)[0] if HARNESS_ENV.match(name)}
            if names:
                reads[str(path.relative_to(REPO_ROOT))] = names
    return reads


def shell_names() -> set[str]:
    found = {a or b for a, b in SHELL_NAME.findall(entrypoint_text())}
    return {m for m in found if not m.endswith("_PID")}


def boolean_flags() -> list[dict[str, Any]]:
    return [f for f in flags("env") if f.get("parser") == FLAG_HELPER]


def registry() -> dict[str, bool]:
    """``PLATFORM_FEATURE_FLAGS``: key -> defaultEnabled (absent means false)."""
    text = REGISTRY.read_text(encoding="utf-8")
    body = text[text.index("PLATFORM_FEATURE_FLAGS") :]
    entries = re.findall(r"\{[^{}]*?key:\s*'([A-Z0-9_]+)'[^{}]*?\}", body, flags=re.S)
    found: dict[str, bool] = {}
    for key in entries:
        block = re.search(r"key:\s*'" + key + r"'[^{}]*?\}", body, flags=re.S)
        assert block is not None
        default = re.search(r"defaultEnabled:\s*(true|false)", block.group(0))
        found[key] = default is not None and default.group(1) == "true"
    return found


# FM-1


def test_fm1_every_flag_and_feature_is_described() -> None:
    names = [f["name"] for f in flags()]
    assert len(names) == len(set(names))
    required = {"name", "kind", "scope", "default", "parse", "changedBy", "gates", "readers", "shipped"}
    for flag in flags():
        assert required <= set(flag), flag["name"]
        assert flag["kind"] in {"env", "labs", "config", "request"}, flag["name"]
        assert flag["scope"] in {"deployment", "org", "agent", "request"}, flag["name"]
        assert flag["readers"], flag["name"]
    features = {f["name"]: f for f in matrix()["features"]}
    known = set(names)
    for feature in features.values():
        assert feature["whenOff"], feature["name"]
        assert feature.get("alwaysOn") or feature["requires"], feature["name"]
        for need in feature["requires"]:
            assert {"on", "whenNot", "reason"} <= set(need), (feature["name"], need)
            assert ("flag" in need) + ("feature" in need) + ("condition" in need) == 1, need
            if "flag" in need:
                assert need["flag"] in known, need
            if "feature" in need:
                assert need["feature"] in features, need
    for flag in flags():
        if "killSwitchFor" in flag:
            assert flag["killSwitchFor"] in known
        if "defaultFollows" in flag:
            # A flag may follow another flag, or whether a listed setting is set (EGRESS_STATE_REQUIRED, H1).
            assert flag["defaultFollows"] in known | setting_names("env")


def test_fm1_the_feature_graph_has_no_cycle() -> None:
    edges = {f["name"]: [n["feature"] for n in f["requires"] if "feature" in n] for f in matrix()["features"]}
    done: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        assert name not in path, f"cycle: {[*path, name]}"
        if name in done:
            return
        for nxt in edges[name]:
            visit(nxt, (*path, name))
        done.add(name)

    for name in edges:
        visit(name, ())


def test_fm1_erasure_is_always_on() -> None:
    erasure = next(f for f in matrix()["features"] if f["name"] == "session_erasure")
    assert erasure["alwaysOn"] is True and erasure["requires"] == []


# FM-2


def test_fm2_every_env_name_python_reads_is_in_the_matrix() -> None:
    known = {f["name"] for f in flags("env")} | setting_names("env")
    missing = {f"{name} ({file})" for file, names in python_reads().items() for name in names - known}
    assert missing == set(), "add these to contracts/harness/flags.json as a flag or a setting"


def test_fm_e1_every_harness_namespace_env_name_read_anywhere_is_in_the_matrix() -> None:
    known = {f["name"] for f in flags("env")} | setting_names("env")
    missing = {f"{name} ({file})" for file, names in wide_reads().items() for name in names - known}
    assert missing == set(), "add these to contracts/harness/flags.json as a flag or a setting"


def test_fm_e2_the_scan_resolves_constants_and_finds_main_s_sandbox_settings() -> None:
    firewall = "backend/python/app/agent_loop_lib/sandbox/coding/egress_firewall.py"
    assert {"SANDBOX_DISK_QUOTA"} <= python_reads()[firewall]
    assert HARNESS_ENV.match("SANDBOX_DISK_QUOTA") and HARNESS_ENV.match("MCP_ALLOW_CUSTOM_STDIO")
    assert HARNESS_ENV.match("PIPESHUB_SKILLS_AUTO_APPROVE") and HARNESS_ENV.match("PIPESHUB_ENABLE_SKILLS")
    assert not HARNESS_ENV.match("PIPESHUB_ENABLE_SKILLS_X") and not HARNESS_ENV.match("PIPESHUB_ENABLE_LAZY_TOOLS")
    assert HARNESS_ENV.match("SANDBOX_DISK_QUOTA") and not HARNESS_ENV.match("MCP_ALLOW_CUSTOM_STDIO_X")
    knobs = {"MCP_ALLOW_CUSTOM_STDIO", "PIPESHUB_ENABLE_SKILLS", "PIPESHUB_ENABLE_CODE_EXECUTION", "PIPESHUB_AGENT_TRANSPORT"}
    assert knobs <= set().union(*wide_reads().values())
    assert {"PIPESHUB_SKILLS_LEARNING_ENABLED", "PIPESHUB_SKILL_VECTOR_CACHE_SIZE"} <= set().union(*wide_reads().values())


def test_fm_e3_flags_md_lists_exactly_the_matrix_settings() -> None:
    doc = (REPO_ROOT / "docs" / "agent-harness-v2" / "FLAGS.md").read_text(encoding="utf-8")
    section = doc.split("## Settings that are not flags", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `([^`]+)` \|", section, flags=re.M))
    assert listed == {s["name"] for s in base_matrix()["settings"]}


def test_fm2_every_python_reader_the_matrix_names_reads_the_flag() -> None:
    reads, wide = python_reads(), wide_reads()
    for flag in flags("env"):
        for file in flag["readers"].get("python", []):
            assert flag["name"] in reads.get(file, set()) | wide.get(file, set()), f"{flag['name']} is not read in {file}"
    scoped = {str(path.relative_to(REPO_ROOT)) for path in scope_files()}
    readers = {name: {file for file, names in reads.items() if name in names} for name in {f["name"] for f in flags("env")}}
    for flag in flags("env"):
        listed = set(flag["readers"].get("python", []))
        assert readers[flag["name"]] == listed & scoped, flag["name"]
        if not readers[flag["name"]]:
            # Read only outside the harness packages (main's operator switches): every such reader is listed.
            assert {file for file, names in wide.items() if flag["name"] in names} == listed, flag["name"]


def test_cpd10_45_the_policy_store_mode_lists_its_python_reader() -> None:
    flag = next(f for f in flags("env") if f["name"] == "CONTROL_PLANE_POLICY_STORE")
    reader = "backend/python/app/harness/policy_documents.py"
    assert flag["readers"]["python"] == [reader]
    assert "CONTROL_PLANE_POLICY_STORE" in python_reads()[reader]
    everywhere = {
        file for file, names in {**python_reads(), **wide_reads()}.items() if "CONTROL_PLANE_POLICY_STORE" in names
    }
    assert everywhere == {reader}, "one reader decides where a policy document is read from (C-31)"
    assert sorted(flag["values"]) == ["dual", "off", "on"] and flag["default"] == "dual"


def test_fm2_every_listed_env_setting_is_read_somewhere() -> None:
    node_text = "\n".join(p.read_text(encoding="utf-8") for p in (NODE_SRC / "modules" / "agent-harness").rglob("*.ts"))
    read = set().union(*python_reads().values(), *wide_reads().values()) | shell_names()
    stale = {name for name in setting_names("env") if name not in read and name not in node_text}
    assert stale == set()


# FM-4


def test_fm4_labs_flags_match_the_registry() -> None:
    known = registry()
    for flag in flags("labs"):
        assert flag["name"] in known, f"{flag['name']} is not in PLATFORM_FEATURE_FLAGS"
        assert known[flag["name"]] is flag["default"], flag["name"]


def test_fm4_every_labs_flag_harness_python_reads_is_in_the_matrix_with_its_default() -> None:
    settings_module = PYTHON_ROOT / "app/services/featureflag/platform_settings.py"
    tree = ast.parse(settings_module.read_text(encoding="utf-8"))
    config_values = _string_constants(ast.parse((PYTHON_ROOT / "app/services/featureflag/config/config.py").read_text(encoding="utf-8")))
    used: set[str] = set()
    for path in scope_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.featureflag.platform_settings":
                used |= {alias.name for alias in node.names}
    # UX1: the readers take their defaults from one table, `PLATFORM_FLAG_DEFAULTS[CONFIG.<NAME>]`.
    table: dict[str, bool] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "PLATFORM_FLAG_DEFAULTS":
            assert isinstance(node.value, ast.Dict)
            for k, v in zip(node.value.keys, node.value.values, strict=True):
                assert isinstance(k, ast.Attribute) and isinstance(v, ast.Constant)
                table[k.attr] = bool(v.value)
    read: dict[str, bool] = {}
    for fn in (n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in used):
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call) and _called_name(c) == "_platform_flag"):
            key = next(a.attr for a in call.args if isinstance(a, ast.Attribute))
            # UX2: the reader takes its default from the table itself (FM-4b keeps it that way).
            read[config_values[key]] = table[key]
    labs = {f["name"]: f for f in flags("labs")}
    assert read, "the harness reads no Labs flag in Python? C-16's scan is broken"
    for name, default in read.items():
        assert name in labs, name
        assert labs[name]["default"] is default, name
        assert "backend/python/app/services/featureflag/platform_settings.py" in labs[name]["readers"]["python"]


LABS_FLAG_READERS = frozenset({"read_platform_feature_flag", "_platform_flag", "is_feature_enabled"})


def literal_labs_defaults(paths: Iterable[Path], labs_flags: set[str], root: Path = PYTHON_ROOT) -> list[str]:
    """Calls of a Labs flag reader that carry their own ``default=`` for a Labs flag, and reader definitions that
    still take one: each is a default that can drift from ``PLATFORM_FLAG_DEFAULTS`` (UX1 OI-5)."""
    found: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8-sig")
        # Defining or calling a reader names it, so only such files are parsed.
        if not any(reader in source for reader in LABS_FLAG_READERS):
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in LABS_FLAG_READERS - {"is_feature_enabled"}
                and any(a.arg == "default" for a in (*node.args.args, *node.args.kwonlyargs))
            ):
                found.append(f"{path.relative_to(root)}:{node.lineno} {node.name} takes a default")
            if not isinstance(node, ast.Call) or _called_name(node) not in LABS_FLAG_READERS or not node.args:
                continue
            first = node.args[0]
            flag = first.attr if isinstance(first, ast.Attribute) else first.value if isinstance(first, ast.Constant) else None
            if flag in labs_flags and any(k.arg == "default" for k in node.keywords):
                found.append(f"{path.relative_to(root)}:{node.lineno} {flag}")
    return found


def test_fm4b_no_labs_flag_reader_carries_a_literal_default() -> None:
    labs = {f["name"] for f in flags("labs")} | set(registry())
    paths = sorted((PYTHON_ROOT / "app").rglob("*.py"))
    assert literal_labs_defaults(paths, labs) == []


def test_fm4b_the_scan_finds_a_planted_literal(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        "async def f(cs):\n    return await read_platform_feature_flag(CONFIG.ENABLE_MCP, cs, default=False)\n"
        "async def read_platform_feature_flag(flag, cs, *, default):\n    return default\n",
        encoding="utf-8",
    )
    assert literal_labs_defaults([planted], {"ENABLE_MCP"}, tmp_path) == [
        "planted.py:3 read_platform_feature_flag takes a default",
        "planted.py:2 ENABLE_MCP",
    ]


# FM-5


def _harness_paths_in_code() -> set[str]:
    # A literal ending in "/" is a key prefix (an edition's list of global keys), not a document.
    literal = re.compile(r"""["'](/services/agentHarness(?:[A-Za-z/]*[A-Za-z])?)["']""")
    found: set[str] = set()
    for path in [*modules("app", "harness_core", "harness_engines"), *NODE_SRC.rglob("*.ts")]:
        if "tests" in path.parts:
            continue
        found |= set(literal.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return found


def test_fm5_every_agent_harness_document_is_in_the_matrix() -> None:
    known = {f["path"] for f in flags("config") if "path" in f} | setting_names("config")
    assert _harness_paths_in_code() - known == set()


def test_fm5_every_matrix_document_is_read_somewhere() -> None:
    in_code = _harness_paths_in_code()
    documents = {f["path"] for f in flags("config") if str(f.get("path", "")).startswith("/services/agentHarness")}
    assert documents | setting_names("config") <= in_code


def test_fm5_every_reader_the_matrix_names_mentions_the_flag() -> None:
    constants = _string_constants(ast.parse((PYTHON_ROOT / "app/config/constants/service.py").read_text(encoding="utf-8")))
    for flag in flags():
        path = str(flag.get("path", ""))
        spellings = {flag["name"], flag["name"].rsplit(".", 1)[-1], path.rsplit("/", 1)[-1] if path.startswith("/") else ""}
        spellings |= {name for name, value in constants.items() if path and value == path}
        spellings.discard("")
        for language, files in flag["readers"].items():
            for file in files:
                text = (REPO_ROOT / file).read_text(encoding="utf-8")
                assert any(s in text for s in spellings), f"{flag['name']}: {file} ({language})"


# FM-6


def test_fm6_the_entrypoint_decides_only_on_matrix_names() -> None:
    known = {f["name"] for f in flags("env")} | setting_names("env")
    assert shell_names() - known == set()
    shell_readers = {f["name"] for f in flags("env") if "shell" in f["readers"]}
    assert shell_readers == {n for n in shell_names() if n in {f["name"] for f in flags("env")}}


# FM-7


def test_fm7_flags_md_lists_every_flag_and_feature() -> None:
    text = FLAGS_DOC.read_text(encoding="utf-8")
    rows = {cells[0].strip("` "): cells for cells in (line.split("|")[1:-1] for line in text.splitlines() if line.startswith("| `"))}
    for flag in base_matrix()["flags"]:
        assert flag["name"] in rows, flag["name"]
        cells = [c.strip() for c in rows[flag["name"]]]
        assert cells[1] == flag["kind"] and cells[2] == flag["scope"], flag["name"]
        assert cells[3].strip("`") == _rendered_default(flag), flag["name"]
    for feature in base_matrix()["features"]:
        assert f"### `{feature['name']}`" in text, feature["name"]


def _rendered_default(flag: dict[str, Any]) -> str:
    if "defaultFollows" in flag:
        return f"follows {flag['defaultFollows']}"
    value = flag["default"]
    return "unset" if value is None else str(value).lower() if isinstance(value, bool) else str(value)


# FD-2


def _compose_env(file: str) -> dict[str, str]:
    doc = yaml.load((COMPOSE_DIR / file).read_text(encoding="utf-8"), Loader=ComposeLoader)
    raw = doc["services"]["pipeshub-ai"].get("environment") or {}
    pairs = dict(entry.split("=", 1) for entry in raw) if isinstance(raw, list) else {k: str(v) for k, v in raw.items()}
    return {k: _render(v) for k, v in pairs.items()}


def _env_file(path: Path) -> dict[str, str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return dict(line.split("=", 1) for line in lines if re.match(r"^[A-Z][A-Z0-9_]*=", line))


@pytest.mark.parametrize(
    ("artifact", "load"),
    [
        ("compose", lambda: _compose_env("docker-compose.yml")),
        ("composeHarness", lambda: _compose_env("docker-compose.agent-harness.yml")),
        ("deploymentEnvTemplate", lambda: _env_file(COMPOSE_DIR / "env.template")),
        ("backendEnvTemplate", lambda: _env_file(REPO_ROOT / "backend" / "env.template")),
    ],
)
def test_fd2_env_files_ship_the_matrix_defaults(artifact: str, load: Any) -> None:  # noqa: ANN401 - a loader
    shipped = load()
    for flag in flags("env"):
        expected = flag["shipped"][artifact]
        assert shipped.get(flag["name"]) == expected, f"{artifact}: {flag['name']}"


def test_fd2_helm_ships_the_matrix_defaults() -> None:
    values = yaml.safe_load((HELM_DIR / "values.yaml").read_text(encoding="utf-8"))
    template = (HELM_DIR / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    for flag in flags("env"):
        helm = flag["shipped"]["helm"]
        if helm is None:
            assert f"name: {flag['name']}\n" not in template, flag["name"]
            continue
        node: Any = values
        for part in helm["values"].split("."):
            node = node[part]
        assert node == helm["value"], flag["name"]
        assert f"name: {flag['name']}" in template, flag["name"]


def _helm_lookup(values: Any, path: str) -> tuple[bool, Any]:  # noqa: ANN401 - parsed YAML
    node = values
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


@pytest.mark.parametrize("preset", sorted(p.name for p in HELM_DIR.glob("values-*.yaml")))
def test_fd2_helm_presets_ship_the_matrix_defaults(preset: str) -> None:
    """A preset (`-f values-cloud.yaml`) overrides values.yaml, so it ships a default too; one that
    differs from the matrix is recorded in `shipped.helm.presets`, never silently allowed."""
    values = yaml.safe_load((HELM_DIR / preset).read_text(encoding="utf-8"))
    for flag in flags("env"):
        helm = flag["shipped"]["helm"]
        if helm is None:
            continue
        present, value = _helm_lookup(values, helm["values"])
        recorded = (helm.get("presets") or {}).get(preset)
        if recorded is not None:
            assert present and value == recorded, f"{preset}: {flag['name']} records {recorded!r}"
        elif present:
            assert value == helm["value"], f"{preset}: {flag['name']} ships {value!r}"


# Compose files FD-2 reads above, opt-in overlays whose whole purpose is to turn a flag on (their
# values are in the matrix), and the integration stacks CI runs the harness on (never shipped to users).
_COMPOSE_NOT_VARIANTS = frozenset(
    {
        "docker-compose.yml",
        "docker-compose.agent-harness.yml",
        "docker-compose.egress-tunnels.yml",
        "docker-compose.sandbox-legacy.yml",
    }
)


def _compose_variants() -> list[str]:
    files = sorted(
        str(p.relative_to(COMPOSE_DIR)) for p in [*COMPOSE_DIR.glob("*.yml"), *COMPOSE_DIR.glob("*/*.yml")]
    )
    return [f for f in files if f not in _COMPOSE_NOT_VARIANTS and not f.rsplit("/", 1)[-1].startswith("docker-compose.integration.")]


def _all_services_env(file: str) -> dict[str, str]:
    doc = yaml.load((COMPOSE_DIR / file).read_text(encoding="utf-8"), Loader=ComposeLoader)
    shipped: dict[str, str] = {}
    for service in (doc.get("services") or {}).values():
        raw = (service or {}).get("environment") or {}
        pairs = dict(e.split("=", 1) for e in raw if "=" in e) if isinstance(raw, list) else {k: str(v) for k, v in raw.items()}
        shipped.update({k: _render(v) for k, v in pairs.items()})
    return shipped


@pytest.mark.parametrize("variant", _compose_variants())
def test_fd2_other_compose_files_ship_the_base_defaults(variant: str) -> None:
    """Main's other shipped Compose files (build-from-source, prod-legacy, beta, hot-reload) never
    turn a harness flag on; a value that differs from the base file is recorded in
    `shipped.composeVariants`, never silently allowed."""
    shipped = _all_services_env(variant)
    # An edition overlay records the values its own Compose files ship (``tests/edition_overlay.py``,
    # ``compose_variants.json``: variant -> flag -> value); a variant this repository records is never redefined.
    edition_values = overlay("compose_variants").get("variants", {}).get(variant, {})
    for flag in flags("env"):
        if flag["name"] not in shipped:
            continue
        recorded = (flag["shipped"].get("composeVariants") or {}).get(variant)
        if recorded is None:
            recorded = edition_values.get(flag["name"])
        expected = recorded if recorded is not None else flag["shipped"]["compose"]
        assert shipped[flag["name"]] == expected, f"{variant}: {flag['name']} ships {shipped[flag['name']]!r}"


def test_fd2_install_sh_writes_no_harness_flag_but_the_sandbox_mode() -> None:
    text = (COMPOSE_DIR / "install.sh").read_text(encoding="utf-8")
    for flag in flags("env"):
        written = re.search(rf"^\s*{flag['name']}=", text, flags=re.M) is not None
        assert written is (flag["shipped"]["installSh"] is not None), flag["name"]
    # Hardened or with the harness a new .env gets the broker; the compatibility profile keeps docker (ADR-OPS-07).
    assert "then SANDBOX_MODE=broker" in text and "else SANDBOX_MODE=docker; fi" in text


# FM-8 (F3 follow-up): one boolean parse per language, and every flag read goes through it.


def test_fm8_every_boolean_env_flag_has_the_one_parse() -> None:
    parse = matrix()["envFlagParse"]
    assert parse["accepted"] == {"true": ["1", "true", "yes", "on"], "false": ["0", "false", "no", "off"]}
    own = {f["name"] for f in flags("env") if f.get("parser") in OWN_BOOLEAN_PARSERS}
    assert {f["name"] for f in boolean_flags()} | own == {
        f["name"] for f in flags("env") if isinstance(f["default"], bool) or "defaultFollows" in f
    }
    # CPS moved main's six operator switches onto env_flag, which emptied this list (R2 F13).
    assert own == set()


def test_fm8_python_reads_boolean_flags_only_through_env_flag() -> None:
    names = {f["name"] for f in boolean_flags()}
    # Only files that spell a flag's name or import the helper's constants can read one.
    marker = re.compile("|".join([*names, r"app\.utils\.env_flags"]))
    candidates = [
        path
        for path in modules("app", "harness_core", "harness_engines")
        if path != PY_FLAG_HELPER and marker.search(path.read_text(encoding="utf-8-sig", errors="ignore"))
    ]
    raw = {f"{name} ({path.relative_to(REPO_ROOT)})" for path in candidates for name in env_reads(path)[2] & names}
    assert candidates, "C-16 found no reader of a boolean flag: the scan is broken"
    assert raw == set()


def test_fm8_the_python_helper_defaults_are_the_matrix_defaults() -> None:
    from app.utils.env_flags import ENV_FLAG_DEFAULTS

    python = {f["name"]: f["default"] for f in boolean_flags() if "python" in f["readers"]}
    assert python == ENV_FLAG_DEFAULTS


def test_fm8_the_entrypoint_reads_boolean_flags_only_through_env_flag() -> None:
    text = entrypoint_text()
    shell = {f["name"]: f for f in boolean_flags() if "shell" in f["readers"]}
    assert set(SHELL_RAW.findall(text)) & set(shell) == set()
    worker = shell["AGENT_HARNESS_WORKER_ENABLED"]
    assert f"env_flag AGENT_HARNESS_WORKER_ENABLED {str(worker['default']).lower()}" in text
    assert 'env_flag MODEL_GATEWAY_ENABLED "$follows"' in text


# FM-9 (F3 follow-up): every configuration read in harness code states what a store outage means.

CONFIG_READS = {"get_config", "configured_endpoint"}


def test_fm9_every_harness_config_read_chooses_raise_on_error() -> None:
    silent = [
        f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
        for path in scope_files()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig")))
        if isinstance(node, ast.Call)
        and _called_name(node) in CONFIG_READS
        and not any(k.arg == "raise_on_error" for k in node.keywords)
    ]
    assert silent == [], (
        "pass raise_on_error=True (an outage must fail closed) or False (absent and unreadable mean the same), "
        "and record the decision in docs/agent-harness-v2/FLAGS.md"
    )
