#!/usr/bin/env python3
"""Find pytest marks applied to fixtures, which do nothing and will stop collection.

    scripts/check_fixture_marks.py          # report, non-zero if any found

A mark on a fixture function is silently ignored: the fixture still runs and
the tests using it still run, so ``@pytest.mark.skip`` above a fixture reads
like it disables something while disabling nothing.

pytest 9.1 turns this into a collection error, and a collection error fails the
whole run rather than one file — so a routine dependency bump would take out
every shard at once. This keeps them out.

To skip the tests that use a fixture, call ``pytest.skip(reason)`` in the
fixture body instead.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEST_TREES = ("integration-tests", "backend/python/tests")


def _is_fixture(decorator: ast.expr) -> bool:
    """Is this decorator a pytest (or pytest-asyncio) fixture?"""
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(node, ast.Attribute) and node.attr == "fixture"


def _mark_name(decorator: ast.expr) -> str | None:
    """``pytest.mark.skip(...)`` → ``skip``; anything else → None."""
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not isinstance(node, ast.Attribute):
        return None
    parent = node.value
    if isinstance(parent, ast.Attribute) and parent.attr == "mark":
        return node.attr
    return None


def marked_fixtures(source: str) -> list[tuple[int, str, str]]:
    """(line, function name, mark) for every marked fixture in this source."""
    found: list[tuple[int, str, str]] = []
    with warnings.catch_warnings():
        # Some test files carry invalid escape sequences; that is not this check's
        # business and its warning would bury the result.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_fixture(d) for d in node.decorator_list):
            continue
        for decorator in node.decorator_list:
            mark = _mark_name(decorator)
            if mark is not None:
                found.append((decorator.lineno, node.name, mark))
    return found


def scan(repo: Path = REPO) -> list[tuple[Path, int, str, str]]:
    """Every marked fixture under the test trees, as (path, line, name, mark)."""
    hits: list[tuple[Path, int, str, str]] = []
    for tree in TEST_TREES:
        for path in sorted((repo / tree).rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line, name, mark in marked_fixtures(source):
                hits.append((path.relative_to(repo), line, name, mark))
    return hits


def main() -> int:
    hits = scan()
    if not hits:
        print("OK: no pytest marks on fixtures.")
        return 0

    print("FAIL: these fixtures carry a pytest mark, which does nothing:")
    for path, line, name, mark in hits:
        print(f"  - {path}:{line} @pytest.mark.{mark} on fixture '{name}'")
    print()
    print("A mark on a fixture is ignored, so this reads as if it disables")
    print("something while the tests still run. pytest 9.1 makes it a collection")
    print("error, which fails the whole run rather than one file.")
    print("To skip the tests that use a fixture, call pytest.skip(reason) in the")
    print("fixture body instead.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
