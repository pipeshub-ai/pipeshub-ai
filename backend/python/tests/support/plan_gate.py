"""What the plan gates of the PostgreSQL graph tests ask of a plan (``EXPLAIN (..., FORMAT JSON)``): each read of a table, and the index conditions
that bound it.

The planner may answer a keyed read with an Index Scan, an Index Only Scan, or a Bitmap Heap Scan fed by Bitmap Index Scans; which one depends on the
statistics and the minor release. A bitmap heap scan is as bounded as its bitmap index scans, every one of them (under a BitmapAnd too: each input
index is scanned in full before the bitmaps are combined, so a keyed sibling bounds the rows returned but not the work of an unkeyed input). A
sequential scan, or any other node that reads the table (a TID scan, a sample scan), is bounded by nothing. Every gate judges reads through
:func:`bounds`, so a keyed plan passes every gate alike whichever shape it takes, and a read of the organisation fails every gate alike."""
from __future__ import annotations

import json
import re
from typing import Any

SCANS = ("Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan")
NODES_AND_EDGES = {"nodes": r"\bid = ", "edges": r"\b(to_id|from_id) = "}     # graph.nodes by id, graph.edges by to_id or from_id


def root(plan: Any) -> dict[str, Any]:  # noqa: ANN401
    """The top plan node of what EXPLAIN returned: the JSON text, the parsed list, the ``{"Plan": ...}`` wrapper or a node."""
    if isinstance(plan, str):
        plan = json.loads(plan)
    if isinstance(plan, list):
        plan = plan[0]
    return plan["Plan"] if "Plan" in plan else plan


def nodes(plan: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Every node of the plan, the root first."""
    top = root(plan)
    return [top, *(n for child in top.get("Plans", []) for n in nodes(child))]


def scans(plan: Any, relation: str | tuple[str, ...]) -> list[dict[str, Any]]:  # noqa: ANN401
    """The nodes that read the tables named ``relation`` (a prefix: ``nodes`` matches the partitions ``nodes_h00`` ...): every node with that
    relation but the target of a write (ModifyTable)."""
    return [n for n in nodes(plan) if n.get("Relation Name") and n["Node Type"] != "ModifyTable" and str(n["Relation Name"]).startswith(relation)]


def bounds(scan: dict[str, Any]) -> list[tuple[str, str]]:
    """The (index, index condition) pairs that bound a scan node: its own for an index scan, its bitmap index scans' for a bitmap heap scan, none for a
    sequential scan."""
    if scan["Node Type"] in ("Index Scan", "Index Only Scan"):
        return [(scan.get("Index Name", ""), scan.get("Index Cond", ""))]
    if scan["Node Type"] == "Bitmap Heap Scan":
        return [(n.get("Index Name", ""), n.get("Index Cond", "")) for n in nodes(scan) if n["Node Type"] == "Bitmap Index Scan"]
    return []


def keyed(scan: dict[str, Any], key: str, index: str | None = None) -> bool:
    """Whether every index condition bounding ``scan`` matches the regular expression ``key`` (and its index ``index``, a full match), with at least one."""
    found = bounds(scan)
    return bool(found) and all(re.search(key, cond) and (index is None or re.fullmatch(index, name)) for name, cond in found)


def unkeyed(plan: Any, relation: str | tuple[str, ...], key: str, index: str | None = None) -> list[str]:  # noqa: ANN401
    """The reads of ``relation`` that :func:`keyed` refuses, described for an assertion message."""
    return [f"{n['Node Type']} on {n.get('Relation Name')}: {bounds(n) or 'no index condition'}" for n in scans(plan, relation) if not keyed(n, key, index)]


def violations(plan: Any, tables: dict[str, str] = NODES_AND_EDGES) -> list[str]:  # noqa: ANN401
    """Each read of a table of ``tables`` (relation name prefix -> the regular expression its key condition matches), in plan order, that
    is not by its key. Reads of other relations are not judged."""
    found = []
    for n in scans(plan, tuple(tables)):
        key = next(k for t, k in tables.items() if str(n["Relation Name"]).startswith(t))
        if not keyed(n, key):
            found.append(f"{n['Node Type']} on {n['Relation Name']}: {bounds(n) or 'no index condition'}")
    return found
