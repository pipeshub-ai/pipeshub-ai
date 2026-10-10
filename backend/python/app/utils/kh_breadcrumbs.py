"""Breadcrumbs for a knowledge hub v2 browse, chosen from flags the query computed.

The trail follows **placement**, not the raw hierarchy. Each level is the node
its child is listed under, so an inaccessible ancestor is replaced by where the
child actually appears and is never named.

The query makes every permission decision: per node, whether the user may open
it and its own record groups; per hierarchy edge, whether the child lists under
that parent. This module only walks those flags upward.
"""

from __future__ import annotations

from typing import Any

_CRUMB_FIELDS = ("id", "name", "nodeType", "subType")


def build_trail(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    start_id: str,
    via_parent_id: str | None = None,
) -> list[dict[str, Any]]:
    """The root-first trail ending at ``start_id``, or ``[]`` if it may not be opened.

    Each step up takes the first of:

    1. a parent the node lists under and the user may open — ``via_parent_id``
       when it is one (first step only), otherwise a non-internal parent before
       an internal one, so the drive location beats Shared with Me, with the id
       as a stable tiebreak;
    2. for a chain-top, an own record group the user may open, in the same
       order as step 1. A node can have several, and choosing one before asking
       whether it opens would drop a group the user can reach;
    3. the App. An own group missing from ``nodes`` counts as unreachable, so
       the trail never names a node the query did not check.
    """
    by_id = {n["id"]: n for n in nodes}
    listed_under: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("lists"):
            listed_under.setdefault(edge["childId"], []).append(edge["parentId"])

    current = by_id.get(start_id)
    if current is None or not current.get("admitted"):
        return []
    trail = [current]
    while current["nodeType"] != "app":
        via = via_parent_id if len(trail) == 1 else None
        current = _step_up(current, by_id, listed_under, {n["id"] for n in trail}, via)
        if current is None:
            break
        trail.append(current)
    return [{field: n.get(field) for field in _CRUMB_FIELDS} for n in reversed(trail)]


def _step_up(
    node: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    listed_under: dict[str, list[str]],
    seen: set[str],
    via: str | None,
) -> dict[str, Any] | None:
    def openable(node_id: str | None) -> dict[str, Any] | None:
        candidate = by_id.get(node_id) if node_id else None
        if candidate is None or node_id in seen or not candidate.get("admitted"):
            return None
        return candidate

    parents = [p for p in map(openable, listed_under.get(node["id"], ())) if p]
    if parents:
        chosen = next((p for p in parents if p["id"] == via), None)
        return chosen or min(parents, key=lambda p: (bool(p.get("isInternal")), p["id"]))
    groups = [g for g in map(openable, node.get("ownGroups") or ()) if g]
    if groups:
        return min(groups, key=lambda g: (bool(g.get("isInternal")), g["id"]))
    apps = [n for n in by_id.values() if n["nodeType"] == "app" and openable(n["id"])]
    return min(apps, key=lambda n: n["id"]) if apps else None


def browse_scope(
    start_id: str,
    admitted: bool,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    via_parent_id: str | None = None,
) -> dict[str, Any]:
    """The browse response's ``scope``: nothing but the verdict unless admitted.

    An inadmissible start node carries no name, type or trail, so the 404 built
    from it cannot confirm that the node exists.
    """
    if not admitted:
        return {"admitted": False, "nodeId": start_id}
    trail = build_trail(nodes, edges, start_id, via_parent_id)
    return {
        "admitted": True,
        "nodeId": start_id,
        "currentNode": trail[-1] if trail else None,
        "parentNode": trail[-2] if len(trail) > 1 else None,
        "breadcrumbs": trail,
    }
