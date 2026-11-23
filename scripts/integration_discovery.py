#!/usr/bin/env python3
"""List the integration test files that need a service, following what each file imports.

A file needs a service when its own text matches the pattern, or when a module it imports from the integration tree (at any
depth), or a conftest.py above it in that tree, matches it. Matching only a file's own text missed every file that gets its
store set-up from another test module, so those files never ran in CI.

usage: integration_discovery.py ROOT --needs REGEX [--not REGEX] [--also REGEX] [--exclude-dir DIR ...]

ROOT is the integration test directory, relative to the import root (backend/python), e.g. tests/integration.
--needs: list the test files that need this pattern.
--not:   leave out the files that also need this pattern (they run in another job).
--also:  keep only the files that also need this pattern.
--exclude-dir: leave out the test files under this directory (a job of their own runs them).
Prints one path per line, sorted. Only test_*.py files are listed; helper modules are followed, not listed.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from functools import cache
from pathlib import Path


class Tree:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
        self.texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in self.files}
        self.by_module = {self._module(p): p for p in self.files}

    def _module(self, path: Path) -> str:
        parts = list(path.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def _resolve(self, name: str) -> list[Path]:
        """The tree files that importing `name` loads: the module itself and every package on its way."""
        out, parts = [], name.split(".")
        for i in range(1, len(parts) + 1):
            hit = self.by_module.get(".".join(parts[:i]))
            if hit is not None:
                out.append(hit)
        return out

    @cache  # noqa: B019 - one Tree per run
    def imports(self, path: Path) -> tuple[Path, ...]:
        try:
            tree = ast.parse(self.texts[path], filename=str(path))
        except SyntaxError:
            return ()
        package = self._module(path).split(".")
        if path.name != "__init__.py":
            package = package[:-1]
        found: list[Path] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found += self._resolve(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    anchor = package[: len(package) - node.level + 1]
                    base = ".".join(anchor + ([node.module] if node.module else []))
                else:
                    base = node.module or ""
                found += self._resolve(base)
                for alias in node.names:  # `from pkg import module`
                    found += self._resolve(f"{base}.{alias.name}")
        return tuple(dict.fromkeys(p for p in found if p != path))

    def conftests(self, path: Path) -> list[Path]:
        out, d = [], path.parent
        while True:
            c = d / "conftest.py"
            if c in self.texts and c != path:
                out.append(c)
            if d == self.root or self.root not in d.parents:
                return out
            d = d.parent

    def closure(self, path: Path) -> set[Path]:
        seen: set[Path] = set()
        todo = [path]
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            todo += self.imports(p)
            todo += self.conftests(p)
        return seen

    def needs(self, path: Path, pattern: re.Pattern[str]) -> bool:
        return any(pattern.search(self.texts[p]) for p in self.closure(path))

    def tests(self) -> list[Path]:
        return [p for p in self.files if p.name.startswith("test_")]


def discover(
    root: Path, needs: str, not_: str | None = None, also: str | None = None, exclude_dirs: list[Path] | None = None
) -> list[str]:
    tree = Tree(root)
    want, skip, keep = re.compile(needs), re.compile(not_) if not_ else None, re.compile(also) if also else None
    excluded = [Path(d) for d in exclude_dirs or []]
    out = []
    for p in tree.tests():
        if any(d == p.parent or d in p.parents for d in excluded):
            continue
        if not tree.needs(p, want):
            continue
        if skip is not None and tree.needs(p, skip):
            continue
        if keep is not None and not tree.needs(p, keep):
            continue
        out.append(str(p))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--needs", required=True)
    ap.add_argument("--not", dest="not_")
    ap.add_argument("--also")
    ap.add_argument("--exclude-dir", action="append", default=[], type=Path)
    a = ap.parse_args(argv)
    if a.root.is_absolute() or not a.root.is_dir():
        print(f"not a directory relative to the import root: {a.root}", file=sys.stderr)
        return 2
    for line in discover(a.root, a.needs, a.not_, a.also, a.exclude_dir):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
