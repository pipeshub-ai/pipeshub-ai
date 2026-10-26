"""How much of each record one `knowledgegraph__fetch_record` call shows, and
whether what it says about that matches what the model actually reads.

Each class pins one failure: a record that fits cut by a block count, a
relevance selection that hides its own hits, a block limit applied to the
call instead of each record, the first record of a call starving the rest, a
result sized past the tool-result cap and then cut in the middle, a table read
in order but labelled "most relevant", and an entity note outside the budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import ImagePart, ToolMessage, image_data_url
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
    shape_budget_reduction,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record
from app.models.blocks import BlockType, GroupType
from app.utils.render_budget import resolve_render_budget

_CITE_RULE = "\n\nCite facts from the above"
_PARA = re.compile(r"\b(\w+) para (\d+)\b")
_GAP = re.compile(r"not shown — this record is too large to return in one read\. To read this part, call "
                  r"knowledgegraph__fetch_record again with start_block=(\d+)")


@pytest.fixture(autouse=True)
def _sized_by_the_shared_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """These pin fetch with FETCH_SIZE_BY_CHARS off: under the shared 64K
    result cap, with the server's block cap. `test_fetch_size_by_chars`
    covers it on."""
    monkeypatch.setenv("FETCH_SIZE_BY_CHARS", "false")


def _text_record(name: str, blocks: int, chars: int) -> dict[str, Any]:
    return {
        "id": f"rec-{name}",
        "virtual_record_id": f"vr-{name}",
        "record_name": f"Doc {name}",
        "frontend_url": "https://app.test",
        "context_metadata": f"Record ID: rec-{name}\nName: Doc {name}",
        "block_containers": {
            "blocks": [
                {"index": i, "type": BlockType.TEXT.value,
                 "data": (f"{name} para {i} " + "lorem ipsum " * (chars // 12 + 1))[:chars]}
                for i in range(blocks)
            ],
            "block_groups": [],
        },
    }


def _sheet(name: str, rows: int) -> dict[str, Any]:
    record = _text_record(name, 0, 0)
    record["block_containers"] = {
        "blocks": [
            {"index": i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
             "data": {"row_natural_language_text": (f"row {i}: " + "cell value " * 10)[:50]}}
            for i in range(rows)
        ],
        "block_groups": [{
            "index": 0, "type": GroupType.TABLE.value, "data": {"table_summary": "Sheet1"},
            "children": {"block_ranges": [{"start": 0, "end": rows - 1}]},
        }],
    }
    return record


def _hits(name: str, indices: list[int]) -> MagicMock:
    service = MagicMock()
    service.search_with_filters = AsyncMock(return_value={"searchResults": [
        {"metadata": {"virtualRecordId": f"vr-{name}", "blockIndex": i}, "score": 1.0 - n / 100}
        for n, i in enumerate(indices)
    ]})
    return service


def _context(**overrides: object) -> SimpleNamespace:
    graph = AsyncMock()
    graph.config_service = MagicMock()
    values: dict[str, Any] = {
        "model_name": "gpt-4", "org_id": "org-1", "user_id": "user-1",
        "graph_provider": graph, "full_records_fetched": set(), "tool_state": {},
        "is_multimodal_llm": False, "context_length": 128_000, "query": "q",
        "retrieval_service": None, "needs_whole_document": False,
        "include_retrieval_context": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _fetch(context: SimpleNamespace, records: list[dict[str, Any]], **kwargs: object) -> str:
    parts = await _fetch_parts(context, records, **kwargs)
    return "".join(getattr(part, "text", part if isinstance(part, str) else "") or "" for part in parts)


async def _fetch_parts(context: SimpleNamespace, records: list[dict[str, Any]], **kwargs: object) -> list:
    """The fetch's output as a list: its text and any images it returned."""
    structured = MagicMock()
    structured.coroutine = AsyncMock(return_value={"ok": True, "records": records, "not_available_ids": []})
    with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=structured):
        output, mapper = await execute_fetch_record(
            context=context, virtual_records={},
            citation_ref_mapper=context.tool_state.get("citation_ref_mapper"),
            record_ids=[r["id"] for r in records], reason="read it", **kwargs,
        )
    context.tool_state["citation_ref_mapper"] = mapper
    data = output.data
    return list(data) if isinstance(data, list) else [str(data)]


def _shown(text: str, name: str) -> set[int]:
    return {int(i) for n, i in _PARA.findall(text) if n == name}


def _outcome(context: SimpleNamespace, record_id: str) -> dict[str, Any]:
    return context.tool_state["fetch_render_outcomes"][record_id][-1]


def _records_part(text: str) -> str:
    return text[:text.index(_CITE_RULE)]


class TestARecordThatFitsIsReadWhole:
    """G1: 300 short blocks fit the character budget; a 200-block default cut
    them and cost the model a second call."""

    @pytest.mark.asyncio
    async def test_all_blocks_in_one_call(self) -> None:
        context = _context()
        text = await _fetch(context, [_text_record("a", 300, 150)])

        assert _shown(text, "a") == set(range(300))
        assert "To read on" not in text
        assert _outcome(context, "rec-a")["complete"] is True
        assert "rec-a" in context.full_records_fetched


