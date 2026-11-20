"""A Force reindex request kept on a record, on a real Neo4j and ArangoDB.

The request is two fields, ``refreshIntentId`` and ``refreshIntentAtMs``. This covers what the stores must do for it:

* the walk's status statement writes the request in the same statement as NOT_STARTED, and ``republishCount`` is reset with it;
* the intent-only write changes nothing but the request, whatever the record's status (a record that is running);
* the intent sweep's read returns the live records of one org that carry a request, in key order, in pages;
* a twin's completion neither promotes nor fails a queued copy that holds a request, and the next-duplicate lookup never
  picks it (the filters of ``update_queued_duplicates_status`` and ``find_next_queued_duplicate``);
* a request is cleared by a compare-and-set on its id only.

The strict ArangoDB ``records`` schema is applied by ``ensure_schema``, so a field it does not declare fails the write here as
it would in production. Runs in backend-matrix on both graph jobs (NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD).
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import FileRecord, RecordType
from app.modules.indexing import refresh_intent as ri
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

RECORDS = CollectionNames.RECORDS.value
ARANGO_DB = "refresh_intent_it"
logger = logging.getLogger("refresh-intent-it")
QUEUED = ProgressStatus.QUEUED.value
COMPLETED = ProgressStatus.COMPLETED.value
IN_PROGRESS = ProgressStatus.IN_PROGRESS.value
NOT_STARTED = ProgressStatus.NOT_STARTED.value


class _Store:
    def __init__(self, graph: IGraphDBProvider, run: str) -> None:
        self.graph = graph
        self.run = run
        self.org = f"org-{run}"
        self.keys: list[str] = []

    def key(self, name: str) -> str:
        return f"{name}-{self.run}"

    async def add(self, name: str, *, status: str = COMPLETED, org: str | None = None, md5: str | None = None, **extra: object) -> str:
        key = self.key(name)
        record = FileRecord(
            id=key, org_id=org or self.org, record_name=key, record_type=RecordType.FILE, external_record_id=f"ext-{key}",
            version=0, origin=OriginTypes.CONNECTOR, connector_name=Connectors.WEB, connector_id=f"conn-{self.run}",
            mime_type="text/html", is_file=True, indexing_status=status, md5_hash=md5, size_in_bytes=5,
        )
        doc = {**record.to_arango_base_record(), **extra}
        assert await self.graph.batch_upsert_nodes([doc], RECORDS)
        self.keys.append(key)
        return key

    async def doc(self, name: str) -> dict:
        found = await self.graph.get_document(self.key(name), RECORDS)
        assert found is not None
        return found


async def _stores(backend: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Store]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if backend == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(backend, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        s = _Store(graph, run)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                if isinstance(graph, Neo4jProvider):
                    await graph.client.execute_query("MATCH (n) WHERE n.id ENDS WITH $run DETACH DELETE n", parameters={"run": run})
                else:
                    await graph.delete_nodes_and_edges(s.keys, RECORDS)

        cleanup.push_async_callback(remove)
        yield s


@pytest.fixture(params=["neo4j", "arango"])
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Store]:
    async for s in _stores(request.param, monkeypatch):
        yield s


@pytest.fixture(params=["arango"])
async def arango_store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Store]:
    """For a test that only makes sense on ArangoDB; parametrized rather than skipped at run time."""
    async for s in _stores(request.param, monkeypatch):
        yield s


async def test_the_walks_status_statement_stores_the_request_with_the_status(store: _Store) -> None:
    k = await store.add("a", status=COMPLETED, republishCount=5, lastRepublishedAt=1)
    fields = ri.intent_fields("intent-1", 1_800_000_000_000)
    await store.graph.update_indexing_status_for_record_ids([k], NOT_STARTED, fields=fields)
    doc = await store.doc("a")
    assert doc["indexingStatus"] == NOT_STARTED
    assert (doc[ri.ID_FIELD], doc[ri.AT_FIELD]) == ("intent-1", 1_800_000_000_000)
    assert doc.get("republishCount") is None              # reset with the request: the sweep schedules from the request's own age
    await store.graph.update_indexing_status_for_record_ids([k], COMPLETED)           # no fields: the request is not touched
    assert (await store.doc("a"))[ri.ID_FIELD] == "intent-1"


async def test_the_intent_only_write_changes_nothing_but_the_request_whatever_the_status(store: _Store) -> None:
    k = await store.add("run", status=IN_PROGRESS, processingStartedAt=7)
    written = await store.graph.update_record_fields_for_ids([k, "missing"], ri.intent_fields("intent-2", 123))
    assert written == 1
    doc = await store.doc("run")
    assert (doc["indexingStatus"], doc["processingStartedAt"]) == (IN_PROGRESS, 7)
    assert (doc[ri.ID_FIELD], doc[ri.AT_FIELD]) == ("intent-2", 123)
    assert await store.graph.update_record_fields_for_ids([k], {}) == 0


async def test_a_request_is_cleared_by_its_own_id_only(store: _Store) -> None:
    k = await store.add("c", **ri.intent_fields("mine", 1))
    assert not await store.graph.update_node_fields_if_match(k, RECORDS, {}, {ri.ID_FIELD: "other"}, unset=ri.CLEARED_FIELDS)
    assert (await store.doc("c"))[ri.ID_FIELD] == "mine"
    assert await store.graph.update_node_fields_if_match(k, RECORDS, {}, {ri.ID_FIELD: "mine"}, unset=ri.CLEARED_FIELDS)
    doc = await store.doc("c")
    assert ri.ID_FIELD not in doc and ri.AT_FIELD not in doc, "a cleared record carries neither field, not nulls"
    assert ri.stored_intent(doc) is None


async def test_the_clear_helper_removes_the_fields_and_writes_its_extra_in_the_same_statement(store: _Store) -> None:
    k = await store.add("h", status=ProgressStatus.FAILED.value, **ri.intent_fields("mine", 1))
    assert not await ri.clear_intent(store.graph, k, "other", {"reason": "x"})
    assert await ri.clear_intent(store.graph, k, "mine", {"reason": ri.REFRESH_EXPIRED_REASON})
    doc = await store.doc("h")
    assert ri.ID_FIELD not in doc and ri.AT_FIELD not in doc and doc["reason"] == ri.REFRESH_EXPIRED_REASON


async def test_the_sweeps_read_lists_the_live_records_of_one_org_with_a_request_in_key_order(store: _Store) -> None:
    for name in ("b", "a", "c"):
        await store.add(name, **ri.intent_fields(f"i-{name}", 1))
    await store.add("none")                                                      # no request
    await store.add("gone", isDeleted=True, **ri.intent_fields("i-gone", 1))     # in the trash
    await store.add("elsewhere", org=f"other-{store.run}", **ri.intent_fields("i-x", 1))
    keys = [store.key(n) for n in ("a", "b", "c")]
    first = await store.graph.get_records_with_refresh_intent(store.org, limit=2)
    assert [d.get("_key") or d.get("id") for d in first] == keys[:2]
    rest = await store.graph.get_records_with_refresh_intent(store.org, after_key=keys[1], limit=2)
    assert [d.get("_key") or d.get("id") for d in rest] == keys[2:]
    assert await store.graph.get_records_with_refresh_intent(store.org, after_key=keys[2], limit=2) == []


async def test_a_twins_completion_leaves_a_queued_copy_that_holds_a_request_alone(store: _Store) -> None:
    ref = await store.add("ref", status=COMPLETED, md5="m1", extractionStatus=COMPLETED)
    plain = await store.add("plain", status=QUEUED, md5="m1")
    held = await store.add("held", status=QUEUED, md5="m1", **ri.intent_fields("intent-3", 1))
    assert await store.graph.update_queued_duplicates_status(ref, COMPLETED, f"v-{store.run}") == 1
    assert (await store.doc("plain"))["indexingStatus"] == COMPLETED
    left = await store.doc("held")
    assert left["indexingStatus"] == QUEUED and left[ri.ID_FIELD] == "intent-3"
    assert await store.graph.update_queued_duplicates_status(ref, ProgressStatus.FAILED.value, None, reason="boom") == 0
    assert (await store.doc("held"))["indexingStatus"] == QUEUED
    assert plain and held


async def test_the_next_queued_duplicate_never_picks_a_copy_that_holds_a_request(store: _Store) -> None:
    ref = await store.add("ref2", status=COMPLETED, md5="m2")
    await store.add("held2", status=QUEUED, md5="m2", **ri.intent_fields("intent-4", 1))
    assert await store.graph.find_next_queued_duplicate(ref) is None
    await store.add("plain2", status=QUEUED, md5="m2")
    nxt = await store.graph.find_next_queued_duplicate(ref)
    assert nxt is not None and (nxt.get("_key") or nxt.get("id")) == store.key("plain2")


async def _put_record_schema(store: _Store, schema: dict) -> None:
    client = store.graph.http_client  # type: ignore[attr-defined]
    session = await client._get_session()
    url = f"{client.base_url}/_db/{client.database}/_api/collection/{RECORDS}/properties"
    async with session.put(url, json={"schema": schema}) as resp:
        assert resp.status == 200, await resp.text()


async def test_a_cleared_or_never_requested_record_stays_writable_under_the_previous_closed_schema(arango_store: _Store) -> None:
    """A process on a release before the two fields applies the closed record schema without them. Only a record that still holds
    a field is refused by it, so a clear removes the fields (not nulls), and stale recovery's connector-off write adds none."""
    import copy

    from app.schema.arango.documents import record_schema

    store = arango_store
    cleared = await store.add("cleared", **ri.intent_fields("mine", 1))
    assert await ri.clear_intent(store.graph, cleared, "mine")
    never = await store.add("never", status=QUEUED)
    previous = copy.deepcopy(record_schema)
    for name in ri.CLEARED_FIELDS:
        previous["rule"]["properties"].pop(name)
    live = copy.deepcopy(record_schema)
    await _put_record_schema(store, previous)
    try:
        for key in (cleared, never):
            await store.graph.http_client.update_document(RECORDS, key, {"reason": "an older image's write"})
    finally:
        await _put_record_schema(store, live)
    for name in ("cleared", "never"):
        doc = await store.doc(name)
        assert ri.ID_FIELD not in doc and ri.AT_FIELD not in doc and doc["reason"] == "an older image's write"
