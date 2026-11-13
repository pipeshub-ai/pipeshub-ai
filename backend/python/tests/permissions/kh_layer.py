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
  left out but one past the cap on the children a browse asks the check about, reported as a `gap`; a node the oracle admits whose browse fails
  is a `browse_gap`), and each row's expand arrow against opening that row: it is on exactly when the browse of the row lists something, and a stub is
  listed exactly where opening it lists something; and the sidebar of each node (its rows with children) holds every group and folder it lists
  and a record exactly where opening the record lists something. At most `MAX_OPENS` browses a viewer: the rows and the stubs left out that are
  not opened past it are counted apart (`truncated`, reported).
"""

from __future__ import annotations

import logging
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.connectors.sources.localKB.handlers.kh_search import search_page
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

from .runner import OracleLayer

if TYPE_CHECKING:
    from .layers import GraphCheckLayer
    from .model import Node, Parent, World

HIERARCHY = ("PARENT_CHILD", "ATTACHMENT")  # the edges a folder lists; anything else is a link
MAX_OPENS = 300  # nodes a viewer's browse opens in one world, at most; past it the rows and stubs not opened are counted (`truncated`, reported)
PAGE = 7  # small, so a world pages through several cursors and the carried total is exercised
# The whole-tree browse opens every other node with one large page: each opened node is held to the same oracle, and only the sample pays for
# the cursor walk (statements per node), which is what bounds the CI step.
BIG_PAGE = 200
SAMPLED_FIRST = 20  # the first nodes a viewer opens, and one node in SAMPLE_EVERY after, page by PAGE
SAMPLE_EVERY = 4
MAX_PAGES = 400


SKIPPED: Any = object()  # a browse the layer did not run: past its budget of opens (`MAX_OPENS`)


@dataclass(frozen=True)
class KhFinding:
    scenario: str
    viewer: str
    kind: str  # over | under | gap | browse_gap | browse_extra | total | counts | facet | browse | arrow | stub | sidebar | truncated
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
        # the flatten's sidebar (rows with children) is a filter on the flatten: its groups and folders, and a record exactly where the
        # flatten's own arrow is on; it never adds a row
        side = {r["id"] for r in (await listing(layer, world, viewer, {"only_containers": True})).rows}
        want = {r["id"] for r in got.rows if r.get("nodeType") != "record" or r.get("hasChildren")}
        for extra in sorted(side - want):
            add("sidebar", f"flatten: the sidebar lists {extra}, which the flatten lists without an arrow" if extra in ids
                else f"flatten: the sidebar lists {extra}, which the flatten does not list")
        for missing in sorted(want - side):
            add("sidebar", f"flatten: the sidebar leaves out {missing}, which the flatten lists with an arrow")
        found.extend(await _browse(layer, reference, world, viewer, seen, scenario_id))
    return found


class _Config:
    async def get_config(self, key: str, *_a: object, **_k: object) -> dict[str, str]:
        return {"scopedJwtSecret": "kh-layer-secret"}


async def children_of(
    layer: GraphCheckLayer, world: World, viewer: str, node: str, parent_type: str, *, only_containers: bool = False, flattened: bool = False,
) -> dict[str, bool] | None:
    """What opening ``node`` lists for the viewer, as {id: expand arrow}; None when the browse does not open it. ``only_containers``: the
    sidebar's listing, the rows with children; ``flattened``: everything below ``node``, not its children alone."""
    from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
        KnowledgeHubService,
    )

    ns = await layer._ns(world)
    page = await KnowledgeHubService(logging.getLogger("kh-layer"), layer._provider, _Config()).get_nodes(
        user_id=ns.id(viewer), org_id=ns.org, parent_id=ns.id(node), parent_type=parent_type, limit=PAGE, sort_by="name", sort_order="asc",
        only_containers=only_containers, flattened=flattened)
    return {ns.local(i.id): bool(i.hasChildren) for i in page.items} if page.success else None