class TestSelectionShowsItsHits:
    """G2: an over-budget record is shown by relevance. The block cap used to
    keep the first 200 selected blocks in document order and drop the hits."""

    @pytest.mark.asyncio
    async def test_the_hits_are_shown_and_the_hint_counts_what_rendered(self) -> None:
        context = _context(retrieval_service=_hits("b", [350, 360, 370]))
        text = await _fetch(context, [_text_record("b", 400, 400)])

        shown = _shown(text, "b")
        assert {350, 360, 370} <= shown
        claimed = re.search(r"showed the (\d+) block\(s\) most relevant to the question, of 400", text)
        assert claimed is not None and int(claimed.group(1)) == len(shown)
        assert len(_records_part(text)) <= resolve_render_budget(128_000).max_chars

    @pytest.mark.asyncio
    async def test_start_block_zero_after_a_selection_reads_in_order(self) -> None:
        service = _hits("b", [350, 360, 370])
        context = _context(retrieval_service=service)
        first = await _fetch(context, [_text_record("b", 400, 400)])
        assert "start_block=0" in first

        second = await _fetch(context, [_text_record("b", 400, 400)], start_block=0, start_block_sent=True)

        assert service.search_with_filters.await_count == 1
        shown = _shown(second, "b")
        assert shown == set(range(len(shown))), "an in-order read from block 0"
        assert "most relevant" not in second
        assert re.search(r"start_block=(\d+)", second).group(1) == str(len(shown))

    @pytest.mark.asyncio
    async def test_a_gap_offset_reads_the_gap(self) -> None:
        service = _hits("b", [350, 360, 370])
        context = _context(retrieval_service=service)
        first = await _fetch(context, [_text_record("b", 400, 400)])
        gaps = [int(g) for g in _GAP.findall(first)]
        assert gaps, "the selection announced its gaps"
        start = gaps[-1]

        second = await _fetch(context, [_text_record("b", 400, 400)], start_block=start, start_block_sent=True)

        assert service.search_with_filters.await_count == 1
        assert start in _shown(second, "b")
        assert min(_shown(second, "b")) == start


class TestAnExplicitZeroOnAFirstReadStillSelects:
    """Models that always send `start_block=0` must still get the relevant
    blocks of a record they have not read before."""

    @pytest.mark.asyncio
    async def test_selection_runs(self) -> None:
        service = _hits("e", [350, 360, 370])
        context = _context(retrieval_service=service)

        text = await _fetch(context, [_text_record("e", 400, 400)], start_block=0, start_block_sent=True)

        service.search_with_filters.assert_awaited_once()
        assert {350, 360, 370} <= _shown(text, "e")

    @pytest.mark.asyncio
    async def test_through_the_tool(self) -> None:
        from app.agents.agent_loop.hooks.citations import (
            CitationCollector,
            _FetchFullRecordTool,
        )

        service = _hits("e", [350, 360, 370])
        context = _context(retrieval_service=service)
        record = _text_record("e", 400, 400)
        context.tool_state["virtual_record_id_to_result"] = {"vr-e": record}
        tool = _FetchFullRecordTool(CitationCollector(context), context)
        structured = MagicMock()
        structured.coroutine = AsyncMock(return_value={"ok": True, "records": [record], "not_available_ids": []})

        with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=structured):
            first = await tool.execute(record_ids=["rec-e"], start_block=0)
            second = await tool.execute(record_ids=["rec-e"], start_block=0)

        assert {350, 360, 370} <= _shown(first.data, "e")
        assert "most relevant" not in second.data
        assert service.search_with_filters.await_count == 1
        sent = [entry["startBlockSent"] for entry in context.tool_state["fetch_render_outcomes"]["rec-e"]]
        assert sent == [True, True]


class TestSelectionIsNotCutByTheBlockCap:
    """G3: many small blocks. The selection grew to fill the characters, the
    block cap rendered 200 of them, and the hint claimed all."""

    @pytest.mark.asyncio
    async def test_selection_fills_the_budget_and_the_hint_is_true(self) -> None:
        context = _context(retrieval_service=_hits("c", [2500, 2600]))
        text = await _fetch(context, [_text_record("c", 3000, 60)])

        shown = _shown(text, "c")
        assert {2500, 2600} <= shown
        claimed = int(re.search(r"showed the (\d+) block\(s\) most relevant", text).group(1))
        assert claimed == len(shown) > 200
        budget = resolve_render_budget(128_000).max_chars
        assert budget * 0.8 <= len(_records_part(text)) <= budget


