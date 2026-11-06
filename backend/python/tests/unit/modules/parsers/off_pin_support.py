"""Shared helpers of the off-pin tests for the rasterizer, local Docling and the Docling service."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from tests.unit.modules.parsers.docling_raster_trace import FIXTURE, MODULES
from tests.unit.modules.parsers.parse_pool_trace import module_hashes

TRACE = json.loads(FIXTURE.read_text())
ROOT = Path(__file__).resolve().parents[4]


def current_hashes(name: str) -> dict[str, str]:
    return module_hashes(ROOT / MODULES[name])


def changed(name: str) -> tuple[set[str], set[str]]:
    """``(modified or removed, added)`` entries against the recorded source of main."""
    recorded, current = TRACE["hashes"][name], current_hashes(name)
    modified = {k for k in recorded if recorded[k] != current.get(k)}
    return modified, set(current) - set(recorded)


def top_names(keys: set[str]) -> set[str]:
    """The top-level names behind hash keys (``Class.method`` and ``class Class`` give ``Class``).
    Imports are left out: they are compared as a count by the tests, since a merged import line
    has a new key."""
    names = set()
    for key in keys:
        if key.startswith("import:"):
            continue
        bare = key.removeprefix("class ").removeprefix("assign:")
        names.add(re.split(r"[.:]", bare)[0])
    return names


def imports(keys: set[str]) -> set[str]:
    return {k for k in keys if k.startswith("import:")}


def _is_lane_branch(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.If) or not isinstance(stmt.test, ast.Compare):
        return False
    walrus = stmt.test.left
    return (
        isinstance(walrus, ast.NamedExpr)
        and isinstance(walrus.target, ast.Name)
        and walrus.target.id in {"route", "mode"}
        and isinstance(walrus.value, ast.Call)
        and ast.unparse(walrus.value.func) in {"_lane_route", "_lane_mode"}
    )


def plain(value: object) -> object:
    """What the fixture's JSON makes of a recorded value (tuples become lists)."""
    return json.loads(json.dumps(value, default=str))


def body_without_lane_branch(name: str, qualified: str) -> dict[str, object]:
    """The function's statements minus the one ``if (route := ...)`` branch, hashed, and its arguments."""
    tree = ast.parse((ROOT / MODULES[name]).read_text())
    nodes: list[ast.stmt] = tree.body
    node: ast.AST | None = None
    for part in qualified.split("."):
        node = next(n for n in nodes if getattr(n, "name", None) == part)
        nodes = getattr(node, "body", [])
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    kept = [s for s in node.body if not _is_lane_branch(s)]
    digest = hashlib.sha256("".join(ast.dump(s, include_attributes=False) for s in kept).encode()).hexdigest()[:16]
    return {"body": digest, "args": [a.arg for a in node.args.args]}
