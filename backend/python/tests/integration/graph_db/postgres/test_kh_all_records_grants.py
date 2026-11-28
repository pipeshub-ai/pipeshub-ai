"""All Records (the knowledge hub's listing of every connector) leaves the viewer's grants to each connector's page when kg holds every gated connector current
(``kh_pages_read_grants``): each page reads them as kg keys, and the merged page's parents are named by the check reading grants per node. Otherwise the listing reads every
grant by node id once, for all pages, as before. Both give the same rows, parents, arrows, totals and counts, for each rule of the read by id."""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.core.db import uow
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres.kg.drainer import drain_org
from tests.permissions import gen
from tests.permissions import kh_layer as khl
from tests.permissions.kh_worlds import KH_WORLDS
from tests.permissions.scenario import load_all

from . import test_kh_kg_grants as kgg
from .pg_support import new_org
from .test_conformance_kh_pg import CHUNK, KhPgLayer, _bad, _describe, check
from .test_kg_derivation import ctx
from .test_kg_read import _admin, _switch
from .test_kh_listing_flat import prov

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]
reads = kgg.reads      # the fixture: every grant read a page takes (True: kg keys)

WORLDS = 8      # generated worlds per generator

GRANT_READS = ("pipeshub:graph.access_grants_all */", "pipeshub:kg.viewer_grants */", "pipeshub:graph.access_grants_of */")


def service(p) -> KnowledgeHubService:
    svc = KnowledgeHubService(logging.getLogger("kh-all-records"), p, khl._Config(), frozenset())

    async def user(user_id: str, _org: str) -> dict[str, Any]:
        return {"_key": user_id, "email": f"{user_id}@example.com"}
    svc._resolve_user = user
    return svc


async def all_records(p, org: str, viewer: str, **kw: Any) -> list[Any]:  # noqa: ANN401
    """Every page of All Records the viewer sees (by cursor), each as its rows (id, parent, arrow) and its total and counts."""
    svc, out, cursor = service(p), [], None
    with ctx(org):
        while True:
            resp = await svc.get_nodes(viewer, org, flattened=True, include=["counts"], limit=kw.get("limit", 3), cursor=cursor, sort_by="name", sort_order="asc")
            assert resp.success, resp.error
            out.append(([(i.id, i.parentId, i.hasChildren) for i in resp.items], resp.pagination.totalItems, resp.counts.model_dump() if resp.counts else None))
            cursor = resp.pagination.nextCursor if resp.pagination.hasNext else None
            if cursor is None:
                return out


def seen(pages: list[Any]) -> set[str]:
    return {i for rows, _, _ in pages for i, _, _ in rows}


async def as_before(p, coro: Any) -> Any:  # noqa: ANN401
    """``coro`` run as All Records ran before: every grant read by node id once, for every page (the provider's pages not reading their own)."""
    p.kh_grants_per_connector = False
    try:
        return await coro
    finally:
        del p.kh_grants_per_connector


async def both(p, org: str, viewer: str, reads: list[bool]) -> list[Any]:
    """All Records with each page reading its grants from kg, then as before (every grant read by node id once, ``kh_grants_per_connector`` off), asserted equal."""
    reads.clear()
    by_sk = await all_records(p, org, viewer)
    assert reads and all(reads), reads
    reads.clear()
    by_id = await as_before(p, all_records(p, org, viewer))
    assert reads and not any(reads), reads
    assert by_sk == by_id
    return by_sk


@pytest.mark.parametrize("rule", list(kgg.CASES))
async def test_each_grant_rule_gives_the_same_all_records_read_by_kg_keys_and_by_node_ids(pg_db, reads, rule) -> None:
    grants, deleted, viewer, see, hide = kgg.CASES[rule]
    p, org = prov(pg_db), new_org()
    await kgg.world(p, pg_db, org, [kgg._perm(*[org if x == kgg.ORG_ else x for x in g]) for g in grants], deleted=deleted)
    got = await both(p, org, viewer, reads)
    assert see <= seen(got) and not (hide & seen(got)), got


async def test_a_parent_is_named_only_when_the_viewer_may_open_it(pg_db, reads) -> None:
    """k0 inherits from F: with a grant on F it is listed under F; with a grant on k0 alone (F closed) its parent is the App, read by kg keys as by node id."""
    p, org = prov(pg_db), new_org()
    await kgg.world(p, pg_db, org, [kgg._perm("u1", "users", "F"), kgg._perm("u2", "users", "k0")])
    named = {v: {i: parent for rows, _, _ in await both(p, org, v, reads) for i, parent, _ in rows} for v in ("u1", "u2")}
    assert named["u1"]["k0"] == "F" and named["u2"] == {"k0": "app1"}, named


