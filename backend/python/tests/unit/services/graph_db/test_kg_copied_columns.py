"""No reader takes the copied columns of ``kg.records`` (rule, model, origin, vrid, external_id, record_type) from kg.

The projector writes a row whose value the records writer refuses with that column empty (``kg.project``) and takes only the row's connector off the readers. That is safe only while
no access check or listing reads those columns from kg: the stamps are compiled from the graph, and the one reader of a copied column, the listing's ``record_type`` filter, reads
only connectors kg serves. This test reads every SQL text of the application and of the migrations that names ``kg.records``, and fails on any other read of those columns: by an alias
of the table, by a bare column name beside it, or through ``SELECT *`` that hands the whole row out.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[5]
COPIED = ("rule", "model", "origin", "vrid", "external_id", "record_type")
_C = "|".join(COPIED)
_NOT_ALIAS = r"(?!(?:WHERE|ON|USING|JOIN|LEFT|RIGHT|INNER|CROSS|ORDER|GROUP|LIMIT|SET|LATERAL|WITH|FOR|AND|OR)\b)"
_BOUND = re.compile(rf"\bkg\.records\b\s+(?:AS\s+)?{_NOT_ALIAS}(\w+)", re.I)
# a row of kg.records handed out whole by a subquery: its alias is the table's for what reads it
_SUBQUERY = re.compile(rf"\(\s*SELECT\s+(?:\w+\.)?\*\s+FROM\s+kg\.records\b[^()]*(?:\([^()]*\)[^()]*)*\)\s+(?:AS\s+)?{_NOT_ALIAS}(\w+)", re.I)
_STAR = re.compile(r"\bSELECT\s+(?:\w+\.)?\*\s+FROM\s+kg\.records\b", re.I)

# (source, what): the reads that may take a copied column or the whole row
ALLOWED = {
    ("python/app/services/graph_db/postgres/kg/listing.py", "r.record_type"),        # the listing's filter: a connector kg serves only
    ("python/app/services/graph_db/postgres/kg/verify.py", "*"),                    # the verify compares the whole row with the graph: what must see the difference
    ("db/versions/kg/0001_baseline.sql", "*"),                                      # the table's definition
    ("db/versions/kg/vetted/w_records_upsert.sql", "*"),                            # the writer
    ("db/versions/kg/vetted/manifest.yaml#kg.w_records_upsert", "*"),
}


_SQL = re.compile(r"\b(?:SELECT|FROM|WHERE|JOIN|AND|OR|ORDER\s+BY|GROUP\s+BY|LIMIT|UNION|EXISTS|ANY)\b|\$\d|::", re.I)
# a whole row of the table handed out again: ``SELECT *`` over a subquery that hands it out
_OUTER_STAR = re.compile(r"\bSELECT\s+\*\s+FROM\s*\(\s*SELECT\s+(?:\w+\.)?\*\s+FROM\s+kg\.records\b", re.I)


def reads(sql_texts: list[str]) -> set[str]:
    """The reads of copied columns of kg.records in one source, given as its SQL texts. A query may be split across texts: the aliases are the source's, and a source that names
    the table has every SQL text of it read for bare column names (a fragment ``" FROM kg.records"`` and another ``"SELECT external_id"`` are one query)."""
    aliases = {m.group(1).lower() for s in sql_texts for rx in (_BOUND, _SUBQUERY) for m in rx.finditer(s)}
    # a text is SQL when it has a keyword of it or names a column of an alias of the table (a list of columns kept apart from its statement)
    texts = [s for s in sql_texts if _SQL.search(s) or any(m.group(1).lower() in aliases for m in re.finditer(r"\b(\w+)\.\w+", s))]
    found: set[str] = set()
    if not any("kg.records" in s for s in texts):
        return found
    for s in texts:
        found |= {m.group(0) for m in re.finditer(rf"\b(\w+)\.({_C})\b", s) if m.group(1).lower() in aliases}
        if any(m.group(1).lower() in aliases for m in re.finditer(r"\b(\w+)\.\*", s)) or _OUTER_STAR.search(s):
            found.add("*")                                  # ``alias.*`` of the table or of a subquery that hands its row out, or ``SELECT *`` over such a subquery
        if any(not s[:m.start()].rstrip().endswith("(") for m in _STAR.finditer(s)):      # a subquery's star is read through its alias, above
            found.add("*")
        found |= {m.group(1) for m in re.finditer(rf"(?<![.\w'\">])({_C})(?![\w'\"])", s) if not _quoted_or_doc(s, m.start())}
    return found


def _quoted_or_doc(s: str, at: int) -> bool:
    """A name that is a JSON key of a document (``doc->>'origin'``) or a word of a comment, not a column."""
    line_start = s.rfind("\n", 0, at) + 1
    return "--" in s[line_start:at] or s[max(0, at - 4):at].endswith(("->>", "->"))


def _python_texts(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def sources() -> dict[str, list[str]]:
    """Every source of SQL that names kg.records: the application's modules, the migrations' SQL files and the vetted manifests' function bodies."""
    out: dict[str, list[str]] = {}
    for p in sorted((BACKEND / "python" / "app").rglob("*.py")):
        if "kg.records" in p.read_text(encoding="utf-8-sig"):
            out[str(p.relative_to(BACKEND))] = _python_texts(p)
    for p in sorted((BACKEND / "db" / "versions").rglob("*.sql")):
        if "kg.records" in (text := p.read_text(encoding="utf-8")):
            out[str(p.relative_to(BACKEND))] = [text]
    for p in sorted((BACKEND / "db" / "versions").rglob("manifest.yaml")):
        for fn in yaml.safe_load(p.read_text(encoding="utf-8")).get("functions", []):
            if "kg.records" in (body := fn.get("body") or ""):
                out[f"{p.relative_to(BACKEND)}#{fn['name']}"] = [body]
    return out


