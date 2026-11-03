"""What a budgeted record render prints is what it charged, what it reports
as shown, and what a continuation can still reach.

Each test pins a shape that broke one of those: a nested list inside a list,
metadata larger than the room, a gap marker past the budget, a unit printed
whole by a clip path, refs for blocks not printed, children listed out of
index order, and blocks of a shape the renderer cannot show.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import CitationRefMapper, record_to_message_content
from app.utils.render_budget import RenderBudget

_HINT = re.compile(r"start_block=(\d+)")


def _record(blocks: list[dict[str, Any]], groups: list[dict[str, Any]] | None = None,
            metadata: str = "Record ID: rec-1") -> dict[str, Any]:
    return {"id": "rec-1", "virtual_record_id": "vr-1", "frontend_url": "https://app.test",
            "context_metadata": metadata,
            "block_containers": {"blocks": blocks, "block_groups": groups or []}}


def _text(index: int, data: str, **extra: object) -> dict[str, Any]:
    return {"index": index, "type": BlockType.TEXT.value, "data": data, **extra}


def _render(record: dict[str, Any], max_chars: int, *, start: int = 0,
            include: set[int] | None = None) -> tuple[str, RenderBudget, CitationRefMapper]:
    budget = RenderBudget(max_chars=max_chars)
    content, mapper = record_to_message_content(
        record, ref_mapper=CitationRefMapper(), start_block=start, budget=budget, include_blocks=include,
    )
    return "".join(c.get("text", "") for c in content if c.get("type") == "text"), budget, mapper


def _follow(record: dict[str, Any], max_chars: int) -> tuple[set[int], bool]:
    """Every block shown by following the hints, and whether the last read
    reported the record complete."""
    shown: set[int] = set()
    start = 0
    for _ in range(30):
        text, budget, _mapper = _render(record, max_chars, start=start)
        outcome = budget.outcome("rec-1")
        shown |= set(outcome.shown_blocks)
        found = _HINT.findall(text)
        if not found:
            return shown, outcome.complete
        assert int(found[-1]) > start, f"no progress past {start}"
        start = int(found[-1])
    raise AssertionError("the hints never ended")


class TestNestedGroups:
    def test_a_list_inside_a_list_is_read_in_place(self) -> None:
        blocks = [_text(0, "item0 " + "a" * 60, parent_index=0), _text(1, "nested1 " + "b" * 60, parent_index=1),
                  _text(2, "nested2 " + "c" * 60, parent_index=1), _text(3, "item3 " + "d" * 60, parent_index=0)]
        groups = [
            {"index": 0, "type": GroupType.LIST.value,
             "children": {"block_ranges": [{"start": 0, "end": 0}, {"start": 3, "end": 3}],
                          "block_group_ranges": [{"start": 1, "end": 1}]}},
            {"index": 1, "type": GroupType.LIST.value, "parent_index": 0,
             "children": {"block_ranges": [{"start": 1, "end": 2}]}},
        ]
        record = _record(blocks, groups)

        for max_chars in (380, 450, 520, 700, 5_000):
            shown, complete = _follow(record, max_chars)
            assert shown == {0, 1, 2, 3}, max_chars
            assert complete

        text, _budget, _mapper = _render(record, 5_000)
        assert text.index("item0") < text.index("nested1") < text.index("nested2") < text.index("item3")

    def test_children_listed_out_of_order_are_read_in_order(self) -> None:
        blocks = [_text(i, f"item{i} " + "z" * 80, parent_index=0) for i in range(4)]
        groups = [{"index": 0, "type": GroupType.LIST.value, "children": [{"block_index": i} for i in (2, 0, 1, 3)]}]

        for max_chars in (380, 450, 600):
            shown, complete = _follow(_record(blocks, groups), max_chars)
            assert shown == {0, 1, 2, 3}, max_chars
            assert complete


class TestWhatIsPrintedIsCharged:
    def test_metadata_larger_than_the_room_is_cut_not_overrun(self) -> None:
        record = _record([_text(0, "body " * 20)], metadata="Record ID: rec-1\n" + "m" * 60_000)

        text, budget, _mapper = _render(record, 40_000)

        assert len(text) <= budget.max_chars
        assert "metadata truncated to fit" in text

    def test_a_gap_marker_is_printed_only_with_room_for_its_block(self) -> None:
        record = _record([_text(0, "A" * 100), _text(1, "B" * 100), _text(2, "C" * 100)])

        for max_chars in range(400, 700, 10):
            text, budget, _mapper = _render(record, max_chars, include={0, 2})
            assert budget.chars_used <= max_chars, max_chars
            if "not shown" in text and "(1–1)" in text:
                assert "C" * 100 in text, max_chars


class TestWhatIsShownIsReported:
    def test_a_table_printed_whole_by_its_clip_counts_its_row(self) -> None:
        rows = [{"index": i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
                 "data": {"row_natural_language_text": f"row{i} " + "c" * 200}} for i in (1, 2)]
        record = _record([_text(0, "intro"), *rows], [{
            "index": 0, "type": GroupType.TABLE.value, "data": {"table_summary": "S" * 400},
            "children": {"block_ranges": [{"start": 1, "end": 2}]},
        }])

        text, budget, _mapper = _render(record, 1_000, include={1, 2})

        shown = budget.outcome("rec-1").shown_blocks
        printed = {i for i in (1, 2) if f"row{i} " + "c" * 200 in text}
        assert printed <= shown
        claimed = re.search(r"showed the (\d+) block", text)
        assert claimed is None or int(claimed.group(1)) == len(shown)

    def test_refs_are_minted_only_for_printed_blocks(self) -> None:
        record = _record([_text(0, "A" * 100), _text(1, "B" * 100), _text(2, "C" * 100)])

        text, _budget, mapper = _render(record, 520)

        printed = set(re.findall(r"\[(ref\d+)\]", text))
        assert set(mapper.ref_to_url) == printed

    def test_a_row_of_a_group_that_is_not_a_table_leaves_the_record_incomplete(self) -> None:
        record = _record(
            [_text(0, "A"), {"index": 1, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
                             "data": {"row_natural_language_text": "ROW1"}}],
            [{"index": 0, "type": GroupType.LIST.value, "children": [{"block_index": 1}]}],
        )

        text, budget, _mapper = _render(record, 10_000)

        assert "ROW1" not in text
        assert budget.outcome("rec-1").complete is False

    def test_a_dropped_table_summary_is_not_claimed_shown(self) -> None:
        rows = [{"index": i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
                 "data": {"row_natural_language_text": f"row{i} " + "c" * 300}} for i in range(3)]
        record = _record(rows, [{
            "index": 0, "type": GroupType.TABLE.value, "data": {"table_summary": "", "ddl": "CREATE " + "D" * 380},
            "children": {"block_ranges": [{"start": 0, "end": 2}]},
        }])

        second, _budget, _mapper = _render(record, 1_000, start=1)

        assert "summary is shown" not in second


class TestOversizedMetadataStillAdvances:
    """Metadata cut to the whole room left nothing for the body: no block was
    shown and the hint named the same offset again."""

    @staticmethod
    def _record(fk: bool = False) -> dict[str, Any]:
        record = _record([_text(i, f"body{i} " + "b" * 300) for i in range(5)],
                         metadata="Record ID: rec-1\n" + "m" * 60_000)
        if fk:
            record["fk_parent_record_ids"] = [
                {"parentTable": "accounts", "sourceColumn": "account_id", "targetColumn": "id", "record_id": "rec-2"},
            ]
        return record

    def test_following_the_hints_reads_the_body(self) -> None:
        shown, _complete = _follow(self._record(), 40_000)

        assert shown == {0, 1, 2, 3, 4}

    def test_the_first_read_shows_a_block(self) -> None:
        text, budget, _mapper = _render(self._record(), 40_000)

        assert budget.outcome("rec-1").blocks_rendered >= 1
        assert len(text) <= budget.max_chars

    def test_foreign_key_lines_are_kept(self) -> None:
        text, budget, _mapper = _render(self._record(fk=True), 40_000)

        assert "Parent Table: accounts" in text
        assert len(text) <= budget.max_chars

    def test_a_room_smaller_than_the_marker_is_not_overrun(self) -> None:
        record = _record([_text(0, "body0 " + "b" * 300)], metadata="m" * 5_000)
        for max_chars in range(300, 1_200, 25):
            text, budget, _mapper = _render(record, max_chars)
            assert budget.chars_used <= max_chars or budget.outcome("rec-1").clipped, max_chars


class TestAGapIsPrintedOnlyBeforeWhatRenders:
    """A gap was priced with the next block's text alone; a table row also
    spends its table's header, so the gap printed and the row did not."""

    def test_a_row_behind_a_large_table_header_leaves_no_dangling_gap(self) -> None:
        rows = [{"index": i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
                 "data": {"row_natural_language_text": f"row{i} " + "c" * 50}} for i in (2, 3)]
        record = _record([_text(0, "A" * 200), _text(1, "B" * 200), *rows], [{
            "index": 0, "type": GroupType.TABLE.value, "data": {"table_summary": "", "ddl": "CREATE " + "D" * 600},
            "children": {"block_ranges": [{"start": 2, "end": 3}]},
        }])

        for max_chars in range(700, 1_600, 20):
            text, budget, _mapper = _render(record, max_chars, include={0, 2, 3})
            shown = budget.outcome("rec-1").shown_blocks
            if 2 not in shown:
                assert "(1–1)" not in text, max_chars
                assert "start_block=2" in text, max_chars
            assert budget.chars_used <= max_chars, max_chars
