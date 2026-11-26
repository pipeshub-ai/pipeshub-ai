"""Typed Python and TypeScript stubs of a run's tools, under ``/pipeshub/session/sdk`` (WS-14, C3.15).

Rendered with the rest of the session folder at each turn's start, from the grant the run is pinned to, so a pin or
a resume renders them again and only the granted tools ever appear. Each tool is one function whose arguments and
docstring come from its input schema and description; each toolset is one module (``pipeshub_tools.jira``,
``ts/jira.mjs``). Every function is one ``ph tools call`` (``sdk_runtime.py``, ``sdk_runtime.mjs``): the stubs add
no network path, credential or decision, so a stub call and ``ph tools call`` answer alike (C3.16) and every ask,
denial and classification rule applies to both.

The same grant renders the same bytes in any order; the folder is named after a digest of the grant, the
generator's version and the runtimes, so a re-seeded sandbox switches the ``sdk`` link at once (as ``tools``).

Tool names, descriptions and schemas come from connected apps and external MCP servers, so nothing from them is
written into code except as an escaped string or comment, and every identifier is rebuilt from ``[a-z0-9_]``.
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources
from typing import TYPE_CHECKING, Any, Final, cast

from harness_engines.pipeshub.sdk_render import (
    _PY_RESERVED,  # pyright: ignore[reportPrivateUsage]
    MAX_TYPE_DEPTH,
    PACKAGE,
    Param,
    Stub,
    _clean,  # pyright: ignore[reportPrivateUsage]
    _literal_ok,  # pyright: ignore[reportPrivateUsage]
    _schema,  # pyright: ignore[reportPrivateUsage]
    _union,  # pyright: ignore[reportPrivateUsage]
    identifier,
    py_docstring,
    py_module,
    py_type,
    toolset_stubs,
)
from harness_engines.pipeshub.toolsets import toolset_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.gateway.types import McpTool

# Bumped when the generated code changes shape, so the folder's digest changes with it.
GENERATOR_VERSION: Final = "1"
SDK_DIR: Final = "sdk"
PYTHON_DIR: Final = "python"
TS_DIR: Final = "ts"
DIGEST_FILE: Final = "DIGEST"
# The tools ``ph`` finds and calls tools with: never stubs (``ph tools call`` refuses them by name).
META_TOOLS: Final = frozenset({"search_tools", "call_tool"})

# C5.01: up to this many granted tools every toolset's stubs are rendered at the turn's start (about 2 KB a tool);
# a larger grant's Python modules are rendered on first import from the pinned schema files, and its TypeScript
# namespaces are built when the index loads, without per-tool types.
SDK_EAGER_MAX_TOOLS: Final = 200
LAZY_INDEX: Final = "_lazy.json"
_RUNTIME_PY: Final = "sdk_runtime.py"
_RENDER_PY: Final = "sdk_render.py"
_LAZY_PY: Final = "sdk_lazy.py"
_RUNTIME_MJS: Final = "sdk_runtime.mjs"
_RUNTIME_DMTS: Final = "sdk_runtime.d.mts"
_TS_EXPORTS: Final = (
    "CallResult", "CallError", "ToolFailed", "ApprovalPending", "UsageError", "NotFound", "Unavailable", "BadAnswer",
    "NotPermitted", "NotInRun", "call", "envelope",
)


def _resource(name: str) -> bytes:
    return resources.files(__package__).joinpath(name).read_bytes()


def js_comment(text: str, indent: str) -> str:
    """``text`` as a JSDoc block that cannot end early (``*/`` broken)."""
    body = _clean(text).replace("*/", "*\\/")
    lines = [f"{indent} * {line}".rstrip() for line in body.split("\n")]
    return f"{indent}/**\n" + "\n".join(lines) + f"\n{indent} */"


def ts_type(schema: Mapping[str, Any], depth: int = 0) -> str:
    """A TypeScript type for a JSON Schema."""
    if depth >= MAX_TYPE_DEPTH:
        return "unknown"
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(_literal_ok(v) for v in cast("list[object]", enum)):
        return " | ".join(sorted({json.dumps(v, ensure_ascii=True) for v in cast("list[object]", enum)}))
    if "const" in schema and _literal_ok(schema["const"]):
        return json.dumps(schema["const"], ensure_ascii=True)
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list) and options:
            return _union([ts_type(_schema(o), depth + 1) for o in cast("list[object]", options)], "unknown", " | ")
    kind = schema.get("type")
    kinds = [k for k in cast("list[object]", kind) if isinstance(k, str)] if isinstance(kind, list) else [kind]
    mapped = [_ts_kind(k, schema, depth) for k in kinds if isinstance(k, str)]
    return _union(mapped, "unknown", " | ")


def _ts_kind(kind: str, schema: Mapping[str, Any], depth: int) -> str:
    if kind == "array":
        return f"Array<{ts_type(_schema(schema.get('items')), depth + 1)}>"
    return {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "null": "null", "object": "Record<string, unknown>"}.get(kind, "unknown")


def stubs(tools: Iterable[McpTool]) -> dict[str, tuple[str, tuple[Stub, ...]]]:
    """Toolset module name -> (the toolset's own name, its stubs), for every granted tool but the meta tools."""
    by_toolset: dict[str, list[McpTool]] = {}
    for tool in sorted(tools, key=lambda t: t.name):
        if tool.name in META_TOOLS:
            continue
        by_toolset.setdefault(toolset_of(tool.name)[0], []).append(tool)
    modules: dict[str, tuple[str, tuple[Stub, ...]]] = {}
    module_names: set[str] = set()
    for toolset in sorted(by_toolset):
        module = identifier(toolset, module_names, _PY_RESERVED)
        entries = [(t.name, toolset_of(t.name)[1], t.description, t.input_schema) for t in by_toolset[toolset]]
        modules[module] = (toolset, toolset_stubs(entries))
    return modules


def lazy(tools: Iterable[McpTool]) -> bool:
    """Whether the grant is large enough for its stubs to be rendered on first use (C5.01)."""
    return sum(1 for t in tools if t.name not in META_TOOLS) > SDK_EAGER_MAX_TOOLS


_PY_EXPORTS: Final = ("ApprovalPending", "BadAnswer", "NotFound", "NotInRun", "NotPermitted", "CallError", "ToolFailed", "CallResult", "Unavailable", "UsageError", "call")
_PY_ALL: Final = ("ApprovalPending", "BadAnswer", "NotFound", "NotInRun", "NotPermitted", "SDK_DIGEST", "TOOLSETS", "CallError", "ToolFailed", "CallResult", "Unavailable", "UsageError", "call")
# A large grant's package (C5.01): its toolset modules are found by ``_lazy``, which renders each on first import.
_PY_LAZY_TAIL: Final = """
from pipeshub_tools import _lazy

_lazy.install(__name__, __file__)


def __getattr__(name: str) -> object:
    if name in TOOLSETS:
        return _lazy.load(__name__, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
"""


def _py_init(modules: Mapping[str, tuple[str, tuple[Stub, ...]]], digest: str, *, on_import: bool) -> str:
    toolsets = ", ".join(repr(m) for m in modules)
    usage = (
        "    from pipeshub_tools import <toolset>\n"
        "    result = <toolset>.<tool>(<argument>=...)   # a CallResult: .text, .json()\n\n"
        "``call(name, arguments)`` calls any tool of the run by its full name. ``ApprovalPending`` means a person\n"
        "must decide first: end the turn."
    )
    if on_import:
        usage += "\n\nThis run has many tools: a toolset's module is written from its schema files when first imported."
    return (
        py_docstring(
            "This run's tools as typed Python functions, one module per toolset. Each call is one\n"
            "``ph tools call``: the same gateway, grant, approvals and journal as ``ph`` and the MCP tools.\n\n" + usage,
            "",
        )
        + "\n\nfrom __future__ import annotations\n\n"
        + "from pipeshub_tools._runtime import (\n"
        + "".join(f"    {n},\n" for n in _PY_EXPORTS)
        + ")\n\n"
        + f"SDK_DIGEST = {digest!r}\n"
        + f"TOOLSETS = ({toolsets}{',' if len(modules) == 1 else ''})\n\n"
        + "__all__ = [\n"
        + "".join(f"    {n!r},\n" for n in _PY_ALL)
        + "]\n"
        + (_PY_LAZY_TAIL if on_import else "")
    )


def python_files(modules: Mapping[str, tuple[str, tuple[Stub, ...]]], digest: str) -> dict[str, bytes]:
    """``python/pipeshub_tools/``: the package, its runtime and one module per toolset."""
    root = f"{PYTHON_DIR}/{PACKAGE}"
    files = {f"{root}/_runtime.py": _resource(_RUNTIME_PY), f"{root}/py.typed": b""}
    for module, (toolset, rendered) in modules.items():
        files[f"{root}/{module}.py"] = py_module(toolset, rendered).encode()
    files[f"{root}/__init__.py"] = _py_init(modules, digest, on_import=False).encode()
    return files


def lazy_python_files(
    modules: Mapping[str, tuple[str, tuple[Stub, ...]]], digest: str, tools_folder: str, schema_paths: Mapping[str, str]
) -> dict[str, bytes]:
    """``python/pipeshub_tools/`` for a large grant (C5.01): the package, its runtime, the renderer the worker uses
    (``_render``) and an index of each toolset's tools and schema files (``_lazy.json``); no toolset module."""
    root = f"{PYTHON_DIR}/{PACKAGE}"
    index = {
        "tools": tools_folder,
        "modules": {
            module: {
                "toolset": toolset,
                "tools": [[s.tool, toolset_of(s.tool)[1], schema_paths[s.tool]] for s in rendered],
            }
            for module, (toolset, rendered) in modules.items()
        },
    }
    return {
        f"{root}/_runtime.py": _resource(_RUNTIME_PY),
        f"{root}/_render.py": _resource(_RENDER_PY),
        f"{root}/_lazy.py": _resource(_LAZY_PY),
        f"{root}/{LAZY_INDEX}": (json.dumps(index, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode(),
        f"{root}/py.typed": b"",
        f"{root}/__init__.py": _py_init(modules, digest, on_import=True).encode(),
    }


def _ts_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) else json.dumps(name, ensure_ascii=True)


def _ts_doc(stub: Stub) -> str:
    lines = [stub.description.strip() or f"The {stub.tool} tool.", ""]
    lines.append(f"Runs `ph tools call {stub.tool}`; rejects with ApprovalPending while a person decides (end the turn).")
    lines += [f"@param args.{p.name} {' '.join(p.description.split())}".rstrip() for p in stub.params]
    return "\n".join(lines)


def _ts_args(stub: Stub) -> str:
    if stub.open_arguments:
        return "args?: Record<string, unknown>"
    fields = "; ".join(f"{_ts_key(p.name)}{'' if p.required else '?'}: {ts_type(p.schema)}" for p in stub.params)
    optional = "" if any(p.required for p in stub.params) else "?"
    return f"args{optional}: {{ {fields} }}"


def _ts_head(digest: str) -> str:
    exports = ", ".join(_TS_EXPORTS)
    return (
        "// This run's tools as typed functions, one namespace per toolset (generated). Each call is one\n"
        "// `ph tools call`: the same gateway, grant, approvals and journal as ph and the MCP tools.\n"
        '//   import { <toolset> } from "/pipeshub/session/sdk/ts/index.mjs";\n'
        "//   const result = await <toolset>.<tool>({ ... });   // a CallResult: .text, .json()\n"
        f'export {{ {exports} }} from "./_runtime.mjs";\n'
        f"export const SDK_DIGEST = {json.dumps(digest)};\n"
    )


def _ts_index_files(head: str, digest: str, modules: Mapping[str, tuple[str, tuple[Stub, ...]]], js: str, dts: str) -> dict[str, bytes]:
    toolsets = f"export const TOOLSETS = {json.dumps(list(modules))};\n"
    dts_head = head.replace(f"export const SDK_DIGEST = {json.dumps(digest)};\n", "export declare const SDK_DIGEST: string;\n")
    return {
        f"{TS_DIR}/index.mjs": (head + toolsets + js).encode(),
        f"{TS_DIR}/index.d.mts": (dts_head + "export declare const TOOLSETS: readonly string[];\n" + dts).encode(),
    }


def _ts_runtime() -> dict[str, bytes]:
    return {f"{TS_DIR}/_runtime.mjs": _resource(_RUNTIME_MJS), f"{TS_DIR}/_runtime.d.mts": _resource(_RUNTIME_DMTS)}


def ts_files(modules: Mapping[str, tuple[str, tuple[Stub, ...]]], digest: str) -> dict[str, bytes]:
    """``ts/``: ES modules with ``.d.mts`` types, the runtime and one module per toolset, and ``index.mjs``."""
    files = _ts_runtime()
    for module, (toolset, rendered) in modules.items():
        head = f"// The {_clean(toolset).replace(chr(10), ' ')} tools of this run (generated; each call is one `ph tools call`).\n"
        js = [head, 'import { invoke } from "./_runtime.mjs";\n']
        dts = [head, 'import type { CallResult } from "./_runtime.mjs";\n']
        for stub in rendered:
            doc = js_comment(_ts_doc(stub), "")
            name = json.dumps(stub.tool, ensure_ascii=True)
            js.append(f"\n{doc}\nexport function {stub.function}(args) {{\n  return invoke({name}, args);\n}}\n")
            dts.append(f"\n{doc}\nexport declare function {stub.function}({_ts_args(stub)}): Promise<CallResult>;\n")
        files[f"{TS_DIR}/{module}.mjs"] = "".join(js).encode()
        files[f"{TS_DIR}/{module}.d.mts"] = "".join(dts).encode()
    namespaces = "".join(f'export * as {m} from "./{m}.mjs";\n' for m in modules)
    return {**files, **_ts_index_files(_ts_head(digest), digest, modules, namespaces, namespaces)}


def lazy_ts_files(modules: Mapping[str, tuple[str, tuple[Stub, ...]]], digest: str) -> dict[str, bytes]:
    """``ts/`` for a large grant (C5.01): ``index.mjs`` builds each toolset's namespace when it loads, from the
    tools' names alone, so a script imports and calls them as for a small grant; the declarations name each
    function but type its arguments loosely (each tool's arguments are in its schema file)."""
    js = [
        'import { invoke } from "./_runtime.mjs";\n',
        "const _ns = (tools) => Object.freeze(Object.fromEntries(Object.entries(tools).map(([fn, tool]) => [fn, (args) => invoke(tool, args)])));\n",
    ]
    dts = [
        'import type { CallResult } from "./_runtime.mjs";\n',
        "// This run has many tools: each tool's arguments are in /pipeshub/session/tools/<toolset>/<tool>.schema.json.\n",
        "type Tool = (args?: Record<string, unknown>) => Promise<CallResult>;\n",
    ]
    for module, (_, rendered) in modules.items():
        pairs = ", ".join(f"{s.function}: {json.dumps(s.tool, ensure_ascii=True)}" for s in rendered)
        js.append(f"export const {module} = _ns({{ {pairs} }});\n")
        fields = "; ".join(f"readonly {s.function}: Tool" for s in rendered)
        dts.append(f"export declare const {module}: {{ {fields} }};\n")
    return {**_ts_runtime(), **_ts_index_files(_ts_head(digest), digest, modules, "".join(js), "".join(dts))}


def sdk_digest(tools: Iterable[McpTool], tools_folder: str | None = None) -> str:
    """The digest of what the stubs are made from: the grant (name, description, schema), the generator, the runtimes
    and, for stubs rendered on first use, the renderer and the tools folder they read (C5.01)."""
    digest = hashlib.sha256(f"pipeshub-sdk/{GENERATOR_VERSION}\0".encode())
    for name in (_RUNTIME_PY, _RUNTIME_MJS, _RUNTIME_DMTS):
        digest.update(hashlib.sha256(_resource(name)).digest())
    if tools_folder is not None:
        digest.update(f"lazy\0{tools_folder}\0".encode())
        for name in (_RENDER_PY, _LAZY_PY):
            digest.update(hashlib.sha256(_resource(name)).digest())
    for tool in sorted(tools, key=lambda t: t.name):
        if tool.name in META_TOOLS:
            continue
        entry = {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
        digest.update(json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode() + b"\0")
    return digest.hexdigest()


def sdk_files(
    tools: tuple[McpTool, ...], schemas: tuple[str, Mapping[str, str]] | None = None
) -> tuple[str, dict[str, bytes]]:
    """The ``sdk`` folder: its name in the session folder (``sdk-<digest>``) and its files. ``schemas``: the turn's
    tools folder and each tool's schema file in it; given, a grant over ``SDK_EAGER_MAX_TOOLS`` gets stubs rendered
    on first use, which read those files (C5.01)."""
    on_use = schemas is not None and lazy(tools)
    modules = stubs(tools)
    if on_use:
        assert schemas is not None
        folder, paths = schemas
        digest = sdk_digest(tools, folder)
        rendered = {**lazy_python_files(modules, digest, folder, paths), **lazy_ts_files(modules, digest)}
    else:
        digest = sdk_digest(tools)
        rendered = {**python_files(modules, digest), **ts_files(modules, digest)}
    return f"{SDK_DIR}-{digest[:16]}", {**rendered, DIGEST_FILE: f"sha256:{digest}\n".encode()}


__all__ = [
    "DIGEST_FILE",
    "GENERATOR_VERSION",
    "LAZY_INDEX",
    "MAX_TYPE_DEPTH",
    "META_TOOLS",
    "PACKAGE",
    "PYTHON_DIR",
    "SDK_DIR",
    "SDK_EAGER_MAX_TOOLS",
    "TS_DIR",
    "Param",
    "Stub",
    "identifier",
    "js_comment",
    "lazy",
    "lazy_python_files",
    "lazy_ts_files",
    "py_docstring",
    "py_type",
    "python_files",
    "sdk_digest",
    "sdk_files",
    "stubs",
    "ts_files",
    "ts_type",
]
