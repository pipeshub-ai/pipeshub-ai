"""The Knowledge Hub layer (``tests/permissions/kh_layer.py``) on the PostgreSQL provider: the rows, totals, counts, facets and arrows of every golden, adversarial and generated world
against the access oracle, with the world's connectors served from kg ("current"), with kg not trusted for them ("not current": the listing's access is then the live walk's), and
copied by kg but neither stamped, verified nor promoted ("copied": what a new connector is until the bootstrap promotes it; the structure is kg's, the access the walk's).
The states must answer the same rows and arrows; the totals are exact in all of them for the worlds of these sizes."""
from __future__ import annotations

import logging
import os
import zlib
from typing import TYPE_CHECKING, Any

import asyncpg
import pytest

from app.connectors.sources.localKB.handlers.kh_merge import key_for
from app.core.org_context import OrgContext, bind
from app.exceptions.graph_db_exceptions import KnowledgeHubListingNotReady
from app.services.graph_db.postgres import kh_browse
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import counts as kg_counts
from app.services.graph_db.postgres.kg.drainer import drain_org
from tests.permissions import gen
from tests.permissions.graph import Namespace
from tests.permissions.kh_layer import (
    BIG_PAGE,
    HIERARCHY,
    KhFinding,
    browse_pages,
    check_world,
    children_of,
    cursor_in_the_clear,
    listing,
)
from tests.permissions.kh_layer import _Config as _KhConfig
from tests.permissions.kh_worlds import (
    APPCHAIN,
    CHAIN,
    CHAINLINK,
    DECLPAG,
    KH_WORLDS,
    OPENGRANT,
    OPENGRANTTEAM,
    PAGMEMBER,
    PAGSEED,
    PAGSTORY,
    STUBHELD,
    STUBREACH,
    STUBTOP,
    UNPLACED,
    WIDE,
)
from tests.permissions.scenario import load_all

from .test_conformance import PgLayer
from .test_kg_read import KgLayer, _admin, _hex, flip

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(3600)]

CHUNK = 25
GENERATED_WORLDS = 12      # per generator and state on a pull request (the not-current state asks the walk about every row); a nightly run sets PERM_GEN_WORLDS
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


class _Ns(Namespace):
    """The store's org of a world is the hash of the namespace's (``PgLayer``), and the listing asks for it by that."""

    @property
    def org(self) -> str:      # type: ignore[override]
        return _hex(self._org)

    @org.setter
    def org(self, value: str) -> None:
        self._org = value


# Prefixes put in front of every name of a world: mixed case, accents, a character in U+E000..U+FFFF and one above U+FFFF (a UTF-16 surrogate pair). In UTF-16 code-unit order (what the listing is
# merged and paged in) the last sorts before U+E000..U+FFFF; by code point it sorts after them.
PREFIXES = ["", "É", "é", "Z", "z", "\uE000", "\uFFFD", "\U00010000", "\U0001F600", "a"]


async def _decorate_names(org: str) -> None:
    """Give the names of a stored world those prefixes (chosen by the node's id, so every run of a world is the same), before it is copied into kg."""
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        for coll, key in (("records", "recordName"), ("recordGroups", "groupName")):
            rows = await con.fetch("SELECT id, doc->>$3 AS name FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>$3 IS NOT NULL", org, coll, key)
            ids = [r["id"] for r in rows]
            names = [PREFIXES[zlib.crc32(r["id"].encode()) % len(PREFIXES)] + r["name"] for r in rows]
            await con.execute("UPDATE graph.nodes n SET doc = jsonb_set(n.doc, ARRAY[$3], to_jsonb(x.name)) FROM unnest($4::text[], $5::text[]) AS x(id, name) WHERE n.org_id = $1 AND n.coll = $2 AND n.id = x.id",
                              org, coll, key, ids, names)
    finally:
        await con.close()