class TestRecordsOfOneCallShareTheBudget:
    """G4 / G4b: the first record of a multi-record call took the whole budget
    (characters, or the block cap) and the next ones rendered nothing."""

    @pytest.mark.asyncio
    async def test_a_small_record_after_a_large_one_is_read_whole(self) -> None:
        context = _context(retrieval_service=_hits("big", [100, 300]))
        text = await _fetch(context, [_text_record("big", 400, 400), _text_record("small", 10, 200)])

        assert _shown(text, "small") == set(range(10))
        assert _outcome(context, "rec-small")["complete"] is True
        assert _shown(text, "big")

    @pytest.mark.asyncio
    async def test_two_records_that_fit_are_both_read_whole(self) -> None:
        context = _context()
        text = await _fetch(context, [_text_record("p", 150, 150), _text_record("q", 150, 150)])

        assert _shown(text, "p") == set(range(150))
        assert _shown(text, "q") == set(range(150))

    @pytest.mark.asyncio
    async def test_equal_large_records_get_equal_shares(self) -> None:
        context = _context()
        names = ["x", "y", "z"]
        text = await _fetch(context, [_text_record(n, 300, 400) for n in names])

        counts = [len(_shown(text, n)) for n in names]
        assert min(counts) > 0
        assert max(counts) - min(counts) <= 1, counts
        assert len(text) <= DEFAULT_MAX_RESULT_CHARS


class TestTheResultFitsTheToolResultCap:
    """G5: a fetch sized from the window (up to 240k characters) was cut to
    64k in the middle while it reported every block shown."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("window", [8_192, 128_000, 200_000, 1_000_000])
    async def test_nothing_is_cut_after_the_fetch(self, window: int) -> None:
        context = _context(context_length=window, query="")
        text = await _fetch(context, [_text_record("g", 100, 1_200)])

        # A budget that cannot bind: what is under test is the shared cap.
        message = ModelCallContext(
            messages=[ToolMessage(content=text, tool_call_id="c1")], budget=ContextBudget(max_tokens=1_000_000),
        )

        async def _next() -> None:
            return None

        await shape_budget_reduction()(message, _next)
        assert message.messages[0].content == text
        outcome = _outcome(context, "rec-g")
        assert outcome["complete"] is False
        assert "rec-g" not in context.full_records_fetched
        assert set(outcome["shownBlocks"]) == _shown(text, "g")
        next_start = int(re.search(r"start_block=(\d+)", text).group(1))
        assert next_start == max(outcome["shownBlocks"]) + 1


class TestLargeSheets:
    """G6: a large sheet is one table of thousands of rows. Its rows were read
    in order from the first selected row whatever was selected, so a hit deep
    in the sheet was dropped, and the in-order read was labelled "most
    relevant". Row framing was not counted, so it overran the budget."""

    @staticmethod
    def _service() -> MagicMock:
        service = MagicMock()
        service.search_with_filters = AsyncMock(return_value={"searchResults": [
            {"metadata": {"virtualRecordId": "vr-s", "blockIndex": 4200}}]})
        return service

    @pytest.mark.asyncio
    async def test_a_hit_deep_in_the_sheet_is_shown_after_its_gap(self) -> None:
        context = _context(retrieval_service=self._service())
        text = await _fetch(context, [_sheet("s", 5000)])

        rows = [int(r) for r in re.findall(r"row (\d+):", text)]
        assert 0 in rows and 4200 in rows
        assert rows == sorted(rows)
        gaps = [int(g) for g in _GAP.findall(text)]
        assert any(0 < g < 4200 for g in gaps), "the rows between are announced as a gap"
        claimed = re.search(r"showed the (\d+) block\(s\) most relevant to the question, of 5000", text)
        assert claimed is not None and int(claimed.group(1)) == len(rows)
        assert len(_records_part(text)) <= resolve_render_budget(128_000).max_chars

    @pytest.mark.asyncio
    async def test_a_gap_inside_the_sheet_is_read_in_order(self) -> None:
        context = _context(retrieval_service=self._service())
        first = await _fetch(context, [_sheet("s", 5000)])
        start = next(int(g) for g in _GAP.findall(first) if 0 < int(g) < 4200)

        second = await _fetch(context, [_sheet("s", 5000)], start_block=start, start_block_sent=True)

        rows = [int(r) for r in re.findall(r"row (\d+):", second)]
        assert rows == list(range(start, start + len(rows)))
        assert "most relevant" not in second

    @pytest.mark.asyncio
    async def test_a_sheet_read_in_order_says_so(self) -> None:
        context = _context(retrieval_service=self._service(), needs_whole_document=True)
        text = await _fetch(context, [_sheet("s", 5000)])

        rows = [int(r) for r in re.findall(r"row (\d+):", text)]
        assert rows == list(range(len(rows)))
        assert "most relevant" not in text
        assert f"start_block={len(rows)}" in text
        assert len(_records_part(text)) <= resolve_render_budget(128_000).max_chars


