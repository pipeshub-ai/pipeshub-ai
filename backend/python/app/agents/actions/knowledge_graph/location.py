"""Permission-aware hierarchy Location for retrieved records.

Retrieval-only (navigate Path uses get_knowledge_hub_breadcrumbs separately).

Flow:
1. One batched parent-adjacency query (structure + names)
2. Breadcrumb-priority walk in Python
3. One batched KH permission_role filter on unique ancestors
4. Accessible prefix from the root + render

Rendering is a prefix from the root: App segments are ACL-proven by the
search itself; below that, the trail stops at the first node the user has
no role on, so denied ids (and anything beneath them) never render.

Soft-fail is App-only (ACL-proven connector) or empty — never unfiltered
RG/parent ids from the record document.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_MAX_DEPTH = 20

_TYPE_LABELS: dict[str, str] = {
    "app": "App ID",
    "recordGroup": "Record Group ID",
    "record": "Record ID",
}

# Adjacency shape (built by both graph providers):
#   {"nodes": {id: {"id", "type", "name"}},
#    "parents": {child_id: [{"parent_id", "parent_type", "via"}, ...]}}


def pick_parent(edges: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Breadcrumb priority: recordRelations record → belongsTo RG → belongsTo app."""
    for via, ptype in (
        ("recordRelations", "record"),
        ("belongsTo", "recordGroup"),
        ("belongsTo", "app"),
    ):
        for edge in edges:
            if edge.get("via") == via and edge.get("parent_type") == ptype:
                return edge
    return None


def walk_ancestors(
    seed_id: str,
    adjacency: Mapping[str, Any],
    *,
    max_depth: int = _MAX_DEPTH,
) -> list[dict[str, Any]]:
    """Walk seed → root using ``pick_parent``. Returns root → immediate parent (excludes seed)."""
    nodes: Mapping[str, Any] = adjacency.get("nodes") or {}
    parents: Mapping[str, Any] = adjacency.get("parents") or {}

    trail: list[dict[str, Any]] = []
    current = seed_id
    visited: set[str] = {seed_id}

    for _ in range(max_depth):
        chosen = pick_parent(parents.get(current) or ())
        if chosen is None:
            break
        parent_id = chosen.get("parent_id")
        if not parent_id or parent_id in visited:
            break
        node = nodes.get(parent_id)
        if not isinstance(node, dict):
            break
        visited.add(parent_id)
        trail.append(node)
        if node.get("type") == "app":
            break
        current = parent_id

    trail.reverse()
    return trail


def filter_accessible_prefix(
    trail: Sequence[Mapping[str, Any]],
    accessible_ids: set[str],
) -> list[Mapping[str, Any]]:
    """Keep the accessible prefix from the root; break at the first denied node.

    App segments need no check (retrieval ACL already verified app access).
    Depth-cap and permission truncation render identically (no marker), so a
    short trail never reveals that a denied ancestor exists.
    """
    prefix: list[Mapping[str, Any]] = []
    for seg in trail:
        if seg.get("type") == "app" or seg.get("id") in accessible_ids:
            prefix.append(seg)
        else:
            break
    return prefix


def render_trail(segments: Sequence[Mapping[str, Any]]) -> str:
    """Render ``Name (App ID: id) -> Name (Record Group ID: id) -> Name (Record ID: id)``."""
    parts: list[str] = []
    for seg in segments:
        seg_id = seg.get("id") or ""
        label = _TYPE_LABELS.get(seg.get("type"), "Record ID")
        name = seg.get("name") or seg_id
        parts.append(f"{name} ({label}: {seg_id})")
    return " -> ".join(parts)


def app_only_fallback(
    *,
    connector_id: str | None,
    connector_name: str | None,
) -> str:
    """ACL-safe soft-fail: App segment only (never RG / parent record ids)."""
    if not connector_id:
        return ""
    return f"{connector_name or connector_id} (App ID: {connector_id})"


async def resolve_ancestor_locations(
    record_ids: Sequence[str],
    *,
    graph_provider: "IGraphDBProvider",
    org_id: str,
    user_key: str,
    record_docs: Mapping[str, Mapping[str, Any]] | None = None,
    max_depth: int = _MAX_DEPTH,
) -> dict[str, str]:
    """Resolve Location strings for retrieved records.

    On provider failure / empty adjacency: App-only fallback from ``record_docs``.
    """
    ids = list(dict.fromkeys(rid for rid in record_ids if rid))
    if not ids:
        return {}

    try:
        adjacency = await graph_provider.get_record_parent_adjacency(
            ids, org_id, max_depth=max_depth
        )
        if not adjacency or not adjacency.get("nodes"):
            logger.warning(
                "resolve_ancestor_locations: empty adjacency for %d seeds — App-only fallback",
                len(ids),
            )
            return _app_only_fallback_map(ids, record_docs)

        trails = {
            rid: walk_ancestors(rid, adjacency, max_depth=max_depth) for rid in ids
        }

        # Unique non-app ancestors needing KH permission_role checks.
        seen: set[str] = set()
        candidates: list[dict[str, str]] = []
        for trail in trails.values():
            for seg in trail:
                seg_id = seg.get("id")
                if not seg_id or seg.get("type") == "app" or seg_id in seen:
                    continue
                seen.add(seg_id)
                candidates.append({"id": seg_id, "type": seg.get("type") or "record"})

        accessible: set[str] = set()
        if candidates:
            if not user_key:
                logger.warning(
                    "resolve_ancestor_locations: no user_key — rendering App-only prefixes"
                )
            else:
                accessible = await graph_provider.filter_nodes_with_permission_role(
                    candidates, user_key, org_id
                )
                if not accessible:
                    # ACL search just returned hits for this user, so zero
                    # accessible ancestors is contradictory — likely a
                    # user_key mixup that soft-fail would otherwise hide.
                    logger.warning(
                        "resolve_ancestor_locations: 0/%d ancestors accessible for "
                        "user %s despite ACL-filtered hits — check user_key wiring",
                        len(candidates),
                        user_key,
                    )

        result: dict[str, str] = {}
        for rid, trail in trails.items():
            rendered = render_trail(filter_accessible_prefix(trail, accessible))
            if rendered:
                result[rid] = rendered
        return result
    except Exception as exc:
        logger.warning(
            "resolve_ancestor_locations: failed — App-only fallback (%s)",
            exc,
            exc_info=True,
        )
        return _app_only_fallback_map(ids, record_docs)


def _app_only_fallback_map(
    record_ids: Sequence[str],
    record_docs: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, str]:
    if not record_docs:
        return {}
    result: dict[str, str] = {}
    for rid in record_ids:
        doc = record_docs.get(rid)
        if not isinstance(doc, dict):
            continue
        connector_id = doc.get("connectorId") or doc.get("connector_id")
        connector_name = doc.get("connectorName") or doc.get("connector_name")
        rendered = app_only_fallback(
            connector_id=str(connector_id) if connector_id else None,
            connector_name=str(connector_name) if connector_name else None,
        )
        if rendered:
            result[rid] = rendered
    return result
