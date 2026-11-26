"""search keeps serving the old copy until the new set commits, on a real graph store and a real Qdrant.

The record's points are in Qdrant under two virtual records. A search is a vector query followed by the access check: a hit counts when a
record names its virtual record as its own ``virtualRecordId`` (the graph-first rule search follows; the holder lookup, which also counts a
pending vrid, is not what search uses). The commit is the real
``SwapCommitter`` against the real graph provider. This is the mechanism and the stores, not ``retrieval_service`` itself.

Needs Neo4j or ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) and Qdrant over REST (QDRANT_REST_URL, or QDRANT_IT_HOST and QDRANT_IT_PORT as the other graph-job tests use).
"""
from __future__ import annotations

import contextlib
import logging
import os
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.exceptions.indexing_exceptions import IndexingError
from app.models.entities import Record, RecordType
from app.modules.indexing import keep_old_copy
from app.modules.transformers.swap_commit import SwapCommitter
from app.services.vector_db.models import ScrollResult
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support import it_backends
from tests.support.require_service import missing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client.models import (  # noqa: E402
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

RECORDS = CollectionNames.RECORDS.value
ORG = "org-search-it"


def _qdrant_url() -> str:
    """QDRANT_REST_URL, else the integration Qdrant on QDRANT_IT_HOST (no default host: unset skips)."""
    return os.environ.get("QDRANT_REST_URL") or f"http://{it_backends.endpoint('QDRANT_IT_HOST')}:{os.environ.get('QDRANT_IT_PORT', '6343')}"

logger = logging.getLogger("keep-old-copy-search-it")
OLD_TEXT, NEW_TEXT = "the old copy of the document", "the new copy of the document"


class _Qdrant:
    """The slice of the vector service the commit reads, over a real Qdrant."""

    def __init__(self, client, name: str) -> None:
        self.client, self.name = client, name

    async def filter_collection(self, must=None, **_: object) -> dict:
        return must

    async def scroll(self, collection_name, scroll_filter, limit, offset=None, with_payload=None) -> ScrollResult:
        points, nxt = self.client.scroll(self.name, scroll_filter=self._flt(scroll_filter), limit=limit, offset=offset, with_payload=True)
        return ScrollResult(points=[SimpleNamespace(payload=p.payload) for p in points], next_offset=str(nxt) if nxt is not None else None)

    async def count_points(self, collection_name, count_filter) -> int:
        return self.client.count(self.name, count_filter=self._flt(count_filter), exact=True).count

    @staticmethod
    def _flt(must: dict) -> Filter:
        return Filter(must=[FieldCondition(key="vrid", match=MatchValue(value=must["virtualRecordId"]))])


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple]:
    url = _qdrant_url()
    try:
        client = qdrant_client.QdrantClient(url=url, timeout=30, prefer_grpc=False)
        client.get_collections()
    except Exception as exc:
        if not (os.environ.get("QDRANT_REST_URL") or os.environ.get("QDRANT_IT_HOST")):
            pytest.skip("no Qdrant is configured for this job (QDRANT_REST_URL or QDRANT_IT_HOST)")  # the offline job; a job that names one must reach it
        missing(f"Qdrant is not reachable at {url}: {exc}")
    try:
        graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, "keep_old_copy_search_it"))
    except Exception as exc:
        backend_unavailable(request.param, exc)
    run = uuid.uuid4().hex[:8]
    name = f"search_{run}"
    client.create_collection(name, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    registry = MagicMock()
    registry.resolve_write_collection.return_value = name
    vs = SimpleNamespace(
        collection_registry=registry, _record_context=lambda org, record: SimpleNamespace(connector_id="conn"),
        vector_db_service=_Qdrant(client, name), collection_locator=MagicMock(), _resync_membership_after_write=AsyncMock(),
        purge_record_vectors=AsyncMock(),
    )
    committer = SwapCommitter(SimpleNamespace(graph_provider=graph, vector_store=vs, blob_storage=MagicMock()))  # type: ignore[arg-type]
    committer._release_unheld = AsyncMock()  # type: ignore[method-assign]
    committer._retire_membership = AsyncMock()  # type: ignore[method-assign]
    try:
        yield graph, client, name, run, committer
    finally:
        with contextlib.suppress(Exception):
            client.delete_collection(name)
        with contextlib.suppress(Exception):
            await graph.delete_nodes([f"{run}-a"], RECORDS)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            await disconnect()


async def _record(graph, key: str, vrid: str, status: str, **more: object) -> None:
    record = Record(
        id=key, org_id=ORG, virtual_record_id=vrid, record_name="r", record_type=RecordType.FILE, external_record_id=key, version=1,
        origin="UPLOAD", connector_id="conn", connector_name=Connectors.KNOWLEDGE_BASE,
    )
    assert await graph.batch_upsert_nodes(
        [{**record.to_arango_base_record(), "virtualRecordId": vrid, "indexingStatus": status, "hasIndexedCopy": True, **more}], RECORDS
    )


def _put(client, name: str, vrid: str, text: str, blocks: list[str]) -> None:
    client.upsert(name, [
        PointStruct(id=uuid.uuid4().hex, vector=[1.0, 0.0], payload={"vrid": vrid, "text": text, "metadata": {"blockId": b, "virtualRecordId": vrid}})
        for b in blocks
    ], wait=True)


async def _search(graph, client, name: str) -> list[str]:
    """The texts a search returns: the vector hits whose virtual record some live record names."""
    hits = client.query_points(name, query=[1.0, 0.0], limit=20, with_payload=True).points
    named = {h.payload["vrid"] for h in hits if await graph.get_nodes_by_filters(RECORDS, {"virtualRecordId": h.payload["vrid"]})}
    return sorted({h.payload["text"] for h in hits if h.payload["vrid"] in named})


def _ctx(key: str, vrid: str, blocks: list[str]) -> SimpleNamespace:
    record = SimpleNamespace(id=key, virtual_record_id=vrid, connector_id="conn", connector_name="KB", external_record_group_id=None)
    return SimpleNamespace(record=record, reconciliation_context=SimpleNamespace(new_metadata={"pointedBlocks": blocks}))


async def test_a_parse_that_fails_midway_leaves_the_old_copy_served(world) -> None:
    graph, client, name, run, committer = world
    key, x, y = f"{run}-a", f"{run}-x", f"{run}-y"
    await _record(graph, key, x, "COMPLETED")
    _put(client, name, x, OLD_TEXT, ["b1", "b2"])
    # the replacement is being written: pending is named, half of its points are in the store
    await _record(graph, key, x, "IN_PROGRESS", pendingVirtualRecordId=y)
    _put(client, name, y, NEW_TEXT, ["b1"])
    assert await _search(graph, client, name) == [OLD_TEXT]  # the new copy is not served, the old one is
    swap = keep_old_copy.SwapRun(key, ORG, x, y)
    with pytest.raises(IndexingError):  # the set is not whole: it is not committed
        await committer.commit(_ctx(key, y, ["b1", "b2"]), swap, {"indexingStatus": "COMPLETED"})
    assert (await graph.get_document(key, RECORDS))["virtualRecordId"] == x
    assert await _search(graph, client, name) == [OLD_TEXT]


async def test_the_commit_swaps_what_search_serves(world) -> None:
    graph, client, name, run, committer = world
    key, x, y = f"{run}-a", f"{run}-x", f"{run}-y"
    _put(client, name, x, OLD_TEXT, ["b1", "b2"])
    _put(client, name, y, NEW_TEXT, ["b1", "b2"])
    await _record(graph, key, x, "IN_PROGRESS", pendingVirtualRecordId=y)
    assert await _search(graph, client, name) == [OLD_TEXT]
    assert await committer.commit(_ctx(key, y, ["b1", "b2"]), keep_old_copy.SwapRun(key, ORG, x, y), {"indexingStatus": "COMPLETED"})
    doc = await graph.get_document(key, RECORDS)
    assert doc["virtualRecordId"] == y and "pendingVirtualRecordId" not in doc
    assert await _search(graph, client, name) == [NEW_TEXT]
