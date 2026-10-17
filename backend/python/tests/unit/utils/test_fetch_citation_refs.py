"""Every `[refN]` a fetched record shows must resolve to the URL of the block
it labels. The answer's citations are built from that mapping, so a renderer
change that mints a ref for one block and prints it beside another sends the
reader to the wrong passage without any error.

Checked over text, image (multimodal on and off), table-row and group blocks,
for a whole read, a budget-cut read and its continuation on one mapper.
"""

from __future__ import annotations

import base64
import re
import struct
import zlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import (
    CitationRefMapper,
    ImageBudget,
    build_block_web_url,
    record_to_message_content,
)
from app.utils.render_budget import RenderBudget

FRONTEND = "https://app.test"
RECORD_ID = "rec-g"

_TEXT_REF = re.compile(r"\[(ref\d+)\] (?:\(image[^)]*\) )?(?:blk|img)(\d+)\b")
_ROW_REF = re.compile(r"\[(\d+)\|(ref\d+)\]")


def _png(seed: int) -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    pixel = bytes([0, seed % 256, (seed * 7) % 256, (seed * 13) % 256])
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(pixel)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _record() -> dict[str, Any]:
    """Text, an image, a three-row table, text, a two-item list, an image,
    text -- block index equals list position, as stored records have it."""
    blocks: list[dict[str, Any]] = [
        {"index": 0, "type": BlockType.TEXT.value, "data": "blk0 opening paragraph"},
        {"index": 1, "type": BlockType.IMAGE.value, "data": {"uri": _png(1), "description": "img1 revenue chart"}},
        {"index": 2, "type": BlockType.TEXT.value, "data": "blk2 before the table"},
    ]
    blocks += [
        {"index": i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
         "data": {"row_natural_language_text": f"row{i} region {i} total {i * 10}"}}
        for i in (3, 4, 5)
    ]
    blocks.append({"index": 6, "type": BlockType.TEXT.value, "data": "blk6 after the table"})
    blocks += [
        {"index": i, "type": BlockType.TEXT.value, "parent_index": 1, "data": f"item{i} of the list"}
        for i in (7, 8)
    ]
    blocks.append({"index": 9, "type": BlockType.IMAGE.value, "data": {"uri": _png(9), "description": "img9 org diagram"}})
    blocks.append({"index": 10, "type": BlockType.TEXT.value, "data": "blk10 closing paragraph"})
    return {
        "id": RECORD_ID,
        "virtual_record_id": "vr-g",
        "frontend_url": FRONTEND,
        "context_metadata": f"Record ID: {RECORD_ID}",
        "block_containers": {
            "blocks": blocks,
            "block_groups": [
                {"index": 0, "type": GroupType.TABLE.value, "data": {"table_summary": "regions"},
                 "children": {"block_ranges": [{"start": 3, "end": 5}]}},
                {"index": 1, "type": GroupType.LIST.value, "data": {},
                 "children": {"block_ranges": [{"start": 7, "end": 8}]}},
            ],
        },
    }


def _labelled(text: str, collected: list[dict[str, Any]]) -> dict[str, int]:
    """ref -> the block index it is printed beside."""
    pairs: list[tuple[str, int]] = [(ref, int(idx)) for ref, idx in _TEXT_REF.findall(text)]
    pairs += [(ref, int(idx)) for idx, ref in _ROW_REF.findall(text)]
    pairs += [(img["ref"], int(img["block_index"])) for img in collected]
    labelled: dict[str, int] = {}
    for ref, index in pairs:
        assert labelled.setdefault(ref, index) == index, f"{ref} labels blocks {labelled[ref]} and {index}"
    return labelled