class TestMaxBlocksIsPerRecord:
    """G8: `max_blocks` is described per record but was enforced per call."""

    @pytest.mark.asyncio
    async def test_records_under_the_limit_are_complete(self) -> None:
        context = _context()
        text = await _fetch(
            context, [_text_record(f"m{i}", 40, 150) for i in range(3)], start_block=0, max_blocks=50,
        )

        for i in range(3):
            assert _shown(text, f"m{i}") == set(range(40))
            assert _outcome(context, f"rec-m{i}")["complete"] is True
        assert "To read on" not in text

    @pytest.mark.asyncio
    async def test_each_record_gets_the_limit(self) -> None:
        context = _context()
        text = await _fetch(context, [_text_record(f"n{i}", 40, 150) for i in range(3)], max_blocks=10)

        for i in range(3):
            assert _shown(text, f"n{i}") == set(range(10))
            entry = _outcome(context, f"rec-n{i}")
            assert entry["truncatedBy"] == "blocks"
            assert entry["requestedMaxBlocks"] == 10


class TestEntityNote:
    """G9: the named-entity note was added outside the budget, one graph read
    after another."""

    @staticmethod
    def _graph(records: int, *, names: int = 20) -> tuple[AsyncMock, dict[str, int]]:
        state = {"in_flight": 0, "peak": 0}
        everyone = asyncio.Event()

        async def entities(record_id: str) -> list[dict[str, str]]:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            if state["in_flight"] >= records:
                everyone.set()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(everyone.wait(), timeout=0.5)
            state["in_flight"] -= 1
            return [{"kind": "ORG", "name": f"{record_id} organisation number {n} " + "x" * 80}
                    for n in range(names)]

        graph = AsyncMock()
        graph.get_named_entities_for_record = AsyncMock(side_effect=entities)
        return graph, state

    @pytest.mark.asyncio
    async def test_the_note_is_counted_and_the_reads_run_together(self) -> None:
        graph, state = self._graph(3)
        context = _context(query="")
        context.tool_state.update({"graph_provider": graph, "config_service": MagicMock()})
        records = [_text_record(f"t{i}", 200, 400) for i in range(3)]

        with patch(
            "app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
            AsyncMock(return_value=True),
        ):
            text = await _fetch(context, records)

        assert "Named entities on rec-t0" in text
        assert len(_records_part(text)) <= resolve_render_budget(128_000).max_chars
        assert state["peak"] == 3


class TestWindowsAndModalities:
    @pytest.mark.asyncio
    async def test_a_small_window_result_stays_inside_the_cap(self) -> None:
        context = _context(context_length=8_192, query="")
        text = await _fetch(context, [_text_record("w", 300, 400)])

        assert len(_records_part(text)) <= resolve_render_budget(8_192).max_chars
        assert len(text) <= DEFAULT_MAX_RESULT_CHARS

    @pytest.mark.asyncio
    async def test_a_large_window_result_stays_inside_the_cap(self) -> None:
        context = _context(context_length=1_000_000, query="")
        text = await _fetch(context, [_text_record(f"L{i}", 300, 400) for i in range(4)])

        assert len(text) <= DEFAULT_MAX_RESULT_CHARS
        assert all(_shown(text, f"L{i}") for i in range(4))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("multimodal", [False, True], ids=["text-only", "multimodal"])
    async def test_images_are_admitted_only_for_blocks_that_rendered(self, multimodal: bool) -> None:
        from tests.unit.utils.test_fetch_citation_refs import _png

        blocks: list[dict[str, Any]] = []
        for i in range(20):
            blocks.append({"index": 2 * i, "type": BlockType.TEXT.value, "data": f"im para {2 * i} " + "y" * 20_000})
            blocks.append({"index": 2 * i + 1, "type": BlockType.IMAGE.value,
                           "data": {"uri": _png(i), "description": f"figure {i}"}})
        record = _text_record("im", 0, 0)
        record["block_containers"] = {"blocks": blocks, "block_groups": []}
        context = _context(is_multimodal_llm=multimodal, query="")

        parts = await _fetch_parts(context, [record])
        text = "".join(part if isinstance(part, str) else getattr(part, "text", "") for part in parts)

        shown = set(_outcome(context, "rec-im")["shownBlocks"])
        assert 0 < len(shown) < 40
        sent = {image_data_url(part.source) for part in parts if isinstance(part, ImagePart)}
        delivered = {2 * i + 1 for i in range(20) if _png(i) in sent}
        if multimodal:
            assert delivered and delivered <= shown
            assert context.tool_state["image_budget"].used == len(delivered)
        else:
            assert delivered == set()
            assert "(image, not shown)" in text


