"""Query catalog and the EXPLAIN gate.

Repository statements carry a constant call-site tag, ``/* pipeshub:<module>.<fn> */``. A test run with
:func:`recording` collects every statement it executed (tag, SQL, sample arguments) and :func:`export` writes the catalog
as JSON. :func:`explain_gate` plans each entry under the service role with a fixture org and fails when the planner kept
more than one leaf of a partitioned org tree, which means the statement relies on RLS alone instead of an explicit
``org_id = $n``.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from app.core import org_context

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from app.core.db.uow import KernelDB

_TAG = re.compile(r"/\*\s*pipeshub:([A-Za-z0-9_.]{1,80})\s*\*/")
_active: list[dict[str, CatalogEntry]] = []


@dataclass(frozen=True)
class CatalogEntry:
    tag: str | None
    sql: str
    args: tuple[Any, ...] = ()


def tag_of(sql: str) -> str | None:
    m = _TAG.search(sql[:200])
    return m.group(1) if m else None


def record(sql: str, args: Sequence[Any]) -> None:
    if _active:
        _active[-1].setdefault(sql, CatalogEntry(tag_of(sql), sql, tuple(args)))


@contextmanager
def recording() -> Iterator[dict[str, CatalogEntry]]:
    """Collect every statement executed through a ``Tx`` while active (tests only)."""
    entries: dict[str, CatalogEntry] = {}
    _active.append(entries)
    try:
        yield entries
    finally:
        _active.pop()


def untagged(entries: Sequence[CatalogEntry]) -> list[CatalogEntry]:
    return [e for e in entries if e.tag is None]


def export(entries: Sequence[CatalogEntry], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in entries], f, indent=1, default=str)


def load(path: str) -> list[CatalogEntry]:
    with open(path, encoding="utf-8") as f:
        return [CatalogEntry(d["tag"], d["sql"], tuple(d["args"])) for d in json.load(f)]


@dataclass(frozen=True)
class GateResult:
    tag: str | None
    leaves: int
    planning_ms: float
    ok: bool


def plan_leaves(node: dict[str, Any], leaf_to_root: dict[str, str], out: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Per table reference, the leaves the PLANNER kept: surviving leaf scans under one Append plus
    ``Subplans Removed`` (those were pruned only at executor start-up, so they were planned)."""
    kids = node.get("Plans", [])
    if node.get("Node Type") in ("Append", "Merge Append"):
        leaf_kids = [c for c in kids if c.get("Relation Name") in leaf_to_root]
        if leaf_kids or node.get("Subplans Removed"):
            root = leaf_to_root[leaf_kids[0]["Relation Name"]] if leaf_kids else "?"
            out.append((root, len(leaf_kids) + node.get("Subplans Removed", 0)))
            for c in kids:
                if c.get("Relation Name") not in leaf_to_root:
                    plan_leaves(c, leaf_to_root, out)
            return out
    if node.get("Relation Name") in leaf_to_root:
        out.append((leaf_to_root[node["Relation Name"]], 1))
    for c in kids:
        plan_leaves(c, leaf_to_root, out)
    return out


_LEAVES_SQL = """/* pipeshub:kernel.gate_leaves */
SELECT c.relname AS leaf, r.root FROM unnest($1::text[]) AS r(root), pg_partition_tree(r.root::regclass) t
  JOIN pg_class c ON c.oid = t.relid WHERE t.isleaf"""


async def explain_gate(db: KernelDB, entries: Sequence[CatalogEntry], *, org_id: str, partitioned: Sequence[str],
                       budget_ms: float = 1.0) -> list[GateResult]:
    """Plan every tagged entry as custom plans under the service role for ``org_id`` (a fixture org).

    ``partitioned`` names the org-partitioned tables (the roots of the partition trees).
    """
    results: list[GateResult] = []
    with org_context.for_org(org_id, job="explain_gate"):
        async with db.uow(custom_plan=True) as tx:
            leaf_to_root = {r["leaf"]: r["root"] for r in await tx.fetch(_LEAVES_SQL, list(partitioned))}
        for e in entries:
            if e.tag is None:
                results.append(GateResult(None, 0, 0.0, False))
                continue
            async with db.uow(custom_plan=True) as tx:
                raw = await tx.fetchval("EXPLAIN (FORMAT JSON, SUMMARY) " + e.sql, *e.args)
            plan = json.loads(raw)[0] if isinstance(raw, str) else raw[0]
            leaves = max((n for _, n in plan_leaves(plan["Plan"], leaf_to_root, [])), default=0)
            ms = float(plan["Planning Time"])
            results.append(GateResult(e.tag, leaves, ms, leaves <= 1 and ms <= budget_ms))
    return results
