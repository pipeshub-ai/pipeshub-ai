"""What a record renders to when it does not fit.

Covers the three things a block count could never bound: an oversized single
block, a table whose one block expands to thousands of rows, and several
records sharing one allowance.
"""

from __future__ import annotations

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import CitationRefMapper, record_to_message_content
from app.utils.render_budget import TRUNCATION_MARKER, RenderBudget


def _text_block(index: int, text: str) -> dict:
    return {"index": index, "type": BlockType.TEXT.value, "parent_index": None, "data": text}


def _record(record_id: str, blocks: list[dict], block_groups: list[dict] | None = None) -> dict:
    return {
        "id": record_id,
        "virtual_record_id": f"v-{record_id}",
        "frontend_url": "https://app.example.com",
        "context_metadata": f"Record ID: {record_id}",
        "block_containers": {"blocks": blocks, "block_groups": block_groups or []},
    }


def _table_record(record_id: str, rows: int) -> dict:
    row_blocks = [
        {
            "index": i,
            "type": BlockType.TABLE_ROW.value,
            "parent_index": 0,
            "data": {"row_natural_language_text": f"row {i} value {'v' * 40}"},
        }
        for i in range(rows)
    ]
    group = {
        "index": 0,
        "type": GroupType.TABLE.value,
        "data": {"table_summary": "a wide table"},
        "children": {"block_ranges": [{"start": 0, "end": rows - 1}]},
        "table_metadata": {"num_of_cells": rows},
    }
    return _record(record_id, row_blocks, [group])


def _render(record: dict, budget: RenderBudget, **kwargs) -> str:
    content, _ = record_to_message_content(
        record, ref_mapper=CitationRefMapper(), budget=budget, **kwargs
    )
    return "".join(c.get("text", "") for c in content if c.get("type") == "text")


class TestCharacterAllowance:
    def test_a_record_stops_when_the_allowance_runs_out(self) -> None:
        record = _record("rec-1", [_text_block(i, "x" * 500) for i in range(50)])
        budget = RenderBudget(max_chars=2_000)

        text = _render(record, budget)

        assert len(text) < 4_000
        assert budget.outcome("rec-1").complete is False

    def test_a_single_block_larger_than_the_whole_allowance_still_renders(self) -> None:
        """Returning nothing at all is worse than returning a prefix."""
        record = _record("rec-1", [_text_block(0, "y" * 50_000)])
        budget = RenderBudget(max_chars=1_000)

        text = _render(record, budget)

        assert "yyy" in text
        assert TRUNCATION_MARKER.strip() in text

    def test_a_record_that_fits_is_complete(self) -> None:
        record = _record("rec-1", [_text_block(i, f"block {i}") for i in range(5)])
        budget = RenderBudget(max_chars=100_000)

        text = _render(record, budget)

        assert "block 4" in text
        assert budget.outcome("rec-1").complete is True

    def test_records_share_one_allowance(self) -> None:
        """N records in one call must not each get a full window."""
        budget = RenderBudget(max_chars=3_000)
        rendered = [
            _render(_record(f"rec-{n}", [_text_block(i, "z" * 400) for i in range(20)]), budget)
            for n in range(3)
        ]

        assert budget.outcome("rec-0").complete is False
        assert len(rendered[2]) < len(rendered[0]), "the last record gets what is left"
        assert sum(len(t) for t in rendered) < 12_000


class TestTables:
    def test_a_wide_table_is_cut_instead_of_rendered_whole(self) -> None:
        """One TABLE_ROW block expands the entire group, so a block count
        never bounded this."""
        budget = RenderBudget(max_chars=2_000)

        text = _render(_table_record("rec-t", rows=5_000), budget)

        assert "row 0" in text
        assert "row 4999" not in text
        assert "of 5000 rows" in text
        assert budget.outcome("rec-t").table_truncation is not None

    def test_a_table_that_fits_is_rendered_whole(self) -> None:
        budget = RenderBudget(max_chars=200_000)

        text = _render(_table_record("rec-t", rows=20), budget)

        assert "row 19" in text
        assert "of 20 rows" not in text

    def test_resuming_inside_a_table_skips_the_rows_already_read(self) -> None:
        """Rows are filtered by start_block; without that a continuation
        re-renders the table from row one, because the seen-groups set is per
        call."""
        budget = RenderBudget(max_chars=200_000)

        text = _render(_table_record("rec-t", rows=40), budget, start_block=20)

        assert "row 25" in text
        assert "row 5 " not in text


class TestDocumentOrder:
    def test_blocks_stored_out_of_order_still_window_correctly(self) -> None:
        """Nothing sorts the stored block list, and the walk compares indices."""
        blocks = [_text_block(i, f"block {i} body") for i in (7, 2, 9, 0, 4)]
        budget = RenderBudget(max_chars=100_000)

        text = _render(_record("rec-o", blocks), budget)

        positions = [text.index(f"block {i} body") for i in (0, 2, 4, 7, 9)]
        assert positions == sorted(positions), "rendered in index order"

    def test_start_block_applies_to_the_index_not_the_list_position(self) -> None:
        blocks = [_text_block(i, f"block {i} body") for i in (9, 1, 5)]
        budget = RenderBudget(max_chars=100_000)

        text = _render(_record("rec-o", blocks), budget, start_block=5)

        assert "block 1 body" not in text
        assert "block 5 body" in text
        assert "block 9 body" in text


