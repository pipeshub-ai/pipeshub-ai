"""How a toolset's Python stubs are written (WS-14, C3.15), shared by the worker and the sandbox (C5.01).

The worker renders every toolset module at a turn's start for a grant of up to ``SDK_EAGER_MAX_TOOLS`` tools
(``sdk.py``). A larger grant's modules are rendered on first import, inside the sandbox, by this same file shipped as
``pipeshub_tools/_render.py``, from the turn's pinned schema files (``tools/<toolset>/<tool>.schema.json``): the same
grant gives the same source either way.

Standard library only, and no import of the rest of the package: it runs on the engine image's own CPython.
"""

from __future__ import annotations

import json
import keyword
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

PACKAGE: Final = "pipeshub_tools"
# How deep a schema is turned into a type; below it a value is ``Any`` / ``unknown``.
MAX_TYPE_DEPTH: Final = 4

# What a toolset module may not be called: the package's own names.
_PY_RESERVED: Final = frozenset({"call", "envelope", "invoke", "program"})
# Builtins a generated module uses (annotations, the call's optional set): never a function or argument name.
_PY_SHADOW: Final = frozenset({"str", "int", "float", "bool", "list", "dict", "frozenset", "None"})
_JS_RESERVED: Final = frozenset(
    "break case catch class const continue debugger default delete do else enum export extends false finally for "
    "function if implements import in instanceof interface let new null package private protected public return "
    "static super switch this throw true try typeof var void while with yield await arguments eval undefined".split()
)

def identifier(raw: str, taken: set[str], reserved: frozenset[str] = frozenset()) -> str:
    """A lower-case ASCII identifier made from ``raw``, valid in Python and JavaScript, not a keyword of either and
    not in ``taken`` (a clash gets ``_2``, ``_3``...). It is added to ``taken``."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    base = re.sub(r"[^a-z0-9_]+", "_", snake.lower()).strip("_") or "tool"
    if base[0].isdigit():
        base = f"t_{base}"
    if keyword.iskeyword(base) or keyword.issoftkeyword(base) or base in _JS_RESERVED or base in _PY_SHADOW or base in reserved:
        base = f"{base}_"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name


def _clean(text: str) -> str:
    """Text a docstring or comment may hold: control characters (but newlines) as spaces, no lone surrogates."""
    text = text.encode("utf-8", "replace").decode("utf-8")
    return re.sub(r"[\x00-\x09\x0b-\x1f\x7f  ]", " ", text.replace("\r\n", "\n")).strip()


def py_docstring(text: str, indent: str) -> str:
    """``text`` as a triple-quoted Python docstring that cannot end early: backslashes and quotes escaped."""
    body = _clean(text).replace("\\", "\\\\").replace('"', '\\"')
    lines = body.split("\n")
    shown = "\n".join(f"{indent}{line}" if line and i else line for i, line in enumerate(lines))
    return f'{indent}"""{shown}\n{indent}"""' if "\n" in body else f'{indent}"""{shown}"""'


def _schema(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _literal_ok(value: object) -> bool:
    return isinstance(value, str | bool | int | float) or value is None


def py_type(schema: Mapping[str, Any], depth: int = 0) -> str:
    """A Python annotation for a JSON Schema."""
    if depth >= MAX_TYPE_DEPTH:
        return "_t.Any"
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(_literal_ok(v) for v in cast("list[object]", enum)):
        values = sorted({json.dumps(v) for v in cast("list[object]", enum)})
        return "_t.Literal[" + ", ".join(repr(json.loads(v)) for v in values) + "]"
    if "const" in schema and _literal_ok(schema["const"]):
        return f"_t.Literal[{schema['const']!r}]"
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list) and options:
            return _union([py_type(_schema(o), depth + 1) for o in cast("list[object]", options)], "_t.Any", " | ")
    kind = schema.get("type")
    kinds = [k for k in cast("list[object]", kind) if isinstance(k, str)] if isinstance(kind, list) else [kind]
    mapped = [_py_kind(k, schema, depth) for k in kinds if isinstance(k, str)]
    return _union(mapped, "_t.Any", " | ")


def _py_kind(kind: str, schema: Mapping[str, Any], depth: int) -> str:
    if kind == "array":
        return f"list[{py_type(_schema(schema.get('items')), depth + 1)}]"
    return {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "null": "None", "object": "dict[str, _t.Any]"}.get(kind, "_t.Any")


def _union(types: list[str], anything: str, sep: str) -> str:
    seen = list(dict.fromkeys(types))
    return anything if not seen or anything in seen else sep.join(seen)


@dataclass(frozen=True, slots=True)
class Param:
    """One argument: its name in the schema, its identifier in Python, whether it is required, its schema."""

    name: str
    ident: str
    required: bool
    schema: dict[str, Any]

    @property
    def description(self) -> str:
        return str(self.schema.get("description", ""))


@dataclass(frozen=True, slots=True)
class Stub:
    """One granted tool as the stubs show it."""

    tool: str
    function: str
    description: str
    params: tuple[Param, ...]
    # The schema names no argument: the function takes any keyword arguments.
    open_arguments: bool


def _params(schema: Mapping[str, Any]) -> tuple[tuple[Param, ...], bool]:
    properties = _schema(schema.get("properties"))
    raw_required = schema.get("required")
    required = {r for r in cast("list[object]", raw_required) if isinstance(r, str)} if isinstance(raw_required, list) else set[str]()
    taken: set[str] = set()
    # Required first, each group by name, so the signature never depends on the order the schema lists them in.
    names = sorted(properties, key=lambda n: (n not in required, n))
    params = tuple(Param(n, identifier(n, taken), n in required, _schema(properties[n])) for n in names)
    return params, not params




def toolset_stubs(tools: Iterable[tuple[str, str, str, Mapping[str, Any]]]) -> tuple[Stub, ...]:
    """One toolset's stubs from ``(tool name, short name, description, input schema)``, in tool-name order."""
    functions: set[str] = set()
    rendered: list[Stub] = []
    for name, short, description, schema in sorted(tools, key=lambda t: t[0]):
        params, open_arguments = _params(schema)
        rendered.append(Stub(name, identifier(short, functions), description, params, open_arguments))
    return tuple(rendered)


