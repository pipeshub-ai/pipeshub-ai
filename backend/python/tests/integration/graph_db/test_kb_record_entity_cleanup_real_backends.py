"""Deleting a knowledge-base record removes the entity points of the taxonomy
entities only it linked to, against a real Neo4j 5.26 and a real ArangoDB 3.12.

The unit tests pin the query text and the call order; only a server shows that
the record's links are read before its edges go, and which entities still count
as reached:

- another record of the same knowledge base, a record of another knowledge
  base or of a connector, and a copy of the record (a duplicate keeps its own
  copied edges) each keep an entity;
- a record in the trash, or a record of another org, does not;
- the trash purge names the purged record's entities the same way.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_kb_record_entity_cleanup_real_backends.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD. Naming them here is
also what puts this file in the backend-matrix graph jobs, which find their tests by these names.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.unit.modules.transformers.test_entity_vectorstore_delete import (
    _Entities,
    _entity_point,
    _store_over,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("kb-record-entity-cleanup-it")
ARANGO_DB = "kb_record_entity_cleanup_it"
RECORDS = CollectionNames.RECORDS.value
EDGES = {
    CollectionNames.TOPICS.value: CollectionNames.BELONGS_TO_TOPIC.value,
    CollectionNames.CATEGORIES.value: CollectionNames.BELONGS_TO_CATEGORY.value,
    CollectionNames.SUBCATEGORIES2.value: CollectionNames.BELONGS_TO_CATEGORY.value,
    CollectionNames.LANGUAGES.value: CollectionNames.BELONGS_TO_LANGUAGE.value,
    CollectionNames.DEPARTMENTS.value: CollectionNames.BELONGS_TO_DEPARTMENT.value,
}
TYPES = {
    CollectionNames.TOPICS.value: "topic",
    CollectionNames.CATEGORIES.value: "category",
    CollectionNames.SUBCATEGORIES2.value: "subcategory",
    CollectionNames.LANGUAGES.value: "language",
    CollectionNames.DEPARTMENTS.value: "department",
}


@dataclass
class _Env:
    graph: IGraphDBProvider
    suffix: str
    org_id: str
    other_org: str

    def key(self, name: str) -> str:
        return f"{name}-{self.suffix}"


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    try:
        if request.param == "neo4j":
            graph = await connect_neo4j(logger, monkeypatch)
        else:
            graph = await connect_arango(logger, ARANGO_DB)
    except Exception as exc:
        backend_unavailable(request.param, exc)
    suffix = uuid.uuid4().hex[:10]
    environment = _Env(graph=graph, suffix=suffix, org_id=f"org-kbe-{suffix}", other_org=f"org2-kbe-{suffix}")
    try:
        yield environment
    finally:
        with contextlib.suppress(Exception):
            await _remove_test_data(environment)
        await graph.disconnect()


async def _remove_test_data(env: _Env) -> None:
    graph = env.graph
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            # A global department has no org; its key carries the suffix.
            "MATCH (n) WHERE n.orgId IN $orgs OR n.id ENDS WITH $s DETACH DELETE n",
            parameters={"orgs": [env.org_id, env.other_org], "s": env.suffix},
        )
        return
    for edges in set(EDGES.values()):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER CONTAINS(e._from, @s) OR CONTAINS(e._to, @s) REMOVE e IN {edges}",
            {"s": env.suffix},
        )
    for collection in (*EDGES, RECORDS, CollectionNames.FILES.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER CONTAINS(d._key, @s) REMOVE d IN {collection}", {"s": env.suffix},
        )


async def _record(env: _Env, name: str, *, connector: str, org: str | None = None, **fields: object) -> str:
    now = get_epoch_timestamp_in_ms()
    key = env.key(name)
    is_kb = connector.startswith("kb")
    await env.graph.batch_upsert_nodes([{
        "id": key, "orgId": org or env.org_id, "recordName": name, "externalRecordId": f"ext-{key}",
        "recordType": "FILE", "origin": "UPLOAD" if is_kb else "CONNECTOR",
        "connectorName": "KB" if is_kb else "DRIVE", "connectorId": env.key(connector),
        "recordGroupId": env.key(f"{connector}-group"), "version": 0,
        "virtualRecordId": f"vr-{key}", "indexingStatus": "COMPLETED", "isDeleted": False,
        "createdAtTimestamp": now, "updatedAtTimestamp": now, **fields,
    }], collection=RECORDS)
    return key


async def _node(env: _Env, collection: str, name: str, *, org: str | None = None) -> str:
    key = env.key(name)
    if collection == CollectionNames.DEPARTMENTS.value:
        # Departments are the org's list (or global, with no org), not extracted names.
        global_department = name.startswith("global-")
        await env.graph.batch_upsert_nodes(
            [{"id": key, "departmentName": name, "orgId": None if global_department else org or env.org_id}],
            collection=collection,
        )
        return key
    await env.graph.create_taxonomy_node_if_absent(collection, {
        "id": key, "name": name, "normalizedName": name.casefold(), "orgId": org or env.org_id,
        "createdAtTimestamp": 1,
    })
    return key


async def _link(env: _Env, record: str, collection: str, node: str) -> None:
    await env.graph.batch_create_edges([{
        "from_id": record, "from_collection": RECORDS, "to_id": node, "to_collection": collection,
        "createdAtTimestamp": 1,
    }], collection=EDGES[collection])


@dataclass
class _Seeded:
    deleted: str
    kept: set[tuple[str, str]]
    gone: set[tuple[str, str]]

    @property
    def refs(self) -> list[dict[str, str]]:
        return [{"id": i, "type": t} for t, i in sorted(self.kept | self.gone)]


async def _seed(env: _Env) -> _Seeded:
    """One knowledge-base record linked to twelve entities, each shared (or
    not) in one of the ways a delete has to tell apart."""
    doc = await _record(env, "doc", connector="kb-a")
    sibling = await _record(env, "sibling", connector="kb-a")
    copy = await _record(env, "copy", connector="kb-b", md5Checksum="same", virtualRecordId=f"vr-{doc}")
    drive = await _record(env, "drive", connector="conn-c")
    trashed = await _record(env, "trashed", connector="kb-a", isDeleted=True, deletedAtTimestamp=1)
    elsewhere = await _record(env, "elsewhere", connector="kb-z", org=env.other_org)

    topics, cats = CollectionNames.TOPICS.value, CollectionNames.CATEGORIES.value
    subs, langs = CollectionNames.SUBCATEGORIES2.value, CollectionNames.LANGUAGES.value
    depts = CollectionNames.DEPARTMENTS.value
    shares = {
        (topics, "shared-with-connector"): [drive],
        (topics, "shared-in-kb"): [sibling],
        (topics, "shared-with-copy"): [copy],
        (topics, "only-trash-left"): [trashed],
        (topics, "other-org-link"): [elsewhere],
        (topics, "only-this-doc"): [],
        (cats, "only-cat"): [],
        (subs, "shared-sub"): [sibling],
        (langs, "only-lang"): [],
        (depts, "dept-shared-with-connector"): [drive],
        (depts, "dept-only-this-doc"): [],
        # A global department's point is per org: another org's record keeps nothing here.
        (depts, "global-dept-other-org-link"): [elsewhere],
    }
    kept, gone = set(), set()
    for (collection, name), others in shares.items():
        node = await _node(env, collection, name)
        for record in (doc, *others):
            await _link(env, record, collection, node)
        reached = any(other in (drive, sibling, copy) for other in others)
        (kept if reached else gone).add((TYPES[collection], node))
    return _Seeded(deleted=doc, kept=kept, gone=gone)


async def test_a_deleted_kb_record_names_every_entity_it_linked_to(env: _Env) -> None:
    seeded = await _seed(env)

    result = await env.graph.delete_records_recursive([seeded.deleted], env.key("kb-a"))

    assert result["success"] is True, result
    (payload,) = result["eventData"]["payloads"]
    assert payload["recordId"] == seeded.deleted
    assert sorted((r["type"], r["id"]) for r in payload["taxonomyRefs"]) == sorted(seeded.kept | seeded.gone)
    # Read before the delete: the edges are gone now.
    assert await env.graph.get_record_taxonomy_links([seeded.deleted]) == []


async def test_only_entities_no_live_record_of_the_org_reaches_are_unreached(env: _Env) -> None:
    seeded = await _seed(env)
    await env.graph.delete_records_recursive([seeded.deleted], env.key("kb-a"))

    unreached = await env.graph.get_unreached_taxonomy_entities(seeded.refs, env.org_id)

    assert unreached == seeded.gone


async def test_an_entity_still_linked_by_the_record_itself_is_reached(env: _Env) -> None:
    seeded = await _seed(env)

    assert await env.graph.get_unreached_taxonomy_entities(seeded.refs, env.org_id) == set()


async def test_a_node_that_is_gone_is_unreached(env: _Env) -> None:
    missing = {"id": env.key("never-created"), "type": "topic"}

    assert await env.graph.get_unreached_taxonomy_entities([missing], env.org_id) == {
        ("topic", env.key("never-created")),
    }


async def test_the_entity_points_of_the_unreached_go_and_the_rest_stay(env: _Env) -> None:
    """The indexing side end to end: the refs from the delete, checked against
    the real graph under the store's locks."""
    seeded = await _seed(env)
    entities = _Entities(*(
        _entity_point(entity_id, entity_type, [env.key("kb-a")], [env.key("kb-a-group")], org=env.org_id)
        for entity_type, entity_id in seeded.kept | seeded.gone
    ))
    store: EntityVectorStore = _store_over(entities)
    result = await env.graph.delete_records_recursive([seeded.deleted], env.key("kb-a"))
    (payload,) = result["eventData"]["payloads"]

    await store.delete_unreached_entities(
        env.org_id,
        payload["taxonomyRefs"],
        lambda refs: env.graph.get_unreached_taxonomy_entities(refs, env.org_id),
    )

    assert set(entities.points) == {f"{t}:{i}" for t, i in seeded.kept}


