"""A Force reindex request kept on a record, on PostgreSQL, against a real database.

The same answers as ``tests/integration/graph_db/test_refresh_intent_real_backends.py`` gives on Neo4j and ArangoDB: the walk's
status statement stores the request with the status; the intent-only write changes nothing else, whatever the status; the
intent sweep's read lists one org's live records that hold a request, in key order; a twin's completion neither promotes nor fails
a queued copy that holds a request, and the next-duplicate lookup never picks it; a request is cleared by its own id only; and
the partial index that serves the sweep's read exists and is used.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.modules.indexing import refresh_intent as ri

from .test_identity import w as _identity_world  # noqa: F401

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


async def record(w: World, key: str, **doc: object) -> None:
    await w.nodes("records", {"_key": key, "recordName": key, "connectorId": "c1", "orgId": w.org, **doc})


async def doc(w: World, key: str) -> dict:
    found = await w.p.get_document(key, "records")
    assert found is not None
    return found


async def test_the_walks_status_statement_stores_the_request_with_the_status(w: World) -> None:
    await record(w, "a", indexingStatus="COMPLETED", republishCount=5, lastRepublishedAt=1)
    await w.p.update_indexing_status_for_record_ids(["a"], "NOT_STARTED", fields=ri.intent_fields("intent-1", 1_800_000_000_000))
    d = await doc(w, "a")
    assert d["indexingStatus"] == "NOT_STARTED" and (d[ri.ID_FIELD], d[ri.AT_FIELD]) == ("intent-1", 1_800_000_000_000)
    assert d.get("republishCount") is None
    await w.p.update_indexing_status_for_record_ids(["a"], "COMPLETED")
    assert (await doc(w, "a"))[ri.ID_FIELD] == "intent-1"


async def test_the_intent_only_write_changes_nothing_but_the_request_whatever_the_status(w: World) -> None:
    await record(w, "run", indexingStatus="IN_PROGRESS", processingStartedAt=7)
    assert await w.p.update_record_fields_for_ids(["run", "missing"], ri.intent_fields("intent-2", 123)) == 1
    d = await doc(w, "run")
    assert (d["indexingStatus"], d["processingStartedAt"]) == ("IN_PROGRESS", 7) and (d[ri.ID_FIELD], d[ri.AT_FIELD]) == ("intent-2", 123)
    assert await w.p.update_record_fields_for_ids(["run"], {}) == 0


async def test_a_request_is_cleared_by_its_own_id_only(w: World) -> None:
    await record(w, "c", **ri.intent_fields("mine", 1))
    assert not await w.p.update_node_fields_if_match("c", "records", {}, {ri.ID_FIELD: "other"}, unset=ri.CLEARED_FIELDS)
    assert (await doc(w, "c"))[ri.ID_FIELD] == "mine"
    assert await w.p.update_node_fields_if_match("c", "records", {}, {ri.ID_FIELD: "mine"}, unset=ri.CLEARED_FIELDS)
    d = await doc(w, "c")
    assert ri.ID_FIELD not in d and ri.AT_FIELD not in d and ri.stored_intent(d) is None


async def test_the_clear_helper_removes_the_fields_and_writes_its_extra_in_the_same_statement(w: World) -> None:
    await record(w, "h", indexingStatus="FAILED", **ri.intent_fields("mine", 1))
    assert not await ri.clear_intent(w.p, "h", "other", {"reason": "x"})
    assert await ri.clear_intent(w.p, "h", "mine", {"reason": ri.REFRESH_EXPIRED_REASON})
    d = await doc(w, "h")
    assert ri.ID_FIELD not in d and ri.AT_FIELD not in d and d["reason"] == ri.REFRESH_EXPIRED_REASON


async def test_the_sweeps_read_lists_the_live_records_of_one_org_with_a_request_in_key_order(w: World) -> None:
    for key in ("b", "a", "c"):
        await record(w, key, **ri.intent_fields(f"i-{key}", 1))
    await record(w, "none")
    await record(w, "gone", isDeleted=True, **ri.intent_fields("i-gone", 1))
    first = await w.p.get_records_with_refresh_intent(w.org, limit=2)
    assert [d.get("_key") or d.get("id") for d in first] == ["a", "b"]
    assert [d.get("_key") or d.get("id") for d in await w.p.get_records_with_refresh_intent(w.org, after_key="b", limit=2)] == ["c"]
    assert await w.p.get_records_with_refresh_intent(w.org, after_key="c") == []
    with pytest.raises(ValueError, match="bound org"):
        await w.p.get_records_with_refresh_intent("another-org")


async def test_a_twins_completion_leaves_a_queued_copy_that_holds_a_request_alone(w: World) -> None:
    same = {"md5Checksum": "m", "sizeInBytes": 5}
    await record(w, "ref", indexingStatus="COMPLETED", extractionStatus="COMPLETED", **same)
    await record(w, "plain", indexingStatus="QUEUED", **same)
    await record(w, "held", indexingStatus="QUEUED", **same, **ri.intent_fields("intent-3", 1))
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 1
    assert (await doc(w, "plain"))["indexingStatus"] == "COMPLETED"
    held = await doc(w, "held")
    assert held["indexingStatus"] == "QUEUED" and held[ri.ID_FIELD] == "intent-3"
    assert await w.p.update_queued_duplicates_status("ref", "FAILED", None, reason="boom") == 0
    assert (await doc(w, "held"))["indexingStatus"] == "QUEUED"


async def test_the_next_queued_duplicate_never_picks_a_copy_that_holds_a_request(w: World) -> None:
    await record(w, "ref", indexingStatus="COMPLETED", md5Checksum="m")
    await record(w, "held", indexingStatus="QUEUED", md5Checksum="m", **ri.intent_fields("intent-4", 1))
    assert await w.p.find_next_queued_duplicate("ref") is None
    await record(w, "plain", indexingStatus="QUEUED", md5Checksum="m")
    nxt = await w.p.find_next_queued_duplicate("ref")
    assert nxt is not None and (nxt.get("_key") or nxt.get("id")) == "plain"


async def test_the_partial_index_exists_and_serves_the_sweeps_read(w: World, pg_db) -> None:
    rows = await pg_db.run(lambda tx: tx.fetch("SELECT indexdef FROM pg_indexes WHERE schemaname = 'graph' AND indexname LIKE 'nodes_record_refresh_intent%'"))
    assert rows, "graph_0015 did not build the index"
    assert all("refreshIntentId" in r["indexdef"] for r in rows)
