"""``get_app_user_links`` against a real Neo4j and a real ArangoDB.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml up -d
Run: pytest tests/integration/graph_db/test_app_user_links.py -m integration

The links are what a sync reads to find the users whose connector access it may withdraw. An account with
no organization membership (a guest account) still has its link, so it must be listed; another
organization's link or another connector's link must not be.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from .test_connector_entities import arango_provider, neo4j_provider  # noqa: F401  (fixtures)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def _ensure_arango_collections(provider) -> None:
    import aiohttp

    client = provider.http_client
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as s:
        for name, kind in (("users", 2), ("people", 2), ("apps", 2), ("userAppRelation", 3)):
            async with s.post(f"{client.base_url}/_db/{client.database}/_api/collection", json={"name": name, "type": kind}) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create collection {name}: {r.status}")


def _ids() -> dict[str, str]:
    t = uuid.uuid4().hex[:8]
    return {k: f"{k}-{t}" for k in ("orgA", "orgB", "app1", "app2", "member", "guest", "stranger", "unnamed", "otherapp", "person")}


# (user, org, connector, source id)
def _links(i: dict[str, str]) -> list[tuple[str, str, str, str | None]]:
    return [
        (i["member"], i["orgA"], i["app1"], "src-member"),
        (i["guest"], i["orgA"], i["app1"], "src-guest"),        # no organization edge
        (i["unnamed"], i["orgA"], i["app1"], None),              # a link the source names no user for
        (i["stranger"], i["orgB"], i["app1"], "src-stranger"),   # another organization
        (i["otherapp"], i["orgA"], i["app2"], "src-otherapp"),   # another connector
    ]


async def _seed_neo4j(p, i: dict[str, str]) -> None:
    q = p.client.execute_query
    await q("CREATE (:Person {id: $id, orgId: $org})", parameters={"id": i["person"], "org": i["orgA"]})
    for app in (i["app1"], i["app2"]):
        await q("CREATE (:App {id: $id, type: 'ONEDRIVE'})", parameters={"id": app})
    await q("CREATE (:Organization {id: $id})", parameters={"id": i["orgA"]})
    for user, org, app, sid in _links(i):
        await q("CREATE (:User {id: $id, orgId: $org})", parameters={"id": user, "org": org})
        await q(
            "MATCH (u:User {id: $u}), (a:App {id: $a}) CREATE (u)-[:USER_APP_RELATION {sourceUserId: $s}]->(a)",
            parameters={"u": user, "a": app, "s": sid},
        )
    await q(  # an external collaborator (a Person) linked to the connector with a source id is no user
        "MATCH (u:Person {id: $u}), (a:App {id: $a}) CREATE (u)-[:USER_APP_RELATION {sourceUserId: 'src-person'}]->(a)",
        parameters={"u": i["person"], "a": i["app1"]},
    )
    await q(
        "MATCH (u:User {id: $u}), (o:Organization {id: $o}) CREATE (u)-[:BELONGS_TO {entityType: 'ORGANIZATION'}]->(o)",
        parameters={"u": i["member"], "o": i["orgA"]},
    )


async def _seed_arango(p, i: dict[str, str]) -> None:
    await _ensure_arango_collections(p)
    q = p.http_client.execute_aql
    for app in (i["app1"], i["app2"]):
        await q("INSERT {_key: @k} INTO apps", bind_vars={"k": app})
    # an external collaborator (a Person) linked to the connector with a source id is no user
    await q("INSERT {_key: @k, orgId: @o} INTO people", bind_vars={"k": i["person"], "o": i["orgA"]})
    await q(
        "INSERT {_from: CONCAT('people/', @p), _to: CONCAT('apps/', @a), sourceUserId: 'src-person'} INTO userAppRelation",
        bind_vars={"p": i["person"], "a": i["app1"]},
    )
    for user, org, app, sid in _links(i):
        await q("INSERT {_key: @k, orgId: @o} INTO users", bind_vars={"k": user, "o": org})
        await q(
            "INSERT {_from: CONCAT('users/', @u), _to: CONCAT('apps/', @a), sourceUserId: @s} INTO userAppRelation",
            bind_vars={"u": user, "a": app, "s": sid},
        )


async def _check(p, i: dict[str, str]) -> None:
    got = {link["id"]: link["sourceUserId"] for link in await p.get_app_user_links(i["orgA"], i["app1"])}
    assert got == {i["member"]: "src-member", i["guest"]: "src-guest"}
    assert {link["id"] for link in await p.get_app_user_links(i["orgB"], i["app1"])} == {i["stranger"]}
    assert {link["id"] for link in await p.get_app_user_links(i["orgA"], i["app2"])} == {i["otherapp"]}
    assert await p.get_app_user_links(i["orgA"], "no-such-app") == []


async def test_neo4j_lists_guest_links_but_not_another_orgs_or_connectors(neo4j_provider) -> None:  # noqa: F811
    i = _ids()
    await _seed_neo4j(neo4j_provider, i)
    await _check(neo4j_provider, i)
    # the members-only read stays as it was: a guest has no organization edge
    assert {u["_key"] for u in await neo4j_provider.get_app_users(i["orgA"], i["app1"])} == {i["member"]}


async def test_arango_lists_guest_links_but_not_another_orgs_or_connectors(arango_provider) -> None:  # noqa: F811
    i = _ids()
    await _seed_arango(arango_provider, i)
    await _check(arango_provider, i)


async def test_a_failed_read_raises_on_neo4j(neo4j_provider, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(neo4j_provider.client, "execute_query", boom)
    with pytest.raises(RuntimeError, match="db down"):
        await neo4j_provider.get_app_user_links("o", "a")


async def test_a_failed_read_raises_on_arango(arango_provider, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(arango_provider.http_client, "execute_aql", boom)
    with pytest.raises(RuntimeError, match="db down"):
        await arango_provider.get_app_user_links("o", "a")


# ---------------------------------------------------------------------------
# A withdrawn link comes back through the real processor
# ---------------------------------------------------------------------------

async def _connected(connecting: Coroutine[object, object, IGraphDBProvider], env: str) -> IGraphDBProvider:
    """A store that is not configured and not reachable skips the test, as in the other integration tests here; one that is configured must be there."""
    import os

    try:
        return await connecting
    except Exception as exc:
        if os.environ.get(env):
            pytest.fail(f"{env} is set but the store is not reachable: {exc!r}")
        pytest.skip(f"no store configured ({env} unset) and none reachable locally: {exc!r}")


async def _recover(graph, user_key: str, org: str, connector: str) -> tuple[set[str], set[str], set[str]]:
    """Link a user to a connector through the processor, withdraw it, save the user again; the link ids seen at each step."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.config.constants.arangodb import Connectors
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
    from app.models.entities import AppUser

    processor = DataSourceEntitiesProcessor(logging.getLogger("recovery-it"), GraphDataStore(logging.getLogger("recovery-it"), graph), MagicMock())
    processor.org_id = org
    processor.messaging_producer = MagicMock(send_message=AsyncMock(return_value=True), send_messages=AsyncMock(return_value=[]))
    app_user = AppUser(app_name=Connectors.ONEDRIVE, connector_id=connector, source_user_id="src-recover",
                       email=f"{user_key}@example.com", full_name="Recover")

    async def links() -> set[str]:
        return {u.id for u in await processor.get_app_user_links(connector)}

    await processor.on_new_app_users([app_user])
    linked = await links()
    await processor.withdraw_app_user_access(connector, [SimpleNamespace(id=user_key)])
    withdrawn = await links()
    await processor.on_new_app_users([app_user])  # the next listing shows the user again
    return linked, withdrawn, await links()


