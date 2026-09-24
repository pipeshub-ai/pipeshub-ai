"""Tests for app.utils.worker_scaling.

The whole point of this module is that raising the worker count must not multiply
per-process budgets, and that leaving it alone must change nothing.
"""

import pytest

from app.utils.worker_scaling import (
    get_process_worker_count,
    scaled,
    set_process_worker_count,
)


@pytest.fixture(autouse=True)
def _reset_count():
    """Module state is global; reset on both sides so test order cannot matter.

    Resetting only on teardown would let an earlier module that declared a count
    leak into the first assertion here.
    """
    set_process_worker_count(1)
    yield
    set_process_worker_count(1)


class TestDefault:
    def test_default_is_one_worker(self) -> None:
        assert get_process_worker_count() == 1

    def test_scaled_is_the_identity_at_one_worker(self) -> None:
        """Every service that never declares a count relies on this."""
        for n in (1, 2, 7, 24, 100, 4096):
            assert scaled(n) == n


class TestDivision:
    def test_divides_the_real_budgets(self) -> None:
        set_process_worker_count(4)
        assert scaled(24) == 6      # MAX_CONCURRENT_INDEXING_LLM_CALLS
        assert scaled(100) == 25    # DOWNLOAD_CONNECTION_LIMIT_DEFAULT

    def test_four_workers_do_not_exceed_the_whole_budget(self) -> None:
        """The invariant that matters: N workers must not claim more than 1 did."""
        for total in (24, 100):
            for workers in (1, 2, 3, 4, 8):
                set_process_worker_count(workers)
                assert scaled(total) * workers <= total

    def test_never_returns_zero(self) -> None:
        """A pool sized 0 would deadlock; flooring at 1 means small budgets stop
        dividing rather than vanish."""
        set_process_worker_count(8)
        assert scaled(1) == 1
        assert scaled(2) == 1


class TestCountParsing:
    def test_clamps_nonsense_counts_to_one(self) -> None:
        for bad in (0, -1, -99):
            set_process_worker_count(bad)
            assert get_process_worker_count() == 1

    def test_accepts_a_numeric_value(self) -> None:
        set_process_worker_count(4)
        assert get_process_worker_count() == 4
        assert scaled(24) == 6
