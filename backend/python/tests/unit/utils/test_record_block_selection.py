"""Which blocks an over-budget record gives up.

A record that does not fit has to lose something. These pin the rule that it
loses the *least relevant* parts rather than the tail, and that a failure in
the ranking never costs the caller its fetch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.record_block_selection import (
    build_selection_query,
    describe_gaps,
    estimate_record_chars,
    select_relevant_blocks,
)
from app.utils.render_budget import RenderBudget


def _record(block_count: int = 100, chars: int = 500) -> dict:
    return {
        "id": "rec-1",
        "virtual_record_id": "vr-1",
        "block_containers": {
            "blocks": [
                {"index": i, "type": "text", "parent_block_index": None, "data": "x" * chars}
                for i in range(block_count)
            ],
        },
    }


def _retrieval(*hit_indices: int, virtual_record_id: str = "vr-1") -> MagicMock:
    service = MagicMock()
    service.search_with_filters = AsyncMock(return_value={
        "searchResults": [
            {"metadata": {"virtualRecordId": virtual_record_id, "blockIndex": i}}
            for i in hit_indices
        ],
    })
    return service


async def _select(record: dict, service, budget: RenderBudget, **kwargs) -> set[int] | None:
    return await select_relevant_blocks(
        record=record,
        virtual_record_id=kwargs.pop("virtual_record_id", "vr-1"),
        query=kwargs.pop("query", "indemnity clause"),
        retrieval_service=service,
        user_id="user-1",
        org_id="org-1",
        budget=budget,
        **kwargs,
    )


class TestSelection:
    async def test_hits_arrive_with_their_neighbours(self) -> None:
        """A matched passage whose neighbours are missing reads as a fragment —
        the sentence defining the term is usually the one next to it. Sized to
        exactly three blocks so the neighbourhood is what is being tested, not
        the fill that uses up whatever room is left."""
        budget = RenderBudget(max_chars=1_500)   # 3 blocks of 500

        selected = await _select(_record(), _retrieval(40), budget)

        assert selected == {39, 40, 41}

    async def test_selection_stops_at_the_allowance(self) -> None:
        """Four blocks of room: the best-ranked neighbourhood, then one more
        block of context around it — and nothing from the other two hits."""
        budget = RenderBudget(max_chars=2_000)   # 4 blocks of 500

        selected = await _select(_record(), _retrieval(10, 50, 90), budget)

        assert len(selected) == 4
        assert selected <= {8, 9, 10, 11, 12}, "stayed around the best hit"
        assert not selected & {49, 50, 51, 89, 90, 91}

    async def test_neighbours_are_clamped_to_the_record(self) -> None:
        """Block -1 does not exist; a hit at the first block must not invent
        one."""
        budget = RenderBudget(max_chars=100_000)

        selected = await _select(_record(block_count=3), _retrieval(0), budget)

        assert selected == {0, 1, 2}, "a record this small fits entirely"
        assert min(selected) >= 0

    async def test_the_search_is_scoped_to_this_record(self) -> None:
        """Cross-record leakage here would put another document's blocks in
        this record's render."""
        service = _retrieval(5)
        await _select(_record(), service, RenderBudget(max_chars=100_000))

        kwargs = service.search_with_filters.await_args.kwargs
        assert kwargs["virtual_record_ids_from_tool"] == ["vr-1"]
        assert kwargs["user_id"] == "user-1"
        assert kwargs["org_id"] == "org-1"

    async def test_results_from_another_record_are_ignored(self) -> None:
        service = _retrieval(5, virtual_record_id="vr-other")
        assert await _select(_record(), service, RenderBudget(max_chars=100_000)) is None

    async def test_results_without_a_block_index_are_skipped(self) -> None:
        """Guessing an index would pull an unrelated block into the answer."""
        service = MagicMock()
        service.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"metadata": {"virtualRecordId": "vr-1"}}],
        })
        assert await _select(_record(), service, RenderBudget(max_chars=100_000)) is None


