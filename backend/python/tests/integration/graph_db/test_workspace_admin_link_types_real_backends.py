"""The cross-org removal on a real ArangoDB and a real Neo4j: for a creator the connector lists it removes the link of exactly the
named app types (exact string), keeps links whose app has another type, a type in other case or spacing, no type, or no app at
all, and reports the app's org."""

from __future__ import annotations

import uuid

import pytest

from app.core.org_context import system

from .test_connector_entities import arango_provider, neo4j_provider  # noqa: F401
from .test_workspace_admin_link_store_real_backends import _ensure_arango_collections


async def _ensure_relation_collection(p) -> None:
    import aiohttp

    client = p.http_client
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as sess:
        async with sess.post(f"{client.base_url}/_db/{client.database}/_api/collection", json={"name": "userAppRelation", "type": 3}) as r:
            assert r.status in (200, 201, 409)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
NAMED = ["Gmail Workspace", "Drive Workspace", "Dropbox"]
CASES = {"gm": "Gmail Workspace", "dr": "Drive Workspace", "db": "Dropbox", "lc": "gmail workspace", "sp": "Gmail Workspace ",
         "dbp": "Dropbox Personal", "gml": "Gmail", "jira": "Jira", "none": None}
REMOVED = {"gm", "dr", "db"}


async def test_arango_only_the_exact_type_names_lose_their_link(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    await _ensure_arango_collections(p)
    await _ensure_relation_collection(p)
    t = uuid.uuid4().hex[:8]
    org = f"org-{t}"
    for u in ("c", "s"):
        await p.execute_query("INSERT {_key: @u, orgId: @o} INTO users", bind_vars={"u": f"{u}-{t}", "o": org})
    for k, ty in CASES.items():
        doc = {"_key": f"{k}-{t}", "orgId": org, **({"type": ty} if ty is not None else {})}
        await p.execute_query("INSERT @d INTO apps", bind_vars={"d": doc})
        await p.execute_query("INSERT {_from: @f, _to: @a, sourceUserId: 'src'} INTO userAppRelation",
                              bind_vars={"f": f"users/c-{t}", "a": f"apps/{k}-{t}"})
        await p.upsert_authenticated_as(f"c-{t}", f"s-{t}", f"{k}-{t}", org)
    await p.upsert_authenticated_as(f"c-{t}", f"s-{t}", f"orphan-{t}", org)          # no app at all
    with system("wsleak-it"):
        removed, failed = await p.remove_listed_authenticator_links(NAMED)
    assert failed == []
    mine = {r["connectorId"]: r["org"] for r in removed if r["connectorId"].endswith(t)}
    assert mine == {f"{k}-{t}": org for k in REMOVED}
    assert {(r["user"], r["selfLink"]) for r in removed if r["connectorId"].endswith(t)} == {(f"c-{t}", False)}
    left = await p.execute_query("FOR l IN authenticatedAs FILTER l.orgId == @o RETURN l.connectorId", bind_vars={"o": org})
    assert sorted(left) == sorted([f"{k}-{t}" for k in CASES if k not in REMOVED] + [f"orphan-{t}"])


async def test_neo4j_only_the_exact_type_names_lose_their_link(neo4j_provider) -> None:  # noqa: F811
    p = neo4j_provider
    t = uuid.uuid4().hex[:8]
    org = f"org-{t}"
    q = p.client.execute_query
    for u in ("c", "s"):
        await q("CREATE (:User {id: $u, orgId: $o})", parameters={"u": f"{u}-{t}", "o": org})
    for k, ty in CASES.items():
        await q("CREATE (a:App {id: $a, orgId: $o}) SET a.type = $t", parameters={"a": f"{k}-{t}", "o": org, "t": ty})
        await q("MATCH (u:User {id: $u}), (a:App {id: $a}) CREATE (u)-[:USER_APP_RELATION {sourceUserId: 'src'}]->(a)",
                parameters={"u": f"c-{t}", "a": f"{k}-{t}"})
        await p.upsert_authenticated_as(f"c-{t}", f"s-{t}", f"{k}-{t}", org)
    await q("MATCH (c:User {id: $c}), (s:User {id: $s}) CREATE (c)-[:AUTHENTICATED_AS {connectorId: $x, orgId: $o}]->(s)",
            parameters={"c": f"c-{t}", "s": f"s-{t}", "x": f"orphan-{t}", "o": org})
    with system("wsleak-it"):
        removed, failed = await p.remove_listed_authenticator_links(NAMED)
    assert failed == []
    mine = {r["connectorId"]: r["org"] for r in removed if r["connectorId"].endswith(t)}
    assert mine == {f"{k}-{t}": org for k in REMOVED}
    assert {(r["user"], r["selfLink"]) for r in removed if r["connectorId"].endswith(t)} == {(f"c-{t}", False)}
    left = await q("MATCH ()-[r:AUTHENTICATED_AS]->() WHERE r.orgId = $o RETURN r.connectorId AS c", parameters={"o": org})
    assert sorted(r["c"] for r in left) == sorted([f"{k}-{t}" for k in CASES if k not in REMOVED] + [f"orphan-{t}"])