class TestAFirstUnitThatDoesNotFitStillMakesProgress:
    """The first unit of a read that does not fit is clipped, as a text block
    is, so the continuation it points to moves past it. A table row or an
    image that only waited would send every retry back to the same block."""

    @staticmethod
    def _sql_table(rows: int, ddl_chars: int) -> dict[str, Any]:
        record = _sheet("d", rows)
        record["block_containers"]["block_groups"][0]["data"]["ddl"] = (
            "CREATE TABLE t (" + "col INT, " * (ddl_chars // 9) + ")"
        )
        return record

    @pytest.mark.asyncio
    async def test_a_table_whose_header_does_not_fit_advances(self) -> None:
        record = self._sql_table(rows=30, ddl_chars=60_000)
        context = _context(query="")
        offsets: list[int] = []
        start = 0
        for _ in range(40):
            text = await _fetch(context, [record], start_block=start, start_block_sent=start > 0)
            match = re.search(r"start_block=(\d+) — one record", text)
            if match is None:
                break
            start = int(match.group(1))
            assert not offsets or start > offsets[-1], f"no progress past block {start}"
            offsets.append(start)
        else:
            pytest.fail("the continuations never reached the end of the table")
        assert offsets and offsets[0] == 1

    @pytest.mark.asyncio
    async def test_a_clipped_first_row_leaves_the_record_incomplete(self) -> None:
        context = _context(query="")
        await _fetch(context, [self._sql_table(rows=3, ddl_chars=60_000)])

        outcome = _outcome(context, "rec-d")
        assert outcome["complete"] is False
        assert "rec-d" not in context.full_records_fetched

    @pytest.mark.asyncio
    async def test_an_image_whose_text_does_not_fit_advances(self) -> None:
        from tests.unit.utils.test_fetch_citation_refs import _png

        record = _text_record("im", 0, 0)
        record["block_containers"] = {"blocks": [
            {"index": 0, "type": BlockType.IMAGE.value,
             "data": {"uri": _png(1), "description": "figure " + "z" * 70_000}},
            {"index": 1, "type": BlockType.TEXT.value, "data": "im para 1 after the figure"},
        ], "block_groups": []}
        context = _context(query="", is_multimodal_llm=True)

        parts = await _fetch_parts(context, [record])
        text = "".join(part if isinstance(part, str) else getattr(part, "text", "") for part in parts)

        assert "start_block=1" in text
        assert not any(isinstance(part, ImagePart) for part in parts)
        second = await _fetch(context, [record], start_block=1, start_block_sent=True)
        assert _shown(second, "im") == {1}


class TestEdgesOfACall:
    @pytest.mark.asyncio
    async def test_records_without_room_are_named_as_not_read(self) -> None:
        context = _context(query="")
        records = [_text_record(f"u{i}", 300, 400) for i in range(60)]

        text = await _fetch(context, records)

        unread = [r["id"] for r in records if f"Record {r['id']} was not read" in text]
        assert unread, "sixty large records cannot all fit one result"
        for record_id in unread:
            entry = _outcome(context, record_id)
            assert (entry["complete"], entry["blocksRendered"], entry["truncatedBy"]) == (False, 0, "not_read")
            assert record_id not in context.full_records_fetched
        assert all(_outcome(context, r["id"])["blocksRendered"] > 0 for r in records if r["id"] not in unread)
        assert len(text) <= DEFAULT_MAX_RESULT_CHARS

    @pytest.mark.asyncio
    async def test_the_order_of_the_ids_does_not_decide_who_is_read(self) -> None:
        records = [_text_record("tiny", 5, 100), *(_text_record(f"o{i}", 300, 400) for i in range(2))]
        forward, backward = _context(query=""), _context(query="")

        await _fetch(forward, records)
        await _fetch(backward, list(reversed(records)))

        for record in records:
            ahead = _outcome(forward, record["id"])["blocksRendered"]
            behind = _outcome(backward, record["id"])["blocksRendered"]
            assert abs(ahead - behind) <= 2, (record["id"], ahead, behind)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("raw", "expected"), [
        ("10", 10), (10.0, 10), ("lots", None), (True, None), (0, None),
        ("inf", None), ("Infinity", None), (float("inf"), None), (float("nan"), None),
    ])
    async def test_max_blocks_from_the_model_is_coerced_not_trusted(self, raw: object, expected: int | None) -> None:
        context = _context(query="")

        await _fetch(context, [_text_record("c", 40, 150)], max_blocks=raw)

        entry = _outcome(context, "rec-c")
        assert entry["requestedMaxBlocks"] == expected
        assert entry["blocksRendered"] == (expected or 40)

    @pytest.mark.asyncio
    async def test_one_failed_entity_read_costs_only_its_record(self) -> None:
        async def entities(record_id: str) -> list[dict[str, str]]:
            if record_id == "rec-f0":
                raise RuntimeError("graph unavailable")
            return [{"kind": "ORG", "name": "Harbor Bank"}]

        graph = AsyncMock()
        graph.get_named_entities_for_record = AsyncMock(side_effect=entities)
        context = _context(query="")
        context.tool_state.update({"graph_provider": graph, "config_service": MagicMock()})
        with patch(
            "app.services.featureflag.platform_settings.is_named_entity_extraction_enabled",
            AsyncMock(return_value=True),
        ):
            text = await _fetch(context, [_text_record(f"f{i}", 5, 100) for i in range(2)])

        assert "Named entities on rec-f1: ORG:Harbor Bank" in text
        assert "Named entities on rec-f0" not in text
        assert _shown(text, "f0") == set(range(5))

    @pytest.mark.asyncio
    async def test_with_short_record_ids_the_result_still_fits(self) -> None:
        context = _context(query="")
        context.tool_state["enable_record_id_shortening"] = True
        records = [_text_record(f"s{i}", 300, 400) for i in range(3)]

        text = await _fetch(context, records)

        assert len(text) <= DEFAULT_MAX_RESULT_CHARS
        assert all(_shown(text, f"s{i}") for i in range(3))