async def test_neo4j_a_withdrawn_user_gets_the_link_back_when_the_next_listing_shows_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..test_soft_delete_e2e import _connect_neo4j

    graph = await _connected(_connect_neo4j(monkeypatch), "NEO4J_IT_URI")
    try:
        t = uuid.uuid4().hex[:8]
        org, user, connector = f"org-{t}", f"u-{t}", f"app-{t}"
        q = graph.client.execute_query
        await q("CREATE (:Organization {id: $o, isActive: true})", parameters={"o": org})
        await q("CREATE (:User {id: $u, orgId: $o, email: $e, isActive: true})", parameters={"u": user, "o": org, "e": f"{user}@example.com"})
        assert await _recover(graph, user, org, connector) == ({user}, set(), {user})
    finally:
        await graph.disconnect()


async def test_arango_a_withdrawn_user_gets_the_link_back_when_the_next_listing_shows_the_user() -> None:
    from ..test_soft_delete_e2e import _connect_arango

    graph = await _connected(_connect_arango(), "ARANGO_IT_URL")
    try:
        t = uuid.uuid4().hex[:8]
        org, user, connector = f"org-{t}", f"u-{t}", f"app-{t}"
        from app.models.entities import Org
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes([Org(id=org, name=org).to_arango_org()], collection="organizations")
        await graph.batch_upsert_nodes(
            [{"id": user, "userId": user, "orgId": org, "email": f"{user}@example.com", "fullName": "Recover",
              "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection="users",
        )
        await graph.batch_upsert_nodes(
            [{"id": connector, "name": "OneDrive", "type": "ONEDRIVE", "appGroup": "Microsoft 365", "scope": "team", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection="apps",
        )
        assert await _recover(graph, user, org, connector) == ({user}, set(), {user})
    finally:
        await graph.disconnect()


# ---------------------------------------------------------------------------
# The read honours the transaction it is given
# ---------------------------------------------------------------------------

async def _link_seen_only_inside_its_transaction(graph, user_key: str, org: str, connector: str) -> tuple[set[str], set[str]]:
    """Write a link inside an open transaction; the ids ``get_app_user_links`` returns inside it, and after it was rolled back."""
    txn = await graph.begin_transaction(read=["users", "apps", "userAppRelation"], write=["userAppRelation"], explicit=True)
    try:
        await graph.ensure_app_membership(user_key, "users", connector, is_external=False, source_user_id="src-tx", transaction=txn)
        inside = {link["id"] for link in await graph.get_app_user_links(org, connector, transaction=txn)}
    finally:
        await graph.rollback_transaction(txn)
    return inside, {link["id"] for link in await graph.get_app_user_links(org, connector)}


async def test_neo4j_a_link_written_in_an_open_transaction_is_read_inside_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..test_soft_delete_e2e import _connect_neo4j

    graph = await _connected(_connect_neo4j(monkeypatch), "NEO4J_IT_URI")
    try:
        t = uuid.uuid4().hex[:8]
        org, user, connector = f"org-{t}", f"u-{t}", f"app-{t}"
        q = graph.client.execute_query
        await q("CREATE (:App {id: $a, type: 'ONEDRIVE'})", parameters={"a": connector})
        await q("CREATE (:User {id: $u, orgId: $o, email: $e})", parameters={"u": user, "o": org, "e": f"{user}@example.com"})
        assert await _link_seen_only_inside_its_transaction(graph, user, org, connector) == ({user}, set())
    finally:
        await graph.disconnect()


async def test_arango_a_link_written_in_an_open_transaction_is_read_inside_it() -> None:
    from ..test_soft_delete_e2e import _connect_arango

    graph = await _connected(_connect_arango(), "ARANGO_IT_URL")
    try:
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

        t = uuid.uuid4().hex[:8]
        org, user, connector = f"org-{t}", f"u-{t}", f"app-{t}"
        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes(
            [{"id": user, "userId": user, "orgId": org, "email": f"{user}@example.com", "fullName": "Tx", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection="users",
        )
        await graph.batch_upsert_nodes(
            [{"id": connector, "name": "OneDrive", "type": "ONEDRIVE", "appGroup": "Microsoft 365", "scope": "team", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection="apps",
        )
        assert await _link_seen_only_inside_its_transaction(graph, user, org, connector) == ({user}, set())
    finally:
        await graph.disconnect()
