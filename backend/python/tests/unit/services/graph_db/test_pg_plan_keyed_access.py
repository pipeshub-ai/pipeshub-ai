"""The plan check of the PostgreSQL graph tests (``tests/support/plan_gate.py``), on EXPLAIN JSON written out by hand, with no database.

The shapes are what PostgreSQL 17 and 18 print. The last case is the plan of the statement before #5848 (b8a3e10d5) for a collection's flat listing: its folders and
children were joined to ``graph.nodes`` on ``org_id``, ``coll`` and ``id`` left to the planner, which read the organisation's records by a bitmap or sequential scan
(``org_id = $1 AND coll = 'records'``, no ``id``) once per folder; the check must refuse it.
"""
from __future__ import annotations

import pytest

from tests.support import plan_gate

KEY = r"\bid = "


def keyed_access_violations(plan: dict) -> list[str]:
    return plan_gate.violations(plan)


def assert_keyed_access(plan: dict) -> None:
    found = plan_gate.violations(plan)
    assert not found, f"read other than by its key: {found}"

NODE_KEY = "((org_id = $1) AND (coll = 'records'::text) AND (id = (b.from_id)::text))"
EDGE_KEY = "((org_id = $1) AND (coll = 'belongsTo'::text) AND (to_coll = 'apps'::text) AND (to_id = $2))"


def idx(kind: str, rel: str, cond: str, name: str = "p") -> dict:
    return {"Node Type": kind, "Relation Name": rel, "Index Name": name, "Index Cond": cond}


def bitmap(rel: str, cond: str) -> dict:
    """What the failing CI run printed: ('Bitmap Heap Scan', '') on the heap node, the condition on its child."""
    return {"Node Type": "Bitmap Heap Scan", "Relation Name": rel, "Recheck Cond": cond, "Plans": [{"Node Type": "Bitmap Index Scan", "Index Name": "p", "Index Cond": cond}]}


def join(*scans: dict) -> dict:
    return {"Node Type": "Nested Loop", "Plans": list(scans)}


@pytest.mark.parametrize("scan", [
    idx("Index Scan", "nodes", NODE_KEY),
    idx("Index Only Scan", "nodes_records", NODE_KEY),
    bitmap("nodes", NODE_KEY),
])
def test_a_node_read_by_its_id_passes_whichever_index_scan_the_planner_takes(scan: dict) -> None:
    assert_keyed_access(join(scan))


@pytest.mark.parametrize("scan", [idx("Index Scan", "edges", EDGE_KEY), bitmap("edges", EDGE_KEY), bitmap("edges", EDGE_KEY.replace("to_id", "from_id"))])
def test_an_edge_read_by_to_id_or_from_id_passes(scan: dict) -> None:
    assert_keyed_access(join(scan))


def test_a_bitmap_scan_ored_or_anded_is_keyed_only_when_every_input_is() -> None:
    keyed, loose = {"Node Type": "Bitmap Index Scan", "Index Cond": NODE_KEY}, {"Node Type": "Bitmap Index Scan", "Index Cond": "((org_id = $1) AND (coll = 'records'::text))"}
    heap = {"Node Type": "Bitmap Heap Scan", "Relation Name": "nodes"}
    # Each input index is scanned before the bitmaps are intersected: a loose input reads the organisation's index.
    assert keyed_access_violations({**heap, "Plans": [{"Node Type": "BitmapAnd", "Plans": [keyed, loose]}]})
    assert not keyed_access_violations({**heap, "Plans": [{"Node Type": "BitmapAnd", "Plans": [keyed, keyed]}]})
    assert not keyed_access_violations({**heap, "Plans": [{"Node Type": "BitmapOr", "Plans": [keyed, keyed]}]})
    assert keyed_access_violations({**heap, "Plans": [{"Node Type": "BitmapOr", "Plans": [keyed, loose]}]})


