"""A knowledge-hub page reads the viewer's grants from kg (``KhListingMixin._kh_kg_grants``: kg keys, from the user's closure and the members of each principal) when kg holds
every gated connector current, and by node id (``get_knowledge_hub_access_v3``) otherwise. Both reads give every page the same rows, totals and arrows: each rule of the read by id
is a case below, run on both. A change kg has not drained yet sends the page to the read by id, and a revoked grant is seen by neither."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.db import uow
from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres.kg.drainer import drain_org

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_read import flip
from .test_kh_listing_flat import prov

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ROOT = ["F", "L1", "L2", "L3", "L4", "L5"]


def _rec(key: str, app: str, **doc: Any) -> dict:  # noqa: ANN401
    return {"_key": key, "recordName": key, "connectorId": app, "recordType": "FILE", "origin": "CONNECTOR", "accessRule": "OPEN", **doc}


def _perm(frm: str, fc: str, to: str, tc: str = "records", t: str = "USER") -> dict:
    return edge(frm, fc, to, tc, type=t, role="READER")


async def world(p, db, org: str, grants: list[dict], *, deleted: tuple[str, ...] = (), groups: tuple[str, ...] = ()) -> None:
    """Two connectors the viewers are gated into. app1: a folder F (its children k0, k1 inherit from it) and records L1..L5 under the App; app2: A1, A2. Viewers: u1 (in the
    organisation, the group grp1 and the team tm1, and signed in to app1 as the source account src, a member of the group sg) and u2 (in the organisation only)."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": org, "name": "Org", "orgId": org}], "organizations")
        await p.batch_upsert_nodes([{"_key": a, "name": a, "type": "DRIVE", "orgId": org} for a in ("app1", "app2")], "apps")
        recs = [_rec("F", "app1", mimeType="application/vnd.folder"), *(_rec(k, "app1") for k in ("k0", "k1", *ROOT[1:])), _rec("A1", "app2"), _rec("A2", "app2")]
        await p.batch_upsert_nodes([{**r, "orgId": org, **({"isDeleted": True} if r["_key"] in deleted else {})} for r in recs], "records")
        await p.batch_create_edges([*(edge("app1", "apps", k, "records", relationshipType="PARENT_CHILD") for k in ROOT),
                                    *(edge("app2", "apps", k, "records", relationshipType="PARENT_CHILD") for k in ("A1", "A2")),
                                    *(edge("F", "records", k, "records", relationshipType="PARENT_CHILD") for k in ("k0", "k1"))], "nodeRelations")
        await p.batch_create_edges([edge(k, "records", "F", "records") for k in ("k0", "k1")], "inheritPermissions")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in ("u1", "u2", "src")], "users")
        await p.batch_upsert_nodes([{"_key": g, "name": g, "orgId": org, "connectorId": "app1"} for g in ("grp1", "sg")], "groups")
        await p.batch_upsert_nodes([{"_key": "tm1", "name": "tm1", "orgId": org}], "teams")
        if groups:      # record groups of app2, by these ids
            await p.batch_upsert_nodes([{"_key": g, "groupName": g, "connectorId": "app2", "orgId": org} for g in groups], "recordGroups")
        await p.batch_create_edges([edge(u, "users", org, "organizations") for u in ("u1", "u2")], "belongsTo")
        await p.batch_create_edges([edge(u, "users", a, "apps") for u in ("u1", "u2") for a in ("app1", "app2")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", "src", "users", connectorId="app1")], "authenticatedAs")
        await p.batch_create_edges([_perm("u1", "users", "grp1", "groups"), _perm("u1", "users", "tm1", "teams"), _perm("src", "users", "sg", "groups"), *grants], "permission")
    await flip(db, org)


async def pages(p, org: str, viewer: str) -> dict[str, Any]:
    """What every knowledge-hub page shows the viewer: the browse of each App and of F, and the flat listing of each App (rows, arrows, total)."""
    with ctx(org):
        gate = await p.get_knowledge_hub_access_context_v2(user_key=viewer, org_id=org)
        out: dict[str, Any] = {}
        for name, kw in [*((f"browse {a}", {"app_id": a, "flatten": False, "start_id": a, "start_type": "app"}) for a in ("app1", "app2")),
                         ("browse F", {"app_id": "", "flatten": False, "start_id": "F", "start_type": "folder"}),
                         *((f"list {a}", {"app_id": a, "flatten": True}) for a in ("app1", "app2"))]:
            page = await p.get_knowledge_hub_connector_page_v3(org_id=org, grantee_ids=gate["grantee_ids"], gated_app_ids=gate["gated_app_ids"], granted_ids=None, user_key=viewer,
                                                               limit=50, include_scope=True, **kw)
            out[name] = (sorted((r["id"], r["hasChildren"]) for r in page["rows"]), page["total"], page.get("counts"))
    return out


def seen(got: dict[str, Any]) -> set[str]:
    return {i for rows, _, _ in got.values() for i, _ in rows}


@pytest.fixture
def reads(monkeypatch) -> list[bool]:
    """Every grant read a page takes: True when it read kg keys."""
    taken: list[bool] = []
    real = kh_listing.KhListingMixin._kh_grants

    async def spy(self, *a, **kw) -> kh_listing.KhGrants:
        g = await real(self, *a, **kw)
        taken.append(g.by_sk)
        return g
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_grants", spy)
    return taken


async def both(p, org: str, viewer: str, reads: list[bool], monkeypatch) -> dict[str, Any]:
    """The pages by kg keys, then by node id (kg's read turned away), asserted equal."""
    reads.clear()
    by_sk = await pages(p, org, viewer)
    assert reads and all(reads), reads
    real = kh_listing.KhListingMixin._kh_kg_grants

    async def none(*_a, **_kw) -> None:
        return None
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_kg_grants", none)
    reads.clear()
    by_id = await pages(p, org, viewer)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_kg_grants", real)
    assert reads and not any(reads), reads
    assert by_sk == by_id
    return by_sk


ORG_ = "@org"      # the organisation's node, named per test

# rule -> (grants, deleted records, viewer, must see, must not see)
CASES: dict[str, tuple[list[tuple], tuple[str, ...], str, set[str], set[str]]] = {
    # a grant on a deleted folder opens nothing below it; the deleted node itself is listed nowhere
    "deleted_node": ([("u1", "users", "F"), ("u1", "users", "L1")], ("F",), "u1", {"L1"}, {"F", "k0", "k1"}),
    # an organisation's grant counts only of a sharing type
    "org_share_type": ([(ORG_, "organizations", "L2", "records", "ORG"), (ORG_, "organizations", "L3", "records", "USER")], (), "u2", {"L2"}, {"L3"}),
    # what the source account (and its group) holds counts inside its own connector only
    "source_account_in_its_connector": ([("src", "users", "L4"), ("sg", "groups", "L5", "records", "GROUP"), ("src", "users", "A1"), ("sg", "groups", "A2", "records", "GROUP")], (),
                                        "u1", {"L4", "L5"}, {"A1", "A2"}),
    # a user with no account at the source sees nothing the account holds
    "no_account_at_the_source": ([("src", "users", "L4"), ("sg", "groups", "L5", "records", "GROUP"), ("src", "users", "A1")], (), "u2", set(), {"L4", "L5", "A1"}),
    # any type of a grant by a user, group or team counts (the type rule is the organisation's)
    "grant_types": ([("grp1", "groups", "L1", "records", "GROUP"), ("grp1", "groups", "L2", "records", "DOMAIN"), ("u1", "users", "A1", "records", "ANYONE")], (), "u1",
                    {"L1", "L2", "A1"}, {"L3"}),
    # a group's grant on a folder opens what inherits from it; a team's grant opens its record
    "inherited_and_group": ([("grp1", "groups", "F", "records", "GROUP"), ("tm1", "teams", "L3", "records", "TEAM")], (), "u1", {"F", "k0", "k1", "L3"}, {"L1"}),
    # a grant on an App is the gate's, never a record's; the userAppRelation rows (@uar) open no record either
    "app_nodes_and_uar": ([("u1", "users", "app1", "apps"), ("grp1", "groups", "app2", "apps", "GROUP")], (), "u1", set(), {"F", "L1", "A1"}),
    # a membership of a group that is no node (written beside the group's delete) gives nothing
    "group_that_is_gone": ([("src", "users", "ghost", "groups"), ("ghost", "groups", "L1", "records", "GROUP"), ("u1", "users", "L2")], (), "u1", {"L2"}, {"L1"}),
}


@pytest.mark.parametrize("rule", list(CASES))
async def test_each_grant_rule_gives_the_same_pages_read_by_kg_keys_and_by_node_ids(pg_db, monkeypatch, reads, rule) -> None:
    grants, deleted, viewer, see, hide = CASES[rule]
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org, [_perm(*[org if x == ORG_ else x for x in g]) for g in grants], deleted=deleted)
    got = await both(p, org, viewer, reads, monkeypatch)
    assert see <= seen(got) and not (hide & seen(got)), (seen(got), got)


async def test_a_change_kg_has_not_drained_takes_the_read_by_node_id_and_is_seen(pg_db, reads) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org, [_perm("u1", "users", "L1")])
    with ctx(org):
        await p.batch_create_edges([_perm("u1", "users", "L2")], "permission")
    reads.clear()
    got = await pages(p, org, "u1")
    assert reads and not any(reads) and {"L1", "L2"} <= seen(got)
    await drain_org(pg_db, org)
    reads.clear()
    assert await pages(p, org, "u1") == got and reads and all(reads)


