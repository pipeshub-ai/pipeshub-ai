"""C-27 (H6): every test fakes the graph's record-access surface through the one shared fake.

H5 migrated the tests it listed onto ``tests/support/record_access.py``, but that list was a snapshot: H6 found 11
more files still mocking the surface by hand, each free to answer in a way no real provider would. This scan keeps
the next one out.
"""

from __future__ import annotations

import ast
from functools import cache
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.interface.record_access import RECORD_ACCESS_METHODS
from tests.cohesion.support import PYTHON_ROOT
from tests.edition_overlay import extended

if TYPE_CHECKING:
    from pathlib import Path

TESTS_ROOT = PYTHON_ROOT / "tests"
SKIPPED_DIRS = (
    "tests/support/",
    "tests/integration/",
    # provider tests exercise the real Neo4j and ArangoDB implementations of the surface
    "tests/unit/services/graph_db/",
    # main's code graph (#3841): its fakes answer access per traversal hop over code edges, which the shared fake
    # has no graph for; the harness reads no code graph of its own
    "tests/unit/agents/actions/code_graph/",
    # main's code graph read API (#5541): the same per-hop access over code edges, with switchable failures of the
    # check itself; the harness reads no code graph of its own
    "tests/unit/modules/graph_view/",
)
THIS_FILE = "tests/cohesion/test_c27_one_record_access_fake.py"
#: main's one access check (#4255) is on the surface the fake serves and the contract holds, but main's own tests
#: of its callers stub it with an ``AccessCheck`` by design (its rules are tested on real graphs in
#: tests/integration/graph_permissions); the harness's own access decisions are held by the fake and C-24.
SCANNED_METHODS = tuple(name for name in RECORD_ACCESS_METHODS if name != "check_access")
SHARED_FAKE = "InMemoryRecordAccess"
MOCK_KNOBS = ("return_value", "side_effect")

#: "path::enclosing function" -> why a hand-made stub stays: the shared fake cannot produce what the test feeds in
ALLOWED: dict[str, str] = {
    "tests/unit/connectors/api/test_router_delete_record_shapes.py::_answering": (
        "feeds the route malformed access results (falsy non-None, record None or missing, wrongly typed) that no "
        "seeded world produces, to hold the route to failing closed on them"
    ),
    "tests/unit/connectors/api/test_router_delete_record_authz.py::"
    "test_access_result_without_record_details_is_refused": (
        "a details dict with no 'record' key is a provider shape the shared fake never returns"
    ),
}


def _surface_attribute(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute) and node.attr in SCANNED_METHODS:
        return node.attr
    return None


def _answers_from_the_shared_rule(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A spy on the shared fake: it may record or raise, but every answer is ``super()``'s for the same method."""
    if not fn.body or not isinstance(fn.body[-1], (ast.Return, ast.Raise)):
        return False
    pending: list[ast.AST] = list(fn.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return):
            value = node.value.value if isinstance(node.value, ast.Await) else node.value
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == fn.name
                and isinstance(value.func.value, ast.Call)
                and isinstance(value.func.value.func, ast.Name)
                and value.func.value.func.id == "super"
            ):
                return False
        pending.extend(ast.iter_child_nodes(node))
    return True


def _extends_shared_fake(cls: ast.ClassDef) -> bool:
    return any(
        (isinstance(base, ast.Name) and base.id == SHARED_FAKE)
        or (isinstance(base, ast.Attribute) and base.attr == SHARED_FAKE)
        for base in cls.bases
    )


