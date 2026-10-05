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
- A folder of a few hundred deleted files beside one deleted file: the file
  organizer's list holds only the file, paging keeps its totals, and on Neo4j
  the reads cost a fixed number of database hits per record in the trash
  (PROFILE). Reading every batch member for every record cost about 277,000
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
# The Neo4j reads a file organizer's first page may cost, per record in the trash.
MAX_DB_HITS_PER_TRASHED_RECORD = 40


async def _trash_at(w: _World, name: str, when: int) -> None:
    """Trash *name* with its subtree, then pin the batch's time so the order is certain."""
    await w.trash(name)
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


async def _neo4j_db_hits(graph: Neo4jProvider, monkeypatch: pytest.MonkeyPatch, call) -> tuple[object, int]:
    """Run *call*, then PROFILE every statement it sent and add up their database hits."""
    sent: list[tuple[str, dict]] = []
    original = graph.client.execute_query

    async def recording(query, parameters=None, txn_id=None, timeout=None) -> list[dict]:
        sent.append((query, parameters or {}))
        return await original(query, parameters=parameters, txn_id=txn_id, timeout=timeout)

    monkeypatch.setattr(graph.client, "execute_query", recording)
    result = await call()
    monkeypatch.setattr(graph.client, "execute_query", original)
    hits = 0
    async with graph.client.driver.session(database=graph.client.database) as session:
        for query, parameters in sent:
            summary = await (await session.run(f"PROFILE {query}", parameters)).consume()
            hits += _db_hits(summary.profile)
    return result, hits


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
    await _trash_at(seeded, "solo", EARLIER)
    await _trash_at(seeded, "big", LATER)

    async def organizer_page() -> dict:
        return await graph.list_trashed_records(seeded.kb_id, seeded.org_id, single_file_batches_only=True)

    if isinstance(graph, Neo4jProvider):
        organizer, hits = await _neo4j_db_hits(graph, monkeypatch, organizer_page)
        trashed = BIG_FOLDER_FILES + 2
        assert hits <= MAX_DB_HITS_PER_TRASHED_RECORD * trashed, (
            f"{hits} database hits for {trashed} records in the trash"
        )
    else:
        organizer = await organizer_page()
    assert (_ids(organizer), organizer["total"]) == ([seeded.ids["solo"]], 1)

    first = await graph.list_trashed_records(seeded.kb_id, seeded.org_id, skip=0, limit=1)
    second = await graph.list_trashed_records(seeded.kb_id, seeded.org_id, skip=1, limit=1)
    assert (_ids(first), first["total"]) == ([seeded.ids["big"]], 2)
    assert first["items"][0]["batchSize"] == BIG_FOLDER_FILES + 1
    assert (_ids(second), second["total"]) == ([seeded.ids["solo"]], 2)
