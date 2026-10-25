"""A stored-record read after the mapped document was replaced, on real storage.

Needs a running storage service (see ``test_artifact_store_contract.py`` for the
environment: ARTIFACTS_IT_STORAGE_URL, ARTIFACTS_IT_SCOPED_SECRET,
ARTIFACTS_IT_STORAGE_TYPE). The mapping is an in-memory stand-in; the graph side
of the lookup is checked on Neo4j and ArangoDB in
``graph_db/test_record_reader_relookup_real_backends.py``.

On S3 the storage service reads a stored record only through an
``<bucket>.s3.<region>.amazonaws.com`` URL. A service configured with a
path-style endpoint (the usual MinIO setup) writes other URLs, so s3 is skipped
unless ARTIFACTS_IT_S3_VHOST is set for a virtual-hosted setup.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.service import config_node_constants
from app.modules.transformers.blob_storage import (
    BlobStorage,
    SignedUrlReadError,
    StorageDocumentNotFoundError,
    close_shared_session,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
SCOPED_SECRET = os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")
ORG_ID = os.environ.get("ARTIFACTS_IT_ORG_ID", "64d0000000000000000a4f01")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
    pytest.mark.skipif(
        STORAGE_TYPE == "s3" and not os.environ.get("ARTIFACTS_IT_S3_VHOST"),
        reason="the S3 adapter reads only <bucket>.s3.<region>.amazonaws.com URLs, which the storage "
        "service writes unless it is set to a path-style endpoint; set ARTIFACTS_IT_S3_VHOST=1 when "
        "it runs virtual-hosted against an S3-compatible server that answers those host names",
    ),
]

logger = logging.getLogger("relookup-it")
COLLECTION = "virtualRecordToDocIdMapping"


class MappingGraph:
    """Just the mapping reads ``get_document_id_by_virtual_record_id`` makes."""

    def __init__(self) -> None:
        self.mapping: dict[str, dict[str, Any]] = {}
        self.reads = 0

    async def get_document(self, key: str, collection: str, **_kw: object) -> dict[str, Any] | None:
        self.reads += 1
        return self.mapping.get(key)

    async def get_nodes_by_filters(self, *_a: object, **_kw: object) -> list[dict[str, Any]]:
        return []


def _config_service() -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    return service


@pytest.fixture
async def graph() -> MappingGraph:
    return MappingGraph()


@pytest.fixture
async def blob(graph: MappingGraph) -> AsyncIterator[BlobStorage]:
    yield BlobStorage(logger=logger, config_service=_config_service(), graph_provider=graph)
    await close_shared_session()


async def _store(blob: BlobStorage, vrid: str, marker: str) -> tuple[str, int]:
    record = {"record_name": marker, "virtual_record_id": vrid, "block_containers": {"blocks": []}}
    document_id, size = await blob.save_record_to_storage(
        ORG_ID, f"rec-{vrid}", vrid, record, document_path=f"{ORG_ID}/relookup/{vrid}/{marker}",
    )
    assert document_id
    return document_id, size


def _map(graph: MappingGraph, vrid: str, document_id: str, size: int | None) -> dict[str, Any]:
    graph.mapping[vrid] = {"documentId": document_id, "fileSizeBytes": size}
    return {"record_doc_id": document_id, "fileSizeBytes": size}


async def _retire_after_signing(
    blob: BlobStorage, graph: MappingGraph, vrid: str, old: str, new: tuple[str, int]
) -> None:
    """Purge *old* and map *new* once the read has been signed a URL for *old*."""
    real = blob._fetch_record_envelope
    done = False

    async def fetch(*args: object, **kwargs: object) -> dict:
        nonlocal done
        envelope = await real(*args, **kwargs)
        if not done:
            done = True
            _map(graph, vrid, *new)
            await blob.purge_document(ORG_ID, old)
        return envelope

    blob._fetch_record_envelope = fetch


async def test_t12_a_reused_lookup_reads_the_replacement(blob: BlobStorage, graph: MappingGraph) -> None:
    """A lookup taken before a re-index, used after the old document was retired."""
    vrid = str(uuid.uuid4())
    old_id, old_size = await _store(blob, vrid, "old")
    held = _map(graph, vrid, old_id, old_size)
    _map(graph, vrid, *await _store(blob, vrid, "new"))
    await blob.purge_document(ORG_ID, old_id)

    record = await blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)

    assert record["record_name"] == "new"


async def test_t12_control_a_mapped_document_that_is_gone_is_still_a_404(
    blob: BlobStorage, graph: MappingGraph
) -> None:
    vrid = str(uuid.uuid4())
    document_id, size = await _store(blob, vrid, "only")
    held = _map(graph, vrid, document_id, size)
    await blob.purge_document(ORG_ID, document_id)
    graph.reads = 0

    with pytest.raises((StorageDocumentNotFoundError, SignedUrlReadError)) as raised:
        await blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)

    assert graph.reads == 1, "exactly one fresh lookup"
    if STORAGE_TYPE == "local":
        assert isinstance(raised.value, StorageDocumentNotFoundError)


@pytest.mark.skipif(STORAGE_TYPE == "local", reason="local storage serves the record inline: no signed URL")
@pytest.mark.parametrize("size_known", [True, False], ids=["single", "ranged"])
async def test_t20_purge_between_signing_and_fetching(
    blob: BlobStorage, graph: MappingGraph, size_known: bool
) -> None:
    vrid = str(uuid.uuid4())
    old_id, old_size = await _store(blob, vrid, "old")
    new = await _store(blob, vrid, "new")
    # An unknown size is read in ranges, as a large object is.
    held = _map(graph, vrid, old_id, old_size if size_known else None)
    await _retire_after_signing(blob, graph, vrid, old_id, new)
    graph.reads = 0

    record = await blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)

    assert record["record_name"] == "new"
    assert graph.reads == 1


@pytest.mark.skipif(STORAGE_TYPE == "local", reason="local storage serves the record inline: no signed URL")
@pytest.mark.parametrize("size_known", [True, False], ids=["single", "ranged"])
async def test_t20_control_the_mapped_document_is_the_one_that_failed(
    blob: BlobStorage, graph: MappingGraph, size_known: bool
) -> None:
    """Same document after the re-lookup: the original error, never a "document gone"."""
    vrid = str(uuid.uuid4())
    old_id, old_size = await _store(blob, vrid, "old")
    held = _map(graph, vrid, old_id, old_size if size_known else None)
    await _retire_after_signing(blob, graph, vrid, old_id, (old_id, old_size))
    graph.reads = 0

    with pytest.raises(SignedUrlReadError):
        await blob.get_record_from_storage(vrid, ORG_ID, lookup_result=held)

    assert graph.reads == 1
