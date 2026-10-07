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
  without them;
- a record's permission rewrite: the old edges, the new ones and the
  inherit-permissions edge were three writes, so a failure on the last left a
  file its drive should no longer read still readable through the drive;
- a record changing record group: it left the old group before it lost that
  group's inherit-permissions edge, with the same result;
- a permission upgrade: the old edge was deleted before the new one was written;
- a move inside a knowledge base: the old parent edge was deleted, the record
  rewritten and the new parent edge created in three writes, so a failure left
  the item, and everything beneath it, in no folder at all.

A knowledge-base move into a folder in the trash is refused too, with a 409 that
says so, whether the folder was in the trash when the move was checked or went
there before it was written; nothing moves. Creating a folder in it, or uploading
to it, is refused the same way.

A failed permission write is also raised now on both stores, where it used to
be logged and the surrounding write committed without the permissions. On
ArangoDB the same goes for a failed delete of a record's inherit-permissions or
belongs-to edge, or of a user's permission on a record, which was answered with
False: the rest committed, or the caller was told the permission was gone.

Taking a user out of a user group, and deleting a user group or an app role,
raise too when the delete fails. They used to answer False, which for a member
removal is also the answer for "was not a member", so the user kept the group's
access and the connector moved on.

Looking up who a permission is for raises as well when the lookup fails. It used
to answer "nobody by that email", and a rewrite then replaced the record's
permissions without them. A read cannot be made to fail on a live database, so
the test here fails the write half of the lookup (creating the Person for an
outside email); the reads are covered by the unit tests.

