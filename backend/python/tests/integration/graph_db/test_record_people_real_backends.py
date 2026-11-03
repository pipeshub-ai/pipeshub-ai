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


async def _cleanup(provider: Neo4jProvider | ArangoHTTPProvider, org: str, *, disconnect: bool = True) -> None:
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    if isinstance(provider, Neo4jProvider):
        await provider.client.execute_query(
            "MATCH (n) WHERE n.orgId = $org OR (n:Organization AND n.id = $org) DETACH DELETE n",
            parameters={"org": org},
        )
        if disconnect:
            await provider.disconnect()
        return
    aql = provider.http_client.execute_aql
    for edges in (CollectionNames.ENTITY_RELATIONS.value, CollectionNames.IS_OF_TYPE.value, CollectionNames.PERMISSION.value):
        await aql(f"FOR e IN {edges} FILTER CONTAINS(e._from, @org) REMOVE e IN {edges}", {"org": org})
    await aql(f"REMOVE {{_key: @org}} IN {CollectionNames.ORGS.value} OPTIONS {{ignoreErrors: true}}", {"org": org})
    for docs in (
        CollectionNames.RECORDS.value, CollectionNames.MAILS.value, CollectionNames.TICKETS.value,
        CollectionNames.USERS.value, CollectionNames.PEOPLE.value,
    ):
        await aql(f"FOR d IN {docs} FILTER d.orgId == @org REMOVE d IN {docs}", {"org": org})


async def _seed(provider: Neo4jProvider | ArangoHTTPProvider, org: str) -> str:
    users = [
        {"id": f"{org}-ann", "userId": f"{org}-ann", "orgId": org, "email": f"ann@{org}.test", "fullName": "Ann"},
        {"id": f"{org}-bob", "userId": f"{org}-bob", "orgId": org, "email": f"bob@{org}.test", "fullName": "Bob"},
    ]
    await provider.batch_upsert_nodes(users, CollectionNames.USERS.value)
    await provider.batch_upsert_nodes(
        [{"id": org, "accountType": "enterprise", "isActive": True, "entityIndexState": "v2:fp"}],
        CollectionNames.ORGS.value,
    )
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
        # The org pass re-runs, so the entity index projects these people.
        (org_doc,) = await provider.get_nodes_by_field_in(
            CollectionNames.ORGS.value, "id", [org], return_fields=["entityIndexState"],
        )
        assert org_doc["entityIndexState"] is None


async def test_neo4j_backfill_pages_walk_the_id_order_without_sorting(backend) -> None:
    """Review: each page filtered the org then sorted all of its records by
    id, so a large org paid its whole size on every page. An (orgId, id)
    index serves the order; the plan must not sort."""
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
    from tests.integration.graph_db.test_entity_graph_real_backends import (
        _capture_queries,
        _profile,
    )

    provider, org = backend
    if not isinstance(provider, Neo4jProvider):
        pytest.skip("Neo4j only")
    await provider.ensure_schema()
    await _seed(provider, org)
    captured = _capture_queries(provider)
    first = await provider.page_record_ids_by_type(org, ["MAIL", "TICKET"], limit=1)
    await provider.page_record_ids_by_type(org, ["MAIL", "TICKET"], after_key=first[0], limit=1)
    pages = [(q, p) for q, p in captured if "LIMIT $limit" in q and "record.recordType IN $types" in q]
    assert len(pages) == 2
    for query, parameters in pages:
        plan = await _profile(provider, query, parameters)
        assert not [op for op in plan["operators"] if "Sort" in op or "Top" in op], plan["operators"]


async def test_arango_backfill_pages_seek_the_orgs_keys_without_sorting(backend) -> None:
    """Review: no index served "this org's records in key order", so a page
    either sorted the org's records or walked every tenant's keys. An
    (orgId, _key) index, hinted, gives the order within the org."""
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    provider, org = backend
    if not isinstance(provider, ArangoHTTPProvider):
        pytest.skip("ArangoDB only")
    await provider.ensure_schema()
    await _seed(provider, org)
    sent: list[dict[str, Any]] = []
    execute = provider.http_client.execute_aql

    async def capture(query, bind_vars=None, txn_id=None, **kwargs):  # noqa: ANN202
        if "LIMIT @limit" in query and "@types" in query:
            sent.append({"query": query, "bindVars": bind_vars or {}})
        return await execute(query, bind_vars=bind_vars, txn_id=txn_id, **kwargs)

    provider.http_client.execute_aql = capture
    try:
        first = await provider.page_record_ids_by_type(org, ["MAIL", "TICKET"], limit=1)
        await provider.page_record_ids_by_type(org, ["MAIL", "TICKET"], after_key=first[0], limit=1)
    finally:
        provider.http_client.execute_aql = execute
    assert len(sent) == 2
    client = provider.http_client
    session = await client._get_session()
    for request in sent:
        async with session.post(f"{client.base_url}/_db/{client.database}/_api/explain", json=request) as resp:
            nodes = (await resp.json())["plan"]["nodes"]
        types = [n["type"] for n in nodes]
        assert "SortNode" not in types, types
        (index_node,) = [n for n in nodes if n["type"] == "IndexNode"]
        assert [i["fields"] for i in index_node["indexes"]] == [["orgId", "_key"]]


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

    # A record-level connector where the viewer holds no permission on any
    # record naming the person shows nothing.
    unpermitted = (await provider.get_permitted_entity_records(
        [ref], org, f"{org}-ann", app_level_connector_ids=[],
    ))[("person", f"{org}-bob")]
    assert unpermitted == []

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
        "id": f"{org}-cat", "userId": f"{org}-cat", "orgId": org, "email": f"cat@{org}.test", "fullName": "",
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


