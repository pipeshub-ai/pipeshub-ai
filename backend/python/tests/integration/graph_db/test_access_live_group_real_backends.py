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
        yield w


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
    tid = await g.begin_transaction(["users", "records", "apps"], ["permission", "userAppRelation"])
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


async def _until_it_meets(g: Any, task: asyncio.Task) -> None:  # noqa: ANN401
    """Returns once ``task`` has met the open writer: it finished (ArangoDB answers a conflict at once, and a reaper the writer does not hold
    finishes) or Neo4j shows a transaction blocked on a lock. Fails if neither happens in 10 s; a fixed sleep let a slow start skip the overlap."""
    for _ in range(200):
        if task.done():
            return
        if hasattr(g, "client"):
            rows = await g.client.execute_query("SHOW TRANSACTIONS YIELD status WHERE status STARTS WITH 'Blocked' RETURN count(*) AS n")
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
        rid = await g.begin_transaction(["users", "records", "apps", "person"], ["userAppRelation", "person"]) if reaper_in_transaction else None
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
    tid = await g.begin_transaction(["users", "records", "apps"], ["permission", "userAppRelation"])
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
    tid = await g.begin_transaction(["users", "apps"], ["userAppRelation"])
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
        tid = await g.begin_transaction(["records", "apps", "person"], ["permission", "person"])
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
    tid = await g.begin_transaction(["users", "records", "apps"], ["permission", "userAppRelation"])
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


async def test_a_reaper_that_decided_before_the_grant_still_reaps(world: _World) -> None:
    """The reverse order: the reaper's transaction has removed ext-own-gone's membership (no grant then) and is still open when a grant to it is
    written through the store. The grant waits for the reaper or conflicts and is run again after it, as its caller does; the membership is gone."""
    w, g = world, world.graph
    ext = w.sid("ext-own-gone")
    rid = await g.begin_transaction(["users", "records", "apps", "person"], ["userAppRelation", "person"])
    await g.reap_stale_external_app_relations(w.sid(APP), transaction=rid)
    grant = asyncio.create_task(_grant_through_the_store(w, ext))
    await _until_it_meets(g, grant)
    await g.commit_transaction(rid)
    try:
        tid = await grant
    except Exception as e:
        if not g.is_write_conflict(e):
            raise
        tid = await _grant_through_the_store(w, ext)
    await g.commit_transaction(tid)
    assert set(await _edges_to_app(w)) == {"u", "ext-own-live", "ext-direct"}


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
    tid = await g.begin_transaction(["users", "records", "apps", coll], ["permission", "userAppRelation", "inheritPermissions"])
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


async def test_a_resync_that_keeps_the_grants_locks_nothing(world: _World) -> None:
    """r-own-live's grants written again as they are: each is committed and the reaper reads it, so the replace locks none of the memberships
    they keep. It finishes without waiting (Neo4j) or conflicting (ArangoDB) while another transaction holds ext-own-live's and ext-direct's."""
    w, g = world, world.graph
    coll = NODE_TARGETS[w.kind]
    hold = await g.begin_transaction(["users", "records", "apps", coll], ["userAppRelation"])
    tid = await g.begin_transaction(["users", "records", "apps", coll], ["permission", "userAppRelation", "inheritPermissions"])
    try:
        held = await g.lock_external_app_memberships([(coll, w.sid("own-live"), "records", w.sid("r-own-live")),
                                                      ("users", w.sid("ext-direct"), "records", w.sid("r-own-live"))], transaction=hold)
        assert {w.plain(k) for _, k, _ in held} == {"ext-own-live", "ext-direct"}
        await asyncio.wait_for(GraphTransactionStore(g, tid, explicit=True).replace_record_permissions(
            w.sid("r-own-live"), _r_own_live_grants(w), None, inherit=False), timeout=10)
        await g.commit_transaction(tid)
    finally:
        await g.rollback_transaction(hold)
        with contextlib.suppress(Exception):
            await g.rollback_transaction(tid)
    assert set(await _edges_to_app(w)) == {"u", "ext-own-live", "ext-direct", "ext-own-gone"}


async def test_a_group_s_new_grants_on_records_of_one_app_lock_its_members_once_a_transaction(world: _World) -> None:
    """own-live's new grants on r-src-live and r-own-gone, both of the App, in one transaction: on ArangoDB the store records (own-live, App)
    with the first, and the second replace is handed it, so it does not lock own-live's members again. Neo4j keeps no pair (its check that an
    edge is created costs what the lock does): the second replace locks them again, which costs a lookup and changes nothing."""
    w, g = world, world.graph
    coll = NODE_TARGETS[w.kind]

    def grant(holder: str, record: str) -> dict:
        return {"from_id": w.sid(holder), "from_collection": coll, "to_id": w.sid(record), "to_collection": "records", "type": GRANT_TYPE[w.kind],
                "role": "WRITER", "createdAtTimestamp": TS}
    tid = await g.begin_transaction(["users", "records", "apps", coll], ["permission", "userAppRelation", "inheritPermissions"])
    try:
        store = GraphTransactionStore(g, tid, explicit=True)
        await store.replace_record_permissions(w.sid("r-src-live"), [grant("src-live", "r-src-live"), grant("own-live", "r-src-live")], None, inherit=False)
        if hasattr(g, "client"):
            assert store._lock_done == set()
            await store.replace_record_permissions(w.sid("r-own-gone"), [grant("own-live", "r-own-gone")], None, inherit=False)
            await g.commit_transaction(tid)
            return
        assert (coll, w.sid("own-live"), w.sid(APP)) in store._lock_done
        handed, locked = [], []
        replace, create = g.replace_record_permissions, g.batch_create_edges

        async def spy_replace(*a, **k) -> object:
            handed.append(set(k.get("lock_done") or ()))
            return await replace(*a, **k)

        async def spy_create(*a, **k) -> object:
            locked.append(list(k.get("lock_grants") or ()))
            return await create(*a, **k)
        g.replace_record_permissions, g.batch_create_edges = spy_replace, spy_create
        try:
            await store.replace_record_permissions(w.sid("r-own-gone"), [grant("own-live", "r-own-gone")], None, inherit=False)
        finally:
            del g.replace_record_permissions, g.batch_create_edges
        assert (coll, w.sid("own-live"), w.sid(APP)) in handed[0]
        assert locked == [[]]      # its replace writes through batch_create_edges, handed no grant to lock
        await g.commit_transaction(tid)
    finally:
        with contextlib.suppress(Exception):
            await g.rollback_transaction(tid)
