"""No reader answers access from ``kg.scopes`` yet.

A drainer of an older release projects a change and acknowledges it without writing ``kg.scopes`` (it predates the table), so a row there can be older
than the record it describes. Today only the projection itself reads the table: the stamp writer compares it with what it computes, the verify and the
restart back-fill look for rows left behind. A difference fails closed there (the verify refuses the flip or the promotion; a restart rebuilds). A
reader that answered a search or a listing from it would answer from that older row. This test lists every statement that names the table, in the
application's modules and in the vetted functions' bodies, and fails on one it does not know: before adding a reader, make sure no drainer can
acknowledge a change without writing the scope (every older one gone, or a durable repair), then add it here.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tests.unit.services.graph_db.test_kg_copied_columns import (
    _body_statements,
    _key,
    _starts_statement,
    python_statements,
)

BACKEND = Path(__file__).resolve().parents[5]
TABLE = "kg.scopes"
KG = "python/app/services/graph_db/postgres/kg/"
MANIFEST = "db/versions/kg/vetted/manifest.yaml"
# (source, tag): the statements that may name the table, each a part of the projection, none a reader's answer
KNOWN = {
    (KG + "stamp.py", "kg.stamp_current_scopes"),          # the stamp writer compares the rows with what it computed
    (KG + "verify.py", "kg.verify_orphan_scopes"),         # the verify names a row left without its record
    (KG + "backfill.py", "kg.orphan_scopes"),              # the restart deletes those rows under the connector's lock
    (MANIFEST, "kg.w_scopes_set"),                         # the vetted writers
    (MANIFEST, "kg.w_scopes_delete"),
}


def statements() -> set[tuple[str, str]]:
    found = set()
    for p in sorted((BACKEND / "python" / "app").rglob("*.py")):
        if TABLE in p.read_text(encoding="utf-8-sig"):
            texts, composed = python_statements(p)
            found |= {(str(p.relative_to(BACKEND)), _key(t)) for t in texts if _starts_statement(t) and TABLE in t.lower()}
            found |= {(str(p.relative_to(BACKEND)), k) for k in composed}       # computed: named, so a new one is seen
    for fn in yaml.safe_load((BACKEND / MANIFEST).read_text(encoding="utf-8")).get("functions", []):
        if any(TABLE in s for s in _body_statements(fn.get("body") or "")):
            found.add((MANIFEST, fn["name"]))
    return found


def test_only_the_projection_names_kg_scopes() -> None:
    assert statements() - KNOWN == set(), (
        "a new statement names kg.scopes: no reader may answer access from it while a drainer of an older release can acknowledge a change "
        "without writing it; see this module's docstring")


def test_the_scan_still_finds_the_known_statements() -> None:
    assert statements() == KNOWN, "a known statement is gone or renamed: update KNOWN, the scan is otherwise blind"
