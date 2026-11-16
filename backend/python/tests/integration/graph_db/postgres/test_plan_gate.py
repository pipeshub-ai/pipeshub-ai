"""The plan-gate helper on plans written out by hand: a bitmap heap scan is judged by its bitmap index scans, a sequential scan is never keyed."""
from __future__ import annotations

import pytest

from . import plan_gate

pytestmark = pytest.mark.integration         # runs with the plan gates, in their CI shard

KEY = r"\bid = "


def _scan(kind: str, *children: dict, index: str = "", cond: str = "") -> dict:
    node = {"Node Type": kind, "Relation Name": "nodes_h00", "Plans": list(children)}
    return {**node, "Index Name": index, "Index Cond": cond} if index else node


def _bitmap(index: str, cond: str) -> dict:
    return {"Node Type": "Bitmap Index Scan", "Index Name": index, "Index Cond": cond}


def test_an_index_scan_is_keyed_by_its_own_condition() -> None:
    assert plan_gate.keyed(_scan("Index Scan", index="nodes_h00_pkey", cond="((org_id = $1) AND (coll = 'users') AND (id = r.deleted_by))"), KEY)
    assert not plan_gate.keyed(_scan("Index Scan", index="nodes_h00_pkey", cond="((org_id = $1) AND (coll = 'users'))"), KEY)
    assert not plan_gate.keyed(_scan("Index Scan", index="nodes_h00_other", cond="(id = $2)"), KEY, r"nodes\w*_pkey")


def test_a_bitmap_heap_scan_is_keyed_by_every_bitmap_index_scan_under_it() -> None:
    keyed = _scan("Bitmap Heap Scan", _bitmap("nodes_h00_pkey", "((org_id = $1) AND (coll = 'records') AND (id = 'r1'))"))
    org_only = _scan("Bitmap Heap Scan", _bitmap("nodes_h00_pkey", "((org_id = $1) AND (coll = 'stageStates'))"))
    mixed = _scan("Bitmap Heap Scan", {"Node Type": "BitmapOr", "Plans": [_bitmap("nodes_h00_pkey", "(id = 'a')"), _bitmap("nodes_h00_x", "(org_id = $1)")]})
    assert plan_gate.keyed(keyed, KEY) and plan_gate.bounds(keyed) == [("nodes_h00_pkey", "((org_id = $1) AND (coll = 'records') AND (id = 'r1'))")]
    assert not plan_gate.keyed(org_only, KEY)
    assert not plan_gate.keyed(mixed, KEY)


def test_a_sequential_scan_is_bounded_by_nothing_and_listed() -> None:
    plan = {"Plan": {"Node Type": "Nested Loop", "Plans": [_scan("Seq Scan"), _scan("Index Scan", index="nodes_h00_pkey", cond="(id = x.id)")]}}
    assert plan_gate.bounds(_scan("Seq Scan")) == []
    assert plan_gate.unkeyed(plan, "nodes", KEY) == ["Seq Scan on nodes_h00: no index condition"]
    assert plan_gate.unkeyed('[{"Plan": {"Node Type": "Index Scan", "Relation Name": "edges_h01", "Index Cond": "(to_id = $2)", "Index Name": "e"}}]', "edges", r"\bto_id = ") == []
