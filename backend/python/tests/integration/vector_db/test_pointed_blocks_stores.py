"""Which blocks of a record have a point (D-179, QA-S7e-26 and -28) on real stores, with a stub multimodal provider (it embeds the image, or skips it).

A legacy multimodal record (no recorded set): (a) its image point present -> moved, 0 embeds; (b) lost -> the record re-embeds and the
image is back in search; (c) the provider skips the image -> one re-embed, the recorded set is written without the image, a second run
embeds nothing, and a verification against the recorded set is exact. (d) stale-low: a point lost after a record was recorded: the next
ordinary run keeps it recorded and writes it again (the set is what the writer wrote, not what the store lists).
QA_STORES=memory,qdrant,redis,opensearch."""
from __future__ import annotations

import contextlib
import hashlib
import os
import random
import uuid
from unittest.mock import AsyncMock

import pytest

from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockType,
    DataFormat,
    ImageMetadata,
)
from app.modules.reconciliation.service import ReconciliationService
from app.services.embeddings.multimodal.interface import ImageEmbeddingResult
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM, make_collection_config
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import ORG, RID, VRID, Blob, make_store, record_of, run

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(600)]
STORES = [s for s in os.getenv("QA_STORES", "memory,qdrant,redis,opensearch").split(",") if s]


def _vec(t: str) -> list[float]:
    r = random.Random(hashlib.sha256(t.encode()).digest())
    return [r.uniform(-1, 1) for _ in range(DIM)]


@pytest.fixture(params=STORES)
def store_svc(request: pytest.FixtureRequest) -> object:
    if request.param == "memory":
        return InMemoryVectorDBService()
    return request.getfixturevalue({"qdrant": "qdrant_service", "redis": "redis_service", "opensearch": "opensearch_service"}[request.param])


async def make_col(svc: object) -> str:
    if isinstance(svc, InMemoryVectorDBService):
        from app.services.vector_db.models import CollectionConfig

        await svc.create_collection("records", CollectionConfig(embedding_size=DIM))
        return "records"
    col = make_collection("pointed")
    await svc.create_collection(col, make_collection_config())
    for f in ("metadata.blockId", "metadata.virtualRecordId", "metadata.orgId"):
        with contextlib.suppress(Exception):
            await svc.create_index(col, f, {"type": "keyword"})
    return col


async def drop_col(svc: object, col: str) -> None:
    if not isinstance(svc, InMemoryVectorDBService):
        await svc.delete_collection(col)


def container(described: bool = False) -> BlocksContainer:
    bl = [Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=f"Paragraph {i} about revenue and costs.") for i in range(4)]
    bl.append(Block(index=4, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": "data:image/png;base64,iVBORw0KGgo="},
                    image_metadata=ImageMetadata(description="A chart of revenue") if described else None))
    return BlocksContainer(blocks=bl)


def store_of(svc: object, col: str, image: str) -> object:
    """image: 'embed' (the provider returns a vector) or 'skip' (it returns none)."""
    s = make_store(svc, dim=DIM, collection=col)
    s.embedding_size = DIM
    s._embed_documents_with_retry = AsyncMock(side_effect=lambda ts, _r, witness_out=None: [_vec(t) for t in ts])
    s.get_embedding_model_instance = AsyncMock(return_value=True)
    s.image_calls = 0

    async def images(chunks: list, b64s: list, record_id: str = "") -> list:
        s.image_calls += len(chunks)
        return s._build_image_points(chunks, [ImageEmbeddingResult(index=i, embedding=_vec("img") if image == "embed" else None,
                                                                   error=None if image == "embed" else "skipped")
                                              for i in range(len(chunks))])
    s._process_image_embeddings = images
    return s


async def points(svc, col) -> list:
    await svc.make_searchable(col)
    flt = await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
    out, off = [], None
    while True:
        page = await svc.scroll(col, flt, 200, off)
        out += page.points
        if not page.next_offset:
            return out
        off = page.next_offset


def is_image(p) -> bool:
    m = p.payload.get("metadata") or {}
    return bool(m.get("isImage")) or str(m.get("blockType", "")).lower() == "image"


