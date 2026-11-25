"""P2.13 PD-1..PD-8, RS-10: knowledge publications on a real Neo4j and a real ArangoDB.

Requires the servers of ``tests/integration/compose/graph-db.yml`` (or any Neo4j 5 and ArangoDB 3.12 at
``NEO4J_TEST_URI`` / ``ARANGO_TEST_URL``); each backend is skipped while its variable is unset.
Run: NEO4J_TEST_URI=bolt://... ARANGO_TEST_URL=http://... pytest tests/integration/graph_db/test_knowledge_publications.py -m integration

Everything is seeded and read through ``IGraphDBProvider``'s own methods, so what passes here is what the
retrieval scope sees on each backend: the directory's node and edge, a knowledge base's records (``apps``), a
record group's records (``belongsTo``, ``isOfType``), ownership edges and team membership.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.harness.knowledge.directory import (
    PUBLICATIONS,
    PUBLISHED_VIA,
    GraphPublicationDirectory,
)
from app.harness.knowledge.service import Caller, KnowledgePublications
from harness_core.knowledge.errors import PublicationForbidden
from harness_core.knowledge.publications import Audience, PublicationDraft
from harness_core.testing.contracts.publications import PublicationDirectoryContract
from tests.support import it_backends

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

C = CollectionNames
NOW = 1_800_000_000_000


def _log():  # noqa: ANN202
    from app.utils.logger import create_logger

    return create_logger("graph_db_publications_test")


@pytest.fixture(scope="module")
async def neo4j_provider() -> AsyncIterator[IGraphDBProvider]:
    pytest.importorskip("neo4j", reason="neo4j driver not installed")
    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    uri = it_backends.endpoint("NEO4J_TEST_URI")
    password = os.environ.get("NEO4J_TEST_PASSWORD", "testpassword")
    client = Neo4jClient(uri=uri, username="neo4j", password=password, database="neo4j", logger=_log())
    try:
        if not await client.connect():
            pytest.skip(f"Neo4j not available at {uri}")
    except Exception as exc:
        pytest.skip(f"Neo4j not available at {uri} — {exc}")
    provider = Neo4jProvider(logger=_log(), config_service=MagicMock())
    provider.client = client
    yield provider
    await client.disconnect()


@pytest.fixture(scope="module")
async def arango_provider() -> AsyncIterator[IGraphDBProvider]:
    pytest.importorskip("aiohttp", reason="aiohttp not installed")
    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    url = it_backends.endpoint("ARANGO_TEST_URL")
    password = os.environ.get("ARANGO_TEST_PASSWORD", "testpassword")
    db = f"p213_{uuid.uuid4().hex[:8]}"
    system = ArangoHTTPClient(base_url=url, username="root", password=password, database="_system", logger=_log())
    try:
        if not await system.connect() or not await system.create_database(db):
            pytest.skip(f"ArangoDB not available at {url}")
    except Exception as exc:
        pytest.skip(f"ArangoDB not available at {url} — {exc}")
    provider = ArangoHTTPProvider(logger=_log(), config_service=MagicMock())
    provider.http_client = ArangoHTTPClient(base_url=url, username="root", password=password, database=db, logger=_log())
    # The production path: every collection, with its schema and indexes, as a service creates them at startup.
    assert await provider.ensure_schema()
    yield provider
    await provider.http_client.disconnect()
    # The shared host's disk is small: every run's database goes with it.
    session = await system._get_session()  # pyright: ignore[reportPrivateUsage]
    async with session.delete(f"{url}/_api/database/{db}"):
        pass
    await system.disconnect()


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> IGraphDBProvider:
    return request.getfixturevalue(f"{request.param}_provider")


def _hex() -> str:
    return uuid.uuid4().hex[:12]


async def _record_group(graph: IGraphDBProvider, org: str, key: str, connector: str) -> None:
    doc = {
        "id": key, "orgId": org, "groupName": key, "groupType": "DRIVE", "connectorName": "DRIVE",
        "connectorId": connector, "createdAtTimestamp": 1,
    }
    assert await graph.batch_upsert_nodes([doc], C.RECORD_GROUPS.value) is not False


async def _record(graph: IGraphDBProvider, org: str, connector: str, *, group: str | None = None) -> tuple[str, str]:
    rid, vid = f"r-{_hex()}", f"v-{_hex()}"
    record = {
        "id": rid, "orgId": org, "recordName": rid, "externalRecordId": rid, "recordType": "FILE",
        "origin": "CONNECTOR", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "connectorId": connector, "virtualRecordId": vid,
        "isDeleted": False, "indexingStatus": "COMPLETED", "version": 1,
    }
    assert await graph.batch_upsert_nodes([record], C.RECORDS.value) is not False
    assert await graph.batch_upsert_nodes([{"id": rid, "name": rid, "isFile": True}], C.FILES.value) is not False
    type_edge = {"from_id": rid, "from_collection": C.RECORDS.value, "to_id": rid, "to_collection": C.FILES.value, "createdAtTimestamp": 1}
    assert await graph.batch_create_edges([type_edge], C.IS_OF_TYPE.value)
    if group is not None:
        belongs = {"from_id": rid, "from_collection": C.RECORDS.value, "to_id": group, "to_collection": C.RECORD_GROUPS.value, "createdAtTimestamp": 1}
        assert await graph.batch_create_edges([belongs], C.BELONGS_TO.value)
    return vid, rid


async def _org_with_kb(graph: IGraphDBProvider) -> tuple[str, str]:
    org, kb = f"org-{_hex()}", f"kb-{_hex()}"
    assert await graph.batch_upsert_nodes([{"id": org, "accountType": "enterprise", "isActive": True}], C.ORGS.value) is not False
    app = {"id": kb, "name": "KB", "type": "KB", "appGroup": "Local Storage", "scope": "team", "isActive": True, "createdAtTimestamp": 1}
    assert await graph.batch_upsert_nodes([app], C.APPS.value) is not False
    edge = {"from_id": org, "from_collection": C.ORGS.value, "to_id": kb, "to_collection": C.APPS.value, "createdAtTimestamp": 1}
    assert await graph.batch_create_edges([edge], C.ORG_APP_RELATION.value)
    return org, kb


async def _user(graph: IGraphDBProvider, org: str) -> tuple[str, str]:
    key, user_id = f"uk-{_hex()}", f"u-{_hex()}"
    doc = {"id": key, "userId": user_id, "orgId": org, "email": f"{user_id}@example.com", "isActive": True}
    assert await graph.batch_upsert_nodes([doc], C.USERS.value) is not False
    return key, user_id


async def _grant(graph: IGraphDBProvider, user_key: str, collection: str, key: str, role: str) -> None:
    edge = {
        "from_id": user_key, "from_collection": C.USERS.value, "to_id": key, "to_collection": collection,
        "type": "USER", "role": role, "createdAtTimestamp": 1,
    }
    assert await graph.batch_create_edges([edge], C.PERMISSION.value)


class _OnBoth(PublicationDirectoryContract):
    @pytest.fixture
    async def containers(self, provider: IGraphDBProvider) -> tuple[str, str, str]:
        keys = [f"pd-{_hex()}" for _ in range(3)]
        for key in keys:
            await _record_group(provider, "org-pd", key, "conn-pd")
        return tuple(f"{C.RECORD_GROUPS.value}/{key}" for key in keys)  # type: ignore[return-value]

    @pytest.fixture
    def directory(self, provider: IGraphDBProvider) -> GraphPublicationDirectory:
        return GraphPublicationDirectory(provider)


class TestGraphPublicationDirectory(_OnBoth):
    async def test_pd8_the_edge_goes_from_the_container_and_goes_with_erasure(
        self, provider: IGraphDBProvider, directory: GraphPublicationDirectory, containers: tuple[str, str, str]
    ) -> None:
        org, pid = f"org-{_hex()}", _hex()
        key = containers[0].split("/", 1)[1]
        draft = PublicationDraft(org, containers[0], Audience(org=True), "full", "u-owner")
        await directory.create(draft, publication_id=pid, now_ms=NOW)
        assert await provider.get_edge(key, C.RECORD_GROUPS.value, pid, PUBLICATIONS, PUBLISHED_VIA) is not None
        assert await directory.erase_container(org, containers[0]) == 1
        assert await provider.get_edge(key, C.RECORD_GROUPS.value, pid, PUBLICATIONS, PUBLISHED_VIA) is None
        assert await provider.get_document(pid, PUBLICATIONS) is None


def _service(graph: IGraphDBProvider, now: int = NOW) -> KnowledgePublications:
    service = KnowledgePublications(graph, MagicMock(), now_ms=lambda: now)

    async def on() -> bool:
        return True

    service.enabled = on  # type: ignore[method-assign]
    return service


async def test_rs10_published_records_resolve_alike_on_both_backends(provider: IGraphDBProvider) -> None:
    org, kb = await _org_with_kb(provider)
    group = f"rg-{_hex()}"
    await _record_group(provider, org, group, f"conn-{_hex()}")
    rg_doc = await provider.get_document(group, C.RECORD_GROUPS.value)
    assert rg_doc is not None
    connector = rg_doc["connectorId"]
    kb_records = dict([await _record(provider, org, kb) for _ in range(3)])
    rg_records = dict([await _record(provider, org, connector, group=group) for _ in range(2)])
    _ = await _record(provider, org, connector)  # the same connector, outside the group: never published
    owner_key, owner = await _user(provider, org)
    await _grant(provider, owner_key, C.APPS.value, kb, "OWNER")
    await _grant(provider, owner_key, C.RECORD_GROUPS.value, group, "OWNER")
    _, alice = await _user(provider, org)
    _, bob = await _user(provider, org)
    service = _service(provider)
    caller = Caller(owner, org, is_admin=False)
    await service.create(caller, PublicationDraft(org, f"apps/{kb}", Audience(user_ids=frozenset({alice})), "answer_only", owner))
    await service.create(caller, PublicationDraft(org, f"recordGroups/{group}", Audience(user_ids=frozenset({alice})), "full", owner, NOW + 1_000))

    alice_p = await service.principal(alice, org)
    bob_p = await service.principal(bob, org)
    assert alice_p is not None
    assert bob_p is not None
    published = await service.published_map(alice_p)
    assert {vid: rid for vid, (rid, _) in published.items()} == {**kb_records, **rg_records}
    assert {published[v][1].mode for v in kb_records} == {"answer_only"}
    assert {published[v][1].mode for v in rg_records} == {"full"}
    assert await service.published_map(bob_p) == {}
    # The grant narrows: a search scoped to the knowledge base admits only its records.
    assert set(await service.published_map(alice_p, {"kb": [kb]})) == set(kb_records)

    some_kb, some_rg = next(iter(kb_records.values())), next(iter(rg_records.values()))
    assert await service.read_decision(alice, org, some_kb, own=False) == "published_answer_only"
    assert await service.read_decision(alice, org, some_rg, own=False) == "published_full"
    assert await service.read_decision(bob, org, some_rg, own=False) == "denied"

    # Revocation and expiry act at the next read.
    for publication in await service.directory.list_for_container(org, f"apps/{kb}"):
        await service.revoke(caller, publication.id)
    assert set(await service.published_map(alice_p)) == set(rg_records)
    assert await _service(provider, now=NOW + 1_000).published_map(alice_p) == {}


async def test_rt1_ownership_is_read_from_the_graph(provider: IGraphDBProvider) -> None:
    org, kb = await _org_with_kb(provider)
    writer_key, writer = await _user(provider, org)
    await _grant(provider, writer_key, C.APPS.value, kb, "WRITER")
    service = _service(provider)
    with pytest.raises(PublicationForbidden):
        await service.create(Caller(writer, org, is_admin=False), PublicationDraft(org, f"apps/{kb}", Audience(org=True), "full", writer))
    created = await service.create(Caller(writer, org, is_admin=True), PublicationDraft(org, f"apps/{kb}", Audience(org=True), "full", writer))
    assert created.container_id == f"apps/{kb}"
    # A knowledge base of another org is absent for this org.
    other, _ = await _org_with_kb(provider)
    assert await service.container(other, f"apps/{kb}") is None


async def test_rs9_a_publication_of_a_deleted_container_grants_nothing(provider: IGraphDBProvider) -> None:
    org, kb = await _org_with_kb(provider)
    _ = await _record(provider, org, kb)
    _, alice = await _user(provider, org)
    service = _service(provider)
    await service.directory.create(PublicationDraft(org, f"apps/{kb}", Audience(org=True), "full", "u-owner"), publication_id=_hex(), now_ms=NOW)
    principal = await service.principal(alice, org)
    assert principal is not None
    assert len(await service.published_map(principal)) == 1
    await provider.delete_nodes([kb], C.APPS.value)
    assert await service.published_map(principal) == {}

