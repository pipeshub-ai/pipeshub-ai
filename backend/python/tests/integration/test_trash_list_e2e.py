"""The "Recently deleted" list against a real Neo4j and a real ArangoDB.

Drives ``list_trashed_records`` and ``KnowledgeBaseService.list_trash`` over a
real provider, on the collection the restore suite seeds: a folder "Docs" with
two files (one with an attachment) and two files at its root, deleted through
the KB's own ``DataSourceEntitiesProcessor`` with ``ENABLE_SOFT_DELETE`` on.

- Each delete action is one item: a folder with its contents is one row that
  counts them, newest first, with who deleted it and from when the purge may
  remove it.
- A file whose folder went to the trash after it is its own row, naming the
  folder and saying it is in the trash too.
- Paging splits the rows and keeps the total.
- A file organizer's list holds single files only, as that is all they may
  restore.
- Live records, a record marked deleted without a timestamp, and other orgs
  are never listed; a restored item leaves the list.
- A folder of a few hundred deleted files, a multi-select delete of 40 files,
  another of two, and one deleted file: the file organizer's list holds only
  the file, paging keeps its rows and totals, and every read costs a fixed
  amount per record in the trash: Neo4j PROFILE database hits, and ArangoDB
  profile index scans with no full scan, the roots read through their hinted
  index. On Neo4j no statement holds more than
  a few KB, as batches are paged before any root is read; collecting every
  root first held 19 KB here and 160 KB with 400 selected files. Reading every batch member for every record cost about 277,000
  hits for 302 records; ``TRASH_LIST_BIG_FOLDER_FILES`` changes the size.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  cd backend/python && pytest tests/integration/test_trash_list_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import TRASH_LIST_INDEX
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration import test_soft_delete_restore_e2e as restore_suite

if TYPE_CHECKING:
    from tests.integration.test_soft_delete_restore_e2e import _World

# The restore suite's seeded collection, on both databases.
seeded = restore_suite.world

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

DAY_MS = 24 * 60 * 60 * 1000
EARLIER = 1_790_000_000_000
LATER = EARLIER + 60_000
# A deleted folder big enough that reading its files once per file shows.
BIG_FOLDER_FILES = int(os.environ.get("TRASH_LIST_BIG_FOLDER_FILES", "300"))
# The Neo4j reads a page may cost, per record in the trash.
MAX_DB_HITS_PER_TRASHED_RECORD = 40
# The ArangoDB index entries a page may read, per record in the trash.
MAX_ARANGO_INDEX_SCANS_PER_TRASHED_RECORD = 12
# A multi-select delete of loose files beside the big folder.
MULTI_SELECT_FILES = int(os.environ.get("TRASH_LIST_MULTI_SELECT_FILES", "40"))
# Memory one Neo4j statement of the list may hold, whatever the number of items in the trash.
MAX_NEO4J_STATEMENT_MEMORY_BYTES = 8 * 1024


async def _trash_at(w: _World, name: str, when: int, *together: str) -> None:
    """Trash *name* (and *together*, in the same action) with their subtrees, then pin the batch's time."""
    await w.trash(name, *together)
    batch = (await w.stored(name))["deleteBatchId"]
    for key in w.ids:
        doc = await w.stored(key)
        if doc and doc.get("deleteBatchId") == batch:
            await w.graph.update_node(w.ids[key], CollectionNames.RECORDS.value, {"deletedAtTimestamp": when})


def _ids(found: dict) -> list[str]:
    return [item["record"]["_key"] for item in found["items"]]


async def test_each_delete_action_is_one_item_newest_first(seeded: _World) -> None:
    await _trash_at(seeded, "solo", EARLIER)
    await _trash_at(seeded, "folder", LATER)

    found = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id)

    assert found["total"] == 2
    assert _ids(found) == [seeded.ids["folder"], seeded.ids["solo"]]
    folder, solo = found["items"]
    assert (folder["isFile"], folder["batchSize"], folder["parentId"]) == (False, 4, None)
    assert (solo["isFile"], solo["batchSize"], solo["record"]["mimeType"]) == (True, 1, "application/pdf")
    for item in found["items"]:
        assert (item["deletedByName"], item["deletedByEmail"]) == ("Restore Tester", f"{seeded.user_id}@example.com")
        assert item["parentIsDeleted"] is None


