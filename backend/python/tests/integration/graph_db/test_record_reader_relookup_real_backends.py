"""The reader's fresh lookup after a replaced document, on a real Neo4j and ArangoDB.

The mapping is written with the stored write's own switch (``_switch_mapping``) and read back with
``get_document_id_by_virtual_record_id`` through the production providers, so
what the re-lookup sees is what a second write really left in the graph. Storage
is a stand-in that answers 404 for retired documents: the storage routers are
checked in ``tests/integration/test_record_reader_relookup_storage.py``.

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.transformers.blob_storage import (
    BlobStorage,
    StorageDocumentNotFoundError,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "record_reader_relookup_it"
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
logger = logging.getLogger("relookup-graph-it")


class _World:
    def __init__(self, graph: IGraphDBProvider) -> None:
        self.graph = graph
        self.blob = BlobStorage(logger=logger, config_service=AsyncMock(), graph_provider=graph)
        self.live: set[str] = set()
        self.vrids: list[str] = []
        self.blob._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
        self.blob._cached_signed_url = AsyncMock(return_value=None)

        async def fetch(session, url, headers, vrid) -> dict:
            doc = url.split("/internal/")[1].split("/")[0]
            if doc not in self.live:
                raise StorageDocumentNotFoundError(doc)
            return {"record": {"doc": doc}}

        self.blob._fetch_record_envelope = fetch
        self.blob._decode_record = AsyncMock(side_effect=lambda data, lease: {"record_name": data["record"]["doc"]})

    async def map(self, vrid: str, doc: str, size: int = 10) -> None:
        self.live.add(doc)
        if vrid not in self.vrids:
            self.vrids.append(vrid)
        # As a stored write switches the mapping to a document: one compare-and-set on what was read.
        node = await self.graph.get_document(vrid, MAPPING)
        fields = self.blob._switch_fields(
            node, document_id=doc, file_size_bytes=size, stored_rev=None, run="none:it", replaced=None, now=1,
        )
        assert await self.blob._switch_mapping("org", vrid, node, fields)


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        w = _World(graph)

        async def _remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_nodes(w.vrids, MAPPING)

        cleanup.push_async_callback(_remove)
        yield w


async def test_t12_a_lookup_held_across_a_re_index_reads_the_replacement(world: _World) -> None:
    vrid = f"vr-{uuid.uuid4().hex[:10]}"
    await world.map(vrid, "doc-old")
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)
    await world.map(vrid, "doc-new")
    world.live.discard("doc-old")

    record = await world.blob.get_record_from_storage(vrid, "org", lookup_result=held)

    assert record == {"record_name": "doc-new"}


async def test_t12_control_the_mapped_document_gone_is_still_a_404(world: _World) -> None:
    vrid = f"vr-{uuid.uuid4().hex[:10]}"
    await world.map(vrid, "doc-only")
    held = await world.blob.get_document_id_by_virtual_record_id(vrid)
    world.live.clear()

    with pytest.raises(StorageDocumentNotFoundError):
        await world.blob.get_record_from_storage(vrid, "org", lookup_result=held)


async def test_the_path_lookup_follows_a_replaced_document(world: _World) -> None:
    vrid = f"vr-{uuid.uuid4().hex[:10]}"
    await world.map(vrid, "doc-old")
    await world.map(vrid, "doc-new")
    paths = {"doc-old": None, "doc-new": "org/PipesHub/records/x"}
    world.blob._get_current_document_path = AsyncMock(side_effect=lambda org, doc, strict=False: paths[doc])
    # The first lookup is served by a mapping that still named the old document.
    real = world.blob.get_document_id_by_virtual_record_id
    answers = [{"record_doc_id": "doc-old", "fileSizeBytes": 10}]

    async def lookup(v) -> dict | None:
        return answers.pop(0) if answers else await real(v)

    world.blob.get_document_id_by_virtual_record_id = lookup

    assert await world.blob.get_actual_content_path("org", vrid) == "records/x"


