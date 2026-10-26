"""The budget stale-record recovery spends before giving up on a record."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing.recovery_budget import (
    record_handback,
    recovery_exhausted,
    recovery_tracking_id,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def limits() -> Iterator[None]:
    with patch("app.modules.indexing.recovery_budget.messaging_env") as env:
        env.max_delivery_attempts = 3
        env.redis_max_deliveries = 10
        yield


def _tracker(failures: int = 0, handbacks: int = 0) -> AsyncMock:
    tracker = AsyncMock()
    tracker.get_count.return_value = failures
    tracker.get_handbacks.return_value = handbacks
    return tracker


class TestRecoveryExhausted:
    def test_one_tracking_id_per_record_version(self) -> None:
        assert recovery_tracking_id("r1", 2) == "stale-recovery:r1:2"
        assert recovery_tracking_id("r1", 2) != recovery_tracking_id("r1", 3)

    async def test_without_a_tracker_there_is_no_limit(self) -> None:
        assert await recovery_exhausted(None, "t", MagicMock()) is False

    @pytest.mark.parametrize(("failures", "exhausted"), [(2, False), (3, True)])
    async def test_failures_are_bounded_by_the_retry_limit(self, failures: int, exhausted: bool) -> None:
        assert await recovery_exhausted(_tracker(failures=failures), "t", MagicMock()) is exhausted

    @pytest.mark.parametrize(("handbacks", "exhausted"), [(9, False), (10, True)])
    async def test_hand_backs_are_bounded_by_the_delivery_backstop(self, handbacks: int, exhausted: bool) -> None:
        assert await recovery_exhausted(_tracker(handbacks=handbacks), "t", MagicMock()) is exhausted

    async def test_deciding_counts_nothing(self) -> None:
        """A pass that then cannot publish must not have spent anything."""
        tracker = _tracker()

        await recovery_exhausted(tracker, "t", MagicMock())

        tracker.record_handback.assert_not_awaited()
        tracker.record_delivery.assert_not_awaited()
        tracker.increment_and_check.assert_not_awaited()

    async def test_an_unreadable_budget_lets_the_record_through(self) -> None:
        """The event still carries the consumer's own retry budget."""
        tracker = _tracker()
        tracker.get_count.side_effect = TimeoutError("Timeout connecting to server")
        logger = MagicMock()

        assert await recovery_exhausted(tracker, "t", logger) is False
        logger.warning.assert_called_once()


class TestRecordHandback:
    async def test_a_published_hand_back_is_counted_on_its_own_counter(self) -> None:
        tracker = _tracker()

        await record_handback(tracker, "t", MagicMock())

        tracker.record_handback.assert_awaited_once_with("t")
        tracker.record_delivery.assert_not_awaited()

    async def test_a_count_that_fails_is_logged_not_raised(self) -> None:
        tracker = _tracker()
        tracker.record_handback.side_effect = TimeoutError("Timeout connecting to server")
        logger = MagicMock()

        await record_handback(tracker, "t", logger)

        logger.warning.assert_called_once()

    async def test_without_a_tracker_nothing_is_counted(self) -> None:
        await record_handback(None, "t", MagicMock())