async def test_a_change_kg_has_not_drained_reads_every_grant_once_by_node_id(pg_db, reads) -> None:
    p, org = prov(pg_db), new_org()
    await kgg.world(p, pg_db, org, [kgg._perm("u1", "users", "L1")])
    with ctx(org):
        await p.batch_create_edges([kgg._perm("u1", "users", "L2")], "permission")
    calls: list[str] = []
    real = p.get_knowledge_hub_access_v3

    async def counted(*a: Any, **kw: Any) -> dict[str, Any]:  # noqa: ANN401
        calls.append(kw.get("user_key") or a[0])
        return await real(*a, **kw)
    p.get_knowledge_hub_access_v3 = counted
    reads.clear()
    got = await all_records(p, org, "u1", limit=50)
    assert {"L1", "L2"} <= seen(got) and calls == ["u1"] and reads and not any(reads), (calls, reads)
    await drain_org(pg_db, org)
    calls.clear()
    reads.clear()
    assert await all_records(p, org, "u1", limit=50) == got and calls == [] and reads and all(reads)


async def test_a_page_that_finds_kg_behind_reads_by_node_id_and_names_the_same_parents(pg_db, monkeypatch, reads) -> None:
    """kg fell behind between the listing's check and a page (the check said yes): that page reads by node id in its snapshot; the others' grants are unread by id, and the
    parents are named by the check reading grants per node."""
    p, org = prov(pg_db), new_org()
    await kgg.world(p, pg_db, org, [kgg._perm("u1", "users", "F"), kgg._perm("u1", "users", "A1")])
    want = await all_records(p, org, "u1")
    with ctx(org):
        await p.batch_create_edges([kgg._perm("u1", "users", "L3")], "permission")

    async def yes(*_a: Any) -> bool:  # noqa: ANN401
        return True
    monkeypatch.setattr(p, "kh_pages_read_grants", yes)
    reads.clear()
    got = await all_records(p, org, "u1")
    assert reads and not any(reads), reads
    assert seen(got) == seen(want) | {"L3"}
    k0 = {i: (parent, arrow) for rows, _, _ in got for i, parent, arrow in rows}
    assert k0["k0"] == {i: (parent, arrow) for rows, _, _ in want for i, parent, arrow in rows}["k0"] == ("F", False), k0


# a connector kg holds current but does not serve (``kg.read.serves``): its keys are not vouched for, so every page reads the grants by node id
NOT_SERVED = {
    "shadow": "UPDATE kg.projection_state s SET mode = 'shadow' FROM kg.node_keys k WHERE k.org_id = s.org_id AND k.sk = s.app_sk AND s.org_id = $1 AND k.coll = 1 AND k.id = 'app2'",
    "stale_code_epoch": "UPDATE kg.projection_state s SET code_epoch = s.code_epoch - 1 FROM kg.node_keys k WHERE k.org_id = s.org_id AND k.sk = s.app_sk AND s.org_id = $1 AND k.coll = 1 AND k.id = 'app2'",
    "not_verified": "UPDATE kg.projection_state s SET verified_at = NULL FROM kg.node_keys k WHERE k.org_id = s.org_id AND k.sk = s.app_sk AND s.org_id = $1 AND k.coll = 1 AND k.id = 'app2'",
}


@pytest.mark.parametrize("state", list(NOT_SERVED))
async def test_a_gated_connector_kg_does_not_serve_reads_every_grant_by_node_id_and_lists_what_the_walk_lists(pg_db, reads, state) -> None:
    p, org = prov(pg_db), new_org()
    await kgg.world(p, pg_db, org, [kgg._perm("u1", "users", "F"), kgg._perm("grp1", "groups", "L1", "records", "GROUP"), kgg._perm("u1", "users", "A1"),
                                    kgg._perm("sg", "groups", "L5", "records", "GROUP")])
    await _admin(NOT_SERVED[state], org)
    reads.clear()
    got = await all_records(p, org, "u1")
    assert reads and not any(reads), reads
    assert got == await as_before(p, all_records(p, org, "u1"))
    assert {"F", "k0", "k1", "L1", "L5"} <= seen(got), got
    if state == "stale_code_epoch":      # kg's copy is of other code: the listing leaves the connector out, and a browse of it says it is being prepared, by any grant read
        assert "A1" not in seen(got)
        return
    reads.clear()
    browse = await kgg.pages(p, org, "u1")
    assert reads and not any(reads), reads
    with _switch("0"):
        walk, browse_walk = await all_records(p, org, "u1"), await kgg.pages(p, org, "u1")
    assert (got, browse) == (walk, browse_walk) and "A1" in seen(got)


