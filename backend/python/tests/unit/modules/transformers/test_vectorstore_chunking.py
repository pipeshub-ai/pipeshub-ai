"""Text blocks embedded as retrieval chunks, and chunk points kept current on update."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockSubType,
    BlockType,
    DataFormat,
)
from app.modules.transformers.vectorstore import (
    VectorStore,
    _build_chunked_text_documents,
    _pack_windows,
    _process_text_blocks,
    deterministic_point_id,
)
from app.services.vector_db.models import ScrollResult, VectorPoint
from tests.unit.modules.transformers.test_vectorstore_embedding_text import (
    _capturing_vectorstore,
    _record,
    _text_block,
    _under,
)


def _words(n: int, word: str = "word") -> str:
    return " ".join(f"{word}{i}." if i % 12 == 11 else f"{word}{i}" for i in range(n))


def _heading(index: int, text: str, level: int) -> Block:
    return Block(index=index, type=BlockType.TEXT, sub_type=BlockSubType.HEADING,
                 format=DataFormat.MARKDOWN, data=text, name=f"H{level}")


def _spans(points: list) -> list[list[int]]:
    return [
        p.payload["metadata"].get("spanBlockIndices", [p.payload["metadata"]["blockIndex"]])
        for p in points
    ]


async def _index(vs: VectorStore, blocks: list[Block], **kwargs: object) -> None:
    await vs.index_documents(
        block_containers=BlocksContainer(blocks=blocks),
        org_id="org-1", record_id="rec-1", virtual_record_id="vr-1", record=_record(), **kwargs,
    )


@pytest.fixture(autouse=True)
def _chunks_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEXING_TEXT_CHUNKS", "true")
    monkeypatch.setenv("EMBED_CHUNK_TARGET_WORDS", "20")
    monkeypatch.setenv("EMBED_CHUNK_MAX_WORDS", "40")


@pytest.mark.asyncio
async def test_short_blocks_of_a_section_embed_as_one_chunk_that_names_every_block() -> None:
    vs, capture = _capturing_vectorstore()
    blocks = [_text_block(0, "Overview"), _text_block(1, _words(8)), _text_block(2, _words(15))]

    await _index(vs, blocks)

    (point,) = capture.points
    meta = point.payload["metadata"]
    assert (meta["blockId"], meta["blockIndex"], meta["isBlock"]) == (blocks[0].id, 0, True)
    assert meta["spanBlockIndices"] == [0, 1, 2]
    assert meta["spanBlockIds"] == [b.id for b in blocks]
    assert point.payload["page_content"] == "\n".join(b.data for b in blocks)
    assert capture.dense == [point.payload["page_content"]]


@pytest.mark.asyncio
async def test_a_chunk_closes_at_the_target_and_never_crosses_a_section_or_a_non_text_block() -> None:
    vs, capture = _capturing_vectorstore()
    blocks = [
        _under(_text_block(0, _words(12)), "Intake"),
        _under(_text_block(1, _words(12)), "Intake"),  # reaches the target of 20
        _under(_text_block(2, _words(5)), "Intake"),
        _under(_text_block(3, _words(5)), "Storage"),  # another section
        _under(_text_block(5, _words(5)), "Storage"),  # block 4 is not text
    ]

    await _index(vs, blocks)

    assert _spans(capture.points) == [[0, 1], [2], [3], [5]]


@pytest.mark.asyncio
async def test_a_heading_starts_a_chunk_that_holds_its_section() -> None:
    vs, capture = _capturing_vectorstore()
    blocks = [
        _text_block(0, _words(5)),
        _heading(1, "Watering", 2),
        _under(_text_block(2, _words(4)), "Watering"),
        _under(_text_block(3, _words(4)), "Watering"),
        _heading(4, "Feeding", 2),
        _under(_text_block(5, _words(4)), "Feeding"),
    ]

    await _index(vs, blocks)

    assert _spans(capture.points) == [[0], [1, 2, 3], [4, 5]]


@pytest.mark.asyncio
async def test_a_long_block_embeds_whole_and_in_chunk_sized_windows() -> None:
    vs, capture = _capturing_vectorstore()
    thread = "\n".join(f"member{i}: {_words(9, f'note{i}w')}" for i in range(60))

    await _index(vs, [_text_block(0, _words(5)), _text_block(1, thread), _text_block(2, _words(5))])

    long_points = [p.payload for p in capture.points if p.payload["metadata"]["blockIndex"] == 1]
    assert [p["metadata"]["isBlock"] for p in long_points].count(True) == 1
    windows = [p["page_content"] for p in long_points if p["metadata"]["isBlock"] is False]
    assert len(windows) > 20
    assert all(len(w.split()) <= 40 for w in windows)
    assert any("member37:" in w for w in windows)


def test_windows_pack_lines_to_the_target_and_repeat_the_last_line() -> None:
    lines = [f"m{i}: one two three four" for i in range(6)]
    windows = _pack_windows("\n".join(lines), "en", 10)
    assert windows[0] == "\n".join(lines[:2])
    assert windows[1].split("\n")[0] == lines[1]
    assert lines[-1] in windows[-1]


def test_a_line_longer_than_the_target_is_split_into_sentences() -> None:
    line = " ".join(["Alpha beta gamma delta epsilon."] * 6)
    windows = _pack_windows(line, "en", 10)
    assert len(windows) >= 3
    assert all(len(w.split()) <= 15 for w in windows)


def test_without_chunks_every_block_keeps_its_own_point() -> None:
    blocks = [_text_block(0, "Overview"), _text_block(1, _words(8))]
    docs = _process_text_blocks(blocks, "vr-1", "org-1")
    assert [d.metadata["blockIndex"] for d in docs] == [0, 1]
    assert all("spanBlockIndices" not in d.metadata for d in docs)


@pytest.mark.asyncio
async def test_switched_off_text_embeds_block_by_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDEXING_TEXT_CHUNKS")
    vs, capture = _capturing_vectorstore()

    await _index(vs, [_text_block(0, "Overview"), _text_block(1, _words(8))])

    assert _spans(capture.points) == [[0], [1]]


@pytest.mark.asyncio
async def test_reconciliation_chunks_all_of_the_records_text_again() -> None:
    vs = VectorStore.__new__(VectorStore)
    vs.logger = MagicMock()
    vs.index_documents = AsyncMock(return_value=True)
    changed, unchanged = _text_block(0, "changed"), _text_block(1, "unchanged")
    row = Block(index=2, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
                data={"row_natural_language_text": "r"})
    record = SimpleNamespace(
        id="rec-1", virtual_record_id="vr-1", org_id="org-1",
        block_containers=BlocksContainer(blocks=[changed, unchanged, row]),
    )
    ctx = SimpleNamespace(
        record=record,
        reconciliation_context=SimpleNamespace(blocks_to_index_ids={changed.id}, block_ids_to_delete=set()),
    )

    await vs.apply(ctx)

    container = vs.index_documents.await_args.args[0]
    assert [b.id for b in container.blocks] == [changed.id, unchanged.id]
    assert vs.index_documents.await_args.kwargs["rechunk_text"] is True


def _stored(vs: VectorStore, by_block: dict[str, set[str]]) -> None:
    vs.vector_db_service.scroll = AsyncMock(return_value=ScrollResult(points=[
        VectorPoint(id=point_id, payload={"metadata": {"blockId": block_id, "blockType": "text"}})
        for block_id, ids in by_block.items() for point_id in ids
    ]))


def _events(vs: VectorStore) -> list[str]:
    events: list[str] = []
    embed = vs.dense_embeddings.aembed_documents.side_effect
    upsert = vs.vector_db_service.upsert_points.side_effect

    async def dense(texts: list[str]) -> list[list[float]]:
        events.append("embed")
        return await embed(texts)

    async def write(collection_name: str, points: list) -> None:
        events.append("upsert")
        await upsert(collection_name, points)

    async def delete(block_ids: set, virtual_record_id: str, collection_name: str) -> None:
        if block_ids:
            events.append("delete " + ",".join(sorted(block_ids)))

    vs.dense_embeddings.aembed_documents = AsyncMock(side_effect=dense)
    vs.vector_db_service.upsert_points = AsyncMock(side_effect=write)
    vs.delete_blocks_by_ids = AsyncMock(side_effect=delete)
    return events


def _chunk(blocks: list[Block]) -> list:
    return _build_chunked_text_documents(blocks, "vr-1", "org-1", "en")


def _ids(documents: list) -> set[str]:
    return {deterministic_point_id(d.metadata, d.page_content) for d in documents}


@pytest.mark.asyncio
async def test_an_unchanged_chunk_is_not_embedded_again() -> None:
    vs, _ = _capturing_vectorstore()
    blocks = [Block(id="a", index=0, type=BlockType.TEXT, data="Vents open at noon.")]
    documents = _chunk(blocks)
    _stored(vs, {"a": _ids(documents)})
    events = _events(vs)

    await vs._replace_text_points(documents, {"a"}, "vr-1", "rec-1", "records")

    assert events == []


@pytest.mark.asyncio
async def test_a_changed_chunk_under_the_same_first_block_is_embedded_before_its_old_points_go() -> None:
    vs, capture = _capturing_vectorstore()
    blocks = [Block(id="a", index=0, type=BlockType.TEXT, data="Vents open at one."),
              Block(id="b", index=1, type=BlockType.TEXT, data="Fans follow them.")]
    _stored(vs, {"a": {"old-a"}, "b": {"old-b"}})
    events = _events(vs)

    await vs._replace_text_points(_chunk(blocks), {"a", "b"}, "vr-1", "rec-1", "records")

    assert events == ["embed", "delete a", "upsert", "delete b"]
    assert capture.points[0].payload["metadata"]["spanBlockIds"] == ["a", "b"]


@pytest.mark.asyncio
async def test_a_chunk_under_a_new_block_is_written_before_anything_is_deleted() -> None:
    vs, _ = _capturing_vectorstore()
    blocks = [Block(id="n", index=0, type=BlockType.TEXT, data="Vents open at two.")]
    _stored(vs, {"gone": {"old"}})
    events = _events(vs)

    await vs._replace_text_points(_chunk(blocks), {"n"}, "vr-1", "rec-1", "records")

    assert events == ["embed", "upsert", "delete gone"]


@pytest.mark.asyncio
async def test_a_failed_embedding_deletes_nothing() -> None:
    vs, _ = _capturing_vectorstore()
    blocks = [Block(id="a", index=0, type=BlockType.TEXT, data="Vents open at three.")]
    _stored(vs, {"a": {"old-a"}})
    _events(vs)
    vs._embed_documents_with_retry = AsyncMock(side_effect=RuntimeError("provider down"))

    with pytest.raises(RuntimeError):
        await vs._replace_text_points(_chunk(blocks), {"a"}, "vr-1", "rec-1", "records")

    vs.delete_blocks_by_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreadable_stored_points_replace_every_chunk() -> None:
    vs, _ = _capturing_vectorstore()
    blocks = [Block(id="a", index=0, type=BlockType.TEXT, data="Vents open."),
              Block(id="b", index=1, type=BlockType.TEXT, data="Fans follow.")]
    vs.vector_db_service.scroll = AsyncMock(side_effect=RuntimeError("scroll window exceeded"))
    events = _events(vs)

    await vs._replace_text_points(_chunk(blocks), {"a", "b"}, "vr-1", "rec-1", "records")

    assert events == ["embed", "delete a", "upsert", "delete b"]


@pytest.mark.asyncio
async def test_stored_points_are_read_by_record_alone() -> None:
    vs, _ = _capturing_vectorstore()
    _stored(vs, {})

    await vs._stored_text_points("vr-1", "records")

    vs.vector_db_service.filter_collection.assert_awaited_once_with(must={"virtualRecordId": "vr-1"})
    assert "metadata.blockType" in vs.vector_db_service.scroll.await_args.kwargs["with_payload"]


@pytest.mark.asyncio
async def test_reconciliation_does_not_delete_the_records_text_before_embedding_it() -> None:
    vs, _ = _capturing_vectorstore()
    blocks = [Block(id="a", index=0, type=BlockType.TEXT, data="Vents open at four."),
              Block(id="b", index=1, type=BlockType.TEXT, data="Fans follow them.")]
    _stored(vs, {"a": {"old-a"}, "b": {"old-b"}})
    events = _events(vs)

    await _index(vs, blocks, is_reconciliation=True, rechunk_text=True, block_ids_to_delete={"gone"})

    assert events == ["delete vr-1_summary", "embed", "delete a", "upsert", "delete b", "delete gone"]