async def _copy(db, org: str) -> None:
    """What the bootstrap does first for each connector of the org: the log drained, the principals and the connector copied. No stamps, no verify, no promotion. A world with a value the
    projection refuses is left uncopied (``flip`` does the same)."""
    async def apps(tx) -> list[str]:
        return [r["id"] for r in await tx.fetch("SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' ORDER BY id", org)]
    with bind(OrgContext(org, None, "job", "job:test", "t")):
        ids = await db.run(apps)
    try:
        await drain_org(db, org)
        await bf.backfill_scope(db, org, None)
        for a in ids:
            await bf.backfill_scope(db, org, a)
    except asyncpg.exceptions.InvalidParameterValueError:
        pass


class KhPgLayer(KgLayer):
    """``KgLayer`` whose worlds are listed through the product's own listing; ``not_current`` takes kg off every connector of the world after it was copied and verified; ``copied``
    only copies them."""

    def __init__(self, provider, db, *, not_current: bool, copied: bool = False) -> None:
        super().__init__(provider, db)
        self.not_current = not_current
        self.copied = copied
        self.not_ready = 0
        self.hide_first_app = False
        self.copies: dict = {}      # world -> (connectors promoted, connectors) of its own copy
        self.name = f"postgres-kg-{'copied' if copied else 'walk' if not_current else 'kg'}"

    async def preload(self, worlds) -> None:
        new = [w for w in dict.fromkeys(worlds) if w not in self._prefix]
        await PgLayer.preload(self, new)
        for w in new:
            org = _hex(Namespace(self._prefix[w]).org)
            await _decorate_names(org)
            if self.hide_first_app:      # in the graph, before the copy: as a project's own collection is written
                await _admin("""UPDATE graph.nodes SET doc = doc || '{"isHidden": true}' WHERE org_id = $1 AND coll = 'apps'
                    AND id = (SELECT min(id) FROM graph.nodes WHERE org_id = $1 AND coll = 'apps')""", org)
            if self.copied:
                await _copy(self._db, org)
                continue
            done, total = await flip(self._db, org)
            self.promoted.update(promoted=done, connectors=total)
            self.copies[w] = (done, total)
        if self.not_current:
            for w in new:
                await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", _hex(Namespace(self._prefix[w]).org))

    async def _ns(self, world) -> Namespace:
        await super()._ns(world)
        return _Ns(self._prefix[world])


@pytest.fixture(params=["current", "not_current"])
async def kh_layer(request, pg_provider, pg_db) -> AsyncIterator[KhPgLayer]:
    lay = KhPgLayer(pg_provider, pg_db, not_current=request.param == "not_current", copied=request.param == "copied")
    yield lay
    await lay.release(list(lay._prefix))


# `gap`: the shapes the flatten listing leaves out on purpose (kh_layer.declared_gap). The browse is held to the oracle in full: a folder it does not open or leaves nodes out of (`browse_gap`),
# a row it lists that the oracle does not admit (`browse_extra`) and an arrow that is not on exactly when opening the row lists something (`arrow`) fail, as on Neo4j and ArangoDB.
# `unlisted` (a node the viewer may open that no browse of the whole tree lists) is reported, as there.
GAPS = {"gap", "unlisted"}


async def check(layer: KhPgLayer, world, scenario: str) -> list[KhFinding]:
    """``check_world`` as the product runs a request: under the world's own org."""
    with bind(OrgContext((await layer._ns(world)).org, None, "job", "job:test", "t")):
        try:
            found = await check_world(layer, world, scenario, browse_apps=True)
            for viewer in world.users():      # the order of the whole listing is the merge's (a string by UTF-16 code unit): what pages and cursors are cut by
                keys = [key_for(r, descending=False) for r in (await listing(layer, world, viewer)).rows]
                if keys != sorted(keys):
                    found.append(KhFinding(scenario, viewer, "order", f"rows out of the merge's order: {[k.value for k in keys][:8]}"))
            return found
        except KnowledgeHubListingNotReady:      # a world the projection refuses (a value the typed graph has no column for) is never copied: the listing says so, and the layer counts it
            layer.not_ready += 1
            return []