async def test_a_user_of_another_org_never_surfaces_through_this_orgs_records(backend) -> None:
    """The user gate, not only the record filter: the record is this org's,
    the linked user is not."""
    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)
    outsider = f"{org}-out"
    await provider.batch_upsert_nodes([{
        "id": outsider, "userId": outsider, "orgId": f"{org}-x", "email": f"out@{org}.test", "fullName": "Out",
    }], CollectionNames.USERS.value)
    await provider.batch_create_entity_relations([{
        "_from": f"records/{org}-case", "_to": f"users/{outsider}", "edgeType": "ASSIGNED_TO", "createdAtTimestamp": 1,
    }])
    try:
        ref = {"id": outsider, "type": "person", "connectorIds": [f"{org}-conn"]}
        assert (await provider.get_entity_candidate_records([ref], org))[("person", outsider)] == []
        permitted = await provider.get_permitted_entity_records(
            [ref], org, f"{org}-ann", app_level_connector_ids=[f"{org}-conn"],
        )
        assert permitted[("person", outsider)] == []
        membership = await provider.get_taxonomy_entity_membership([{"id": outsider, "type": "person"}], org)
        assert membership[("person", outsider)] == {"connectorIds": [], "recordGroupIds": []}
        assert await provider.get_record_people(f"{org}-case", org) == []
    finally:
        await _cleanup(provider, f"{org}-x", disconnect=False)


async def test_a_person_is_reachable_only_through_records_the_viewer_may_read(backend) -> None:
    """People are visible only through records a user can open: Bob is named
    by a mail and a case; a viewer granted the mail reaches Bob through it
    alone, and a viewer with no grant reaches nothing, so cannot even learn
    that Bob is named anywhere."""
    provider, org = backend
    await provider.ensure_schema()
    mail_id = await _seed(provider, org)
    store = GraphDataStore(logger, provider)
    await backfill(provider, store, org, apply=True, logger=logger, out=io.StringIO())
    reader, stranger = f"{org}-reader", f"{org}-stranger"
    await provider.batch_upsert_nodes([
        {"id": reader, "userId": reader, "orgId": org, "email": f"reader@{org}.test", "isActive": True},
        {"id": stranger, "userId": stranger, "orgId": org, "email": f"stranger@{org}.test", "isActive": True},
    ], CollectionNames.USERS.value)
    await provider.batch_create_edges([{
        "from_id": reader, "from_collection": CollectionNames.USERS.value,
        "to_id": mail_id, "to_collection": CollectionNames.RECORDS.value,
        "type": "USER", "role": "READER",
    }], collection=CollectionNames.PERMISSION.value)

    ref = {"id": f"{org}-bob", "type": "person", "connectorIds": [f"{org}-conn"]}
    seen = await provider.get_permitted_entity_records([ref], org, reader, app_level_connector_ids=[])
    assert [r["_key"] for r in seen[("person", f"{org}-bob")]] == [mail_id]
    unseen = await provider.get_permitted_entity_records([ref], org, stranger, app_level_connector_ids=[])
    assert list(unseen[("person", f"{org}-bob")]) == []


class TestPeopleWithoutAnEmail:
    """A person a source names only by its own user id (a Jira account id) is
    keyed by (org, source key); several can exist in one org, and the same
    key always resolves to the same node."""

    async def test_source_keyed_people_coexist_and_upsert_to_one_node(self, backend) -> None:
        from app.models.entities import Person

        provider, org = backend
        await provider.ensure_schema()
        first = await provider.upsert_person_by_source_key(
            Person(source_key="conn-1:acc-1", org_id=org, full_name="Ann"), raise_on_error=True,
        )
        second = await provider.upsert_person_by_source_key(
            Person(source_key="conn-1:acc-2", org_id=org, full_name="Bob"), raise_on_error=True,
        )
        again = await provider.upsert_person_by_source_key(
            Person(source_key="conn-1:acc-1", org_id=org, full_name="Renamed"), raise_on_error=True,
        )
        assert first and second and first != second
        assert again == first

    async def test_email_keyed_people_stay_unique_per_org(self, backend) -> None:
        from app.models.entities import Person

        provider, org = backend
        await provider.ensure_schema()
        first = await provider.upsert_person_by_email(Person(email="Eve@Partner.com", org_id=org), raise_on_error=True)
        again = await provider.upsert_person_by_email(Person(email="eve@partner.com", org_id=org), raise_on_error=True)
        assert first and again == first


