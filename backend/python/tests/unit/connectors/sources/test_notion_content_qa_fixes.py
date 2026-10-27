"""Notion content and streaming fixes from QA: nested lists, comment attachments of one name, attachment and
file names and types, link titles, table markdown, trashed file blocks, and the parser gaps."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.models.blocks import (
    BlockGroup,
    BlockGroupChildren,
    BlockSubType,
    BlockType,
    GroupSubType,
    GroupType,
)
from tests.unit.connectors.sources.test_notion_streaming_security import (
    _connector,
    _file_record,
    _resp,
)

PAGE = "https://www.notion.so/page-1"


def _rt(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}, "plain_text": text, "annotations": {}}]


def _item(block_id: str, text: str, kind: str = "numbered_list_item") -> dict:
    return {"id": block_id, "type": kind, kind: {"rich_text": _rt(text)}, "has_children": False}


async def _parse(parser: NotionBlockParser, notion_block: dict, parent: int | None) -> "object":
    block, _, _ = await parser.parse_block(notion_block, parent, 0, PAGE, "page-1")
    return block


def _wrapper(groups: list[BlockGroup], owner_id: str, parent: int | None) -> int:
    groups.append(BlockGroup(index=len(groups), parent_index=parent, type=GroupType.TEXT_SECTION,
                             sub_type=GroupSubType.NESTED_BLOCK, source_group_id=owner_id,
                             children=BlockGroupChildren()))
    return len(groups) - 1


def _parents(blocks: list, groups: list[BlockGroup]) -> dict[int, list[int]]:
    """block index -> the groups listing it as a child."""
    out: dict[int, list[int]] = {i: [] for i in range(len(blocks))}
    for g in groups:
        for r in (g.children.block_ranges if g.children else []):
            for i in range(r.start, r.end + 1):
                out[i].append(g.index)
    return out


@pytest.mark.asyncio
async def test_nested_numbered_list_numbering_indent_and_single_parents() -> None:
    """N1, N2 (with child N2.1), N3 — as the connector builds it: N2 and N2.1 sit in N2's wrapper."""
    parser = NotionBlockParser(logger=MagicMock())
    blocks, groups = [], []
    blocks.append(await _parse(parser, _item("n1", "N1"), None))
    w = _wrapper(groups, "n2", None)
    blocks.append(await _parse(parser, _item("n2", "N2"), w))
    blocks.append(await _parse(parser, _item("n21", "N2.1"), w))
    groups[w].children.add_block_index(1)
    groups[w].children.add_block_index(2)
    blocks.append(await _parse(parser, _item("n3", "N3"), None))
    for i, b in enumerate(blocks):
        b.index = i

    parser.post_process_blocks(blocks, groups)

    assert [b.data for b in blocks] == ["1. N1", "2. N2", "1. N2.1", "3. N3"]
    assert [b.list_metadata.indent_level for b in blocks] == [0, 0, 1, 0]
    lists = [g for g in groups if g.type == GroupType.ORDERED_LIST]
    outer = next(g for g in lists if g.parent_index is None)
    inner = next(g for g in lists if g is not outer)
    assert inner.parent_index == w and groups[w].parent_index == outer.index
    # Every block is listed by exactly one group (or by none, at the root of a list group's members).
    assert all(len(p) <= 1 for p in _parents(blocks, groups).values())
    assert blocks[2].parent_index == inner.index


@pytest.mark.asyncio
async def test_bullets_in_two_columns_are_two_lists() -> None:
    parser = NotionBlockParser(logger=MagicMock())
    groups = [BlockGroup(index=0, type=GroupType.COLUMN, children=BlockGroupChildren()),
              BlockGroup(index=1, type=GroupType.COLUMN, children=BlockGroupChildren())]
    blocks = [await _parse(parser, _item("a", "colA", "bulleted_list_item"), 0),
              await _parse(parser, _item("b", "colB", "bulleted_list_item"), 1)]
    for i, b in enumerate(blocks):
        b.index = i
        groups[b.parent_index].children.add_block_index(i)
    parser.post_process_blocks(blocks, groups)
    lists = [g for g in groups if g.type == GroupType.LIST]
    assert len(lists) == 2 and {g.parent_index for g in lists} == {0, 1}


@pytest.mark.asyncio
async def test_same_named_comment_attachments_download_their_own_file() -> None:
    conn = _connector()
    comment = {"attachments": [
        {"category": "productivity", "file": {"url": "https://files.example/ws/f1/dup.txt?sig=1"}},
        {"category": "productivity", "file": {"url": "https://files.example/ws/f2/dup.txt?sig=2"}},
    ]}
    conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(
        retrieve_comment=AsyncMock(return_value=_resp(data=comment))))
    ids = [i for i, _ in conn._comment_attachment_ids("c1", comment["attachments"])]
    assert ids == ["ca_c1_dup.txt", "ca_c1_dup.txt~2"]
    second = await conn._get_comment_attachment_url(_file_record("ca_c1_dup.txt~2", "dup.txt"))
    assert second.startswith("https://files.example/ws/f2/")
    first = await conn._get_comment_attachment_url(_file_record("ca_c1_dup.txt", "dup.txt"))
    assert first.startswith("https://files.example/ws/f1/")


