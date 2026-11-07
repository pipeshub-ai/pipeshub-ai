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
    _Window,
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


async def _first_lines_title(vs, capture, name: str, extension: str | None, *lines: str) -> set[str]:
    container = BlocksContainer(blocks=[_text_block(i, line) for i, line in enumerate(lines)])
    await _index_record(vs, container, _record(name=name, extension=extension))
    assert capture.dense
    return {text.split("\n", 1)[0] for text in capture.dense}


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_a_file_named_by_ids_is_titled_by_its_opening_line() -> None:
    vs, capture = _capturing_vectorstore()
    titles = await _first_lines_title(
        vs, capture, "exp_4be1c0f2a9d84e7b9c3a5f6d7e8f9012__ops-311.txt", "txt",
        "Humidity Probe Recalibration Plan",
        "The west probe reads two points high after each restart.",
    )
    assert titles == {"Humidity Probe Recalibration Plan"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_a_file_name_without_ids_stays_the_title() -> None:
    vs, capture = _capturing_vectorstore()
    titles = await _first_lines_title(
        vs, capture, "probe-notes.txt", "txt",
        "Humidity Probe Recalibration Plan", "The west probe reads high.",
    )
    assert titles == {"probe-notes"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_an_opening_sentence_is_not_taken_as_the_title() -> None:
    vs, capture = _capturing_vectorstore()
    titles = await _first_lines_title(
        vs, capture, "exp_4be1c0f2a9d84e7b9c3a5f6d7e8f9012__probe-notes.txt", "txt",
        "We moved the probe check to Thursdays.", "Facilities agreed on the call.",
    )
    assert titles == {"probe notes"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_a_chat_export_is_titled_by_its_channel_and_the_words_of_its_name() -> None:
    vs, capture = _capturing_vectorstore()
    titles = await _first_lines_title(
        vs, capture, "thr_5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b__1712345678-misting-valve-schedule", "txt",
        "plant-ops", "dana: the misting valve sticks open after midnight.",
    )
    assert titles == {"plant-ops › misting valve schedule"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_a_chat_export_named_only_by_ids_is_titled_by_its_channel() -> None:
    vs, capture = _capturing_vectorstore()
    titles = await _first_lines_title(
        vs, capture, "7c1e2b3a-4d5f-6a7b-8c9d-0e1f2a3b4c5d_1712345678.txt", "txt",
        "plant-ops", "omar: who resets the timer after a power cut?",
    )
    assert titles == {"plant-ops"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_a_ticket_title_with_an_id_in_it_is_kept() -> None:
    vs, capture = _capturing_vectorstore()
    titles = await _first_lines_title(
        vs, capture, "OPS-42 Replace misting valve", None,
        "Steps to reproduce", "Open the valve twice within a minute.",
    )
    assert titles == {"OPS-42 Replace misting valve"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_a_channel_line_the_name_repeats_is_not_said_twice() -> None:
    vs, capture = _capturing_vectorstore()
    titles = await _first_lines_title(
        vs, capture, "ch_0a1b2c3d4e5f60718293a4b5__plant_ops.txt", "txt",
        "plant-ops", "Rota for April.",
    )
    assert titles == {"plant ops"}


@pytest.mark.asyncio
async def test_switched_off_text_with_headings_embeds_as_before(monkeypatch) -> None:
    monkeypatch.delenv("INDEXING_SECTION_PATHS", raising=False)
    vs, capture = _capturing_vectorstore()
    block = _under(_text_block(0, "Body."), "Pests")

    await _index_record(vs, BlocksContainer(blocks=[block]), _record())

    assert capture.dense == ["Body."]


def test_context_is_left_off_rather_than_overflowing_the_model_limit(monkeypatch) -> None:
    monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", "8")
    document = Document(page_content="a b c d e f g h", metadata={"blockId": "b"})
    _embed_under([document], {"b": "A long title › A long section"})
    assert _embedding_text(document) == "a b c d e f g h"


def test_a_model_window_is_counted_once_for_all_documents() -> None:
    calls: list[list[str]] = []

    def count(texts: list[str]) -> list[int]:
        calls.append(texts)
        return [len(t.split()) for t in texts]

    short = Document(page_content="two words", metadata={"blockId": "s"})
    long = Document(page_content="one two three four five six", metadata={"blockId": "l"})
    _embed_under([short, long], {"s": "Care", "l": "Care"}, _Window(limit=5, count=count))

    assert len(calls) == 1
    assert _embedding_text(short) == "Care\ntwo words"
    assert _embedding_text(long) == "one two three four five six"


@pytest.mark.asyncio
@pytest.mark.usefixtures("section_paths_on")
async def test_one_point_per_block_policies_embed_under_the_line_too(monkeypatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    vs, capture = _capturing_vectorstore()
    rows = _table("Tray row one")
    rows.blocks.append(Block(index=1, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=0,
                             data={"row_natural_language_text": "Second row", "row_number": 2}))
    rows.block_groups[0].children = BlockGroupChildren.from_indices(block_indices=[0, 1])
    _under(rows.blocks[0], "Lighting")
    _under(rows.blocks[1], "Lighting")
    text = _under(_text_block(2, "Lamps switch off at nine."), "Lighting")
    container = BlocksContainer(blocks=[*rows.blocks, text], block_groups=rows.block_groups)

    await _index_record(vs, container, _record())

    assert "Greenhouse Handbook › Lighting\nLamps switch off at nine." in capture.dense
    grouped = next(t for t in capture.dense if "Second row" in t)
    assert grouped.startswith("Greenhouse Handbook › Lighting\n")


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