async def test_a_connector_distrusted_while_another_ones_trust_is_read_takes_all_records_to_the_read_by_id(pg_db, monkeypatch, reads) -> None:
    """As on a page (``test_kh_kg_grants``): app2's trust needs a read (an old distrust, since re-verified), and while it runs the process comes to distrust app1. The listing's
    own question (``kh_pages_read_grants``, the page's read) says no, so every grant is read by node id, and All Records is what it was."""
    from app.services.graph_db.postgres.kg import net
    p, org = prov(pg_db), new_org()
    await kgg.world(p, pg_db, org, [kgg._perm("u1", "users", "L1"), kgg._perm("u1", "users", "A1")])
    monkeypatch.setattr(net, "DISTRUSTED", {(org, "app2"): "a verification before the last one"})
    real = uow.Tx.fetchrow

    async def fetchrow(self: Any, sql: str, *args: Any) -> Any:  # noqa: ANN401
        if "pipeshub:kg.net_state */" in sql and args[1:] == ("app2",):
            net.distrust(org, "app1")
        return await real(self, sql, *args)
    monkeypatch.setattr(uow.Tx, "fetchrow", fetchrow)
    calls: list[str] = []
    real_access = p.get_knowledge_hub_access_v3

    async def counted(*a: Any, **kw: Any) -> dict[str, Any]:  # noqa: ANN401
        calls.append(kw.get("user_key") or a[0])
        return await real_access(*a, **kw)
    p.get_knowledge_hub_access_v3 = counted
    reads.clear()
    got = await all_records(p, org, "u1")
    assert (org, "app1") in net.DISTRUSTED and reads and not any(reads), reads
    assert calls == ["u1"], calls      # read by id once, for every page: not by each page on its own
    assert {"L1", "A1"} <= seen(got) and got == await as_before(p, all_records(p, org, "u1")), got


async def test_all_records_grant_blocks_grow_by_a_fraction_of_a_block_per_grant(pg_db, monkeypatch) -> None:
    """The viewer holds 500, then 5,000 grants in another connector. The grant statements All Records runs (the read by id of every grant, kg's read of the grants, the check's
    reads per node): read by id, every grant costs its edge and its graph node (about 3 blocks a grant, once); read as kg keys, a range of members per principal, many to a block
    (about a fifth of a block a grant, once per connector page: two here)."""
    real = uow.Tx.fetch
    blocks: list[int] = []

    async def fetch(self: Any, sql: str, *args: Any) -> Any:  # noqa: ANN401
        if any(n in sql for n in GRANT_READS):
            plan = json.loads(await self._con.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, *args))
            blocks.append(plan[0]["Plan"]["Shared Hit Blocks"] + plan[0]["Plan"]["Shared Read Blocks"])
        return await real(self, sql, *args)
    p = prov(pg_db)
    by_size: dict[int, int] = {}
    for n in (500, 5000):
        org = new_org()
        await kgg.world(p, pg_db, org, [kgg._perm("u1", "users", "L1")])
        with ctx(org):
            await p.batch_upsert_nodes([{**kgg._rec(f"B{i:05d}", "app2"), "orgId": org} for i in range(n)], "records")
            await p.batch_create_edges([kgg._perm("u1", "users", f"B{i:05d}") for i in range(n)], "permission")
        await drain_org(pg_db, org)
        monkeypatch.setattr(uow.Tx, "fetch", fetch)
        blocks.clear()
        with ctx(org):
            resp = await service(p).get_nodes("u1", org, flattened=True, include=["counts"], limit=50, sort_by="name", sort_order="asc")
        monkeypatch.setattr(uow.Tx, "fetch", real)
        assert resp.success and resp.pagination.totalItems == n + 1 and len(resp.items) == 50, resp
        by_size[n] = sum(blocks)
    assert by_size[5000] - by_size[500] < 4500 // 2, by_size      # under a block for two more grants (read by id: about 6)


# ---- the permission corpus and the generated worlds ------------------------------------------------------------------------------------------------------------------------------

