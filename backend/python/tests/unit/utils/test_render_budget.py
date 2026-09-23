"""`app/utils/render_budget.py` — the character/block allowance for one fetch.

Pure accounting, so these are exhaustive and need nothing but the object.
"""

from __future__ import annotations

import random

import pytest

from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
)
from app.utils.render_budget import (
    fair_shares,
    DEFAULT_CONTEXT_LENGTH,
    FETCH_RESULT_RESERVE,
    MAX_CHARS_ENV_VAR,
    MAX_RENDER_CHARS,
    MIN_RENDER_CHARS,
    TRUNCATION_MARKER,
    RenderBudget,
    resolve_render_budget,
)


def _budget(max_chars: int = 100, max_blocks: int | None = None) -> RenderBudget:
    budget = RenderBudget(max_chars=max_chars, max_blocks=max_blocks)
    budget.begin_record("rec-1")
    return budget


class TestSpending:
    def test_text_within_the_allowance_is_returned_whole(self) -> None:
        budget = _budget(100)
        assert budget.take("x" * 40) == "x" * 40
        assert budget.chars_used == 40
        assert budget.chars_remaining == 60

    def test_exactly_the_remaining_allowance_still_fits(self) -> None:
        budget = _budget(100)
        budget.take("x" * 60)
        assert budget.take("y" * 40) == "y" * 40
        assert budget.exhausted

    def test_text_beyond_the_allowance_is_refused_once_something_was_rendered(self) -> None:
        budget = _budget(100)
        budget.take("x" * 90)
        assert budget.take("y" * 20) is None
        assert budget.chars_used == 90, "a refused block costs nothing"

    def test_a_block_larger_than_the_whole_budget_still_renders_a_prefix(self) -> None:
        """A fetch that returns a prefix is useful; one that returns an empty
        record is not."""
        budget = _budget(100)
        emitted = budget.take("x" * 5_000)

        assert emitted is not None
        assert emitted.endswith(TRUNCATION_MARKER)
        assert len(emitted) <= 100
        assert budget.exhausted

    def test_the_prefix_rule_applies_only_before_anything_is_rendered(self) -> None:
        budget = _budget(100)
        budget.take("x" * 10)
        assert budget.take("y" * 5_000) is None

    def test_shown_blocks_accumulate_per_record(self) -> None:
        budget = _budget(1_000)
        budget.note_shown([1, 2])
        budget.begin_record("rec-2")
        budget.note_shown([7])
        budget.begin_record("rec-1")
        budget.note_shown((3,))

        assert budget.outcome("rec-1").shown_blocks == {1, 2, 3}
        assert budget.outcome("rec-2").shown_blocks == {7}

    def test_a_clip_makes_the_record_incomplete_and_shows_nothing_more(self) -> None:
        budget = _budget(100)
        budget.take("x" * 5_000)
        budget.note_shown([0])

        outcome = budget.outcome("rec-1")
        assert outcome.clipped is True
        assert outcome.complete is False
        assert outcome.shown_blocks == frozenset()

    def test_framing_counts_against_the_size_but_still_allows_a_prefix(self) -> None:
        """A record header is spent before the first block; that block must
        still render a prefix rather than nothing."""
        budget = _budget(100)
        budget.charge_framing("h" * 30)
        emitted = budget.take("x" * 5_000)

        assert emitted is not None and emitted.endswith(TRUNCATION_MARKER)
        assert budget.chars_used <= 100

    def test_empty_text_is_free(self) -> None:
        budget = _budget(100)
        assert budget.take("") == ""
        assert budget.chars_used == 0

    def test_can_afford_is_a_peek_and_charges_nothing(self) -> None:
        budget = _budget(100)
        assert budget.can_afford("x" * 100) is True
        assert budget.can_afford("x" * 101) is False
        assert budget.chars_used == 0

    def test_charge_accumulates_for_callers_that_build_text_in_pieces(self) -> None:
        """A table charges row by row while accumulating its rows."""
        budget = _budget(100)
        for _ in range(4):
            budget.charge("x" * 10)
        assert budget.chars_used == 40