def _png_uri() -> str:
    from tests.unit.utils.test_fetch_citation_refs import _png

    return _png(3)


class TestImageFormsAreCharged:
    """A group or table row that carries an image renders in another form;
    its text was not charged, so a large one passed the cap while the record
    was reported complete."""

    @staticmethod
    def _list_with_an_image(items: int, chars: int) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = [
            {"index": i, "type": BlockType.TEXT.value, "parent_index": 0,
             "data": f"gl para {i} " + "x" * chars}
            for i in range(items)
        ]
        # Item 1 is split around an inline image: its text and pixels are fragments.
        blocks[1] = {"index": 1, "type": BlockType.TEXT.value, "parent_index": 0, "data": ""}
        blocks.append({"index": items, "type": BlockType.TEXT.value, "parent_block_index": 1,
                       "data": "gl para 1 caption text"})
        blocks.append({"index": items + 1, "type": BlockType.IMAGE.value, "parent_block_index": 1,
                       "data": {"uri": _png_uri()}})
        record = _text_record("gl", 0, 0)
        record["block_containers"] = {"blocks": blocks, "block_groups": [{
            "index": 0, "type": GroupType.LIST.value, "data": {},
            "children": {"block_ranges": [{"start": 0, "end": items - 1}]},
        }]}
        return record

    @pytest.mark.asyncio
    async def test_a_group_with_an_image_stays_inside_the_budget(self) -> None:
        context = _context(query="", is_multimodal_llm=True)

        text = await _fetch(context, [self._list_with_an_image(items=60, chars=1_500)])

        assert len(_records_part(text)) <= resolve_render_budget(128_000).max_chars
        assert _outcome(context, "rec-gl")["complete"] is False
        assert "rec-gl" not in context.full_records_fetched

    @pytest.mark.asyncio
    async def test_a_group_with_an_image_that_fits_is_read_whole(self) -> None:
        context = _context(query="", is_multimodal_llm=True)

        text = await _fetch(context, [self._list_with_an_image(items=6, chars=100)])

        assert _outcome(context, "rec-gl")["complete"] is True
        assert "gl para 5" in text

    @pytest.mark.asyncio
    async def test_a_table_with_image_rows_stays_inside_the_budget(self) -> None:
        blocks: list[dict[str, Any]] = [
            {"index": i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
             "data": {"row_natural_language_text": "" if i % 2 else f"row {i}: " + "v" * 200}}
            for i in range(400)
        ]
        fragments = []
        for i in range(1, 400, 2):
            fragments.append({"index": 1000 + 2 * i, "type": BlockType.TEXT.value, "parent_block_index": i,
                              "data": f"row {i}: " + "w" * 200})
            fragments.append({"index": 1001 + 2 * i, "type": BlockType.IMAGE.value, "parent_block_index": i,
                              "data": {"uri": _png_uri(), "description": "a chart " + "d" * 300}})
        record = _sheet("ti", 0)
        record["block_containers"] = {"blocks": blocks + fragments, "block_groups": [{
            "index": 0, "type": GroupType.TABLE.value, "data": {"table_summary": "Sheet1"},
            "children": {"block_ranges": [{"start": 0, "end": 399}]},
        }]}
        context = _context(query="", is_multimodal_llm=True)

        text = await _fetch(context, [record])

        assert len(_records_part(text)) <= resolve_render_budget(128_000).max_chars
        assert _outcome(context, "rec-ti")["complete"] is False


class TestALargeTableHeader:
    """A SQL table's DDL can be larger than the room. A clip spent the room
    on the DDL and dropped the cells, every continuation repeated the DDL,
    and a read past the end marked the record fully fetched."""

    @staticmethod
    def _record() -> dict[str, Any]:
        return TestAFirstUnitThatDoesNotFitStillMakesProgress._sql_table(rows=4, ddl_chars=60_000)

    @pytest.mark.asyncio
    async def test_a_clipped_first_row_shows_its_cells(self) -> None:
        context = _context(query="")
        text = await _fetch(context, [self._record()])

        assert "row 0:" in text

    @pytest.mark.asyncio
    async def test_a_continuation_does_not_repeat_the_ddl(self) -> None:
        context = _context(query="")
        await _fetch(context, [self._record()])

        text = await _fetch(context, [self._record()], start_block=1, start_block_sent=True)

        assert "CREATE TABLE" not in text
        assert "row 1:" in text

    @pytest.mark.asyncio
    async def test_a_read_past_the_end_does_not_mark_the_record_read(self) -> None:
        context = _context(query="")

        await _fetch(context, [self._record()], start_block=4, start_block_sent=True)

        assert _outcome(context, "rec-d")["complete"] is False
        assert "rec-d" not in context.full_records_fetched


