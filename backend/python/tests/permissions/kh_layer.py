"""The Knowledge Hub layer: every row, total, count and facet the listing gives, against the oracle.

A count is content: it must be computed from what the viewer may read and from nothing else.
The layer asks the product's own `search_page` (the global listing that merges connectors, sums the totals and
carries them in the cursor) and `KnowledgeHubService.get_nodes` (the browse path) for every viewer of a world, then
holds them to the oracle:

* over: a row the oracle does not admit (a leak);
* under: an admitted node the listing leaves out. Fails, except the two shapes the oracle cannot decide (`declared_gap`: an upload, which has its
  own surface, and a node under a group that hides its children), which are reported as `gap`;
* total / counts: `total` is the number of rows, `counts_by_type` is the rows by type (one predicate for the rows and the counts);
* facet: a filtered total equals the oracle's count of visible nodes with that value, and a value no visible node
  holds has total 0 (a bucket with no visible item does not exist). The oracle's count, not the listing's rows;
* beyond the cap: a row the browse lists that the oracle refuses only because its walk from the App passes the hop cap (`refused_by_cap`, the
  verdict `R7:depth-cap`) is a `gap`: a browse reads one hop from the node it opens. An admitted node a browse leaves out is a `browse_gap` however
  deep it lies (a seed or an open App's member is admitted with no hop cap);
* browse: the service path from each App down, every node it lists opened in turn (groups, folders, records, stubs), each held to the oracle's
  children of it (one row per node over every page, `totalItems` and `counts` that count each once, no row the oracle refuses, no child it admits
  left out), and each row's expand arrow against opening that row: it is on exactly when the browse of the row lists something, and a stub is
  listed exactly where opening it lists something.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.connectors.sources.localKB.handlers.kh_search import search_page

from .runner import OracleLayer

if TYPE_CHECKING:
    from .layers import GraphCheckLayer
    from .model import Node, Parent, World

HIERARCHY = ("PARENT_CHILD", "ATTACHMENT")  # the edges a folder lists; anything else is a link
MAX_OPENS = 300  # nodes a viewer's browse opens in one world, at most
PAGE = 7  # small, so a world pages through several cursors and the carried total is exercised
MAX_PAGES = 400


@dataclass(frozen=True)
class KhFinding:
    scenario: str
    viewer: str
    kind: str  # over | under | gap | browse_gap | browse_extra | total | counts | facet | browse | arrow | stub
    detail: str


@dataclass
class Listing:
    rows: list[dict[str, Any]]
    total: int
    counts: dict[str, int]
    totals_per_page: set[int]


async def listing(layer: GraphCheckLayer, world: World, viewer: str, filters: dict[str, Any] | None = None) -> Listing:
    """All pages of the viewer's flatten listing, walked by cursor as the product walks it."""
    ns = await layer._ns(world)
    rows: list[dict[str, Any]] = []
    token: str | None = None
    first = None
    totals: set[int] = set()
    for _ in range(MAX_PAGES):
        page = await search_page(
            layer._provider, user_key=ns.id(viewer), user_id=ns.id(viewer), org_id=ns.org, limit=PAGE,
            filters=filters, cursor_token=token, secret=None)
        first = first or page
        totals.add(page.total or 0)
        rows += [{**r, "id": ns.local(r["id"])} for r in page.rows]
        token = page.next_cursor
        if not token:
            break
    else:
        raise AssertionError("listing did not end")
    return Listing(rows, first.total or 0, first.counts_by_type or {}, totals)


def visible(reference: OracleLayer, world: World, viewer: str) -> dict[str, Any]:
    """The nodes the oracle lets the viewer list: groups and records that are live and not placeholders."""
    nodes = {n.id: n for n in world.nodes if n.kind != "app" and not n.deleted and not n.placeholder}
    verdicts = reference.explain(world, viewer, list(nodes))
    return {i: n for i, n in nodes.items() if verdicts[i].admitted}


def facets(seen: dict[str, Any]) -> dict[str, tuple[dict[str, Any], int]]:
    """Each probe (filters) with the oracle's count of visible nodes it must give."""
    out: dict[str, tuple[dict[str, Any], int]] = {
        "groups": ({"node_types": ["recordGroup"]}, sum(1 for n in seen.values() if n.kind == "group")),
    }
    for status in ("COMPLETED", "NOT_STARTED", "FAILED", "IN_PROGRESS"):
        out[f"indexing-{status}"] = (
            {"indexing_status": [status]},
            sum(1 for n in seen.values() if n.kind == "record" and n.indexing == status))
    return out