def _describe(found: list[KhFinding]) -> str:
    return "\n".join(f"{f.kind} {f.scenario} {f.viewer}: {f.detail}" for f in found[:40])


def _bad(found: list[KhFinding]) -> list[KhFinding]:
    return [f for f in found if f.kind not in GAPS]


@pytest.mark.parametrize("kh_layer", ["current", "not_current", "copied"], indirect=True)
async def test_golden_and_adversarial_worlds_list_what_the_viewer_may_read(kh_layer) -> None:
    worlds = [*dict.fromkeys(s.final_world() for s in load_all()), *KH_WORLDS]
    bad: list[KhFinding] = []
    for start in range(0, len(worlds), CHUNK):
        chunk = worlds[start:start + CHUNK]
        await kh_layer.preload(chunk)
        for offset, world in enumerate(chunk):
            bad += _bad(await check(kh_layer, world, f"golden-{start + offset}"))
        await kh_layer.release(chunk)
    assert not bad, f"{kh_layer.name}\n{_describe(bad)}"
    print(f"{kh_layer.name}: {len(worlds)} golden worlds, {kh_layer.not_ready} refused as not copied, promoted {dict(kh_layer.promoted)}")
    assert kh_layer.not_ready * 10 <= len(worlds), f"{kh_layer.not_ready} of {len(worlds)} worlds were refused as not copied"


async def test_golden_and_adversarial_worlds_count_from_the_counters_what_the_viewer_may_read(pg_provider, pg_db, monkeypatch) -> None:
    """With the counters read (PG_KG_COUNTERS=1) an unfiltered total of a connector kg serves comes from kg.counts: every total, count and facet is still the oracle's."""
    monkeypatch.setenv("PG_KG_COUNTERS", "1")
    layer = KhPgLayer(pg_provider, pg_db, not_current=False)
    worlds = [*dict.fromkeys(s.final_world() for s in load_all()), *KH_WORLDS]
    bad: list[KhFinding] = []
    seeded = 0
    try:
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for world in chunk:
                seeded += await kg_counts.run(pg_db, _hex(Namespace(layer._prefix[world]).org))
            for offset, world in enumerate(chunk):
                bad += _bad(await check(layer, world, f"golden-{start + offset}"))
            await layer.release(chunk)
    finally:
        await layer.release(list(layer._prefix))
    assert seeded, "no connector was counted whole: the counters were never read"
    assert not bad, f"{layer.name} with counters\n{_describe(bad)}"


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_list_what_the_viewer_may_read(kh_layer, kind: str, monkeypatch) -> None:
    # The typed graph takes the four access rules a connector writes; the adversarial generator also writes INHERITED, which no connector does and which the projection refuses (the connector is then never
    # copied and the listing refuses it). The worlds are generated without it, so that every adversarial shape is listed.
    from tests.permissions.gen import adversarial
    monkeypatch.setattr(adversarial, "RULES", [r for r in adversarial.RULES if r != "INHERITED"])
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, int(os.environ.get("PERM_GEN_WORLDS", GENERATED_WORLDS)))
        bad: list[KhFinding] = []
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await kh_layer.preload(chunk)
            for offset, world in enumerate(chunk):
                bad += _bad(await check(kh_layer, world, f"{kind}-{seed}-{start + offset}"))
            await kh_layer.release(chunk)
        print(f"{kh_layer.name} {kind} seed {seed}: {len(worlds)} worlds, {kh_layer.not_ready} refused as not copied so far")
        assert not bad, f"{kh_layer.name} {kind} seed {seed}\n{_describe(bad)}"
        assert kh_layer.not_ready * 10 <= len(worlds), f"{kh_layer.not_ready} of {len(worlds)} {kind} worlds were refused as not copied"