class TestAGroupContinues:
    """A group too large for the room was clipped whole, and its continuation
    rendered every child again from the first, so its tail never appeared."""

    @staticmethod
    def _list(*, image: bool) -> dict[str, Any]:
        if image:
            return TestImageFormsAreCharged._list_with_an_image(items=60, chars=1_500)
        record = TestImageFormsAreCharged._list_with_an_image(items=60, chars=1_500)
        blocks = record["block_containers"]["blocks"]
        record["block_containers"]["blocks"] = [b for b in blocks if b.get("parent_block_index") is None]
        record["block_containers"]["blocks"][1]["data"] = "gl para 1 " + "x" * 1_500
        return record

    @pytest.mark.asyncio
    @pytest.mark.parametrize("image", [False, True], ids=["text", "image"])
    async def test_the_continuation_reads_on_from_the_hint(self, image: bool) -> None:
        context = _context(query="", is_multimodal_llm=image)
        first = await _fetch(context, [self._list(image=image)])
        start = int(re.search(r"start_block=(\d+) — one record", first).group(1))

        second = await _fetch(context, [self._list(image=image)], start_block=start, start_block_sent=True)

        assert "gl para 0 " not in second
        assert _shown(second, "gl") - _shown(first, "gl")
        assert min(_shown(second, "gl")) == start

    @pytest.mark.asyncio
    @pytest.mark.parametrize("image", [False, True], ids=["text", "image"])
    async def test_following_the_hints_reaches_the_last_item(self, image: bool) -> None:
        context = _context(query="", is_multimodal_llm=image)
        seen: set[int] = set()
        start = 0
        for _ in range(10):
            text = await _fetch(context, [self._list(image=image)], start_block=start, start_block_sent=start > 0)
            seen |= _shown(text, "gl")
            match = re.search(r"start_block=(\d+) — one record", text)
            if match is None:
                break
            assert int(match.group(1)) > start
            start = int(match.group(1))
        assert seen == set(range(60))

    @pytest.mark.asyncio
    async def test_a_selection_inside_a_group_shows_its_hit_after_a_gap(self) -> None:
        record = _text_record("sg", 400, 400)
        for block in record["block_containers"]["blocks"]:
            block["parent_index"] = 0
        record["block_containers"]["block_groups"] = [{
            "index": 0, "type": GroupType.LIST.value, "data": {},
            "children": {"block_ranges": [{"start": 0, "end": 399}]},
        }]
        context = _context(retrieval_service=_hits("sg", [350]))

        text = await _fetch(context, [record])

        shown = _shown(text, "sg")
        assert 0 in shown and 350 in shown
        assert any(0 < int(g) < 350 for g in _GAP.findall(text))
        claimed = re.search(r"showed the (\d+) block\(s\) most relevant to the question, of 400", text)
        assert claimed is not None and int(claimed.group(1)) == len(shown)
        assert len(_records_part(text)) <= resolve_render_budget(128_000).max_chars

    @pytest.mark.asyncio
    async def test_a_child_that_could_not_be_clipped_is_not_skipped(self) -> None:
        from app.utils.render_budget import RenderBudget

        record = self._list(image=False)
        record["block_containers"]["blocks"][0]["data"] = "gl para 0 " + "x" * 70_000
        context = _context(query="")
        with patch.object(RenderBudget, "take", return_value=None):
            text = await _fetch(context, [record])

        assert "start_block=0 to read it on its own" in text
        assert _outcome(context, "rec-gl")["blocksRendered"] == 0


