"""Against real servers (KG-13 slice 1): the backfill command links a mail
to its member sender and recipients through the real typed-record read and
entity-relation write, on Neo4j 5.26 and ArangoDB 3.12, and a second run
leaves the same edges.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_people_real_backends.py -m integration
"""
from __future__ import annotations

import io
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import MailRecord, RecordType, TicketRecord
from app.scripts.kg_record_people import backfill
from tests.integration.graph_db.test_record_graph_write_concurrency import (
    _open_arango,
    _open_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("record-people-it")


@pytest.fixture(params=["arango", "neo4j"])
async def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Any, str]]:
    try:
        provider = await (_open_arango() if request.param == "arango" else _open_neo4j(monkeypatch, explicit=False))
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    org = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org
    finally:
        await _cleanup(provider, org)


async def _cleanup(provider: Neo4jProvider | ArangoHTTPProvider, org: str) -> None:
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    if isinstance(provider, Neo4jProvider):
        await provider.client.execute_query("MATCH (n) WHERE n.orgId = $org DETACH DELETE n", parameters={"org": org})
        await provider.disconnect()
        return
    aql = provider.http_client.execute_aql
    for edges in (CollectionNames.ENTITY_RELATIONS.value, CollectionNames.IS_OF_TYPE.value):
        await aql(f"FOR e IN {edges} FILTER CONTAINS(e._from, @org) REMOVE e IN {edges}", {"org": org})
    for docs in (CollectionNames.RECORDS.value, CollectionNames.MAILS.value, CollectionNames.TICKETS.value, CollectionNames.USERS.value):
        await aql(f"FOR d IN {docs} FILTER d.orgId == @org REMOVE d IN {docs}", {"org": org})


async def _seed(provider: Neo4jProvider | ArangoHTTPProvider, org: str) -> str:
    users = [
        {"id": f"{org}-ann", "userId": f"{org}-ann", "orgId": org, "email": f"ann@{org}.test", "fullName": "Ann"},
        {"id": f"{org}-bob", "userId": f"{org}-bob", "orgId": org, "email": f"bob@{org}.test", "fullName": "Bob"},
    ]
    await provider.batch_upsert_nodes(users, CollectionNames.USERS.value)
    mail_id = f"{org}-mail"
    await provider.batch_upsert_records([MailRecord(
        id=mail_id, org_id=org, external_record_id=mail_id, record_name="Quarterly plan",
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_MAIL, connector_id=f"{org}-conn",
        record_type=RecordType.MAIL, version=1, source_created_at=1000, source_updated_at=1000,
        indexing_status="COMPLETED",
        from_email=f"Ann <ann@{org}.test>", to_emails=['"Lee', f'Bob" <bob@{org}.test>', "stranger@else.test"],
        bcc_emails=[f"ann@{org}.test"],
    ), TicketRecord(
        id=f"{org}-case", org_id=org, external_record_id=f"{org}-case", record_name="Case 1",
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_MAIL, connector_id=f"{org}-conn",
        record_type=RecordType.CASE, version=1, source_created_at=1000, source_updated_at=1000,
        indexing_status="COMPLETED",
        assignee_email=f"bob@{org}.test",
    )])
    return mail_id


async def _edges(provider: Neo4jProvider | ArangoHTTPProvider, mail_id: str) -> list[tuple[str, str]]:
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    if isinstance(provider, Neo4jProvider):
        rows = await provider.client.execute_query(
            "MATCH (:Record {id: $id})-[r]->(u:User) WHERE r.edgeType IS NOT NULL RETURN u.id AS u, r.edgeType AS t",
            parameters={"id": mail_id},
        )
        return sorted((r["u"], r["t"]) for r in rows)
    rows = await provider.http_client.execute_aql(
        "FOR e IN entityRelations FILTER e._from == @f RETURN [PARSE_IDENTIFIER(e._to).key, e.edgeType]",
        {"f": f"records/{mail_id}"},
    )
    return sorted(tuple(r) for r in rows)


