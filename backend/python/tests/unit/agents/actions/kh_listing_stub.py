"""The graph provider's side of a Knowledge Hub listing, as `KnowledgeHubService`
reads it: the access gate, the root listing of Apps, and one page per connector.

The stubs filter and page the way both providers do (`_kh_v2_filters_aql`,
`_kh_v2_filters_cypher`, keyset paging on the merge's comparator), so a scope
the service fails to pass down shows up as rows from another source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.connectors.sources.localKB.handlers.kh_merge import key_for

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def node(
    node_id: str, name: str, node_type: str = "record", *,
    parent_id: str | None = None, origin: str = "CONNECTOR",
) -> dict[str, Any]:
    return {"id": node_id, "name": name, "nodeType": node_type, "origin": origin, "parentId": parent_id}


def _passes(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    connector_ids = filters.get("connector_ids")
    if connector_ids and not (
        (row["nodeType"] == "app" and row["id"] in connector_ids) or row["connectorId"] in connector_ids
    ):
        return False
    group_ids = filters.get("record_group_ids")
    if group_ids and row["nodeType"] == "recordGroup" and row["origin"] == "COLLECTION" and row["id"] not in group_ids:
        return False
    query = filters.get("search_query")
    if query and query.lower() not in row["name"].lower():
        return False
    node_types = filters.get("node_types")
    return not node_types or row["nodeType"] in node_types


def _page(
    rows: list[dict[str, Any]], filters: dict[str, Any], *,
    limit: int, sort_dir: str, after: dict[str, Any] | None,
) -> dict[str, Any]:
    descending = sort_dir == "DESC"
    ordered = sorted((r for r in rows if _passes(r, filters)), key=lambda r: key_for(r, descending))
    if after is not None:
        boundary = key_for(after, descending)
        ordered = [r for r in ordered if boundary < key_for(r, descending)]
    return {"rows": [dict(r) for r in ordered[:limit]], "hasMore": len(ordered) > limit, "total": len(ordered)}


def stub_listing(graph: MagicMock, user_key: str, sources: dict[str, list[dict[str, Any]]]) -> None:
    """Give `graph` a user who may open `sources`: each App or knowledge base id,
    with the nodes inside it. A node with no `parentId` sits directly under its source."""
    apps = {
        app_id: {**node(app_id, app_id, "app"), "connectorId": app_id, "sortKey": app_id.lower(), "nullRank": 0}
        for app_id in sources
    }
    inside = {
        app_id: [
            {**row, "connectorId": app_id, "parentId": row.get("parentId") or app_id,
             "sortKey": row["name"].lower(), "nullRank": 0}
            for row in rows
        ]
        for app_id, rows in sources.items()
    }
    by_id = {row["id"]: row for rows in inside.values() for row in rows} | apps

    def gate(**_: object) -> dict[str, Any]:
        return {"grantee_ids": [user_key], "gated_app_ids": list(sources)}

    def grants(**_: object) -> dict[str, Any]:
        return {**gate(), "by_connector": {app_id: [] for app_id in sources}}

    def root_listing(
        *, user_app_ids: list[str], limit: int, sort_dir: str = "ASC",
        after: dict[str, Any] | None = None, **filters: object,
    ) -> dict[str, Any]:
        page = _page([apps[a] for a in user_app_ids], filters, limit=limit, sort_dir=sort_dir, after=after)
        return {"partitions": [{**page, "ids": []}], "scope": None}

    def connector_page(
        *, app_id: str, gated_app_ids: list[str], limit: int = 50, flatten: bool = True,
        sort_dir: str = "ASC", after: dict[str, Any] | None = None, filters: dict[str, Any] | None = None,
        start_id: str | None = None, include_scope: bool = False, **_: object,
    ) -> dict[str, Any]:
        start = by_id.get(start_id or app_id)
        if start is None or start["connectorId"] not in gated_app_ids:
            scope = {"admitted": False, "nodeId": start_id or app_id} if include_scope else None
            return {"rows": [], "hasMore": False, "total": 0, "counts": {}, "scope": scope}
        rows = inside[start["connectorId"]]
        if not (flatten and start["nodeType"] == "app"):
            rows = [r for r in rows if r["parentId"] == start["id"]]
        scope = None
        if include_scope:
            current = {"id": start["id"], "name": start["name"], "nodeType": start["nodeType"]}
            scope = {"admitted": True, "nodeId": start["id"], "currentNode": current, "breadcrumbs": [current]}
        page = _page(rows, filters or {}, limit=limit, sort_dir=sort_dir, after=after)
        return {**page, "counts": None, "scope": scope}

    graph.kh_grants_per_connector = False
    graph.get_user_by_user_id.return_value = {"_key": user_key}
    graph.get_knowledge_hub_access_context_v2.side_effect = gate
    graph.get_knowledge_hub_access_v3.side_effect = grants
    graph.get_knowledge_hub_root_nodes_v2.side_effect = root_listing
    graph.get_knowledge_hub_connector_page_v3.side_effect = connector_page
    graph.get_user_kb_permission.return_value = "READER"


def root_listings(graph: MagicMock) -> list[dict[str, Any]]:
    """The root listings asked for, without the source names read for the filter options."""
    return [
        call.kwargs for call in graph.get_knowledge_hub_root_nodes_v2.await_args_list
        if not call.kwargs.get("names_only")
    ]


def connector_pages(graph: MagicMock) -> list[dict[str, Any]]:
    return [call.kwargs for call in graph.get_knowledge_hub_connector_page_v3.await_args_list]