async def _root_rows(layer: KhPgLayer, world, viewer: str) -> list[str] | None:
    """Every page of the service's All Records (``get_nodes`` with no parent, flattened), by cursor; None when a page fails."""
    from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
        KnowledgeHubService,
    )
    from tests.permissions.kh_layer import _Config

    ns = await layer._ns(world)
    service = KnowledgeHubService(logging.getLogger("kh-root"), layer._provider, _Config())
    rows: list[str] = []
    cursor = None
    for _ in range(200):
        page = await service.get_nodes(user_id=ns.id(viewer), org_id=ns.org, flattened=True, limit=BIG_PAGE, sort_by="name", sort_order="asc", cursor=cursor)
        if not page.success:
            return None
        rows += [ns.local(i.id) for i in page.items]
        if not (cursor := page.pagination.nextCursor):
            return rows
    raise AssertionError("the root listing did not end")


async def test_the_root_listing_is_the_flat_listing_of_every_app_but_the_hidden_one(kh_layer) -> None:
    """The root listing (All Records) against the provider's flat listing of the same Apps, every viewer of the golden and KH worlds, with each world's first App hidden:
    the same rows in the same order, and none of the hidden App's."""
    kh_layer.hide_first_app = True
    worlds = [*dict.fromkeys(s.final_world() for s in load_all()), *KH_WORLDS]
    bad: list[str] = []
    for start in range(0, len(worlds), CHUNK):
        chunk = worlds[start:start + CHUNK]
        await kh_layer.preload(chunk)
        for world in chunk:
            ns = await kh_layer._ns(world)
            apps = sorted(ns.id(n.id) for n in world.nodes if n.kind == "app")
            if not apps:
                continue
            with bind(OrgContext(ns.org, None, "job", "job:test", "t")):
                for viewer in world.users():
                    try:
                        want = [r["id"] for r in (await listing(kh_layer, world, viewer, {"connector_ids": apps[1:]})).rows] if apps[1:] else []
                    except KnowledgeHubListingNotReady:
                        continue
                    got = await _root_rows(kh_layer, world, viewer)
                    if got != want:
                        bad.append(f"{viewer}: root {got} flat {want}")
        await kh_layer.release(chunk)
    assert not bad, f"{kh_layer.name}\n" + "\n".join(bad[:20])


async def test_a_stub_is_listed_under_a_declaration_exactly_when_opening_it_lists_something(kh_layer) -> None:
    """STUBREACH: the stub's listing and arrow read the reach a browse of the stub starts with, never the declaration's (what opening the declaration and then the stub shows must agree)."""
    await kh_layer.preload([STUBREACH])
    with bind(OrgContext((await kh_layer._ns(STUBREACH)).org, None, "job", "job:test", "t")):
        bad = _bad(await check_world(kh_layer, STUBREACH, "stubreach", browse_apps=True))
        under_d = await children_of(kh_layer, STUBREACH, "u", "d", "recordGroup")
        under_s = await children_of(kh_layer, STUBREACH, "u", "s", "record")
    assert not bad, _describe(bad)
    assert under_d is not None and ("s" in under_d) == bool(under_s), (under_d, under_s)
    assert under_d.get("s", False) == bool(under_s), (under_d, under_s)
    print(f"{kh_layer.name} STUBREACH: d lists {under_d}, s lists {under_s}")


async def test_a_connector_with_no_copy_is_refused_not_listed_as_empty(pg_provider, pg_db) -> None:
    """The listing of a connector the typed graph has never copied raises the refusal the service answers as an empty page marked ``preparing`` (ask again in a moment)."""
    from app.connectors.sources.localKB.handlers.kh_search import search_page
    lay = KhPgLayer(pg_provider, pg_db, not_current=False)
    world = KH_WORLDS[0]
    try:
        await PgLayer.preload(lay, [world])      # stored, never copied
        ns = await lay._ns(world)
        viewer = world.users()[0]
        with bind(OrgContext(ns.org, None, "job", "job:test", "t")), pytest.raises(KnowledgeHubListingNotReady):
            await search_page(pg_provider, user_key=ns.id(viewer), user_id=ns.id(viewer), org_id=ns.org, limit=5, secret=None)
    finally:
        await lay.release(list(lay._prefix))