def test_no_reader_takes_a_copied_column_of_kg_records_from_kg() -> None:
    found = {(src, what) for src, texts in sources().items() for what in reads(texts)}
    allowed = {(s, w) for s, w in found if (s, w) in ALLOWED or (s, "*") in ALLOWED}
    assert found - allowed == set(), "a read of a column the projector may leave empty for a connector kg no longer serves: read it from the graph, or serve it only for a served connector"


def test_the_scan_finds_the_modules_it_must_read() -> None:
    srcs = sources()
    assert {"python/app/services/graph_db/postgres/kg/listing.py", "python/app/services/graph_db/postgres/kg/read.py",
            "python/app/services/graph_db/postgres/kh_listing.py", "python/app/services/graph_db/postgres/kg/stamp.py"} <= set(srcs)
    assert ("python/app/services/graph_db/postgres/kg/listing.py", "r.record_type") in {(s, w) for s, t in srcs.items() for w in reads(t)}


@pytest.mark.parametrize("texts, found", [
    (["SELECT r.sk, r.rule FROM kg.records r WHERE r.org_id = $1"], {"r.rule"}),
    (["SELECT x.origin FROM unnest($2::text[]) q CROSS JOIN LATERAL (SELECT * FROM kg.records k WHERE k.coll = ANY('{1,2}'::smallint[]) LIMIT 3) x"], {"x.origin"}),
    (["SELECT * FROM kg.records WHERE org_id = $1"], {"*"}),
    (["SELECT sk, external_id FROM kg.records WHERE org_id = $1"], {"external_id"}),
    (["SELECT r.sk FROM kg.records r WHERE r.org_id = $1", " AND r.vrid = $2"], {"r.vrid"}),                  # a query composed from two texts
    (["SELECT external_id", " FROM kg.records WHERE org_id = $1"], {"external_id"}),                         # a bare column in another text than the table
    (["SELECT x.* FROM (SELECT * FROM kg.records) x"], {"*"}),                                               # the whole row through a subquery's alias
    (["SELECT * FROM (SELECT * FROM kg.records WHERE org_id = $1) x"], {"*"}),
    (["SELECT r.* FROM kg.records r"], {"*"}),
    (["SELECT r.id FROM unnest($2::text[]) q CROSS JOIN LATERAL (SELECT * FROM kg.records k WHERE k.id = q.id LIMIT 3) r"], set()),   # as read.py: only safe columns of the row
    (["origin", "SELECT r.sk FROM kg.records r"], set()),                                                   # a dict key of the source is no SQL
    (["r.sk, r.id, vrid", "SELECT {cols} FROM kg.records r"], {"vrid"}),                                     # a list of columns kept apart from its statement
    (["SELECT r.sk, r.name_key FROM kg.records AS r WHERE r.org_id = $1"], set()),
    (["SELECT n.doc->>'origin' AS o FROM graph.nodes n JOIN kg.records r ON r.id = n.id"], set()),
    (["SELECT x.model FROM retrieval.spaces x"], set()),
])
def test_the_scan_names_each_way_of_reading_a_copied_column(texts: list[str], found: set[str]) -> None:
    assert reads(texts) == found