class TestFillingTheAllowance:
    """Relevance decides what comes first, not how much comes at all."""

    async def test_the_allowance_is_the_constraint_not_the_hit_count(self) -> None:
        """A fixed hit count capped the render at ~hit_limit x 3 blocks however
        much room was left — barely half a 128k allowance on a real document."""
        budget = RenderBudget(max_chars=100_000)   # 200 blocks of 500
        budget.begin_record("rec-1")

        selected = await _select(_record(block_count=1_000), _retrieval(*range(0, 1000, 50)), budget)

        assert len(selected) > 150, "the room was left unused"
        assert sum(500 for _ in selected) >= 0.9 * budget.max_chars

    async def test_the_extra_room_goes_around_what_matched(self) -> None:
        """A passage reads correctly when what surrounds it comes with it, so
        the selection grows outward rather than appending a distant slice."""
        budget = RenderBudget(max_chars=10_000)    # 20 blocks
        budget.begin_record("rec-1")

        selected = await _select(_record(block_count=500), _retrieval(250), budget)

        assert selected == set(range(250 - len(selected) // 2, 250 + len(selected) // 2 + 1)) or (
            min(selected) >= 240 and max(selected) <= 260
        ), "growth stayed around the hit"

    async def test_a_record_that_fits_entirely_is_selected_entirely(self) -> None:
        budget = RenderBudget(max_chars=100_000)
        budget.begin_record("rec-1")

        selected = await _select(_record(block_count=20), _retrieval(5), budget)

        assert selected == set(range(20))


class TestFallback:
    """`None` means "no opinion, render positionally" — never an empty record."""

    async def test_a_failing_search_falls_back(self) -> None:
        service = MagicMock()
        service.search_with_filters = AsyncMock(side_effect=RuntimeError("vector db down"))

        assert await _select(_record(), service, RenderBudget(max_chars=100_000)) is None

    async def test_no_hits_falls_back(self) -> None:
        service = MagicMock()
        service.search_with_filters = AsyncMock(return_value={"searchResults": []})

        assert await _select(_record(), service, RenderBudget(max_chars=100_000)) is None

    async def test_no_retrieval_service_falls_back(self) -> None:
        assert await _select(_record(), None, RenderBudget(max_chars=100_000)) is None

    @pytest.mark.parametrize("query", ["", "   "])
    async def test_no_query_falls_back(self, query: str) -> None:
        assert await _select(_record(), _retrieval(5), RenderBudget(max_chars=1_000), query=query) is None

    async def test_no_virtual_record_id_falls_back(self) -> None:
        assert await _select(
            _record(), _retrieval(5), RenderBudget(max_chars=1_000), virtual_record_id=None,
        ) is None

    async def test_an_exhausted_budget_falls_back_rather_than_selecting_nothing(self) -> None:
        budget = RenderBudget(max_chars=100)
        budget.begin_record("rec-1")
        budget.take("x" * 100)

        assert await _select(_record(), _retrieval(5), budget) is None


class TestQueryText:
    def test_both_halves_are_used(self) -> None:
        """The question is the goal; `reason` is what the model wants from
        *this* record, and until now was only logged."""
        assert build_selection_query(
            "what are the risks", "check the indemnity clause",
        ) == "what are the risks check the indemnity clause"

    def test_an_echoed_reason_is_not_repeated(self) -> None:
        assert build_selection_query("what are the risks", "What are the risks") == "what are the risks"

    @pytest.mark.parametrize(
        ("query", "reason", "expected"),
        [("", "find the total", "find the total"), ("find the total", "", "find the total"), ("", "", "")],
    )
    def test_either_half_may_be_missing(self, query: str, reason: str, expected: str) -> None:
        assert build_selection_query(query, reason) == expected


class TestGapMarkers:
    def test_a_leading_gap_is_announced(self) -> None:
        markers = describe_gaps({10, 11}, list(range(20)))
        assert "(0–9)" in markers[10]

    def test_a_gap_says_how_to_read_it(self) -> None:
        """A gap the model cannot address is just an admission that something
        is missing."""
        markers = describe_gaps({10}, list(range(20)))
        assert "start_block=0" in markers[10]

    def test_an_interior_gap_is_announced(self) -> None:
        markers = describe_gaps({1, 2, 8}, list(range(10)))
        assert "(3–7)" in markers[8]

    def test_contiguous_selection_has_no_markers(self) -> None:
        assert describe_gaps({0, 1, 2}, list(range(3))) == {}

    def test_a_single_missing_block_reads_naturally(self) -> None:
        markers = describe_gaps({0, 2}, [0, 1, 2])
        assert "1 block " in markers[2]


class TestEstimation:
    def test_fragments_do_not_count_toward_the_estimate(self) -> None:
        """They render through their container, not on their own."""
        record = _record(block_count=2, chars=100)
        record["block_containers"]["blocks"].append(
            {"index": 99, "type": "text", "parent_block_index": 0, "data": "x" * 10_000},
        )
        assert estimate_record_chars(record) == 200

    def test_an_image_block_is_not_measured_by_its_base64(self) -> None:
        record = {
            "block_containers": {
                "blocks": [
                    {"index": 0, "type": "image", "parent_block_index": None,
                     "data": {"uri": "data:image/png;base64," + "A" * 500_000}},
                ],
            },
        }
        assert estimate_record_chars(record) < 1_000