async def test_a_granted_strict_group_below_a_seed_the_app_does_not_reach_is_read_and_listed_nowhere(kh_layer) -> None:
    """The strict group of OPENGRANT / OPENGRANTTEAM, granted to the viewer directly or through a group of users: the access check denies it, as the oracle
    does (its only chain to the App runs through a declaration), and the listing holds exactly what the check admits, from kg and from the walk."""
    await kh_layer.preload([OPENGRANT, OPENGRANTTEAM])
    for world in (OPENGRANT, OPENGRANTTEAM):
        ns = await kh_layer._ns(world)
        with bind(OrgContext(ns.org, None, "job", "job:test", "t")):
            found = await kh_layer._provider.check_access(ns.id("u"), ns.org, node_ids=[ns.id(n) for n in ("k", "gs", "go")])
            assert {ns.local(n) for n in found.node_ids} == {"k", "go"}, kh_layer.name
            assert {r["id"] for r in (await listing(kh_layer, world, "u")).rows} == {"k", "go", "d"}, kh_layer.name
        assert not _bad(await check(kh_layer, world, "opengrant")), kh_layer.name


async def test_a_page_that_ends_at_a_scan_bound_names_no_child_the_viewer_may_not_open(kh_layer, monkeypatch) -> None:
    """WIDE read a window at a time with no time to read past the first: a page of f holds fewer than a page of readable children, so it ends at the
    last child it looked at, which is most likely one the viewer may not open, and the next page resumes there. The cursor carries that position
    sealed: a client reading it finds no name or id of a child it may not open, and the pages still list all forty readable children once."""
    monkeypatch.setattr(kh_browse, "UNDECIDED_MAX", -1)      # the children are read a window at a time, as for a connector kg does not serve
    monkeypatch.setattr(kh_browse, "BUDGET_S", -1.0)
    readable = [f"k{i:03d}" for i in (*range(10), *range(200, 230))]
    denied = [f"k{i:03d}" for i in range(10, 200)]
    await kh_layer.preload([WIDE])
    ns, cursors = await kh_layer._ns(WIDE), []
    with bind(OrgContext(ns.org, None, "job", "job:test", "t")):
        items, _, _ = await browse_pages(kh_layer, WIDE, "u", "f", "recordGroup", 50, cursors=cursors)
    listed = [ns.local(i.id) for i in items]
    assert sorted(listed) == readable, kh_layer.name      # the order is the store's (these names sort by their stored keys), each row once
    assert cursors, kh_layer.name
    assert not [n for c in cursors for n in denied if n in cursor_in_the_clear(c)], kh_layer.name


async def test_a_browse_lists_the_chain_tops_the_stores_place_under_it_and_their_arrows(kh_layer) -> None:
    """A granted record with no hierarchy parent the viewer may open is listed under its own group, else under the App (the placement rule Neo4j and
    ArangoDB share). APPCHAIN: opening h lists r, and h's arrow is on in All Records and under the App. CHAIN, CHAINLINK and STUBHELD have the same shape."""
    worlds = [APPCHAIN, CHAIN, CHAINLINK, STUBHELD]
    await kh_layer.preload(worlds)
    with bind(OrgContext((await kh_layer._ns(APPCHAIN)).org, None, "job", "job:test", "t")):
        assert await children_of(kh_layer, APPCHAIN, "u", "h", "recordGroup") == {"r": False}, kh_layer.name
        assert await children_of(kh_layer, APPCHAIN, "u", "A", "app") == {"h": True}, kh_layer.name
        assert {r["id"]: r["hasChildren"] for r in (await listing(kh_layer, APPCHAIN, "u")).rows}["h"] is True, kh_layer.name
    bad = [f for world in worlds for f in _bad(await check(kh_layer, world, "chain"))]
    assert not bad, _describe(bad)


