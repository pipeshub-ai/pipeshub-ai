"""What a record render spends and says: continuation offsets, image slots,
table framing against the budget, and max_blocks per record."""

from __future__ import annotations

import base64
import random
import re
import struct
import zlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record
from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import (
    CitationRefMapper,
    ImageBudget,
    record_to_message_content,
)
from app.utils.render_budget import RenderBudget
from tests.unit.agents.actions.knowledge_graph.test_fetch import (
    _make_context,
    _text_record,
)

_HINT = re.compile(r"start_block=(\d+)")


def _png(seed: int) -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixel = bytes([0, seed % 256, (seed * 7) % 256, (seed * 13) % 256])
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(pixel)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def _table_record(rows: list[str], *, ddl: str = "", lead: list[str] | None = None) -> dict:
    lead = lead or []
    blocks = [
        {"index": i, "type": BlockType.TEXT.value, "parent_index": None, "data": text}
        for i, text in enumerate(lead)
    ]
    first_row = len(blocks)
    blocks += [
        {"index": first_row + i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
         "data": {"row_natural_language_text": text}}
        for i, text in enumerate(rows)
    ]
    return {
        "id": "rec-t", "virtual_record_id": "vr-t", "frontend_url": "https://app.test",
        "context_metadata": "Record ID: rec-t",
        "block_containers": {
            "blocks": blocks,
            "block_groups": [{
                "index": 0, "type": GroupType.TABLE.value,
                "data": {"table_summary": "people", "ddl": ddl},
                "children": [{"block_index": first_row + i} for i in range(len(rows))],
            }],
        },
    }


def _text(content: list[dict]) -> str:
    return "".join(item.get("text", "") for item in content)


def _render(record: dict, *, max_chars: int, start_block: int = 0, **kwargs) -> tuple[str, RenderBudget]:
    budget = RenderBudget(max_chars=max_chars)
    content, _ = record_to_message_content(
        record, ref_mapper=CitationRefMapper(), start_block=start_block, budget=budget, **kwargs,
    )
    return _text(content), budget


class TestContinuationOffset:
    def test_a_table_cut_mid_rows_resumes_at_the_first_row_not_shown(self) -> None:
        rows = [f"row {i} " + "x" * 80 for i in range(40)]
        record = _table_record(rows, lead=["intro"])

        first, _ = _render(record, max_chars=1_500)
        (offset,) = {int(m) for m in _HINT.findall(first)}
        shown = {int(m) for m in re.findall(r"row (\d+) x", first)}
        # Row k is block k + 1 (block 0 is the intro).
        assert shown and offset == max(shown) + 2, "resume at the next unread row"

        second, _ = _render(record, max_chars=1_500, start_block=offset)
        again = {int(m) for m in re.findall(r"row (\d+) x", second)}
        assert again and min(again) == max(shown) + 1
        assert not again & shown


class TestImagesAdmittedOnlyWhenRendered:
    def test_image_slots_go_only_to_images_that_rendered(self) -> None:
        blocks = []
        for i in range(12):
            blocks.append({"index": 2 * i, "type": BlockType.TEXT.value, "parent_index": None,
                           "data": f"paragraph {i} " + "y" * 300})
            blocks.append({"index": 2 * i + 1, "type": BlockType.IMAGE.value, "parent_index": None,
                           "data": {"uri": _png(i)}})
        record = {"id": "rec-i", "virtual_record_id": "vr-i", "frontend_url": "https://app.test",
                  "context_metadata": "Record ID: rec-i",
                  "block_containers": {"blocks": blocks, "block_groups": []}}
        image_budget = ImageBudget(max_images=50)
        collected: list[dict] = []

        text, budget = _render(
            record, max_chars=1_200, is_multimodal_llm=True,
            collected_images=collected, image_budget=image_budget,
        )

        rendered_images = [i for i in budget.outcome("rec-i").shown_blocks if i % 2 == 1]
        assert rendered_images, "some images rendered before the budget ran out"
        assert len(rendered_images) < 12
        assert image_budget.used == len(collected) == len(rendered_images)
        assert all(img["block_index"] in rendered_images for img in collected)
        assert budget.chars_used <= budget.max_chars


class TestTableFramingIsCharged:
    def test_rendered_blocks_never_exceed_the_allowance(self) -> None:
        rng = random.Random(21)
        for _ in range(300):
            rows = ["r" * rng.randint(5, 200) for _ in range(rng.randint(1, 40))]
            ddl = "CREATE TABLE t (" + "c INT, " * rng.randint(0, 60) + ")" if rng.random() < 0.5 else ""
            lead = ["lead " + "z" * rng.randint(1, 300) for _ in range(rng.randint(0, 3))]
            record = _table_record(rows, ddl=ddl, lead=lead)
            max_chars = rng.randint(600, 8_000)

            budget = RenderBudget(max_chars=max_chars)
            content, _ = record_to_message_content(record, ref_mapper=CitationRefMapper(), budget=budget)

            body = "".join(
                item["text"] for item in content
                if item.get("type") == "text"
                and not item["text"].startswith("\n[Record ")
                and item["text"] != "\n</record>\n"
            )
            assert budget.chars_used <= max_chars or budget.outcome("rec-t").clipped
            assert len(body) <= budget.chars_used, (len(body), budget.chars_used)


class TestMaxBlocksPerRecord:
    @pytest.mark.asyncio
    async def test_each_record_gets_max_blocks(self) -> None:
        records = [_text_record(f"r{i}", blocks=10, chars=20) for i in range(2)]
        context = _make_context(include_retrieval_context=True)
        structured = MagicMock()
        structured.coroutine = AsyncMock(return_value={"ok": True, "records": records, "not_available_ids": []})
        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=structured):
            await execute_fetch_record(
                context=context, virtual_records={}, citation_ref_mapper=None,
                record_ids=["r0", "r1"], max_blocks=3,
            )

        outcomes = context.tool_state["fetch_render_outcomes"]
        assert [outcomes[rid][-1]["blocksRendered"] for rid in ("r0", "r1")] == [3, 3]


class TestTraceOfAFetchedTable:
    @pytest.mark.asyncio
    async def test_the_trace_names_every_row_the_model_read(self) -> None:
        """A table is one rendered unit, so `blocksRendered` alone made a
        fetched table look like its first few blocks."""
        from tests.unit.agents.actions.knowledge_graph.test_fetch import _fetch_records

        record = _table_record([f"row {i}" for i in range(40)], lead=["Intro one.", "Intro two."])

        _text_out, context = await _fetch_records([record])

        outcome = context.tool_state["fetch_render_outcomes"]["rec-t"][-1]
        assert outcome["blocksRendered"] < 40
        assert outcome["shownBlocks"] == list(range(42))
