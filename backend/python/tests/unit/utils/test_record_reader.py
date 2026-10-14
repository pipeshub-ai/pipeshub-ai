"""Reading part of a long record, and a whole Slack thread, through the API.

A record larger than a caller's tool-output limit used to arrive whole or not
at all. These pin that a read stays inside its budget, says where to carry on,
and that following those pointers reaches every block exactly once.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import record_to_text
from app.utils.record_reader import read_record, read_thread


def _text_block(index: int, text: str) -> dict:
    return {"index": index, "type": BlockType.TEXT.value, "parent_index": None, "data": text}


def _record(record_id: str, blocks: list[dict], block_groups: list[dict] | None = None, **extra: object) -> dict:
    return {
        "id": record_id,
        "virtual_record_id": f"v-{record_id}",
        "frontend_url": "https://app.example.com",
        "context_metadata": f"Record ID: {record_id}\nName: {record_id}",
        "block_containers": {"blocks": blocks, "block_groups": block_groups or []},
        **extra,
    }


def _long_record(blocks: int = 3_000, chars: int = 400) -> dict:
    """A record the size of a large pull request: about 1.2M characters."""
    return _record(
        "pr-1",
        [_text_block(i, f"<b{i}> " + "x" * (chars - len(f"<b{i}> "))) for i in range(blocks)],
    )


def _blocks_in(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"<b(\d+)>", text)]


def _retrieval(*hits: int, vrid: str = "v-pr-1") -> MagicMock:
    service = MagicMock()
    service.search_with_filters = AsyncMock(return_value={
        "searchResults": [{"metadata": {"virtualRecordId": vrid, "blockIndex": i}} for i in hits],
    })
    return service


class TestReadingInOrder:
    async def test_a_long_record_stays_inside_its_budget_and_says_where_to_continue(self) -> None:
        record = _long_record()
        assert len(record_to_text(record)) > 1_000_000, "what the endpoint returned before"

        read = await read_record(record, max_chars=40_000)

        assert read["chars"] <= 40_000
        assert len(read["content"]) == read["chars"]
        assert read["complete"] is False
        shown = _blocks_in(read["content"])
        assert shown == list(range(len(shown)))
        assert read["nextStartBlock"] == len(shown)
        assert f"startBlock={read['nextStartBlock']}" in read["content"]

    async def test_following_next_start_block_reads_every_block_exactly_once(self) -> None:
        record = _long_record()
        seen: list[int] = []
        start: int | None = None
        reads = 0
        while True:
            read = await read_record(record, max_chars=40_000, start_block=start)
            reads += 1
            assert read["chars"] <= 40_000
            seen.extend(_blocks_in(read["content"]))
            if read["complete"]:
                break
            start = read["nextStartBlock"]
            assert reads < 100

        assert seen == list(range(3_000))
        # 1.2M characters in reads of 40k: about 31 of them.
        assert reads <= 35

    async def test_a_record_that_fits_is_returned_whole(self) -> None:
        record = _record("doc-1", [_text_block(i, f"<b{i}> short") for i in range(5)])

        read = await read_record(record, max_chars=40_000)

        assert read["complete"] is True
        assert read["nextStartBlock"] is None
        assert read["truncatedBy"] is None
        assert _blocks_in(read["content"]) == [0, 1, 2, 3, 4]
        assert read["shownBlocks"] == [[0, 4]]
        assert read["totalBlocks"] == 5

    async def test_an_offset_past_the_end_says_so(self) -> None:
        record = _record("doc-1", [_text_block(i, f"<b{i}>") for i in range(5)])

        read = await read_record(record, max_chars=40_000, start_block=9)

        assert "last block is 4" in read["content"]
        assert _blocks_in(read["content"]) == []


class TestReadingByQuestion:
    async def test_the_passage_that_answers_comes_first_however_deep_it_is(self) -> None:
        """A positional window would need 31 reads to reach block 2500."""
        record = _long_record()
        retrieval = _retrieval(2_500)

        read = await read_record(
            record, max_chars=40_000, query="when was the migration rolled back",
            retrieval_service=retrieval, user_id="u-1", org_id="o-1",
        )

        assert read["order"] == "relevance"
        assert read["chars"] <= 40_000
        assert {2_499, 2_500, 2_501} <= set(_blocks_in(read["content"]))
        assert "not shown" in read["content"]
        kwargs = retrieval.search_with_filters.await_args.kwargs
        assert kwargs["virtual_record_ids_from_tool"] == ["v-pr-1"]
        assert kwargs["user_id"] == "u-1"

    async def test_an_explicit_start_block_reads_in_order_and_skips_the_ranking(self) -> None:
        """`startBlock=0` is what the hint tells a caller to send to read a
        selected record from the top."""
        retrieval = _retrieval(2_500)

        read = await read_record(
            _long_record(), max_chars=40_000, start_block=0, query="rollback",
            retrieval_service=retrieval, user_id="u-1", org_id="o-1",
        )

        assert read["order"] == "in_order"
        assert _blocks_in(read["content"])[0] == 0
        retrieval.search_with_filters.assert_not_awaited()

    async def test_a_record_that_fits_is_not_ranked(self) -> None:
        retrieval = _retrieval(2)
        record = _record("doc-1", [_text_block(i, f"<b{i}>") for i in range(5)])

        read = await read_record(
            record, max_chars=40_000, query="anything",
            retrieval_service=retrieval, user_id="u-1", org_id="o-1",
        )

        assert read["complete"] is True
        retrieval.search_with_filters.assert_not_awaited()

    async def test_a_failed_ranking_falls_back_to_reading_in_order(self) -> None:
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(side_effect=RuntimeError("index down"))

        read = await read_record(
            _long_record(), max_chars=40_000, query="rollback",
            retrieval_service=retrieval, user_id="u-1", org_id="o-1",
        )

        assert read["order"] == "in_order"
        assert _blocks_in(read["content"])[0] == 0


class TestWhatTheCallerReads:
    async def test_hints_name_the_api_parameters_not_the_assistants_tool(self) -> None:
        for read in (
            await read_record(_long_record(), max_chars=40_000),
            await read_record(
                _long_record(), max_chars=40_000, query="q",
                retrieval_service=_retrieval(2_500), user_id="u", org_id="o",
            ),
        ):
            assert "knowledgegraph__" not in read["content"]
            assert "start_block" not in read["content"]
            assert "startBlock=" in read["content"]

    async def test_citation_labels_are_left_out(self) -> None:
        rows = [
            {"index": i, "type": BlockType.TABLE_ROW.value, "parent_index": 0,
             "data": {"row_natural_language_text": f"row {i}"}}
            for i in range(3)
        ]
        table = {"index": 0, "type": GroupType.TABLE.value, "data": {"table_summary": "t"},
                 "children": {"block_ranges": [{"start": 0, "end": 2}]}}
        record = _record("t-1", [_text_block(3, "after the table"), *rows], [table])

        read = await read_record(record, max_chars=40_000)

        assert not re.search(r"\[ref\d+\]|\|ref\d+\]", read["content"])
        assert "[0] row 0" in read["content"]
        assert "after the table" in read["content"]

    async def test_a_document_line_that_looks_like_a_label_is_kept(self) -> None:
        """`ref1` is minted by every render, so a document line starting with
        it must survive -- as must a line that only looks like a row label."""
        record = _record("doc-1", [_text_block(0, "intro\n[ref1] is cited\n[note|] keep")])

        read = await read_record(record, max_chars=40_000)

        assert "\n[ref1] is cited" in read["content"]
        assert "\n[note|] keep" in read["content"]
        assert "[ref1] intro" not in read["content"], "the renderer's own label is still removed"


def _burst(record_id: str, created: int, *messages: str) -> dict:
    return _record(
        record_id,
        [_text_block(i, m) for i, m in enumerate(messages)],
        source_created_at=created,
        record_name=f"thread burst {record_id}",
    )


class TestReadingAThread:
    async def test_every_reply_arrives_in_order(self) -> None:
        records = [
            _burst("t-0", 1, "<m0> parent: deploy is failing"),
            _burst("t-1", 2, "<m1> reply: it is the cache", "<m2> reply: fixed in #812"),
            _burst("t-2", 3, "<m3> reply: confirmed green"),
        ]

        read = await read_thread(records, max_chars=40_000)

        assert re.findall(r"<m(\d)>", read["content"]) == ["0", "1", "2", "3"]
        assert read["complete"] is True
        assert read["nextStartRecord"] is None
        assert read["threadRecordCount"] == 3
        assert [r["recordId"] for r in read["records"]] == ["t-0", "t-1", "t-2"]

    @staticmethod
    async def _follow(records: list[dict], max_chars: int) -> tuple[list[str], int]:
        """Read a thread by following only the cursor each read returns."""
        seen: list[str] = []
        record, block, reads = 0, 0, 0
        while True:
            read = await read_thread(records, max_chars=max_chars, start_record=record, start_block=block)
            reads += 1
            assert read["chars"] <= max_chars
            seen.extend(re.findall(r"<m(\d+)>", read["content"]))
            if read["nextStartRecord"] is None:
                assert read["complete"] is True, "a null cursor means the thread was read in full"
                return seen, reads
            assert read["complete"] is False
            record, block = read["nextStartRecord"], read["nextStartBlock"]
            assert f"startRecord={record} and startBlock={block}" in read["content"]
            assert reads < 200

    async def test_a_long_thread_continues_by_record_and_reads_each_once(self) -> None:
        records = [_burst(f"t-{n}", n, f"<m{n}> " + "y" * 1_500) for n in range(20)]

        seen, _ = await self._follow(records, max_chars=5_000)

        assert seen == [str(n) for n in range(20)]

    async def test_a_record_larger_than_the_budget_is_finished_before_the_cursor_moves_on(self) -> None:
        """Following the returned cursor alone must reach every block of every
        record exactly once -- including the tail of a record too large for
        one read, and when that record is the last one."""
        records = [
            _burst("t-0", 1, *[f"<m{i}> " + "z" * 900 for i in range(20)]),
            _burst("t-1", 2, "<m20> a short reply"),
            _burst("t-2", 3, *[f"<m{i}> " + "w" * 900 for i in range(21, 40)]),
        ]

        seen, reads = await self._follow(records, max_chars=5_000)

        assert seen == [str(i) for i in range(40)]
        assert reads > 3, "the large records took several reads each"

    async def test_a_block_larger_than_the_budget_holds_the_cursor_until_it_fits(self) -> None:
        """A block cut because it alone exceeds `maxChars` has no later block
        to resume from. Following the cursor at that budget never skips past
        it to the next record; a budget that covers it reads it, and then the
        next record, once."""
        records = [
            _burst("t-0", 1, "<m0> " + "z" * 9_000),
            _burst("t-1", 2, "<m1> the next reply"),
        ]

        record, block = 0, 0
        for _ in range(5):
            read = await read_thread(records, max_chars=5_000, start_record=record, start_block=block)
            assert "<m1>" not in read["content"]
            assert read["complete"] is False
            assert (read["nextStartRecord"], read["nextStartBlock"]) == (0, 0)
            assert "larger than maxChars=5000" in read["content"]
            record, block = read["nextStartRecord"], read["nextStartBlock"]

        seen, _ = await self._follow(records, max_chars=20_000)
        assert seen == ["0", "1"]

    async def test_the_cursor_stays_on_a_cut_record(self) -> None:
        records = [
            _burst("t-0", 1, *[f"<m{i}> " + "z" * 900 for i in range(20)]),
            _burst("t-1", 2, "<m99> the last reply"),
        ]

        read = await read_thread(records, max_chars=5_000)

        assert read["records"][0]["complete"] is False
        assert read["nextStartRecord"] == 0
        assert read["nextStartBlock"] == read["records"][0]["nextStartBlock"] > 0
        assert "<m99>" not in read["content"]

    async def test_a_thread_with_nothing_readable_is_empty_and_complete(self) -> None:
        read = await read_thread([], max_chars=40_000)

        assert read["complete"] is True
        assert read["records"] == []