async def test_a_placeholder_group_whose_only_content_is_a_chain_top_is_listed_with_its_arrow(kh_layer) -> None:
    """STUBTOP: opening the placeholder group s lists its chain-top r, so the folder f lists s, with an arrow: a placeholder leads by the chain-top rule as well as by its
    hierarchy children."""
    await kh_layer.preload([STUBTOP])
    with bind(OrgContext((await kh_layer._ns(STUBTOP)).org, None, "job", "job:test", "t")):
        assert await children_of(kh_layer, STUBTOP, "u", "s", "recordGroup") == {"r": False}, kh_layer.name
        assert await children_of(kh_layer, STUBTOP, "u", "f", "recordGroup") == {"s": True}, kh_layer.name
        assert {r["id"]: r["hasChildren"] for r in (await listing(kh_layer, STUBTOP, "u")).rows}["f"] is True, kh_layer.name
    bad = _bad(await check(kh_layer, STUBTOP, "stubtop"))
    assert not bad, _describe(bad)


async def _flat_pages(layer: KhPgLayer, world, viewer: str, node: str, parent_type: str, depth: int | None) -> dict[str, Any] | None:
    """Every row of ``get_nodes(flattened=True)`` below ``node`` (``depth``: its bound), {id: item}; None when the flatten does not open the node."""
    from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
        KnowledgeHubService,
    )
    ns = await layer._ns(world)
    service = KnowledgeHubService(logging.getLogger("kh-flat"), layer._provider, _KhConfig())
    rows: dict[str, Any] = {}
    cursor = None
    for _ in range(100):
        args = {"user_id": ns.id(viewer), "org_id": ns.org, "parent_id": ns.id(node), "parent_type": parent_type, "limit": 50, "sort_by": "name", "sort_order": "asc",
                "flattened": True, "depth": depth}
        page = await service.get_nodes(**args, **({"cursor": cursor} if cursor else {}))
        if not page.success:
            return None
        rows |= {ns.local(i.id): i for i in page.items}
        cursor = page.pagination.nextCursor
        if not cursor:
            return rows
    raise AssertionError("the flatten did not end")


def _hier_children(world, node: str) -> set[str]:
    return {n.id for n in world.nodes if any(p.id == node and p.rel in HIERARCHY for p in n.parents)}


async def _browsed_down(layer: KhPgLayer, world, viewer: str, node: str, parent_type: str, depth: int) -> dict[str, bool] | None:
    """What browsing down from ``node`` reaches in ``depth`` hops, opening every hierarchy child a browse lists, {id: is a placeholder}; None when the browse does not open ``node``."""
    first = await browse_pages(layer, world, viewer, node, parent_type, BIG_PAGE)
    if first is None:
        return None
    got: dict[str, bool] = {}
    frontier = {node: first[0]}
    for _ in range(depth):
        nxt = {}
        for at, items in frontier.items():
            for item in items:
                i = (await layer._ns(world)).local(item.id)
                if i in got or i not in _hier_children(world, at):      # a chain-top a browse places here is no hierarchy child: no flatten lists it (Neo4j, ArangoDB)
                    continue
                got[i] = bool(item.isPlaceholder)
                kind = world.node(i).kind
                below = await browse_pages(layer, world, viewer, i, "recordGroup" if kind == "group" else "record", BIG_PAGE)
                nxt[i] = below[0] if below else []
        frontier = nxt
    return got