async def test_arango_keeps_no_index_that_counts_a_missing_email(backend) -> None:
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    provider, _ = backend
    if not isinstance(provider, ArangoHTTPProvider):
        pytest.skip("ArangoDB only")
    await provider.ensure_schema()
    indexes = await provider.http_client.get_indexes(CollectionNames.PEOPLE.value)
    by_fields = {tuple(i.get("fields", [])): i for i in indexes if i.get("type") == "persistent"}
    assert by_fields[("orgId", "email")]["unique"] and by_fields[("orgId", "email")]["sparse"]
    assert by_fields[("orgId", "sourceKey")]["unique"] and by_fields[("orgId", "sourceKey")]["sparse"]
    assert not [i for i in indexes if i.get("fields") == ["orgId", "email"] and not i.get("sparse")]


async def test_a_file_links_its_author_and_last_editor_even_when_they_are_not_members(backend) -> None:
    """Authorship: a file names a member as owner, an outside author by email
    and an editor known only by a source id; the outsiders get person nodes,
    and a second run writes the same edges to the same nodes."""
    from app.connectors.core.base.data_processor.record_people import link_record_people
    from app.models.entities import FileRecord, SourcePerson

    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)
    record = FileRecord(
        id=f"{org}-file", org_id=org, external_record_id=f"{org}-file", record_name="plan.pdf",
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=f"{org}-conn",
        record_type=RecordType.FILE, version=1, source_created_at=1000, source_updated_at=2000,
        is_file=True, extension="pdf",
        authored_by=SourcePerson(email="eve@partner.test", display_name="Eve"),
        last_modified_by=SourcePerson(source_id="acc-9", display_name="Finn"),
        owners=[SourcePerson(email=f"ann@{org}.test")],
    )
    await provider.batch_upsert_records([record])
    store = GraphDataStore(logger, provider)

    async def _targets() -> list[tuple[str, str, str]]:
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        if isinstance(provider, Neo4jProvider):
            rows = await provider.client.execute_query(
                "MATCH (:Record {id: $id})-[r]->(n) WHERE r.edgeType IS NOT NULL "
                "RETURN labels(n)[0] AS kind, coalesce(n.email, n.sourceKey) AS who, r.edgeType AS t",
                parameters={"id": record.id},
            )
            return sorted((r["kind"], r["who"], r["t"]) for r in rows)
        rows = await provider.http_client.execute_aql(
            "FOR e IN entityRelations FILTER e._from == @f LET n = DOCUMENT(e._to) "
            "RETURN [PARSE_IDENTIFIER(e._to).collection, n.email || n.sourceKey, e.edgeType]",
            {"f": f"records/{record.id}"},
        )
        return sorted(tuple(r) for r in rows)

    for _ in range(2):
        async with store.transaction() as tx:
            assert await link_record_people(record, tx, logger) == 3
        kinds = {"users": "User", "person": "Person"}
        got = [(kinds.get(k, k), who, t) for k, who, t in await _targets()]
        assert got == [
            ("Person", "eve@partner.test", "AUTHORED_BY"),
            ("Person", f"{org}-conn:acc-9", "LAST_MODIFIED_BY"),
            ("User", f"ann@{org}.test", "OWNED_BY"),
        ]


