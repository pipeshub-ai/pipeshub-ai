"""A test that belongs to one graph store is not selected by the other store's job.

The graph jobs of ``backend-matrix.yml`` run every discovered file with ``-k neo4j`` or ``-k arango``, and
``-k`` matches a substring of the whole node id: the file name, the test name and its parameters. A file
for one store (``..._arango.py``) is picked up by that store's job through its name; a test inside it
whose own name says ``neo4j`` is picked up by the other store's job too, which has no such store to run
it against, and each case fails after its connection timeout. ``arango_only`` does not prevent that: the
conftest only deselects on a parameter named ``env``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1] / "integration"
_STORES = ("neo4j", "arango")


def _store_of(path: Path) -> str | None:
    named = [store for store in _STORES if store in path.name.lower()]
    return named[0] if len(named) == 1 else None


def _wrong_names(source: str, store: str) -> list[str]:
    others = [other for other in _STORES if other != store]
    names = [
        node.name for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return sorted(name for name in names if any(other in name.lower() for other in others))


def test_the_check_sees_a_test_the_other_job_would_select() -> None:
    assert _wrong_names("async def test_agrees_with_neo4j_use() -> None: ...", "arango") == ["test_agrees_with_neo4j_use"]
    assert _wrong_names("class TestArango:\n    def test_x(self): ...", "neo4j") == ["TestArango"]
    assert _wrong_names("def test_agrees(): ...", "arango") == []


def test_no_store_specific_integration_file_has_a_test_the_other_stores_job_selects() -> None:
    offenders = {}
    for path in sorted(_TESTS.rglob("test_*.py")):
        store = _store_of(path)
        if store is None:
            continue
        if wrong := _wrong_names(path.read_text(encoding="utf-8-sig"), store):
            offenders[str(path.relative_to(_TESTS))] = wrong
    assert offenders == {}, (
        "these tests would be selected by the other store's `-k` job; rename them: " + repr(offenders)
    )