def _within(world, app: str, depth: int) -> set[str]:
    """The nodes within ``depth`` hierarchy hops of the App (through any node, a deleted one too, as Neo4j walks), and the records no hierarchy edge places that belong to it or to
    a group fewer hops down."""
    levels = [{app}]
    for _ in range(depth):
        levels.append({c for p in levels[-1] for c in _hier_children(world, p)} - set().union(*levels))
    near = set().union(*levels[1:])
    shorter = {app, *(i for i in set().union(*levels[:-1]) if world.node(i).kind == "group")}
    placed = {n.id for n in world.nodes if any(p.rel in HIERARCHY and any(m.id == p.id for m in world.nodes) for p in n.parents)}
    unplaced = {n.id for n in world.nodes if n.kind == "record" and n.id not in placed and set(n.belongs) & shorter}
    return near | unplaced


async def test_a_flatten_below_a_node_or_with_a_depth_lists_what_browsing_down_reaches(kh_layer) -> None:
    """A flatten from a record group or a folder lists the rows browsing down from it reaches within the depth, each a hierarchy child the viewer may open (Neo4j's and
    ArangoDB's walk from the start), and refuses a start the browse refuses. From the App, a depth keeps the flatten's rows within that many hops (and the records the hierarchy
    does not place, one below their group). The same from kg and from the walk."""
    worlds = [*KH_WORLDS, *dict.fromkeys(s.final_world() for s in load_all())][:40]
    bad: list[str] = []
    asked = opened = 0
    for start in range(0, len(worlds), CHUNK):
        chunk = worlds[start:start + CHUNK]
        await kh_layer.preload(chunk)
        for w_i, world in enumerate(chunk):
            ns = await kh_layer._ns(world)
            with bind(OrgContext(ns.org, None, "job", "job:test", "t")):
                try:
                    for viewer in world.users()[:2]:
                        starts = [n for n in world.nodes if n.kind != "app" and _hier_children(world, n.id)][:6]
                        for n in starts:
                            ptype = "recordGroup" if n.kind == "group" else "record"
                            for depth in (1, 2, 50):
                                want = await _browsed_down(kh_layer, world, viewer, n.id, ptype, depth)
                                got = await _flat_pages(kh_layer, world, viewer, n.id, ptype, depth)
                                asked += 1
                                opened += got is not None
                                want_rows = None if want is None else {i for i, stub in want.items() if not stub}
                                if (got is None) != (want is None) or (got is not None and set(got) != want_rows):
                                    bad.append(f"{start + w_i} {viewer} below {n.id} depth {depth}: flatten {None if got is None else sorted(got)} browse {want_rows and sorted(want_rows)}")
                        whole = {r["id"] for r in (await listing(kh_layer, world, viewer)).rows}
                        for app in (n for n in world.nodes if n.kind == "app"):
                            opens = await browse_pages(kh_layer, world, viewer, app.id, "app", BIG_PAGE) is not None      # the flatten opens exactly the Apps a browse opens
                            for depth in (1, 2):
                                got = await _flat_pages(kh_layer, world, viewer, app.id, "app", depth)
                                want_rows = {i for i in whole if world.node(i).connector == app.id} & _within(world, app.id, depth)
                                asked += 1
                                opened += got is not None
                                if (got is not None) != opens or (got is not None and set(got) != want_rows):
                                    bad.append(f"{start + w_i} {viewer} app {app.id} depth {depth}: flatten {None if got is None else sorted(got)} want {sorted(want_rows) if opens else None}")
                except KnowledgeHubListingNotReady:
                    kh_layer.not_ready += 1
        await kh_layer.release(chunk)
    print(f"{kh_layer.name}: {asked} flattens asked, {opened} opened their start")
    assert asked and not bad, f"{kh_layer.name}\n" + "\n".join(bad[:30])
    assert opened * 2 >= asked, f"{kh_layer.name}: only {opened} of {asked} flattens opened their start: the comparison proves little"


