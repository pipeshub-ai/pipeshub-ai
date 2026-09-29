"""What the vector store embeds versus what it leaves in the stored blocks."""

from unittest.mock import AsyncMock

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
    ImageMetadata,
    MediaMetadata,
)
from app.modules.transformers.vectorstore import VectorStore
from tests.unit.modules.transformers.test_vectorstore_deep import _make_vectorstore_p1

LINKED_TEXT = (
    "The [Harbour Office](https://example.com/org/Harbour_Office) opened in 1901. "
    "It moved to [Dock Street](https://example.com/places/Dock_Street) later."
)


class _Capture:
    def __init__(self) -> None:
        self.dense: list[str] = []
        self.sparse: list[str] = []
        self.points = []


def _capturing_vectorstore(supports_sparse: bool = True):
    vs = _make_vectorstore_p1(supports_sparse=supports_sparse)
    capture = _Capture()

    async def dense(texts):
        capture.dense.extend(texts)
        return [[0.1] * 4 for _ in texts]

    async def sparse(texts):
        capture.sparse.extend(texts)
        return [None] * len(texts)

    async def upsert(collection_name, points):
        capture.points.extend(points)

    vs.dense_embeddings.aembed_documents = AsyncMock(side_effect=dense)
    vs._compute_sparse_embeddings = AsyncMock(side_effect=sparse)
    vs.vector_db_service.upsert_points = AsyncMock(side_effect=upsert)
    vs.get_embedding_model_instance = AsyncMock(return_value=False)
    vs.delete_embeddings = AsyncMock()
    vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()
    vs._resync_membership_after_write = AsyncMock()
    return vs, capture


def _text_block(index: int, text: str) -> Block:
    return Block(index=index, type=BlockType.TEXT, format=DataFormat.MARKDOWN, data=text)


def _table(row_text: str) -> BlocksContainer:
    row = Block(
        index=0,
        type=BlockType.TABLE_ROW,
        format=DataFormat.JSON,
        parent_index=0,
        data={"row_natural_language_text": row_text, "row_number": 1},
    )
    group = BlockGroup(
        index=0,
        type=GroupType.TABLE,
        data={"table_summary": "Offices of [Acme](https://example.com/Acme)"},
        children=BlockGroupChildren.from_indices(block_indices=[0]),
    )
    return BlocksContainer(blocks=[row], block_groups=[group])


async def _index(vs, container: BlocksContainer) -> None:
    await vs.index_documents(
        block_containers=container,
        org_id="org-1",
        record_id="rec-1",
        virtual_record_id="vr-1",
    )


@pytest.mark.asyncio
async def test_text_block_embeds_anchor_text_but_keeps_links_in_the_block():
    vs, capture = _capturing_vectorstore()
    block = _text_block(0, LINKED_TEXT)

    await _index(vs, BlocksContainer(blocks=[block]))

    assert capture.dense and capture.dense == capture.sparse
    for text in capture.dense:
        assert "https://" not in text and "](" not in text
    assert any("Harbour Office" in text and "Dock Street" in text for text in capture.dense)
    assert block.data == LINKED_TEXT


@pytest.mark.asyncio
async def test_sentence_points_are_link_free(monkeypatch):
    monkeypatch.setenv("EMBED_SENTENCE_MIN_WORDS", "0")
    vs, capture = _capturing_vectorstore()

    await _index(vs, BlocksContainer(blocks=[_text_block(0, LINKED_TEXT)]))

    sentence_points = [p for p in capture.points if p.payload["metadata"].get("isBlock") is False]
    assert len(sentence_points) == 2
    for point in capture.points:
        assert "https://" not in point.payload["page_content"]


@pytest.mark.asyncio
async def test_table_row_and_summary_embed_without_urls():
    vs, capture = _capturing_vectorstore()
    container = _table("Office: [Harbour Office](https://example.com/Harbour_Office), Opened: 1901")

    await _index(vs, container)

    assert "Office: Harbour Office, Opened: 1901" in capture.dense
    assert "Offices of Acme" in capture.dense
    assert all("https://" not in text for text in capture.dense + capture.sparse)
    assert "https://" in container.blocks[0].data["row_natural_language_text"]


@pytest.mark.asyncio
async def test_block_that_is_only_an_empty_image_link_embeds_nothing():
    vs, capture = _capturing_vectorstore()

    await _index(vs, BlocksContainer(blocks=[_text_block(0, "![](https://example.com/spacer.gif)")]))

    assert capture.dense == []


def test_image_alt_text_is_part_of_the_image_description():
    block = Block(
        index=0,
        type=BlockType.IMAGE,
        data={"uri": "data:image/png;base64,AAAA"},
        image_metadata=ImageMetadata(captions=["Revenue by quarter"]),
        media_metadata=MediaMetadata(alt_text="Bar chart"),
    )
    assert VectorStore._image_block_description(block) == "Revenue by quarter Bar chart"
