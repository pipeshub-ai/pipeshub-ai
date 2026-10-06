"""A write that fails partway leaves the graph as it was, on Neo4j 5.26 and ArangoDB 3.12.

With NEO4J_EXPLICIT_TRANSACTIONS off (the default), every Neo4j statement
commits on its own, so a write split over several statements kept its first
steps when a later one failed. ArangoDB rolls the whole transaction back. Each
write below is now one statement on Neo4j:

- a sync rewriting a user group's members, a record group's permissions or an
  app role's members: the old edges were deleted in a statement of their own,
  so a failure left the group with no members at all;
- a record upsert: the Record node committed before its type node and its
  IS_OF_TYPE edge;
- a hard delete: the type nodes went first, and the records stayed live
  without them.

Each test makes the write fail inside the database, partway through: another
transaction holds a lock that only the later part of the write needs. Neo4j
gives up on it after db.lock.acquisition.timeout, which the compose file sets
(a write already committing cannot be terminated, only timed out); ArangoDB
fails it as a write-write conflict. The permission edges have random keys on
ArangoDB, so nothing can be held there: a unique index that the second new edge
breaks fails it instead. Each test checks its setup took, that the write failed
for the reason given, and that the same write succeeds once nothing is in its way.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_partial_write_failures_real_backends.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store import (
    graph_data_store as graph_data_store_module,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("partial-write-failures-it")

NEO4J_LOCK_TIMEOUT = "LockAcquisitionTimeout"
ARANGO_CONFLICT = "1200"


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str
    ids: set[str] = field(default_factory=set)

    @property
    def neo4j(self) -> bool:
        return isinstance(self.graph, Neo4jProvider)


async def _remove(w: _World) -> None:
    await w.graph.client.execute_query(
        "MATCH (n) WHERE n.id IN $ids OR n.orgId = $org OR n.connectorId = $connector DETACH DELETE n",
        parameters={"ids": sorted(w.ids), "org": w.org_id, "connector": w.connector_id},
    )


async def _drop_database(graph: IGraphDBProvider, name: str) -> None:
    client = graph.http_client
    session = await client._get_session()
    async with session.delete(f"{client.base_url}/_api/database/{name}") as resp:
        if resp.status >= 300:
            logger.warning("Could not drop %s: %s", name, await resp.text())


async def _require_lock_timeout(graph: Neo4jProvider) -> None:
    rows = await graph.client.execute_query(
        "SHOW SETTINGS YIELD name, value WHERE name = 'db.lock.acquisition.timeout' RETURN value"
    )
    value = rows[0]["value"] if rows else "0s"
    if value in ("0s", "0", "0ms"):
        pytest.fail(
            "Neo4j waits for a lock forever (db.lock.acquisition.timeout=0); start it with "
            "NEO4J_db_lock_acquisition_timeout, as docker-compose.integration.graph-db.yml does"
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    # The default, where each Neo4j statement commits on its own.
    monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
    # The failure is the point: a retry would only wait on the same hold again.
    monkeypatch.setattr(graph_data_store_module, "_is_retryable", lambda _instance, _error: False)
    suffix = uuid.uuid4().hex[:10]
    database = f"partial_writes_it_{suffix}"
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, database)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        cleanup.push_async_callback(graph.disconnect)
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = f"org-pw-{suffix}"
        processor.messaging_producer = MagicMock(send_message=AsyncMock(), send_event=AsyncMock())
        w = _World(graph=graph, processor=processor, org_id=processor.org_id, connector_id=f"conn-pw-{suffix}")
        if w.neo4j:
            await _require_lock_timeout(graph)
            cleanup.push_async_callback(_remove, w)
        else:
            cleanup.push_async_callback(_drop_database, graph, database)
        yield w


async def _add_user(w: _World, name: str) -> tuple[str, str]:
    key = f"user-{name}-{uuid.uuid4().hex[:8]}"
    email = f"{key}@example.com"
    now = get_epoch_timestamp_in_ms()
    await w.graph.batch_upsert_nodes(
        [{"id": key, "userId": key, "orgId": w.org_id, "email": email, "fullName": name,
          "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    w.ids.add(key)
    return key, email


async def _sources(w: _World, to_id: str, to_collection: str) -> set[str]:
    """Who holds a PERMISSION edge into the node."""
    if w.neo4j:
        rows = await w.graph.client.execute_query(
            "MATCH (s)-[:PERMISSION]->(t {id: $id}) RETURN s.id AS id", parameters={"id": to_id}
        )
        return {row["id"] for row in rows or []}
    rows = await w.graph.http_client.execute_aql(
        "FOR e IN permission FILTER e._to == @to RETURN PARSE_IDENTIFIER(e._from).key",
        {"to": f"{to_collection}/{to_id}"},
    )
    return set(rows or [])


async def _typed(w: _World, record_id: str) -> tuple[bool, bool, bool]:
    """Whether the Record, its File node and the IS_OF_TYPE edge between them exist."""
    record = await w.graph.get_document(record_id, CollectionNames.RECORDS.value)
    file_doc = await w.graph.get_document(record_id, CollectionNames.FILES.value)
    if w.neo4j:
        rows = await w.graph.client.execute_query(
            "MATCH (:Record {id: $id})-[e:IS_OF_TYPE]->(:File {id: $id}) RETURN count(e) AS n",
            parameters={"id": record_id},
        )
        edge = bool(rows and rows[0]["n"])
    else:
        rows = await w.graph.http_client.execute_aql(
            "FOR e IN isOfType FILTER e._from == @from AND e._to == @to RETURN 1",
            {"from": f"records/{record_id}", "to": f"files/{record_id}"},
        )
        edge = bool(rows)
    return record is not None, file_doc is not None, edge


@contextlib.asynccontextmanager
async def _neo4j_hold(w: _World, cypher: str, parameters: dict) -> AsyncIterator[None]:
    """Hold the write locks of *cypher*, which returns ``n``, until the block ends."""
    session = w.graph.client.driver.session(database=w.graph.client.database)
    tx = await session.begin_transaction()
    try:
        record = await (await tx.run(cypher, parameters)).single()
        assert record is not None and record["n"] == 1, "the hold took nothing"
        yield
    finally:
        await tx.rollback()
        await session.close()


@contextlib.asynccontextmanager
async def _arango_hold(w: _World, collection: str, aql: str, bind_vars: dict) -> AsyncIterator[None]:
    """Write *aql*, which returns one row per write, in a transaction open until the block ends."""
    txn = await w.graph.begin_transaction(read=[], write=[collection])
    try:
        rows = await w.graph.http_client.execute_aql(aql, bind_vars, txn_id=txn)
        assert len(rows or []) == 1, "the hold took nothing"
        yield
    finally:
        await w.graph.rollback_transaction(txn)


@contextlib.asynccontextmanager
async def _arango_unique(w: _World, collection: str, fields: list[str]) -> AsyncIterator[None]:
    client = w.graph.http_client
    name = f"pw_unique_{uuid.uuid4().hex[:8]}"
    assert await client.ensure_persistent_index(collection, fields, True, name=name)
    try:
        yield
    finally:
        index = next(i for i in await client.get_indexes(collection) if i.get("name") == name)
        session = await client._get_session()
        async with session.delete(f"{client.base_url}/_db/{client.database}/_api/index/{index['id']}") as resp:
            assert resp.status < 300, await resp.text()


async def _failure(write: Callable[[], Awaitable[object]]) -> str:
    """Run *write*, which must fail, and return what it failed with."""
    try:
        result = await write()
    except Exception as exc:
        return str(exc)
    # Neo4j's recursive delete reports a failure instead of raising.
    assert isinstance(result, dict) and result.get("success") is False, result
    return str(result.get("reason"))


def _members(w: _World, *emails: str) -> list[AppUser]:
    return [
        AppUser(app_name=Connectors.GOOGLE_DRIVE, connector_id=w.connector_id, source_user_id=email,
                email=email, full_name=email)
        for email in emails
    ]


def _grants(*emails: str) -> list[Permission]:
    return [Permission(email=email, type=PermissionType.READ, entity_type=EntityType.USER) for email in emails]


class _Target:
    """One of the three syncs that rewrite the PERMISSION edges into a node."""

    def __init__(self, kind: str, w: _World) -> None:
        self.kind = kind
        self.w = w
        external = f"ext-{kind}-{uuid.uuid4().hex[:8]}"
        if kind == "user_group":
            self.node = AppUserGroup(app_name=Connectors.GOOGLE_DRIVE, connector_id=w.connector_id,
                                     source_user_group_id=external, name="Engineering")
            self.collection = CollectionNames.GROUPS.value
        elif kind == "app_role":
            self.node = AppRole(app_name=Connectors.GOOGLE_DRIVE, connector_id=w.connector_id,
                                source_role_id=external, name="Admins")
            self.collection = CollectionNames.ROLES.value
        else:
            self.node = RecordGroup(external_group_id=external, name="Shared drive",
                                    group_type=RecordGroupType.DRIVE, connector_name=Connectors.GOOGLE_DRIVE,
                                    connector_id=w.connector_id)
            self.collection = CollectionNames.RECORD_GROUPS.value

    async def sync(self, *emails: str) -> None:
        p = self.w.processor
        if self.kind == "user_group":
            await p.on_new_user_groups([(self.node, _members(self.w, *emails))])
        elif self.kind == "app_role":
            await p.on_new_app_roles([(self.node, _members(self.w, *emails))])
        else:
            await p.on_new_record_groups([(self.node, _grants(*emails))])
        self.w.ids.add(self.node.id)


@pytest.mark.parametrize("kind", ["user_group", "app_role", "record_group"])
async def test_a_failed_permission_rewrite_keeps_the_old_edges(world: _World, kind: str) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    target = _Target(kind, w)
    await target.sync(alice_email)
    assert await _sources(w, target.node.id, target.collection) == {alice}

    # Bob joins, and the write fails at his edge: after Alice's old edge is deleted.
    if w.neo4j:
        hold = _neo4j_hold(w, "MATCH (u:User {id: $id}) SET u.heldByTest = true RETURN count(u) AS n", {"id": bob})
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_unique(w, CollectionNames.PERMISSION.value, ["_to", "role"])
        expected = "unique constraint violated"
    async with hold:
        assert expected in await _failure(lambda: target.sync(alice_email, bob_email))

    assert await _sources(w, target.node.id, target.collection) == {alice}

    await target.sync(alice_email, bob_email)
    assert await _sources(w, target.node.id, target.collection) == {alice, bob}


def _file(w: _World, name: str) -> FileRecord:
    record_id = f"rec-{name}-{uuid.uuid4().hex[:8]}"
    w.ids.add(record_id)
    return FileRecord(
        id=record_id, org_id=w.org_id, record_name=f"{name}.pdf", record_type=RecordType.FILE,
        external_record_id=f"ext-{record_id}", version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE, connector_id=w.connector_id, mime_type="application/pdf",
        indexing_status=ProgressStatus.NOT_STARTED.value, is_file=True, extension="pdf",
    )


async def _upsert(w: _World, record: FileRecord) -> None:
    async with w.processor.data_store_provider.transaction() as tx_store:
        await tx_store.batch_upsert_records([record])


async def test_a_failed_record_upsert_writes_nothing(world: _World) -> None:
    w = world
    record = _file(w, "report")

    # Another writer is creating the same file node, so the upsert waits on it
    # after writing its Record node.
    if w.neo4j:
        hold = _neo4j_hold(w, "CREATE (f:File {id: $id, orgId: $org}) RETURN count(f) AS n",
                           {"id": record.id, "org": w.org_id})
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_hold(w, CollectionNames.FILES.value, "INSERT @doc INTO files RETURN 1",
                            {"doc": record.to_arango_record()})
        expected = ARANGO_CONFLICT
    async with hold:
        assert expected in await _failure(lambda: _upsert(w, record))

    assert await _typed(w, record.id) == (False, False, False)

    await _upsert(w, record)
    assert await _typed(w, record.id) == (True, True, True)


async def test_a_failed_hard_delete_keeps_records_and_their_types(world: _World) -> None:
    w = world
    group = _Target("record_group", w)
    await group.sync()
    record = _file(w, "notes")
    await _upsert(w, record)
    now = get_epoch_timestamp_in_ms()
    await w.graph.batch_create_edges(
        [{"from_id": record.id, "from_collection": CollectionNames.RECORDS.value,
          "to_id": group.node.id, "to_collection": CollectionNames.RECORD_GROUPS.value,
          "entityType": "GROUP", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.BELONGS_TO.value,
    )
    assert await _typed(w, record.id) == (True, True, True)

    async def delete() -> dict:
        return await w.processor.on_records_deleted_cascade([record.id], w.connector_id, soft_delete=False)

    # A sync is writing to the record's group on Neo4j, whose lock only the delete of
    # the record itself needs (for its BELONGS_TO edge), not the delete of its type node.
    if w.neo4j:
        hold = _neo4j_hold(
            w, "MATCH (g:RecordGroup {id: $id}) SET g.heldByTest = true RETURN count(g) AS n",
            {"id": group.node.id},
        )
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_hold(w, CollectionNames.RECORDS.value,
                            "UPDATE @key WITH {updatedAtTimestamp: @now} IN records RETURN 1",
                            {"key": record.id, "now": now + 1})
        # The conflict on the record is logged by the batch helper, which goes on.
        expected = "Could not delete 1 batch(es) of records"
    async with hold:
        assert expected in await _failure(delete)

    assert await _typed(w, record.id) == (True, True, True)

    result = await delete()
    assert result["successfully_deleted"] == 1, result
    assert await _typed(w, record.id) == (False, False, False)