async def test_a_chain_top_nothing_else_lists_is_listed_under_the_app(kh_layer) -> None:
    """UNPLACED: r1's parent and own group are closed, so the App lists it; r2's own group opens, so it is listed there and not under the App; r3
    is granted but the check denies it, so it is listed nowhere."""
    await kh_layer.preload([UNPLACED])
    with bind(OrgContext((await kh_layer._ns(UNPLACED)).org, None, "job", "job:test", "t")):
        assert await children_of(kh_layer, UNPLACED, "u", "A", "app") == {"open": True, "r1": False}, kh_layer.name
        assert await children_of(kh_layer, UNPLACED, "u", "open", "recordGroup") == {"r2": False}, kh_layer.name
    bad = _bad(await check(kh_layer, UNPLACED, "unplaced"))
    assert not bad, _describe(bad)


async def test_a_parent_and_grant_record_is_read_below_a_parent_read_by_its_own_grant_and_nowhere_else(kh_layer: KhPgLayer) -> None:
    """PAGSEED: dave reads the issue i1 only by his own grant, and the PARENT_AND_GRANT comment c1 (granted to his group), the file f1 in it and the reply c5
    granted to him, by the access check and by browsing i1; never without the record's own grant or under a parent he cannot read. PAGSTORY: a RESTRICTED story
    under an epic read the same way stays closed. DECLPAG: under a member of a declared group it stays closed, as an OPEN child there does. PAGMEMBER: under
    a member that is also read below a seed it is read, as an OPEN child there is. The worlds are copied into kg and served from it ("current"), or answered by the walk ("not_current")."""
    expected = {
        (PAGSEED, "dave"): {"i1", "c1", "f1", "c5"},
        (PAGSEED, "alice"): {"P", "i1", "i2", "c1", "f1", "c3", "s1"},
        (PAGSEED, "bob"): {"P", "i1", "i2", "c2"},
        (PAGSEED, "carol"): {"i2"},
        (PAGSTORY, "dave"): {"epic"},
        (DECLPAG, "u"): {"d", "k"},
        (PAGMEMBER, "u"): {"S", "M", "C", "O"},
    }
    asked = {PAGSEED: ["P", "i1", "i2", "c1", "c2", "c3", "c4", "c5", "f1", "s1"], PAGSTORY: ["P", "epic", "story", "sub", "plain", "note"], DECLPAG: ["d", "k", "c"], PAGMEMBER: ["S", "M", "C", "O"]}
    worlds = [PAGSEED, PAGSTORY, DECLPAG, PAGMEMBER]
    await kh_layer.preload(worlds)
    for world in worlds:      # no world is refused by the copy
        done, total = kh_layer.copies[world]
        assert total > 0 and done == total, (kh_layer.name, worlds.index(world), done, total)
    for (world, who), want in expected.items():
        ns = await kh_layer._ns(world)
        with bind(OrgContext(ns.org, None, "job", "job:test", "t")):
            found = await kh_layer._provider.check_access(ns.id(who), ns.org, node_ids=[ns.id(n) for n in asked[world]])
        assert {ns.local(n) for n in found.node_ids - found.placeholder_ids} == want, (kh_layer.name, who)
    with bind(OrgContext((await kh_layer._ns(PAGSEED)).org, None, "job", "job:test", "t")):
        assert await children_of(kh_layer, PAGSEED, "dave", "i1", "record") == {"c1": True}, kh_layer.name
        assert await children_of(kh_layer, PAGSEED, "dave", "c1", "record") == {"f1": False, "c5": False}, kh_layer.name
    with bind(OrgContext((await kh_layer._ns(PAGSTORY)).org, None, "job", "job:test", "t")):
        assert await children_of(kh_layer, PAGSTORY, "dave", "epic", "record") == {}, kh_layer.name
    bad = []
    for i, world in enumerate(worlds):
        with bind(OrgContext((await kh_layer._ns(world)).org, None, "job", "job:test", "t")):      # not ``check``: a world the copy refuses fails here
            bad += _bad(await check_world(kh_layer, world, f"pag-{i}", browse_apps=True))
    assert not bad, f"{kh_layer.name}\n{_describe(bad)}"