async def _root(layer: KhPgLayer, world, viewer: str) -> list[Any]:
    """Every page of All Records the viewer sees (``kh_layer.PAGE`` rows a page, by cursor): rows with their parent and arrow, the total and the counts."""
    ns = await layer._ns(world)
    svc, out, cursor = KnowledgeHubService(logging.getLogger("kh-all-records"), layer._provider, khl._Config()), [], None
    for _ in range(khl.MAX_PAGES):
        resp = await svc.get_nodes(user_id=ns.id(viewer), org_id=ns.org, flattened=True, include=["counts"], limit=khl.PAGE, sort_by="name", sort_order="asc", cursor=cursor)
        assert resp.success, resp.error
        out.append(([(ns.local(i.id), i.parentId and ns.local(i.parentId), i.hasChildren) for i in resp.items], resp.pagination.totalItems,
                    resp.counts.model_dump() if resp.counts else None))
        if not (cursor := resp.pagination.nextCursor):
            return out
    raise AssertionError("All Records did not end")


@pytest.fixture
def per_page(monkeypatch) -> list[bool]:
    """The oracle's listing (``kh_layer.listing``, which calls ``search_page`` with no access) given the gate alone, as the service gives it when the pages read their own
    grants: each page then reads them as kg keys when kg holds them current. The grant reads the pages took (True: kg keys) are recorded."""
    taken: list[bool] = []
    real_search, real_grants = khl.search_page, kh_listing.KhListingMixin._kh_grants

    async def search(provider, **kw: Any) -> Any:  # noqa: ANN401
        gate = await provider.get_knowledge_hub_access_context_v2(kw["user_key"], kw["org_id"])
        if kw.get("access") is None and await provider.kh_pages_read_grants(kw["user_key"], kw["org_id"], gate["gated_app_ids"]):
            kw["access"] = gate
        return await real_search(provider, **kw)

    async def grants(self, *a: Any, **kw: Any) -> kh_listing.KhGrants:  # noqa: ANN401
        g = await real_grants(self, *a, **kw)
        taken.append(g.by_sk)
        return g
    monkeypatch.setattr(khl, "search_page", search)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_grants", grants)
    return taken


async def _same(layer: KhPgLayer, worlds: list, tag: str) -> tuple[list[Any], list[str]]:
    """Each world held to the oracle with the pages reading their own grants, and its All Records, every viewer, read by kg keys and as before: the findings and the differences."""
    found: list[Any] = []
    diffs: list[str] = []
    for start in range(0, len(worlds), CHUNK):
        chunk = worlds[start:start + CHUNK]
        await layer.preload(chunk)
        for offset, world in enumerate(chunk):
            found += _bad(await check(layer, world, f"{tag}-{start + offset}"))
            with bind(OrgContext((await layer._ns(world)).org, None, "job", "job:test", "t")):
                for viewer in world.users():
                    new = await _root(layer, world, viewer)
                    old = await as_before(layer._provider, _root(layer, world, viewer))
                    if new != old:
                        diffs.append(f"{tag}-{start + offset} {viewer}: by kg keys {new} as before {old}")
        await layer.release(chunk)
    return found, diffs


@pytest.fixture
async def kg_layer(pg_provider, pg_db) -> Any:  # noqa: ANN401
    lay = KhPgLayer(pg_provider, pg_db, not_current=False)
    yield lay
    await lay.release(list(lay._prefix))


async def test_the_corpus_lists_in_all_records_what_the_oracle_admits_and_what_the_read_by_id_lists(kg_layer, per_page) -> None:
    found, diffs = await _same(kg_layer, [*dict.fromkeys(s.final_world() for s in load_all()), *KH_WORLDS], "golden")
    assert not found, _describe(found)
    assert not diffs, "\n".join(diffs[:20])
    assert sum(per_page) > len(per_page) // 2, f"{sum(per_page)} of {len(per_page)} pages read kg keys"      # one side never reading kg would prove nothing


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_list_in_all_records_what_the_oracle_admits_and_what_the_read_by_id_lists(kg_layer, per_page, kind, monkeypatch) -> None:
    from tests.permissions.gen import adversarial
    monkeypatch.setattr(adversarial, "RULES", [r for r in adversarial.RULES if r != "INHERITED"])      # as the conformance suite: the projection refuses it
    found, diffs = await _same(kg_layer, gen.generate(kind, gen.seeds()[0], WORLDS), kind)
    assert not found, _describe(found)
    assert not diffs, "\n".join(diffs[:20])
    assert sum(per_page) > len(per_page) // 2, f"{sum(per_page)} of {len(per_page)} pages read kg keys"