async def test_an_author_who_signs_up_keeps_their_documents(backend) -> None:
    """A person promoted to a user (they joined) takes the records naming
    them along; the person node goes, and no edge is left pointing at it."""
    from app.connectors.core.base.data_processor.record_people import link_record_people
    from app.models.entities import FileRecord, SourcePerson

    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)
    record = FileRecord(
        id=f"{org}-file", org_id=org, external_record_id=f"{org}-file", record_name="plan.pdf",
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=f"{org}-conn",
        record_type=RecordType.FILE, version=1, source_created_at=1000, source_updated_at=2000,
        is_file=True, extension="pdf", authored_by=SourcePerson(email=f"eve@{org}.test"),
    )
    await provider.batch_upsert_records([record])
    store = GraphDataStore(logger, provider)
    async with store.transaction() as tx:
        assert await link_record_people(record, tx, logger) == 1

    await provider.batch_upsert_nodes([{
        "id": f"{org}-eve", "userId": f"{org}-eve", "orgId": org, "email": f"eve@{org}.test", "fullName": "Eve",
    }], CollectionNames.USERS.value)
    await provider.migrate_person_to_user(f"eve@{org}.test", f"{org}-eve", org)

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    if isinstance(provider, Neo4jProvider):
        rows = await provider.client.execute_query(
            "MATCH (:Record {id: $id})-[r]->(n) WHERE r.edgeType IS NOT NULL "
            "RETURN labels(n)[0] AS kind, n.id AS id, r.edgeType AS t",
            parameters={"id": record.id},
        )
        got = [(r["kind"], r["id"], r["t"]) for r in rows]
        left = await provider.client.execute_query(
            "MATCH (p:Person {orgId: $org, email: $email}) RETURN count(p) AS n",
            parameters={"org": org, "email": f"eve@{org}.test"},
        )
        assert left[0]["n"] == 0
    else:
        rows = await provider.http_client.execute_aql(
            "FOR e IN entityRelations FILTER e._from == @f RETURN [PARSE_IDENTIFIER(e._to).collection, "
            "PARSE_IDENTIFIER(e._to).key, e.edgeType, DOCUMENT(e._to) != null]",
            {"f": f"records/{record.id}"},
        )
        assert all(r[3] for r in rows), "an edge points at a removed node"
        got = [("User" if r[0] == "users" else r[0], r[1], r[2]) for r in rows]
    assert got == [("User", f"{org}-eve", "AUTHORED_BY")]


async def test_two_open_syncs_naming_the_same_outsider_both_link_them(backend) -> None:
    """An existing person is only read, so two transactions in flight at once
    do not collide on its lock and both records keep their author edge."""
    from app.connectors.core.base.data_processor.record_people import link_record_people
    from app.models.entities import FileRecord, Person, SourcePerson

    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)
    person_id = await provider.upsert_person_by_email(Person(email="eve@partner.test", org_id=org), raise_on_error=True)
    records = [
        FileRecord(
            id=f"{org}-file-{i}", org_id=org, external_record_id=f"{org}-file-{i}", record_name=f"f{i}.pdf",
            origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=f"{org}-conn",
            record_type=RecordType.FILE, version=1, source_created_at=1000, source_updated_at=2000,
            is_file=True, extension="pdf", authored_by=SourcePerson(email="eve@partner.test"),
        )
        for i in range(2)
    ]
    await provider.batch_upsert_records(records)
    store = GraphDataStore(logger, provider)
    async with store.transaction() as first, store.transaction() as second:
        assert await link_record_people(records[0], first, logger) == 1
        assert await link_record_people(records[1], second, logger) == 1

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    for record in records:
        if isinstance(provider, Neo4jProvider):
            rows = await provider.client.execute_query(
                "MATCH (:Record {id: $id})-[r]->(p:Person) RETURN p.id AS id", parameters={"id": record.id},
            )
            assert [r["id"] for r in rows] == [person_id]
        else:
            rows = await provider.http_client.execute_aql(
                "FOR e IN entityRelations FILTER e._from == @f RETURN e._to", {"f": f"records/{record.id}"},
            )
            assert rows == [f"person/{person_id}"]


async def test_a_collaborator_membership_with_a_source_id_is_never_read_as_a_user(backend) -> None:
    """People hold app membership edges too (external collaborators). A source-id
    lookup must only ever return a member: a Person read as a User would link a
    record to users/<person key>, a node that does not exist."""
    from app.models.entities import Person

    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)
    app = f"{org}-conn"
    await provider.batch_upsert_nodes(
        [{"id": app, "name": app, "type": "Jira", "appGroup": "Atlassian", "authType": "OAUTH", "scope": "team",
          "orgId": org, "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
        collection=CollectionNames.APPS.value,
    )
    person = await provider.upsert_person_by_email(
        Person(email=f"guest@{org}.test", org_id=org, full_name="Guest"), raise_on_error=True,
    )
    try:
        await provider.ensure_app_membership(
            person, CollectionNames.PEOPLE.value, app, is_external=True, source_user_id="src-guest",
        )
        await provider.ensure_app_membership(
            f"{org}-ann", CollectionNames.USERS.value, app, is_external=False, source_user_id="src-ann",
        )

        assert await provider.get_user_by_source_id("src-guest", app) is None
        member = await provider.get_user_by_source_id("src-ann", app)
        assert member is not None and member.id == f"{org}-ann"
    finally:
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        if not isinstance(provider, Neo4jProvider):
            aql = provider.http_client.execute_aql
            edges = CollectionNames.USER_APP_RELATION.value
            await aql(f"FOR e IN {edges} FILTER e._to == @app REMOVE e IN {edges}", {"app": f"apps/{app}"})
            await aql(f"REMOVE {{_key: @app}} IN {CollectionNames.APPS.value} OPTIONS {{ignoreErrors: true}}", {"app": app})