async def test_the_trash_purge_names_the_purged_records_entities(env: _Env) -> None:
    seeded = await _seed(env)
    await env.graph.batch_upsert_nodes(
        [{"id": seeded.deleted, "isDeleted": True, "deletedAtTimestamp": 1}], collection=RECORDS,
    )

    result = await env.graph.purge_trashed_records([seeded.deleted], env.org_id, get_epoch_timestamp_in_ms())

    (purged,) = result["purged"]
    refs = purged["deleteRecordPayload"]["taxonomyRefs"]
    assert sorted((r["type"], r["id"]) for r in refs) == sorted(seeded.kept | seeded.gone)
    assert await env.graph.get_unreached_taxonomy_entities(refs, env.org_id) == seeded.gone


async def test_a_connector_record_delete_names_no_entities(env: _Env) -> None:
    drive = await _record(env, "drive-only", connector="conn-c")
    node = await _node(env, CollectionNames.TOPICS.value, "drive-topic")
    await _link(env, drive, CollectionNames.TOPICS.value, node)

    result = await env.graph.delete_records_recursive([drive], env.key("conn-c"))

    (payload,) = result["eventData"]["payloads"]
    assert "taxonomyRefs" not in payload


async def test_a_copy_not_reconciled_yet_does_not_keep_an_entity(env: _Env) -> None:
    """A duplicate parked QUEUED has no copied edges until its reconcile, which
    then writes the point again (see test_sink_orchestrator's
    ``test_taxonomy_rows_carry_duplicates_membership``)."""
    doc = await _record(env, "primary", connector="kb-a")
    await _record(env, "queued-copy", connector="kb-b", indexingStatus="QUEUED", virtualRecordId=f"vr-{doc}")
    node = await _node(env, CollectionNames.TOPICS.value, "primary-topic")
    await _link(env, doc, CollectionNames.TOPICS.value, node)

    result = await env.graph.delete_records_recursive([doc], env.key("kb-a"))

    (payload,) = result["eventData"]["payloads"]
    assert await env.graph.get_unreached_taxonomy_entities(payload["taxonomyRefs"], env.org_id) == {
        ("topic", node),
    }