def hidden_by_navigation(world: World, node: Node) -> bool:
    """Whether the listing hides the node because a group that hides its children holds it or lies above it (`hideChildren` is a display flag:
    the access check ignores it, the listing honours it, so the oracle admits what the listing does not show)."""
    def hides(group_id: str) -> bool:
        return world.node(group_id).hide_children or any(world.node(a.id).hide_children for a in _ancestors(world, world.node(group_id)))
    return any(hides(g) for g in node.belongs if world.node(g).kind == "group") or any(world.node(a.id).hide_children for a in _ancestors(world, node))


def refused_by_cap(reference: OracleLayer, world: World, viewer: str, node_id: str) -> bool:
    """Whether the oracle refuses the node only because its walk from the App is longer than the cap (`oracle.MAX_HOPS`): the verdict says so
    (`R7:depth-cap`, every hop up to the cap passed). No other refusal and no admission is excused."""
    found = reference.explain(world, viewer, [node_id])[node_id]
    return not found.admitted and found.reason == "R7:depth-cap"


def declared_gap(world: World, node_id: str) -> bool:
    """The shapes the flatten listing leaves out on purpose, which the oracle cannot decide: a record that belongs to no connector (an upload, listed
    by its own surface), and a node a group that hides its children holds or lies above (`hideChildren` is a display flag the check ignores).
    Any other admitted node the listing leaves out is an under-admit and fails."""
    node = world.node(node_id)
    return node.connector is None or node.origin != "CONNECTOR" or hidden_by_navigation(world, node)


def _ancestors(world: World, node: Node) -> list[Parent]:
    """Every edge up the hierarchy from the node (a path of PARENT_CHILD and ATTACHMENT edges)."""
    out, seen_ids, frontier = [], set(), [node]
    while frontier:
        for edge in frontier.pop().parents:
            if edge.rel in HIERARCHY and edge.id not in seen_ids:
                seen_ids.add(edge.id)
                out.append(edge)
                frontier.append(world.node(edge.id))
    return out


async def check_world(layer: GraphCheckLayer, world: World, scenario_id: str = "") -> list[KhFinding]:
    reference = OracleLayer(layer.shape)
    found: list[KhFinding] = []
    for viewer in world.users():
        seen = visible(reference, world, viewer)
        got = await listing(layer, world, viewer)
        ids = [r["id"] for r in got.rows]

        def add(kind: str, detail: str, viewer: str = viewer) -> None:
            found.append(KhFinding(scenario_id, viewer, kind, detail))

        for extra in sorted(set(ids) - set(seen)):
            add("over", extra)
        gaps = {i for i in set(seen) - set(ids) if declared_gap(world, i)}
        for missing in sorted(set(seen) - set(ids)):
            add("gap" if missing in gaps else "under", missing)
        if len(ids) != len(set(ids)):
            add("over", "a row listed twice")
        if got.total != len(ids) or got.totals_per_page != {got.total}:
            add("total", f"total {got.total} (per page {sorted(got.totals_per_page)}), rows {len(ids)}")
        if got.counts != dict(Counter(r["nodeType"] for r in got.rows)):
            add("counts", f"{got.counts} vs rows {dict(Counter(r['nodeType'] for r in got.rows))}")
        # the oracle's count of what the listing is meant to show, not the rows it happens to show
        for name, (flt, want) in facets({i: n for i, n in seen.items() if i not in gaps}).items():
            have = (await listing(layer, world, viewer, flt)).total
            if have != want:
                add("facet", f"{name}: total {have}, oracle count {want}")
        found.extend(await _browse(layer, reference, world, viewer, seen, scenario_id))
    return found


class _Config:
    async def get_config(self, key: str, *_a: object, **_k: object) -> dict[str, str]:
        return {"scopedJwtSecret": "kh-layer-secret"}


async def children_of(layer: GraphCheckLayer, world: World, viewer: str, node: str, parent_type: str) -> dict[str, bool] | None:
    """What opening ``node`` lists for the viewer, as {id: expand arrow}; None when the browse does not open it."""
    from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
        KnowledgeHubService,
    )

    ns = await layer._ns(world)
    page = await KnowledgeHubService(logging.getLogger("kh-layer"), layer._provider, _Config()).get_nodes(
        user_id=ns.id(viewer), org_id=ns.org, parent_id=ns.id(node), parent_type=parent_type, limit=PAGE, sort_by="name", sort_order="asc")
    return {ns.local(i.id): bool(i.hasChildren) for i in page.items} if page.success else None