class TestAFigureInAGroup:
    """Parsers parent a figure to its list or section with `parent_index`. The
    group's render leaves images out, so the image branch is what prints them;
    skipping them as part of the group dropped the figure."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("multimodal", [False, True], ids=["text-only", "multimodal"])
    async def test_the_figure_and_its_caption_are_shown(self, multimodal: bool) -> None:
        record = _text_record("fg", 0, 0)
        record["block_containers"] = {"blocks": [
            {"index": 0, "type": BlockType.TEXT.value, "parent_index": 0, "data": "fg para 0 the first item"},
            {"index": 1, "type": BlockType.IMAGE.value, "parent_index": 0,
             "data": {"uri": _png_uri(), "description": "quarterly revenue chart"}},
            {"index": 2, "type": BlockType.TEXT.value, "parent_index": 0, "data": "fg para 2 the last item"},
        ], "block_groups": [{
            "index": 0, "type": GroupType.LIST.value, "data": {},
            "children": {"block_ranges": [{"start": 0, "end": 2}]},
        }]}
        context = _context(query="", is_multimodal_llm=multimodal)

        text = await _fetch(context, [record])

        assert "quarterly revenue chart" in text
        assert set(_outcome(context, "rec-fg")["shownBlocks"]) == {0, 1, 2}

    @staticmethod
    def _figure_list(last_chars: int) -> dict[str, Any]:
        record = _text_record("fl", 0, 0)
        record["block_containers"] = {"blocks": [
            {"index": 0, "type": BlockType.TEXT.value, "parent_index": 0, "data": "fl para 0 the first item"},
            {"index": 1, "type": BlockType.IMAGE.value, "parent_index": 0,
             "data": {"uri": _png_uri(), "description": "quarterly revenue chart"}},
            {"index": 2, "type": BlockType.TEXT.value, "parent_index": 0,
             "data": "fl para 2 " + "x" * last_chars},
            {"index": 3, "type": BlockType.TEXT.value, "parent_index": 0, "data": "fl para 3 the last item"},
        ], "block_groups": [{
            "index": 0, "type": GroupType.LIST.value, "data": {},
            "children": {"block_ranges": [{"start": 0, "end": 3}]},
        }]}
        return record

    @pytest.mark.asyncio
    async def test_a_figure_before_a_child_that_does_not_fit_is_not_skipped(self) -> None:
        context = _context(query="")
        seen: set[int] = set()
        texts: list[str] = []
        start = 0
        for _ in range(6):
            text = await _fetch(context, [self._figure_list(80_000)], start_block=start, start_block_sent=start > 0)
            texts.append(text)
            seen |= set(_outcome(context, "rec-fl")["shownBlocks"])
            match = re.search(r"start_block=(\d+)", text)
            if match is None:
                break
            start = int(match.group(1))
        assert any("quarterly revenue chart" in t for t in texts)
        assert {0, 1, 3} <= seen

    @pytest.mark.asyncio
    async def test_a_figure_prints_in_place_between_its_neighbours(self) -> None:
        context = _context(query="")
        text = await _fetch(context, [self._figure_list(100)])

        assert text.index("fl para 0") < text.index("quarterly revenue chart") < text.index("fl para 2")
        assert set(_outcome(context, "rec-fl")["shownBlocks"]) == {0, 1, 2, 3}
        assert _outcome(context, "rec-fl")["complete"] is True

    @pytest.mark.asyncio
    async def test_a_figure_parented_to_a_table_prints_in_place(self) -> None:
        record = _sheet("tf", 6)
        blocks = record["block_containers"]["blocks"]
        blocks.insert(3, {"index": 3, "type": BlockType.IMAGE.value, "parent_index": 0,
                          "data": {"uri": _png_uri(), "description": "a chart of the rows above"}})
        for i, block in enumerate(blocks):
            block["index"] = i
        record["block_containers"]["block_groups"][0]["children"] = {"block_ranges": [{"start": 0, "end": 2}, {"start": 4, "end": 6}]}
        context = _context(query="")

        text = await _fetch(context, [record])

        assert text.index("row 2:") < text.index("a chart of the rows above") < text.index("row 4:")
        assert set(_outcome(context, "rec-tf")["shownBlocks"]) == set(range(7))


class TestClipGuardsAndTelemetry:
    @pytest.mark.asyncio
    async def test_a_table_row_that_could_not_be_clipped_is_not_skipped(self) -> None:
        from app.utils.render_budget import RenderBudget

        context = _context(query="")
        record = TestAFirstUnitThatDoesNotFitStillMakesProgress._sql_table(rows=3, ddl_chars=60_000)
        with patch.object(RenderBudget, "take", return_value=None):
            text = await _fetch(context, [record])

        assert "start_block=0" in text
        assert _outcome(context, "rec-d")["blocksRendered"] == 0

    @pytest.mark.asyncio
    async def test_a_read_past_the_end_says_why_it_is_incomplete(self) -> None:
        context = _context(query="")

        await _fetch(context, [_text_record("pe", 5, 100)], start_block=9, start_block_sent=True)

        entry = _outcome(context, "rec-pe")
        assert (entry["complete"], entry["truncatedBy"]) == (False, "past_end")


class TestAnInfiniteOffsetIsAToolError:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ["inf", float("inf"), "-Infinity"])
    async def test_not_a_traceback(self, raw: object) -> None:
        from app.agents.agent_loop.hooks.citations import (
            CitationCollector,
            _FetchFullRecordTool,
        )

        context = _context(query="")
        output = await _FetchFullRecordTool(CitationCollector(context), context).execute(
            record_ids=["rec-x"], start_block=raw,
        )

        assert output.success is False
        assert "whole number" in output.error


class TestATinyCallStillReadsItsLastRecord:
    """The 2,000-character floor names a record "not read" so that the other
    records of a call get room. When the record's share is the whole call,
    there is no one to make room for, and the note made every retry the same."""

    @pytest.mark.asyncio
    async def test_a_single_record_under_the_floor_is_read_and_advances(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PIPESHUB_FULL_RECORD_MAX_CHARS", "1500")
        record = _text_record("tc", 30, 300)
        context = _context(query="")
        start = 0
        seen: set[int] = set()
        for _ in range(60):
            text = await _fetch(context, [record], start_block=start, start_block_sent=start > 0)
            assert "was not read" not in text
            seen |= _shown(text, "tc")
            match = re.search(r"start_block=(\d+)", text)
            if match is None:
                break
            assert int(match.group(1)) > start
            start = int(match.group(1))
        assert seen == set(range(30))

    @pytest.mark.asyncio
    async def test_a_record_crowded_out_by_later_ones_is_still_named(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PIPESHUB_FULL_RECORD_MAX_CHARS", "3000")
        context = _context(query="")
        text = await _fetch(context, [_text_record(f"cr{i}", 30, 300) for i in range(3)])

        assert "was not read" in text