def _py_signature(stub: Stub) -> str:
    if stub.open_arguments:
        return "**arguments: _t.Any"
    parts = ["*"]
    for p in stub.params:
        annotation = py_type(p.schema)
        if not p.required:
            nullable = annotation == "_t.Any" or "None" in annotation.split(" | ")
            annotation = f"{annotation} = None" if nullable else f"{annotation} | None = None"
        parts.append(f"{p.ident}: {annotation}")
    return ", ".join(parts)


def _py_doc(stub: Stub) -> str:
    lines = [stub.description.strip() or f"The {stub.tool} tool."]
    if stub.params:
        lines += ["", "Args:"]
        for p in stub.params:
            flag = "" if p.required else ", optional"
            shown = f" (argument {p.name!r})" if p.ident != p.name else ""
            text = " ".join(p.description.split())
            lines.append(f"    {p.ident} ({py_type(p.schema).replace('_t.', '')}{flag}){shown}: {text}".rstrip())
    lines += [
        "",
        f"Runs ``ph tools call {stub.tool}``: the same gateway, grant, approvals and journal. Raises",
        "``ApprovalPending`` while a person decides (end the turn), ``CallError`` on another failure.",
    ]
    return "\n".join(lines)


def _py_function(stub: Stub) -> str:
    if stub.open_arguments:
        body = f"    return _rt.call({stub.tool!r}, arguments)"
    else:
        given = ", ".join(f"{p.name!r}: {p.ident}" for p in stub.params)
        optional = sorted(p.name for p in stub.params if not p.required)
        body = f"    return _rt.invoke({stub.tool!r}, {{{given}}}, frozenset({optional!r}))"
    return f"def {stub.function}({_py_signature(stub)}) -> _rt.CallResult:\n{py_docstring(_py_doc(stub), '    ')}\n{body}\n"


def py_module(toolset: str, rendered: tuple[Stub, ...]) -> str:
    """The source of one toolset's module."""
    names = ", ".join(s.function for s in rendered)
    doc = f"The {toolset} tools of this run: {names}."
    head = f'{py_docstring(doc, "")}\n\nfrom __future__ import annotations\n\nimport typing as _t\n\nfrom {PACKAGE} import _runtime as _rt\n'
    body = "\n\n".join(_py_function(s) for s in rendered)
    exported = ", ".join(repr(s.function) for s in rendered)
    return f"{head}\n\n{body}\n\n__all__ = [{exported}]\n"


def schema_entry(name: str, short: str, document: Mapping[str, Any]) -> tuple[str, str, str, Mapping[str, Any]]:
    """A tool as ``toolset_stubs`` takes it, from its pinned ``<tool>.schema.json`` (whose ``description`` is the
    tool's; its properties and required list are the input schema's)."""
    return name, short, str(document.get("description", "")), document


def module_from_schemas(toolset: str, tools: Iterable[tuple[str, str, str]], read: Callable[[str], bytes]) -> str:
    """A toolset module's source from ``(tool name, short name, schema path)`` and ``read(path)``: what the sandbox
    renders on first import."""
    entries = [schema_entry(name, short, json.loads(read(path))) for name, short, path in tools]
    return py_module(toolset, toolset_stubs(entries))


__all__ = [
    "MAX_TYPE_DEPTH",
    "PACKAGE",
    "Param",
    "Stub",
    "identifier",
    "module_from_schemas",
    "py_docstring",
    "py_module",
    "py_type",
    "schema_entry",
    "toolset_stubs",
]