async def legacy(svc, col, image: str, c: BlocksContainer) -> Blob:
    s = store_of(svc, col, image)
    await s.index_documents(c, ORG, RID, VRID, record=record_of(c))
    blob = Blob()
    blob.saved = ReconciliationService().build_metadata(c).to_dict()
    assert "pointedBlocks" not in blob.saved and "idScheme" not in blob.saved
    return blob


async def drop_image_points(svc, col) -> int:
    imgs = [p for p in await points(svc, col) if is_image(p)]
    for p in imgs:
        await svc.delete_points(col, await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID,
                                                                         "blockId": [p.payload["metadata"]["blockId"]]}))
    return len(imgs)


def embeds(s) -> int:
    return sum(len(c.args[0]) for c in s._embed_documents_with_retry.await_args_list) + s.image_calls


async def test_a_legacy_multimodal_image_point_present_moves(store_svc) -> None:
    svc, col = store_svc, await make_col(store_svc)
    try:
        blob = await legacy(svc, col, "embed", container())
        s = store_of(svc, col, "embed")
        await run(s, blob, container())
        assert embeds(s) == 0 and any(is_image(p) for p in await points(svc, col))
        assert "pointedBlocks" in blob.saved and len(blob.saved["pointedBlocks"]) == 5
    finally:
        await drop_col(svc, col)


@pytest.mark.parametrize("described", [False, True])
async def test_a_legacy_multimodal_images_lost_point_reembeds_and_returns_to_search(store_svc, described) -> None:
    svc, col = store_svc, await make_col(store_svc)
    try:
        blob = await legacy(svc, col, "embed", container(described))
        assert await drop_image_points(svc, col) == 1
        s = store_of(svc, col, "embed")
        await run(s, blob, container(described))
        imgs = [p for p in await points(svc, col) if is_image(p)]
        assert imgs, "the image is still out of search"
    finally:
        await drop_col(svc, col)


async def test_a_provider_skipped_image_reembeds_once_then_is_recorded_exactly(store_svc) -> None:
    svc, col = store_svc, await make_col(store_svc)
    try:
        blob = await legacy(svc, col, "skip", container())
        s1 = store_of(svc, col, "skip")
        await run(s1, blob, container())
        first = embeds(s1)
        recorded = set(blob.saved.get("pointedBlocks") or [])
        s2 = store_of(svc, col, "skip")
        await run(s2, blob, container())
        second = embeds(s2)
        # a verification against the recorded set (a later scheme change): the image is not expected, nothing else is missing
        c = container()
        from app.modules.reconciliation.block_identity import BlockIdentity

        BlockIdentity.assign(c, ORG, VRID)
        newer = c.model_copy(deep=True)
        for b in newer.blocks:
            b.id = str(uuid.uuid4())
        rekey = {o.id: n.id for o, n in zip(c.blocks, newer.blocks, strict=True)}
        s3 = store_of(svc, col, "skip")
        to_index, _ = await s3._move_points_to_new_ids(record_of(newer), rekey, set(), set(), recorded=recorded)
        assert first > 0 and second == 0 and len(recorded) == 4 and not to_index
    finally:
        await drop_col(svc, col)


async def test_a_point_lost_after_recording_stays_recorded_and_is_written_again(store_svc) -> None:
    """Stale-low: the record is indexed and recorded (5 blocks); a paragraph's point is lost (an interrupted delete, QA-S7e-20-style);
    an ordinary no-change run lists the record and records the set again. A later verification must still expect the paragraph."""
    svc, col = store_svc, await make_col(store_svc)
    try:
        blob = await legacy(svc, col, "embed", container())
        await run(store_of(svc, col, "embed"), blob, container())
        before = set(blob.saved.get("pointedBlocks") or [])
        lost = next(p for p in await points(svc, col) if "Paragraph 2" in str(p.payload.get("page_content")))
        lost_block = lost.payload["metadata"]["blockId"]
        await svc.delete_points(col, await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID, "blockId": [lost_block]}))
        s = store_of(svc, col, "embed")
        await run(s, blob, container())                    # an ordinary run: nothing changed
        after = set(blob.saved.get("pointedBlocks") or [])
        assert lost_block in after and len(after) == len(before) == 5, "the lost block left the recorded set"
        assert embeds(s) == 1 and lost_block in {p.payload["metadata"]["blockId"] for p in await points(svc, col)}, "its point was not written again"
    finally:
        await drop_col(svc, col)
