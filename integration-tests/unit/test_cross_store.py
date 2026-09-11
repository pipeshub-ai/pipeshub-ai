"""Unit tests for the cross-store cleanup helpers (no live services).

These helpers decide whether a deletion test passes, so the properties that
make them trustworthy are worth pinning directly. Three matter most:

* A footprint of nothing must fail rather than be recorded. A helper that
  happily captures "0 embeddings" makes every later assertion pass while
  checking nothing.
* A store the probe cannot inspect must raise, never read as clean.
* A failure must report all four stores, not stop at the first.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from helper.blob_store import BlobProbeUnavailable, BlobStoreProbe
from helper.cross_store import (
    RecordFootprint,
    _virtual_id_of,
    assert_fully_deleted,
    capture_footprint,
)
from helper.mongo_store import MongoStoreProbe

pytestmark = pytest.mark.unit

ORG = "org-1"
VRID = "virtual-abc"


def _footprint(**overrides: object) -> RecordFootprint:
    base = {
        "record_name": "report.pdf",
        "virtual_record_id": VRID,
        "org_id": ORG,
        "connector_id": "conn-1",
        "embedding_count": 3,
        "blob_file_count": 2,
        "mongo_document_count": 1,
    }
    base.update(overrides)
    return RecordFootprint(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------- #
# The join between the stores
# --------------------------------------------------------------------- #


def test_storage_prefix_is_where_blob_and_mongo_both_file_content() -> None:
    assert _footprint().storage_prefix == f"{ORG}/PipesHub/records/{VRID}"


@pytest.mark.parametrize(
    "record",
    [
        {"virtualRecordId": VRID},
        {"virtual_record_id": VRID},
    ],
)
def test_virtual_id_read_from_either_dict_spelling(record: dict) -> None:
    """Providers hand back the graph's camelCase or the model's snake_case."""
    assert _virtual_id_of(record) == VRID


def test_virtual_id_read_from_a_model_object() -> None:
    record = MagicMock(spec=["virtual_record_id"])
    record.virtual_record_id = VRID
    assert _virtual_id_of(record) == VRID


def test_virtual_id_absent_is_none_not_an_exception() -> None:
    assert _virtual_id_of({"name": "no id here"}) is None


# --------------------------------------------------------------------- #
# capture_footprint refuses to snapshot nothing
# --------------------------------------------------------------------- #


def _probes(embeddings: int = 3, files: int = 2, documents: int = 1):
    vector = MagicMock()
    vector.count_for_virtual_record = AsyncMock(return_value=embeddings)
    blob = MagicMock()
    blob.count_under = AsyncMock(return_value=files)
    mongo = MagicMock()
    mongo.count_documents_under_path = AsyncMock(return_value=documents)
    return vector, blob, mongo


@pytest.mark.asyncio
async def test_capture_records_what_every_store_holds() -> None:
    graph = MagicMock()
    graph.get_record_by_name = AsyncMock(return_value={"virtualRecordId": VRID})
    vector, blob, mongo = _probes()

    footprint = await capture_footprint(
        graph, vector, blob, mongo,
        connector_id="conn-1", record_name="report.pdf", org_id=ORG,
    )

    assert footprint.virtual_record_id == VRID
    assert (footprint.embedding_count, footprint.blob_file_count) == (3, 2)


@pytest.mark.asyncio
async def test_capture_fails_when_the_record_was_never_indexed() -> None:
    """Zero embeddings before a delete makes the whole test vacuous."""
    graph = MagicMock()
    graph.get_record_by_name = AsyncMock(return_value={"virtualRecordId": VRID})
    vector, blob, mongo = _probes(embeddings=0)

    with pytest.raises(AssertionError, match="no embeddings before the delete"):
        await capture_footprint(
            graph, vector, blob, mongo,
            connector_id="conn-1", record_name="report.pdf", org_id=ORG,
        )


@pytest.mark.asyncio
async def test_capture_fails_when_the_record_has_no_virtual_id() -> None:
    """Without it only the graph could be checked — the gap this closes."""
    graph = MagicMock()
    graph.get_record_by_name = AsyncMock(return_value={"virtualRecordId": None})
    vector, blob, mongo = _probes()

    with pytest.raises(AssertionError, match="no virtualRecordId"):
        await capture_footprint(
            graph, vector, blob, mongo,
            connector_id="conn-1", record_name="report.pdf", org_id=ORG,
        )


@pytest.mark.asyncio
async def test_capture_fails_when_the_record_is_not_in_the_graph() -> None:
    graph = MagicMock()
    graph.get_record_by_name = AsyncMock(return_value=None)
    vector, blob, mongo = _probes()

    with pytest.raises(AssertionError, match="nothing to delete"):
        await capture_footprint(
            graph, vector, blob, mongo,
            connector_id="conn-1", record_name="gone.pdf", org_id=ORG,
        )


# --------------------------------------------------------------------- #
# A failure reports every store
# --------------------------------------------------------------------- #


def _clean_stores():
    graph = MagicMock()
    graph.assert_record_not_exists = AsyncMock()
    vector = MagicMock()
    vector.assert_embeddings_gone = AsyncMock()
    blob = MagicMock()
    blob.assert_blobs_gone = AsyncMock()
    mongo = MagicMock()
    mongo.assert_documents_under_path_gone = AsyncMock()
    return graph, vector, blob, mongo


@pytest.mark.asyncio
async def test_all_four_clean_passes() -> None:
    graph, vector, blob, mongo = _clean_stores()
    await assert_fully_deleted(_footprint(), graph, vector, blob, mongo)


@pytest.mark.asyncio
async def test_a_graph_only_delete_is_caught() -> None:
    """The exact bug the existing suite cannot see."""
    graph, vector, blob, mongo = _clean_stores()
    vector.assert_embeddings_gone = AsyncMock(
        side_effect=AssertionError("3 embeddings survived")
    )
    blob.assert_blobs_gone = AsyncMock(side_effect=AssertionError("2 files remain"))
    mongo.assert_documents_under_path_gone = AsyncMock(
        side_effect=AssertionError("1 document remains")
    )

    with pytest.raises(AssertionError) as caught:
        await assert_fully_deleted(_footprint(), graph, vector, blob, mongo)

    message = str(caught.value)
    assert "3 of 4 stores" in message
    # Every store is named, so one lagging store reads differently from a
    # deletion that only ever touched the graph.
    for store in ("vector database", "blob storage", "MongoDB"):
        assert store in message, f"{store} missing from the failure report"


@pytest.mark.asyncio
async def test_every_store_is_checked_even_after_one_fails() -> None:
    graph, vector, blob, mongo = _clean_stores()
    vector.assert_embeddings_gone = AsyncMock(side_effect=AssertionError("boom"))

    with pytest.raises(AssertionError):
        await assert_fully_deleted(_footprint(), graph, vector, blob, mongo)

    blob.assert_blobs_gone.assert_awaited_once()
    mongo.assert_documents_under_path_gone.assert_awaited_once()


# --------------------------------------------------------------------- #
# A store that cannot be inspected is not a pass
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_storage_vendor_raises_rather_than_reporting_clean() -> None:
    with pytest.raises(BlobProbeUnavailable, match="No blob probe implemented"):
        await BlobStoreProbe().files_under("some/path", vendor="azureBlob")


# --------------------------------------------------------------------- #
# Soft delete is three states, not two
# --------------------------------------------------------------------- #


def _mongo_returning(doc: dict | None) -> MongoStoreProbe:
    probe = MongoStoreProbe(uri="mongodb://unused", db_name="es")
    probe.find_document = AsyncMock(return_value=doc)  # type: ignore[method-assign]
    return probe


@pytest.mark.asyncio
async def test_soft_delete_flag_reads_true_false_and_missing() -> None:
    assert await _mongo_returning({"isDeleted": True}).is_soft_deleted("d") is True
    assert await _mongo_returning({"isDeleted": False}).is_soft_deleted("d") is False
    assert await _mongo_returning(None).is_soft_deleted("d") is None


@pytest.mark.asyncio
async def test_a_hard_delete_fails_the_soft_delete_assertion() -> None:
    """Gone is a different outcome from flagged, and must not pass as one."""
    with pytest.raises(AssertionError, match="removed outright"):
        await _mongo_returning(None).assert_soft_deleted("d", timeout=0)


@pytest.mark.asyncio
async def test_never_flagged_fails_because_nothing_will_collect_it() -> None:
    with pytest.raises(AssertionError, match="never flagged"):
        await _mongo_returning({"isDeleted": False}).assert_soft_deleted("d", timeout=0)


@pytest.mark.asyncio
async def test_flagged_document_passes() -> None:
    await _mongo_returning({"isDeleted": True}).assert_soft_deleted("d", timeout=0)


# --------------------------------------------------------------------- #
# Identifiers are ObjectIds in MongoDB, not strings
# --------------------------------------------------------------------- #


def test_id_forms_offers_both_spellings_for_an_object_id() -> None:
    """A string query against an ObjectId column matches nothing, silently.

    Found against a live stack: ``orgId`` and ``_id`` are BSON ObjectIds, so
    every count came back zero and the cleanup assertions read that as "the
    store is clean". A false pass, in the direction that hides the bug.
    """
    from bson import ObjectId

    from helper.mongo_store import _id_forms

    raw = "6aa314acbd2b7c50691d22f7"
    forms = _id_forms(raw)
    assert raw in forms
    assert ObjectId(raw) in forms


def test_id_forms_leaves_a_uuid_as_a_string() -> None:
    """Record and virtual ids are UUIDs and have no ObjectId spelling."""
    from helper.mongo_store import _id_forms

    uuid_like = "bd97f387-953c-460d-9194-0885e80598ce"
    assert _id_forms(uuid_like) == [uuid_like]