@pytest.mark.parametrize("scan", [
    {"Node Type": "Seq Scan", "Relation Name": "nodes", "Filter": "(id = 'x'::text)"},
    {"Node Type": "Seq Scan", "Relation Name": "edges"},
    idx("Index Scan", "nodes", "((org_id = $1) AND (coll = 'records'::text))"),
    idx("Index Scan", "edges", "((org_id = $1) AND (coll = 'belongsTo'::text))"),
    idx("Index Scan", "nodes", "((org_id = $1) AND (coll = 'records'::text) AND (to_id = $2))"),
    bitmap("nodes", "((org_id = $1) AND (coll = 'records'::text))"),
    {"Node Type": "Bitmap Heap Scan", "Relation Name": "nodes"},
])
def test_a_scan_not_bounded_by_its_key_fails(scan: dict) -> None:
    assert keyed_access_violations(join(scan))
    with pytest.raises(AssertionError):
        assert_keyed_access(join(scan))


def test_the_plan_before_5848_reading_every_record_of_the_organisation_per_folder_fails() -> None:
    """Folders joined to their children's nodes with no id in the condition, as the old statement's estimates led the planner to: the organisation's records by a bitmap scan,
    looped once per folder (and the collection's edges, estimated at one row, by a scan of the organisation's edges)."""
    old = {"Node Type": "Nested Loop", "Plans": [
        {"Node Type": "Seq Scan", "Relation Name": "edges", "Filter": "((org_id = $1) AND (coll = 'belongsTo'::text) AND (to_coll = 'apps'::text))"},
        {"Node Type": "Bitmap Heap Scan", "Relation Name": "nodes", "Actual Loops": 20, "Recheck Cond": "((org_id = $1) AND (coll = 'records'::text))",
         "Filter": "((doc ->> 'mimeType'::text) = ANY ($3))",
         "Plans": [{"Node Type": "Bitmap Index Scan", "Index Name": "nodes_pkey", "Index Cond": "((org_id = $1) AND (coll = 'records'::text))"}]}]}
    found = keyed_access_violations(old)
    assert len(found) == 2 and found[0].startswith("Seq Scan on edges") and found[1].startswith("Bitmap Heap Scan on nodes")


def test_other_relations_are_not_judged() -> None:
    assert not keyed_access_violations({"Node Type": "Seq Scan", "Relation Name": "other"})


# ---- the helper's own primitives (bounds, keyed, unkeyed) ---------------------------------------------------------------------------

def _scan(kind: str, *children: dict, index: str = "", cond: str = "") -> dict:
    node = {"Node Type": kind, "Relation Name": "nodes_h00", "Plans": list(children)}
    return {**node, "Index Name": index, "Index Cond": cond} if index else node


def _bitmap(index: str, cond: str) -> dict:
    return {"Node Type": "Bitmap Index Scan", "Index Name": index, "Index Cond": cond}


def test_an_index_scan_is_keyed_by_its_own_condition_and_index() -> None:
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
    assert not plan_gate.keyed(_scan("Bitmap Heap Scan", _bitmap("nodes_h00_record_connector_id", "((org_id = $1) AND (id = 'r1'))")), KEY, r"nodes\w*_pkey")


def test_a_read_that_is_not_an_index_scan_is_bounded_by_nothing_and_a_write_target_is_not_a_read() -> None:
    plan = {"Plan": {"Node Type": "Nested Loop", "Plans": [_scan("Seq Scan"), _scan("Index Scan", index="nodes_h00_pkey", cond="(id = x.id)"),
                                                          _scan("Tid Scan")]}}
    assert plan_gate.bounds(_scan("Seq Scan")) == []
    assert plan_gate.unkeyed(plan, "nodes", KEY) == ["Seq Scan on nodes_h00: no index condition", "Tid Scan on nodes_h00: no index condition"]
    assert plan_gate.unkeyed({"Node Type": "ModifyTable", "Relation Name": "nodes_h00", "Plans": [_scan("Index Scan", index="p", cond="(id = $1)")]}, "nodes", KEY) == []
    assert plan_gate.unkeyed('[{"Plan": {"Node Type": "Index Scan", "Relation Name": "edges_h01", "Index Cond": "(to_id = $2)", "Index Name": "e"}}]', "edges", r"\bto_id = ") == []
