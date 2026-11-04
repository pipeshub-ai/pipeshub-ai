"""Records on PostgreSQL: the upsert with its type document, the reads and lookups, indexing status, sync points and the delete, in one org and
across two. The contract tests of the other backends for the code graph run beside this in ``test_code_graph_contract.py``.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.core import org_context
from app.core.org_context import (
    MissingOrgContext,
    OrgContext,
    SystemContextNotAllowed,
    bind,
)
from app.models.entities import CodeFileRecord, FileRecord, Record, RecordType
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.postgres.records import RecordsMixin

from .pg_support import OrgBound, StandInProvider, new_org

pytestmark = pytest.mark.integration

COMMON = {"record_name": "n", "version": 1, "origin": OriginTypes.CONNECTOR, "connector_name": Connectors.GITHUB, "connector_id": "c1"}


def code(org: str, rid: str, **kw: object) -> CodeFileRecord:
    base = {**COMMON, "id": rid, "org_id": org, "record_type": RecordType.CODE_FILE, "external_record_id": f"ext-{rid}", "mime_type": "text/x-python",
            "extension": "py", "file_path": f"src/{rid}.py", "file_hash": "h", "language": "python"}
    return CodeFileRecord(**{**base, **kw})


def plain(org: str, rid: str, **kw: object) -> Record:
    """A record whose type has no type collection."""
    return Record(**{**COMMON, "id": rid, "org_id": org, "record_type": RecordType.DRIVE, "external_record_id": f"ext-{rid}", **kw})


def bound(pg_db: object, org: str | None = None) -> OrgBound:
    return OrgBound(StandInProvider(logging.getLogger("rec"), pg_db), org or new_org())


NAMES = [n for n, f in vars(RecordsMixin).items() if not n.startswith("__") and callable(f)]
CALLS = {
    "get_record_by_external_id": ("c1", "e"), "get_record_by_id": ("r",), "get_records_by_virtual_record_id": ("v",), "get_records_by_parent": ("c1", "p"),
    "get_record_by_external_revision_id": ("c1", "rev"), "get_existing_record_keys": (["r"],), "get_records_by_record_ids": (["r"], "o"),
    "get_typed_records_batch": (["r"],), "get_virtual_record_ids_for_record_ids": (["r"],), "get_virtual_record_ids_in_use": (["v"],),
    "batch_upsert_records": ([plain("o", "r")],), "create_record_relation": ("a", "b", "BOOKMARK"),
    "compare_and_set_indexing_status": (["r"], "QUEUED", "COMPLETED"), "update_indexing_status_for_record_ids": (["r"], "QUEUED"),
    "has_records_indexed_since": ("c1", 0), "get_sync_point": ("k", "syncPoints"), "upsert_sync_point": ("k", {}, "syncPoints"),
    "remove_sync_point": ("k", "syncPoints"), "_delete_records_with_their_types": (["r"],),
}


def test_every_public_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert set(NAMES) == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*CALLS[name])
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*CALLS[name])


async def test_a_record_is_written_with_its_type_document_and_read_back_typed(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "r1"), plain(p.org, "r2")])
    typed = await p.get_record_by_id("r1")
    assert isinstance(typed, CodeFileRecord) and typed.file_path == "src/r1.py"
    assert await p.get_document("r1", "codeFiles") is not None and await p.get_document("r2", "codeFiles") is None
    assert len(await p.get_edges_from_node("records/r1", "isOfType")) == 1 and await p.get_edges_from_node("records/r2", "isOfType") == []
    assert await p.get_record_by_id("r2") is None                      # no type document: Neo4j cannot build it either, and answers None
    assert await p.get_record_by_id("nope") is None
    got = await p.get_typed_records_batch(["r1", "r2", "nope"])
    assert list(got) == ["r1"]                                          # what will not build is left out


async def test_an_upsert_merges_clears_a_null_and_applies_a_repeat_in_order(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "r1", weburl="http://x")])
    await p.batch_update_nodes([{"id": "r1", "note": "kept"}], "records")
    await p.batch_upsert_records([code(p.org, "r1", record_name="first"), code(p.org, "r1", record_name="second", weburl=None)])
    doc = await p.get_document("r1", "records")
    assert doc["recordName"] == "second" and "webUrl" not in doc and doc["note"] == "kept"         # the later repeat wins, its null cleared the field, the rest merged
    assert len(await p.get_edges_from_node("records/r1", "isOfType")) == 1                         # one edge, however often it is written


async def test_records_of_two_orgs_never_meet(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    await a.batch_upsert_records([code(a.org, "same")])
    await b.batch_upsert_records([code(b.org, "same", record_name="b's")])
    assert (await a.get_record_by_id("same")).record_name == "n" and (await b.get_record_by_id("same")).record_name == "b's"
    assert await a.get_record_by_external_id("c1", "ext-same") is not None
    assert await a.get_existing_record_keys(["same", "zz"]) == {"same"}
    assert await a.get_records_by_record_ids(["same"], b.org) == []                                # the org argument filters too
    assert len(await a.get_records_by_record_ids(["same"], a.org)) == 1


async def test_visibility_live_trashed_and_both_and_a_missing_flag_is_live(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "live"), code(p.org, "gone", external_revision_id="rv"), code(p.org, "old")])
    await p.batch_update_nodes([{"id": "live", "virtualRecordId": "v"}, {"id": "gone", "virtualRecordId": "v", "isDeleted": True},
                                {"id": "old", "virtualRecordId": "v", "isDeleted": None}], "records")
    for vis, want in ((RecordVisibility.LIVE, {"live", "old"}), (RecordVisibility.DELETED, {"gone"}), (RecordVisibility.ALL, {"live", "gone", "old"})):
        assert set(await p.get_records_by_virtual_record_id("v", visibility=vis)) == want, vis
        assert {r["_key"] for r in await p.get_records_by_record_ids(["live", "gone", "old"], p.org, vis)} == want, vis
        assert await p.get_virtual_record_ids_in_use(["v", "w"], visibility=vis) == {"v"}
    assert await p.get_record_by_external_id("c1", "ext-gone") is None
    assert (await p.get_record_by_external_id("c1", "ext-gone", visibility=RecordVisibility.DELETED)).id == "gone"
    assert await p.get_record_by_external_revision_id("c1", "rv") is None
    assert (await p.get_record_by_external_revision_id("c1", "rv", visibility=RecordVisibility.ALL)).id == "gone"
    assert await p.get_virtual_record_ids_in_use([]) == set() and await p.get_virtual_record_ids_for_record_ids([]) == {}


async def test_no_list_filters_nothing_an_empty_list_returns_nothing_and_a_list_filters(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "a"), code(p.org, "b")])
    await p.batch_update_nodes([{"id": "a", "virtualRecordId": "v"}, {"id": "b", "virtualRecordId": "v"}], "records")
    assert set(await p.get_records_by_virtual_record_id("v", None)) == {"a", "b"}
    assert await p.get_records_by_virtual_record_id("v", []) == []                                  # may read nothing: nothing, never everything
    assert await p.get_records_by_virtual_record_id("v", ["a", "zz"]) == ["a"]
    assert await p.get_virtual_record_ids_for_record_ids(["a", "zz"]) == {"a": "v"}


async def test_children_by_external_parent_and_type(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "k1", parent_external_record_id="par"), plain(p.org, "k2", parent_external_record_id="par"),
                                  code(p.org, "k3", parent_external_record_id="other")])
    assert {r.id for r in await p.get_records_by_parent("c1", "par")} == {"k1", "k2"}
    assert [r.id for r in await p.get_records_by_parent("c1", "par", "CODE_FILE")] == ["k1"]
    assert await p.get_records_by_parent("zz", "par") == []


async def test_a_trashed_holder_gives_up_the_external_id_only_when_asked(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "old", external_record_id="same-ext")])
    await p.batch_update_nodes([{"id": "old", "isDeleted": True}], "records")
    await p.batch_upsert_records([code(p.org, "new", external_record_id="same-ext")])
    assert (await p.get_document("old", "records"))["externalRecordId"] == "same-ext"
    await p.batch_upsert_records([code(p.org, "new", external_record_id="same-ext")], release_trashed_external_ids=True)
    held = await p.get_document("old", "records")
    assert held["trashedExternalRecordId"] == "same-ext" and held["externalRecordId"].endswith("old") and held["externalRecordId"] != "same-ext"
    assert (await p.get_record_by_external_id("c1", "same-ext")).id == "new"


async def test_indexing_status_moves_only_from_the_expected_one_and_one_writer_wins(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, f"s{i}", indexing_status=ProgressStatus.COMPLETED.value if i == 0 else ProgressStatus.IN_PROGRESS.value) for i in range(3)])
    assert await p.compare_and_set_indexing_status([], "COMPLETED", "QUEUED") == []
    moved = await p.compare_and_set_indexing_status(["s0", "s1", "s0", "", "nope"], ProgressStatus.COMPLETED.value, ProgressStatus.QUEUED.value)
    assert moved == ["s0"]
    assert isinstance((await p.get_document("s0", "records"))["queuedAtTimestamp"], int) and "queuedAtTimestamp" not in await p.get_document("s1", "records")
    wins = await asyncio.gather(*(p.compare_and_set_indexing_status(["s1"], ProgressStatus.IN_PROGRESS.value, f"X{i}") for i in range(5)))
    assert sorted(len(w) for w in wins) == [0, 0, 0, 0, 1]                                              # exactly one writer saw the expected status


async def test_a_bulk_status_write_skips_internal_and_placeholder_records(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "n1"), code(p.org, "n2", is_internal=True), plain(p.org, "n3", is_internal=False)])
    await p.batch_update_nodes([{"id": "n3", "isPlaceholder": True}], "records")
    await p.update_indexing_status_for_record_ids(["n1", "n2", "n3", "n1", "zz", ""], ProgressStatus.FAILED.value)
    status = {i: (await p.get_document(i, "records"))["indexingStatus"] for i in ("n1", "n2", "n3")}
    assert status["n1"] == ProgressStatus.FAILED.value and status["n2"] != ProgressStatus.FAILED.value and status["n3"] != ProgressStatus.FAILED.value
    await p.update_indexing_status_for_record_ids([], "X")


async def test_records_indexed_since_needs_a_completed_number_after_the_time(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "i1", indexing_status=ProgressStatus.COMPLETED.value), code(p.org, "i2", indexing_status=ProgressStatus.COMPLETED.value)])
    await p.batch_update_nodes([{"id": "i1", "lastIndexTimestamp": 100}, {"id": "i2", "lastIndexTimestamp": "not a number"}], "records")
    assert await p.has_records_indexed_since("c1", 99) is True
    assert await p.has_records_indexed_since("c1", 100) is False                                       # strictly after
    assert await p.has_records_indexed_since("other", 0) is False


async def test_a_relation_between_records_is_a_link_with_its_times(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "x1"), code(p.org, "x2")])
    await p.create_record_relation("x1", "x2", "BOOKMARK")
    [link] = await p.get_edges_from_node("records/x1", "recordLinks")
    assert link["relationshipType"] == "BOOKMARK" and isinstance(link["createdAtTimestamp"], int) and isinstance(link["updatedAtTimestamp"], int)


async def test_deleting_records_takes_their_type_documents_and_every_edge_and_leaves_blocks(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    for p in (a, b):
        await p.batch_upsert_records([code(p.org, "d1"), code(p.org, "d2")])
        await p.batch_upsert_nodes([{"_key": "blk", "recordId": "d1", "name": "B"}], "blocks")
        await p.batch_create_edges([{"from_id": "d1", "from_collection": "records", "to_id": "d2", "to_collection": "records", "relationshipType": "PARENT_CHILD"},
                                    {"from_id": "d2", "from_collection": "records", "to_id": "d1", "to_collection": "records", "relationshipType": "BOOKMARK"}],
                                   "nodeRelations")
    assert await a.delete_nodes(["d1", "zz"], "records") is False                                       # one of two keys existed, as the count says
    assert await a.get_document("d1", "records") is None and await a.get_document("d1", "codeFiles") is None
    assert await a.get_edges_to_node("records/d2", "nodeRelations") == [] and await a.get_edges_from_node("records/d2", "nodeRelations") == []
    assert await a.get_document("d2", "records") is not None and await a.get_document("blk", "blocks") is not None
    assert await b.get_document("d1", "records") is not None and len(await b.get_edges_to_node("records/d2", "nodeRelations")) == 1       # the other org untouched
    await a.delete_nodes_and_edges(["d2"], "records")
    assert await a.get_document("d2", "codeFiles") is None and await a.get_existing_record_keys(["d1", "d2"]) == set()


async def test_a_sync_point_is_created_once_merged_cleared_and_removed(pg_db) -> None:
    p = bound(pg_db)
    assert await p.get_sync_point("k", "syncPoints") is None
    results = await asyncio.gather(*(p.upsert_sync_point("k", {"cursor": f"c{i}"}, "syncPoints") for i in range(8)))      # two first upserts converge on one point
    assert all(results) and len(await p.get_all_documents("syncPoints")) == 1
    first = (await p.get_sync_point("k", "syncPoints"))["createdAtTimestamp"]
    await p.upsert_sync_point("k", {"cursor": None, "n": 2}, "syncPoints")
    got = await p.get_sync_point("k", "syncPoints")
    assert "cursor" not in got and got["n"] == 2 and got["createdAtTimestamp"] == first and got["syncPointKey"] == "k"
    other = bound(pg_db)
    assert await other.get_sync_point("k", "syncPoints") is None
    await p.remove_sync_point("k", "syncPoints")
    assert await p.get_sync_point("k", "syncPoints") is None
    await p.remove_sync_point("k", "syncPoints")


def filerec(org: str, rid: str, **kw: object) -> FileRecord:
    base = {**COMMON, "id": rid, "org_id": org, "record_type": RecordType.FILE, "external_record_id": f"ext-{rid}", "is_file": True, "extension": "txt"}
    return FileRecord(**{**base, **kw})


async def test_only_top_level_nulls_are_removed_a_nested_null_is_kept(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "r1")])
    with bind(OrgContext(p.org, None, "job", "job:test", "t")):
        await pg_db.run(lambda tx: tx.execute("""UPDATE graph.nodes SET doc = doc || '{"meta": {"a": null, "b": 1}}'::jsonb WHERE org_id = $1 AND coll = 'records' AND id = 'r1'""", p.org), write=True)
    await p.batch_upsert_records([code(p.org, "r1", record_name="again")])
    assert (await p.get_document("r1", "records"))["meta"] == {"a": None, "b": 1}                      # the upsert did not touch the nested value
    await p.upsert_sync_point("k", {"cursor": {"a": None, "b": 2}, "gone": None}, "syncPoints")
    await p.upsert_sync_point("k", {"n": 1}, "syncPoints")
    got = await p.get_sync_point("k", "syncPoints")
    assert got["cursor"] == {"a": None, "b": 2} and "gone" not in got and got["n"] == 1


async def test_a_database_error_in_a_status_claim_raises_so_the_consumer_can_retry(pg_db, monkeypatch) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([code(p.org, "s1", indexing_status=ProgressStatus.COMPLETED.value)])

    async def boom(self, fn, transaction, *, write=False):  # noqa: ANN202
        raise RuntimeError("down")
    with monkeypatch.context() as m:
        m.setattr(type(p._p), "_q", boom)
        with pytest.raises(RuntimeError, match="down"):
            await p.compare_and_set_indexing_status(["s1"], ProgressStatus.COMPLETED.value, ProgressStatus.QUEUED.value)
    assert (await p.get_document("s1", "records"))["indexingStatus"] == ProgressStatus.COMPLETED.value


async def test_batches_of_mixed_types_in_opposite_orders_do_not_deadlock(pg_db) -> None:
    p = bound(pg_db)
    batches = []
    for i in range(12):
        items = [code(p.org, f"m{j}") if j % 2 else filerec(p.org, f"m{j}") for j in range(8)]
        batches.append(items if i % 2 else list(reversed(items)))
    await asyncio.gather(*(p.batch_upsert_records(b) for b in batches))
    assert await p.get_existing_record_keys([f"m{j}" for j in range(8)]) == {f"m{j}" for j in range(8)}
    assert len(await p.get_edges_from_node("records/m1", "isOfType")) >= 1


async def test_a_delete_waits_for_a_writer_that_is_attaching_a_type_and_takes_that_type_too(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_records([plain(p.org, "w1")])                                     # a record with no type document yet
    txn = await p.begin_transaction(read=[], write=[])
    await p.batch_upsert_records([code(p.org, "w1")], transaction=txn)                     # rewrites the record's row and adds its type document, uncommitted
    deleting = asyncio.create_task(p.delete_nodes(["w1"], "records"))
    await asyncio.sleep(0.5)
    assert not deleting.done()                                                             # it waits on the record's row instead of reading the types around the writer
    await p.commit_transaction(txn)
    assert await asyncio.wait_for(deleting, 20) is True
    assert await p.get_document("w1", "codeFiles") is None and await p.get_edges_from_node("records/w1", "isOfType") == []
