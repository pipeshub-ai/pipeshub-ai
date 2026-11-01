"""What the vector store embeds versus what it leaves in the stored blocks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupType,
    ImageMetadata,
    MediaMetadata,
)
from app.modules.transformers.vectorstore import (
    VectorStore,
    _embed_under,
    _embedding_text,
)
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


def _capturing_vectorstore(supports_sparse: bool = True) -> tuple[VectorStore, _Capture]:
    vs = _make_vectorstore_p1(supports_sparse=supports_sparse)
    capture = _Capture()

    async def dense(texts: list[str]) -> list[list[float]]:
        capture.dense.extend(texts)
        return [[0.1] * 4 for _ in texts]

    async def sparse(texts: list[str]) -> list[None]:
        capture.sparse.extend(texts)
        return [None] * len(texts)

    async def upsert(collection_name, points) -> None:
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
async def test_text_block_embeds_anchor_text_but_keeps_links_in_the_block() -> None:
    vs, capture = _capturing_vectorstore()
    block = _text_block(0, LINKED_TEXT)

    await _index(vs, BlocksContainer(blocks=[block]))

    assert capture.dense and capture.dense == capture.sparse
    for text in capture.dense:
        assert "https://" not in text and "](" not in text
    assert any("Harbour Office" in text and "Dock Street" in text for text in capture.dense)
    assert block.data == LINKED_TEXT


@pytest.mark.asyncio
async def test_sentence_points_are_link_free(monkeypatch) -> None:
    monkeypatch.setenv("EMBED_SENTENCE_MIN_WORDS", "0")
    vs, capture = _capturing_vectorstore()

    await _index(vs, BlocksContainer(blocks=[_text_block(0, LINKED_TEXT)]))

    sentence_points = [p for p in capture.points if p.payload["metadata"].get("isBlock") is False]
    assert len(sentence_points) == 2
    for point in capture.points:
        assert "https://" not in point.payload["page_content"]


@pytest.mark.asyncio
async def test_table_row_and_summary_embed_without_urls() -> None:
    vs, capture = _capturing_vectorstore()
    container = _table("Office: [Harbour Office](https://example.com/Harbour_Office), Opened: 1901")

    await _index(vs, container)

    assert "Office: Harbour Office, Opened: 1901" in capture.dense
    assert "Offices of Acme" in capture.dense
    assert all("https://" not in text for text in capture.dense + capture.sparse)
    assert "https://" in container.blocks[0].data["row_natural_language_text"]


@pytest.mark.asyncio
async def test_block_that_is_only_an_empty_image_link_embeds_nothing() -> None:
    vs, capture = _capturing_vectorstore()

    await _index(vs, BlocksContainer(blocks=[_text_block(0, "![](https://example.com/spacer.gif)")]))

    assert capture.dense == []


def _record(name: str = "Greenhouse Handbook.html", extension: str | None = "html") -> SimpleNamespace:
    return SimpleNamespace(
        record_name=name, extension=extension, connector_id=None, connector_name=None,
        semantic_metadata=None,
    )


def _under(block: Block, *headings: str) -> Block:
    block.citation_metadata = CitationMetadata(heading_path=list(headings))
    return block


async def _index_record(vs, container: BlocksContainer, record) -> None:
    await vs.index_documents(
        block_containers=container,
        org_id="org-1",
        record_id="rec-1",
        virtual_record_id="vr-1",
        record=record,
    )


@pytest.fixture
def section_paths_on(monkeypatch) -> None:
    monkeypatch.setenv("INDEXING_SECTION_PATHS", "true")


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_text_embeds_under_title_and_headings_but_stores_its_own_text() -> None:
    vs, capture = _capturing_vectorstore()
    block = _under(_text_block(0, "Seedlings are watered at dawn."), "Daily care", "Watering")

    await _index_record(vs, BlocksContainer(blocks=[block]), _record())

    assert capture.dense == ["Greenhouse Handbook › Daily care › Watering\nSeedlings are watered at dawn."]
    assert capture.sparse == capture.dense
    (point,) = capture.points
    assert point.payload["page_content"] == "Seedlings are watered at dawn."
    assert "_embedContext" not in point.payload["metadata"]
    assert block.data == "Seedlings are watered at dawn."


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_table_row_embeds_under_its_headings() -> None:
    vs, capture = _capturing_vectorstore()
    container = _table("Rack: North, Lamps: 6")
    _under(container.blocks[0], "Lighting")

    await _index_record(vs, container, _record())

    assert "Greenhouse Handbook › Lighting\nRack: North, Lamps: 6" in capture.dense
    row_point = next(p for p in capture.points if p.payload["metadata"]["blockType"] == "table_row")
    assert row_point.payload["page_content"] == "Rack: North, Lamps: 6"


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_a_heading_repeating_the_title_is_not_embedded_twice() -> None:
    vs, capture = _capturing_vectorstore()
    block = _under(_text_block(0, "Body."), "Greenhouse Handbook", "Pests")

    await _index_record(vs, BlocksContainer(blocks=[block]), _record())

    assert capture.dense == ["Greenhouse Handbook › Pests\nBody."]


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_the_context_line_changes_the_point_id() -> None:
    vs, capture = _capturing_vectorstore()
    await _index_record(vs, BlocksContainer(blocks=[_under(_text_block(0, "Body."), "Pests")]), _record())
    await _index_record(vs, BlocksContainer(blocks=[_under(_text_block(0, "Body."), "Soil")]), _record())

    first, second = capture.points
    assert first.id != second.id


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_the_documents_own_title_names_the_record() -> None:
    vs, capture = _capturing_vectorstore()
    container = BlocksContainer(
        blocks=[_under(_text_block(0, "Body."), "Pests")], document_title="Glasshouse Rules",
    )

    await _index_record(vs, container, _record(name="upload_3c9d.html"))

    assert capture.dense == ["Glasshouse Rules › Pests\nBody."]


@pytest.mark.asyncio
async def test_switched_off_text_with_headings_embeds_as_before(monkeypatch) -> None:
    monkeypatch.delenv("INDEXING_SECTION_PATHS", raising=False)
    vs, capture = _capturing_vectorstore()
    block = _under(_text_block(0, "Body."), "Pests")

    await _index_record(vs, BlocksContainer(blocks=[block]), _record())

    assert capture.dense == ["Body."]


def test_context_is_left_off_rather_than_overflowing_the_model_limit(monkeypatch) -> None:
    monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", "8")
    document = Document(page_content="a b c d e f g h", metadata={})
    _embed_under("A long title › A long section", [document])
    assert _embedding_text(document) == "a b c d e f g h"


def test_image_alt_text_is_part_of_the_image_description() -> None:
    block = Block(
        index=0,
        type=BlockType.IMAGE,
        data={"uri": "data:image/png;base64,AAAA"},
        image_metadata=ImageMetadata(captions=["Revenue by quarter"]),
        media_metadata=MediaMetadata(alt_text="Bar chart"),
    )
    assert VectorStore._image_block_description(block) == "Revenue by quarter Bar chart"


def test_image_alt_text_is_added_to_a_generated_description() -> None:
    block = Block(
        index=0,
        type=BlockType.IMAGE,
        data={"uri": "data:image/png;base64,AAAA"},
        image_metadata=ImageMetadata(description="A bar chart with four bars."),
        media_metadata=MediaMetadata(alt_text="Revenue FY26"),
    )
    assert VectorStore._image_block_description(block) == "A bar chart with four bars. Revenue FY26"


def test_alt_text_already_in_the_generated_description_is_not_repeated() -> None:
    block = Block(
        index=0,
        type=BlockType.IMAGE,
        data={"uri": "data:image/png;base64,AAAA"},
        image_metadata=ImageMetadata(description="Revenue FY26 by quarter"),
        media_metadata=MediaMetadata(alt_text="revenue fy26"),
    )
    assert VectorStore._image_block_description(block) == "Revenue FY26 by quarter"