async def test_the_service_says_from_when_each_item_may_go(seeded: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOFT_DELETE_PURGE_MIN_AGE_SECONDS", raising=False)
    seeded.service.config_service.get_config = AsyncMock(return_value={"softDeletePurge": {"minAgeDays": 21}})
    await _trash_at(seeded, "folder", EARLIER)

    result = await seeded.service.list_trash(seeded.kb_id, seeded.user_id, seeded.org_id)

    assert result["success"] is True, result
    [item] = result["items"]
    assert (item["id"], item["name"], item["isFolder"], item["itemCount"]) == (seeded.ids["folder"], "Docs", True, 4)
    assert item["deletedBy"] == {"name": "Restore Tester", "email": f"{seeded.user_id}@example.com"}
    assert item["removableAfterTimestamp"] == EARLIER + 21 * DAY_MS
    assert result["pagination"] == {"page": 1, "limit": 25, "totalCount": 1, "totalPages": 1}


async def test_a_file_whose_folder_was_trashed_after_it_names_the_folder(seeded: _World) -> None:
    await _trash_at(seeded, "file_a", EARLIER)
    await _trash_at(seeded, "folder", LATER)

    found = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id)

    assert _ids(found) == [seeded.ids["folder"], seeded.ids["file_a"]]
    folder, file_a = found["items"]
    assert folder["batchSize"] == 3
    assert (file_a["parentId"], file_a["parentName"], file_a["parentIsDeleted"]) == (seeded.ids["folder"], "Docs", True)


async def test_paging_splits_the_rows_and_keeps_the_total(seeded: _World) -> None:
    await _trash_at(seeded, "solo", EARLIER)
    await _trash_at(seeded, "folder", LATER)

    first = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id, skip=0, limit=1)
    second = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id, skip=1, limit=1)
    past = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id, skip=2, limit=1)

    assert (_ids(first), first["total"]) == ([seeded.ids["folder"]], 2)
    assert (_ids(second), second["total"]) == ([seeded.ids["solo"]], 2)
    assert (_ids(past), past["total"]) == ([], 2)


async def test_a_file_organizer_sees_single_files_only(seeded: _World) -> None:
    await _trash_at(seeded, "solo", EARLIER)
    await _trash_at(seeded, "folder", LATER)

    found = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id, single_file_batches_only=True)

    assert (_ids(found), found["total"]) == ([seeded.ids["solo"]], 1)


async def test_live_records_unstamped_deletes_and_other_orgs_are_left_out(seeded: _World) -> None:
    await _trash_at(seeded, "solo", EARLIER)
    # The artifact registry marks a race's loser like this; it is not in the trash.
    await seeded.graph.update_node(seeded.ids["report"], CollectionNames.RECORDS.value, {"isDeleted": True})

    mine = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id)
    theirs = await seeded.graph.list_trashed_records(seeded.kb_id, f"{seeded.org_id}-other")

    assert (_ids(mine), mine["total"]) == ([seeded.ids["solo"]], 1)
    assert theirs == {"items": [], "total": 0}


async def test_a_restored_item_leaves_the_list(seeded: _World) -> None:
    await _trash_at(seeded, "solo", EARLIER)
    await _trash_at(seeded, "folder", LATER)

    restored = await seeded.restore("file_b")

    assert restored["success"] is True, restored
    found = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id)
    assert (_ids(found), found["total"]) == ([seeded.ids["solo"]], 1)


async def _seed_big_folder(w: _World, files: int) -> list[str]:
    """A folder "Big" at the collection root holding *files* files."""
    names = ["big", *(f"big_{i}" for i in range(files))]
    for name in names:
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    await w.graph.batch_upsert_records([
        restore_suite._file(w, "big", folder=True, record_name="Big"),
        *(restore_suite._file(w, name) for name in names[1:]),
    ])
    await restore_suite._link_to_kb(w, tuple(names))
    records = CollectionNames.RECORDS.value
    await w.graph.batch_create_edges(
        [restore_suite._edge(w.ids["big"], records, w.ids[name], records, relationshipType="PARENT_CHILD")
         for name in names[1:]],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )
    return names


