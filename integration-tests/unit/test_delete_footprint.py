"""Unit tests for helper/delete_footprint.py (no live services).

The cleanup scenarios lean on these checks, so the properties that keep them
honest are pinned here:

* An empty "before" is a failed precondition (plain AssertionError), never
  ``StoreNotEmptied``, so a strict xfail cannot swallow a fixture that built nothing.
* Data left after a delete raises ``StoreNotEmptied`` for blob and Mongo, which is
  what the strict xfails match.
* A survivor check reports every store that changed, and passes only when none did.
* A record's envelope is counted in the folder MongoDB says it was filed in, and a
  re-count after a delete looks in that same folder.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from helper import delete_footprint as fp
from helper.blob_store import BlobStoreProbe
from helper.cleanup_errors import StoreNotEmptied
from helper.mongo_store import MongoStoreProbe
from helper.vector_store import VectorStoreProbe

pytestmark = pytest.mark.unit

ORG = "org-1"
VRID = "vrid-1"
FOLDER = f"{ORG}/PipesHub/records/kb-1"
PREFIX = f"{FOLDER}/a.md"
FLAT = f"{ORG}/PipesHub/records/{VRID}"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fp, "POLL", 0)


def _stores(points: int = 3, blobs: int = 2, docs: int = 1, *, nodes: int = 2, edges: int = 4):
    graph = AsyncMock()
    graph.count_existing_nodes.return_value = nodes
    graph.count_edges_touching.return_value = edges
    vector = AsyncMock(spec=VectorStoreProbe)
    vector.count_for_virtual_record.return_value = points
    vector.count_for_connector.return_value = points
    blob = AsyncMock(spec=BlobStoreProbe)
    blob.count_under.return_value = blobs
    mongo = AsyncMock(spec=MongoStoreProbe)
    mongo.count_documents_under_path.return_value = docs
    mongo.find_document.return_value = {"_id": "doc"} if docs else None
    mongo.assert_document_present.return_value = {"documentPath": f"{ORG}/PipesHub/uploads"}
    mongo.envelope_path.return_value = PREFIX
    return graph, vector, blob, mongo


RECORD = fp.Tracked(name="a.md", record_id="r1", virtual_record_id=VRID, upload_document_id="doc-1")
GRAPH = fp.GraphFootprint(handles=("records/r1", "files/r1"), edges=4)


async def _capture(stores, **kwargs) -> fp.StoresFootprint:
    graph, vector, blob, mongo = stores
    kwargs.setdefault("within", FOLDER)
    return await fp.capture(GRAPH, vector, blob, mongo, org_id=ORG, records=[RECORD], **kwargs)


@pytest.mark.asyncio
async def test_capture_keys_the_envelope_and_the_original_upload() -> None:
    before = await _capture(_stores())

    assert before.points == {VRID: 3}
    assert before.blobs == {PREFIX: 2, f"{ORG}/PipesHub/uploads/doc-1": 2}
    assert before.documents == {f"prefix:{PREFIX}": 1, "id:doc-1": 1}
    assert before.upload_paths == {"doc-1": f"{ORG}/PipesHub/uploads/doc-1"}
    assert before.envelope_paths == {VRID: PREFIX}


@pytest.mark.asyncio
async def test_capture_reads_the_envelope_folder_inside_the_given_collection() -> None:
    _, _, _, mongo = stores = _stores()
    await _capture(stores)

    mongo.envelope_path.assert_awaited_once_with(ORG, VRID, within=FOLDER)


@pytest.mark.asyncio
async def test_capture_refuses_to_guess_where_the_envelope_is() -> None:
    with pytest.raises(AssertionError, match="No folder to look for"):
        await _capture(_stores(), within=None)


@pytest.mark.asyncio
async def test_capture_uses_a_known_envelope_folder_without_looking_it_up() -> None:
    _, _, _, mongo = stores = _stores()
    before = await _capture(stores, within=None, envelope_paths={VRID: FLAT})

    mongo.envelope_path.assert_not_awaited()
    assert before.blobs[FLAT] == 2 and before.documents[f"prefix:{FLAT}"] == 1


@pytest.mark.asyncio
async def test_gone_check_keys_name_the_folder_the_envelope_was_filed_in() -> None:
    before = await _capture(_stores())

    assert fp.blob_keys_for(before, [RECORD]) == [PREFIX, f"{ORG}/PipesHub/uploads/doc-1"]
    assert fp.document_keys_for(before, [RECORD]) == [f"prefix:{PREFIX}", "id:doc-1"]


@pytest.mark.asyncio
async def test_storage_vendor_is_read_from_the_envelope_folder() -> None:
    *_, mongo = _stores()
    mongo.storage_vendor_under_path.return_value = "s3"
    assert await fp.storage_vendor(mongo, ORG, VRID, within=FOLDER) == "s3"
    mongo.storage_vendor_under_path.assert_awaited_once_with(PREFIX)

    mongo.storage_vendor_under_path.return_value = None
    assert await fp.storage_vendor(mongo, ORG, VRID, within=FOLDER) == "local"


def test_pending_shared_envelopes_still_point_at_the_flat_folder() -> None:
    assert fp.pending_shared_envelopes(ORG, [VRID]) == {VRID: FLAT}


@pytest.mark.asyncio
async def test_an_empty_store_before_the_delete_is_a_precondition_failure() -> None:
    before = await _capture(_stores(blobs=0))

    with pytest.raises(AssertionError) as caught:
        fp.assert_every_store_holds_it(before)
    assert not isinstance(caught.value, StoreNotEmptied)


@pytest.mark.asyncio
async def test_gone_checks_refuse_keys_that_were_empty_before() -> None:
    _, _, blob, mongo = stores = _stores(blobs=0, docs=0)
    before = await _capture(stores)

    for check in (
        fp.assert_blobs_gone(blob, before, [PREFIX], timeout=0),
        fp.assert_documents_gone(mongo, before, [f"prefix:{PREFIX}"], timeout=0),
    ):
        with pytest.raises(AssertionError) as caught:
            await check
        assert not isinstance(caught.value, StoreNotEmptied)


@pytest.mark.asyncio
async def test_leftover_blob_and_documents_raise_store_not_emptied() -> None:
    _, _, blob, mongo = stores = _stores()
    before = await _capture(stores)

    with pytest.raises(StoreNotEmptied):
        await fp.assert_blobs_gone(blob, before, [PREFIX], timeout=0)
    with pytest.raises(StoreNotEmptied):
        await fp.assert_documents_gone(mongo, before, ["id:doc-1"], timeout=0)


@pytest.mark.asyncio
async def test_emptied_stores_pass() -> None:
    graph, vector, blob, mongo = stores = _stores()
    before = await _capture(stores)
    graph.count_existing_nodes.return_value = 0
    graph.count_edges_touching.return_value = 0
    vector.count_for_virtual_record.return_value = 0
    blob.count_under.return_value = 0
    mongo.count_documents_under_path.return_value = 0
    mongo.find_document.return_value = None

    await fp.assert_graph_gone(graph, before.graph, timeout=0)
    await fp.assert_embeddings_gone(vector, before, [VRID], timeout=0)
    await fp.assert_blobs_gone(blob, before, list(before.blobs), timeout=0)
    await fp.assert_documents_gone(mongo, before, list(before.documents), timeout=0)


@pytest.mark.asyncio
async def test_a_dangling_edge_keeps_the_graph_check_failing() -> None:
    graph, *_ = _stores(nodes=0, edges=1)

    with pytest.raises(AssertionError, match="1 edge"):
        await fp.assert_graph_gone(graph, GRAPH, timeout=0)


@pytest.mark.asyncio
async def test_unchanged_survivor_passes_and_any_change_is_reported() -> None:
    graph, vector, blob, mongo = stores = _stores()
    before = await _capture(stores, connector_id="c1")

    await fp.assert_unchanged(
        before, graph, vector, blob, mongo, org_id=ORG, records=[RECORD], connector_id="c1", what="x"
    )

    vector.count_for_virtual_record.return_value = 1
    graph.count_edges_touching.return_value = 3
    with pytest.raises(AssertionError) as caught:
        await fp.assert_unchanged(
            before, graph, vector, blob, mongo, org_id=ORG, records=[RECORD], connector_id="c1", what="x"
        )
    message = str(caught.value)
    assert "graph edges 4 -> 3" in message
    assert f"embeddings {VRID}: 3 -> 1" in message


@pytest.mark.asyncio
async def test_a_survivor_is_recounted_where_its_envelope_was_before_the_delete() -> None:
    graph, vector, blob, mongo = stores = _stores()
    before = await _capture(stores)
    mongo.envelope_path.return_value = f"{FOLDER}/moved.md"

    await fp.assert_unchanged(before, graph, vector, blob, mongo, org_id=ORG, records=[RECORD], what="x")

    mongo.envelope_path.assert_awaited_once()
    assert f"{FOLDER}/moved.md" not in {call.args[0] for call in blob.count_under.await_args_list}


@pytest.mark.asyncio
async def test_a_record_missing_from_the_graph_fails_even_when_counts_match() -> None:
    """Two found files bring two type nodes, which would cover two missing folders in a count."""
    graph = AsyncMock()
    graph.record_node_handles.return_value = ["records/f1", "files/f1", "records/f2", "files/f2"]
    graph.count_edges_touching.return_value = 4

    with pytest.raises(AssertionError, match="folder-1"):
        await fp.graph_footprint_of_records(graph, ["f1", "f2", "folder-1", "folder-2"])

    graph.record_node_handles.return_value = ["Record/f1", "File/f1"]
    footprint = await fp.graph_footprint_of_records(graph, ["f1"])
    assert footprint.handles == ("Record/f1", "File/f1")