async def test_backfill_links_member_sender_and_recipients_once(backend) -> None:
    provider, org = backend
    await provider.ensure_schema()
    mail_id = await _seed(provider, org)
    store = GraphDataStore(logger, provider)

    dry = io.StringIO()
    await backfill(provider, store, org, apply=False, logger=logger, out=dry)
    assert await _edges(provider, mail_id) == []
    assert '"edges": 3' in dry.getvalue()

    for _ in range(2):
        code = await backfill(provider, store, org, apply=True, logger=logger, out=io.StringIO())
        assert code == 0
        assert await _edges(provider, mail_id) == [(f"{org}-ann", "AUTHORED_BY"), (f"{org}-bob", "ADDRESSED_TO")]
        # A Salesforce CASE is a ticket too.
        assert await _edges(provider, f"{org}-case") == [(f"{org}-bob", "ASSIGNED_TO")]


async def test_a_person_lists_the_records_naming_them_in_either_direction(backend) -> None:
    """KG-13 slice 2: a member's records are the ones linked to them by any
    entityRelations edge (record -> user from record_people, user -> record
    from Slack mentions), within the org and the asked connectors."""
    provider, org = backend
    await provider.ensure_schema()
    mail_id = await _seed(provider, org)
    store = GraphDataStore(logger, provider)
    await backfill(provider, store, org, apply=True, logger=logger, out=io.StringIO())
    await provider.batch_create_entity_relations([{
        "_from": f"users/{org}-bob", "_to": f"records/{org}-case", "edgeType": "MENTIONED_IN", "createdAtTimestamp": 1,
    }])

    ref = {"id": f"{org}-bob", "type": "person", "connectorIds": [f"{org}-conn"]}
    rows = (await provider.get_entity_candidate_records([ref], org))[("person", f"{org}-bob")]
    assert sorted(r["_key"] for r in rows) == sorted([mail_id, f"{org}-case"])

    other_org = (await provider.get_entity_candidate_records([ref], f"{org}-x"))[("person", f"{org}-bob")]
    assert other_org == []

    permitted = (await provider.get_permitted_entity_records(
        [ref], org, f"{org}-ann", app_level_connector_ids=[f"{org}-conn"],
    ))[("person", f"{org}-bob")]
    assert sorted(r["_key"] for r in permitted) == sorted([mail_id, f"{org}-case"])

    people = await provider.get_record_people(f"{org}-case", org)
    assert [(p["id"], p["email"]) for p in people] == [(f"{org}-bob", f"bob@{org}.test")]
    assert await provider.get_record_people(f"{org}-case", f"{org}-x") == []


async def test_the_rebuild_reads_people_and_their_membership(backend) -> None:
    """The B-1 rebuild projects people already linked before slice 2: users
    are paged per org, and a person's connectors and groups come from the
    records linked to them in either direction."""
    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)
    await provider.batch_upsert_nodes([{
        "id": f"{org}-cat", "userId": f"{org}-cat", "orgId": org, "email": f"cat@{org}.test",
    }], CollectionNames.USERS.value)
    await backfill(provider, GraphDataStore(logger, provider), org, apply=True, logger=logger, out=io.StringIO())
    await provider.batch_create_entity_relations([{
        "_from": f"users/{org}-cat", "_to": f"records/{org}-case", "edgeType": "MENTIONED_IN", "createdAtTimestamp": 1,
    }])

    rows = await provider.page_entity_index_source(CollectionNames.USERS.value, org, None, 10)
    assert [(r["_key"], r["name"]) for r in rows] == [
        (f"{org}-ann", "Ann"), (f"{org}-bob", "Bob"), (f"{org}-cat", f"cat@{org}.test"),
    ]
    assert await provider.page_entity_index_source(CollectionNames.USERS.value, org, f"{org}-ann", 10) == rows[1:]

    refs = [{"id": f"{org}-{u}", "type": "person"} for u in ("ann", "bob", "cat")]
    membership = await provider.get_taxonomy_entity_membership(refs, org)
    assert all(membership[("person", r["id"])]["connectorIds"] == [f"{org}-conn"] for r in refs)
    elsewhere = await provider.get_taxonomy_entity_membership(refs, f"{org}-x")
    assert all(not m["connectorIds"] for m in elsewhere.values())