Each test makes the write fail inside the database, partway through: another
transaction holds a lock that only the later part of the write needs. Neo4j
gives up on it after db.lock.acquisition.timeout, which the compose file sets
(a write already committing cannot be terminated, only timed out); ArangoDB
fails it as a write-write conflict. The permission edges have random keys on
ArangoDB, so nothing can be held there: a unique index that the second new edge
breaks fails it instead. A single edge has no later part to hold, so its upgrade
is given a value that both stores refuse to write. Each test checks its setup
took, that the write failed for the reason given, and that the same write
succeeds once nothing is in its way.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_partial_write_failures_real_backends.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
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
    DeleteSource,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.neo4j import collection_to_label
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as processor_module,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store import (
    graph_data_store as graph_data_store_module,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    Person,
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
        processor.messaging_producer = MagicMock(
            send_message=AsyncMock(),
            send_event=AsyncMock(),
            send_messages=AsyncMock(side_effect=lambda _topic, messages: [True] * len(messages)),
        )
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


def _file(w: _World, name: str, group: _Target | None = None) -> FileRecord:
    record_id = f"rec-{name}-{uuid.uuid4().hex[:8]}"
    w.ids.add(record_id)
    return FileRecord(
        id=record_id, org_id=w.org_id, record_name=f"{name}.pdf", record_type=RecordType.FILE,
        external_record_id=f"ext-{record_id}", version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE, connector_id=w.connector_id, mime_type="application/pdf",
        indexing_status=ProgressStatus.NOT_STARTED.value, is_file=True, extension="pdf",
        external_record_group_id=group.node.external_group_id if group else None,
        record_group_type=RecordGroupType.DRIVE if group else None,
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
        # The batch helper re-raises the conflict as it came, so the delete is retried
        # while the hold lasts and then fails with ArangoDB's own error.
        expected = ARANGO_CONFLICT
    async with hold:
        assert expected in await _failure(delete)

    assert await _typed(w, record.id) == (True, True, True)

    result = await delete()
    assert result["successfully_deleted"] == 1, result
    assert await _typed(w, record.id) == (False, False, False)


async def _add_app(w: _World, *users: str) -> None:
    """The connector's app and the users who have it: a connector's record is read only through it."""
    now = get_epoch_timestamp_in_ms()
    await w.graph.batch_upsert_nodes(
        [{"id": w.connector_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace",
          "scope": "team", "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now,
          "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    await w.graph.batch_create_edges(
        [{"from_id": user, "from_collection": CollectionNames.USERS.value, "to_id": w.connector_id,
          "to_collection": CollectionNames.APPS.value, "syncState": "COMPLETED", "lastSyncUpdate": now,
          "createdAtTimestamp": now, "updatedAtTimestamp": now} for user in users],
        collection=CollectionNames.USER_APP_RELATION.value,
    )


async def _readers(w: _World, record_id: str, *users: str) -> set[str]:
    """Which of the users the product's own permission check lets read the record."""
    return {
        user for user in users
        if record_id in await w.graph.filter_accessible_record_ids([record_id], user, w.org_id)
    }


async def _links(w: _World, record_id: str, group: _Target) -> set[str]:
    """The edge collections that hold an edge from the record to the record group."""
    if w.neo4j:
        rows = await w.graph.client.execute_query(
            "MATCH (:Record {id: $record})-[e]->(:RecordGroup {id: $group}) RETURN type(e) AS type",
            parameters={"record": record_id, "group": group.node.id},
        )
        names = {"BELONGS_TO": CollectionNames.BELONGS_TO.value,
                 "INHERIT_PERMISSIONS": CollectionNames.INHERIT_PERMISSIONS.value}
        return {names[row["type"]] for row in rows or []}
    rows = await w.graph.http_client.execute_aql(
        "FOR name IN APPEND("
        "(FOR e IN belongsTo FILTER e._from == @from AND e._to == @to RETURN 'belongsTo'), "
        "(FOR e IN inheritPermissions FILTER e._from == @from AND e._to == @to RETURN 'inheritPermissions')) "
        "RETURN name",
        {"from": f"records/{record_id}", "to": f"recordGroups/{group.node.id}"},
    )
    return set(rows or [])


def _holding_group(w: _World, group: _Target) -> contextlib.AbstractAsyncContextManager[None]:
    """Another sync is writing to the record group, so no edge to it can be written or removed."""
    return _neo4j_hold(
        w, "MATCH (g:RecordGroup {id: $id}) SET g.heldByTest = true RETURN count(g) AS n", {"id": group.node.id}
    )


def _holding_inherit_edge(w: _World, record_id: str, group: _Target) -> contextlib.AbstractAsyncContextManager[None]:
    """Another ArangoDB writer holds the record's inherit-permissions edge to the group, and nothing else."""
    return _arango_hold(
        w, CollectionNames.INHERIT_PERMISSIONS.value,
        "FOR e IN inheritPermissions FILTER e._from == @from AND e._to == @to "
        "UPDATE e WITH {heldByTest: true} IN inheritPermissions RETURN 1",
        {"from": f"records/{record_id}", "to": f"recordGroups/{group.node.id}"},
    )


BELONGS_AND_INHERITS = {CollectionNames.BELONGS_TO.value, CollectionNames.INHERIT_PERMISSIONS.value}


async def test_a_failed_record_permission_rewrite_leaves_no_stale_access(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    carol, carol_email = await _add_user(w, "carol")
    await _add_app(w, alice, bob, carol)
    drive = _Target("record_group", w)
    await drive.sync(alice_email)

    # Alice reads the file through its drive, Bob through a share of his own.
    record = _file(w, "plan", drive)
    await w.processor.on_updated_record_permissions(record, _grants(bob_email))
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {bob}
    assert await _links(w, record.id, drive) == BELONGS_AND_INHERITS
    assert await _readers(w, record.id, alice, bob, carol) == {alice, bob}

    # The file stops inheriting from the drive and is shared with Carol alone. The
    # write fails on the inherit edge, after Bob's edge is deleted and Carol's written.
    private = record.model_copy(update={"inherit_permissions": False})
    if w.neo4j:
        hold = _holding_group(w, drive)
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _holding_inherit_edge(w, record.id, drive)
        expected = ARANGO_CONFLICT
    async with hold:
        assert expected in await _failure(
            lambda: w.processor.on_updated_record_permissions(private, _grants(carol_email))
        )

    # Nothing of it stayed: not Carol's edge beside the drive's access.
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {bob}
    assert await _links(w, record.id, drive) == BELONGS_AND_INHERITS
    assert await _readers(w, record.id, alice, bob, carol) == {alice, bob}

    await w.processor.on_updated_record_permissions(private, _grants(carol_email))
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {carol}
    assert await _links(w, record.id, drive) == {CollectionNames.BELONGS_TO.value}
    assert await _readers(w, record.id, alice, bob, carol) == {carol}


async def test_a_record_that_stops_inheriting_does_so_when_its_group_is_not_found(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    await _add_app(w, alice, bob)
    drive = _Target("record_group", w)
    await drive.sync(alice_email)
    record = _file(w, "memo", drive)
    await w.processor.on_updated_record_permissions(record, _grants(bob_email))
    assert await _links(w, record.id, drive) == BELONGS_AND_INHERITS
    assert await _readers(w, record.id, alice, bob) == {alice, bob}

    # The source now names a drive the graph does not have, so there is no group
    # to delete the inherit edge to; the edge to the old drive must still go.
    private = record.model_copy(
        update={"inherit_permissions": False, "external_record_group_id": f"ext-unknown-{uuid.uuid4().hex[:8]}"}
    )
    await w.processor.on_updated_record_permissions(private, _grants(bob_email))

    assert await _links(w, record.id, drive) == {CollectionNames.BELONGS_TO.value}
    assert await _readers(w, record.id, alice, bob) == {bob}


async def test_a_failed_permission_removal_is_raised_and_the_access_stays_until_it_succeeds(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    await _add_app(w, alice, bob)
    record = _file(w, "minutes")
    await _upsert(w, record)
    await w.processor.add_permission_to_record(record, _grants(alice_email, bob_email))
    assert await _readers(w, record.id, alice, bob) == {alice, bob}

    def remove() -> Awaitable[None]:
        return w.processor.delete_permission_from_record(record.id, bob_email)

    # Bob loses the file at the source, and his edge cannot be deleted.
    if w.neo4j:
        hold = _neo4j_hold(w, "MATCH (u:User {id: $id}) SET u.heldByTest = true RETURN count(u) AS n", {"id": bob})
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_hold(
            w, CollectionNames.PERMISSION.value,
            "FOR e IN permission FILTER e._from == @from AND e._to == @to "
            "UPDATE e WITH {heldByTest: true} IN permission RETURN 1",
            {"from": f"users/{bob}", "to": f"records/{record.id}"},
        )
        expected = ARANGO_CONFLICT
    async with hold:
        # Raised, so the caller knows Bob still has it.
        assert expected in await _failure(remove)

    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice, bob}
    assert await _readers(w, record.id, alice, bob) == {alice, bob}

    await remove()
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice}
    assert await _readers(w, record.id, alice, bob) == {alice}


async def _share_with(w: _World, target: _Target, record: FileRecord) -> None:
    """Let the user group or app role read the record."""
    entity_type = EntityType.GROUP if target.kind == "user_group" else EntityType.ROLE
    await w.graph.batch_create_edges(
        [Permission(type=PermissionType.READ, entity_type=entity_type).to_arango_permission(
            target.node.id, target.collection, record.id, CollectionNames.RECORDS.value)],
        collection=CollectionNames.PERMISSION.value,
    )


async def test_a_failed_group_member_removal_is_raised_and_the_access_stays_until_it_succeeds(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    await _add_app(w, alice, bob)
    team = _Target("user_group", w)
    await team.sync(alice_email, bob_email)
    record = _file(w, "handbook")
    await _upsert(w, record)
    await _share_with(w, team, record)
    assert await _sources(w, team.node.id, team.collection) == {alice, bob}
    assert await _readers(w, record.id, alice, bob) == {alice, bob}

    def remove() -> Awaitable[bool]:
        return w.processor.on_user_group_member_removed(team.node.source_user_group_id, bob_email, w.connector_id)

    # Bob leaves the group at the source, and his membership edge cannot be deleted.
    if w.neo4j:
        hold = _neo4j_hold(w, "MATCH (u:User {id: $id}) SET u.heldByTest = true RETURN count(u) AS n", {"id": bob})
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_hold(
            w, CollectionNames.PERMISSION.value,
            "FOR e IN permission FILTER e._from == @from AND e._to == @to "
            "UPDATE e WITH {heldByTest: true} IN permission RETURN 1",
            {"from": f"users/{bob}", "to": f"groups/{team.node.id}"},
        )
        expected = ARANGO_CONFLICT
    async with hold:
        # Raised, so the connector knows Bob is still in the group. False would
        # have read as "he was not a member".
        assert expected in await _failure(remove)

    assert await _sources(w, team.node.id, team.collection) == {alice, bob}
    assert await _readers(w, record.id, alice, bob) == {alice, bob}

    assert await remove() is True
    assert await _sources(w, team.node.id, team.collection) == {alice}
    assert await _readers(w, record.id, alice, bob) == {alice}

    # Not a member any more: nothing to remove is an answer, not a failure.
    assert await remove() is False


@pytest.mark.parametrize("kind", ["user_group", "app_role"])
async def test_a_failed_group_or_role_deletion_is_raised_and_the_access_stays_until_it_succeeds(
    world: _World, kind: str
) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    await _add_app(w, alice)
    target = _Target(kind, w)
    await target.sync(alice_email)
    record = _file(w, "runbook")
    await _upsert(w, record)
    await _share_with(w, target, record)
    assert await _readers(w, record.id, alice) == {alice}

    def delete() -> Awaitable[bool]:
        if kind == "user_group":
            return w.processor.on_user_group_deleted(target.node.source_user_group_id, w.connector_id)
        return w.processor.on_app_role_deleted(target.node.source_role_id, w.connector_id)

    # The source deleted it while another sync is writing to it.
    if w.neo4j:
        hold = _neo4j_hold(
            w, f"MATCH (n:{collection_to_label(target.collection)} {{id: $id}}) SET n.heldByTest = true RETURN count(n) AS n",
            {"id": target.node.id},
        )
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_hold(
            w, target.collection, f"UPDATE @key WITH {{updatedAtTimestamp: @now}} IN {target.collection} RETURN 1",
            {"key": target.node.id, "now": get_epoch_timestamp_in_ms()},
        )
        expected = ARANGO_CONFLICT
    async with hold:
        assert expected in await _failure(delete)

    # Still there with its member and its share, as the caller was told.
    assert await w.graph.get_document(target.node.id, target.collection) is not None
    assert await _sources(w, target.node.id, target.collection) == {alice}
    assert await _readers(w, record.id, alice) == {alice}

    assert await delete() is True
    assert await w.graph.get_document(target.node.id, target.collection) is None
    assert await _readers(w, record.id, alice) == set()


async def test_a_rewrite_whose_principal_lookup_fails_replaces_nothing(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    await _add_app(w, alice, bob)
    record = _file(w, "proposal")
    await w.processor.on_updated_record_permissions(record, _grants(alice_email, bob_email))
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice, bob}

    # The file is now shared with Alice and someone outside the workspace, whose
    # Person another sync is creating at this moment, so it cannot be created here.
    outside_email = f"partner-{uuid.uuid4().hex[:8]}@elsewhere.example"
    being_created = Person(email=outside_email, org_id=w.org_id)
    if w.neo4j:
        hold = _neo4j_hold(
            w, "CREATE (p:Person {id: $id, email: $email, orgId: $org}) RETURN count(p) AS n",
            {"id": being_created.id, "email": outside_email, "org": w.org_id},
        )
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_hold(w, CollectionNames.PEOPLE.value,
                            f"INSERT @doc INTO {CollectionNames.PEOPLE.value} RETURN 1",
                            {"doc": being_created.to_arango_person()})
        expected = ARANGO_CONFLICT
    rewrite = _grants(alice_email, outside_email)
    async with hold:
        assert expected in await _failure(lambda: w.processor.on_updated_record_permissions(record, rewrite))

    # Bob is still there: the rewrite did not go ahead without the outsider.
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice, bob}
    assert await _readers(w, record.id, alice, bob) == {alice, bob}

    await w.processor.on_updated_record_permissions(record, rewrite)
    outsider = await w.graph.get_person_by_email(outside_email, w.org_id, raise_on_error=True)
    assert outsider is not None
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice, outsider.id}
    assert await _readers(w, record.id, alice, bob) == {alice}


async def test_a_failed_permission_write_is_raised(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    record = _file(w, "budget")
    await _upsert(w, record)
    await w.processor.add_permission_to_record(record, _grants(alice_email))
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice}

    if w.neo4j:
        hold = _neo4j_hold(w, "MATCH (u:User {id: $id}) SET u.heldByTest = true RETURN count(u) AS n", {"id": bob})
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_unique(w, CollectionNames.PERMISSION.value, ["_to", "role"])
        expected = "unique constraint violated"
    async with hold:
        assert expected in await _failure(lambda: w.processor.add_permission_to_record(record, _grants(bob_email)))

    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice}

    await w.processor.add_permission_to_record(record, _grants(bob_email))
    assert await _sources(w, record.id, CollectionNames.RECORDS.value) == {alice, bob}


async def test_a_failed_move_between_record_groups_keeps_the_record_in_its_old_group(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    bob, bob_email = await _add_user(w, "bob")
    await _add_app(w, alice, bob)
    old_drive, new_drive = _Target("record_group", w), _Target("record_group", w)
    await old_drive.sync(alice_email)
    await new_drive.sync(bob_email)
    record = _file(w, "roadmap", old_drive)
    await w.processor.on_new_records([(record, [])])
    assert await _links(w, record.id, old_drive) == BELONGS_AND_INHERITS
    assert await _readers(w, record.id, alice, bob) == {alice}

    # The source moves the file to Bob's drive. On Neo4j the write fails on joining
    # that drive, after the file has left the old one; a lock on the old drive's
    # inherit edge alone would stop the first delete too. On ArangoDB it fails on
    # that inherit edge, after the file's membership of the old drive is deleted.
    moved = _file(w, "roadmap", new_drive).model_copy(
        update={"id": record.id, "external_record_id": record.external_record_id}
    )
    if w.neo4j:
        hold = _holding_group(w, new_drive)
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _holding_inherit_edge(w, record.id, old_drive)
        expected = ARANGO_CONFLICT
    async with hold:
        assert expected in await _failure(lambda: w.processor.on_new_records([(moved, [])]))

    assert await _links(w, record.id, old_drive) == BELONGS_AND_INHERITS
    assert await _links(w, record.id, new_drive) == set()
    assert await _readers(w, record.id, alice, bob) == {alice}

    await w.processor.on_new_records([(moved, [])])
    assert await _links(w, record.id, old_drive) == set()
    assert await _links(w, record.id, new_drive) == BELONGS_AND_INHERITS
    assert await _readers(w, record.id, alice, bob) == {bob}


async def _roles(w: _World, user: str, to_id: str) -> list[str]:
    """The role on each PERMISSION edge from the user to the node."""
    if w.neo4j:
        rows = await w.graph.client.execute_query(
            "MATCH (:User {id: $user})-[e:PERMISSION]->({id: $to}) RETURN e.role AS role",
            parameters={"user": user, "to": to_id},
        )
        return [row["role"] for row in rows or []]
    return await w.graph.http_client.execute_aql(
        "FOR e IN permission FILTER e._from == @from AND PARSE_IDENTIFIER(e._to).key == @to RETURN e.role",
        {"from": f"users/{user}", "to": to_id},
    ) or []


async def test_a_failed_permission_upgrade_keeps_the_old_permission(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    record = _file(w, "contract")
    await _upsert(w, record)
    await w.processor.add_permission_to_record(record, _grants(alice_email))
    assert await _roles(w, alice, record.id) == ["READER"]

    def upgrade(permission: Permission) -> Awaitable[object]:
        return w.processor.upsert_permission_edge(
            alice, CollectionNames.USERS.value, record.id, CollectionNames.RECORDS.value, permission
        )

    # Neither store can write this edge: Neo4j takes no map as a property value,
    # and ArangoDB's schema wants a number.
    refused = Permission.model_construct(
        type=PermissionType.WRITE, entity_type=EntityType.USER, created_at={"refused": True},
        updated_at=get_epoch_timestamp_in_ms(),
    )
    expected = "Property values can only be of primitive types" if w.neo4j else "permissions schema"
    assert expected in await _failure(lambda: upgrade(refused))

    assert await _roles(w, alice, record.id) == ["READER"]

    await upgrade(Permission(type=PermissionType.WRITE, entity_type=EntityType.USER))
    assert await _roles(w, alice, record.id) == ["WRITER"]


async def test_a_failed_group_to_user_migration_keeps_the_users_own_permissions(world: _World) -> None:
    w = world
    alice, alice_email = await _add_user(w, "alice")
    team = _Target("user_group", w)
    await team.sync()
    handbook, payroll = _file(w, "handbook"), _file(w, "payroll")
    for record in (handbook, payroll):
        await _upsert(w, record)
    # The group may write both files; Alice may read the first on her own.
    await w.graph.batch_create_edges(
        [Permission(type=PermissionType.WRITE, entity_type=EntityType.GROUP).to_arango_permission(
            team.node.id, CollectionNames.GROUPS.value, record.id, CollectionNames.RECORDS.value)
         for record in (handbook, payroll)],
        collection=CollectionNames.PERMISSION.value,
    )
    await w.processor.add_permission_to_record(handbook, _grants(alice_email))
    assert await _roles(w, alice, handbook.id) == ["READER"]

    def migrate() -> Awaitable[None]:
        return w.processor.migrate_group_permissions_to_user(team.node.id, alice_email, w.connector_id)

    # The upgrade of Alice's edge to the first file and her new edge to the second
    # are written together, and the second file cannot be written to.
    if w.neo4j:
        hold = _neo4j_hold(
            w, "MATCH (r:Record {id: $id}) SET r.heldByTest = true RETURN count(r) AS n", {"id": payroll.id}
        )
        expected = NEO4J_LOCK_TIMEOUT
    else:
        hold = _arango_unique(w, CollectionNames.PERMISSION.value, ["_to", "role"])
        expected = "unique constraint violated"
    async with hold:
        assert expected in await _failure(migrate)

    assert await _roles(w, alice, handbook.id) == ["READER"]
    assert await _roles(w, alice, payroll.id) == []

    await migrate()
    assert await _roles(w, alice, handbook.id) == ["WRITER"]
    assert await _roles(w, alice, payroll.id) == ["WRITER"]


@dataclass
class _KbTree:
    """Old/Reports/q3.pdf and New/Archive in one knowledge base, and the service that moves its items."""

    service: KnowledgeBaseService
    kb_id: str
    owner: str
    old: str
    new: str
    reports: str
    report: str
    storage_moves: AsyncMock


async def _kb_tree(w: _World) -> _KbTree:
    owner, _ = await _add_user(w, "owner")

    async def processor_for_kb(_kb_id: str) -> DataSourceEntitiesProcessor:
        return w.processor

    service = KnowledgeBaseService(logger, w.graph, MagicMock(), processor_for_kb=processor_for_kb)
    created = await service.create_knowledge_base(user_id=owner, org_id=w.org_id, name="Handbook")
    assert created.get("success") is True, created
    kb_id = created["id"]
    w.ids.add(kb_id)

    async def folder(name: str, parent: str | None = None) -> str:
        made = await (
            service.create_nested_folder(kb_id, parent, name, owner, w.org_id) if parent
            else service.create_folder_in_kb(kb_id, name, owner, w.org_id)
        )
        assert made.get("success") is True, made
        w.ids.add(made["id"])
        return made["id"]

    old, new = await folder("Old"), await folder("New")
    reports = await folder("Reports", old)
    # New holds a folder already, so on ArangoDB a second child of it can be refused.
    await folder("Archive", new)
    report = _file(w, "q3").model_copy(update={
        "origin": OriginTypes.UPLOAD, "connector_name": Connectors.KNOWLEDGE_BASE, "connector_id": kb_id,
        "external_record_group_id": kb_id, "parent_external_record_id": reports,
    })
    await w.processor.on_new_records([(report, [])])

    # The stored files follow the graph: the move asks the storage service to
    # move them, and that request is all there is of it here.
    storage_moves = AsyncMock(return_value={"moved": 1})
    w.processor._get_storage_cleanup().move_record_tree = storage_moves
    return _KbTree(service, kb_id, owner, old, new, reports, report.id, storage_moves)


async def _parents(w: _World, record_id: str) -> list[str]:
    """The record at the other end of each PARENT_CHILD edge into the record."""
    if w.neo4j:
        rows = await w.graph.client.execute_query(
            "MATCH (p)-[:RECORD_RELATION {relationshipType: 'PARENT_CHILD'}]->(:Record {id: $id}) RETURN p.id AS id",
            parameters={"id": record_id},
        )
        return [row["id"] for row in rows or []]
    return await w.graph.http_client.execute_aql(
        "FOR e IN recordRelations FILTER e._to == @to AND e.relationshipType == 'PARENT_CHILD' "
        "RETURN PARSE_IDENTIFIER(e._from).key",
        {"to": f"records/{record_id}"},
    ) or []


async def _kb_place(w: _World, kb: _KbTree) -> dict[str, object]:
    """Where the Reports folder is: by its edges, by its own record, and as the product reads it."""
    stored = await w.graph.get_document(kb.reports, CollectionNames.RECORDS.value)
    shown_in = []
    for name, parent in (("root", None), ("Old", kb.old), ("New", kb.new)):
        found = await w.graph.find_folder_by_name_in_parent(
            kb_id=kb.kb_id, folder_name="Reports", parent_folder_id=parent, raise_on_error=True
        )
        if found:
            shown_in.append(name)
    return {
        "parents": await _parents(w, kb.reports),
        "externalParentId": stored.get("externalParentId"),
        "shown_in": shown_in,
        # Read through the folder: only a parent the record itself names counts.
        "path_of_its_file": await w.graph.get_record_path_segments(kb.report, raise_on_error=True),
    }


async def _failed_kb_move(
    w: _World,
    kb: _KbTree,
    new_parent_id: str | None,
    *,
    code: int = 500,
    reason: str | None = None,
    before_write: Callable[[], Awaitable[object]] | None = None,
) -> str:
    """Move Reports through the KB service, which answers a failure instead of raising it; return its cause.

    *before_write* runs after the service has checked the move and before the move is written.
    """
    causes: list[str] = []
    write = w.processor.on_records_moved

    async def recording(moves: list) -> None:
        if before_write:
            await before_write()
        try:
            await write(moves)
        except Exception as exc:
            causes.append(str(exc))
            raise

    w.processor.on_records_moved = recording
    try:
        result = await kb.service.move_record(kb.kb_id, kb.reports, new_parent_id, kb.owner)
    finally:
        w.processor.on_records_moved = write
    assert result["success"] is False and result["code"] == code, result
    if reason is not None:
        assert result["reason"] == reason, result
    assert len(causes) == 1, causes
    return causes[0]


@pytest.mark.parametrize("fails_on", ["old_edge", "record", "new_edge"])
async def test_a_failed_kb_move_leaves_the_item_in_its_old_folder(world: _World, fails_on: str) -> None:
    w = world
    kb = await _kb_tree(w)
    in_old = {
        "parents": [kb.old], "externalParentId": kb.old, "shown_in": ["Old"],
        "path_of_its_file": ["Old", "Reports", "q3.pdf"],
    }
    assert await _kb_place(w, kb) == in_old

    target: str | None = kb.new
    moved = {
        "parents": [kb.new], "externalParentId": kb.new, "shown_in": ["New"],
        "path_of_its_file": ["New", "Reports", "q3.pdf"],
    }
    expected = NEO4J_LOCK_TIMEOUT if w.neo4j else ARANGO_CONFLICT
    if fails_on == "old_edge":
        # The edge from the old folder cannot be deleted. Neo4j answered that with
        # False, and the move went on to put the item in the new folder as well.
        if w.neo4j:
            hold = _neo4j_hold(
                w, "MATCH (r:Record {id: $id}) SET r.heldByTest = true RETURN count(r) AS n", {"id": kb.old}
            )
        else:
            hold = _arango_hold(
                w, CollectionNames.RECORD_RELATIONS.value,
                "FOR e IN recordRelations FILTER e._to == @to "
                "UPDATE e WITH {updatedAtTimestamp: @now} IN recordRelations RETURN 1",
                {"to": f"records/{kb.reports}", "now": get_epoch_timestamp_in_ms()},
            )
    elif fails_on == "record":
        # To the root there is no new edge: the move fails on rewriting the record,
        # after the edge from the old folder is deleted. Neo4j's hold is on the
        # folder's File node, which that delete does not need.
        target = None
        moved = {
            "parents": [], "externalParentId": None, "shown_in": ["root"],
            "path_of_its_file": ["Reports", "q3.pdf"],
        }
        if w.neo4j:
            hold = _neo4j_hold(
                w, "MATCH (f:File {id: $id}) SET f.heldByTest = true RETURN count(f) AS n", {"id": kb.reports}
            )
        else:
            hold = _arango_hold(w, CollectionNames.RECORDS.value,
                                "UPDATE @key WITH {updatedAtTimestamp: @now} IN records RETURN 1",
                                {"key": kb.reports, "now": get_epoch_timestamp_in_ms()})
    # The move fails on the edge from the new folder, after the edge from the old
    # one is deleted and the record rewritten. Another writer holds the new folder
    # on Neo4j; on ArangoDB, whose edges have random keys, a unique index refuses
    # the folder a second child.
    elif w.neo4j:
        hold = _neo4j_hold(
            w, "MATCH (r:Record {id: $id}) SET r.heldByTest = true RETURN count(r) AS n", {"id": kb.new}
        )
    else:
        hold = _arango_unique(w, CollectionNames.RECORD_RELATIONS.value, ["_from", "relationshipType"])
        expected = "unique constraint violated"
    async with hold:
        assert expected in await _failed_kb_move(w, kb, target)

    # Still in the old folder, with everything beneath it, and nowhere else.
    assert await _kb_place(w, kb) == in_old
    kb.storage_moves.assert_not_awaited()

    result = await kb.service.move_record(kb.kb_id, kb.reports, target, kb.owner)
    assert result["success"] is True, result
    assert await _kb_place(w, kb) == moved
    # The retry is a whole move, the stored files included.
    old_path, new_path = (
        "/".join(["records", kb.kb_id, *place["path_of_its_file"][:-1]]) for place in (in_old, moved)
    )
    assert [call.args[1:3] for call in kb.storage_moves.await_args_list] == [(old_path, new_path)]


async def test_a_kb_move_into_a_folder_deleted_on_the_way_moves_nothing(world: _World) -> None:
    w = world
    kb = await _kb_tree(w)
    in_old = {
        "parents": [kb.old], "externalParentId": kb.old, "shown_in": ["Old"],
        "path_of_its_file": ["Old", "Reports", "q3.pdf"],
    }
    assert await _kb_place(w, kb) == in_old

    # The new folder is there when the service checks it and gone when the move is
    # written. Neither store refuses an edge from a record that does not exist:
    # Neo4j wrote none and ArangoDB a dangling one, after the old edge was deleted.
    async def delete_the_new_folder() -> None:
        await w.graph.delete_nodes_and_edges([kb.new], CollectionNames.RECORDS.value)
        assert await w.graph.get_document(kb.new, CollectionNames.RECORDS.value) is None

    cause = await _failed_kb_move(w, kb, kb.new, code=404, before_write=delete_the_new_folder)
    assert f"its new parent {kb.new} is not in the graph" in cause

    assert await _kb_place(w, kb) == in_old
    kb.storage_moves.assert_not_awaited()

    result = await kb.service.move_record(kb.kb_id, kb.reports, None, kb.owner)
    assert result["success"] is True, result
    assert await _kb_place(w, kb) == {
        "parents": [], "externalParentId": None, "shown_in": ["root"],
        "path_of_its_file": ["Reports", "q3.pdf"],
    }



def _in_trash(folder: str, action: str) -> str:
    return f"'{folder}' is in Recently deleted, so you can't {action}. Restore it first, or choose another folder."


async def _trash(w: _World, kb: _KbTree, folder_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the folder, and everything in it, in the trash as its owner's delete does."""
    monkeypatch.setattr(processor_module, "is_soft_delete_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(processor_module, "notify_kb_records_changed", AsyncMock())
    result = await w.processor.on_records_deleted_cascade(
        [folder_id], kb.kb_id, delete_source=DeleteSource.USER, deleted_by_user_id=kb.owner
    )
    assert result["success"] is True and result["softDeleted"] is True, result
    assert (await w.graph.get_document(folder_id, CollectionNames.RECORDS.value))["isDeleted"] is True


async def _children(w: _World, record_id: str) -> set[str]:
    """The record at the other end of each PARENT_CHILD edge out of the record."""
    if w.neo4j:
        rows = await w.graph.client.execute_query(
            "MATCH (:Record {id: $id})-[:RECORD_RELATION {relationshipType: 'PARENT_CHILD'}]->(c) RETURN c.id AS id",
            parameters={"id": record_id},
        )
        return {row["id"] for row in rows or []}
    return set(await w.graph.http_client.execute_aql(
        "FOR e IN recordRelations FILTER e._from == @from AND e.relationshipType == 'PARENT_CHILD' "
        "RETURN PARSE_IDENTIFIER(e._to).key",
        {"from": f"records/{record_id}"},
    ) or [])


@pytest.mark.parametrize("item", ["folder", "file"])
async def test_a_kb_move_into_a_folder_in_the_trash_is_refused(
    world: _World, item: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    kb = await _kb_tree(w)
    await _trash(w, kb, kb.new, monkeypatch)
    in_trash = await _children(w, kb.new)
    moving, home = (kb.reports, kb.old) if item == "folder" else (kb.report, kb.reports)
    before = await _kb_place(w, kb)

    result = await kb.service.move_record(kb.kb_id, moving, kb.new, kb.owner)

    assert result == {"success": False, "code": 409, "reason": _in_trash("New", "move items into it")}
    assert await _parents(w, moving) == [home]
    assert (await w.graph.get_document(moving, CollectionNames.RECORDS.value))["externalParentId"] == home
    assert await _kb_place(w, kb) == before
    assert await _children(w, kb.new) == in_trash
    kb.storage_moves.assert_not_awaited()

    # A live folder still takes it.
    made = await kb.service.create_folder_in_kb(kb.kb_id, "Live", kb.owner, w.org_id)
    assert made.get("success") is True, made
    w.ids.add(made["id"])
    moved = await kb.service.move_record(kb.kb_id, moving, made["id"], kb.owner)
    assert moved["success"] is True, moved
    assert await _parents(w, moving) == [made["id"]]
    assert (await w.graph.get_document(moving, CollectionNames.RECORDS.value))["externalParentId"] == made["id"]


async def test_a_kb_move_into_a_folder_trashed_on_the_way_moves_nothing(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    kb = await _kb_tree(w)
    in_old = {
        "parents": [kb.old], "externalParentId": kb.old, "shown_in": ["Old"],
        "path_of_its_file": ["Old", "Reports", "q3.pdf"],
    }
    assert await _kb_place(w, kb) == in_old

    # The new folder is live when the service checks it and in the trash when the move is written.
    async def trash_the_new_folder() -> None:
        await _trash(w, kb, kb.new, monkeypatch)

    cause = await _failed_kb_move(
        w, kb, kb.new, code=409, reason=_in_trash("New", "move items into it"), before_write=trash_the_new_folder
    )
    assert f"its new parent {kb.new} is not in the graph or is in the trash" in cause

    assert await _kb_place(w, kb) == in_old
    assert kb.reports not in await _children(w, kb.new)
    kb.storage_moves.assert_not_awaited()


async def test_a_folder_in_the_trash_takes_no_new_folder_and_no_upload(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    kb = await _kb_tree(w)
    await _trash(w, kb, kb.new, monkeypatch)
    in_trash = await _children(w, kb.new)

    made = await kb.service.create_nested_folder(kb.kb_id, kb.new, "Q4", kb.owner, w.org_id)
    assert made == {"success": False, "code": 409, "reason": _in_trash("New", "create a folder in it")}

    upload_refused = {"code": 409, "reason": _in_trash("New", "upload files to it")}
    checked = await kb.service.validate_folder_for_upload(kb.kb_id, kb.new, kb.owner, w.org_id)
    assert checked["valid"] is False and {k: checked[k] for k in upload_refused} == upload_refused, checked
    files = [{"filePath": "q4.pdf", "record": {"recordName": "q4.pdf"}, "fileRecord": {"name": "q4.pdf"}}]
    uploaded = await kb.service.upload_records_to_folder(kb.kb_id, kb.new, kb.owner, w.org_id, files)
    assert uploaded["success"] is False and {k: uploaded[k] for k in upload_refused} == upload_refused, uploaded

    assert await _children(w, kb.new) == in_trash

    # A live folder still takes both.
    made = await kb.service.create_nested_folder(kb.kb_id, kb.old, "Q4", kb.owner, w.org_id)
    assert made.get("success") is True, made
    w.ids.add(made["id"])
    assert (await kb.service.validate_folder_for_upload(kb.kb_id, kb.old, kb.owner, w.org_id))["valid"] is True


# Neo4j only: ArangoDB reads the parent without a lock (see upsert_record_under_parent).
@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_a_kb_move_waits_for_a_trash_of_its_folder_still_being_written(world: _World) -> None:
    """The trash marks the folder in a transaction still open when the move is checked and written.

    The service's check reads the folder as live, since the trash has not committed.
    The move must then wait for the trash and see it, not read the folder as live
    before taking its lock and put the item under it once the trash commits.
    """
    w = world
    kb = await _kb_tree(w)
    in_old = {
        "parents": [kb.old], "externalParentId": kb.old, "shown_in": ["Old"],
        "path_of_its_file": ["Old", "Reports", "q3.pdf"],
    }
    session = w.graph.client.driver.session(database=w.graph.client.database)
    trash = await session.begin_transaction()
    try:
        marked = await (await trash.run(
            "MATCH (n:Record) WHERE n.id IN $ids SET n.isDeleted = true, n.deletedAtTimestamp = $now "
            "RETURN count(n) AS n",
            {"ids": [kb.new, *await _children(w, kb.new)], "now": get_epoch_timestamp_in_ms()},
        )).single()
        assert marked["n"] == 2, marked
        move = asyncio.create_task(kb.service.move_record(kb.kb_id, kb.reports, kb.new, kb.owner))
        await asyncio.sleep(3)
        assert not move.done(), await move
        await trash.commit()
    finally:
        await session.close()

    result = await asyncio.wait_for(move, timeout=30)
    assert result == {"success": False, "code": 409, "reason": _in_trash("New", "move items into it")}
    assert await _kb_place(w, kb) == in_old
    kb.storage_moves.assert_not_awaited()
    stored = await w.graph.get_document(kb.new, CollectionNames.RECORDS.value)
    assert not any("lock" in key.lower() for key in stored), stored
