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
* Content a survivor shares with a deleted record is counted where it was filed,
  must hold something before the delete, and moving it is reported as a change.
* Shared content a connector or collection delete rebuilds under its surviving
  holder must turn up inside that holder's folder with every count it had before.
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


@pytest.mark.asyncio
async def test_envelope_location_gives_the_folder_and_its_backend() -> None:
    *_, mongo = _stores()
    mongo.storage_vendor_under_path.return_value = "azureBlob"

    assert await fp.envelope_location(mongo, ORG, VRID, within=FOLDER) == (PREFIX, "azureBlob")
    mongo.envelope_path.assert_awaited_once_with(ORG, VRID, within=FOLDER)


SURVIVOR_FOLDER = f"{ORG}/PipesHub/records/kb-2"
OWN_VRID = "vrid-own"
OWN_PATH = f"{SURVIVOR_FOLDER}/own.md"
SURVIVORS = [
    fp.Tracked(name="copy.md", record_id="r2", virtual_record_id=VRID),
    fp.Tracked(name="own.md", record_id="r3", virtual_record_id=OWN_VRID),
]


def _survivor_stores(blobs_by_path: dict[str, int], docs_by_path: dict[str, int] | None = None):
    docs = blobs_by_path if docs_by_path is None else docs_by_path
    stores = _stores()
    _, _, blob, mongo = stores
    blob.count_under.side_effect = lambda path, vendor: blobs_by_path.get(path, 0)
    mongo.count_documents_under_path.side_effect = lambda path: 1 if docs.get(path) else 0
    mongo.envelope_path.return_value = OWN_PATH
    return stores


async def _survivor_capture(stores) -> fp.StoresFootprint:
    graph, vector, blob, mongo = stores
    return await fp.capture(
        GRAPH, vector, blob, mongo, org_id=ORG, records=SURVIVORS,
        within=SURVIVOR_FOLDER, envelope_paths={VRID: PREFIX},
    )


@pytest.mark.asyncio
async def test_a_survivor_counts_shared_content_where_the_deleted_copy_filed_it() -> None:
    _, _, _, mongo = stores = _survivor_stores({PREFIX: 2, OWN_PATH: 2})
    before = await _survivor_capture(stores)

    mongo.envelope_path.assert_awaited_once_with(ORG, OWN_VRID, within=SURVIVOR_FOLDER)
    assert before.envelope_paths == {VRID: PREFIX, OWN_VRID: OWN_PATH}
    assert before.blobs == {PREFIX: 2, OWN_PATH: 2}
    assert before.documents == {f"prefix:{PREFIX}": 1, f"prefix:{OWN_PATH}": 1}
    fp.assert_shared_envelope_counted(before, VRID)


@pytest.mark.asyncio
async def test_shared_content_leaving_where_it_was_fails_the_survivor_check() -> None:
    """Moving it under the survivor's name is a change the check must report, not absorb."""
    graph, vector, blob, mongo = stores = _survivor_stores({PREFIX: 2, OWN_PATH: 2})
    before = await _survivor_capture(stores)
    moved = {OWN_PATH: 2, f"{SURVIVOR_FOLDER}/copy.md": 2}
    blob.count_under.side_effect = lambda path, vendor: moved.get(path, 0)
    mongo.count_documents_under_path.side_effect = lambda path: 1 if moved.get(path) else 0

    with pytest.raises(AssertionError) as caught:
        await fp.assert_unchanged(before, graph, vector, blob, mongo, org_id=ORG, records=SURVIVORS, what="x")
    message = str(caught.value)
    assert f"blob files {PREFIX}: 2 -> 0" in message
    assert f"storage documents prefix:{PREFIX}: 1 -> 0" in message


@pytest.mark.parametrize(
    ("blobs_by_path", "docs_by_path", "missing", "present"),
    [
        ({OWN_PATH: 2}, {PREFIX: 1, OWN_PATH: 1}, "blob files", "storage documents"),
        ({PREFIX: 2, OWN_PATH: 2}, {OWN_PATH: 1}, "storage documents", "blob files"),
    ],
)
@pytest.mark.asyncio
async def test_shared_content_counted_as_nothing_before_the_delete_is_refused(
    blobs_by_path: dict[str, int], docs_by_path: dict[str, int], missing: str, present: str
) -> None:
    before = await _survivor_capture(_survivor_stores(blobs_by_path, docs_by_path))

    with pytest.raises(AssertionError, match=missing) as caught:
        fp.assert_shared_envelope_counted(before, VRID)
    assert present not in str(caught.value)
    assert not isinstance(caught.value, StoreNotEmptied)


def test_shared_content_that_was_never_counted_is_refused() -> None:
    before = fp.StoresFootprint(GRAPH, None, points={VRID: 3})

    with pytest.raises(AssertionError, match="prove nothing"):
        fp.assert_shared_envelope_counted(before, VRID)


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


