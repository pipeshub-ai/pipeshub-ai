"""CPD0-23: every code the worker's journal client reacts to is one Node answers with.

Both sides take the codes from ``contracts/harness/ext/pipeshub/errors.json`` (R3 D18: they were two
hand-kept lists, and the Python one waited for a ``RUN_NOT_FOUND`` Node never sends).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.harness.contracts.generated.pipeshub.errors import ERROR_CODES, WIRE_PREFIX
from app.harness.journal_node import client
from harness_core.journal.children import ChildRefusal

CLIENT = Path(client.__file__)
CODE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def _code_literals(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Upper-snake string constants the client compares a code with, and the prefixes it tests."""
    codes: set[str] = set()
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            prefixes |= {a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        operands = [node.left, *node.comparators] if isinstance(node, ast.Compare) else node.elts if isinstance(node, ast.Set) else []
        codes |= {o.value for o in operands if isinstance(o, ast.Constant) and isinstance(o.value, str) and CODE.match(o.value)}
    return codes, prefixes


def test_every_code_the_client_reacts_to_is_in_the_catalog() -> None:
    codes, prefixes = _code_literals(ast.parse(CLIENT.read_text(encoding="utf-8")))
    assert codes, "the scan found no code: it is broken"
    assert codes <= set(ERROR_CODES), sorted(codes - set(ERROR_CODES))
    for prefix in prefixes:
        assert any(code.startswith(prefix) for code in ERROR_CODES), prefix


def test_child_refusals_are_catalog_codes() -> None:
    # ACCESS_REFUSED is no code of its own: it groups Node's access and engine codes, which ``ChildRefused.code`` names.
    grouped = {ChildRefusal.ACCESS_REFUSED}
    assert {refusal.value.upper() for refusal in ChildRefusal if refusal not in grouped} <= set(ERROR_CODES)


def test_the_client_strips_the_catalog_s_prefix() -> None:
    response = type("R", (), {"json": lambda self: {"error": {"code": f"{WIRE_PREFIX}STALE_ATTEMPT"}}})()
    assert client.error_code(response) == "STALE_ATTEMPT"


def test_a_code_node_never_sends_is_refused_by_the_scan(tmp_path: Path) -> None:
    source = 'def f(code):\n    return code == "RUN_NOT_FOUND"\n'
    codes, _ = _code_literals(ast.parse(source))
    assert codes - set(ERROR_CODES) == {"RUN_NOT_FOUND"}