@pytest.mark.asyncio
async def test_comment_attachment_type_comes_from_the_file() -> None:
    conn = _connector()
    txt = await conn._transform_to_comment_file_record(
        {"category": "productivity", "file": {"url": "https://files.example/ws/f/notes.txt"}}, "c1", "p1")
    jpg = await conn._transform_to_comment_file_record(
        {"category": "image", "file": {"url": "https://files.example/ws/f/shot.jpg"}}, "c1", "p1")
    bare = await conn._transform_to_comment_file_record(
        {"category": "image", "file": {"url": "https://files.example/ws/f/shot"}}, "c1", "p1")
    assert (txt.mime_type, jpg.mime_type, bare.mime_type) == ("text/plain", "image/jpeg", "image/png")


@pytest.mark.asyncio
async def test_media_blocks_are_named_after_their_file() -> None:
    parser = NotionBlockParser(logger=MagicMock())
    block = await _parse(parser, {"id": "v1", "type": "video", "video": {
        "type": "file", "file": {"url": "https://files.example/ws/f/demo%20reel.mp4?sig=1"}, "caption": []}}, None)
    assert "demo reel.mp4" in str(block.data)


@pytest.mark.asyncio
async def test_trashed_file_block_is_not_downloadable() -> None:
    conn = _connector()
    conn._get_fresh_datasource = AsyncMock(return_value=MagicMock(retrieve_block=AsyncMock(return_value=_resp(data={
        "id": "b1", "type": "pdf", "in_trash": True, "pdf": {"type": "file", "file": {"url": "https://files.example/x.pdf"}},
    }))))
    assert await conn._get_block_file_source(_file_record("b1")) == (None, True)


@pytest.mark.asyncio
async def test_link_to_page_shows_the_resolved_title() -> None:
    from app.models.blocks import ChildRecord, ChildType

    conn = _connector()
    parser = NotionBlockParser(logger=MagicMock())
    block = await _parse(parser, {"id": "l1", "type": "link_to_page", "link_to_page": {
        "type": "page_id", "page_id": "0123456789abcdef"}}, None)
    assert block.data.startswith("Page ")
    conn._batch_get_or_create_child_records = AsyncMock(return_value={
        "0123456789abcdef": ChildRecord(child_type=ChildType.RECORD, child_id="r1", child_name="Launch plan")})
    await conn._resolve_child_reference_blocks([block])
    assert block.data == block.source_name == "Launch plan"


@pytest.mark.asyncio
async def test_table_markdown_shows_names_like_the_cells() -> None:
    from app.models.blocks import ChildRecord, ChildType

    parser = NotionBlockParser(logger=MagicMock())
    meta = {"properties": {"Name": {"type": "title"}, "Owner": {"type": "people"}}}
    rows = [{"id": "row1", "properties": {
        "Name": {"type": "title", "title": _rt("Task A")},
        "Owner": {"type": "people", "people": [{"object": "user", "id": "u-123"}]}}}]

    async def user(uid: str) -> "ChildRecord":
        return ChildRecord(child_type=ChildType.USER, child_id="pu", child_name="Ada Lovelace")

    _, groups = await parser.parse_data_source_to_blocks(meta, rows, "ds1", get_user_child_callback=user)
    markdown = groups[0].data["table_markdown"]
    assert "Ada Lovelace" in markdown and "u-123" not in markdown


@pytest.mark.asyncio
async def test_parser_gaps_heading_4_meeting_notes_code_caption_tab() -> None:
    parser = NotionBlockParser(logger=MagicMock())
    h4 = await _parse(parser, {"id": "h", "type": "heading_4", "heading_4": {"rich_text": _rt("Deep")}}, None)
    assert (h4.sub_type, h4.name, h4.data) == (BlockSubType.HEADING, "H4", "Deep")
    notes = await _parse(parser, {"id": "m", "type": "meeting_notes", "meeting_notes": {"title": _rt("Standup")}}, None)
    assert notes.data == "Standup"
    code = await _parse(parser, {"id": "c", "type": "code", "code": {
        "rich_text": _rt("print(1)"), "language": "python", "caption": _rt("hello")}}, None)
    assert code.type == BlockType.TEXT and code.data == "print(1)" and code.name == "python code: hello"
    tab, group, _ = await parser.parse_block({"id": "t", "type": "tab", "tab": {}, "has_children": True}, None, 0)
    assert tab is None and group is None


@pytest.mark.asyncio
async def test_children_of_a_dropped_block_stay_nested() -> None:
    """An empty toggle (or a tab) renders nothing, but its children keep a wrapper of their own."""
    conn = _connector()
    pages = {"page-1": [{"id": "t1", "type": "toggle", "toggle": {"rich_text": []}, "has_children": True}],
             "t1": [{"id": "p1", "type": "paragraph", "paragraph": {"rich_text": _rt("inside")}, "has_children": False}]}
    conn._fetch_block_children_recursive = AsyncMock(side_effect=lambda bid, *a, **k: pages.get(bid, []))
    blocks, groups = [], []
    top = await conn._process_blocks_recursive("page-1", NotionBlockParser(logger=MagicMock()), blocks, groups, None,
                                               PAGE, "page-1")
    assert len(top) == 1 and top[0].block_group_index is not None
    wrapper = groups[top[0].block_group_index]
    assert blocks[0].data == "inside" and blocks[0].parent_index == wrapper.index
    assert wrapper.children.block_ranges[0].start == 0
