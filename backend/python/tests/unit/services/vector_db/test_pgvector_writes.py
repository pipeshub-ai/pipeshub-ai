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
    assert b.groups == {"r1": {"connectorIds": ["c1"], "recordGroupIds": ["g1"], "rootRecordGroupIds": []}}


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


def test_the_package_follows_the_database_runtime_rules() -> None:
    """No session SQL and every literal statement tagged (the runtime's own static checks). Statements that splice in a compiled
    filter (``sql_filters``: structure only, every value a ``$n`` bind) are the only string-built ones, and are tagged too."""
    from app.core.db import sql_lint

    root = Path(writes.__file__).parent
    found = sql_lint.scan_paths(sorted(root.glob("*.py")))
    assert [str(f) for f in found if "string-built" not in f.rule] == []
    assert {Path(f.path).name for f in found} <= {"reads.py", "writes.py"}
    for name in ("reads.py", "writes.py"):
        text = (root / name).read_text()
        assert text.count("/* pipeshub:pgvector.") >= text.count("tx.fetch") + text.count("tx.execute") - 1


def test_a_point_without_a_payload_is_refused_as_having_no_org_not_crashed_on() -> None:
    from app.services.vector_db.pgvector.errors import OrgRequired

    with pytest.raises(OrgRequired):
        writes.prepare([VectorPoint(id=str(uuid.UUID(int=1)), dense_vector=[1.0], payload=None)])