async def browse_pages(
    layer: GraphCheckLayer, world: World, viewer: str, node: str, parent_type: str, page_size: int = PAGE, *, only_containers: bool = False,
) -> tuple[list[Any], list[int], list[int]] | None:
    """Every page of opening ``node`` for the viewer, as the product pages it (``page_size`` rows a page): the rows in order, and each page's
    ``totalItems`` and ``counts.total``; None when the browse does not open it. ``only_containers``: the sidebar's listing."""
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
        args = {"user_id": ns.id(viewer), "org_id": ns.org, "parent_id": ns.id(node), "parent_type": parent_type, "limit": page_size,
                "sort_by": "name", "sort_order": "asc", "include": ["counts"], "only_containers": only_containers}
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

    def capped(node: str, missing: str, listed: dict[str, Any]) -> bool:
        """Whether a browse of ``node`` leaves ``missing`` out only for the cap on the children it asks the check about (`_KH_UNDECIDED_MAX`, the
        first by id of its live children of its App, no stub, that its rules leave undecided): reported, as the cap's known under-share, not
        failed. A child the browse lists may be one its rules select, so only the unlisted ones ahead of ``missing`` that no group hiding its
        children holds are sure to be candidates ahead of it: the cap is excused only when at least ``_KH_UNDECIDED_MAX`` of those precede it."""
        app = node if world.node(node).kind == "app" else world.node(node).connector
        # A child hidden by a group that hides its children is no candidate either: the browse leaves it out before the cap.
        ahead = [n.id for n in children(node)
                 if not n.placeholder and not n.deleted and n.connector == app and n.id not in listed and ns.id(n.id) < ns.id(missing)
                 and not hidden_by_navigation(world, n)]
        return len(ahead) >= IGraphDBProvider._KH_UNDECIDED_MAX

    opened: dict[str, dict[str, Any] | None] = {}
    skipped: dict[str, set[str]] = {"rows": set(), "stubs": set()}

    async def open_(node: str, parent_type: str, *, kind: str = "rows") -> dict[str, Any] | None:
        """What opening ``node`` lists, {id: row} (once per node), checked when first opened; ``SKIPPED`` past the budget of opens (counted
        under ``kind``: a listed row, or a stub left out that is opened to see whether leaving it out was right)."""
        if node in opened:
            return opened[node]
        if len(opened) >= MAX_OPENS:
            skipped[kind].add(node)
            return SKIPPED
        sampled = len(opened) < SAMPLED_FIRST or zlib.crc32(node.encode()) % SAMPLE_EVERY == 0
        result = await browse_pages(layer, world, viewer, node, parent_type, PAGE if sampled else BIG_PAGE)
        if result is None:
            opened[node] = None
            # a node the viewer may open and the browse does not (a leaf opens to an empty list): an under-admit
            if node in seen and not hidden_by_navigation(world, world.node(node)):
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
        # A row is listed only in its own App, a stub too (the check decides a node in its own App).
        app = node if world.node(node).kind == "app" else world.node(node).connector
        for foreign in sorted(i for i in got if world.node(i).connector != app):
            add("browse_extra", f"{node}: lists {foreign}, a node of another App")
        # A readable child the browse leaves out is an under-admit. The one exemption is a folder that hides its children (a display flag).
        for missing in sorted(n.id for n in children(node) if n.id in seen and n.id not in got):
            if not hidden_by_navigation(world, world.node(missing)):
                add("gap" if capped(node, missing, got) else "browse_gap", f"{node}: the browse leaves out {missing}, which the viewer may open")
        # A stub is no content the oracle judges: it is listed exactly where opening it lists something.
        for stub in sorted(n.id for n in children(node) if n.placeholder and n.id not in got):
            below = await open_(stub, "record", kind="stubs")
            if below and below is not SKIPPED:
                add("browse_gap", f"{node}: the browse leaves out the stub {stub}, and opening it lists something")
        return got

    def type_of(item: object) -> str:
        return str(getattr(item.nodeType, "value", item.nodeType))

    async def sidebar(node: str, parent_type: str, rows: dict[str, Any], lists: dict[str, bool]) -> None:
        """The sidebar (the rows with children) of ``node``: every group and folder the browse lists, and a record exactly where opening it lists
        something."""
        result = await browse_pages(layer, world, viewer, node, parent_type, BIG_PAGE, only_containers=True)
        got = {ns.local(item.id) for item in (result[0] if result else [])}
        want = {row for row, item in rows.items() if type_of(item) != "record" or lists[row]}
        for missing in sorted(want - got):
            add("sidebar", f"{node}: the sidebar leaves out {missing}, and opening it lists something")
        for extra in sorted(got - want):
            add("sidebar", f"{node}: the sidebar lists {extra}, and opening it lists nothing")

    queue = [(n.id, "app") for n in world.nodes if n.kind == "app"]
    expanded: set[str] = set()
    while queue:
        node, parent_type = queue.pop(0)
        if node in expanded:
            continue
        expanded.add(node)
        rows = await open_(node, parent_type)
        if rows is SKIPPED:      # past the budget: counted, not opened (a second App, say)
            continue
        rows = rows or {}
        lists_of: dict[str, bool] = {}
        for row, item in rows.items():      # the expand arrow is on exactly when opening the row lists something
            row_type = type_of(item)
            below = await open_(row, row_type)
            if below is SKIPPED:
                continue
            if below is None:      # a row is listed only where it opens (a leaf to an empty list)
                add("browse", f"{node}: lists {row}, and the browse refuses to open it")
            lists = lists_of[row] = bool(below)
            if bool(getattr(item, "hasChildren", False)) != lists:
                add("arrow", f"{node}: {row} hasChildren {item.hasChildren}, opening it lists something: {lists}")
            if getattr(item, "isPlaceholder", False) and not lists:      # a stub is listed only as the way to something the viewer may open
                add("stub", f"{node}: lists the stub {row}, and opening it lists nothing")
            queue.append((row, row_type))
        if len(lists_of) == len(rows) and any(type_of(item) == "record" for item in rows.values()):      # only a record can drop out
            await sidebar(node, parent_type, rows, lists_of)
    if skipped["rows"] or skipped["stubs"]:
        add("truncated", f"opened {len(opened)} nodes (the cap); {len(skipped['rows'])} listed rows and {len(skipped['stubs'])} stubs left out"
                         " not opened")
    return found
