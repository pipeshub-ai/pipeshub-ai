"""What the plan gates ask of a plan (``EXPLAIN (..., FORMAT JSON)``): the scans of a table, and the index conditions that bound each.

A bitmap heap scan is as bounded as its bitmap index scans (under a BitmapAnd or BitmapOr too): each must carry the key a plain index scan must. A
sequential scan is bounded by nothing. Every gate judges a scan through :func:`bounds`, so a key-bounded bitmap plan, which a planner may pick on one
minor release and not the next, passes every gate alike, and a bitmap scan bounded by the organisation alone fails every gate alike."""
from __future__ import annotations

import json
import re
from typing import Any

SCANS = ("Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan")


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
    """The scan nodes of the tables named ``relation`` (a prefix: ``nodes`` matches the partitions ``nodes_h00`` ...)."""
    return [n for n in nodes(plan) if n["Node Type"] in SCANS and str(n.get("Relation Name", "")).startswith(relation)]


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
    """The scans of ``relation`` that :func:`keyed` refuses, described for an assertion message."""
    return [f"{n['Node Type']} on {n.get('Relation Name')}: {bounds(n) or 'no index condition'}" for n in scans(plan, relation) if not keyed(n, key, index)]