def _assert_refs_resolve(text: str, mapper: CitationRefMapper, collected: list[dict[str, Any]]) -> dict[str, int]:
    labelled = _labelled(text, collected)
    assert labelled, "the render printed no refs"
    urls = mapper.ref_to_url
    for ref, index in labelled.items():
        assert urls.get(ref) == build_block_web_url(FRONTEND, RECORD_ID, index), (ref, index)
    by_block: dict[int, str] = {}
    for ref, index in labelled.items():
        assert by_block.setdefault(index, ref) == ref, f"block {index} printed as {by_block[index]} and {ref}"
    return labelled


def _text(content: list[dict[str, Any]]) -> str:
    return "".join(item.get("text", "") for item in content if item.get("type") == "text")


@pytest.mark.parametrize("multimodal", [False, True], ids=["text-only", "multimodal"])
def test_a_whole_read_prints_each_ref_beside_its_own_block(multimodal: bool) -> None:
    mapper = CitationRefMapper()
    collected: list[dict[str, Any]] = []

    content, mapper = record_to_message_content(
        _record(), ref_mapper=mapper, is_multimodal_llm=multimodal,
        collected_images=collected, image_budget=ImageBudget(max_images=50),
        budget=RenderBudget(max_chars=100_000),
    )

    labelled = _assert_refs_resolve(_text(content), mapper, collected)
    assert set(labelled.values()) == set(range(11))
    assert set(mapper.ref_to_url) == set(labelled)
    if multimodal:
        assert sorted(img["block_index"] for img in collected) == [1, 9]
    else:
        assert collected == []


@pytest.mark.parametrize("multimodal", [False, True], ids=["text-only", "multimodal"])
def test_a_cut_read_and_its_continuation_share_one_consistent_mapping(multimodal: bool) -> None:
    record = _record()
    # The smallest allowance that renders something: how much a record's
    # framing costs is the renderer's business, not this test's.
    for max_chars in range(200, 2_000, 20):
        mapper = CitationRefMapper()
        collected: list[dict[str, Any]] = []
        first_budget = RenderBudget(max_chars=max_chars)
        first, mapper = record_to_message_content(
            record, ref_mapper=mapper, is_multimodal_llm=multimodal,
            collected_images=collected, image_budget=ImageBudget(max_images=50), budget=first_budget,
        )
        stop = first_budget.outcome(RECORD_ID).stopped_at_block
        if stop:
            break
    assert stop is not None and 0 < stop <= 10, "the budget cut the first read"

    second, mapper = record_to_message_content(
        record, ref_mapper=mapper, is_multimodal_llm=multimodal, start_block=stop,
        collected_images=collected, image_budget=ImageBudget(max_images=50),
        budget=RenderBudget(max_chars=100_000),
    )

    labelled = _assert_refs_resolve(_text(first) + _text(second), mapper, collected)
    assert set(range(stop, 11)) <= set(labelled.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("multimodal", [False, True], ids=["text-only", "multimodal"])
async def test_a_fetch_result_prints_each_ref_beside_its_own_block(multimodal: bool) -> None:
    from types import SimpleNamespace

    from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record

    context = SimpleNamespace(
        model_name="gpt-4", org_id="org-1", user_id="user-1", graph_provider=AsyncMock(),
        full_records_fetched=set(), tool_state={}, is_multimodal_llm=multimodal,
        context_length=128_000, query="", retrieval_service=None,
    )
    structured = MagicMock()
    structured.coroutine = AsyncMock(return_value={"ok": True, "records": [_record()], "not_available_ids": []})
    with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=structured):
        output, mapper = await execute_fetch_record(
            context=context, virtual_records={}, citation_ref_mapper=None, record_ids=[RECORD_ID],
        )

    data = output.data
    parts = data if isinstance(data, list) else [data]
    text = "".join(getattr(p, "text", p if isinstance(p, str) else "") or "" for p in parts)
    images = context.tool_state.get("pending_tool_images") or []
    labelled = _assert_refs_resolve(text, mapper, [])
    assert set(labelled.values()) == set(range(11))
    if multimodal:
        assert len([p for p in parts if not isinstance(p, str) and getattr(p, "type", "") != "text"]) == 2
    assert images == []
