"""The ArangoDB and Neo4j link fixes against real stores: a link a verified email change moved still has its key and is
removed by its connector id, a re-auth leaves one link, and the connector id is indexed.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml up -d
Run: pytest tests/integration/graph_db/test_workspace_admin_link_store_real_backends.py -m integration
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import pytest

from .test_connector_entities import arango_provider, neo4j_provider  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class _Config:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.values[key] = value
        return True


async def _ensure_arango_collections(provider) -> None:
    import aiohttp

    client = provider.http_client
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as s:
        for name, kind in (("users", 2), ("apps", 2), ("organizations", 2), ("authenticatedAs", 3)):
            async with s.post(f"{client.base_url}/_db/{client.database}/_api/collection", json={"name": name, "type": kind}) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create collection {name}: {r.status}")


# ---- a link moved by a verified email change ----------------------------------------------------------------
# The admin's source account is a stub user; when the admin's login user verifies the Workspace email, the stub's
# edges move to the login user. The moved link must still be found by the connector id and removed, or the creator
# keeps the admin's grants through it.

LOGIN_USER_ID = "65a1b2c3d4e5f6a7b8c9d0e1"  # a PipesHub (Mongo) user id


async def _moved_link_case(provider, create_users, links_of) -> None:
    t = uuid.uuid4().hex[:8]
    org, conn = f"org-{t}", f"gm-{t}"
    email = f"admin-{t}@corp.example"
    await create_users(provider, org, t, email)
    await provider.upsert_authenticated_as(f"creator-{t}", f"adminstub-{t}", conn, org)
    assert await links_of(provider, conn) == [f"adminstub-{t}"]

    result = await provider.apply_verified_user_email(LOGIN_USER_ID[:-8] + t, org, email)
    assert result and result["mergedStubKeys"] == [f"adminstub-{t}"]
    assert await links_of(provider, conn) == [f"adminlogin-{t}"], "the link moved to the admin's login user"

    assert await provider.remove_authenticated_as(conn) is True
    assert await links_of(provider, conn) == [], "a moved link is removed like any other"


async def _arango_users(p, org, t, email) -> None:
    await _ensure_arango_collections(p)
    import aiohttp

    client = p.http_client
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as s:
        for name in ("permission", "belongsTo", "userAppRelation", "userDriveRelation", "entityRelations"):
            async with s.post(f"{client.base_url}/_db/{client.database}/_api/collection", json={"name": name, "type": 3}) as r:
                assert r.status in (200, 201, 409)
    q = p.execute_query
    await q("INSERT {_key: @o, isActive: true} INTO organizations", bind_vars={"o": org})
    await q("INSERT {_key: @a, type: 'Gmail Workspace', orgId: @o} INTO apps", bind_vars={"a": f"gm-{t}", "o": org})
    for key, uid, mail, active in (
        (f"creator-{t}", "75a1b2c3d4e5f6a7" + t, f"creator-{t}@corp.example", True),
        (f"adminlogin-{t}", LOGIN_USER_ID[:-8] + t, f"old-{t}@corp.example", True),
        (f"adminstub-{t}", f"g-src-{t}", email, False),
    ):
        await q(
            "INSERT {_key: @k, orgId: @o, userId: @u, email: @e, isActive: @a} INTO users",
            bind_vars={"k": key, "o": org, "u": uid, "e": mail, "a": active},
        )


async def _arango_links_of(p, conn) -> list[str]:
    rows = await p.execute_query(
        "FOR l IN authenticatedAs FILTER l.connectorId == @c RETURN PARSE_IDENTIFIER(l._to).key", bind_vars={"c": conn}
    )
    return sorted(rows)


async def _neo4j_users(p, org, t, email) -> None:
    q = p.client.execute_query
    await q("CREATE (:Organization {id: $o, isActive: true})", parameters={"o": org})
    await q("CREATE (:App {id: $a, type: 'Gmail Workspace', orgId: $o})", parameters={"a": f"gm-{t}", "o": org})
    for key, uid, mail, active in (
        (f"creator-{t}", "75a1b2c3d4e5f6a7" + t, f"creator-{t}@corp.example", True),
        (f"adminlogin-{t}", LOGIN_USER_ID[:-8] + t, f"old-{t}@corp.example", True),
        (f"adminstub-{t}", f"g-src-{t}", email, False),
    ):
        await q(
            "CREATE (:User {id: $k, orgId: $o, userId: $u, email: $e, isActive: $a})",
            parameters={"k": key, "o": org, "u": uid, "e": mail, "a": active},
        )


async def _neo4j_links_of(p, conn) -> list[str]:
    rows = await p.client.execute_query(
        "MATCH ()-[r:AUTHENTICATED_AS {connectorId: $c}]->(t:User) RETURN t.id AS id", parameters={"c": conn}
    )
    return sorted(r["id"] for r in rows)


async def test_arango_a_link_moved_by_a_verified_email_change_is_still_removed(arango_provider) -> None:  # noqa: F811
    await _moved_link_case(arango_provider, _arango_users, _arango_links_of)


async def test_arango_a_moved_link_keeps_its_key_so_the_next_upsert_repoints_it(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    t = uuid.uuid4().hex[:8]
    org, conn = f"org-{t}", f"gm-{t}"
    await _arango_users(p, org, t, f"admin-{t}@corp.example")
    await p.upsert_authenticated_as(f"creator-{t}", f"adminstub-{t}", conn, org)
    await p.apply_verified_user_email(LOGIN_USER_ID[:-8] + t, org, f"admin-{t}@corp.example")

    keys = await p.execute_query("FOR l IN authenticatedAs FILTER l.connectorId == @c RETURN l._key", bind_vars={"c": conn})
    assert keys == [conn]
    await p.upsert_authenticated_as(f"creator-{t}", f"adminstub-{t}", conn, org)
    assert len(await _arango_links_of(p, conn)) == 1, "one link per connector, even after a move"


async def test_neo4j_a_link_moved_by_a_verified_email_change_is_still_removed(monkeypatch) -> None:
    from tests.integration.real_graph import backend_unavailable, connect_neo4j

    try:
        provider = await connect_neo4j(logging.getLogger("wsleak-it"), monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    try:
        await _moved_link_case(provider, _neo4j_users, _neo4j_links_of)
    finally:
        await provider.disconnect()


# ---- a link that already has a random key, and a stale duplicate -------------------------------------------------
# Earlier releases copied a moved link under a random key. Such a link must be removed like any other, and a re-auth
# must not leave it pointing at the old source account beside the new link.

async def _arango_random_key_link(p, t: str, to: str, conn: str, org: str) -> None:
    await p.execute_query(
        "INSERT {_from: @f, _to: @to, connectorId: @c, orgId: @o} INTO authenticatedAs",
        bind_vars={"f": f"users/creator-{t}", "to": f"users/{to}", "c": conn, "o": org},
    )


async def test_arango_a_link_with_a_random_key_is_removed(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    t = uuid.uuid4().hex[:8]
    org, conn = f"org-{t}", f"gm-{t}"
    await _arango_users(p, org, t, f"admin-{t}@corp.example")
    await _arango_random_key_link(p, t, f"adminlogin-{t}", conn, org)
    keys = await p.execute_query("FOR l IN authenticatedAs FILTER l.connectorId == @c RETURN l._key", bind_vars={"c": conn})
    assert keys and keys != [conn], "the link has a key that is not the connector id"

    assert await p.remove_authenticated_as(conn) is True
    assert await _arango_links_of(p, conn) == []


async def test_arango_a_re_auth_leaves_one_link_not_a_stale_random_key_copy(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    t = uuid.uuid4().hex[:8]
    org, conn = f"org-{t}", f"jira-{t}"
    await _arango_users(p, org, t, f"admin-{t}@corp.example")
    await p.upsert_authenticated_as(f"creator-{t}", f"adminstub-{t}", conn, org)
    await _arango_random_key_link(p, t, f"adminlogin-{t}", conn, org)  # the copy an older absorb left

    await p.upsert_authenticated_as(f"creator-{t}", f"creator-{t}", conn, org)  # the connector is re-authenticated

    rows = await p.execute_query(
        "FOR l IN authenticatedAs FILTER l.connectorId == @c RETURN {k: l._key, to: PARSE_IDENTIFIER(l._to).key}", bind_vars={"c": conn}
    )
    assert rows == [{"k": conn, "to": f"creator-{t}"}], "one link per connector, pointing at the new account"


async def test_arango_upsert_repoints_only_its_own_connector(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    t = uuid.uuid4().hex[:8]
    org = f"org-{t}"
    await _arango_users(p, org, t, f"admin-{t}@corp.example")
    for conn in (f"a-{t}", f"b-{t}"):
        await p.upsert_authenticated_as(f"creator-{t}", f"adminstub-{t}", conn, org)
    await p.upsert_authenticated_as(f"creator-{t}", f"adminlogin-{t}", f"a-{t}", org)
    assert await _arango_links_of(p, f"a-{t}") == [f"adminlogin-{t}"]
    assert await _arango_links_of(p, f"b-{t}") == [f"adminstub-{t}"], "another connector's link is not touched"


# ---- the indexes the lookups rely on ------------------------------------------------------------------------------

async def test_arango_the_link_collection_has_an_index_on_the_connector_id(monkeypatch) -> None:
    from tests.integration.real_graph import backend_unavailable, connect_arango

    try:
        p = await connect_arango(logging.getLogger("wsleak-it"), f"wsleak_idx_{uuid.uuid4().hex[:6]}")
    except Exception as exc:
        backend_unavailable("arango", exc)
    try:
        indexes = await p.http_client.get_indexes("authenticatedAs") if hasattr(p.http_client, "get_indexes") else None
        if indexes is None:
            import aiohttp

            client = p.http_client
            async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as s:
                async with s.get(f"{client.base_url}/_db/{client.database}/_api/index", params={"collection": "authenticatedAs"}) as r:
                    indexes = (await r.json())["indexes"]
        assert any(i.get("fields") == ["connectorId"] and i.get("type") == "persistent" for i in indexes), indexes
    finally:
        await p.http_client.disconnect()


async def test_neo4j_the_link_relationship_has_an_index_on_the_connector_id(monkeypatch) -> None:
    from tests.integration.real_graph import backend_unavailable, connect_neo4j

    try:
        p = await connect_neo4j(logging.getLogger("wsleak-it"), monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    try:
        await p.ensure_schema()
        rows = await p.client.execute_query(
            "SHOW INDEXES YIELD name, entityType, labelsOrTypes, properties WHERE entityType = 'RELATIONSHIP' "
            "AND 'AUTHENTICATED_AS' IN labelsOrTypes RETURN properties"
        )
        assert ["connectorId"] in [r["properties"] for r in rows], rows
    finally:
        await p.disconnect()


async def test_arango_concurrent_reauth_keeps_one_link(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    await _ensure_arango_collections(p)
    for rnd in range(5):
        t = uuid.uuid4().hex[:8]
        org, c = f"org-{t}", f"jira-{t}"
        targets = [f"s{i}-{t}" for i in range(20)]
        for u in [f"c-{t}", *targets]:
            await p.execute_query("INSERT {_key: @u, orgId: @o} INTO users", bind_vars={"u": u, "o": org})
        await p.execute_query("INSERT {_from: @f, _to: @to, connectorId: @c, orgId: @o} INTO authenticatedAs",
                              bind_vars={"f": f"users/c-{t}", "to": f"users/{targets[0]}", "c": c, "o": org})
        await asyncio.gather(*[p.upsert_authenticated_as(f"c-{t}", s, c, org) for s in targets], return_exceptions=True)
        links = await p.execute_query("FOR l IN authenticatedAs FILTER l.connectorId == @c RETURN {k: l._key, to: l._to}", bind_vars={"c": c})
        assert len(links) == 1, f"round {rnd}: {links}"
