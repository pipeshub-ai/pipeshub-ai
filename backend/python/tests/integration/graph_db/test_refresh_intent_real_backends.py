"""A Force reindex request kept on a record, on a real Neo4j and ArangoDB (G-46).

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


@pytest.fixture(params=["neo4j", "arango"])
async def store(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Store]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
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
    clear = dict(ri.CLEARED_FIELDS)
    assert not await store.graph.update_node_fields_if_match(k, RECORDS, clear, {ri.ID_FIELD: "other"})
    assert (await store.doc("c"))[ri.ID_FIELD] == "mine"
    assert await store.graph.update_node_fields_if_match(k, RECORDS, clear, {ri.ID_FIELD: "mine"})
    doc = await store.doc("c")
    assert doc.get(ri.ID_FIELD) is None and doc.get(ri.AT_FIELD) is None
    assert ri.stored_intent(doc) is None


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
