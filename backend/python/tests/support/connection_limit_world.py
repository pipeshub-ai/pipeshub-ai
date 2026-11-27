"""A world for the connected-app limit tests: one org, two connector apps and a collection,
with nested folders, sibling folders and sibling files. The graph runs the
real ``check_access`` and ``filter_accessible_virtual_record_ids`` of
``IGraphDBProvider``; only the row source (``_kh_v3_accessible_rows``) and the
hierarchy lookup (``get_selection_nodes``) are faked."""
from __future__ import annotations

import functools
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.interface.graph_db_provider import (
    AccessibleContainers,
    IGraphDBProvider,
)

APPS = ("app-A", "app-B", "kb-1")
# group -> (app, parent group)
GROUPS = {
    "gP": ("app-A", None), "gC": ("app-A", "gP"), "gS": ("app-A", None),
    "gB": ("app-B", None),
}
# record -> (app, direct group, parent record, vrid or "" for a folder)
RECORDS = {
    "rP": ("app-A", "gP", None, "v-rP"),
    "rC1": ("app-A", "gC", None, "v-rC1"),
    "rC2": ("app-A", "gC", None, "v-rC2"),
    "rS": ("app-A", "gS", None, "v-rS"),
    "rB": ("app-B", "gB", None, "v-rB"),
    "kF": ("kb-1", None, None, ""),
    "kR1": ("kb-1", None, "kF", "v-kR1"),
    "kR2": ("kb-1", None, None, "v-kR2"),
}
VRID_TO_RECORD = {v[3]: r for r, v in RECORDS.items() if v[3]}
ALL_RECORDS = frozenset(VRID_TO_RECORD.values())


def _group_desc(g: str) -> set[str]:
    out = {g}
    for child, (_, parent) in GROUPS.items():
        if parent == g:
            out |= _group_desc(child)
    return out


def _record_desc(r: str) -> set[str]:
    out = {r}
    for child, (_, _, parent, _) in RECORDS.items():
        if parent == r:
            out |= _record_desc(child)
    return out


def node_row(node_id: str) -> dict:
    if node_id in APPS:
        return {"id": node_id}
    if node_id in GROUPS:
        return {"id": node_id, "connectorId": GROUPS[node_id][0]}
    app, group, _, vrid = RECORDS[node_id]
    return {
        "id": node_id, "connectorId": app, "groupIds": [group] if group else [],
        "vrid": vrid, "indexingStatus": "COMPLETED",
    }


def make_graph(unreadable: frozenset[str] = frozenset(), base: MagicMock | None = None) -> MagicMock:
    graph = base or AsyncMock()
    graph.logger = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "ukey-1", "id": "ukey-1"})

    async def rows(user_key, org_id, node_ids, vrids, access=None, transaction=None) -> list:
        out = [node_row(n) for n in node_ids if n not in unreadable and (n in APPS or n in GROUPS or n in RECORDS)]
        out += [node_row(VRID_TO_RECORD[v]) for v in vrids if v in VRID_TO_RECORD and VRID_TO_RECORD[v] not in unreadable]
        return out

    async def selection_nodes(org_id, *, group_ids=(), record_ids=(), exact_record_ids=(), limit=0) -> dict:
        groups = sorted({d for g in group_ids if g in GROUPS for d in _group_desc(g)})
        recs = sorted({d for r in record_ids if r in RECORDS for d in _record_desc(r)} | {r for r in exact_record_ids if r in RECORDS})
        return {
            "groups": [{"id": g, "connectorId": GROUPS[g][0]} for g in groups],
            "records": [{"id": r, "vrid": RECORDS[r][3], "connectorId": RECORDS[r][0]} for r in recs],
        }

    graph._kh_v3_accessible_rows = AsyncMock(side_effect=rows)
    graph.check_access = functools.partial(IGraphDBProvider.check_access, graph)
    graph.filter_accessible_virtual_record_ids = functools.partial(
        IGraphDBProvider.filter_accessible_virtual_record_ids, graph)
    graph.get_selection_nodes = AsyncMock(side_effect=selection_nodes)
    graph.get_nodes_by_field_in = AsyncMock(side_effect=lambda collection, field, ids, *a, **k: [
        {"id": i, "vectorMembershipBackfilled": True} for i in ids])
    graph.get_org_apps = AsyncMock(return_value=[{"_key": a} for a in APPS])
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(app_ids=frozenset(APPS)))
    graph.get_records_by_record_ids = AsyncMock(side_effect=lambda record_ids, org_id: [
        {"_key": r, "recordName": f"{r}.txt", "recordType": "FILE", "connectorName": "DRIVE",
         "connectorId": RECORDS[r][0], "virtualRecordId": RECORDS[r][3], "origin": "CONNECTOR",
         "indexingStatus": "COMPLETED", "version": 0}
        for r in record_ids if r in RECORDS])
    return graph