def _db_hits(plan: dict) -> int:
    return int(plan.get("dbHits", 0)) + sum(_db_hits(child) for child in plan.get("children", []))


async def _neo4j_db_hits(graph: Neo4jProvider, monkeypatch: pytest.MonkeyPatch, call) -> tuple[object, int, int]:
    """Run *call*, then PROFILE every statement it sent: its result, their database hits, and the most memory one held."""
    sent: list[tuple[str, dict]] = []
    original = graph.client.execute_query

    async def recording(query, parameters=None, txn_id=None, timeout=None) -> list[dict]:
        sent.append((query, parameters or {}))
        return await original(query, parameters=parameters, txn_id=txn_id, timeout=timeout)

    monkeypatch.setattr(graph.client, "execute_query", recording)
    result = await call()
    monkeypatch.setattr(graph.client, "execute_query", original)
    hits = memory = 0
    async with graph.client.driver.session(database=graph.client.database) as session:
        for query, parameters in sent:
            summary = await (await session.run(f"PROFILE {query}", parameters)).consume()
            hits += _db_hits(summary.profile)
            memory = max(memory, int(summary.profile.get("args", {}).get("GlobalMemory") or 0))
    return result, hits, memory


async def _seed_loose_files(w: _World, prefix: str, files: int) -> list[str]:
    """*files* files at the collection root, named ``{prefix}_{i}``."""
    names = [f"{prefix}_{i}" for i in range(files)]
    for name in names:
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    await w.graph.batch_upsert_records([restore_suite._file(w, name) for name in names])
    await restore_suite._link_to_kb(w, tuple(names))
    return names


def _arango_root_indexes(plan: dict) -> list[str]:
    """The indexes the plan reads the trash's roots (``r``) through."""
    return [
        index["name"]
        for node in plan["nodes"]
        if node["type"] == "IndexNode" and node["outVariable"]["name"] == "r"
        for index in node["indexes"]
    ]


