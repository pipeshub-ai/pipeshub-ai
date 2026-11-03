"""Deleting a record has to clear all four stores.

A record's data lives in four places — the graph, the vector database, blob
storage and MongoDB — and until now only the graph was ever checked after a
delete. These tests check each store separately, so a failure names the store
that kept the data rather than reporting one undifferentiated "cleanup failed".

Records with identical content share one virtual record id, and with it the
embeddings and the processed-record envelope; those stay until the last copy is
deleted. Each record's own uploaded file goes with that record. The same has
to hold when a new copy is uploaded while the original is being deleted, which
is the last class here.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper import cleanup_sources as src
from helper import delete_footprint as fp
from helper.mongo_store import records_folder
from helper.stored_names import stored_name

logger = logging.getLogger("cleanup-record-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]




class TestDeletingOneRecord:
    """The test list's 'Delete a record in collection' scenario, store by store."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_embeddings_are_removed(
        self, indexed_record, kb_client, vector_store
    ) -> None:
        """The one store the delete path does reach."""
        virtual_id = indexed_record["virtual_record_id"]
        await vector_store.assert_embeddings_present(virtual_id)

        kb_client.delete_record(indexed_record["record_id"])

        await vector_store.assert_embeddings_gone(virtual_id, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_node_type_node_and_edges_leave_the_graph(
        self, indexed_record, kb_client, graph_provider
    ) -> None:
        graph_fp = await fp.graph_footprint_of_records(graph_provider, [indexed_record["record_id"]])
        assert len(graph_fp.handles) >= 2 and graph_fp.edges > 0, (
            f"Expected the record, its File node and their edges before the delete; found {graph_fp}."
        )

        kb_client.delete_record(indexed_record["record_id"])

        await fp.assert_graph_gone(graph_provider, graph_fp, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_files_are_removed_from_blob_storage(
        self, indexed_record, kb_client, blob_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        vendor = indexed_record["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        kb_client.delete_record(indexed_record["record_id"])

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_storage_documents_are_removed_from_mongodb(
        self, indexed_record, kb_client, mongo_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        before = await mongo_store.count_documents_under_path(prefix)
        assert before > 0, "No storage documents existed before the delete."

        kb_client.delete_record(indexed_record["record_id"])

        await mongo_store.assert_documents_under_path_gone(prefix, timeout=120)


class TestWhatSurvivesADelete:
    """Deleting one record must not disturb another."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_second_records_embeddings_are_untouched(
        self, indexed_record, second_indexed_record, kb_client, vector_store
    ) -> None:
        """Over-deletion is the quieter failure and needs its own guard.

        A delete that removes too much leaves the other record present in the
        graph and silently unsearchable. Nothing looks broken until someone
        searches for it and it is not there, which is why this is checked
        directly rather than inferred from a total count.
        """
        survivor = second_indexed_record["virtual_record_id"]
        before = await vector_store.assert_embeddings_present(survivor)

        kb_client.delete_record(indexed_record["record_id"])
        await vector_store.assert_embeddings_gone(
            indexed_record["virtual_record_id"], timeout=120
        )

        after = await vector_store.count_for_virtual_record(survivor)
        assert after == before, (
            f"Deleting one record changed another record's embeddings from "
            f"{before} to {after}. The surviving record is still in the graph "
            "but has lost the content that makes it findable."
        )


COPIED = b"# Hood Fitting Notes\n\nA hood is fitted to the bird, never the bird to the hood.\n"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def two_copies(
    kb_client, graph_provider, vector_store, blob_store, mongo_store, test_org_id
) -> AsyncGenerator[dict[str, Any], None]:
    """Two records with identical bytes, so one virtual record id, and an unrelated neighbour."""
    tag = uuid.uuid4().hex[:6]
    kb_id = kb_client.create_kb(f"cleanup-copies-{tag}")["id"]
    try:
        first = await src.upload_to_kb(kb_client, vector_store, kb_id, f"copy-1-{tag}.md", COPIED)
        second = await src.upload_to_kb(kb_client, vector_store, kb_id, f"copy-2-{tag}.md", COPIED)
        neighbour = await src.upload_to_kb(
            kb_client, vector_store, kb_id, f"neighbour-{tag}.md", src.unique_text("neighbour")
        )
        assert second.virtual_record_id == first.virtual_record_id, (
            f"Identical files got different virtual record ids ({first.virtual_record_id}, "
            f"{second.virtual_record_id}): MD5 dedup did not happen, so there is no shared content to test."
        )
        await fp.wait_for_connector_records(graph_provider, kb_id, [first.name, second.name, neighbour.name])
        within = records_folder(test_org_id, kb_id)
        vendor = await fp.storage_vendor(mongo_store, test_org_id, first.virtual_record_id, within=within)

        def graph_of(record):
            return fp.graph_footprint_of_records(graph_provider, [record.record_id])

        copies = await fp.capture_when_stable(
            await graph_of(first), vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[first, second], within=within, vendor=vendor,
        )
        fp.assert_every_store_holds_it(copies)
        yield {
            "kb_id": kb_id,
            "first": first,
            "second": second,
            "copies": copies,
            "second_graph": await graph_of(second),
            "neighbour": neighbour,
            "neighbour_before": await fp.capture_when_stable(
                await graph_of(neighbour), vector_store, blob_store, mongo_store,
                org_id=test_org_id, records=[neighbour], within=within, vendor=vendor,
            ),
            "vendor": vendor,
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def one_copy_deleted(two_copies, kb_client, graph_provider) -> dict[str, Any]:
    kb_client.delete_record(two_copies["first"].record_id)
    await fp.settle(fp.assert_graph_gone(graph_provider, two_copies["copies"].graph), "the first copy")
    return two_copies


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def last_copy_deleted(one_copy_deleted, kb_client, vector_store) -> dict[str, Any]:
    kb_client.delete_record(one_copy_deleted["second"].record_id)
    await fp.settle(
        fp.assert_embeddings_gone(
            vector_store, one_copy_deleted["copies"], [one_copy_deleted["second"].virtual_record_id]
        ),
        "the last copy",
    )
    return one_copy_deleted


class TestDeletingOneOfTwoIdenticalRecords:
    """'VectorDB embeddings / blob deleted if record doesn't have any duplicates', first half."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_deleted_copy_leaves_the_graph(self, one_copy_deleted, graph_provider) -> None:
        await fp.assert_graph_gone(graph_provider, one_copy_deleted["copies"].graph, timeout=60)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_shared_embeddings_envelope_and_documents_stay(
        self, one_copy_deleted, graph_provider, vector_store, blob_store, mongo_store
    ) -> None:
        """Checked store by store against the counts from before; only the deleted copy's upload may go."""
        copies = one_copy_deleted["copies"]
        vrid = one_copy_deleted["second"].virtual_record_id
        path = copies.envelope_paths[vrid]
        assert await vector_store.count_for_virtual_record(vrid) == copies.points[vrid], (
            "Deleting one copy changed the embeddings the other copy still uses."
        )
        assert await blob_store.count_under(path, one_copy_deleted["vendor"]) == copies.blobs[path], (
            "Deleting one copy changed the shared envelope in blob storage."
        )
        assert await mongo_store.count_documents_under_path(path) == copies.documents[f"prefix:{path}"], (
            "Deleting one copy changed the shared envelope's storage documents."
        )
        second_upload = one_copy_deleted["second"].upload_document_id
        assert await mongo_store.find_document(second_upload) is not None, (
            "The surviving copy's own uploaded file lost its storage document."
        )
        upload_path = copies.upload_paths[second_upload]
        assert await blob_store.count_under(upload_path, one_copy_deleted["vendor"]) == copies.blobs[upload_path], (
            "The surviving copy's own uploaded file lost bytes in blob storage."
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_other_copy_is_still_in_the_graph_and_readable(
        self, one_copy_deleted, kb_client, graph_provider
    ) -> None:
        second = one_copy_deleted["second"]
        assert await graph_provider.count_existing_nodes(one_copy_deleted["second_graph"].handles) == len(
            one_copy_deleted["second_graph"].handles
        ), "The surviving copy lost graph nodes."
        record = kb_client.get_record(second.record_id).get("record") or {}
        assert record.get("virtualRecordId") == second.virtual_record_id, (
            f"The surviving copy no longer points at the shared content: {record}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_deleted_copys_own_upload_leaves_blob_storage(
        self, one_copy_deleted, blob_store
    ) -> None:
        """The original file is per record, not shared, so it goes with its record."""
        copies = one_copy_deleted["copies"]
        document_id = one_copy_deleted["first"].upload_document_id
        await fp.assert_blobs_gone(
            blob_store, copies, [copies.upload_paths[document_id]], vendor=one_copy_deleted["vendor"]
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_deleted_copys_own_upload_leaves_mongodb(
        self, one_copy_deleted, mongo_store
    ) -> None:
        copies = one_copy_deleted["copies"]
        document_id = one_copy_deleted["first"].upload_document_id
        await fp.assert_documents_gone(mongo_store, copies, [f"id:{document_id}"])


class TestDeletingTheLastCopy:
    """Second half: once no record uses the content, every store lets go of it."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_embeddings_are_removed(self, last_copy_deleted, vector_store) -> None:
        await fp.assert_embeddings_gone(
            vector_store, last_copy_deleted["copies"], [last_copy_deleted["second"].virtual_record_id],
            timeout=60,
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_last_copy_leaves_the_graph(self, last_copy_deleted, graph_provider) -> None:
        await fp.assert_graph_gone(graph_provider, last_copy_deleted["second_graph"], timeout=60)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_envelope_and_uploads_are_removed_from_blob_storage(
        self, last_copy_deleted, blob_store
    ) -> None:
        copies = last_copy_deleted["copies"]
        records = [last_copy_deleted["first"], last_copy_deleted["second"]]
        await fp.assert_blobs_gone(
            blob_store, copies, fp.blob_keys_for(copies, records), vendor=last_copy_deleted["vendor"]
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_storage_documents_are_removed_from_mongodb(
        self, last_copy_deleted, mongo_store
    ) -> None:
        records = [last_copy_deleted["first"], last_copy_deleted["second"]]
        await fp.assert_documents_gone(
            mongo_store, last_copy_deleted["copies"], fp.document_keys_for(last_copy_deleted["copies"], records)
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_an_unrelated_record_in_the_same_collection_is_untouched(
        self, last_copy_deleted, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        await fp.assert_unchanged(
            last_copy_deleted["neighbour_before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[last_copy_deleted["neighbour"]],
            vendor=last_copy_deleted["vendor"], what="the neighbouring record",
        )


RACED = "# Jess Fitting Notes\n\nA jess is cut to the bird's leg, never the leg to the jess.\n"
RACED_QUERY = "How is a jess fitted to the bird's leg?"

# The window that matters is inside indexing, not at the HTTP layer: the copy's
# duplicate check finds the original, then attaches to its content a moment
# later, while the original's delete asks whether anything still uses that
# content before removing it. Each round sends the delete at a different point
# in the copy's life, so between them it lands both before that check and while
# it runs. Deleting after the copy has finished is TestDeletingOneOfTwoIdenticalRecords.
TIMINGS = ("at_the_same_moment", "just_after_the_upload_returns", "as_the_copy_starts_indexing")
PICKUP_POLL = 0.1
PICKUP_TIMEOUT = 120
# The states an upload leaves a record in before indexing touches it.
UPLOAD_LEAVES = frozenset({"NOT_STARTED", "QUEUED"})


@dataclass
class RaceRound:
    kb_id: str
    vendor: str
    marker: str
    body: bytes
    original: fp.Tracked
    # What every store held for the original, read before the race.
    before: fp.StoresFootprint
    copy_name: str = ""
    copy_id: str = ""
    copy: fp.Tracked | None = None

    @property
    def shares_content(self) -> bool:
        return self.copy is not None and self.copy.virtual_record_id == self.original.virtual_record_id


def _upload_copy(kb_client, kb_id: str, name: str, body: bytes) -> str:
    upload = kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")
    assert upload["summary"]["failed"] == 0, f"Upload of the copy {name} failed: {upload}"
    return str(upload["records"][0]["recordId"])


async def _until_indexing_picks_up(kb_client, record_id: str) -> tuple[Any, Any]:
    """Return the first state of the record that shows indexing has picked it up.

    The upload leaves the record ``NOT_STARTED`` or, once its event is acked
    (before the upload call returns), ``QUEUED``; neither means indexing began.
    The duplicate check writes ``md5Checksum`` just before it looks for the
    original, so a change there is the closest outside sign of that check.
    """

    def state() -> tuple[Any, Any]:
        record = (kb_client.get_record(record_id).get("record") or {})
        return record.get("indexingStatus"), record.get("md5Checksum")

    first = await asyncio.to_thread(state)
    deadline = asyncio.get_event_loop().time() + PICKUP_TIMEOUT
    now = first
    while asyncio.get_event_loop().time() < deadline:
        if now[0] not in UPLOAD_LEAVES or now[1] != first[1]:
            return now
        await asyncio.sleep(PICKUP_POLL)
        now = await asyncio.to_thread(state)
    raise AssertionError(f"Indexing never picked up the copy {record_id} within {PICKUP_TIMEOUT}s.")


async def _race(kb_client, race: RaceRound, timing: str) -> str:
    """Upload the copy and delete the original, close together; returns the copy's record id."""
    original_id = race.original.record_id
    if timing == "at_the_same_moment":
        start = threading.Barrier(2, timeout=60)

        def upload() -> str:
            start.wait()
            return _upload_copy(kb_client, race.kb_id, race.copy_name, race.body)

        def delete() -> None:
            start.wait()
            kb_client.delete_record(original_id)

        copy_id, _ = await asyncio.gather(asyncio.to_thread(upload), asyncio.to_thread(delete))
        return copy_id

    copy_id = await asyncio.to_thread(_upload_copy, kb_client, race.kb_id, race.copy_name, race.body)
    if timing == "as_the_copy_starts_indexing":
        seen = await _until_indexing_picks_up(kb_client, copy_id)
        logger.info("Deleting the original once the copy showed %s", seen)
    await asyncio.to_thread(kb_client.delete_record, original_id)
    return copy_id


async def _until_counts_stop_changing(vector_store, mongo_store, org_id: str, vrids: set[str]) -> None:
    """Give a delete that is still releasing content time to finish; the tests assert the outcome."""

    async def read() -> dict[str, tuple[int, int]]:
        return {
            v: (
                await vector_store.count_for_virtual_record(v),
                len(await mongo_store.stored_record_documents(org_id, virtual_record_id=v)),
            )
            for v in sorted(vrids)
        }

    previous = await read()
    for _ in range(12):
        await asyncio.sleep(fp.POLL)
        current = await read()
        if current == previous:
            return
        previous = current
    logger.warning("Store counts for %s still changing; last read %s", sorted(vrids), previous)


async def _settle(race: RaceRound, kb_client, graph_provider, vector_store, mongo_store, org_id: str) -> None:
    original = race.original
    await fp.settle(fp.assert_graph_gone(graph_provider, race.before.graph), f"the original {original.name}")
    await fp.settle(
        fp.wait_for_connector_records(graph_provider, race.kb_id, [stored_name(race.copy_name)], enriched=True),
        f"indexing of the copy {race.copy_name}",
    )
    record = kb_client.get_record(race.copy_id).get("record") or {}
    race.copy = fp.Tracked(
        name=stored_name(race.copy_name),
        record_id=race.copy_id,
        virtual_record_id=record.get("virtualRecordId"),
        upload_document_id=record.get("externalRecordId"),
        status=record.get("indexingStatus"),
    )
    await fp.settle(
        fp.assert_documents_gone(mongo_store, race.before, [f"id:{original.upload_document_id}"]),
        f"the original {original.name}'s upload",
    )
    if race.copy.virtual_record_id and not race.shares_content:
        await fp.settle(
            fp.assert_embeddings_gone(vector_store, race.before, [original.virtual_record_id]),
            f"the original {original.name}'s content",
        )
    vrids = {original.virtual_record_id, race.copy.virtual_record_id} - {None}
    await _until_counts_stop_changing(vector_store, mongo_store, org_id, vrids)
    logger.info(
        "Round in %s: copy %s ended %s on %s content (original's %s)",
        race.kb_id, race.copy_id, race.copy.status,
        "the shared" if race.shares_content else "its own", original.virtual_record_id,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def raced(
    kb_client, graph_provider, vector_store, blob_store, mongo_store, test_org_id
) -> AsyncGenerator[dict[str, RaceRound], None]:
    """One round per timing, each in its own collection with content no other test uploads.

    The originals are indexed side by side to keep the runtime down; the races
    then run one at a time so no round's timing is skewed by another's upload.
    """
    kb_ids = [kb_client.create_kb(f"cleanup-race-{uuid.uuid4().hex[:6]}")["id"] for _ in TIMINGS]
    try:

        async def indexed_original(kb_id: str) -> RaceRound:
            tag = uuid.uuid4().hex[:6]
            marker = f"Reference {uuid.uuid4().hex}"
            body = f"{RACED}\n{marker}\n".encode()
            original = await src.upload_to_kb(kb_client, vector_store, kb_id, f"original-{tag}.md", body)
            # Enriched first: a copy of an original that is still enriching is
            # queued behind it rather than attached, which is not this race.
            await fp.wait_for_connector_records(graph_provider, kb_id, [original.name], enriched=True)
            within = records_folder(test_org_id, kb_id)
            vendor = await fp.storage_vendor(mongo_store, test_org_id, original.virtual_record_id, within=within)
            before = await fp.capture_when_stable(
                await fp.graph_footprint_of_records(graph_provider, [original.record_id]),
                vector_store, blob_store, mongo_store,
                org_id=test_org_id, records=[original], within=within, vendor=vendor,
            )
            fp.assert_every_store_holds_it(before)
            return RaceRound(
                kb_id=kb_id, vendor=vendor, marker=marker, body=body, original=original,
                before=before, copy_name=f"copy-{tag}.md",
            )

        rounds = dict(zip(TIMINGS, await asyncio.gather(*(indexed_original(k) for k in kb_ids))))
        for timing, race in rounds.items():
            race.copy_id = await _race(kb_client, race, timing)
        await asyncio.gather(*(
            _settle(race, kb_client, graph_provider, vector_store, mongo_store, test_org_id)
            for race in rounds.values()
        ))
        yield rounds
    finally:
        for kb_id in kb_ids:
            try:
                kb_client.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
                logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


def _search_hits(search_client, kb_id: str) -> list[dict[str, Any]]:
    response = search_client.search(RACED_QUERY, limit=10, filters={"kb": [kb_id]})
    assert response.status_code == 200, f"Search failed with HTTP {response.status_code}: {response.text[:400]}"
    return (response.json().get("searchResponse") or {}).get("searchResults") or []


@pytest.mark.parametrize("timing", TIMINGS)
class TestAddingACopyWhileTheOriginalIsDeleted:
    """'Race conditions — a duplicate file record is added at the same time an existing record is removed.'

    Either side may win. If the copy's duplicate check still saw the original,
    the copy shares its content and the delete must leave that content alone;
    if not, the copy is indexed as new content and the original's goes with
    it. The tests hold for both, and fail on the one outcome that is wrong: a
    copy left pointing at content the original's cleanup removed.
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_original_and_its_edges_leave_the_graph(self, raced, timing, graph_provider) -> None:
        await fp.assert_graph_gone(graph_provider, raced[timing].before.graph, timeout=60)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_copy_is_listed_and_indexed(self, raced, timing, kb_client, graph_provider) -> None:
        race = raced[timing]
        listed = {str(item.get("id")) for item in kb_client.list_records(race.kb_id).get("items") or []}
        assert listed == {race.copy_id}, (
            f"The collection should list only the copy {race.copy_id} once the original is deleted; it lists {listed}."
        )
        record = kb_client.get_record(race.copy_id).get("record") or {}
        assert record.get("indexingStatus") == "COMPLETED" and record.get("virtualRecordId"), (
            f"The copy did not finish indexing: indexingStatus {record.get('indexingStatus')}, "
            f"virtualRecordId {record.get('virtualRecordId')}."
        )
        copy_graph = await fp.graph_footprint_of_records(graph_provider, [race.copy_id])
        assert copy_graph.edges > 0, f"The copy is in the graph with no edges: {copy_graph}."

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_copy_is_searchable(self, raced, timing, vector_store, search_client) -> None:
        """Checked through the search API as well as the vector database.

        Points that exist but resolve to no record a user can open are not
        searchable, and the copy is the only record left to resolve them to.
        """
        race = raced[timing]
        vrid = race.copy.virtual_record_id if race.copy else None
        assert vrid, "The copy has no virtual record id, so nothing can be searched for it."
        texts = await vector_store.content_texts(vrid)
        assert any(race.marker in text for text in texts), (
            f"The copy's content {vrid} has {len(texts)} chunk(s) in the vector database and none holds "
            f"{race.marker!r}{' (shared with the deleted original)' if race.shares_content else ''}."
        )
        deadline = asyncio.get_event_loop().time() + 60
        hits = _search_hits(search_client, race.kb_id)
        while not hits and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(fp.POLL)
            hits = _search_hits(search_client, race.kb_id)
        found = {(hit.get("metadata") or {}).get("recordId") for hit in hits}
        assert race.copy_id in found, (
            f"Searching the collection does not return the copy {race.copy_id}; the hits came from {found or 'nothing'}."
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_copys_content_is_stored_exactly_once(
        self, raced, timing, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        race = raced[timing]
        vrid = race.copy.virtual_record_id if race.copy else None
        assert vrid, "The copy has no virtual record id, so no stored content can be its."
        path = await mongo_store.envelope_path(
            test_org_id, vrid, within=records_folder(test_org_id, race.kb_id), timeout=30
        )
        envelopes = [
            d for d in await mongo_store.stored_record_documents(test_org_id, virtual_record_id=vrid)
            if d.get("documentName") == f"record_{vrid}"
        ]
        assert len(envelopes) == 1, f"The copy's content has {len(envelopes)} envelope documents, not one: {envelopes}"
        blobs = await blob_store.count_under(path, race.vendor)
        documents = await mongo_store.count_documents_under_path(path)
        assert blobs and documents, (
            f"The copy's content under {path!r} has {blobs} blob file(s) and {documents} storage document(s)."
        )
        if race.shares_content:
            # Shared, so the original's cleanup must have left every piece of it,
            # where it was: a record delete does not move stored content.
            was = race.before
            assert path == was.envelope_paths[vrid], (
                f"The shared content {vrid} moved from {was.envelope_paths[vrid]!r} to {path!r}."
            )
            now = {"embeddings": await vector_store.count_for_virtual_record(vrid), "blob files": blobs,
                   "storage documents": documents}
            then = {"embeddings": was.points[vrid], "blob files": was.blobs[path],
                    "storage documents": was.documents[f"prefix:{path}"]}
            assert now == then, (
                f"Deleting the original took part of the content the copy shares ({vrid}): before {then}, now {now}."
            )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_copys_own_upload_is_kept(self, raced, timing, blob_store, mongo_store) -> None:
        race = raced[timing]
        document_id = race.copy.upload_document_id if race.copy else None
        assert document_id, "The copy has no externalRecordId pointing at its uploaded file."
        doc = await mongo_store.find_document(document_id)
        assert doc is not None, f"The copy's uploaded file {document_id} has no storage document."
        upload_path = f"{doc['documentPath']}/{document_id}"
        await blob_store.assert_blobs_present(upload_path, race.vendor)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_nothing_of_the_original_is_left_behind(
        self, raced, timing, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        race = raced[timing]
        original, before = race.original, race.before
        upload = original.upload_document_id
        await fp.assert_documents_gone(mongo_store, before, [f"id:{upload}"])
        await fp.assert_blobs_gone(blob_store, before, [before.upload_paths[upload]], vendor=race.vendor)
        vrid = race.copy.virtual_record_id if race.copy else None
        if vrid and not race.shares_content:
            old = before.envelope_paths[original.virtual_record_id]
            await fp.assert_embeddings_gone(vector_store, before, [original.virtual_record_id], timeout=60)
            await fp.assert_blobs_gone(blob_store, before, [old], vendor=race.vendor)
            await fp.assert_documents_gone(mongo_store, before, [f"prefix:{old}"])
        stored = await mongo_store.stored_record_documents(test_org_id, under=records_folder(test_org_id, race.kb_id))
        in_use = {f"record_{vrid}", f"metadata_{vrid}"} if vrid else set()
        strays = sorted({str(d.get("documentName")) for d in stored} - in_use)
        assert not strays, f"The collection still stores content no record uses: {strays}"