async def browse_pages(layer: GraphCheckLayer, world: World, viewer: str, node: str, parent_type: str) -> tuple[list[Any], list[int], list[int]] | None:
    """Every page of opening ``node`` for the viewer, as the product pages it: the rows in order, and each page's ``totalItems`` and
    ``counts.total``; None when the browse does not open it."""
    from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
        KnowledgeHubService,
    )

    ns = await layer._ns(world)
    service = KnowledgeHubService(logging.getLogger("kh-layer"), layer._provider, _Config())
    items: list[Any] = []
    totals: list[int] = []
    counted: list[int] = []
    cursor = None
    for _ in range(MAX_PAGES):
        args = {"user_id": ns.id(viewer), "org_id": ns.org, "parent_id": ns.id(node), "parent_type": parent_type, "limit": PAGE,
                "sort_by": "name", "sort_order": "asc", "include": ["counts"]}
        if cursor:
            args["cursor"] = cursor
        page = await service.get_nodes(**args)
        if not page.success:
            return None
        items += page.items
        totals.append(page.pagination.totalItems)
        counted.append(page.counts.total if page.counts else -1)
        cursor = page.pagination.nextCursor
        if not cursor:
            return items, totals, counted
    raise AssertionError("browse did not end")


async def _browse(
    layer: GraphCheckLayer, reference: OracleLayer, world: World, viewer: str, seen: dict[str, Any], scenario_id: str,
) -> list[KhFinding]:
    """The service path: the viewer's browse from each App down, every node it lists opened in turn (groups, folders, records, stubs), each held
    to the oracle's children of it, its pages to one row per node, and each row's arrow to what opening the row lists."""
    ns = await layer._ns(world)
    found: list[KhFinding] = []

    def add(kind: str, detail: str) -> None:
        found.append(KhFinding(scenario_id, viewer, kind, detail))

    on_the_way = {p.id for n in seen.values() for p in _ancestors(world, n)}

    def extras(rows: dict[str, Any]) -> set[str]:
        """The rows the oracle does not admit. One may be listed on the way to something readable, and only when it is shown as a placeholder (a
        node a seed it admits hangs under is listed under the nearest folder, and is in `seen`)."""
        return {i for i, row in rows.items() if i not in seen and not (i in on_the_way and getattr(row, "isPlaceholder", False))}

    def children(node: str) -> list[Node]:
        return [n for n in world.nodes if not n.deleted and any(p.id == node and p.rel in HIERARCHY for p in n.parents)]

    opened: dict[str, dict[str, Any] | None] = {}

    async def open_(node: str, parent_type: str) -> dict[str, Any] | None:
        """What opening ``node`` lists, {id: row} (once per node), checked when first opened."""
        if node in opened:
            return opened[node]
        result = await browse_pages(layer, world, viewer, node, parent_type)
        if result is None:
            opened[node] = None
            if node in seen and seen[node].kind == "group":      # a group the viewer may open and the browse does not: an under-admit
                add("browse_gap", f"{node}: the browse does not open it")
            return None
        items, totals, counted = result
        ids = [ns.local(item.id) for item in items]
        got = opened[node] = {ns.local(item.id): item for item in items}
        # one row per node over every page, and a total that counts each once
        for twice in sorted({i for i in ids if ids.count(i) > 1}):
            add("browse", f"{node}: lists {twice} more than once")
        if set(totals) != {len(got)} or set(counted) != {len(got)}:
            add("browse", f"{node}: totalItems per page {sorted(set(totals))}, counts.total {sorted(set(counted))}, rows {len(got)}")
        for extra in sorted(extras(got)):
            add("gap" if refused_by_cap(reference, world, viewer, extra) else "browse_extra", f"{node}: lists {extra}, which the oracle does not let the viewer open")
        # A readable child the browse leaves out is an under-admit. The one exemption is a folder that hides its children (a display flag).
        for missing in sorted(n.id for n in children(node) if n.id in seen and n.id not in got):
            if not hidden_by_navigation(world, world.node(missing)):
                add("browse_gap", f"{node}: the browse leaves out {missing}, which the viewer may open")
        # A stub is no content the oracle judges: it is listed exactly where opening it lists something.
        for stub in sorted(n.id for n in children(node) if n.placeholder and n.id not in got):
            if await open_(stub, "record"):
                add("browse_gap", f"{node}: the browse leaves out the stub {stub}, and opening it lists something")
        return got

    queue = [(n.id, "app") for n in world.nodes if n.kind == "app"]
    expanded: set[str] = set()
    while queue:
        node, parent_type = queue.pop(0)
        if node in expanded:
            continue
        expanded.add(node)
        for row, item in (await open_(node, parent_type) or {}).items():      # the expand arrow is on exactly when opening the row lists something
            row_type = str(getattr(item.nodeType, "value", item.nodeType))
            if row not in opened and len(opened) >= MAX_OPENS:
                return found
            lists = bool(await open_(row, row_type))
            if bool(getattr(item, "hasChildren", False)) != lists:
                add("arrow", f"{node}: {row} hasChildren {item.hasChildren}, opening it lists something: {lists}")
            if getattr(item, "isPlaceholder", False) and not lists:      # a stub is listed only as the way to something the viewer may open
                add("stub", f"{node}: lists the stub {row}, and opening it lists nothing")
            queue.append((row, row_type))
    return found
