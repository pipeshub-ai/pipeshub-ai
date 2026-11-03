"""Pure parts of the pgvector write path: batch preparation, payload merges, and the SQL rules."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.services.vector_db.models import SparseVector, VectorPoint
from app.services.vector_db.pgvector import writes
from app.services.vector_db.pgvector.errors import (
    OrgMismatch,
    OrgRequired,
    PgVectorError,
)

A, B = "a" * 24, "b" * 24


def pt(i: int, org: str = A, **payload) -> VectorPoint:
    meta = {"orgId": org, "virtualRecordId": "r1", "blockId": f"b{i}"}
    return VectorPoint(id=str(uuid.UUID(int=i)), dense_vector=[0.5, -0.25], payload={"page_content": f"t{i}", "metadata": meta, **payload})


def test_prepare_builds_columns_and_groups() -> None:
    p = pt(1, connectorIds=["c1"], recordGroupIds=["g1"], extra="x")
    p.sparse_vector = SparseVector([3, 9], [1.0, 2.0])
    b = writes.prepare([p, pt(2)])
    assert b.org == A and b.vrids == ["r1", "r1"] and b.block_ids == ["b1", "b2"] and b.dims == [2, 2]
    assert b.embeddings[0] == "[0.5,-0.25]" and b.contents == ["t1", "t2"]
    assert (b.term_start, b.term_end, b.terms, b.weights) == ([0, 2], [2, 2], [3, 9], [1.0, 2.0])
    assert '"extra":"x"' in b.payloads[0] and "connectorIds" not in b.payloads[0] and "page_content" not in b.payloads[0]
    assert b.groups == {"r1": {"connectorIds": ["c1"], "recordGroupIds": ["g1"]}}   # only the arrays the payload carries


def test_prepare_keeps_one_weight_per_term() -> None:
    p = pt(1)
    p.sparse_vector = SparseVector([3, 9, 3], [1.0, 2.0, 5.0])         # a repeated index would count twice in the document frequency
    b = writes.prepare([p])
    assert (b.terms, b.weights, b.term_end) == ([3, 9], [5.0, 2.0], [2])


def test_prepare_last_point_wins_and_entities_group_by_id() -> None:
    ent = VectorPoint(id=str(uuid.UUID(int=7)), dense_vector=[1.0], payload={"metadata": {"orgId": A, "entityType": "T", "entityId": "e", "level": 2}})
    b = writes.prepare([pt(1), pt(1), ent])
    assert len(b.point_ids) == 2 and b.group_keys[1] == str(uuid.UUID(int=7)) and b.vrids[1] is None and b.ent_levels[1] == "2"


def test_prepare_refuses_what_it_cannot_write() -> None:
    with pytest.raises(OrgMismatch):
        writes.prepare([pt(1), pt(2, org=B)])
    with pytest.raises(OrgRequired):
        writes.prepare([VectorPoint(id=str(uuid.UUID(int=1)), dense_vector=[1.0], payload={"metadata": {}})])
    with pytest.raises(PgVectorError, match="not a UUID"):
        writes.prepare([VectorPoint(id="nope", dense_vector=[1.0], payload={"metadata": {"orgId": A}})])
    with pytest.raises(PgVectorError, match="no dense vector"):
        writes.prepare([VectorPoint(id=str(uuid.UUID(int=1)), payload={"metadata": {"orgId": A}})])


def test_a_payload_merge_cannot_move_a_point() -> None:
    with pytest.raises(PgVectorError, match="may not change"):
        writes._merge_args({"metadata": {"blockId": "other"}})
    assert writes._merge_args({"a": 1, "connectorIds": ["c"], "page_content": "t"}) == ('{"a":1}', "t")


def test_the_only_string_built_statements_are_the_three_that_splice_a_compiled_filter() -> None:
    """The runtime's static SQL checks over the whole package (no session SQL, every literal statement tagged). A statement built from
    parts is allowed in exactly three places of ``writes.py``, where the compiled filter's structure is spliced in (every value in it a
    ``$n`` bound parameter, which ``test_sql_filters`` pins); any other built statement fails here and in the shared guard."""
    import ast

    from app.core.db import sql_lint

    root = Path(writes.__file__).parent
    found = sql_lint.scan_paths(sorted(root.glob("*.py")))
    assert [str(f) for f in found if "string-built" not in f.rule] == []
    built = [f for f in found if "string-built" in f.rule]
    assert {Path(f.path).name for f in built} == {"writes.py"}
    tree = ast.parse((root / "writes.py").read_text())
    owner = {n: f.name for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) for n in range(f.lineno, f.end_lineno + 1)}
    assert sorted(owner[f.line] for f in built) == ["delete_batch", "group_keys_matching", "merge_payload"]
    for name in ("reads.py", "writes.py"):
        text = (root / name).read_text()
        assert text.count("/* pipeshub:pgvector.") >= text.count("tx.fetch") + text.count("tx.execute") - 3


def test_a_point_without_a_payload_is_refused_as_having_no_org_not_crashed_on() -> None:
    from app.services.vector_db.pgvector.errors import OrgRequired

    with pytest.raises(OrgRequired):
        writes.prepare([VectorPoint(id=str(uuid.UUID(int=1)), dense_vector=[1.0], payload=None)])


# ---- every statement of a write names its org ----------------------------------------------------------------------------------------
class RecordingTx:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.sql: list[str] = []
        self.rows = rows or []

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        self.sql.append(sql)
        return self.rows

    async def fetchval(self, sql: str, *args: object) -> int:
        self.sql.append(sql)
        return 0

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return "UPDATE 0"


@pytest.mark.asyncio
@pytest.mark.parametrize("safe", [False, True])
async def test_every_statement_of_a_delete_pins_the_org_including_the_lock_that_selects_the_victims(safe: bool) -> None:
    from app.services.vector_db.pgvector.sql_filters import Compiled

    tx = RecordingTx([{"point_sk": 1}])
    await writes.delete_batch(tx, A, 1, Compiled("p.vrid = ANY($3::text[])", [["r"]]), safe_mode=safe)
    assert len(tx.sql) == 2
    lock, victims = tx.sql
    assert "p.org_id = $1" in lock and "FOR UPDATE" in lock
    assert victims.count("org_id = $1") >= 3
    for statement in tx.sql:
        assert "$1" in statement and "org_id" in statement


@pytest.mark.asyncio
async def test_payload_and_group_statements_pin_the_org() -> None:
    from datetime import UTC, datetime

    from app.services.vector_db.pgvector.sql_filters import Compiled

    tx = RecordingTx()
    await writes.merge_payload(tx, A, 1, {"k": 1}, Compiled("true", []), None)
    await writes.merge_payload(tx, A, 1, {"k": 1}, None, [uuid.UUID(int=1)])
    await writes.group_keys_matching(tx, A, 1, Compiled("true", []))
    await writes.group_keys_of_ids(tx, A, 1, [uuid.UUID(int=1)])
    assert len(tx.sql) == 4
    for statement in tx.sql:
        assert "org_id = $1" in statement, statement[:80]
    tx.sql.clear()
    await writes.write_groups(tx, A, 1, {"g": {"connectorIds": ["c"]}}, datetime.now(UTC), allow_empty=True)
    assert tx.sql and all("$1" in statement and "org_id" in statement for statement in tx.sql)       # $1 is the org of every group row


@pytest.mark.asyncio
async def test_a_group_write_replaces_only_the_arrays_it_carries() -> None:
    from datetime import UTC, datetime

    tx = RecordingTx([{"group_key": "g", "connector_ids": ["c9"], "record_group_ids": ["old"], "root_record_group_ids": []}])
    seen: list[tuple] = []

    async def fetch(sql: str, *args: object) -> list[dict]:
        seen.append(args)
        return tx.rows

    async def execute(sql: str, *args: object) -> str:
        seen.append(args)
        return "INSERT 0 0"

    tx.fetch, tx.execute = fetch, execute      # type: ignore[method-assign]
    assert await writes.write_groups(tx, A, 1, {"g": {"connectorIds": ["c9"]}}, datetime.now(UTC), allow_empty=True) == 1
    import json

    rows = json.loads(seen[0][2])
    assert rows == [{"k": "g", "c": ["c9"], "r": None, "x": None}]                        # the other two arrays are absent, not empty
    members = json.loads(seen[1][2])
    assert members == [{"k": "g", "kind": 1, "members": ["c9"]}]                             # and only connector members are diffed
