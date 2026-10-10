"""The knowledge hub scope listing (Neo4j): served from precomputed scopes, it returns exactly what the full query
returns, and it is used only while the ENABLE_KH_SCOPE_LISTING flag is on and the connector's stamp is fresh.

The graph is built to be eligible (no declarations, no hidden or multi-parent nodes) and to hold every hop kind
the stamp classifies: free chains, a gate, a nested gate, a RESTRICTED gate, a block with seeds below it, a chain
deeper than the walk's 50 hops, a null name, and a null-named seed. A second, small App has no null names.
Every listing is compared page by page, both directions, against the full query (KH_SCOPE_MODE=off).
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from neo4j import AsyncGraphDatabase

from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
from app.services.graph_db.neo4j import kh_scope

from .fixture_graph import (
    ORG,
    TS,
    USER_U,
    USER_V,
    _principals,
    app,
    ip,
    nr,
    perm,
    rec,
    rg,
    user_app,
)
from .loaders import load_into_neo4j

pytestmark = pytest.mark.integration

APP = "sc-app"
SMALL = "sc-small"
DEEP = 60
SORTS = [("name", "ASC"), ("name", "DESC"), ("updatedAt", "ASC"), ("updatedAt", "DESC"),
         ("createdAt", "ASC"), ("createdAt", "DESC")]


def _graph() -> tuple[list, list]:
    nodes, edges = _principals()
    group = {"group_type": "SHAREPOINT_SITE", "connector": "SHAREPOINT ONLINE", "connectorId": APP,
             "updatedAtTimestamp": TS}
    n = 0

    def r(node_id: str, name: str | None, app_id: str = APP, **props) -> dict:
        nonlocal n
        n += 1
        node = rec(node_id, name, connector_id=app_id, updatedAtTimestamp=TS + 1000 * (n % 7),
                   **props)
        node["props"]["createdAtTimestamp"] = TS + 10 * n
        return node

    nodes += [
        app(APP, "Scope App", connector="SHAREPOINT ONLINE", app_group="Microsoft"),
        # Free: inherits from the App.
        rg("g-open", "Open site", **group),
        r("f-docs", "docs", mimeType="text/directory"), r("r-a", "Alpha"), r("r-b", "beta"),
        r("r-c", "Gamma"), r("r-same-1", "same"), r("r-same-2", "same"), r("r-null", None),
        r("r-strict", "Strict but free", rule="STRICT"),
        # A gate (OPEN, does not inherit), with a nested gate below it.
        rg("g-gate", "Gated site", **group),
        r("r-g1", "Gated one"), r("r-g2", "gated two"),
        r("r-nested", "Nested gate"), r("r-nested-1", "Below the nested gate"),
        # A RESTRICTED gate that inherits.
        rg("g-restricted", "Restricted site", rule="RESTRICTED", **group),
        r("r-res-1", "In the restricted site"),
        # A block (RESTRICTED, no inheritance): only seeds below it are listed.
        rg("g-block", "Blocked site", rule="RESTRICTED", **group),
        r("r-seed", "Seed"), r("r-seed-1", "Below the seed"), r("r-seed-strict", "Strict below the seed",
                                                               rule="STRICT"),
        r("r-seed-null", None),
        # The small App: no null names anywhere.
        app(SMALL, "Small App", connector="SHAREPOINT ONLINE", app_group="Microsoft"),
        r("s-1", "small one", app_id=SMALL), r("s-2", "small two", app_id=SMALL),
    ]
    nodes += [r(f"deep-{i}", f"deep {i:02d}") for i in range(DEEP)]
    edges += [
        user_app(USER_U, APP), user_app(USER_V, APP), user_app(USER_U, SMALL), user_app(USER_V, SMALL),
        nr(APP, "g-open"), ip("g-open", APP),
        nr("g-open", "f-docs"), ip("f-docs", "g-open"),
        *[e for c in ("r-a", "r-b", "r-c", "r-same-1", "r-same-2", "r-null", "r-strict")
          for e in (nr("f-docs", c), ip(c, "f-docs"))],
        nr(APP, "g-gate"),
        *[e for c in ("r-g1", "r-g2", "r-nested") for e in (nr("g-gate", c),)],
        ip("r-g1", "g-gate"), ip("r-g2", "g-gate"),
        nr("r-nested", "r-nested-1"), ip("r-nested-1", "r-nested"),
        nr(APP, "g-restricted"), ip("g-restricted", APP),
        nr("g-restricted", "r-res-1"), ip("r-res-1", "g-restricted"),
        nr(APP, "g-block"),
        nr("g-block", "r-seed"), nr("r-seed", "r-seed-1"), ip("r-seed-1", "r-seed"),
        nr("r-seed", "r-seed-strict"), ip("r-seed-strict", "r-seed"),
        nr("g-block", "r-seed-null"),
        perm(USER_U, "g-gate"), perm(USER_U, "r-nested"), perm(USER_U, "g-restricted"),
        perm(USER_U, "r-seed"), perm(USER_U, "r-seed-null"),
        nr(SMALL, "s-1"), ip("s-1", SMALL), nr(SMALL, "s-2"), ip("s-2", SMALL),
    ]
    parent = "g-open"
    for i in range(DEEP):
        edges += [nr(parent, f"deep-{i}"), ip(f"deep-{i}", parent)]
        parent = f"deep-{i}"
    return nodes, edges


async def _cypher(settings: dict, query: str, **params: Any) -> list[dict]:  # noqa: ANN401
    driver = AsyncGraphDatabase.driver(settings["uri"], auth=(settings["username"], settings["password"]))
    try:
        async with driver.session(database=settings["database"]) as session:
            return [r.data() async for r in await session.run(query, **params)]
    finally:
        await driver.close()


@pytest.fixture(scope="module")
async def scope_graph(neo4j_provider, neo4j_settings) -> dict:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await neo4j_provider.stamp_kh_listing_state(batch_size=7)
    neo4j_provider._kh_state_ready = True
    stamps = {a: await neo4j_provider.kh_scope_stamp(a) for a in (APP, SMALL)}
    return {"stamps": stamps}


@pytest.fixture
def scope_counter(monkeypatch) -> dict:
    """How many requests the scope path served (``page_payload`` returning a page)."""
    seen = {"served": 0}
    original = kh_scope.page_payload

    async def counting(*args: Any, **kwargs: Any) -> dict:  # noqa: ANN401
        page = await original(*args, **kwargs)
        seen["served"] += 1
        return page

    monkeypatch.setattr(kh_scope, "page_payload", counting)
    return seen


async def _page(provider, user: str, app_id: str = APP, **overrides: Any) -> dict:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(user, ORG)
    kwargs = {
        "app_id": app_id, "org_id": ORG, "grantee_ids": access["grantee_ids"],
        "gated_app_ids": access["gated_app_ids"], "grants_by_connector": access["by_connector"],
        "limit": 50, "flatten": True, "sort_field": "name", "sort_dir": "ASC", "include_total": True,
    }
    kwargs.update(overrides)
    return await provider.get_knowledge_hub_connector_page_v3(**kwargs)


def _sig(page: dict) -> tuple:
    rows = [(r["id"], r["sortKey"], type(r["sortKey"]).__name__, r["nullRank"], r["nodeType"], r.get("parentId"))
            for r in page["rows"]]
    return rows, page["hasMore"], page["total"], page.get("counts")


def _boundary(row: dict) -> dict:
    return {"id": row["id"], "sortKey": row["sortKey"], "nullRank": row["nullRank"]}


async def _walk(provider, user: str, app_id: str, limit: int, **overrides: Any) -> list[tuple]:  # noqa: ANN401
    """Forward to the end, then back from each page's first row; every page's signature."""
    forward, after = [], None
    for n in range(100):
        page = await _page(provider, user, app_id, limit=limit, after=after, include_total=n == 0, **overrides)
        forward.append(page)
        if not page["hasMore"] or not page["rows"]:
            break
        after = _boundary(page["rows"][-1])
    back = [await _page(provider, user, app_id, limit=limit, after=_boundary(page["rows"][0]),
                        direction="prev", include_total=False, **overrides)
            for page in forward[1:] if page["rows"]]
    return [_sig(p) for p in forward] + [("back",)] + [_sig(p) for p in back]


async def _pair(provider, monkeypatch, user: str, app_id: str, limit: int, **overrides: Any) -> tuple:  # noqa: ANN401
    monkeypatch.setenv("KH_SCOPE_MODE", "on")
    on = await _walk(provider, user, app_id, limit, **overrides)
    monkeypatch.setenv("KH_SCOPE_MODE", "off")
    off = await _walk(provider, user, app_id, limit, **overrides)
    return on, off


async def test_both_apps_are_stamped_eligible(scope_graph) -> None:
    for a in (APP, SMALL):
        stamp = scope_graph["stamps"][a]
        assert stamp["stamped"] and stamp["eligible"], stamp


@pytest.mark.parametrize("user", [USER_U, USER_V])
@pytest.mark.parametrize("sort_field,sort_dir", SORTS)
@pytest.mark.parametrize("limit", [1, 4, 500])
async def test_scope_listing_equals_the_full_query(
    scope_graph, neo4j_provider, monkeypatch, scope_counter, user, sort_field, sort_dir, limit,
) -> None:
    on, off = await _pair(neo4j_provider, monkeypatch, user, APP, limit, sort_field=sort_field, sort_dir=sort_dir)
    assert on == off
    # Served by scopes, except where the null names send a previous page to the full query.
    assert scope_counter["served"] > 0


async def test_the_walk_stops_at_fifty_hops(scope_graph, neo4j_provider, monkeypatch) -> None:
    monkeypatch.setenv("KH_SCOPE_MODE", "on")
    rows = [r for p in await _walk(neo4j_provider, USER_V, APP, 500) if p != ("back",) for r in p[0]]
    deep = sorted(r[0] for r in rows if r[0].startswith("deep-"))
    # The App is hop 0, the site hop 1: deep-0 is hop 2, so hops 2..50 are deep-0..deep-48.
    assert deep == sorted(f"deep-{i}" for i in range(49))


async def test_a_null_named_seed_is_listed_and_counted_once(scope_graph, neo4j_provider, monkeypatch) -> None:
    monkeypatch.setenv("KH_SCOPE_MODE", "on")
    pages = [p for p in await _walk(neo4j_provider, USER_U, APP, 3) if p != ("back",)]
    ids = [r[0] for p in pages[: next(i for i, p in enumerate(pages) if not p[1]) + 1] for r in p[0]]
    assert "r-seed-null" in ids
    assert len(ids) == len(set(ids)) == pages[0][2]


async def test_previous_page_from_another_apps_null_bucket(scope_graph, neo4j_provider, monkeypatch,
                                                           scope_counter) -> None:
    """All records put the cursor among another App's null names; the small App has none, and going back must
    give its last named rows, as the full query does (it used to give none)."""
    boundary = {"id": "zzzz", "sortKey": None, "nullRank": 1}
    monkeypatch.setenv("KH_SCOPE_MODE", "on")
    on = await _page(neo4j_provider, USER_V, SMALL, after=boundary, direction="prev", include_total=False)
    monkeypatch.setenv("KH_SCOPE_MODE", "off")
    off = await _page(neo4j_provider, USER_V, SMALL, after=boundary, direction="prev", include_total=False)
    assert [r["id"] for r in on["rows"]] == [r["id"] for r in off["rows"]]
    assert {r["id"] for r in on["rows"]} == {"s-1", "s-2"}
    assert scope_counter["served"] == 1


async def test_the_labs_flag_decides(scope_graph, neo4j_provider, monkeypatch, scope_counter) -> None:
    monkeypatch.delenv("KH_SCOPE_MODE", raising=False)
    await _page(neo4j_provider, USER_U, APP)
    assert scope_counter["served"] == 0  # the flag defaults to off

    class FlagOn:
        def __init__(self, inner) -> None:
            self.inner = inner

        async def get_config(self, key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            if key == PLATFORM_SETTINGS_KEY:
                return {"featureFlags": {"ENABLE_KH_SCOPE_LISTING": True}}
            return await self.inner.get_config(key, *args, **kwargs)

    monkeypatch.setattr(neo4j_provider, "config_service", FlagOn(neo4j_provider.config_service))
    await _page(neo4j_provider, USER_U, APP)
    assert scope_counter["served"] == 1
    monkeypatch.setenv("KH_SCOPE_MODE", "off")  # the operators' kill switch wins over the flag
    await _page(neo4j_provider, USER_U, APP)
    assert scope_counter["served"] == 1


async def test_a_sync_takes_the_app_off_scopes_until_it_is_restamped(
    scope_graph, neo4j_provider, neo4j_settings, monkeypatch, scope_counter,
) -> None:
    monkeypatch.setenv("KH_SCOPE_MODE", "on")
    generation = await neo4j_provider.kh_scope_mark_stale(SMALL)
    await _page(neo4j_provider, USER_V, SMALL)
    assert scope_counter["served"] == 0
    # A write outside the sync while it runs keeps the sync's generation, so the sync can still end its mark.
    await neo4j_provider.kh_scope_mark_changed(SMALL)
    meta = (await _cypher(neo4j_settings, "MATCH (m:KhScopeMeta {connectorId: $c}) RETURN m.generation AS g, "
                                          "m.syncing AS s", c=SMALL))[0]
    assert meta == {"g": generation, "s": True}
    # A replaced sync ends only its own mark.
    await neo4j_provider.kh_scope_sync_ended(SMALL, generation - 1)
    assert (await neo4j_provider.kh_scope_stamp(SMALL))["reason"] == "a sync is running"
    await neo4j_provider.kh_scope_sync_ended(SMALL, generation)
    assert (await neo4j_provider.kh_scope_stamp(SMALL))["stamped"]
    await _page(neo4j_provider, USER_V, SMALL)
    assert scope_counter["served"] == 1


async def test_a_record_deleted_after_the_stamp_is_not_listed(scope_graph, neo4j_provider, neo4j_settings,
                                                             monkeypatch) -> None:
    await _cypher(neo4j_settings, "MATCH (n:Record {id: 'r-c'}) SET n.isDeleted = true, n:KhDeleted")
    try:
        # Before its writer marks the App changed, the row is already left out; the total waits for the restamp.
        monkeypatch.setenv("KH_SCOPE_MODE", "on")
        page = await _page(neo4j_provider, USER_V, APP, limit=500)
        assert "r-c" not in [r["id"] for r in page["rows"]]
        # Marked changed, the full query answers; restamped, the scope listing agrees with it again.
        await neo4j_provider.kh_scope_mark_changed(APP)
        on, off = await _pair(neo4j_provider, monkeypatch, USER_V, APP, 500)
        assert on == off
        assert (await neo4j_provider.kh_scope_stamp(APP))["stamped"]
        on, off = await _pair(neo4j_provider, monkeypatch, USER_V, APP, 500)
        assert on == off
        assert "r-c" not in [r[0] for p in on if p != ("back",) for r in p[0]]
    finally:
        await _cypher(neo4j_settings, "MATCH (n:Record {id: 'r-c'}) SET n.isDeleted = false REMOVE n:KhDeleted")
        await neo4j_provider.kh_scope_mark_changed(APP)
        await neo4j_provider.kh_scope_stamp(APP)


async def test_a_hard_delete_marks_the_app_changed(scope_graph, neo4j_provider, neo4j_settings) -> None:
    await _cypher(neo4j_settings, """
        CREATE (n:Record {id: 's-gone', recordName: 'gone', connectorId: $c, orgId: $o, accessRule: 'OPEN'})
    """, c=SMALL, o=ORG)
    assert await neo4j_provider.delete_records_and_relations("s-gone", hard_delete=True)
    meta = (await _cypher(neo4j_settings, "MATCH (m:KhScopeMeta {connectorId: $c}) RETURN m.fresh AS f",
                          c=SMALL))[0]
    assert meta["f"] is False
    assert (await neo4j_provider.kh_scope_stamp(SMALL))["stamped"]


async def test_ineligible_shapes(scope_graph, neo4j_provider, neo4j_settings) -> None:
    # A child of another connector under the App: the walk lists it, the stamp cannot.
    await _cypher(neo4j_settings, """
        MATCH (a:App {id: $c})
        CREATE (a)-[:NODE_RELATION {relationshipType: 'PARENT_CHILD'}]->
               (:Record {id: 's-foreign', recordName: 'foreign', connectorId: 'other', orgId: $o})
    """, c=SMALL, o=ORG)
    try:
        stamp = await neo4j_provider.kh_scope_stamp(SMALL)
        assert stamp == {**stamp, "eligible": False, "reason": "a child belongs to another connector"}
    finally:
        await _cypher(neo4j_settings, "MATCH (n:Record {id: 's-foreign'}) DETACH DELETE n")
    # A timestamp stored as a string: that sort goes to the full query, the others stay on scopes.
    await _cypher(neo4j_settings, "MATCH (n:Record {id: 's-1'}) SET n.updatedAtTimestamp = 'yesterday'")
    try:
        assert (await neo4j_provider.kh_scope_stamp(SMALL))["eligible"]
        meta = (await _cypher(neo4j_settings, "MATCH (m:KhScopeMeta {connectorId: $c}) RETURN m.nullUpdated AS u",
                              c=SMALL))[0]
        assert meta["u"] == 1
    finally:
        await _cypher(neo4j_settings, "MATCH (n:Record {id: 's-1'}) SET n.updatedAtTimestamp = $t", t=TS)
        await neo4j_provider.kh_scope_stamp(SMALL)


async def test_one_meta_per_connector(scope_graph, neo4j_provider, neo4j_settings) -> None:
    # An older build's plain index and a duplicate left by concurrent first stamps.
    await _cypher(neo4j_settings, f"DROP CONSTRAINT {kh_scope.META_CONSTRAINT} IF EXISTS")
    await _cypher(neo4j_settings, "CREATE INDEX kh_scope_meta_conn IF NOT EXISTS FOR (m:KhScopeMeta) ON (m.connectorId)")
    await _cypher(neo4j_settings, "CREATE (:KhScopeMeta {connectorId: $c, fresh: false})", c=SMALL)
    assert (await neo4j_provider.kh_scope_stamp(SMALL))["stamped"]
    metas = await _cypher(neo4j_settings, "MATCH (m:KhScopeMeta {connectorId: $c}) RETURN count(m) AS n", c=SMALL)
    assert metas[0]["n"] == 1
    constraints = await _cypher(neo4j_settings, "SHOW CONSTRAINTS YIELD name WHERE name = $n RETURN name",
                                n=kh_scope.META_CONSTRAINT)
    assert constraints


async def test_an_old_stamp_version_is_not_read(scope_graph, neo4j_provider, neo4j_settings, monkeypatch,
                                                scope_counter) -> None:
    monkeypatch.setenv("KH_SCOPE_MODE", "on")
    await _cypher(neo4j_settings, "MATCH (m:KhScopeMeta {connectorId: $c}) SET m.version = 1", c=SMALL)
    await _page(neo4j_provider, USER_V, SMALL)
    assert scope_counter["served"] == 0
    assert SMALL in await kh_scope.stale_connectors(neo4j_provider.client)
    assert (await neo4j_provider.kh_scope_stamp(SMALL))["stamped"]


async def test_a_deleted_connector_leaves_no_scopes(scope_graph, neo4j_provider, neo4j_settings) -> None:
    await neo4j_provider.kh_scope_forget(SMALL)
    left = await _cypher(neo4j_settings, """
        OPTIONAL MATCH (m:KhScopeMeta {connectorId: $c}) WITH count(m) AS metas
        OPTIONAL MATCH (k:KhScope {connectorId: $c}) RETURN metas, count(k) AS scopes
    """, c=SMALL)
    assert left == [{"metas": 0, "scopes": 0}]
    assert (await neo4j_provider.kh_scope_stamp(SMALL))["stamped"]


def test_no_mode_override_leaks_between_tests() -> None:
    assert os.environ.get("KH_SCOPE_MODE") in (None, "")
