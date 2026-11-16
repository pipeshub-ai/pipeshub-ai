"""Checks on an ``EXPLAIN (FORMAT JSON)`` plan of a PostgreSQL graph statement: is each read of a table made by its key?

The planner may answer a keyed read with an Index Scan, an Index Only Scan, or a Bitmap Heap Scan fed by a Bitmap Index Scan on the same index;
which one it takes depends on the statistics and the version. All of them read by the key. What a keyed read never is: a sequential scan,
or an index scan whose condition does not bound the key (the whole organisation read and filtered).
"""
from __future__ import annotations

import re
from typing import Any

INDEX_SCANS = ("Index Scan", "Index Only Scan")
NODES_AND_EDGES = {"nodes": ("id",), "edges": ("to_id", "from_id")}


def walk(node: dict[str, Any]):  # noqa: ANN201
    """The node and everything below it."""
    yield node
    for child in node.get("Plans", []):
        yield from walk(child)


def has_key(cond: str, keys: tuple[str, ...]) -> bool:
    """Whether an index condition bounds one of ``keys`` by equality (``to_id`` is not ``id``)."""
    return any(re.search(rf"\b{re.escape(k)} = ", cond or "") for k in keys)


def _bitmap_keyed(node: dict[str, Any], keys: tuple[str, ...]) -> bool:
    kind = node["Node Type"]
    subs = [c for c in node.get("Plans", []) if c.get("Node Type", "").startswith("Bitmap") or c.get("Node Type") == "BitmapAnd"]
    if kind == "Bitmap Index Scan":
        return has_key(node.get("Index Cond") or "", keys)
    if kind == "BitmapAnd":        # the rows meeting every input: one keyed input bounds them
        return any(_bitmap_keyed(c, keys) for c in subs)
    if kind == "BitmapOr":         # the rows meeting any input: every input must be keyed
        return bool(subs) and all(_bitmap_keyed(c, keys) for c in subs)
    return False


def keyed_read(node: dict[str, Any], keys: tuple[str, ...]) -> bool:
    """Whether a scan node reads its table by one of ``keys``: an index scan with that key in its condition, or a bitmap heap scan fed by bitmap index scans that have it."""
    kind = node["Node Type"]
    if kind in INDEX_SCANS:
        return has_key(node.get("Index Cond") or "", keys)
    if kind == "Bitmap Heap Scan":
        inputs = [c for c in node.get("Plans", []) if c.get("Node Type") in ("Bitmap Index Scan", "BitmapAnd", "BitmapOr")]
        return bool(inputs) and all(_bitmap_keyed(c, keys) for c in inputs)
    return False


def keyed_access_violations(plan: dict[str, Any], tables: dict[str, tuple[str, ...]] = NODES_AND_EDGES) -> list[str]:
    """Each read of a table of ``tables`` (relation name prefix -> its key columns) in the plan that is not by its key.

    ``plan`` is the plan node (``[0]["Plan"]`` of the EXPLAIN output). Scans of other relations are not judged."""
    found = []
    for n in walk(plan):
        rel = n.get("Relation Name") or ""
        keys = next((k for t, k in tables.items() if rel.startswith(t)), None)
        if keys is not None and not keyed_read(n, keys):
            found.append(f"{n['Node Type']} on {rel} ({n.get('Index Name')}): {n.get('Index Cond') or [c.get('Index Cond') for c in walk(n)][1:]}")
    return found


def assert_keyed_access(plan: dict[str, Any], tables: dict[str, tuple[str, ...]] = NODES_AND_EDGES, at: object = "") -> None:
    found = keyed_access_violations(plan, tables)
    assert not found, f"{at}: read other than by its key: {found}"