class _Scan(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[tuple[int, str, str]] = []
        self._classes: list[ast.ClassDef] = []
        self._functions: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._classes.append(node)
        self.generic_visit(node)
        self._classes.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = bool(self._classes) and node in self._classes[-1].body
        if node.name in SCANNED_METHODS and not (
            is_method and _extends_shared_fake(self._classes[-1]) and _answers_from_the_shared_rule(node)
        ):
            self._add(node.lineno, f"defines {node.name}")
        self._functions.append(node.name)
        saved, self._classes = self._classes, [] if not is_method else self._classes
        self.generic_visit(node)
        self._classes = saved
        self._functions.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _targets(self, targets: list[ast.expr]) -> None:
        for target in targets:
            if (name := _surface_attribute(target)) is not None:
                self._add(target.lineno, f"assigns .{name}")
            elif (
                isinstance(target, ast.Attribute)
                and target.attr in MOCK_KNOBS
                and (name := _surface_attribute(target.value)) is not None
            ):
                self._add(target.lineno, f"sets .{name}.{target.attr}")

    def visit_Assign(self, node: ast.Assign) -> None:
        self._targets(node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._targets([node.target])
        self.generic_visit(node)

    def _add(self, line: int, what: str) -> None:
        self.found.append((line, what, self._functions[-1] if self._functions else "<module>"))


def local_fakes(source: str) -> list[tuple[int, str, str]]:
    """(line, what, enclosing function) for each place the source fakes a method of the record-access surface."""
    scan = _Scan()
    scan.visit(ast.parse(source))
    return sorted(scan.found)


def _scanned_files() -> list[Path]:
    files = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        rel = path.relative_to(PYTHON_ROOT).as_posix()
        if rel == THIS_FILE or rel.startswith(SKIPPED_DIRS):
            continue
        files.append(path)
    return files


@cache
def _allowed() -> dict[str, str]:
    """``ALLOWED`` with an edition overlay's entries (``tests/edition_overlay.py``, ``record_access_fake.json``)."""
    return extended(ALLOWED, "record_access_fake", "allowed")


@cache
def _offences() -> tuple[list[str], set[str]]:
    offences, allowed_seen = [], set()
    allowed = _allowed()
    for path in _scanned_files():
        rel = path.relative_to(PYTHON_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        # Faking a method of the surface names it, so only such files are parsed.
        if not any(method in source for method in SCANNED_METHODS):
            continue
        for line, what, scope in local_fakes(source):
            if f"{rel}::{scope}" in allowed:
                allowed_seen.add(f"{rel}::{scope}")
            else:
                offences.append(f"{rel}:{line}: {what}")
    return offences, allowed_seen


def test_no_test_fakes_record_access_by_hand() -> None:
    offences, _ = _offences()
    assert offences == [], (
        "these tests fake the graph's record-access surface by hand; seed InMemoryRecordAccess from "
        "tests/support/record_access.py and .serve() it onto the graph double instead:\n  " + "\n  ".join(offences)
    )


def test_every_allowed_stub_still_exists() -> None:
    _, seen = _offences()
    assert set(_allowed()) - seen == set(), "drop these from ALLOWED: no hand-made stub is left there"


SPY = (
    "class Spy(InMemoryRecordAccess):\n"
    "    async def filter_accessible_record_ids(self, record_ids, user_id, org_id, *, transaction=None):\n"
    "        self.calls.append(record_ids)\n"
    "        if not record_ids:\n"
    "            raise RuntimeError('down')\n"
    "        return await super().filter_accessible_record_ids(record_ids, user_id, org_id, transaction=transaction)\n"
    "    async def check_record_access_with_details(self, user_id, org_id, record_id, transaction=None):\n"
    "        raise AssertionError('asked one at a time')\n"
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "def f(g):\n    g.check_record_access_with_details = AsyncMock()",
            [(2, "assigns .check_record_access_with_details", "f")],
        ),
        (
            "g.check_record_access_with_details.return_value = None",
            [(1, "sets .check_record_access_with_details.return_value", "<module>")],
        ),
        (
            "g.filter_accessible_record_ids.side_effect = boom",
            [(1, "sets .filter_accessible_record_ids.side_effect", "<module>")],
        ),
        (
            "class G:\n    async def check_record_access_with_details(self, u, o, r):\n        return None",
            [(2, "defines check_record_access_with_details", "<module>")],
        ),
        (
            "class G(InMemoryRecordAccess):\n"
            "    async def filter_accessible_record_ids(self, ids, u, o, *, transaction=None):\n"
            "        return set(ids)",
            [(2, "defines filter_accessible_record_ids", "<module>")],
        ),
        (
            "access.serve(g)\nawait g.check_record_access_with_details('u', 'o', 'r')\n"
            "g.get_record_by_id.return_value = None\ng.check_record_access_with_details.assert_not_called()\n" + SPY,
            [],
        ),
    ],
    ids=["assign", "return_value", "side_effect", "def", "subclass-answering-itself", "negative"],
)
def test_matcher(source: str, expected: list[tuple[int, str, str]]) -> None:
    assert local_fakes(source) == expected