class TestBlockCounting:
    def test_blocks_and_characters_are_counted_separately(self) -> None:
        """A whole table group counts as one rendered unit however many rows
        it charges — the pre-existing meaning of the block cap."""
        budget = _budget(1_000, max_blocks=2)
        for _ in range(50):
            budget.charge("row")
        budget.count_block()

        assert budget.blocks_used == 1
        assert budget.blocks_exhausted is False

    def test_block_cap_is_reached_independently_of_characters(self) -> None:
        budget = _budget(1_000, max_blocks=2)
        budget.count_block()
        budget.count_block()
        assert budget.blocks_exhausted is True
        assert budget.exhausted is False

    def test_no_block_cap_means_no_block_exhaustion(self) -> None:
        budget = _budget(1_000, max_blocks=None)
        for _ in range(1_000):
            budget.count_block()
        assert budget.blocks_exhausted is False


class TestPerRecordOutcomes:
    def test_records_share_the_pool_but_report_separately(self) -> None:
        budget = RenderBudget(max_chars=100)

        budget.begin_record("a")
        budget.take("x" * 30)
        budget.count_block()

        budget.begin_record("b")
        budget.take("y" * 50)
        budget.count_block()
        budget.stop_at(7)

        first, second = budget.outcome("a"), budget.outcome("b")
        assert (first.chars_rendered, first.blocks_rendered) == (30, 1)
        assert (second.chars_rendered, second.blocks_rendered) == (50, 1)
        assert budget.chars_used == 80, "one shared pool"
        assert first.complete is True
        assert second.complete is False
        assert second.stopped_at_block == 7

    def test_the_earliest_unrendered_block_wins(self) -> None:
        """Continuation resumes at the first block the model did not get."""
        budget = _budget()
        budget.stop_at(12)
        budget.stop_at(30)
        assert budget.outcome("rec-1").stopped_at_block == 12

    def test_an_untouched_record_reports_a_complete_empty_outcome(self) -> None:
        budget = RenderBudget(max_chars=100)
        outcome = budget.outcome("never-rendered")
        assert outcome.complete is True
        assert outcome.blocks_rendered == 0

    def test_a_truncated_table_makes_the_record_incomplete(self) -> None:
        budget = _budget()
        budget.note_table_truncation(group_index=3, shown=100, total=5_000)

        outcome = budget.outcome("rec-1")
        assert outcome.complete is False
        assert outcome.table_truncation.rows_shown == 100
        assert outcome.table_truncation.rows_total == 5_000

    def test_the_first_table_truncation_is_kept(self) -> None:
        budget = _budget()
        budget.note_table_truncation(1, 10, 100)
        budget.note_table_truncation(2, 5, 50)
        assert budget.outcome("rec-1").table_truncation.group_index == 1

    def test_spending_before_begin_record_does_not_crash(self) -> None:
        """Callers that never frame a record still get a working budget."""
        budget = RenderBudget(max_chars=100)
        assert budget.take("x" * 10) == "x" * 10
        budget.count_block()
        budget.stop_at(4)
        assert budget.chars_used == 10