async def _arango_scans(graph: object, monkeypatch: pytest.MonkeyPatch, call) -> tuple[object, int, int]:
    """Run *call*, then profile every query it sent; return its result and the full and index scans.

    Each query must also read the roots through the trash index it hints.
    """
    sent: list[tuple[str, dict]] = []
    original = graph.execute_query

    async def recording(query, bind_vars=None, transaction=None, timeout_seconds=None) -> list | None:
        sent.append((query, bind_vars or {}))
        return await original(query, bind_vars=bind_vars, transaction=transaction, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(graph, "execute_query", recording)
    result = await call()
    monkeypatch.setattr(graph, "execute_query", original)
    client = graph.http_client
    session = await client._get_session()
    full = index = 0
    for query, bind_vars in sent:
        async with session.post(
            f"{client.base_url}/_db/{client.database}/_api/cursor",
            json={"query": query, "bindVars": bind_vars, "batchSize": 1000, "options": {"profile": 2}},
        ) as resp:
            body = await resp.json()
        assert _arango_root_indexes(body["extra"]["plan"]) == [TRASH_LIST_INDEX], query
        stats = body["extra"]["stats"]
        full += stats["scannedFull"]
        index += stats["scannedIndex"]
    return result, full, index


async def _bounded(graph: object, monkeypatch: pytest.MonkeyPatch, trashed: int, call) -> dict:
    """Run *call* and check its reads cost a fixed amount per record in the trash."""
    if isinstance(graph, Neo4jProvider):
        result, hits, memory = await _neo4j_db_hits(graph, monkeypatch, call)
        assert hits <= MAX_DB_HITS_PER_TRASHED_RECORD * trashed, f"{hits} database hits for {trashed} records in the trash"
        # Paging the batches before reading any root keeps the trash's roots out of memory.
        assert memory <= MAX_NEO4J_STATEMENT_MEMORY_BYTES, f"a statement held {memory} bytes"
        return result
    result, full, index = await _arango_scans(graph, monkeypatch, call)
    assert full == 0, f"{full} documents read by a full collection scan"
    assert index <= MAX_ARANGO_INDEX_SCANS_PER_TRASHED_RECORD * trashed, (
        f"{index} index entries read for {trashed} records in the trash"
    )
    return result


async def test_a_large_deleted_folder_does_not_slow_a_page_or_hide_a_loose_file(
    seeded: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = seeded.graph
    if isinstance(graph, Neo4jProvider):
        # The production indexes, as ensure_schema creates them on a real install.
        for statement in graph._generate_performance_indexes():
            await graph.client.execute_query(statement)
        await graph.client.execute_query("CALL db.awaitIndexes(300)")
    await _seed_big_folder(seeded, BIG_FOLDER_FILES)
    many = await _seed_loose_files(seeded, "many", MULTI_SELECT_FILES)
    pair = await _seed_loose_files(seeded, "pair", 2)
    await _trash_at(seeded, "solo", EARLIER)
    await _trash_at(seeded, many[0], EARLIER + 60_000, *many[1:])
    await _trash_at(seeded, pair[0], EARLIER + 120_000, *pair[1:])
    await _trash_at(seeded, "big", EARLIER + 180_000)
    trashed = 1 + MULTI_SELECT_FILES + 2 + BIG_FOLDER_FILES + 1
    first_of = {name: min(seeded.ids[n] for n in group) for name, group in (("many", many), ("pair", pair))}

    organizer = await _bounded(graph, monkeypatch, trashed, lambda: graph.list_trashed_records(
        seeded.kb_id, seeded.org_id, single_file_batches_only=True,
    ))
    assert (_ids(organizer), organizer["total"]) == ([seeded.ids["solo"]], 1)

    first = await _bounded(graph, monkeypatch, trashed, lambda: graph.list_trashed_records(
        seeded.kb_id, seeded.org_id, skip=0, limit=2,
    ))
    second = await _bounded(graph, monkeypatch, trashed, lambda: graph.list_trashed_records(
        seeded.kb_id, seeded.org_id, skip=2, limit=2,
    ))
    assert (_ids(first), first["total"]) == ([seeded.ids["big"], first_of["pair"]], 4)
    assert (_ids(second), second["total"]) == ([first_of["many"], seeded.ids["solo"]], 4)
    big, two = first["items"]
    lots, solo = second["items"]
    assert (big["rootCount"], big["batchSize"], big["otherRootNames"]) == (1, BIG_FOLDER_FILES + 1, [])
    assert (two["rootCount"], two["batchSize"], len(two["otherRootNames"])) == (2, 2, 1)
    by_key = sorted(many, key=lambda n: seeded.ids[n])
    assert (lots["rootCount"], lots["batchSize"]) == (MULTI_SELECT_FILES, MULTI_SELECT_FILES)
    assert lots["otherRootNames"] == [f"{name}.pdf" for name in by_key[1:4]]
    assert (solo["rootCount"], solo["batchSize"]) == (1, 1)


async def test_a_multi_select_delete_is_one_row_and_restores_exactly_what_it_says(seeded: _World) -> None:
    """Two files and a folder deleted in one action are one batch, so one row: restore brings back all of it."""
    await seeded.trash("solo", "report", "folder")
    together = ("solo", "report", "folder", "file_a", "file_b", "attachment")

    found = await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id)

    assert found["total"] == 1
    [row] = found["items"]
    roots = {seeded.ids[name] for name in ("solo", "report", "folder")}
    assert row["record"]["_key"] == min(roots)
    assert (row["rootCount"], row["batchSize"]) == (3, len(together))
    names = {"solo": "solo.pdf", "report": "report.pdf", "folder": "Docs"}
    others = {names[n] for n in names if seeded.ids[n] != row["record"]["_key"]}
    assert set(row["otherRootNames"]) == others

    listed = await seeded.service.list_trash(seeded.kb_id, seeded.user_id, seeded.org_id)
    [item] = listed["items"]
    assert (item["id"], item["itemCount"], item["rootCount"]) == (row["record"]["_key"], 6, 3)

    restored = await seeded.service.restore_record(item["id"], seeded.user_id, seeded.org_id)

    assert restored["success"] is True, restored
    assert {r["recordId"] for r in restored["restoredRecords"]} == {seeded.ids[n] for n in together}
    assert len(restored["restoredRecords"]) == item["itemCount"]
    assert await seeded.live(together) == set(together)
    assert await seeded.graph.list_trashed_records(seeded.kb_id, seeded.org_id) == {"items": [], "total": 0}