class TestSelection:
    def test_only_selected_blocks_render_and_gaps_are_announced(self) -> None:
        """The model must know the document it is reading is not contiguous."""
        record = _record("rec-s", [_text_block(i, f"block {i} body") for i in range(20)])
        budget = RenderBudget(max_chars=100_000)

        text = _render(record, budget, include_blocks={10, 11, 12})

        assert "block 11 body" in text
        assert "block 3 body" not in text
        assert "not shown" in text
        assert "(0–9)" in text

    def test_a_selected_record_is_never_complete(self) -> None:
        """It reported itself complete once, which is what hides a record from
        candidate lists — after the model saw three blocks of four hundred."""
        record = _record("rec-s", [_text_block(i, f"block {i} body") for i in range(400)])
        budget = RenderBudget(max_chars=100_000)

        text = _render(record, budget, include_blocks={299, 300, 301})

        assert budget.outcome("rec-s").complete is False
        assert "showed the 3 block(s) most relevant" in text
        assert "start_block=0" in text, "a path back to reading it in order"

    def test_a_selection_covering_everything_is_complete(self) -> None:
        record = _record("rec-s", [_text_block(i, f"block {i}") for i in range(5)])
        budget = RenderBudget(max_chars=100_000)

        _render(record, budget, include_blocks=set(range(5)))

        assert budget.outcome("rec-s").complete is True

    def test_selection_and_the_allowance_compose(self) -> None:
        record = _record("rec-s", [_text_block(i, "q" * 800) for i in range(30)])
        budget = RenderBudget(max_chars=1_600)

        _render(record, budget, include_blocks=set(range(30)))

        assert budget.outcome("rec-s").complete is False


class TestRecordDelimiters:
    def test_a_record_closes_its_tag(self) -> None:
        text = _render(_record("rec-1", [_text_block(0, "hello")]), RenderBudget(max_chars=10_000))
        assert text.count("<record>") == 1
        assert text.count("</record>") == 1

    def test_two_records_are_separable(self) -> None:
        budget = RenderBudget(max_chars=100_000)
        joined = "\n".join(
            _render(_record(f"rec-{n}", [_text_block(0, f"body {n}")]), budget) for n in range(2)
        )
        assert joined.count("<record>") == 2
        assert joined.count("</record>") == 2

    def test_the_continuation_hint_names_its_record(self) -> None:
        """A result carrying several records cannot be continued from an
        anonymous start_block."""
        record = _record("rec-9", [_text_block(i, "w" * 400) for i in range(30)])
        budget = RenderBudget(max_chars=1_200)

        text = _render(record, budget)

        assert "Record rec-9" in text
        assert 'record_ids=["rec-9"]' in text
        assert "start_block=" in text


class TestLegacyCallers:
    def test_a_caller_without_a_budget_is_bounded_only_by_max_blocks(self) -> None:
        """Attachment rendering passes no budget and must behave as before."""
        record = _record("rec-l", [_text_block(i, "m" * 5_000) for i in range(10)])

        content, _ = record_to_message_content(record, ref_mapper=CitationRefMapper())
        text = "".join(c.get("text", "") for c in content if c.get("type") == "text")

        assert len(text) > 45_000, "no character cap for a caller that asked for none"
        assert "block truncated" not in text


class TestBlockGroupsCountAsBlocks:
    """A block group is one renderable unit, like a table or a top-level
    block. It was the one branch of the walk that rendered content without
    charging `count_block()`, so it cost nothing against `max_blocks` and left
    `blocks_rendered` at zero — which `execute_fetch_record` reads to decide a
    resumed fetch found nothing.
    """

    @staticmethod
    def _group_record(record_id: str, lines: int = 4) -> dict:
        # List position must equal `index`: `build_group_blocks` indexes the
        # block list positionally.
        blocks = [
            {"index": i, "type": BlockType.TEXT.value, "parent_index": 0,
             "data": f"group line {i}"}
            for i in range(lines)
        ]
        group = {
            "type": GroupType.LIST.value,
            "data": {},
            "children": {"block_ranges": [{"start": 0, "end": lines - 1}]},
        }
        return _record(record_id, blocks, [group])

    def test_a_rendered_group_is_counted(self) -> None:
        budget = RenderBudget(max_chars=100_000)
        budget.begin_record("rec-g")

        text = _render(self._group_record("rec-g"), budget)

        assert "group line 2" in text
        assert budget.outcome("rec-g").blocks_rendered == 1

    def test_a_resumed_fetch_does_not_report_an_empty_read(self) -> None:
        """The concrete regression: `execute_fetch_record` appends
        "[No blocks at offset N]" when `blocks_rendered == 0`, and used to do
        so directly underneath the group it had just rendered."""
        budget = RenderBudget(max_chars=100_000)
        budget.begin_record("rec-g")

        text = _render(self._group_record("rec-g"), budget, start_block=1)

        assert "group line 2" in text, "content was rendered"
        assert budget.outcome("rec-g").blocks_rendered > 0, "so it must not read as empty"

    def test_groups_consume_the_block_cap(self) -> None:
        """Otherwise a record of many groups renders past `max_blocks`."""
        blocks, groups = [], []
        for g in range(6):
            blocks.append({"index": g, "type": BlockType.TEXT.value,
                           "parent_index": g, "data": f"group {g} body"})
            groups.append({"type": GroupType.LIST.value, "data": {},
                           "children": {"block_ranges": [{"start": g, "end": g}]}})
        budget = RenderBudget(max_chars=100_000, max_blocks=2)
        budget.begin_record("rec-m")

        _render(_record("rec-m", blocks, groups), budget)

        assert budget.outcome("rec-m").blocks_rendered == 2
        assert budget.outcome("rec-m").complete is False

    def test_counting_does_not_change_completeness(self) -> None:
        """`complete` reads `stopped_at_block`/`table_truncation`, never
        `blocks_rendered` — a fully rendered group is still complete."""
        budget = RenderBudget(max_chars=100_000)
        budget.begin_record("rec-g")

        _render(self._group_record("rec-g"), budget)

        assert budget.outcome("rec-g").complete is True
