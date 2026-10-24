"""Revision retention's graph queries against a real Neo4j and a real ArangoDB.

The pointer page (``get_revision_pointers``), the holders' revisions
(``get_virtual_record_holders``), and the stage states of revisions no record
is on (``stage_states_unheld_revisions`` and its re-checking delete), with a
trashed holder and a revision still held by one record of two.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_revision_retention_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.neo4j import collection_to_label
from app.models.entities import DeleteSource, FileRecord, RecordType
from app.modules.artifacts.retention import RevisionRetentionSweep
from app.modules.pipeline.models import Priority, StageJob, StageState
from app.services.graph_db.arango.arango_http_client import ArangoQueryError
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.resource_governor.models import ParseTier
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "revision_retention_it"
HOUR_MS = 3600 * 1000
logger = logging.getLogger("revision-retention-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
            await provider.ensure_pipeline_schema()
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        yield provider


class _World:
    def __init__(self, graph: IGraphDBProvider) -> None:
        self.graph = graph
        # Alphanumeric only, so the keys sort together under ArangoDB's collation too.
        self.prefix = f"rt{uuid.uuid4().hex[:12]}x"
        self.org_id = f"org{self.prefix}"
        self.connector_id = f"conn{self.prefix}"
        self.record_ids: list[str] = []
        self.vrids: list[str] = []

    def vrid(self, suffix: str) -> str:
        return f"{self.prefix}{suffix}"

    async def record(self, name: str, vrid: str, rev: str | None, *, trashed: bool = False) -> str:
        record_id = f"{self.prefix}{name}"
        now = get_epoch_timestamp_in_ms()
        fields: dict = {
            "id": record_id, "org_id": self.org_id, "record_name": f"{name}.pdf", "record_type": RecordType.FILE,
            "external_record_id": f"ext-{record_id}", "version": 1, "origin": OriginTypes.CONNECTOR,
            "connector_name": Connectors.GOOGLE_DRIVE, "connector_id": self.connector_id,
            "mime_type": "application/pdf", "indexing_status": ProgressStatus.COMPLETED.value,
            "is_file": True, "extension": "pdf", "size_in_bytes": 1,
        }
        if trashed:
            fields.update(
                is_deleted=True, deleted_at=now - 1000, deleted_by_user_id="user",
                delete_source=DeleteSource.USER, delete_batch_id=f"batch-{self.org_id}",
            )
        await self.graph.batch_upsert_records([FileRecord(**fields)])
        update: dict = {"virtualRecordId": vrid}
        if rev is not None:
            update["contentRev"] = rev
        await self.graph.update_node(record_id, CollectionNames.RECORDS.value, update)
        self.record_ids.append(record_id)
        return record_id

    async def mapping(self, vrid: str, current: str | None = None, previous: str | None = None) -> None:
        node: dict = {"id": vrid, "orgId": self.org_id, "virtualRecordId": vrid, "documentId": f"doc-{vrid}"}
        if current is not None:
            node.update(currentRev=current, previousRev=previous, manifestDocId=f"manifest-{vrid}")
        await self.graph.batch_upsert_nodes([node], CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value)
        self.vrids.append(vrid)

    async def state(self, vrid: str, rev: str, stage: str, updated_at_ms: int) -> None:
        job = StageJob(
            stage=stage, stage_version=1, org_id=self.org_id, virtual_record_id=vrid, rev=rev,
            record_ids=("r",), connector_id=self.connector_id, tier=ParseTier.LIGHT, priority=Priority.BULK,
            trigger="embed", text_digest="t", blocks_digest="b", text_chars=1, has_tables=False, has_images=False,
        )
        document = StageState.from_job(job, ProgressStatus.COMPLETED, updated_at_ms).model_dump(
            mode="json", by_alias=True
        )
        document["id"] = document.pop("key")
        assert await self.graph.stage_state_create(document)

    async def revs_with_states(self, vrid: str) -> set[str]:
        states = []
        for rev in ("r1", "r2", "r3", "r4"):
            states += await self.graph.stage_states_for_revision(vrid, rev)
        return {s["rev"] for s in states}

    async def remove(self) -> None:
        g = self.graph
        await g.stage_states_delete_for_virtual_records(self.vrids)
        if isinstance(g, Neo4jProvider):
            await g.client.execute_query(
                "MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": [*self.record_ids, *self.vrids]}
            )
            return
        for collection, keys in (
            (CollectionNames.RECORDS.value, self.record_ids),
            (CollectionNames.FILES.value, self.record_ids),
            (CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value, self.vrids),
        ):
            await g.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": keys}
            )


@pytest.fixture
async def world(graph: IGraphDBProvider) -> AsyncIterator[_World]:
    w = _World(graph)
    try:
        yield w
    finally:
        await w.remove()


async def test_the_pointer_page_walks_every_mapping_row_by_key(world: _World) -> None:
    a, b, c = world.vrid("a"), world.vrid("b"), world.vrid("c")
    await world.mapping(a, "r2", "r1")
    await world.mapping(b)
    await world.mapping(c, "r1")

    got = await world.graph.get_revision_pointers(world.prefix, 3)
    assert got == [
        {"virtualRecordId": a, "orgId": world.org_id, "currentRev": "r2", "previousRev": "r1"},
        {"virtualRecordId": b, "orgId": world.org_id, "currentRev": None, "previousRev": None},
        {"virtualRecordId": c, "orgId": world.org_id, "currentRev": "r1", "previousRev": None},
    ]
    assert await world.graph.get_revision_pointers(world.prefix, 1) == got[:1]
    assert await world.graph.get_revision_pointers(a, 2) == got[1:]
    assert await world.graph.get_revision_pointers(world.prefix, 0) == []
    first = await world.graph.get_revision_pointers(None, 1)
    assert len(first) == 1


async def test_holders_carry_their_revision(world: _World) -> None:
    vrid = world.vrid("v")
    live = await world.record("live", vrid, "r2")
    trashed = await world.record("trashed", vrid, "r1", trashed=True)
    holders = (await world.graph.get_virtual_record_holders([vrid], world.org_id))[vrid]
    assert {h["id"]: (h["contentRev"], h["isDeleted"]) for h in holders} == {
        live: ("r2", False), trashed: ("r1", True),
    }


async def test_stage_states_of_revisions_no_record_is_on(world: _World) -> None:
    now = get_epoch_timestamp_in_ms()
    old, young, cutoff = now - 48 * HOUR_MS, now - HOUR_MS, now - 24 * HOUR_MS
    shared, trash, lone = world.vrid("s"), world.vrid("t"), world.vrid("u")
    # Two records share `shared`: one moved on to r3, the other is still on r2.
    await world.record("one", shared, "r3")
    await world.record("two", shared, "r2")
    await world.record("binned", trash, "r1", trashed=True)
    for rev in ("r1", "r2", "r3"):
        await world.state(shared, rev, "classify", old)
        await world.state(shared, rev, "entities", old)
    await world.state(shared, "r4", "classify", old)
    await world.state(shared, "r4", "entities", young)
    await world.state(trash, "r1", "classify", old)
    await world.state(trash, "r2", "classify", old)
    await world.state(lone, "r1", "classify", old)
    world.vrids += [shared, trash, lone]

    unheld = await world.graph.stage_states_unheld_revisions(
        [lone, shared, trash, "no-such-vrid"], updated_before_ms=cutoff
    )
    # r4 has a state younger than the cutoff; r2 and r3 are held, r1 of `trash` by a trashed record.
    assert unheld == sorted([(shared, "r1"), (trash, "r2"), (lone, "r1")])

    # A record moving back onto r1 after the read keeps its states: the delete re-checks.
    await world.record("back", shared, "r1")
    deleted = await world.graph.stage_states_delete_unheld_revisions(
        [*unheld, (shared, "r4")], updated_before_ms=cutoff
    )
    assert deleted == 2
    assert await world.revs_with_states(shared) == {"r1", "r2", "r3", "r4"}
    assert await world.revs_with_states(trash) == {"r1"}
    assert await world.revs_with_states(lone) == set()
    assert await world.graph.stage_states_unheld_revisions([], updated_before_ms=cutoff) == []
    assert await world.graph.stage_states_delete_unheld_revisions([], updated_before_ms=cutoff) == 0


async def test_the_sweep_against_the_graph(world: _World) -> None:
    """The sweep end to end on a real graph, with a fake store: trash skips, holders keep."""
    now = get_epoch_timestamp_in_ms()
    old = now - 48 * HOUR_MS
    kept, binned = world.vrid("k"), world.vrid("l")
    await world.mapping(kept, "r3", "r2")
    await world.mapping(binned, "r2", "r1")
    await world.record("on3", kept, "r3")
    await world.record("on4", kept, "r4")
    await world.record("bin", binned, "r2", trashed=True)
    await world.state(kept, "r1", "classify", old)
    await world.state(binned, "r0", "classify", old)
    revisions = {
        vrid: [{"documentId": f"{vrid}-{rev}", "rev": rev, "createdAt": old} for rev in ("r1", "r2", "r3", "r4")]
        for vrid in (kept, binned)
    }
    store = MagicMock()
    store.list_revisions = AsyncMock(side_effect=lambda org, vrid: revisions[vrid])
    store.delete_revision = AsyncMock(return_value=1)

    sweep = RevisionRetentionSweep(world.graph, store, logger, page_size=2)
    next_key, report = await sweep.run_page(world.prefix)

    assert next_key == binned
    store.delete_revision.assert_awaited_once_with(world.org_id, f"{kept}-r1")
    assert (report.revisions_deleted, report.skipped_trashed, report.stage_states_deleted) == (1, 1, 1)
    assert await world.revs_with_states(kept) == set()
    assert await world.graph.stage_states_for_revision(binned, "r0") != []


class _Leases:
    """``record:<id>`` leases, as the record consumer and the sweep take them."""

    def __init__(self) -> None:
        self.owners: dict[str, str] = {}

    async def try_acquire(self, pool: str, owner: str, limit: int, lease_seconds: float) -> bool:
        if self.owners.get(pool, owner) != owner:
            return False
        self.owners[pool] = owner
        return True

    async def renew_many(self, leases: list[tuple[str, str]], lease_seconds: float) -> dict[tuple[str, str], bool]:
        return {(p, o): self.owners.get(p) == o for p, o in leases}

    async def release(self, pool: str, owner: str) -> None:
        if self.owners.get(pool) == owner:
            del self.owners[pool]


@contextlib.asynccontextmanager
async def _uncommitted(graph: IGraphDBProvider, collection: str, key: str, fields: dict) -> AsyncIterator[None]:
    """A write to one document, held open: committed when the block ends."""
    if isinstance(graph, Neo4jProvider):
        session = graph.client.driver.session(database=graph.client.database)
        try:
            tx = await session.begin_transaction()
            await (await tx.run(
                f"MATCH (n:{collection_to_label(collection)} {{id: $id}}) SET n += $fields", {"id": key, "fields": fields}
            )).consume()
            yield
            await tx.commit()
        finally:
            await session.close()
        return
    txn = await graph.http_client.begin_transaction(read=[], write=[collection])
    try:
        await graph.http_client.execute_aql(
            f"UPDATE {{_key: @id}} WITH @fields IN {collection}", {"id": key, "fields": fields}, txn_id=txn
        )
        yield
    except BaseException:
        await graph.http_client.abort_transaction(txn)
        raise
    await graph.http_client.commit_transaction(txn)


async def _until_blocked(graph: Neo4jProvider, statement: Awaitable[int], marker: str) -> asyncio.Task[int]:
    """Start ``statement`` and return once Neo4j shows it waiting for a lock."""
    task = asyncio.ensure_future(statement)
    for _ in range(200):
        rows = await graph.client.execute_query(
            "SHOW TRANSACTIONS YIELD currentQuery, status WHERE currentQuery CONTAINS $marker RETURN status",
            parameters={"marker": marker},
        )
        if any(str(row["status"]).startswith("Blocked") for row in rows):
            return task
        assert not task.done(), "the statement ran without waiting for the lock"
        await asyncio.sleep(0.05)
    raise AssertionError("the statement never waited for the lock")


async def test_a_stage_state_written_while_the_delete_waits_is_kept(world: _World) -> None:
    """A stage state claimed again (or settled) while the delete reads it is not deleted on the
    age it had before. Neo4j reads committed data without a lock, so the statement locks the
    states before reading their age; on ArangoDB the delete conflicts with the write."""
    now = get_epoch_timestamp_in_ms()
    vrid = world.vrid("v")
    await world.record("on2", vrid, "r2")
    await world.state(vrid, "r1", "classify", now - 48 * HOUR_MS)
    world.vrids.append(vrid)
    delete = world.graph.stage_states_delete_unheld_revisions([(vrid, "r1")], updated_before_ms=now - 24 * HOUR_MS)

    async with _uncommitted(world.graph, CollectionNames.STAGE_STATES.value, f"{vrid}:r1:classify",
                            {"updatedAtMs": now, "status": ProgressStatus.QUEUED.value}):
        if isinstance(world.graph, Neo4jProvider):
            pending = await _until_blocked(world.graph, delete, "DETACH DELETE state")
        else:
            with contextlib.suppress(ArangoQueryError):
                assert await delete == 0
            pending = None
    if pending is not None:
        assert await pending == 0

    assert await world.revs_with_states(vrid) == {"r1"}


async def test_a_record_moving_onto_an_old_revision_under_its_lease_keeps_it(world: _World) -> None:
    """The graph re-checks cannot see a ``contentRev`` write that commits while they run: it is
    another document than the stage states, or the artifact set the record's writer re-uses.
    A re-index writes it under the record lease, so the sweep, which takes the holders' leases,
    leaves the virtual record for the next pass."""
    now = get_epoch_timestamp_in_ms()
    old = now - 48 * HOUR_MS
    vrid = world.vrid("v")
    await world.mapping(vrid, "r3", "r2")
    record_id = await world.record("back", vrid, "r3")
    await world.state(vrid, "r1", "classify", old)
    store = MagicMock()
    store.list_revisions = AsyncMock(return_value=[{"documentId": "doc-r1", "rev": "r1", "createdAt": old}])
    store.delete_revision = AsyncMock(return_value=1)
    leases = _Leases()
    sweep = RevisionRetentionSweep(world.graph, store, logger, leases=leases)  # type: ignore[arg-type]

    assert await leases.try_acquire(f"record:{record_id}", "indexer", 1, 60)
    async with _uncommitted(world.graph, CollectionNames.RECORDS.value, record_id, {"contentRev": "r1"}):
        _, report = await sweep.run_page(world.prefix)
    await leases.release(f"record:{record_id}", "indexer")

    assert report.skipped_busy == 1
    store.delete_revision.assert_not_awaited()
    assert await world.revs_with_states(vrid) == {"r1"}
    assert leases.owners == {}

    # Once the record is on r1 for good, the next pass keeps both because a record is on it.
    _, report = await sweep.run_page(world.prefix)
    assert report.skipped_busy == 0
    store.delete_revision.assert_not_awaited()
    assert await world.revs_with_states(vrid) == {"r1"}