@pytest.mark.asyncio
async def test_a_connector_footprint_can_leave_out_a_record_and_its_type_node() -> None:
    graph = AsyncMock()
    graph.connector_node_handles.return_value = ["Record/r2", "File/r2", "Record/r3", "File/r3", "RecordGroup/g"]
    graph.record_node_handles.return_value = ["Record/r2", "File/r2"]
    graph.count_edges_touching.return_value = 5

    footprint = await fp.graph_footprint_of_connector(graph, "kb-2", excluding_records=["r2"])

    assert footprint.handles == ("Record/r3", "File/r3", "RecordGroup/g")
    graph.count_edges_touching.assert_awaited_once_with(footprint.handles)
    graph.record_node_handles.return_value = graph.connector_node_handles.return_value
    with pytest.raises(AssertionError, match="leaves nothing"):
        await fp.graph_footprint_of_connector(graph, "kb-2", excluding_records=["r2", "r3"])


def test_store_changes_read_a_moved_folder_at_its_new_place() -> None:
    new = f"{SURVIVOR_FOLDER}/copy.md"
    before = fp.StoresFootprint(GRAPH, None, {VRID: 3}, {PREFIX: 2}, {f"prefix:{PREFIX}": 1})
    after = fp.StoresFootprint(GRAPH, None, {VRID: 3}, {new: 2}, {f"prefix:{new}": 1})

    assert fp.store_changes(before, after, moved={PREFIX: new}) == []
    assert fp.store_changes(before, after) == [
        f"blob files {PREFIX}: 2 -> None",
        f"storage documents prefix:{PREFIX}: 1 -> None",
    ]
    short = fp.StoresFootprint(GRAPH, None, {VRID: 3}, {new: 1}, {f"prefix:{new}": 1})
    assert fp.store_changes(before, short, moved={PREFIX: new}) == [f"blob files {PREFIX} (now {new}): 2 -> 1"]


REBUILT = f"{SURVIVOR_FOLDER}/copy.md"
HOLDER = fp.Tracked(name="copy.md", record_id="r2", virtual_record_id=VRID)


async def _rebuild_stores(counts_by_path: dict[str, int], *, rebuilt_at: str = REBUILT):
    """Shared content counted under the deleted copy's folder, then the stores as they stand after the rebuild."""
    graph, vector, blob, mongo = stores = _survivor_stores({PREFIX: 2})
    before = await fp.capture(
        GRAPH, vector, blob, mongo, org_id=ORG, records=[HOLDER], envelope_paths={VRID: PREFIX}
    )
    blob.count_under.side_effect = lambda path, vendor: counts_by_path.get(path, 0)
    mongo.count_documents_under_path.side_effect = lambda path: 1 if counts_by_path.get(path) else 0
    mongo.envelope_path.return_value = rebuilt_at
    graph.get_record_by_name.return_value = {"id": "r2", "recordName": "copy.md", "indexingStatus": "COMPLETED"}
    return before, stores


async def _assert_rebuilt(before, stores) -> None:
    graph, vector, blob, mongo = stores
    await fp.assert_rebuilt(
        before, graph, vector, blob, mongo, org_id=ORG, connector_id="kb-2", holder=HOLDER, what="x"
    )


@pytest.mark.asyncio
async def test_shared_content_rebuilt_whole_under_the_survivor_passes() -> None:
    before, stores = await _rebuild_stores({REBUILT: 2})
    graph, *_, mongo = stores
    graph.count_edges_touching.return_value = 9

    await _assert_rebuilt(before, stores)

    mongo.envelope_path.assert_awaited_with(ORG, VRID, within=SURVIVOR_FOLDER, timeout=480)
    graph.get_record_by_name.assert_awaited_with("kb-2", "copy.md")


@pytest.mark.asyncio
async def test_shared_content_not_rebuilt_whole_is_reported() -> None:
    before, stores = await _rebuild_stores({REBUILT: 1})
    stores[1].count_for_virtual_record.return_value = 0

    with pytest.raises(AssertionError) as caught:
        await _assert_rebuilt(before, stores)
    message = str(caught.value)
    assert f"blob files {PREFIX} (now {REBUILT}): 2 -> 1" in message
    assert f"embeddings {VRID}: 3 -> 0" in message


@pytest.mark.parametrize("rebuilt_at", [FLAT, f"{ORG}/PipesHub/records/kb-2-old/copy.md"])
@pytest.mark.asyncio
async def test_shared_content_rebuilt_outside_the_survivors_folder_is_reported(rebuilt_at: str) -> None:
    before, stores = await _rebuild_stores({rebuilt_at: 2}, rebuilt_at=rebuilt_at)

    with pytest.raises(AssertionError, match="not under"):
        await _assert_rebuilt(before, stores)


@pytest.mark.asyncio
async def test_a_rebuilt_holder_missing_from_the_graph_is_reported() -> None:
    before, stores = await _rebuild_stores({REBUILT: 2})
    stores[0].count_existing_nodes.return_value = 1

    with pytest.raises(AssertionError, match="graph nodes 2 -> 1"):
        await _assert_rebuilt(before, stores)
