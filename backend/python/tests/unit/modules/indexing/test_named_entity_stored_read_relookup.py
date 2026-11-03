"""Named-entity retries read the stored record through the blob store, which
re-looks-up a replaced document once. A job is "gone" only when the mapped
document is itself missing; a 403 on the mapped document is a failure to count,
never a reason to drop the job."""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import named_entity_completion as completion
from app.modules.indexing import named_entity_persist_retry as retry
from app.modules.transformers.blob_storage import (
    BlobStorage,
    SignedUrlReadError,
    StorageDocumentNotFoundError,
)

RECORD = {"_key": "r1", "id": "r1", "orgId": "o1", "virtualRecordId": "v1", "contentRev": "rev-a",
          "recordName": "n", "recordType": "FILE", "origin": "UPLOAD", "connectorName": "KB",
          "connectorId": "c", "externalRecordId": "e", "version": 0}


def _sink(mapping: list[str], by_doc: dict) -> MagicMock:
    bs = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
    bs._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "azureBlob"))
    bs._cached_signed_url = AsyncMock(return_value=None)
    bs.get_document_id_by_virtual_record_id = AsyncMock(
        side_effect=[{"record_doc_id": d, "fileSizeBytes": 10} for d in mapping]
    )

    async def read(org_id, vrid, doc, size, headers, endpoint) -> dict:
        outcome = by_doc[doc]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    bs._read_stored_document = read
    sink = MagicMock()
    sink.blob_storage = bs
    sink.reproject_named_entities = AsyncMock(return_value="written")
    return sink


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=dict(RECORD))
    return graph


@pytest.fixture(autouse=True)
def _record_model() -> Iterator[None]:
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", MagicMock()):
        yield


async def test_retry_reads_the_replacement_when_the_document_was_replaced() -> None:
    # mapping read 1: the old document (the 404), read 2: its replacement.
    sink = _sink(["old", "new"], {"old": StorageDocumentNotFoundError("gone"), "new": {"semantic_metadata": None}})

    outcome = await retry._retry_one(_graph(), sink, "r1", "o1", 100)

    assert outcome == "written"
    sink.reproject_named_entities.assert_awaited_once()


async def test_retry_control_a_mapped_document_that_is_gone_is_gone() -> None:
    sink = _sink(["old", "old"], {"old": StorageDocumentNotFoundError("gone")})

    assert await retry._retry_one(_graph(), sink, "r1", "o1", 100) == "gone"
    sink.reproject_named_entities.assert_not_awaited()


async def test_retry_a_403_on_the_mapped_document_is_a_failure_not_gone() -> None:
    sink = _sink(["old", "old"], {"old": SignedUrlReadError("Failed to retrieve record: status 403", 403)})

    with pytest.raises(SignedUrlReadError):
        await retry._retry_one(_graph(), sink, "r1", "o1", 100)


async def test_completion_reads_the_replacement_when_the_document_was_replaced() -> None:
    sink = _sink(["old", "new"], {"old": StorageDocumentNotFoundError("gone"), "new": {"block_containers": None}})

    outcome = await completion._complete_one(_graph(), sink, None, "r1", "o1", 100, 0)

    assert outcome == "gone"  # the replacement has no blocks: reached and read, not a 404
    assert sink.blob_storage.get_document_id_by_virtual_record_id.await_count == 2


async def test_completion_a_403_on_the_mapped_document_raises_to_be_counted() -> None:
    sink = _sink(["old", "old"], {"old": SignedUrlReadError("Failed to retrieve record: status 403", 403)})

    with pytest.raises(SignedUrlReadError):
        await completion._complete_one(_graph(), sink, None, "r1", "o1", 100, 0)
