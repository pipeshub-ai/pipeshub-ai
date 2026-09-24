"""`app/utils/render_budget.py` — the character/block allowance for one fetch.

Pure accounting, so these are exhaustive and need nothing but the object.
"""

from __future__ import annotations

import pytest

from app.utils.render_budget import (
    DEFAULT_CONTEXT_LENGTH,
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
        budget = resolve_render_budget(200_000)
        assert MIN_RENDER_CHARS < budget.max_chars < MAX_RENDER_CHARS

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

    def test_an_absurd_override_is_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(MAX_CHARS_ENV_VAR, "999999999")
        assert resolve_render_budget(128_000).max_chars == 2_000_000
