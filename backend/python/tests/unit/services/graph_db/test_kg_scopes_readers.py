"""No reader answers access from ``kg.scopes`` yet.

A drainer of an older release projects a change and acknowledges it without writing ``kg.scopes`` (it predates the table), so a row there can be older
than the record it describes. Today only the projection itself reads the table: the stamp writer compares it with what it computed, the verify and the
restart back-fill look for rows left behind. A difference fails closed there (the verify refuses the flip or the promotion; a restart rebuilds). A
reader that answered a search or a listing from it would answer from that older row.

So every mention of the table is pinned, not only the ones that look like a statement: each string literal of the application and of the migrations'
Python, each SQL file and each vetted function body that names it (in any case), counted per enclosing function (or vetted function, or file).
One more mention anywhere fails the test, whatever shape the SQL around it has. Before adding a reader, make sure no drainer can acknowledge a change
without writing the scope (every older one gone, or a durable repair), then add it here.

shortcut: a name split across literals (``"kg." + "scopes"``, ``f"kg.{table}"``) is not seen; the SQL lint and review are what catch that, until the
statements are parsed with their parts resolved.
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[5]
NAME = re.compile(r"\bkg\s*\.\s*scopes\b", re.I)
KG = "python/app/services/graph_db/postgres/kg/"
# (file, enclosing function or vetted function, or <module> / <file>) -> mentions. None of them answers a search or a listing.
KNOWN = Counter({
    (KG + "stamp.py", "<module>"): 1,                                   # the module docstring
    (KG + "stamp.py", "_current_scopes"): 1,                            # the stamp writer compares the rows with what it computed
    (KG + "verify.py", "verify_app"): 1,                                # the verify names a row left without its record
    (KG + "backfill.py", "backfill_scope.reset"): 1,                    # the restart deletes those rows under the connector's lock
    ("db/versions/kg/0008_scopes_r13.sql", "<file>"): 7,                # the table, its index, settings, policy and grants
    ("db/versions/kg/0008_scopes_r13.py", "<module>"): 1,               # its docstring
    ("db/versions/kg/0008_scopes_r13.py", "downgrade"): 3,              # the downgrade that drops it
    ("db/versions/kg/vetted/manifest.yaml", "kg.w_scopes_set"): 1,      # the vetted writers
    ("db/versions/kg/vetted/manifest.yaml", "kg.w_scopes_delete"): 1,
    ("db/versions/kg/vetted/w_scopes_set.sql", "<file>"): 2,            # their generated files (the body and the pin)
    ("db/versions/kg/vetted/w_scopes_delete.sql", "<file>"): 2,
})


def python_mentions(text: str, path: str) -> Counter:
    """Mentions of the table in the string literals of one module, by enclosing function (dotted with its classes and outer functions)."""
    out: Counter = Counter()

    def walk(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, [*scope, child.name])
                continue
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and (n := len(NAME.findall(child.value))):
                out[(path, ".".join(s for s in scope if s) or "<module>")] += n
            walk(child, scope)
    walk(ast.parse(text), [])
    return out


def manifest_mentions(text: str, path: str) -> Counter:
    return Counter({(path, fn["name"]): n for fn in yaml.safe_load(text).get("functions", []) if (n := len(NAME.findall(fn.get("body") or "")))})


def mentions() -> Counter:
    found: Counter = Counter()
    for p in sorted([*(BACKEND / "python" / "app").rglob("*.py"), *(BACKEND / "db").rglob("*.py")]):
        if "tests" not in p.relative_to(BACKEND).parts and NAME.search(text := p.read_text(encoding="utf-8-sig")):      # (a literal is in the text)
            found += python_mentions(text, str(p.relative_to(BACKEND)))
    for p in sorted((BACKEND / "db").rglob("*.sql")):
        if "tests" not in p.relative_to(BACKEND).parts and (n := len(NAME.findall(p.read_text(encoding="utf-8")))):
            found[(str(p.relative_to(BACKEND)), "<file>")] += n
    for p in sorted((BACKEND / "db").rglob("manifest.yaml")):
        if "tests" not in p.relative_to(BACKEND).parts:
            found += manifest_mentions(p.read_text(encoding="utf-8"), str(p.relative_to(BACKEND)))
    return found


def test_every_mention_of_kg_scopes_is_one_the_projection_makes() -> None:
    found = mentions()
    assert found - KNOWN == Counter(), (
        "a new mention of kg.scopes: no reader may answer access from it while a drainer of an older release can acknowledge a change without "
        "writing it; see this module's docstring")
    assert KNOWN - found == Counter(), "a known mention is gone or moved: update KNOWN, the scan is otherwise blind to a new one in its place"


# Each shape a review found the first version of this guard blind to.
@pytest.mark.parametrize("source", [
    'SQL = "SELECT * FROM KG.SCOPES"',                                                       # upper case: unquoted SQL folds it
    'SQL = "SELECT s.sk FROM {} s".format("kg.scopes")',                                     # str.format composition
    'SQL = "SELECT s.sk FROM kg.scopes s WHERE s.org_id = {}".format(1)',
    'SQL = "-- the scopes\\nSELECT * FROM kg.scopes"',                                         # a statement after a line comment
    'def page(cols, sc, where):\n    return f"(SELECT {cols} FROM kg.scopes s WHERE {sc}{where})"',   # a parenthesised select
    'A = "SELECT s.restricted"\nB = " FROM kg.scopes s WHERE s.org_id = $1"',               # a continuation of another text
    'SQL = "/* pipeshub:x */ SELECT 1 FROM Kg . Scopes"',
])
def test_a_reader_in_any_shape_is_a_new_mention(source: str) -> None:
    assert sum(python_mentions(source, "m.py").values()) == 1


def test_one_more_statement_in_an_allowlisted_vetted_function_is_a_new_mention() -> None:
    manifest = (BACKEND / "db/versions/kg/vetted/manifest.yaml").read_text(encoding="utf-8")
    before = manifest_mentions(manifest, "m")[("m", "kg.w_scopes_set")]
    body = manifest.index("body: |", manifest.index("- name: kg.w_scopes_set")) + len("body: |\n")
    more = manifest[:body] + "      -- SELECT restricted FROM kg.scopes\n" + manifest[body:]
    assert manifest_mentions(more, "m")[("m", "kg.w_scopes_set")] == before + 1


def test_one_more_mention_in_an_allowlisted_function_is_a_new_mention() -> None:
    src = 'class W:\n    async def f(self):\n        a = "SELECT 1 FROM kg.scopes"\n        b = "(SELECT 2 FROM kg.scopes)"\n'
    assert python_mentions(src, "m.py") == Counter({("m.py", "W.f"): 2})