@pytest.mark.parametrize("drained", [False, True], ids=["pending", "drained"])
async def test_a_grant_revoked_before_the_request_is_not_seen(pg_db, reads, drained) -> None:
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org, [_perm("u1", "users", "L1"), _perm("u1", "users", "L2"), _perm("u1", "users", "A1")])
    with ctx(org):
        await p.batch_delete_edges([{"from_id": "u1", "from_collection": "users", "to_id": k, "to_collection": "records"} for k in ("L1", "A1")], "permission")
    if drained:
        await drain_org(pg_db, org)
    reads.clear()
    got = await pages(p, org, "u1")
    assert "L2" in seen(got) and not ({"L1", "A1"} & seen(got)) and reads and all(r is drained for r in reads), reads


async def test_a_class_whose_literal_id_another_collection_holds_too_is_the_walks_on_both_reads(pg_db, monkeypatch, reads) -> None:
    """F (a folder of app1, k0 and k1 inherit from it; u2 holds it) has the id of a record group of app2 that the viewer holds a grant on. A grant read by id carries no collection, the
    one read by key does: so kg vouches for no class with F as a literal (``kg.read.LITERALS``), and the access check decides k0 and k1 on both reads, by its own rule."""
    import os

    import asyncpg

    from app.services.graph_db.postgres.kg import read as kg_read
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org, [_perm("u1", "users", "F", "recordGroups"), _perm("u2", "users", "F"), _perm("u1", "users", "L1")], groups=("F",))      # u2's: F is a literal
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        cls = await con.fetchval("SELECT class_id FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'k0'", org)
    finally:
        await con.close()
    verdicts: list[object] = []
    real = kg_read.class_verdicts

    async def spy(*a, **kw) -> dict:
        out = await real(*a, **kw)
        if cls in out:
            verdicts.append(out[cls])
        return out
    monkeypatch.setattr(kg_read, "class_verdicts", spy)
    got = await both(p, org, "u1", reads, monkeypatch)
    assert verdicts and all(v is None for v in verdicts) and "L1" in seen(got), (verdicts, got)


