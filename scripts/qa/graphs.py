"""Reverse import graphs: which tests reach a changed module, and through how many hops."""

from __future__ import annotations

import ast
import posixpath
import re
from collections import deque
from collections.abc import Iterable
from pathlib import Path

from qa import pyparse

PY_ROOT = "backend/python"
PY_PACKAGE = "app"
TS_ROOTS = ("backend/nodejs/apps/", "frontend/")
# tsconfig "paths" for the frontend; longest prefix wins.
TS_ALIASES = {"frontend/": [
    ("@/knowledge-base/", "frontend/app/(main)/knowledge-base/"),
    ("@/workspace/", "frontend/app/(main)/workspace/"),
    ("@/chat/", "frontend/app/(main)/chat/"),
    ("@/", "frontend/"),
]}

TS_IMPORT = re.compile(
    r"""(?:from\s+|import\s*\(\s*|require\s*\(\s*|import\s+)['"]((?:\.{1,2}/|@/)[^'"]+)['"]"""
)
TS_EXT = (".ts", ".tsx", ".js", "/index.ts", "/index.tsx")


class Graph:
    def __init__(self) -> None:
        self.deps: dict[str, set[str]] = {}
        self.rdeps: dict[str, set[str]] = {}

    def add(self, src: str, dep: str) -> None:
        if src == dep:
            return
        self.deps.setdefault(src, set()).add(dep)
        self.rdeps.setdefault(dep, set()).add(src)

    def dependents(self, start: Iterable[str], depth: int) -> dict[str, int]:
        """Every node reaching `start` within `depth` hops, with its distance."""
        seen = {s: 0 for s in start}
        queue = deque(seen)
        while queue:
            node = queue.popleft()
            if seen[node] >= depth:
                continue
            for parent in self.rdeps.get(node, ()):
                if parent not in seen:
                    seen[parent] = seen[node] + 1
                    queue.append(parent)
        return seen


# --- Python -------------------------------------------------------------------

def py_module(path: str) -> str | None:
    """backend/python/app/x/y.py -> app.x.y (packages map to their __init__)."""
    prefix = f"{PY_ROOT}/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    parts = path[len(prefix):-3].split("/")
    if parts[0] != PY_PACKAGE:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _py_refs(source: str, module: str | None, is_pkg: bool, known: set[str]) -> set[str]:
    try:
        tree = pyparse.parse(source)
    except SyntaxError:
        return set()
    refs: set[str] = set()
    package = module if is_pkg else (module.rsplit(".", 1)[0] if module and "." in module else module)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and package:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
                base = ".".join(anchor + ([base] if base else []))
            if base:
                refs.add(base)
                refs.update(f"{base}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(PY_PACKAGE + "."):
            # mock.patch("app.x.y.func") is a dependency the import list misses.
            refs.add(node.value)
    out = set()
    for ref in refs:
        parts = ref.split(".")
        while parts:
            name = ".".join(parts)
            if name in known:
                out.add(name)
                break
            parts.pop()
    return out


def python_graph(root: Path, files: list[str]) -> tuple[Graph, dict[str, str]]:
    """Graph over app modules plus every python test file under backend/python."""
    modules = {m: f for f in files if (m := py_module(f))}
    known = set(modules)
    g = Graph()
    for mod, path in modules.items():
        source = (root / path).read_text(encoding="utf-8", errors="replace")
        for dep in _py_refs(source, mod, path.endswith("__init__.py"), known):
            g.add(mod, dep)
    for path in files:
        if path.startswith(f"{PY_ROOT}/") and path.endswith(".py") and py_module(path) is None and "/test" in path:
            source = (root / path).read_text(encoding="utf-8", errors="replace")
            for dep in _py_refs(source, None, False, known):
                g.add(path, dep)
    return g, modules


# --- TypeScript ----------------------------------------------------------------

def _ts_resolve(src: str, spec: str, existing: set[str]) -> str | None:
    if spec.startswith("@/"):
        root = next((r for r in TS_ALIASES if src.startswith(r)), None)
        target = next((t + spec[len(a):] for a, t in TS_ALIASES.get(root, []) if spec.startswith(a)), None)
        if target is None:
            return None
        base = posixpath.normpath(target)
    else:
        base = posixpath.normpath(posixpath.join(posixpath.dirname(src), spec))
    if base in existing:
        return base
    stem = base.removesuffix(".js")
    for ext in TS_EXT:
        if stem + ext in existing:
            return stem + ext
    return None


def typescript_graph(root: Path, files: list[str]) -> Graph:
    ts = [f for f in files if f.startswith(TS_ROOTS) and f.endswith((".ts", ".tsx")) and "/node_modules/" not in f]
    existing = set(ts)
    g = Graph()
    for path in ts:
        source = (root / path).read_text(encoding="utf-8", errors="replace")
        for m in TS_IMPORT.finditer(source):
            dep = _ts_resolve(path, m.group(1), existing)
            if dep:
                g.add(path, dep)
    return g
