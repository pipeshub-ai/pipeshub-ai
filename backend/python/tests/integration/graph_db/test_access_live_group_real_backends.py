"""A membership whose group no longer exists grants nothing, on a real Neo4j and a real ArangoDB.

The user u is a member of a group, role or team that exists and of one whose node is gone; so is the source
account u signed in to the connector as. Each of them grants a record, a record group and (a team) a collection.
Only what an existing group grants counts, on every read of a viewer's grants: ``get_knowledge_hub_access_v3``,
``check_access``, the role of a node (``get_knowledge_hub_context_permissions``) and the entity search's scope
(``get_entity_access_context``). On Neo4j a relationship never outlives its node; ArangoDB keeps an edge whose
vertex is removed, so the gone group's node is removed there alone. Same cases as
``postgres/test_access_live_group.py``.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore
from tests.integration.graph_permissions.fixture_graph import (
    DRIVE,
    KB,
    ORG,
    TS,
    _node,
    app,
    authenticated_as,
    perm,
    rec,
    rg,
    user_app,
)
from tests.integration.graph_permissions.loaders import EDGE_TARGETS, NODE_TARGETS
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "access_live_group_it"
logger = logging.getLogger("access-live-group-it")

APP = "lg-app"
HOLDERS = ("own-live", "own-gone", "src-live", "src-gone")
LIVE = ("own-live", "src-live")
GRANT_TYPE = {"Group": "GROUP", "Role": "ROLE", "Teams": "TEAM"}


def _graph(kind: str) -> tuple[list, list]:
    nodes = [
        _node("User", "u", email="u@example.com", fullName="U", userId="u-uid"),
        _node("User", "src", email="src@example.com", fullName="Src"),
        app(APP, "Drive", connector=DRIVE, app_group="G"),
    ]
    edges = [user_app("u", APP), authenticated_as("u", "src", APP)]
    # external collaborators of the App, each kept there only by what its one membership grants (the reaper's question), and one by a grant of its own
    for h in ("own-live", "own-gone", "direct"):
        nodes.append(_node("User", f"ext-{h}", email=f"ext-{h}@example.com", fullName=f"Ext {h}"))
        edges += [perm(f"ext-{h}", h, role="READER") if h != "direct" else perm(f"ext-{h}", "r-own-live", role="READER"),
                  {"type": "USER_APP_RELATION", "from": f"ext-{h}", "to": APP, "props": {"syncState": "COMPLETED", "lastSyncUpdate": TS, "isExternalUser": True}}]
    extra = {"Role": {"externalRoleId": "ext", "connectorName": DRIVE, "connectorId": APP, "createdAtTimestamp": TS}}
    for h in HOLDERS:
        nodes += [_node(kind, h, name=h, **extra.get(kind, {})),
                  rec(f"r-{h}", f"r {h}", connector_id=APP),
                  rg(f"g-{h}", f"g {h}", group_type="DRIVE", connector=DRIVE, connectorId=APP)]
        edges += [perm("u" if h.startswith("own") else "src", h, role="OWNER"),
                  perm(h, f"r-{h}", grant_type=GRANT_TYPE[kind], role="WRITER"),
                  perm(h, f"g-{h}", grant_type=GRANT_TYPE[kind], role="WRITER")]
        if kind == "Teams" and h.startswith("own"):
            nodes.append(app(f"k-{h}", f"kb {h}", connector=KB, app_group="Local"))
            edges.append(perm(h, f"k-{h}", grant_type="TEAM", role="WRITER"))
    return nodes, edges


class _World:
    """The seeded graph; every id and the org carry a per-run suffix, so runs and other tests do not meet."""

    def __init__(self, graph: Any, run: str, kind: str) -> None:  # noqa: ANN401
        self.graph, self.run, self.kind, self.org = graph, run, kind, f"{ORG}-{run}"
        self.nodes, self.edges = _graph(kind)

    def sid(self, node_id: str) -> str:
        return f"{node_id}-{self.run}"

    def plain(self, node_id: str) -> str:
        return node_id.removesuffix(f"-{self.run}")

    async def seed(self) -> None:
        kind_of = {n["id"]: n["kind"] for n in self.nodes}
        for kind, collection in NODE_TARGETS.items():
            rows = [{**n["props"], "id": self.sid(n["id"]), **({"orgId": self.org} if "orgId" in n["props"] else {}),
                     **({"userId": self.sid(n["props"]["userId"])} if n["props"].get("userId") else {}),
                     **({"connectorId": self.sid(n["props"]["connectorId"])} if n["props"].get("connectorId") else {})}
                    for n in self.nodes if n["kind"] == kind]
            if rows:
                assert await self.graph.batch_upsert_nodes(rows, collection=collection)
        for edge_type, collection in EDGE_TARGETS.items():
            rows = [{"from_id": self.sid(e["from"]), "from_collection": NODE_TARGETS[kind_of[e["from"]]],
                     "to_id": self.sid(e["to"]), "to_collection": NODE_TARGETS[kind_of[e["to"]]],
                     **e["props"], **({"connectorId": self.sid(e["props"]["connectorId"])}
                                      if e["props"].get("connectorId") else {})}
                    for e in self.edges if e["type"] == edge_type]
            if rows:
                assert await self.graph.batch_create_edges(rows, collection=collection)
        # The gone groups' nodes are removed and their edges stay where the store keeps them.
        gone = [self.sid(h) for h in HOLDERS if h not in LIVE]
        if hasattr(self.graph, "client"):
            await self.graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": gone})
        else:
            coll = NODE_TARGETS[self.kind]
            await self.graph.http_client.execute_aql(f"FOR d IN {coll} FILTER d._key IN @ids REMOVE d IN {coll}", {"ids": gone})

    async def remove(self) -> None:
        ids = [self.sid(n["id"]) for n in self.nodes]
        if hasattr(self.graph, "client"):
            await self.graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
            return
        for collection in NODE_TARGETS.values():
            await self.graph.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids})
        for collection in EDGE_TARGETS.values():
            await self.graph.http_client.execute_aql(
                f"FOR e IN {collection} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
                f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {collection}", {"ids": ids})


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    try:
        provider = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                          else connect_arango(logger, ARANGO_DB))
    except Exception as exc:
        backend_unavailable(request.param, exc)
    # The reaper removes in two passes, the second a horizon after the first (common.grant_locks): a millisecond here, so a test's reaper sweeps
    # what the world's setup pass marked.
    monkeypatch.setattr(type(provider), "_reap_horizon_ms", lambda *_: 1)
    yield provider
    disconnect = getattr(provider, "disconnect", None)
    if disconnect is not None:
        await disconnect()


@pytest.fixture(params=["Group", "Role", "Teams"])
async def world(request: pytest.FixtureRequest, graph: Any) -> AsyncIterator[_World]:  # noqa: ANN401
    async with contextlib.AsyncExitStack() as cleanup:
        w = _World(graph, uuid.uuid4().hex[:10], request.param)
        cleanup.push_async_callback(w.remove)
        await w.seed()
        await _first_pass(w)
        yield w


async def _first_pass(w: _World) -> None:
    """The reaper's first pass over the world's App: it marks the flagged memberships no grant keeps (ext-own-gone's) and removes nothing; the
    horizon (a millisecond in these tests) then passes, so the next pass, the one a test races, sweeps them."""
    await w.graph.reap_stale_external_app_relations(w.sid(APP))
    await asyncio.sleep(0.01)


async def reap_twice(g: Any, app_id: str) -> None:  # noqa: ANN401
    """Both passes of the reaper over an App, the horizon (a millisecond in these tests) between them: what one pass did before the two-pass
    reaper."""
    await g.reap_stale_external_app_relations(app_id)
    await asyncio.sleep(0.01)
    await g.reap_stale_external_app_relations(app_id)


async def test_a_membership_whose_group_no_longer_exists_grants_nothing(world: _World) -> None:
    w, g = world, world.graph
    granted = {f"{p}-{h}" for p in ("r", "g") for h in LIVE}
    access = await g.get_knowledge_hub_access_v3(w.sid("u"), w.org)
    assert {w.plain(i) for i in access["by_connector"][w.sid(APP)]} == granted
    checked = await g.check_access(w.sid("u"), w.org, node_ids=[w.sid(f"{p}-{h}") for p in ("r", "g") for h in HOLDERS])
    assert {w.plain(i) for i in checked.node_ids} == granted

    nodes = [(f"{p}-{h}", t) for p, t in (("r", "record"), ("g", "recordGroup")) for h in HOLDERS]
    if w.kind == "Teams":
        nodes += [(f"k-{h}", "app") for h in ("own-live", "own-gone")]
    roles = {n: (await g.get_knowledge_hub_context_permissions(w.sid("u"), w.org, w.sid(n), parent_type=t))["role"]
             for n, t in nodes}
    # No path at all reads as READER; the grants are WRITER.
    assert roles == {n: "WRITER" if n.endswith(LIVE) else "READER" for n, _ in nodes}

    scope = await g.get_entity_access_context(w.sid("u-uid"), w.org)
    assert {w.plain(i) for i in scope["record_group_ids"]} == {f"g-{h}" for h in LIVE}


@pytest.fixture
async def teams(graph: Any) -> AsyncIterator[_World]:  # noqa: ANN401
    """The Teams world, with what a team opens beside records: an App it holds a user-app relation to (``a-``) and an agent template it is shared (``t-``)."""
    async with contextlib.AsyncExitStack() as cleanup:
        w = _World(graph, uuid.uuid4().hex[:10], "Teams")
        for h in ("own-live", "own-gone"):
            w.nodes.append(app(f"a-{h}", f"app {h}", connector=DRIVE, app_group="G"))
            w.edges.append({"type": "USER_APP_RELATION", "from": h, "to": f"a-{h}", "props": {"syncState": "COMPLETED", "lastSyncUpdate": TS}})
        cleanup.push_async_callback(w.remove)
        templates = [w.sid(f"t-{h}") for h in ("own-live", "own-gone")]

        async def drop_templates() -> None:
            if hasattr(graph, "client"):
                await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": templates})
            else:
                await graph.http_client.execute_aql("FOR d IN agentTemplates FILTER d._key IN @ids REMOVE d IN agentTemplates", {"ids": templates})
                await graph.http_client.execute_aql("FOR e IN permission FILTER PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN permission", {"ids": templates})
        cleanup.push_async_callback(drop_templates)
        await w.seed()      # the teams first: Neo4j writes a relationship only between nodes it holds
        assert await graph.batch_upsert_nodes([{"id": t, "name": t, "description": "d", "startMessage": "s", "systemPrompt": "p", "isDeleted": False, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}
                                               for t in templates], collection="agentTemplates")
        assert await graph.batch_create_edges([{"from_id": w.sid(h), "from_collection": "teams", "to_id": w.sid(f"t-{h}"), "to_collection": "agentTemplates",
                                                "type": "TEAM", "role": "READER", "createdAtTimestamp": TS} for h in ("own-live", "own-gone")], collection="permission")
        # A write answers True when Neo4j matched no endpoint: the edges are read back. Neo4j holds no edge of a gone node; ArangoDB keeps it.
        if hasattr(graph, "client"):
            rows = await graph.client.execute_query("MATCH (t:Teams)-[:PERMISSION]->(a:AgentTemplate) WHERE a.id IN $ids RETURN t.id AS id", parameters={"ids": templates})
        else:
            rows = await graph.http_client.execute_aql("FOR e IN permission FILTER e._to IN @ids RETURN {id: PARSE_IDENTIFIER(e._from).key}",
                                                       {"ids": [f"agentTemplates/{t}" for t in templates]})
        assert sorted(w.plain(r["id"]) for r in rows) == (["own-live"] if hasattr(graph, "client") else ["own-gone", "own-live"])
        yield w


async def test_a_team_that_no_longer_exists_opens_no_collection_app_or_agent_template(teams: _World) -> None:
    """The reads of what a user's teams open besides records: the role on a collection and the collections listed, the Apps shared through a team, and the agent templates."""
    w, g = teams, teams.graph
    u, live, gone = w.sid("u"), "own-live", "own-gone"
    kbs, _, _ = await g.list_user_knowledge_bases(u, w.org, 0, 100)
    app_ids = {w.plain(a) for a in await g.get_user_app_ids(u)}
    got = {
        "kb role": {h: await g.get_user_kb_permission(w.sid(f"k-{h}"), u) for h in (live, gone)},
        "kbs listed": {w.plain(k["id"]) for k in kbs},
        "kbs shared": {w.plain(a) for a in await g.get_user_permission_app_ids(u, w.org)},
        "apps": {a for a in app_ids if a.startswith("a-")},
        "templates": {w.plain(t.get("_key") or t.get("id")) for t in await g.get_all_agent_templates(u)},
        "template": {h: await g.get_template(w.sid(f"t-{h}"), u) is not None for h in (live, gone)},
    }
    want = {"kb role": {live: "WRITER", gone: None}, "kbs listed": {f"k-{live}"}, "kbs shared": {f"k-{live}"}, "apps": {f"a-{live}"},
            "templates": {f"t-{live}"}, "template": {live: True, gone: False}}
    if hasattr(g, "_get_user_accessible_team_app_keys"):      # the connector listing's team Apps (ArangoDB's helper)
        got["team apps"] = {a for a in (w.plain(k) for k in await g._get_user_accessible_team_app_keys(w.sid("u-uid"))) if a.startswith("a-")}
        want["team apps"] = {f"a-{live}"}
    assert got == want


async def test_the_collaborator_reaper_keeps_only_whom_an_existing_group_still_grants(world: _World) -> None:
    """``reap_stale_external_app_relations`` keeps an external collaborator's gate edge while a grant still holds them there, the read of grants' rule
    (``get_knowledge_hub_access_v3``): a membership of a group that no longer exists holds no one."""
    w, g = world, world.graph
    await g.reap_stale_external_app_relations(w.sid(APP))
    if hasattr(g, "client"):
        rows = await g.client.execute_query("MATCH (u:User)-[:USER_APP_RELATION]->(:App {id: $app}) RETURN u.id AS id", parameters={"app": w.sid(APP)})
    else:
        rows = await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._to == @app RETURN {id: PARSE_IDENTIFIER(e._from).key}", {"app": f"apps/{w.sid(APP)}"})
    assert {w.plain(r["id"]) for r in rows} == {"u", "ext-own-live", "ext-direct"}


async def test_the_reaper_keeps_a_collaborator_granted_while_it_runs(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    """ext-own-gone is held by nothing when the reaper starts. A direct grant to it commits just before the statement that removes the
    reaper's edges runs: the removal decides on the grant as it is then, so the collaborator stays."""
    w, g = world, world.graph
    ext, rec_id = w.sid("ext-own-gone"), w.sid("r-own-live")
    if hasattr(g, "client"):
        real, name = g.client.execute_query, "client"

        async def grant() -> None:
            await real("MATCH (u:User {id: $u}), (r:Record {id: $r}) CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER', createdAtTimestamp: $ts}]->(r)",
                       parameters={"u": ext, "r": rec_id, "ts": TS})

        def removes(query: str, *_a: Any, **_k: Any) -> bool:  # noqa: ANN401
            return "isExternalUser" in query and "DELETE" in query
    else:
        real, name = g.http_client.execute_aql, "http_client"

        async def grant() -> None:
            await real("INSERT {_from: @u, _to: @r, type: 'USER', role: 'READER', createdAtTimestamp: @ts} IN permission",
                       {"u": f"users/{ext}", "r": f"records/{rec_id}", "ts": TS})

        def removes(query: str, *a: Any, **k: Any) -> bool:  # noqa: ANN401
            bind = (a[0] if a else k.get("bind_vars")) or {}
            return "REMOVE" in query and ("userAppRelation" in query or bind.get("@collection") == "userAppRelation")
    fired: list[bool] = []

    async def hooked(query: str, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        if not fired and removes(query, *a, **k):
            fired.append(True)
            await grant()
        return await real(query, *a, **k)
    monkeypatch.setattr(getattr(g, name), "execute_query" if name == "client" else "execute_aql", hooked)
    await g.reap_stale_external_app_relations(w.sid(APP))
    monkeypatch.undo()
    if hasattr(g, "client"):
        rows = await g.client.execute_query("MATCH (u:User)-[:USER_APP_RELATION]->(:App {id: $app}) RETURN u.id AS id", parameters={"app": w.sid(APP)})
    else:
        rows = await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._to == @app RETURN {id: PARSE_IDENTIFIER(e._from).key}", {"app": f"apps/{w.sid(APP)}"})
    assert fired and {w.plain(r["id"]) for r in rows} == {"u", "ext-own-live", "ext-direct", "ext-own-gone"}


async def test_the_reaper_keeps_a_collaborator_whose_grant_commits_while_it_decides(world: _World) -> None:
    """A writer grants ext-own-gone a record and ensures its membership in one transaction, still open while the reaper runs; the reaper's view cannot
    see that grant. The writer's touch of the edge conflicts with the removal (ArangoDB) or holds it until the writer commits (Neo4j), so the
    collaborator keeps the App once the grant is committed."""
    w, g = world, world.graph
    ext, rec_id = w.sid("ext-own-gone"), w.sid("r-own-live")
    tid = await g.begin_transaction(["users", "records", "apps"], ["apps", "organizations", "permission", "userAppRelation"])
    assert await g.batch_create_edges([{"from_id": ext, "from_collection": "users", "to_id": rec_id, "to_collection": "records", "type": "USER", "role": "READER",
                                        "createdAtTimestamp": TS}], collection="permission", transaction=tid)
    await g.ensure_app_membership(ext, "users", w.sid(APP), is_external=True, transaction=tid)
    await _reap_while(w, tid)      # the reaper decides, waits on the writer's lock or loses to it (and runs again) while the grant is uncommitted
    if hasattr(g, "client"):
        rows = await g.client.execute_query("MATCH (u:User)-[:USER_APP_RELATION]->(:App {id: $app}) RETURN u.id AS id", parameters={"app": w.sid(APP)})
    else:
        rows = await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._to == @app RETURN {id: PARSE_IDENTIFIER(e._from).key}", {"app": f"apps/{w.sid(APP)}"})
    assert {w.plain(r["id"]) for r in rows} == {"u", "ext-own-live", "ext-direct", "ext-own-gone"}


async def _edges_to_app(w: _World) -> dict[str, bool]:
    """Each principal holding a membership of the App, by its plain id, with the membership's external flag."""
    g = w.graph
    if hasattr(g, "client"):
        rows = await g.client.execute_query("MATCH (u)-[r:USER_APP_RELATION]->(:App {id: $app}) RETURN u.id AS id, r.isExternalUser AS ext", parameters={"app": w.sid(APP)})
    else:
        rows = await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._to == @app RETURN {id: PARSE_IDENTIFIER(e._from).key, ext: e.isExternalUser}", {"app": f"apps/{w.sid(APP)}"})
    return {w.plain(r["id"]): bool(r["ext"]) for r in rows}


REAPER_MARK = "collect([principal, uar]) AS flagged"      # a piece of the Neo4j reaper's statement, to find its blocked transaction


async def _until_it_meets(g: Any, task: asyncio.Task, *, marker: str | None = None) -> None:  # noqa: ANN401
    """Returns once ``task`` has met the open writer: it finished (ArangoDB answers a conflict at once, and a reaper the writer does not hold
    finishes) or Neo4j shows a transaction blocked on a lock (one running a statement with ``marker`` in it, when given). Fails if neither
    happens in 10 s; a fixed sleep let a slow start skip the overlap."""
    for _ in range(200):
        if task.done():
            return
        if hasattr(g, "client"):
            rows = await g.client.execute_query(
                "SHOW TRANSACTIONS YIELD status, currentQuery WHERE status STARTS WITH 'Blocked' AND ($m IS NULL OR currentQuery CONTAINS $m) "
                "RETURN count(*) AS n", parameters={"m": marker})
            if rows and rows[0]["n"]:
                return
        await asyncio.sleep(0.05)
    raise AssertionError("it neither finished nor waited on a lock")


async def _reap_while(w: _World, writer_tid: str, *, reaper_in_transaction: bool = False) -> list[str]:
    """Run the reaper while the writer's transaction is open, then commit the writer. A reaper that loses to the writer raises a write conflict (its
    transaction, if it had one, is rolled back) and runs again once the writer has committed, as its caller does; one that returns has its
    transaction committed. It never returns after a conflict, and any other failure is raised. Returns the reaper transactions a conflict rolled back."""
    g = w.graph
    rolled_back: list[str] = []

    async def once() -> None:
        rid = await g.begin_transaction(["users", "records", "apps", "person"], ["apps", "organizations", "userAppRelation", "person"]) if reaper_in_transaction else None
        try:
            await g.reap_stale_external_app_relations(w.sid(APP), transaction=rid)
        except BaseException:
            if rid:
                await g.rollback_transaction(rid)
                rolled_back.append(rid)
            raise
        if rid:
            await g.commit_transaction(rid)
    reap = asyncio.create_task(once())
    try:
        await _until_it_meets(g, reap)
        await g.commit_transaction(writer_tid)
    except BaseException:      # the writer failed: nothing is left running, and the reaper's transaction is rolled back
        reap.cancel()
        with contextlib.suppress(BaseException):
            await reap
        with contextlib.suppress(Exception):
            await g.rollback_transaction(writer_tid)
        raise
    try:
        await reap
    except Exception as e:
        if not g.is_write_conflict(e):
            raise
        await once()      # the caller's retry, now that the writer has committed
    return rolled_back


@pytest.mark.parametrize("in_transaction", [False, True], ids=["alone", "in_transaction"])
async def test_the_reaper_in_its_caller_s_transaction_never_commits_after_a_conflict(world: _World, in_transaction: bool) -> None:
    """As connectors run it (inside the store's transaction): a writer grants ext-own-gone a record and ensures its membership while the reaper runs.
    The reaper either waits and sees the grant, or raises the conflict and is run again; its transaction commits only after a reaper that returned."""
    w, g = world, world.graph
    ext, rec_id = w.sid("ext-own-gone"), w.sid("r-own-live")
    tid = await g.begin_transaction(["users", "records", "apps"], ["apps", "organizations", "permission", "userAppRelation"])
    assert await g.batch_create_edges([{"from_id": ext, "from_collection": "users", "to_id": rec_id, "to_collection": "records", "type": "USER", "role": "READER",
                                        "createdAtTimestamp": TS}], collection="permission", transaction=tid)
    await g.ensure_app_membership(ext, "users", w.sid(APP), is_external=True, transaction=tid)
    rolled_back = await _reap_while(w, tid, reaper_in_transaction=in_transaction)
    assert set(await _edges_to_app(w)) == {"u", "ext-own-live", "ext-direct", "ext-own-gone"}
    if in_transaction and not hasattr(g, "client"):
        assert rolled_back, "ArangoDB answers the removal of the writer's locked edge with a conflict"
    for rid in rolled_back:      # a conflicted reaper's transaction is gone: it cannot be committed after the fact (each store says so its own way)
        committed = True
        try:
            await g.commit_transaction(rid)
        except Exception:
            committed = False
        assert not committed, "a reaper transaction rolled back after a conflict could still be committed"


async def test_the_reaper_keeps_a_membership_made_ordinary_while_it_runs(world: _World) -> None:
    """ext-own-gone's flagged membership is made an ordinary one (the user joined the App) in a transaction still open while the reaper runs: the
    reaper decides on the flag as it is committed, so the ordinary membership stays."""
    w, g = world, world.graph
    ext = w.sid("ext-own-gone")
    tid = await g.begin_transaction(["users", "apps"], ["apps", "organizations", "userAppRelation"])
    if hasattr(g, "client"):
        await g.client.execute_query("MATCH (:User {id: $u})-[r:USER_APP_RELATION]->(:App {id: $app}) SET r.isExternalUser = false",
                                     parameters={"u": ext, "app": w.sid(APP)}, txn_id=tid)
    else:
        await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._from == @u AND e._to == @app UPDATE e WITH {isExternalUser: false} IN userAppRelation",
                                        {"u": f"users/{ext}", "app": f"apps/{w.sid(APP)}"}, txn_id=tid)
    await _reap_while(w, tid)
    edges = await _edges_to_app(w)
    assert edges.get("ext-own-gone") is False and "ext-own-live" in edges


async def test_a_person_granted_while_the_reaper_runs_is_not_deleted(world: _World) -> None:
    """A person held on the App only by its flagged membership, and a writer that grants it a record while the reaper runs (it takes the person,
    as the processor's principal lookup does, then writes the grant). The reaper may drop the membership it could not see a grant for, but never the
    person the committed grant comes from."""
    from app.models.entities import Person
    w, g = world, world.graph
    email = f"pers-{w.run}@example.com"
    pid = await g.upsert_person_by_email(Person(email=email, org_id=w.org), raise_on_error=True)
    try:
        await g.ensure_app_membership(pid, "person", w.sid(APP), is_external=True)
        await _first_pass(w)      # marks the membership: the reaper below sweeps
        tid = await g.begin_transaction(["records", "apps", "person"], ["apps", "organizations", "permission", "person"])
        assert await g.upsert_person_by_email(Person(email=email, org_id=w.org), transaction=tid, raise_on_error=True) == pid
        assert await g.batch_create_edges([{"from_id": pid, "from_collection": "person", "to_id": w.sid("r-own-live"), "to_collection": "records", "type": "USER",
                                            "role": "READER", "createdAtTimestamp": TS}], collection="permission", transaction=tid)
        await _reap_while(w, tid)
        if hasattr(g, "client"):
            left = await g.client.execute_query("MATCH (p:Person {id: $p})-[:PERMISSION]->(:Record {id: $r}) RETURN p.id AS id", parameters={"p": pid, "r": w.sid("r-own-live")})
        else:
            left = await g.http_client.execute_aql("FOR p IN person FILTER p._key == @p FOR e IN permission FILTER e._from == p._id AND e._to == @r RETURN p._key",
                                                   {"p": pid, "r": f"records/{w.sid('r-own-live')}"})
        assert len(left) == 1
    finally:
        if hasattr(g, "client"):
            await g.client.execute_query("MATCH (p:Person {id: $p}) DETACH DELETE p", parameters={"p": pid})
        else:
            await g.http_client.execute_aql("FOR e IN permission FILTER e._from == @p REMOVE e IN permission", {"p": f"person/{pid}"})
            await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._from == @p REMOVE e IN userAppRelation", {"p": f"person/{pid}"})
            await g.http_client.execute_aql("FOR p IN person FILTER p._key == @p REMOVE p IN person", {"p": pid})


async def _grant_through_the_store(w: _World, ext: str) -> str:
    """A connector's grant write alone (no ensure_app_membership after it), in a transaction left open; returns it."""
    g = w.graph
    tid = await g.begin_transaction(["users", "records", "apps"], ["apps", "organizations", "permission", "userAppRelation"])
    try:
        assert await GraphTransactionStore(g, tid, explicit=True).batch_create_edges(
            [{"from_id": ext, "from_collection": "users", "to_id": w.sid("r-own-live"), "to_collection": "records", "type": "USER", "role": "READER",
              "createdAtTimestamp": TS}], "permission")
    except BaseException:
        with contextlib.suppress(Exception):
            await g.rollback_transaction(tid)
        raise
    return tid


async def test_a_grant_through_the_store_keeps_its_holder_s_membership_from_a_reaper_that_overlaps_it(world: _World) -> None:
    """The store touches ext-own-gone's flagged membership before it writes the grant, in the grant's transaction, still open while the reaper
    runs: the reaper waits for it (Neo4j) or conflicts and runs again (ArangoDB), so the collaborator keeps the App once both have committed."""
    w = world
    await _reap_while(w, await _grant_through_the_store(w, w.sid("ext-own-gone")))
    assert set(await _edges_to_app(w)) == {"u", "ext-own-live", "ext-direct", "ext-own-gone"}


@pytest.mark.parametrize("how", ["batch", "replace"])
async def test_a_group_s_grant_through_the_store_keeps_its_member_s_membership_from_a_reaper_that_overlaps_it(world: _World, how: str) -> None:
    """ext-fresh is a flagged collaborator of the App and a member of the group fresh, which grants nothing yet: the reaper would drop it. The
    store writes fresh's grant on a record in a transaction still open while the reaper runs, after locking the flagged memberships of fresh's
    members on the record's App: the reaper waits for it (Neo4j) or conflicts and runs again (ArangoDB), and ext-fresh keeps the App."""
    w, g = world, world.graph
    extra = {"Role": {"externalRoleId": "ext-fresh", "connectorName": DRIVE, "connectorId": w.sid(APP), "createdAtTimestamp": TS}}
    group, member = _node(w.kind, "fresh", name="fresh", **extra.get(w.kind, {})), _node("User", "ext-fresh", email="ext-fresh@example.com", fullName="Ext fresh")
    w.nodes += [group, member]      # removed with the world
    for n in (group, member):
        assert await g.batch_upsert_nodes([{**n["props"], "id": w.sid(n["id"]), "orgId": w.org}], collection=NODE_TARGETS[n["kind"]])
    coll = NODE_TARGETS[w.kind]
    assert await g.batch_create_edges([{"from_id": w.sid("ext-fresh"), "from_collection": "users", "to_id": w.sid(APP), "to_collection": "apps",
                                        "syncState": "COMPLETED", "lastSyncUpdate": TS, "isExternalUser": True}], collection="userAppRelation")
    assert await g.batch_create_edges([{"from_id": w.sid("ext-fresh"), "from_collection": "users", "to_id": w.sid("fresh"), "to_collection": coll,
                                        "type": "USER", "role": "READER", "createdAtTimestamp": TS}], collection="permission")
    tid = await g.begin_transaction(["users", "records", "apps", coll], ["apps", "organizations", "permission", "userAppRelation", "inheritPermissions", coll])
    fresh = {"from_id": w.sid("fresh"), "from_collection": coll, "to_id": w.sid("r-own-live"), "to_collection": "records",
             "type": GRANT_TYPE[w.kind], "role": "READER", "createdAtTimestamp": TS}
    try:
        store = GraphTransactionStore(g, tid, explicit=True)
        if how == "batch":
            assert await store.batch_create_edges([fresh], "permission")
        else:      # the record's grants as they are (kept: nothing to lock) and fresh's (created: locked)
            await store.replace_record_permissions(w.sid("r-own-live"), [*_r_own_live_grants(w), fresh], None, inherit=False)
    except BaseException:
        with contextlib.suppress(Exception):
            await g.rollback_transaction(tid)
        raise
    await _reap_while(w, tid)
    assert "ext-fresh" in await _edges_to_app(w)


def _r_own_live_grants(w: _World) -> list[dict]:
    """The grants on r-own-live as the world holds them: own-live's (a group, role or team) and ext-direct's own."""
    return [{"from_id": w.sid("own-live"), "from_collection": NODE_TARGETS[w.kind], "to_id": w.sid("r-own-live"), "to_collection": "records",
             "type": GRANT_TYPE[w.kind], "role": "WRITER", "createdAtTimestamp": TS},
            {"from_id": w.sid("ext-direct"), "from_collection": "users", "to_id": w.sid("r-own-live"), "to_collection": "records",
             "type": "USER", "role": "READER", "createdAtTimestamp": TS}]


@pytest.mark.parametrize("records", [1, 2], ids=["one_record", "two_records"])
async def test_a_member_added_while_a_group_s_grant_is_written_keeps_its_membership(world: _World, records: int) -> None:
    """T1 writes the grant of late (a group, role or team with no member) on r-own-live and stays open. T2 makes ext-late, a flagged collaborator
    of the App, a member of late and commits (or waits or conflicts, when it shares a lock with T1); the reaper runs while T1 is open; T1
    commits. T1 holds the App's lock and T2 the App's or the org's (late has no connector), which the reaper takes too: it decides after both
    have committed, and ext-late keeps the App (without the lock, the reaper removed it before T1 committed). With two records T1 writes
    late's grant on r-own-gone too, after T2 has met it, in the same transaction. late has many members already: on Neo4j a dense node's
    relationships are written without the node's own lock, so only the grant writers' locks order the three."""
    w, g = world, world.graph
    coll = NODE_TARGETS[w.kind]
    extra = {"Role": {"externalRoleId": "ext-late", "connectorName": DRIVE, "connectorId": w.sid(APP), "createdAtTimestamp": TS}}
    late, member = _node(w.kind, "late", name="late", **extra.get(w.kind, {})), _node("User", "ext-late", email="ext-late@example.com", fullName="Ext late")
    w.nodes += [late, member]      # removed with the world
    others = [_node("User", f"late-{i}", email=f"late-{i}@example.com", fullName=f"Late {i}") for i in range(60)]
    w.nodes += others
    for n in (late, member, *others):
        assert await g.batch_upsert_nodes([{**n["props"], "id": w.sid(n["id"]), "orgId": w.org}], collection=NODE_TARGETS[n["kind"]])
    assert await g.batch_create_edges([{"from_id": w.sid(o["id"]), "from_collection": "users", "to_id": w.sid("late"), "to_collection": coll,
                                        "type": "USER", "role": "READER", "createdAtTimestamp": TS} for o in others], collection="permission")
    assert await g.batch_create_edges([{"from_id": w.sid("ext-late"), "from_collection": "users", "to_id": w.sid(APP), "to_collection": "apps",
                                        "syncState": "COMPLETED", "lastSyncUpdate": TS, "isExternalUser": True}], collection="userAppRelation")
    grant = {"from_id": w.sid("late"), "from_collection": coll, "to_id": w.sid("r-own-live"), "to_collection": "records",
             "type": GRANT_TYPE[w.kind], "role": "READER", "createdAtTimestamp": TS}
    membership = {"from_id": w.sid("ext-late"), "from_collection": "users", "to_id": w.sid("late"), "to_collection": coll,
                  "type": "USER", "role": "READER", "createdAtTimestamp": TS}
    collections = (["users", "records", "apps", coll], ["apps", "organizations", "permission", "userAppRelation", "inheritPermissions", coll])
    t1 = await g.begin_transaction(*collections)
    open_ = [t1]
    conflicted, t1_ended = asyncio.Event(), asyncio.Event()

    async def add() -> None:
        """T2, run again after a conflict once T1 is no longer open, as its caller does."""
        while True:
            t2 = await g.begin_transaction(*collections)
            open_.append(t2)
            st = GraphTransactionStore(g, t2, explicit=True)
            try:
                assert await st.batch_create_edges([membership], "permission")
                await st.commit()
                open_.remove(t2)
                return
            except Exception as e:
                open_.remove(t2)
                with contextlib.suppress(Exception):
                    await st.rollback()
                if not g.is_write_conflict(e):
                    raise
                conflicted.set()
                await t1_ended.wait()
    adding = reaping = conflict_seen = None
    try:
        store = GraphTransactionStore(g, t1, explicit=True)
        if records == 1:
            assert await store.batch_create_edges([grant], "permission")
        else:
            await store.replace_record_permissions(w.sid("r-own-live"), [*_r_own_live_grants(w), grant], None, inherit=False)
        adding = asyncio.create_task(add())
        conflict_seen = asyncio.ensure_future(conflicted.wait())
        if hasattr(g, "client"):
            await _until_it_meets(g, adding)
        else:      # ArangoDB answers a second writer of one lock with a conflict at once; T2 may share none with T1 (late has no connector)
            await asyncio.wait_for(asyncio.wait([adding, conflict_seen], return_when=asyncio.FIRST_COMPLETED), 10)
        if records == 2:
            await store.replace_record_permissions(w.sid("r-own-gone"), [{**grant, "to_id": w.sid("r-own-gone")}], None, inherit=False)
        # the reaper while T1 is open, whatever T2 does: it waits for T1 (Neo4j: its own statement is the one blocked) or conflicts with it
        reaping = asyncio.create_task(g.reap_stale_external_app_relations(w.sid(APP)))
        await _until_it_meets(g, reaping, marker=REAPER_MARK)
        if hasattr(g, "client"):
            assert not reaping.done(), "the reaper decided while T1's grant was open"
        else:
            with pytest.raises(Exception) as lost:
                await reaping
            assert g.is_write_conflict(lost.value)
        await g.commit_transaction(t1)
        open_.remove(t1)
        t1_ended.set()
        await adding
        if hasattr(g, "client"):
            await reaping      # after T1; before or after T2, whose commit makes again what it removed
        await g.reap_stale_external_app_relations(w.sid(APP))      # the control: a reaper that runs after both decides on the committed chain
    finally:
        for task in (adding, reaping, conflict_seen):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        t1_ended.set()
        while open_:
            with contextlib.suppress(Exception):
                await g.rollback_transaction(open_.pop())
    assert "ext-late" in await _edges_to_app(w)


async def _commit_with_reaper_between(w: _World, write: Any, collections: tuple[list, list]) -> None:  # noqa: ANN401
    """Runs ``write(store)`` in a transaction, then the reaper while it is open, then commits: a reaper that waits for the writer (Neo4j) or
    loses to it (ArangoDB: a conflict) runs again once the writer has committed, as its caller does."""
    g = w.graph
    tid = await g.begin_transaction(*collections)
    reaping = None
    try:
        await write(GraphTransactionStore(g, tid, explicit=True))
        reaping = asyncio.create_task(g.reap_stale_external_app_relations(w.sid(APP)))
        await _until_it_meets(g, reaping)
        await g.commit_transaction(tid)
        tid = None
        try:
            await reaping
        except Exception as e:
            if not g.is_write_conflict(e):
                raise
            await g.reap_stale_external_app_relations(w.sid(APP))
    finally:
        if reaping is not None and not reaping.done():
            reaping.cancel()
            with contextlib.suppress(BaseException):
                await reaping
        if tid is not None:
            with contextlib.suppress(Exception):
                await g.rollback_transaction(tid)


@pytest.mark.parametrize("holder", ["direct", "group"])
async def test_a_restored_record_s_grant_locks_its_membership_though_the_edge_was_there(world: _World, holder: str) -> None:
    """r-own-live is in the trash with one grant on it, ext-direct's own or own-live's (ext-own-live's group, role or team; its grant on
    g-own-live is removed, so r-own-live's is its only one). A transaction restores the record and writes its grant again: the restore holds the
    App's lock first, so the reaper waits for it or conflicts with it and runs again, and the collaborator keeps the App."""
    w, g = world, world.graph
    coll = NODE_TARGETS[w.kind]
    kept, grants = ("ext-direct", [e for e in _r_own_live_grants(w) if e["from_id"] == w.sid("ext-direct")]) if holder == "direct" else \
                   ("ext-own-live", [e for e in _r_own_live_grants(w) if e["from_id"] == w.sid("own-live")])
    if holder == "group":
        assert await g.batch_delete_edges([{"from_id": w.sid("own-live"), "from_collection": coll, "to_id": w.sid("g-own-live"),
                                            "to_collection": "recordGroups"}], collection="permission")
    await g.soft_delete_records([w.sid("r-own-live")], w.sid(APP), delete_source="CONNECTOR", batch_id=f"trash-{w.run}")

    async def restore(store: GraphTransactionStore) -> None:
        assert await store.restore_records([{"id": w.sid("r-own-live")}], f"trash-{w.run}") == [w.sid("r-own-live")]
        await store.replace_record_permissions(w.sid("r-own-live"), grants, None, inherit=False)
    await _commit_with_reaper_between(w, restore, (["users", "records", "apps", coll], ["apps", "organizations", "permission", "userAppRelation", "inheritPermissions", "records", coll]))
    assert kept in await _edges_to_app(w)


async def test_a_grant_kept_by_a_replace_while_another_removes_it_is_locked(world: _World) -> None:
    """T0 removes ext-direct's grant on r-own-live and stays open; T1 writes r-own-live's grants with ext-direct's. T1 locks the record first
    (Neo4j) or conflicts with T0's removal (ArangoDB), so it reads the edge gone: ext-direct's grant is created and locks its membership, the
    reaper waits for T1 or loses to it, and ext-direct keeps the App (Neo4j kept the grant it read before T0 committed, without a lock)."""
    w, g = world, world.graph
    coll = NODE_TARGETS[w.kind]
    collections = (["users", "records", "apps", coll], ["apps", "organizations", "permission", "userAppRelation", "inheritPermissions", coll])
    t0 = await g.begin_transaction(*collections)
    others = [e for e in _r_own_live_grants(w) if e["from_id"] != w.sid("ext-direct")]
    writing = None
    t0_done, t1_conflicted = asyncio.Event(), asyncio.Event()
    try:
        await GraphTransactionStore(g, t0, explicit=True).replace_record_permissions(w.sid("r-own-live"), others, None, inherit=False)

        async def keep(store: GraphTransactionStore) -> None:
            await store.replace_record_permissions(w.sid("r-own-live"), _r_own_live_grants(w), None, inherit=False)

        async def after_t0() -> None:
            """T1, run again after a conflict once T0 has committed, as its caller does."""
            while True:
                try:
                    await _commit_with_reaper_between(w, keep, collections)
                    return
                except Exception as e:
                    if not g.is_write_conflict(e):
                        raise
                    t1_conflicted.set()
                    await t0_done.wait()
        writing = asyncio.create_task(after_t0())
        if hasattr(g, "client"):
            await _until_it_meets(g, writing)      # T1 waits on the record T0 holds
        else:      # T1 has met T0's removal (a conflict) before T0 commits; it runs again after
            await asyncio.wait_for(t1_conflicted.wait(), 10)
        await g.commit_transaction(t0)
        t0_done.set()
        await writing
    finally:
        if writing is not None and not writing.done():
            writing.cancel()
            with contextlib.suppress(BaseException):
                await writing
        if not t0_done.is_set():
            with contextlib.suppress(Exception):
                await g.rollback_transaction(t0)
    assert "ext-direct" in await _edges_to_app(w)


async def test_a_team_member_added_while_an_app_is_added_keeps_its_membership_of_it(world: _World) -> None:
    """The app other has a record granted to team late-team and no flagged member. W makes ext-team a member of late-team and stays open;
    meanwhile ext-team becomes a flagged collaborator of other, which W never named. The reaper of other runs before W commits. W holds the
    org's lock (its node or document), which every reaper takes with its app's, so the reaper waits for W (Neo4j) or conflicts with it and runs
    again (ArangoDB), and ext-team keeps other: its chain to the record is committed by then. (On Neo4j W also holds the team's and the
    member's nodes, which any write of the grant or the flag would wait for: both are written before W there.)"""
    w, g = world, world.graph
    org_node = _node("Organization", w.org, with_org=False, name="Org", accountType="enterprise", isActive=True)
    team, ext = _node("Teams", "late-team", name="late team"), _node("User", "ext-team", email="ext-team@example.com", fullName="Ext team")
    other, record = app("other", "Mail", connector="MAIL", app_group="M"), rec("r-other", "r other", connector_id="other")
    w.nodes += [team, ext, other, record]      # removed with the world
    tid = reaping = None
    try:      # the org is not the world's: written inside, so the cleanup below removes it whatever fails
        assert await g.batch_upsert_nodes([org_node["props"]], collection="organizations")
        for n in (team, ext):
            assert await g.batch_upsert_nodes([{**n["props"], "id": w.sid(n["id"]), "orgId": w.org}], collection=NODE_TARGETS[n["kind"]])
        assert await g.batch_upsert_nodes([{**other["props"], "id": w.sid("other"), "orgId": w.org}], collection="apps")
        assert await g.batch_upsert_nodes([{**record["props"], "id": w.sid("r-other"), "orgId": w.org, "connectorId": w.sid("other")}], collection="records")
        assert await g.batch_create_edges([{"from_id": w.sid("late-team"), "from_collection": "teams", "to_id": w.sid("r-other"), "to_collection": "records",
                                            "type": "TEAM", "role": "READER", "createdAtTimestamp": TS}], collection="permission")
        async def flag() -> None:
            assert await g.batch_create_edges([{"from_id": w.sid("ext-team"), "from_collection": "users", "to_id": w.sid("other"), "to_collection": "apps",
                                                "syncState": "COMPLETED", "lastSyncUpdate": TS, "isExternalUser": True}], collection="userAppRelation")
        if hasattr(g, "client"):
            await flag()
        collections = (["users", "records", "apps", "teams"], ["apps", "organizations", "permission", "userAppRelation", "teams"])
        tid = await g.begin_transaction(*collections)
        assert await GraphTransactionStore(g, tid, explicit=True).batch_create_edges(
            [{"from_id": w.sid("ext-team"), "from_collection": "users", "to_id": w.sid("late-team"), "to_collection": "teams", "type": "USER",
              "role": "READER", "createdAtTimestamp": TS}], "permission")
        if not hasattr(g, "client"):
            await flag()
        reaping = asyncio.create_task(g.reap_stale_external_app_relations(w.sid("other")))
        await _until_it_meets(g, reaping, marker=REAPER_MARK)
        if hasattr(g, "client"):
            assert not reaping.done(), "the reaper of other decided while ext-team's membership of the team was open"
        await g.commit_transaction(tid)
        tid = None
        try:
            await reaping
        except Exception as e:
            if not g.is_write_conflict(e):
                raise
            await g.reap_stale_external_app_relations(w.sid("other"))      # the caller's retry, now that W has committed
        if hasattr(g, "client"):
            rows = await g.client.execute_query("MATCH (u:User)-[:USER_APP_RELATION]->(:App {id: $app}) RETURN u.id AS id", parameters={"app": w.sid("other")})
        else:
            rows = await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._to == @app RETURN {id: PARSE_IDENTIFIER(e._from).key}",
                                                   {"app": f"apps/{w.sid('other')}"})
        assert [w.plain(r["id"]) for r in rows] == ["ext-team"]
    finally:
        if reaping is not None and not reaping.done():
            reaping.cancel()
            with contextlib.suppress(BaseException):
                await reaping
        if tid is not None:
            with contextlib.suppress(Exception):
                await g.rollback_transaction(tid)
        if hasattr(g, "client"):
            await g.client.execute_query("MATCH (o:Organization {id: $o}) DETACH DELETE o", parameters={"o": w.org})
        else:
            await g.http_client.execute_aql("FOR d IN organizations FILTER d._key == @o REMOVE d IN organizations", {"o": w.org})


async def test_writers_of_one_app_take_turns(world: _World) -> None:
    """Neo4j and ArangoDB hold an app's lock exclusively: a second grant writer of the App waits for the first to commit (Neo4j) or conflicts
    with it at once (ArangoDB), and goes through once the first has committed."""
    w, g = world, world.graph
    collections = (["users", "records", "apps"], ["apps", "organizations", "permission", "userAppRelation"])

    def grant(user: str, record: str) -> dict:
        return {"from_id": w.sid(user), "from_collection": "users", "to_id": w.sid(record), "to_collection": "records", "type": "USER",
                "role": "READER", "createdAtTimestamp": TS}
    t1 = await g.begin_transaction(*collections)
    open_ = [t1]
    second = None
    try:
        assert await GraphTransactionStore(g, t1, explicit=True).batch_create_edges([grant("ext-direct", "r-src-live")], "permission")

        async def write() -> None:
            t2 = await g.begin_transaction(*collections)
            open_.append(t2)
            # another record of the App: the App is the only node the two writers share
            assert await GraphTransactionStore(g, t2, explicit=True).batch_create_edges([grant("ext-own-live", "r-own-gone")], "permission")
            await g.commit_transaction(t2)
            open_.remove(t2)
        second = asyncio.create_task(write())
        await _until_it_meets(g, second)
        if hasattr(g, "client"):
            assert not second.done(), "the second writer of the App did not wait for the first"
        else:
            with pytest.raises(Exception) as conflict:
                await second
            assert g.is_write_conflict(conflict.value)
        await g.commit_transaction(t1)
        open_.remove(t1)
        if hasattr(g, "client"):
            await asyncio.wait_for(second, 10)
        else:
            await write()      # the caller's retry, now that the first has committed
    finally:
        if second is not None and not second.done():
            second.cancel()
            with contextlib.suppress(BaseException):
                await second
        while open_:
            with contextlib.suppress(Exception):
                await g.rollback_transaction(open_.pop())


async def test_within_a_transaction_an_app_s_lock_is_written_once(world: _World) -> None:
    """Two grant statements on records of the App in one transaction: the first writes the App's lock (its node or document), the second is
    told the lock is held and writes nothing for it, and the reaper of the App still waits for (or conflicts with) the transaction."""
    w, g = world, world.graph
    seen: list[list[str]] = []
    if hasattr(g, "client"):
        run = g.client.execute_query

        async def spy(query: str, *a: object, **k: object) -> list:
            rows = await run(query, *a, **k)
            if "lock_new" in query:
                seen.append(rows[0]["lock_new"])
            return rows
        g.client.execute_query = spy
    else:
        run = g.http_client.execute_aql

        async def spy(query: str, *a: object, **k: object) -> list:
            rows = await run(query, *a, **k)
            if "lock_apps" in query:
                seen.append(rows[0]["locked"])
            return rows
        g.http_client.execute_aql = spy
    tid = await g.begin_transaction(["users", "records", "apps"], ["apps", "organizations", "permission", "userAppRelation"])
    reaping = None
    try:
        store = GraphTransactionStore(g, tid, explicit=True)
        for user, record in (("ext-direct", "r-src-live"), ("ext-own-live", "r-own-live")):
            assert await store.batch_create_edges([{"from_id": w.sid(user), "from_collection": "users", "to_id": w.sid(record),
                                                    "to_collection": "records", "type": "USER", "role": "READER", "createdAtTimestamp": TS}], "permission")
        assert seen == [[w.sid(APP)], []]
        assert store._app_locks == {w.sid(APP)}
        reaping = asyncio.create_task(g.reap_stale_external_app_relations(w.sid(APP)))
        await _until_it_meets(g, reaping, marker=REAPER_MARK if hasattr(g, "client") else None)
        if hasattr(g, "client"):
            assert not reaping.done(), "the reaper decided while the transaction held the App's lock"
        await g.commit_transaction(tid)
        tid = None
        try:
            await reaping
        except Exception as e:
            if not g.is_write_conflict(e):
                raise
    finally:
        if hasattr(g, "client"):
            g.client.execute_query = run
        else:
            g.http_client.execute_aql = run
        if reaping is not None and not reaping.done():
            reaping.cancel()
            with contextlib.suppress(BaseException):
                await reaping
        if tid is not None:
            with contextlib.suppress(Exception):
                await g.rollback_transaction(tid)


async def test_an_app_without_an_org_is_not_reaped(world: _World) -> None:
    """An app a backfill left without orgId cannot take the org's lock a writer of a team's membership holds: its reaper removes nothing (the
    next pass, once the org is known, decides), not a flagged membership whose chain such a writer may be committing."""
    w, g = world, world.graph
    lone, ext = app("no-org", "Lone", connector="MAIL", app_group="M"), _node("User", "ext-lone", email="ext-lone@example.com", fullName="Ext lone")
    w.nodes += [lone, ext]      # removed with the world
    assert await g.batch_upsert_nodes([{**lone["props"], "id": w.sid("no-org"), "orgId": None}], collection="apps")
    assert await g.batch_upsert_nodes([{**ext["props"], "id": w.sid("ext-lone"), "orgId": w.org}], collection="users")
    assert await g.batch_create_edges([{"from_id": w.sid("ext-lone"), "from_collection": "users", "to_id": w.sid("no-org"), "to_collection": "apps",
                                        "syncState": "COMPLETED", "lastSyncUpdate": TS, "isExternalUser": True}], collection="userAppRelation")
    assert await g.reap_stale_external_app_relations(w.sid("no-org")) == 0
    if hasattr(g, "client"):
        rows = await g.client.execute_query("MATCH (u:User)-[:USER_APP_RELATION]->(:App {id: $app}) RETURN u.id AS id", parameters={"app": w.sid("no-org")})
    else:
        rows = await g.http_client.execute_aql("FOR e IN userAppRelation FILTER e._to == @app RETURN {id: PARSE_IDENTIFIER(e._from).key}",
                                               {"app": f"apps/{w.sid('no-org')}"})
    assert [w.plain(r["id"]) for r in rows] == ["ext-lone"]


def _grant_of(w: _World, user: str, record: str) -> dict:
    return {"from_id": w.sid(user), "from_collection": "users", "to_id": w.sid(record), "to_collection": "records", "type": "USER",
            "role": "READER", "createdAtTimestamp": TS}


@pytest.mark.parametrize("locked", [True, False], ids=["under_the_app_lock", "lock_free"])
async def test_a_writer_open_at_the_sweep_keeps_its_membership_only_under_the_app_lock(world: _World, monkeypatch: pytest.MonkeyPatch, locked: bool) -> None:
    """ext-own-gone's membership was marked by the reaper's first pass (no grant kept it). It is a member of hole-g, which has no grant. A writer
    opens after the mark, grants hole-g a record of the App and stays open while the second pass runs. Under the app lock the pass waits for the
    writer (Neo4j) or conflicts with it and runs again (ArangoDB), and reads the chain: ext-own-gone keeps the App. Lock-free (no writer lock, no
    reaper lock) the pass reads the committed state, where the grant is not yet, and removes the membership under a chain about to commit: the
    hole of a reaper-only design (design-6715-v2, section 1). The grant is a group's, so it locks nothing of ext-own-gone's on Neo4j either."""
    w, g = world, world.graph
    if not locked:
        monkeypatch.setattr(type(g), "_REAPER_HOLDS_APP_LOCK", False)
        head = "_app_lock_cypher" if hasattr(g, "client") else "_app_lock_aql"
        monkeypatch.setattr(type(g), head, staticmethod(lambda *_a, **_k: ("", {})))
    coll = NODE_TARGETS[w.kind]
    extra = {"Role": {"externalRoleId": "ext-hole", "connectorName": DRIVE, "connectorId": w.sid(APP), "createdAtTimestamp": TS}}
    hole = _node(w.kind, "hole-g", name="hole", **extra.get(w.kind, {}))
    w.nodes.append(hole)
    assert await g.batch_upsert_nodes([{**hole["props"], "id": w.sid("hole-g"), "orgId": w.org}], collection=coll)
    assert await g.batch_create_edges([{"from_id": w.sid("ext-own-gone"), "from_collection": "users", "to_id": w.sid("hole-g"), "to_collection": coll,
                                        "type": "USER", "role": "READER", "createdAtTimestamp": TS}], collection="permission")
    collections = (["users", "records", "apps", coll], ["apps", "organizations", "permission", "userAppRelation", coll])
    tid = await g.begin_transaction(*collections)
    reaping = None
    try:
        assert await GraphTransactionStore(g, tid, explicit=True).batch_create_edges(
            [{"from_id": w.sid("hole-g"), "from_collection": coll, "to_id": w.sid("r-own-live"), "to_collection": "records",
              "type": GRANT_TYPE[w.kind], "role": "READER", "createdAtTimestamp": TS}], "permission")
        reaping = asyncio.create_task(g.reap_stale_external_app_relations(w.sid(APP)))
        if locked:
            await _until_it_meets(g, reaping, marker=REAPER_MARK)
            if hasattr(g, "client"):
                assert not reaping.done(), "the sweep decided while the grant was open"
        else:
            await asyncio.wait_for(reaping, 10)      # nothing to wait for: it decides at once
        await g.commit_transaction(tid)
        tid = None
        try:
            await reaping
        except Exception as e:
            if not g.is_write_conflict(e):
                raise
            await g.reap_stale_external_app_relations(w.sid(APP))      # the caller's retry, now that the writer has committed
    finally:
        if reaping is not None and not reaping.done():
            reaping.cancel()
            with contextlib.suppress(BaseException):
                await reaping
        if tid is not None:
            with contextlib.suppress(Exception):
                await g.rollback_transaction(tid)
    assert ("ext-own-gone" in await _edges_to_app(w)) is locked


async def test_a_grant_queued_behind_the_sweep_loses_the_membership_until_the_next_membership_step(world: _World, caplog: pytest.LogCaptureFixture) -> None:
    """The residual window of the two-pass reaper (design-6715-v2, section 2, option D), explained rather than silent. ext-own-gone was marked a
    horizon ago and nothing wrote its membership since. The sweep removes it holding the App's lock; a grant to it queued behind the sweep (Neo4j)
    or run again after it (ArangoDB) then commits without the membership, and the removal is logged with the App, the principal and the mark.
    The next sync's membership step (ensure_app_membership) makes it again. Before the two-pass reaper the grant's re-make covered this, at the
    cost measured in m6715."""
    w, g = world, world.graph
    ext = w.sid("ext-own-gone")
    caplog.set_level(logging.INFO)
    rid = await g.begin_transaction(["users", "records", "apps", "person"], ["apps", "organizations", "userAppRelation", "person"])
    granting = None
    conflicted = asyncio.Event()

    async def grant() -> None:
        while True:
            tid = await g.begin_transaction(["users", "records", "apps"], ["apps", "organizations", "permission", "userAppRelation"])
            try:
                assert await GraphTransactionStore(g, tid, explicit=True).batch_create_edges([_grant_of(w, "ext-own-gone", "r-own-live")], "permission")
                await g.commit_transaction(tid)
                return
            except Exception as e:
                with contextlib.suppress(Exception):
                    await g.rollback_transaction(tid)
                if not g.is_write_conflict(e):
                    raise
                conflicted.set()
                await asyncio.sleep(0.2)      # the caller's retry
    try:
        await g.reap_stale_external_app_relations(w.sid(APP), transaction=rid)
        granting = asyncio.create_task(grant())
        if hasattr(g, "client"):
            await _until_it_meets(g, granting)      # waits on the App's lock the sweep holds
            assert not granting.done()
        else:
            await asyncio.wait_for(conflicted.wait(), 10)      # conflicts with the sweep, and runs again after it
        await g.commit_transaction(rid)
        rid = None
        await asyncio.wait_for(granting, 20)
    finally:
        if granting is not None and not granting.done():
            granting.cancel()
            with contextlib.suppress(BaseException):
                await granting
        if rid is not None:
            with contextlib.suppress(Exception):
                await g.rollback_transaction(rid)
    assert "ext-own-gone" not in await _edges_to_app(w)
    from app.utils.logger import log_record_fields
    removed = [log_record_fields(r) for r in caplog.records if r.getMessage() == "graph.reaper.membership_removed"]
    assert any(f.get("connector_id") == w.sid(APP) and f.get("principal_id") == ext and f.get("reap_mark") for f in removed), removed
    await g.ensure_app_membership(ext, "users", w.sid(APP), is_external=True)      # the next sync's membership step
    assert (await _edges_to_app(w)).get("ext-own-gone") is True


async def test_a_membership_written_after_the_mark_is_not_swept(world: _World) -> None:
    """ext-own-gone was marked by the first pass; its sync's membership step writes it again (the source still shares with it): the write clears
    the mark, so the next pass does not remove it though no grant keeps it yet, and marks it again instead."""
    w, g = world, world.graph
    await g.ensure_app_membership(w.sid("ext-own-gone"), "users", w.sid(APP), is_external=True)
    await g.reap_stale_external_app_relations(w.sid(APP))
    assert "ext-own-gone" in await _edges_to_app(w)
    await asyncio.sleep(0.01)
    await g.reap_stale_external_app_relations(w.sid(APP))      # marked again by the pass above, nothing wrote it since: removed
    assert "ext-own-gone" not in await _edges_to_app(w)


async def _visible(w: _World, user_id: str, records: list[str]) -> set[str]:
    return {w.plain(r) for r in await w.graph.filter_accessible_record_ids([w.sid(r) for r in records], user_id, w.org)}


async def test_a_trashed_grant_s_collaborator_loses_the_app_and_its_org_wide_records_and_gets_both_back_on_restore(world: _World) -> None:
    """ext-direct, a user of the org (it belongs to the Organization), is an external collaborator of the App through its one grant, on r-own-live.
    Its flagged membership is what lets an org-wide grant of the App (on r-org) reach it: app membership is not inert (design-6715-v2, section 3).
    r-own-live goes to the trash. While the membership is kept (what keep-trashed would do) ext-direct still reads r-org. The reaper's two passes
    remove it, as the trashed grant keeps nothing: r-org is hidden. The restore makes the membership again from the trashed grant's note, and
    both records are readable again. ext-cut, whose grant on r-own-live the source revoked while it was in the trash, gets nothing back."""
    w, g = world, world.graph
    org_node = _node("Organization", w.org, with_org=False, name="Org", accountType="enterprise", isActive=True)
    cut, r_org = _node("User", "ext-cut", email="ext-cut@example.com", fullName="Ext cut"), rec("r-org", "r org", connector_id=APP)
    w.nodes += [cut, r_org]
    uid = f"uid-ext-direct-{w.run}"
    try:
        assert await g.batch_upsert_nodes([org_node["props"]], collection="organizations")
        assert await g.batch_upsert_nodes([{"id": w.sid("ext-direct"), "userId": uid, "orgId": w.org, "email": "ext-direct@example.com",
                                            "fullName": "Ext direct", "isActive": True}], collection="users")
        assert await g.batch_upsert_nodes([{**cut["props"], "id": w.sid("ext-cut"), "orgId": w.org}], collection="users")
        assert await g.batch_upsert_nodes([{**r_org["props"], "id": w.sid("r-org"), "orgId": w.org, "connectorId": w.sid(APP)}], collection="records")
        assert await g.batch_create_edges([{"from_id": w.sid("ext-direct"), "from_collection": "users", "to_id": w.org, "to_collection": "organizations",
                                            "entityType": "ORGANIZATION", "createdAtTimestamp": TS}], collection="belongsTo")
        assert await g.batch_create_edges([{"from_id": w.org, "from_collection": "organizations", "to_id": w.sid("r-org"), "to_collection": "records",
                                            "type": "ORG", "role": "READER", "createdAtTimestamp": TS}, _grant_of(w, "ext-cut", "r-own-live")],
                                          collection="permission")
        assert await g.batch_create_edges([{"from_id": w.sid("ext-cut"), "from_collection": "users", "to_id": w.sid(APP), "to_collection": "apps",
                                            "syncState": "COMPLETED", "lastSyncUpdate": TS, "isExternalUser": True}], collection="userAppRelation")
        assert await _visible(w, uid, ["r-org", "r-own-live"]) == {"r-org", "r-own-live"}

        tid = await g.begin_transaction(["records", "permission", "userAppRelation", "users"], ["records", "permission", "userAppRelation"])
        await GraphTransactionStore(g, tid, explicit=True).soft_delete_records([w.sid("r-own-live")], w.sid(APP), delete_source="CONNECTOR",
                                                                               batch_id=f"trash-{w.run}")
        await g.commit_transaction(tid)
        assert "r-org" in await _visible(w, uid, ["r-org"])      # the membership kept with the grant in the trash: r-org still reachable
        await reap_twice(g, w.sid(APP))
        edges = await _edges_to_app(w)
        assert "ext-direct" not in edges and "ext-cut" not in edges
        assert await _visible(w, uid, ["r-org"]) == set()      # no org-wide record while the only grant is in the trash

        assert await g.batch_delete_edges([{"from_id": w.sid("ext-cut"), "from_collection": "users", "to_id": w.sid("r-own-live"),
                                            "to_collection": "records"}], collection="permission")      # revoked at the source meanwhile
        tid = await g.begin_transaction(["records", "permission", "userAppRelation", "users", "apps"],
                                        ["records", "permission", "userAppRelation", "apps", "organizations"])
        assert await GraphTransactionStore(g, tid, explicit=True).restore_records([{"id": w.sid("r-own-live")}], f"trash-{w.run}") == [w.sid("r-own-live")]
        await g.commit_transaction(tid)
        edges = await _edges_to_app(w)
        assert edges.get("ext-direct") is True and "ext-cut" not in edges
        assert await _visible(w, uid, ["r-org", "r-own-live"]) == {"r-org", "r-own-live"}
    finally:
        if hasattr(g, "client"):
            await g.client.execute_query("MATCH (o:Organization {id: $o}) DETACH DELETE o", parameters={"o": w.org})
        else:
            await g.http_client.execute_aql("FOR d IN organizations FILTER d._key == @o REMOVE d IN organizations", {"o": w.org})
            await g.http_client.execute_aql("FOR e IN belongsTo FILTER e._to == @o REMOVE e IN belongsTo", {"o": f"organizations/{w.org}"})
            await g.http_client.execute_aql("FOR e IN permission FILTER e._from == @o REMOVE e IN permission", {"o": f"organizations/{w.org}"})