class TestSizing:
    def test_a_large_window_is_capped(self) -> None:
        assert resolve_render_budget(1_000_000).max_chars == MAX_RENDER_CHARS

    def test_a_small_window_gets_the_floor(self) -> None:
        """A local model whose config claims 8k must still return something
        usable."""
        assert resolve_render_budget(8_000).max_chars == MIN_RENDER_CHARS

    def test_a_typical_window_lands_between_the_bounds(self) -> None:
        budget = resolve_render_budget(50_000)
        assert MIN_RENDER_CHARS < budget.max_chars < MAX_RENDER_CHARS

    @pytest.mark.parametrize("window", [8_000, 128_000, 200_000, 1_000_000])
    def test_a_fetch_always_fits_under_the_tool_result_cap(self, window: int) -> None:
        """Past the cap, the tool result is cut in the middle and the model
        loses blocks the fetch reported as shown."""
        budget = resolve_render_budget(window)
        assert budget.max_chars + FETCH_RESULT_RESERVE <= DEFAULT_MAX_RESULT_CHARS

    @pytest.mark.parametrize("window", [None, 0, -1])
    def test_an_unknown_window_falls_back_to_the_default(self, window: int | None) -> None:
        assert resolve_render_budget(window).max_chars == resolve_render_budget(
            DEFAULT_CONTEXT_LENGTH
        ).max_chars

    def test_the_block_cap_is_passed_through(self) -> None:
        assert resolve_render_budget(128_000, max_blocks=25).max_blocks == 25

    def test_env_override_wins_over_the_derived_size(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(MAX_CHARS_ENV_VAR, "5000")
        assert resolve_render_budget(1_000_000).max_chars == 5_000

    @pytest.mark.parametrize("bad", ["lots", "", "  ", "12.5"])
    def test_a_malformed_override_falls_back_rather_than_failing_the_request(
        self, monkeypatch: pytest.MonkeyPatch, bad: str,
    ) -> None:
        """A typo in an env var must not change how much of a record the model
        gets, and must never fail the request."""
        expected = resolve_render_budget(128_000).max_chars
        monkeypatch.setenv(MAX_CHARS_ENV_VAR, bad)
        assert resolve_render_budget(128_000).max_chars == expected

    def test_an_override_past_the_tool_result_cap_is_clamped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(MAX_CHARS_ENV_VAR, "999999999")
        assert resolve_render_budget(128_000).max_chars == MAX_RENDER_CHARS


class TestFairShares:
    """How one call's room is split between the records it names."""

    def test_records_that_fit_get_what_they_need(self) -> None:
        assert fair_shares([100, 200], 1_000) == [100, 200]

    def test_equal_records_split_evenly(self) -> None:
        assert fair_shares([900, 900, 900], 900) == [300, 300, 300]

    def test_what_a_small_record_leaves_goes_to_the_large_ones(self) -> None:
        assert fair_shares([5_000, 100, 5_000], 1_100) == [500, 100, 500]

    def test_shares_follow_the_input_order(self) -> None:
        assert fair_shares([100, 5_000], 1_000) == [100, 900]
        assert fair_shares([5_000, 100], 1_000) == [900, 100]

    def test_no_room_means_no_shares(self) -> None:
        assert fair_shares([10, 20], 0) == [0, 0]
        assert fair_shares([], 1_000) == []

    def test_never_over_allocates_and_never_starves(self) -> None:
        rng = random.Random(7)
        for _ in range(500):
            sizes = [rng.randint(0, 20_000) for _ in range(rng.randint(1, 12))]
            total = rng.randint(0, 60_000)

            shares = fair_shares(sizes, total)

            assert sum(shares) <= total
            assert all(0 <= share <= max(0, size) for share, size in zip(shares, sizes, strict=True))
            equal = total // len(sizes)
            assert all(share >= min(size, equal) for share, size in zip(shares, sizes, strict=True))


class TestPerRecordAllowance:
    def test_an_allotted_record_cannot_spend_past_its_share(self) -> None:
        budget = RenderBudget(max_chars=10_000)
        budget.allot("a", 1_000)
        budget.begin_record("a")

        assert budget.chars_remaining == 1_000
        assert budget.take("x" * 800) is not None
        assert budget.take("x" * 800) is None
        assert budget.call_chars_remaining == 9_200

    def test_framing_counts_against_the_share(self) -> None:
        budget = RenderBudget(max_chars=10_000)
        budget.allot("a", 1_000)
        budget.begin_record("a")
        budget.charge_framing("h" * 300)

        assert budget.chars_remaining == 700

    def test_each_record_can_clip_a_prefix_of_its_first_block(self) -> None:
        """A later record whose share is smaller than its first block gets a
        prefix, not an empty shell."""
        budget = RenderBudget(max_chars=10_000)
        budget.begin_record("a")
        budget.take("a" * 500)
        budget.allot("b", 1_000)
        budget.begin_record("b")

        emitted = budget.take("b" * 5_000)

        assert emitted is not None
        assert emitted.startswith("b" * 100)
        assert budget.outcome("b").clipped
        assert not budget.outcome("a").clipped