@pytest.mark.parametrize("state", ["shadow", "not verified"])
async def test_a_gated_connector_kg_does_not_serve_takes_the_read_by_id(pg_db, monkeypatch, reads, state) -> None:
    """app2 is current in kg but kg does not answer access for it (``drainer.serves``: in shadow, or never verified): the page reads the grants by id."""
    import os

    import asyncpg
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org, [_perm("u1", "users", "L1"), _perm("u1", "users", "A1")])
    change = {"shadow": "mode = 'shadow'", "not verified": "verified_at = NULL"}[state]
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await con.execute(f"UPDATE kg.projection_state s SET {change} FROM kg.node_keys k WHERE k.org_id = s.org_id AND k.sk = s.app_sk AND s.org_id = $1 AND k.coll = 1 "
                          "AND k.id = 'app2'", org)
    finally:
        await con.close()
    reads.clear()
    got = await pages(p, org, "u1")
    assert reads and not any(reads) and {"L1", "A1"} <= seen(got), (reads, got)


def _on_kg_grants(monkeypatch, then) -> None:
    """Runs ``then(tx)`` in place of the first statement of each kg grant read (:data:`kh_listing.VIEWER_GRANTS`), and the statement after it."""
    real = uow.Tx.fetch

    async def fetch(self: Any, sql: str, *args: Any) -> Any:  # noqa: ANN401
        if "pipeshub:kg.viewer_grants */" in sql:
            await then(self)
        return await real(self, sql, *args)
    monkeypatch.setattr(uow.Tx, "fetch", fetch)


