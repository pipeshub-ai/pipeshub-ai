"""An update keeps the text of the table rows the stored version already described, and describes only the others.

Measured before this change, with a real model: a 30-row table described twice kept the text of 0 of its 30 rows, and after a
one-cell edit 0 of the 29 unchanged rows kept theirs. A row's block id follows its text, so every row of an edited table was
re-embedded. The fake model here answers over the real call path and words every row by the model's own style ("A: ..." or
"B: ..."), so a row that keeps its text is one that was not sent again. All data is synthetic.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.events.events import keeps_stored_row_text
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
    TableMetadata,
    TableRowMetadata,
)
from app.modules.pipeline.fingerprint import content_facts
from app.modules.reconciliation.block_identity import BlockIdentity
from app.utils import table_enrichment
from app.utils.table_row_memory import (
    ROW_DIGEST_KEY,
    PreviousRows,
    end_previous_rows,
    hashed_block_data,
    offer_previous_rows,
    previous_row_texts,
    row_digest,
    row_texts_of,
    with_row_digest,
)
from tests.support.table_model import HEAD, FakeTableModel, by_first_row, make_rows

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.utils.table_enrichment import TableEnrichmentResult

LOG = logging.getLogger("test-table-row-memory")
GRID = [HEAD, *make_rows(30)]
EDITED_ROW = 12  # the data row whose cell an edit changes


@pytest.fixture(autouse=True)
def _pinned(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("INDEXING_TABLE_ROW_BATCH_SIZE", "50")
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "1000")
    end_previous_rows()
    yield
    end_previous_rows()


def _edited(grid: list[list[str]] = GRID) -> list[list[str]]:
    out = [list(r) for r in grid]
    out[EDITED_ROW + 1][1] = "0xFFF"
    return out


def _model(style: str, **kw: Any) -> FakeTableModel:  # noqa: ANN401
    return FakeTableModel(describe=lambda rid: f"{style}: {rid}", **kw)


def _stored(*results: TableEnrichmentResult) -> BlocksContainer:
    """The row blocks a parser stores from *results* (one table each), as the stored version of the record."""
    rows = [(table, i, text, result) for table, result in enumerate(results) for i, text in enumerate(result.descriptions)]
    blocks = [
        Block(
            index=n, parent_index=table, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
            data=with_row_digest({"row_natural_language_text": text, "row_number": i + 1}, result.row_digests, i),
        )
        for n, (table, i, text, result) in enumerate(rows)
    ]
    return BlocksContainer(blocks=blocks, block_groups=[])


def _offer(container: BlocksContainer | None) -> AsyncMock:
    load = AsyncMock(return_value=container)
    offer_previous_rows(PreviousRows(load, LOG, "rec-1"))
    return load


async def _enrich(model: FakeTableModel, grid: list[list[str]], *, known: int | None, **kw: Any) -> TableEnrichmentResult:  # noqa: ANN401
    return await table_enrichment.enrich_table_grid(model, grid, logger=LOG, known_header_row_count=known, **kw)


class TestTheDigest:
    def test_the_same_row_under_the_same_header_has_the_same_digest(self) -> None:
        assert row_digest(["a", "b"], ["1", "2"]) == row_digest(["a", "b"], ["1", "2"])

    def test_a_changed_cell_or_header_changes_it(self) -> None:
        base = row_digest(["a", "b"], ["1", "2"])
        assert row_digest(["a", "b"], ["1", "3"]) != base
        assert row_digest(["a", "c"], ["1", "2"]) != base

    def test_a_table_without_a_header_is_not_one_with_an_empty_header(self) -> None:
        assert row_digest(None, ["1"]) != row_digest([], ["1"])
        assert row_digest(None, ["1"]) != row_digest([""], ["1"])

    def test_cells_are_not_run_together(self) -> None:
        assert row_digest(None, ["ab", "c"]) != row_digest(None, ["a", "bc"])

    def test_no_cell_text_can_move_a_boundary(self) -> None:
        # Kills: separator-joined cells, where a cell holding the separator makes two different rows one digest.
        assert row_digest(None, ["a\x1fb", "c"]) != row_digest(None, ["a", "b\x1fc"])
        assert row_digest(["h", "x"], ["y"]) != row_digest(["h"], ["x", "y"])
        assert row_digest(["h\x1e"], ["y"]) != row_digest(["h"], ["\x1ey"])


class TestStoredRows:
    def test_only_table_rows_with_a_digest_and_a_text_are_read(self) -> None:
        container = BlocksContainer(blocks=[
            Block(index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON, data={"row_natural_language_text": "kept", ROW_DIGEST_KEY: "d0"}),
            Block(index=1, type=BlockType.TABLE_ROW, format=DataFormat.JSON, data={"row_natural_language_text": "no digest"}),
            Block(index=2, type=BlockType.TABLE_ROW, format=DataFormat.JSON, data={"row_natural_language_text": " ", ROW_DIGEST_KEY: "d2"}),
            Block(index=3, type=BlockType.TEXT, format=DataFormat.TXT, data="text"),
        ], block_groups=[])
        assert row_texts_of(container) == [{"d0": ["kept"]}]
        assert row_texts_of(None) == []

    def test_each_table_and_each_repeat_of_a_row_keeps_its_own_text(self) -> None:
        def row(i: int, table: int, text: str) -> Block:
            return Block(index=i, parent_index=table, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
                         data={"row_natural_language_text": text, ROW_DIGEST_KEY: "total"})
        container = BlocksContainer(blocks=[row(0, 0, "q1 first"), row(1, 0, "q1 second"), row(2, 1, "q2")], block_groups=[])
        assert row_texts_of(container) == [{"total": ["q1 first", "q1 second"]}, {"total": ["q2"]}]

    def test_a_row_without_a_digest_loses_a_stale_one(self) -> None:
        assert with_row_digest({"x": 1}, ["d"], 0) == {"x": 1, ROW_DIGEST_KEY: "d"}
        assert with_row_digest({"x": 1, ROW_DIGEST_KEY: "old"}, [None], 0) == {"x": 1}
        assert with_row_digest({"x": 1}, [], 3) == {"x": 1}

    @pytest.mark.asyncio
    async def test_nothing_is_offered_outside_an_update(self) -> None:
        assert await previous_row_texts() is None

    @pytest.mark.asyncio
    async def test_the_stored_record_is_read_once_on_the_first_ask(self) -> None:
        load = _offer(_stored(await _enrich(_model("A"), GRID, known=1)))
        load.assert_not_awaited()
        first, second = await asyncio.gather(previous_row_texts(), previous_row_texts())
        assert first is second and [len(table) for table in first] == [30]
        load.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_read_that_fails_gives_no_rows_and_one_warning_without_any_text(self) -> None:
        logger = MagicMock()
        offer_previous_rows(PreviousRows(AsyncMock(side_effect=OSError("blob store down: secret-path")), logger, "rec-1"))
        assert await previous_row_texts() == []
        assert await previous_row_texts() == []
        logger.warning.assert_called_once()
        assert "secret-path" not in str(logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_the_end_of_a_delivery_withdraws_the_rows(self) -> None:
        _offer(BlocksContainer(blocks=[], block_groups=[]))
        end_previous_rows()
        assert await previous_row_texts() is None


class TestAnUpdateOfAnEditedTable:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("known", [1, None], ids=["header-known", "header-decided-by-the-model"])
    async def test_only_the_edited_row_is_described_again(self, known: int | None) -> None:
        # Kills: describing the whole table again whenever one cell changes (every row's text and vector replaced).
        first = await _enrich(_model("A", header_row_count=1), GRID, known=known)
        _offer(_stored(first))
        model = _model("B", header_row_count=1)
        result = await _enrich(model, _edited(), known=known)

        assert model.count("combined") == 0 and model.count("summary") == 1
        assert model.row_calls == [1]
        assert result.descriptions[EDITED_ROW] == f"B: REG{EDITED_ROW}"
        kept = [i for i in range(30) if i != EDITED_ROW]
        assert [result.descriptions[i] for i in kept] == [first.descriptions[i] for i in kept]
        assert all(result.row_digests)

    @pytest.mark.asyncio
    async def test_an_unchanged_table_sends_no_row_at_all(self) -> None:
        first = await _enrich(_model("A"), GRID, known=1)
        _offer(_stored(first))
        model = _model("B")
        result = await _enrich(model, GRID, known=1)
        assert model.row_calls == []
        assert result.descriptions == first.descriptions

    @pytest.mark.asyncio
    async def test_a_row_equal_to_one_of_another_table_keeps_its_own_tables_text(self) -> None:
        # Kills: one record-wide digest map, where the first table's wording of an equal row replaces the second table's.
        other = [HEAD, GRID[1], *[[f"OTHER{i}", "0x1", "0"] for i in range(10)]]
        first = await _enrich(_model("A"), GRID, known=1)
        second = await _enrich(_model("C"), other, known=1)
        assert first.row_digests[0] == second.row_digests[0] and first.descriptions[0] != second.descriptions[0]
        _offer(_stored(first, second))
        edited = [list(r) for r in other]
        edited[5][1] = "0xFFF"
        model = _model("B")
        result = await _enrich(model, edited, known=1)
        assert model.row_calls == [1]
        assert [result.descriptions[i] for i in range(11) if i != 4] == [second.descriptions[i] for i in range(11) if i != 4]

    @pytest.mark.asyncio
    async def test_each_repeat_of_a_row_keeps_its_own_text(self) -> None:
        # Kills: every repeat of a row taking the first repeat's stored text.
        grid = [HEAD, GRID[1], GRID[1], GRID[2]]
        stored = await _enrich(_model("A"), grid, known=1)
        stored.descriptions[:2] = ["the first", "the second"]
        _offer(_stored(stored))
        model = _model("B")
        result = await _enrich(model, [*grid, GRID[1]], known=1)
        assert result.descriptions[:3] == ["the first", "the second", stored.descriptions[2]]
        assert model.row_calls == [1] and result.descriptions[3] == "B: REG0"

    @pytest.mark.asyncio
    async def test_the_stored_table_that_can_give_the_most_texts_is_used(self) -> None:
        # Kills: scoring a table by digest membership, where A=[X,Y] outscores B=[X,X,X,Z] for B updated to [X,X,X,Y].
        x, y, z = GRID[1], GRID[2], GRID[3]
        a = await _enrich(_model("A"), [HEAD, x, y], known=1)
        b = await _enrich(_model("C"), [HEAD, x, x, x, z], known=1)
        _offer(_stored(a, b))
        model = _model("B")
        result = await _enrich(model, [HEAD, x, x, x, y], known=1)
        assert result.descriptions == ["C: REG0"] * 3 + ["B: REG1"]
        assert model.row_calls == [1]

    @pytest.mark.asyncio
    async def test_two_stored_tables_that_tie_give_no_text(self) -> None:
        # Kills: the first of two tied tables winning, where a shared `Total` row takes the other table's wording.
        total = GRID[1]
        first = await _enrich(_model("A"), [HEAD, total, GRID[2]], known=1)
        second = await _enrich(_model("C"), [HEAD, total, ["OTHER0", "0x1", "0"]], known=1)
        _offer(_stored(first, second))
        model = _model("B")
        result = await _enrich(model, [HEAD, total, ["OTHER0", "0xFFF", "0"]], known=1)
        assert model.calls == [("combined", 2)]
        assert result.descriptions[0] == "B: REG0" and result.descriptions[1].startswith("B: ")

    @pytest.mark.asyncio
    async def test_the_kept_rows_still_get_the_new_summary(self) -> None:
        _offer(_stored(await _enrich(_model("A"), GRID, known=1)))
        result = await _enrich(_model("B", summary="Register map, revised"), _edited(), known=1)
        assert result.summary == "Register map, revised"


class TestWhenNothingIsKept:
    @pytest.mark.asyncio
    async def test_a_first_index_runs_as_before_and_records_every_row(self) -> None:
        model = _model("A")
        result = await _enrich(model, GRID, known=1)
        assert model.calls == [("combined", 30)]
        assert len(result.row_digests) == 30 and all(result.row_digests)

    @pytest.mark.asyncio
    async def test_a_table_the_stored_version_did_not_have_runs_as_before(self) -> None:
        _offer(_stored(await _enrich(_model("A"), GRID, known=1)))
        other = [["Field", "Width", "Default"], *[[f"REG{i}", "8", "1"] for i in range(40, 52)]]
        model = _model("B")
        result = await _enrich(model, other, known=1)
        assert model.calls == [("combined", 12)]
        assert all(t.startswith("B: ") for t in result.descriptions)

    @pytest.mark.asyncio
    async def test_a_stored_record_that_cannot_be_read_runs_as_before(self) -> None:
        offer_previous_rows(PreviousRows(AsyncMock(side_effect=OSError("down")), MagicMock(), "rec-1"))
        model = _model("B")
        await _enrich(model, _edited(), known=1)
        assert model.calls == [("combined", 30)]

    @pytest.mark.asyncio
    async def test_rows_over_the_row_budget_read_nothing_and_record_no_digest(self) -> None:
        load = _offer(_stored(await _enrich(_model("A"), GRID, known=1)))
        result = await _enrich(_model("B"), _edited(), known=1, describe_rows=False)
        load.assert_not_awaited()
        assert result.row_digests == []

    @pytest.mark.asyncio
    async def test_a_row_that_fell_back_to_plain_text_has_no_digest_and_is_described_next_time(self) -> None:
        # Kills: a plain row (the model left it out) kept for ever, never described again.
        first = await _enrich(_model("A", behave=by_first_row("REG0", "skip:3", kind="combined")), GRID, known=1)
        assert first.row_digests[3] is None and not first.descriptions[3].startswith("A: ")
        _offer(_stored(first))
        model = _model("B")
        result = await _enrich(model, GRID, known=1)
        assert model.row_calls == [1]
        assert result.descriptions[3] == "B: REG3"


class TestTheDigestLeavesBlockIdsAlone:
    def _container(self, *, digest: bool) -> BlocksContainer:
        data = {"row_natural_language_text": "Alpha recorded 412 in Q1.", "row_number": 1}
        if digest:
            data[ROW_DIGEST_KEY] = "d" * 32
        return BlocksContainer(blocks=[Block(index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON, data=data)], block_groups=[])

    def test_a_row_that_gains_a_digest_keeps_its_id_and_hash(self) -> None:
        # Kills: hashing the digest (every stored row would get a new id on its next update after the deploy).
        without, with_ = self._container(digest=False), self._container(digest=True)
        BlockIdentity.assign(without, "org", "vr")
        BlockIdentity.assign(with_, "org", "vr")
        assert with_.blocks[0].content_hash == without.blocks[0].content_hash
        assert with_.blocks[0].id == without.blocks[0].id

    def test_the_classification_fingerprint_does_not_change(self) -> None:
        assert content_facts(self._container(digest=True)) == content_facts(self._container(digest=False))

    def test_only_the_digest_and_the_rows_position_are_left_out(self) -> None:
        row = {"row_natural_language_text": "t", "row_number": 3, "row_end_number": 3, "row_count": 1, ROW_DIGEST_KEY: "d"}
        assert hashed_block_data(row) == {"row_natural_language_text": "t", "row_count": 1}
        assert hashed_block_data({"a": 1, ROW_DIGEST_KEY: "d"}) == {"a": 1}
        # Only a table row's position is left out: another block's fields are all hashed.
        assert hashed_block_data({"text": "t", "row_number": 3}) == {"text": "t", "row_number": 3}
        assert hashed_block_data("text") == "text"


class TestWhichDeliveriesKeepRowText:
    @pytest.mark.parametrize(
        ("event_type", "event_data", "stored_vrid", "store_only", "refresh", "keeps"),
        [
            (EventTypes.UPDATE_RECORD.value, {}, "vr", False, False, True),
            (EventTypes.REINDEX_RECORD.value, {}, "vr", False, False, True),
            (EventTypes.NEW_RECORD.value, {}, "vr", False, False, False),
            (EventTypes.UPDATE_RECORD.value, {"forceReindex": True}, "vr", False, False, False),
            (EventTypes.UPDATE_RECORD.value, {}, "vr", False, True, False),
            (EventTypes.UPDATE_RECORD.value, {}, "vr", True, False, False),
            (EventTypes.UPDATE_RECORD.value, {}, None, False, False, False),
        ],
        ids=["update", "reindex", "new-record", "force-reindex", "refresh-of-paid-outputs", "store-only", "nothing-stored"],
    )
    def test_only_a_content_update_does(
        self, event_type: str, event_data: dict, stored_vrid: str | None, store_only: bool, refresh: bool, keeps: bool,
    ) -> None:
        run = SimpleNamespace(refresh=True) if refresh else None
        with patch("app.events.events.current_paid_run", return_value=run):
            assert keeps_stored_row_text(event_type, event_data, stored_vrid, "org", store_only=store_only) is keeps


class TestParsersStoreTheDigest:
    def test_pdfplumber_rows_carry_their_digest_and_skip_empty_rows_in_step(self) -> None:
        from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
            _table_row_blocks,
        )

        blocks = _table_row_blocks(["one", "", "three"], 0, 0, None, ["d1", "d2", None])
        assert [b.data.get(ROW_DIGEST_KEY) for b in blocks] == ["d1", None]
        assert [b.data["row_number"] for b in blocks] == [1, 3]

    @pytest.mark.asyncio
    async def test_block_rows_carry_their_digest(self) -> None:
        rows = [["Idea", "Status"], ["REG1", "New"], ["REG2", "Review"]]
        blocks = [
            Block(
                id=f"b{i}", index=i, parent_index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
                data={"cells": cells, "row_natural_language_text": "|".join(cells), ROW_DIGEST_KEY: "stale"},
                table_row_metadata=TableRowMetadata(row_number=i + 1, is_header=i == 0),
            )
            for i, cells in enumerate(rows)
        ]
        children = BlockGroupChildren()
        for b in blocks:
            children.add_block_index(b.index)
        group = BlockGroup(
            id="g", index=0, type=GroupType.TABLE, format=DataFormat.JSON, data={"table_markdown": "x"},
            table_metadata=TableMetadata(num_of_cols=2, has_header=True), children=children,
        )
        container = BlocksContainer(blocks=blocks, block_groups=[group])
        await table_enrichment._enhance_one_table(group, container, _model("A", headers=["Idea", "Status"]), LOG)
        assert [b.data.get(ROW_DIGEST_KEY) for b in blocks[1:]] == [row_digest(rows[0], r) for r in rows[1:]]


class TestTheStoredBlocksReader:
    @pytest.mark.asyncio
    async def test_it_reads_the_stored_record_of_the_virtual_record(self) -> None:
        from app.events.processor import Processor

        stored = {"block_containers": {"blocks": [], "block_groups": []}}
        reader = MagicMock()
        reader.read_record = AsyncMock(return_value=stored)
        processor = SimpleNamespace(sink_orchestrator=SimpleNamespace(blob_storage=object()))
        with patch("app.events.processor.record_artifact_reader", return_value=reader):
            container = await Processor.load_stored_blocks(processor, "vr-1", "org-1")
        reader.read_record.assert_awaited_once_with("vr-1", "org-1")
        assert isinstance(container, BlocksContainer)

    @pytest.mark.asyncio
    async def test_nothing_stored_is_no_blocks(self) -> None:
        from app.events.processor import Processor

        reader = MagicMock()
        reader.read_record = AsyncMock(return_value=None)
        processor = SimpleNamespace(sink_orchestrator=SimpleNamespace(blob_storage=object()))
        with patch("app.events.processor.record_artifact_reader", return_value=reader):
            assert await Processor.load_stored_blocks(processor, "vr-1", "org-1") is None