async def test_a_failed_kg_grant_read_is_the_read_by_ids_and_the_page_is_served(pg_db, monkeypatch, reads) -> None:
    """A statement of kg's grant read fails (here: a division by zero in its place). The page's snapshot is still usable: the grants are read by id, and every page is the one the
    read by id gives."""
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org, [_perm("u1", "users", "L1"), _perm("u1", "users", "A1")])
    real_kg = kh_listing.KhListingMixin._kh_kg_grants

    async def none(*_a, **_kw) -> None:
        return None
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_kg_grants", none)
    by_id = await pages(p, org, "u1")
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_kg_grants", real_kg)

    async def fail(tx: Any) -> None:  # noqa: ANN401
        await tx.fetchval("/* pipeshub:test.fail */ SELECT 1 / 0")
    _on_kg_grants(monkeypatch, fail)
    reads.clear()
    assert await pages(p, org, "u1") == by_id and reads and not any(reads), reads
    assert {"L1", "A1"} <= seen(by_id)


async def test_a_connector_distrusted_while_the_grants_are_read_takes_the_read_by_id(pg_db, monkeypatch, reads) -> None:
    """The process comes to distrust app1 while a page reads the viewer's grants from kg: the grants it read are not used; the page reads them by id."""
    from app.services.graph_db.postgres.kg import net
    p, org = prov(pg_db), new_org()
    await world(p, pg_db, org, [_perm("u1", "users", "L1")])
    monkeypatch.setattr(net, "DISTRUSTED", {})

    async def distrust(_tx: Any) -> None:  # noqa: ANN401
        net.distrust(org, "app1")
    _on_kg_grants(monkeypatch, distrust)
    reads.clear()
    got = await pages(p, org, "u1")
    assert reads and not any(reads) and "L1" in seen(got), (reads, got)


async def test_the_grant_blocks_a_browse_reads_grow_by_a_fraction_of_a_block_per_grant(pg_db, monkeypatch) -> None:
    """The viewer holds 100, then 1,000 grants in another connector. The grant statements of a browse of app1 (the grants, and the grants read per node by the check): kg's read
    is a range of members per principal, packed many to a block; the read by id read the edge and the graph node of each grant (about 3 blocks a grant: 325 -> 3,054)."""
    real = uow.Tx.fetch
    blocks: list[int] = []
    named = ("pipeshub:graph.access_grants_all */", "pipeshub:kg.viewer_grants */", "pipeshub:graph.access_grants_of */")
    kg_reads: list[str] = []

    async def fetch(self: Any, sql: str, *args: Any) -> Any:  # noqa: ANN401
        if any(n in sql for n in named):
            plan = json.loads(await self._con.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, *args))
            blocks.append(plan[0]["Plan"]["Shared Hit Blocks"] + plan[0]["Plan"]["Shared Read Blocks"])
            if named[1] in sql:
                kg_reads.append(sql)
        return await real(self, sql, *args)
    p = prov(pg_db)
    by_size = {}
    for n in (100, 1000):
        org = new_org()
        await world(p, pg_db, org, [_perm("u1", "users", "L1")])
        with ctx(org):
            await p.batch_upsert_nodes([{**_rec(f"B{i:04d}", "app2"), "orgId": org} for i in range(n)], "records")
            await p.batch_create_edges([_perm("u1", "users", f"B{i:04d}") for i in range(n)], "permission")
        await drain_org(pg_db, org)
        monkeypatch.setattr(uow.Tx, "fetch", fetch)
        blocks.clear()
        kg_reads.clear()
        with ctx(org):
            gate = await p.get_knowledge_hub_access_context_v2(user_key="u1", org_id=org)
            page = await p.get_knowledge_hub_connector_page_v3(app_id="app1", org_id=org, grantee_ids=gate["grantee_ids"], gated_app_ids=gate["gated_app_ids"], granted_ids=None,
                                                               user_key="u1", limit=50, flatten=False, start_id="app1", start_type="app")
        monkeypatch.setattr(uow.Tx, "fetch", real)
        assert [r["id"] for r in page["rows"]] == ["L1"]
        assert kg_reads, n      # the grants were read from kg, and measured
        by_size[n] = sum(blocks)
    assert by_size[1000] - by_size[100] < 900 // 2, by_size      # less than a block for two more grants

